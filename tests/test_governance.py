"""Tests for the deterministic governance evaluation.

The subject here is a *decision*, so the tests are about two things: that the
decision follows from the checks and cannot be asserted independently of them,
and that each check reads real evidence correctly. The evidence in these tests
is produced by the same commands the pipeline runs — ``build_results`` in
``conftest.py`` — and then damaged in one specific way, so a test that says
"missing lineage blocks the study" is about a directory that really is missing
its lineage rather than a hand-written fixture shaped to fail.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app
from bio_governance.governance import (
    GovernanceCheck,
    GovernanceCheckResult,
    GovernanceCheckStatus,
    GovernanceDecision,
    GovernanceError,
    GovernanceReport,
    evaluate_governance,
)
from conftest import (
    CONTRACTS_DIR,
    GOVERNANCE_DIR,
    METADATA_EVIDENCE,
    build_results,
    rewrite_csv,
    validate_declaration,
)

runner = CliRunner()


def statuses(report: GovernanceReport) -> dict[GovernanceCheck, GovernanceCheckStatus]:
    """The report as a mapping, so a test can name the check it is about."""
    return {check.check_id: check.status for check in report.checks}


def check(status: GovernanceCheckStatus) -> GovernanceCheckResult:
    """A throwaway check result, for the tests about the decision itself."""
    return GovernanceCheckResult(
        check_id=GovernanceCheck.DATA_QUALITY, status=status, message="synthetic"
    )


def warn_the_quality_report(results: Path) -> None:
    """Turn one quality finding into a WARN, leaving the report otherwise intact.

    The generator is deliberately not changed to manufacture this: none of the
    six quality checks currently warns, and inventing a warning in the generator
    to exercise the governance layer would put a defect in the wrong place.
    """
    path = results / "quality" / "dq-report.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["checks"][0]["status"] = "warn"
    document["overall_status"] = "warn"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def fail_the_contract(results: Path, tmp_path: Path, dataset: str, key: str) -> None:
    """Replace one contract result with a genuinely failing one.

    A copy of the study has the dataset's key column blanked, which breaks the
    contract's ``required`` rule, and the validator's own verdict over that copy
    overwrites the evidence. The failure is never hand-written: governance has
    to be reading what the validator said.
    """
    broken = tmp_path / f"broken-{dataset}"
    assert runner.invoke(app, ["demo", "generate", "--output", str(broken)]).exit_code == 0
    study = broken / "BIO-001"
    rewrite_csv(
        study / f"{dataset}.csv",
        lambda rows: [{**row, key: ""} if index == 0 else row for index, row in enumerate(rows)],
    )

    result = runner.invoke(
        app,
        [
            "contract",
            "validate",
            str(CONTRACTS_DIR / f"{dataset}.v1.yaml"),
            str(study / f"{dataset}.csv"),
            "--json-out",
            str(results / "contracts" / f"{dataset}.contract.json"),
        ],
    )
    assert result.exit_code == 1, result.output


# --- the decision, and that it is derived ---------------------------------


def test_clean_evidence_is_ready(tmp_path: Path) -> None:
    report = evaluate_governance(build_results(tmp_path))

    assert report.study_id == "BIO-001"
    assert report.decision is GovernanceDecision.READY
    assert set(statuses(report)) == set(GovernanceCheck)
    assert all(check.passed for check in report.checks)


def test_the_decision_is_derived_from_the_checks_and_cannot_be_asserted() -> None:
    """A report cannot claim a verdict its checks do not support.

    ``decision`` is computed, so a caller passing one is not rejected — it is
    simply not a field, and the checks answer anyway. That is the guarantee the
    milestone rests on: whatever hands a report around, including a language
    model in a later milestone, has no way to write the verdict.
    """
    claimed = GovernanceReport(
        study_id="BIO-001",
        checks=(check(GovernanceCheckStatus.FAIL),),
        decision=GovernanceDecision.READY,  # type: ignore[call-arg]
    )

    assert claimed.decision is GovernanceDecision.BLOCKED
    assert claimed.model_dump(mode="json")["decision"] == "blocked"


def test_any_failure_blocks_however_many_checks_pass() -> None:
    report = GovernanceReport(
        study_id="BIO-001",
        checks=(
            check(GovernanceCheckStatus.PASS),
            check(GovernanceCheckStatus.WARN),
            check(GovernanceCheckStatus.FAIL),
        ),
    )

    assert report.decision is GovernanceDecision.BLOCKED


def test_a_warning_without_a_failure_asks_for_review() -> None:
    report = GovernanceReport(
        study_id="BIO-001",
        checks=(check(GovernanceCheckStatus.PASS), check(GovernanceCheckStatus.WARN)),
    )

    assert report.decision is GovernanceDecision.REVIEW
    assert not report.ready


# --- the seven checks -----------------------------------------------------


def test_a_failing_samples_contract_blocks_the_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    fail_the_contract(results, tmp_path, "samples", "sample_id")

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.SAMPLES_CONTRACT] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_a_failing_compounds_contract_blocks_the_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    fail_the_contract(results, tmp_path, "compounds", "compound_id")

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.COMPOUNDS_CONTRACT] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_a_quality_warning_is_carried_through_as_a_review(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    warn_the_quality_report(results)

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.DATA_QUALITY] is GovernanceCheckStatus.WARN
    assert report.decision is GovernanceDecision.REVIEW


def test_a_failing_quality_report_blocks_the_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    path = results / "quality" / "dq-report.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["checks"][1]["status"] = "fail"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.DATA_QUALITY] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_a_missing_curated_output_blocks_the_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    (results / "curated" / "expression.csv").unlink()

    report = evaluate_governance(results)

    failed = next(c for c in report.checks if c.check_id is GovernanceCheck.CURATED_OUTPUTS)
    assert failed.status is GovernanceCheckStatus.FAIL
    assert "expression.csv" in failed.message
    assert report.decision is GovernanceDecision.BLOCKED


def test_valid_lineage_evidence_passes(tmp_path: Path) -> None:
    results = build_results(tmp_path)

    report = evaluate_governance(results)

    passed = next(c for c in report.checks if c.check_id is GovernanceCheck.LINEAGE_EVIDENCE)
    assert passed.status is GovernanceCheckStatus.PASS
    assert "curate-study" in passed.message


def test_missing_lineage_evidence_blocks_the_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    (results / "lineage" / "openlineage.jsonl").unlink()

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.LINEAGE_EVIDENCE] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_lineage_events_from_two_runs_block_the_study(tmp_path: Path) -> None:
    """Two events are not evidence of one run unless they share a run ID."""
    results = build_results(tmp_path)
    path = results / "lineage" / "openlineage.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[1]["run"]["runId"] = "11111111-2222-3333-4444-555555555555"
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    report = evaluate_governance(results)

    failed = next(c for c in report.checks if c.check_id is GovernanceCheck.LINEAGE_EVIDENCE)
    assert failed.status is GovernanceCheckStatus.FAIL
    assert "run ID" in failed.message
    assert report.decision is GovernanceDecision.BLOCKED


def test_lineage_naming_another_study_blocks_the_study(tmp_path: Path) -> None:
    """Provenance for something else is not provenance for this curation."""
    results = build_results(tmp_path)
    path = results / "lineage" / "openlineage.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "bio://BIO-001/curated/", "bio://BIO-009/curated/"
        ),
        encoding="utf-8",
    )

    report = evaluate_governance(results)

    failed = next(c for c in report.checks if c.check_id is GovernanceCheck.LINEAGE_EVIDENCE)
    assert failed.status is GovernanceCheckStatus.FAIL
    assert "outputs" in failed.message


# --- ownership and classification -----------------------------------------


def redeclare(results: Path, tmp_path: Path, text: str) -> None:
    """Replace the metadata evidence with the validator's verdict on ``text``.

    The declaration is written under tmp_path and judged by the same command
    the pipeline's gate runs, against the same generated study. The evidence is
    never hand-written: governance has to be reading what the validator said.
    """
    declaration = tmp_path / "declaration.yaml"
    declaration.write_text(text, encoding="utf-8")
    validate_declaration(tmp_path / "data" / "BIO-001", results, declaration, expect=1)


SHIPPED = (GOVERNANCE_DIR / "BIO-001.yaml").read_text(encoding="utf-8")


def test_a_valid_declaration_passes_both_checks_and_names_who_answers(tmp_path: Path) -> None:
    report = evaluate_governance(build_results(tmp_path))

    found = {c.check_id: c for c in report.checks}
    assert found[GovernanceCheck.OWNERSHIP].passed
    assert "Avery Example" in found[GovernanceCheck.OWNERSHIP].message
    assert "Jordan Example" in found[GovernanceCheck.OWNERSHIP].message
    assert found[GovernanceCheck.CLASSIFICATION].passed
    assert "internal" in found[GovernanceCheck.CLASSIFICATION].message


def test_missing_governance_metadata_cannot_be_ready(tmp_path: Path) -> None:
    """No declaration means no evidence for either claim, whatever else passed."""
    results = build_results(tmp_path)
    (results / METADATA_EVIDENCE).unlink()

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.OWNERSHIP] is GovernanceCheckStatus.FAIL
    assert statuses(report)[GovernanceCheck.CLASSIFICATION] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED
    others = [c for c in report.checks if c.check_id not in _METADATA_CHECKS]
    assert all(c.passed for c in others)


_METADATA_CHECKS = {GovernanceCheck.OWNERSHIP, GovernanceCheck.CLASSIFICATION}


def test_an_invalid_classification_fails_classification_and_not_ownership(
    tmp_path: Path,
) -> None:
    results = build_results(tmp_path)
    redeclare(
        results, tmp_path, SHIPPED.replace("classification: internal", "classification: secret")
    )

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.CLASSIFICATION] is GovernanceCheckStatus.FAIL
    assert statuses(report)[GovernanceCheck.OWNERSHIP] is GovernanceCheckStatus.PASS
    assert report.decision is GovernanceDecision.BLOCKED


def test_a_missing_steward_fails_ownership_and_not_classification(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    redeclare(results, tmp_path, SHIPPED.replace("  steward: Jordan Example\n", ""))

    report = evaluate_governance(results)

    failed = next(c for c in report.checks if c.check_id is GovernanceCheck.OWNERSHIP)
    assert failed.status is GovernanceCheckStatus.FAIL
    assert "steward" in failed.message
    assert statuses(report)[GovernanceCheck.CLASSIFICATION] is GovernanceCheckStatus.PASS
    assert report.decision is GovernanceDecision.BLOCKED


@pytest.mark.parametrize(
    "text",
    [
        SHIPPED.replace("study_id: BIO-001", "study_id: BIO-002"),
        "study_id: [BIO-001\n",
        SHIPPED + "retention_days: 30\n",
    ],
    ids=["another-study", "malformed-yaml", "unsupported-field"],
)
def test_a_declaration_that_fails_as_a_whole_fails_both_checks(tmp_path: Path, text: str) -> None:
    results = build_results(tmp_path)
    redeclare(results, tmp_path, text)

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.OWNERSHIP] is GovernanceCheckStatus.FAIL
    assert statuses(report)[GovernanceCheck.CLASSIFICATION] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_metadata_evidence_for_another_study_blocks_the_study(tmp_path: Path) -> None:
    """BIO-002's valid declaration, judged against BIO-002, is no evidence for BIO-001."""
    results = build_results(tmp_path)
    other = tmp_path / "other"
    generated = runner.invoke(
        app, ["demo", "generate", "--study", "BIO-002", "--output", str(other)]
    )
    assert generated.exit_code == 0, generated.output
    validate_declaration(other / "BIO-002", results)

    report = evaluate_governance(results)

    failed = next(c for c in report.checks if c.check_id is GovernanceCheck.OWNERSHIP)
    assert failed.status is GovernanceCheckStatus.FAIL
    assert "BIO-002" in failed.message
    assert report.decision is GovernanceDecision.BLOCKED


