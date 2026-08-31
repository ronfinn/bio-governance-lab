"""Reading a governed study's evidence back off disk, for the MCP server.

Everything here is a *reader*. The pipeline produced the evidence, the
governance layer already reached a verdict over it, and this module's whole job
is to find that material under a results root and hand it back as the models
the rest of the project already defines. Nothing is recalculated and nothing is
written:

    **Deterministic code decides. AI explains.**

That principle is what shapes the module. :func:`governance_report` deserializes
``governance-report.json`` into the very
:class:`~bio_governance.governance.models.GovernanceReport` the evaluator
produced, which means the decision an AI client is shown is derived from the
checks by the same computed field as always — editing ``"decision"`` in the JSON
by hand changes nothing, because that field is never read.

The results root is the boundary. A study identifier arrives from an MCP client,
which is to say from outside, so it is validated as an
:class:`~bio_governance.models.identifiers.AssetIdentifier` domain before it is
ever joined to a path, and the joined path is then checked to still be inside
the root. There is no tool that takes a file name, and no way to reach a file
this module does not name itself.

Ordinary evidence problems — an unknown study, a report that was never written,
a file that is not valid JSON — are :class:`EvidenceError`, not tracebacks. They
are the normal condition of a repository where a run may have stopped at a gate,
and an MCP client deserves a sentence rather than a stack.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bio_governance.contracts import ContractValidationResult
from bio_governance.governance import (
    COMPOUNDS_EVIDENCE,
    CONTRACTS_SUBDIR,
    LINEAGE_EVENTS,
    QUALITY_REPORT,
    SAMPLES_EVIDENCE,
    GovernanceCheckResult,
    GovernanceCheckStatus,
    GovernanceDecision,
    GovernanceReport,
)
from bio_governance.lineage import RAW_STAGE
from bio_governance.models import AssetIdentifier
from bio_governance.quality import QualityReport

#: Any of the evidence models a JSON file is read back into.
ModelT = TypeVar("ModelT", bound=BaseModel)

#: Where the server looks for governed studies when nothing else is configured.
#: The same directory ``pipelines/nextflow/nextflow.config`` publishes into.
DEFAULT_RESULTS_ROOT = Path("results")

#: Where ``bio-gov governance evaluate --json-out`` leaves its report, as
#: ``EVALUATE_GOVERNANCE`` in ``main.nf`` invokes it. The evaluator itself has
#: no constant for this: it reads the other four kinds of evidence and writes
#: this one, so the path belongs to whoever reads it back.
GOVERNANCE_REPORT = Path("governance") / "governance-report.json"

#: What makes a directory under the results root a governed study rather than
#: some other folder. Any one of these is enough, deliberately: a run stopped at
#: a contract gate leaves contract and quality evidence and no decision, and
#: that study is exactly the one a steward wants to be shown.
EVIDENCE_FILES: tuple[Path, ...] = (
    Path(CONTRACTS_SUBDIR) / SAMPLES_EVIDENCE,
    Path(CONTRACTS_SUBDIR) / COMPOUNDS_EVIDENCE,
    QUALITY_REPORT,
    LINEAGE_EVENTS,
    GOVERNANCE_REPORT,
)


class EvidenceError(Exception):
    """A study, or a piece of its evidence, is absent or unreadable."""


class StudySummary(BaseModel):
    """One line of the study listing.

    ``decision`` is ``None`` when the study has no governance report — an
    unfinished run, not a verdict of any kind. ``detail`` says which of the two
    it is in a sentence, so a listing never leaves a reader to guess whether a
    blank means "not evaluated" or "nothing wrong".
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    decision: GovernanceDecision | None = None
    detail: str = Field(min_length=1)


class ContractResults(BaseModel):
    """Both of a study's contract results, as the validator wrote them."""

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    samples: ContractValidationResult
    compounds: ContractValidationResult


class LineageSummary(BaseModel):
    """What one curation run's OpenLineage events say, without the events.

    The raw JSONL is a poor thing to hand a model: two events that repeat each
    other's datasets, wrapped in facets nothing here reads. This is the same
    information as identities — the run, the job, and the ``bio://`` names on
    either side of it.
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    job_namespace: str = Field(min_length=1)
    job_name: str = Field(min_length=1)
    event_types: tuple[str, ...] = Field(min_length=1)
    complete: bool
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()


class ReadinessExplanation(BaseModel):
    """Which of a study's governance checks stand between it and READY.

    Nothing here is inferred. The checks are the report's own, partitioned by
    the status they already carry, and ``decision`` is the report's derived
    verdict passed through unchanged. It is a convenience, not an opinion: the
    same answer a person would reach by reading the report, without their having
    to read it.
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    decision: GovernanceDecision
    summary: str = Field(min_length=1)
    blocking: tuple[GovernanceCheckResult, ...] = ()
    review: tuple[GovernanceCheckResult, ...] = ()


