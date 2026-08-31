"""Turning a study's accumulated evidence into one governance decision.

The pipeline leaves a results directory behind::

    results/BIO-001/
        contracts/{samples,compounds}.contract.json
        quality/dq-report.json
        curated/{samples,compounds,expression}.csv
        lineage/openlineage.jsonl

This module reads exactly that, and nothing else. There is no clock, no network
call, no catalogue lookup and no model: the same directory always produces the
same report. That determinism is the argument of the milestone — the decision
has to be reproducible and explicable by anybody who reads the code, which rules
out anything that could answer differently on a second run.

Evidence that is missing, unparseable or inconsistent is a ``FAIL``, not an
exception. A curated file that was never written and a curated file whose
provenance is incoherent are both governance failures, and a governance layer
that crashes instead of returning ``BLOCKED`` has failed to do its job.
:class:`GovernanceError` is reserved for the one case where no verdict is
possible at all: the results directory itself cannot be read as a study.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bio_governance.contracts import ContractValidationResult
from bio_governance.governance.models import (
    GovernanceCheck,
    GovernanceCheckResult,
    GovernanceCheckStatus,
    GovernanceReport,
)
from bio_governance.lineage import (
    CURATED_STAGE,
    DATASET_FILES,
    JOB_NAME,
    NAMESPACE,
    RAW_STAGE,
)
from bio_governance.models import AssetIdentifier
from bio_governance.quality import QualityCheckStatus, QualityReport

#: Where each kind of evidence sits under a results directory. These mirror what
#: ``pipelines/nextflow/main.nf`` publishes; the pipeline and the evaluator have
#: to agree on the layout, and this is the side that states it.
CONTRACTS_SUBDIR = "contracts"
SAMPLES_EVIDENCE = "samples.contract.json"
COMPOUNDS_EVIDENCE = "compounds.contract.json"
CURATED_SUBDIR = "curated"
QUALITY_REPORT = Path("quality") / "dq-report.json"
LINEAGE_EVENTS = Path("lineage") / "openlineage.jsonl"

#: The OpenLineage run states one emission must consist of, exactly once each.
REQUIRED_EVENTS = ("START", "COMPLETE")

#: A data-quality verdict maps straight onto a governance status. A warning is
#: carried through rather than flattened: the point of ``REVIEW`` is that
#: something wants a human without stopping the study being used.
_QUALITY_STATUS = {
    QualityCheckStatus.PASS: GovernanceCheckStatus.PASS,
    QualityCheckStatus.WARN: GovernanceCheckStatus.WARN,
    QualityCheckStatus.FAIL: GovernanceCheckStatus.FAIL,
}


class GovernanceError(Exception):
    """The results directory could not be read as a study's evidence."""


class _EvidenceError(Exception):
    """Evidence is absent or not the shape it must be — reported as a FAIL."""


def evaluate_governance(results_dir: Path) -> GovernanceReport:
    """Evaluate one study's evidence and return its governance report.

    ``results_dir`` is a pipeline results directory named for the study, such as
    ``results/BIO-001``. Every check runs, so a report describes everything that
    is wrong in one pass rather than stopping at the first failure.
    """
    study_id = _study_id(results_dir)
    contracts = results_dir / CONTRACTS_SUBDIR

    return GovernanceReport(
        study_id=study_id,
        checks=(
            _contract_check(
                GovernanceCheck.SAMPLES_CONTRACT, contracts / SAMPLES_EVIDENCE, "samples"
            ),
            _contract_check(
                GovernanceCheck.COMPOUNDS_CONTRACT, contracts / COMPOUNDS_EVIDENCE, "compounds"
            ),
            _quality_check(results_dir / QUALITY_REPORT),
            _curated_check(results_dir / CURATED_SUBDIR),
            _lineage_check(study_id, results_dir / LINEAGE_EVENTS),
        ),
    )


def _study_id(results_dir: Path) -> str:
    """Take the study identifier from the results directory's name."""
    if not results_dir.is_dir():
        raise GovernanceError(f"results directory not found: {results_dir}")
    study_id = results_dir.resolve().name
    try:
        AssetIdentifier.parse(f"bio://{study_id}/{RAW_STAGE}/samples")
    except ValueError as exc:
        raise GovernanceError(f"{results_dir} is not named for a study: {exc}") from exc
    return study_id


def _contract_check(check_id: GovernanceCheck, path: Path, dataset: str) -> GovernanceCheckResult:
    """Was the structured contract evidence written, and did the dataset conform?

    The evidence is deserialized back into the very
    :class:`~bio_governance.contracts.models.ContractValidationResult` the
    validator produced, so ``passed`` is the validator's own definition rather
    than this module's reading of a JSON field.
    """
    try:
        result = ContractValidationResult.model_validate(_read_json(path, f"{dataset} contract"))
    except _EvidenceError as exc:
        return _result(check_id, GovernanceCheckStatus.FAIL, str(exc))
    except ValidationError:
        return _result(
            check_id,
            GovernanceCheckStatus.FAIL,
            f"{path} is not a contract validation result",
        )

    if result.passed:
        return _result(
            check_id,
            GovernanceCheckStatus.PASS,
            f"{result.label} passed over {result.rows_checked} rows",
        )
    count = len(result.violations)
    return _result(
        check_id,
        GovernanceCheckStatus.FAIL,
        f"{result.label} reported {count} violation{'' if count == 1 else 's'}",
    )