def test_evidence_edited_to_claim_it_passed_still_fails(tmp_path: Path) -> None:
    """``passed`` is computed from the problems, so editing it changes nothing."""
    results = build_results(tmp_path)
    redeclare(
        results, tmp_path, SHIPPED.replace("classification: internal", "classification: secret")
    )
    path = results / METADATA_EVIDENCE
    document = json.loads(path.read_text(encoding="utf-8"))
    document["passed"] = True
    path.write_text(json.dumps(document), encoding="utf-8")

    report = evaluate_governance(results)

    assert statuses(report)[GovernanceCheck.CLASSIFICATION] is GovernanceCheckStatus.FAIL
    assert report.decision is GovernanceDecision.BLOCKED


def test_a_restricted_study_is_as_ready_as_a_public_one(tmp_path: Path) -> None:
    """The check is that a classification was declared, not what it permits."""
    results = build_results(tmp_path)
    declaration = tmp_path / "restricted.yaml"
    declaration.write_text(SHIPPED.replace("internal", "restricted"), encoding="utf-8")
    validate_declaration(tmp_path / "data" / "BIO-001", results, declaration)

    report = evaluate_governance(results)

    assert report.decision is GovernanceDecision.READY


# --- serialization and the unreadable case --------------------------------


def test_the_report_serializes_with_its_decision(tmp_path: Path) -> None:
    report = evaluate_governance(build_results(tmp_path))

    document = json.loads(json.dumps(report.model_dump(mode="json")))

    assert document["study_id"] == "BIO-001"
    assert document["decision"] == "ready"
    assert [entry["check_id"] for entry in document["checks"]] == [
        check_id.value for check_id in GovernanceCheck
    ]
    assert GovernanceReport.model_validate(document) == report


def test_a_directory_that_is_not_a_study_cannot_be_evaluated(tmp_path: Path) -> None:
    """The one case with no verdict: nothing says which study this would be."""
    unnamed = tmp_path / "not-a-study"
    unnamed.mkdir()

    with pytest.raises(GovernanceError):
        evaluate_governance(unnamed)


def test_an_empty_results_directory_is_blocked_rather_than_an_error(tmp_path: Path) -> None:
    """Absent evidence is a governance failure, not a crash."""
    empty = tmp_path / "BIO-001"
    empty.mkdir()

    report = evaluate_governance(empty)

    assert report.decision is GovernanceDecision.BLOCKED
    assert all(check.status is GovernanceCheckStatus.FAIL for check in report.checks)