def study_directory(results_root: Path, study_id: str) -> Path:
    """Resolve one study's results directory, or refuse to.

    This is the whole of the server's file-system boundary, so it is deliberately
    strict twice over. ``study_id`` must be a valid asset-identifier domain —
    ``BIO-001``, never ``../etc`` or ``/etc`` or ``a/b`` — which rules out
    traversal before a path is built at all. The path is then resolved and
    required to still be inside the root, so a symlink cannot do what a string
    could not.
    """
    root = results_root.resolve()
    try:
        AssetIdentifier.parse(f"bio://{study_id}/{RAW_STAGE}/samples")
    except ValueError as exc:
        # Deliberately not the validation error's own text: it is a paragraph of
        # pydantic, and what a client needs is the shape an identifier has to be.
        raise EvidenceError(
            f"{study_id!r} is not a study identifier: expected a code such as 'BIO-001'"
        ) from exc

    directory = (root / study_id).resolve()
    if directory != root and not directory.is_relative_to(root):
        raise EvidenceError(f"study {study_id!r} is outside the results root {results_root}")
    if not directory.is_dir():
        raise EvidenceError(f"unknown study {study_id!r} under {results_root}")
    return directory


def discover_studies(results_root: Path) -> tuple[StudySummary, ...]:
    """Summarise every governed study under ``results_root``, in name order.

    A directory qualifies when it is named for a study and holds at least one
    piece of the evidence this project produces. That keeps an unrelated folder
    out of the listing without hiding a run that stopped at a gate — the study
    with no decision is the interesting one.
    """
    if not results_root.is_dir():
        raise EvidenceError(f"results root not found: {results_root}")

    return tuple(
        _summarize(results_root, directory.name)
        for directory in sorted(results_root.iterdir(), key=lambda path: path.name)
        if directory.is_dir()
        and _is_study_id(directory.name)
        and any((directory / relative).is_file() for relative in EVIDENCE_FILES)
    )


def governance_report(results_root: Path, study_id: str) -> GovernanceReport:
    """The study's governance report, as the evaluator's own model.

    Deserializing rather than passing the JSON through is the point. ``decision``
    is a computed field, so it is re-derived from the checks on the way in and
    the ``"decision"`` the file happens to carry is never read. A report cannot
    reach an MCP client claiming a verdict its checks do not support, however
    the file on disk was edited.
    """
    path = study_directory(results_root, study_id) / GOVERNANCE_REPORT
    return _load(GovernanceReport, path, f"governance report for {study_id}")


def quality_report(results_root: Path, study_id: str) -> QualityReport:
    """The study's data-quality report, as the quality layer's own model."""
    path = study_directory(results_root, study_id) / QUALITY_REPORT
    return _load(QualityReport, path, f"quality report for {study_id}")


def contract_results(results_root: Path, study_id: str) -> ContractResults:
    """Both contract results for the study, as the validator's own model."""
    contracts = study_directory(results_root, study_id) / CONTRACTS_SUBDIR
    return ContractResults(
        study_id=study_id,
        samples=_load(
            ContractValidationResult,
            contracts / SAMPLES_EVIDENCE,
            f"samples contract result for {study_id}",
        ),
        compounds=_load(
            ContractValidationResult,
            contracts / COMPOUNDS_EVIDENCE,
            f"compounds contract result for {study_id}",
        ),
    )


def lineage_summary(results_root: Path, study_id: str) -> LineageSummary:
    """Summarise the study's OpenLineage events as one curation run.

    Events that do not describe a single run cannot be summarised as one, and
    that is reported as an evidence problem rather than papered over. It is not
    a verdict — ``lineage_evidence`` in the governance report is where the
    incoherence becomes a ``FAIL`` and the study becomes ``BLOCKED``.
    """
    path = study_directory(results_root, study_id) / LINEAGE_EVENTS
    events = _read_events(path, f"lineage evidence for {study_id}")

    run_ids = _distinct(str(event.get("run", {}).get("runId", "")) for event in events)
    jobs = _distinct(
        f"{event.get('job', {}).get('namespace')}/{event.get('job', {}).get('name')}"
        for event in events
    )
    if len(run_ids) != 1 or len(jobs) != 1:
        raise EvidenceError(
            f"{path} does not describe one curation run: "
            f"{len(run_ids)} run IDs and {len(jobs)} jobs. "
            "'get_governance_report' carries the verdict on that."
        )

    namespace, _, name = jobs[0].partition("/")
    event_types = tuple(str(event.get("eventType")) for event in events)
    return LineageSummary(
        study_id=study_id,
        run_id=run_ids[0],
        job_namespace=namespace,
        job_name=name,
        event_types=event_types,
        complete="COMPLETE" in event_types,
        inputs=_dataset_names(events, "inputs"),
        outputs=_dataset_names(events, "outputs"),
    )