def _quality_check(path: Path) -> GovernanceCheckResult:
    """Carry the data-quality verdict through as a governance status."""
    check_id = GovernanceCheck.DATA_QUALITY
    try:
        report = QualityReport.model_validate(_read_json(path, "quality report"))
    except _EvidenceError as exc:
        return _result(check_id, GovernanceCheckStatus.FAIL, str(exc))
    except ValidationError:
        return _result(check_id, GovernanceCheckStatus.FAIL, f"{path} is not a quality report")

    status = _QUALITY_STATUS[report.overall_status]
    if status is GovernanceCheckStatus.PASS:
        return _result(check_id, status, f"all {len(report.checks)} quality checks passed")
    named = ", ".join(
        check.check_id.value for check in report.checks if check.status is report.overall_status
    )
    return _result(check_id, status, f"data quality {report.overall_status.value.upper()}: {named}")


def _curated_check(curated_dir: Path) -> GovernanceCheckResult:
    """Are all three curated outputs actually on disk?"""
    check_id = GovernanceCheck.CURATED_OUTPUTS
    expected = [file_name for _, file_name in DATASET_FILES]
    missing = [name for name in expected if not (curated_dir / name).is_file()]

    if missing:
        return _result(
            check_id,
            GovernanceCheckStatus.FAIL,
            f"{len(missing)} of {len(expected)} curated outputs are missing from "
            f"{curated_dir}: {', '.join(missing)}",
        )
    return _result(
        check_id, GovernanceCheckStatus.PASS, f"all {len(expected)} curated outputs are present"
    )


def _lineage_check(study_id: str, path: Path) -> GovernanceCheckResult:
    """Does the provenance evidence describe one coherent curation run?

    Presence is not enough. The events have to be one START and one COMPLETE of
    a single run of the known job, naming the raw datasets as inputs and the
    curated datasets as outputs. Provenance that does not say that is provenance
    for something other than this study's curation.
    """
    check_id = GovernanceCheck.LINEAGE_EVIDENCE
    try:
        events = _read_events(path)
    except _EvidenceError as exc:
        return _result(check_id, GovernanceCheckStatus.FAIL, str(exc))

    problem = _lineage_problem(study_id, events)
    if problem is not None:
        return _result(check_id, GovernanceCheckStatus.FAIL, problem)

    run_id = str(events[0].get("run", {}).get("runId"))
    return _result(
        check_id,
        GovernanceCheckStatus.PASS,
        f"{NAMESPACE}/{JOB_NAME} run {run_id} records the curation of {study_id}",
    )


def _lineage_problem(study_id: str, events: Sequence[dict[str, Any]]) -> str | None:
    """The first thing wrong with a run's events, or None if nothing is."""
    states = [str(event.get("eventType")) for event in events]
    if sorted(states) != sorted(REQUIRED_EVENTS):
        found = ", ".join(states) if states else "none"
        return f"expected exactly one START and one COMPLETE event, found: {found}"

    run_ids = {str(event.get("run", {}).get("runId")) for event in events}
    if len(run_ids) != 1:
        return f"the events do not share one run ID: {', '.join(sorted(run_ids))}"

    jobs = {
        (str(event.get("job", {}).get("namespace")), str(event.get("job", {}).get("name")))
        for event in events
    }
    if jobs != {(NAMESPACE, JOB_NAME)}:
        named = ", ".join(f"{namespace}/{name}" for namespace, name in sorted(jobs))
        return f"expected the {NAMESPACE}/{JOB_NAME} job, found: {named}"

    for stage, key in ((RAW_STAGE, "inputs"), (CURATED_STAGE, "outputs")):
        expected = {_identifier(study_id, stage, name).uri for name, _ in DATASET_FILES}
        missing = expected - _dataset_names(events, key)
        if missing:
            return f"{key} do not name {', '.join(sorted(missing))}"
    return None


def _identifier(study_id: str, stage: str, name: str) -> AssetIdentifier:
    return AssetIdentifier.parse(f"bio://{study_id}/{stage}/{name}")


def _dataset_names(events: Sequence[dict[str, Any]], key: str) -> set[str]:
    """Every dataset name the events carry under ``inputs`` or ``outputs``.

    Taken across both events rather than from one of them: the spec does not
    oblige a COMPLETE event to repeat what its START already declared.
    """
    return {
        str(dataset.get("name"))
        for event in events
        for dataset in event.get(key) or ()
        if isinstance(dataset, dict)
    }


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise _EvidenceError(f"{label} evidence is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _EvidenceError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise _EvidenceError(f"{path} is not valid JSON: {exc}") from exc


def _read_events(path: Path) -> list[dict[str, Any]]:
    """The JSON Lines of an OpenLineage file, one object per non-blank line."""
    if not path.is_file():
        raise _EvidenceError(f"lineage evidence is missing: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _EvidenceError(f"cannot read {path}: {exc.strerror or exc}") from exc

    events: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _EvidenceError(f"{path} line {number} is not valid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise _EvidenceError(f"{path} line {number} is not an OpenLineage event")
        events.append(event)
    return events


def _result(
    check_id: GovernanceCheck, status: GovernanceCheckStatus, message: str
) -> GovernanceCheckResult:
    return GovernanceCheckResult(check_id=check_id, status=status, message=message)