def readiness(results_root: Path, study_id: str) -> ReadinessExplanation:
    """Partition the study's governance checks into what blocks and what warns.

    No finding is invented and no status is reinterpreted: the checks come from
    the report exactly as the evaluator wrote them, sorted into the two buckets
    their own statuses put them in.
    """
    report = governance_report(results_root, study_id)
    blocking = tuple(check for check in report.checks if check.status is GovernanceCheckStatus.FAIL)
    review = tuple(check for check in report.checks if check.status is GovernanceCheckStatus.WARN)

    return ReadinessExplanation(
        study_id=report.study_id,
        decision=report.decision,
        summary=_readiness_summary(report, blocking, review),
        blocking=blocking,
        review=review,
    )


def _readiness_summary(
    report: GovernanceReport,
    blocking: tuple[GovernanceCheckResult, ...],
    review: tuple[GovernanceCheckResult, ...],
) -> str:
    """One sentence saying what the two buckets amount to."""
    study = report.study_id
    if report.decision is GovernanceDecision.READY:
        return (
            f"{study} is READY: all {len(report.checks)} governance checks passed. "
            "Nothing is blocking it and nothing is waiting on review."
        )
    if report.decision is GovernanceDecision.REVIEW:
        return (
            f"{study} is under REVIEW: nothing is blocking it, but "
            f"{_checks(review)} warned and a person should look."
        )
    warned = f", and {_checks(review)} also warned" if review else ""
    return f"{study} is BLOCKED: {_checks(blocking)} failed{warned}."


def _checks(checks: tuple[GovernanceCheckResult, ...]) -> str:
    """``2 checks (samples_contract, data_quality)``, for a summary sentence."""
    named = ", ".join(check.check_id.value for check in checks)
    return f"{len(checks)} check{'' if len(checks) == 1 else 's'} ({named})"


def _summarize(results_root: Path, study_id: str) -> StudySummary:
    """One study's listing entry, whether or not it has been evaluated."""
    try:
        report = governance_report(results_root, study_id)
    except EvidenceError as exc:
        return StudySummary(study_id=study_id, detail=f"no governance decision yet: {exc}")

    passed = sum(1 for check in report.checks if check.passed)
    return StudySummary(
        study_id=report.study_id,
        decision=report.decision,
        detail=f"{passed} of {len(report.checks)} governance checks passed",
    )


def _is_study_id(name: str) -> bool:
    try:
        AssetIdentifier.parse(f"bio://{name}/{RAW_STAGE}/samples")
    except ValueError:
        return False
    return True


def _load(model: type[ModelT], path: Path, label: str) -> ModelT:
    """Read one JSON evidence file back into the model that produced it."""
    try:
        return model.model_validate(_read_json(path, label))
    except ValidationError as exc:
        raise EvidenceError(
            f"{label} is not a {model.__name__}: {path} ({exc.error_count()} problems)"
        ) from exc


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise EvidenceError(f"{label} is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{path} is not valid JSON: {exc}") from exc


def _read_events(path: Path, label: str) -> list[dict[str, Any]]:
    """The JSON Lines of an OpenLineage file, one object per non-blank line."""
    if not path.is_file():
        raise EvidenceError(f"{label} is missing: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc.strerror or exc}") from exc

    events: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{path} line {number} is not valid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise EvidenceError(f"{path} line {number} is not an OpenLineage event")
        events.append(event)

    if not events:
        raise EvidenceError(f"{label} holds no events: {path}")
    return events


def _dataset_names(events: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    """Every dataset name the events carry under ``inputs`` or ``outputs``.

    Taken across both events and sorted: the spec does not oblige a COMPLETE
    event to repeat what its START declared, and a summary should not vary with
    the order the transport happened to write.
    """
    return tuple(
        sorted(
            {
                str(dataset.get("name"))
                for event in events
                for dataset in event.get(key) or ()
                if isinstance(dataset, dict)
            }
        )
    )


def _distinct(values: Iterable[str]) -> list[str]:
    return sorted(set(values))
