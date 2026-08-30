"""Tests for the study-level data-quality checks.

The fixtures are generated studies that are then damaged in specific ways, so
each test names the one check it is about. Several defects trip more than one
check — that is the honest behaviour — so the assertions look at the status of a
named check rather than only at the report as a whole.
"""

import csv
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app
from bio_governance.contracts import load_contract, validate_dataset
from bio_governance.quality import (
    QualityCheck,
    QualityCheckResult,
    QualityCheckStatus,
    QualityReport,
    StudyError,
    evaluate_study,
)
from conftest import Rows, drop_vehicle_rows, rewrite_csv

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
runner = CliRunner()


def generated_study(tmp_path: Path, *options: str) -> Path:
    """Write the demonstration study under tmp_path and return its directory."""
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path), *options])
    assert result.exit_code == 0
    return tmp_path / "BIO-001"


def rewrite_study(path: Path, **changes: object) -> None:
    """Change fields of study.json in place."""
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


def status_of(report: QualityReport, check_id: QualityCheck) -> QualityCheckStatus:
    return next(check.status for check in report.checks if check.check_id is check_id)


def message_of(report: QualityReport, check_id: QualityCheck) -> str:
    return next(check.message for check in report.checks if check.check_id is check_id)


def failing_checks(report: QualityReport) -> set[QualityCheck]:
    return {check.check_id for check in report.checks if not check.passed}


def test_a_clean_study_passes_every_check(tmp_path: Path) -> None:
    report = evaluate_study(generated_study(tmp_path))

    assert report.study_id == "BIO-001"
    assert report.overall_status is QualityCheckStatus.PASS
    assert not report.failed
    assert failing_checks(report) == set()


def test_every_check_runs_exactly_once(tmp_path: Path) -> None:
    report = evaluate_study(generated_study(tmp_path))

    assert tuple(check.check_id for check in report.checks) == tuple(QualityCheck)


def test_a_sample_count_mismatch_fails(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    rewrite_study(study / "study.json", sample_count=99)

    report = evaluate_study(study)

    assert report.failed
    assert failing_checks(report) == {QualityCheck.SAMPLE_COUNT_CONSISTENCY}
    assert "20 rows but study.json declares 99" in message_of(
        report, QualityCheck.SAMPLE_COUNT_CONSISTENCY
    )


def test_a_missing_vehicle_control_fails(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    drop_vehicle_rows(study)

    report = evaluate_study(study)

    assert report.failed
    assert status_of(report, QualityCheck.VEHICLE_CONTROL_PRESENCE) is QualityCheckStatus.FAIL
    assert "vehicle" in message_of(report, QualityCheck.VEHICLE_CONTROL_PRESENCE)


def test_an_untested_compound_fails_coverage(tmp_path: Path) -> None:
    """Two samples cannot cover three compounds, and nothing else notices."""
    study = generated_study(tmp_path, "--samples", "2", "--compounds", "3")

    report = evaluate_study(study)

    assert failing_checks(report) == {QualityCheck.COMPOUND_COVERAGE}
    assert "CMP-002, CMP-003" in message_of(report, QualityCheck.COMPOUND_COVERAGE)


def test_a_renamed_expression_column_fails_alignment(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    expression = study / "expression.csv"
    lines = expression.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = lines[0].replace("BIO-001-S003", "BIO-001-S999")
    expression.write_text("".join(lines), encoding="utf-8", newline="\n")

    report = evaluate_study(study)

    assert failing_checks(report) == {QualityCheck.EXPRESSION_SAMPLE_ALIGNMENT}
    message = message_of(report, QualityCheck.EXPRESSION_SAMPLE_ALIGNMENT)
    assert "BIO-001-S003" in message
    assert "BIO-001-S999" in message


def drop_expression_column(path: Path, column: str) -> None:
    """Remove one sample's column from the matrix, header and cells alike."""
    with path.open(encoding="utf-8", newline="") as handle:
        table = list(csv.reader(handle))
    index = table[0].index(column)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows([row[:index] + row[index + 1 :] for row in table])


def test_a_sample_with_no_measurements_fails_alignment(tmp_path: Path) -> None:
    """The other direction: a manifested sample the matrix never measured."""
    study = generated_study(tmp_path)
    drop_expression_column(study / "expression.csv", "BIO-001-S004")

    report = evaluate_study(study)

    assert failing_checks(report) == {QualityCheck.EXPRESSION_SAMPLE_ALIGNMENT}
    message = message_of(report, QualityCheck.EXPRESSION_SAMPLE_ALIGNMENT)
    assert "1 missing from expression.csv (BIO-001-S004)" in message


@pytest.mark.parametrize("bad_value", ["", "n/a", "NaN", "inf"])
def test_an_unusable_measurement_fails_completeness(tmp_path: Path, bad_value: str) -> None:
    study = generated_study(tmp_path)

    def damage(rows: Rows) -> Rows:
        rows[0]["BIO-001-S002"] = bad_value
        return rows

    rewrite_csv(study / "expression.csv", damage)

    report = evaluate_study(study)

    assert failing_checks(report) == {QualityCheck.EXPRESSION_COMPLETENESS}
    assert "1 of 240" in message_of(report, QualityCheck.EXPRESSION_COMPLETENESS)


def test_completeness_counts_rather_than_enumerating(tmp_path: Path) -> None:
    """A damaged matrix produces one finding, not one per cell."""
    study = generated_study(tmp_path)

    def damage(rows: Rows) -> Rows:
        for row in rows:
            row["BIO-001-S002"] = ""
            row["BIO-001-S003"] = ""
        return rows

    rewrite_csv(study / "expression.csv", damage)

    report = evaluate_study(study)

    assert len(report.checks) == len(QualityCheck)
    assert "24 of 240" in message_of(report, QualityCheck.EXPRESSION_COMPLETENESS)


def test_a_wrong_gene_count_fails(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    rewrite_study(study / "study.json", gene_count=30)

    report = evaluate_study(study)

    assert failing_checks(report) == {QualityCheck.EXPRESSION_GENE_COUNT}
    assert "12 genes but study.json declares 30" in message_of(
        report, QualityCheck.EXPRESSION_GENE_COUNT
    )


def test_a_contract_valid_study_can_still_fail_data_quality(tmp_path: Path) -> None:
    """The whole argument for two layers, in one test.

    Removing the controls leaves every row of samples.csv exactly as valid as it
    was, so the contract passes. The study is nonetheless unusable.
    """
    study = generated_study(tmp_path)
    drop_vehicle_rows(study)

    contract = validate_dataset(load_contract(CONTRACTS / "samples.v1.yaml"), study / "samples.csv")
    report = evaluate_study(study)

    assert contract.passed, contract.violations
    assert report.failed
    assert QualityCheck.VEHICLE_CONTROL_PRESENCE in failing_checks(report)


def test_an_unreadable_study_raises(tmp_path: Path) -> None:
    with pytest.raises(StudyError, match=r"study\.json"):
        evaluate_study(tmp_path)


def test_a_malformed_study_json_raises(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    (study / "study.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StudyError, match="not valid JSON"):
        evaluate_study(study)


def test_a_study_json_without_counts_raises(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    rewrite_study(study / "study.json", sample_count="twenty")

    with pytest.raises(StudyError, match="sample_count"):
        evaluate_study(study)


def test_the_report_serializes_to_json(tmp_path: Path) -> None:
    report = evaluate_study(generated_study(tmp_path))

    document = json.loads(json.dumps(report.model_dump(mode="json")))

    assert document["study_id"] == "BIO-001"
    assert document["overall_status"] == "pass"
    assert [check["check_id"] for check in document["checks"]] == [
        check.value for check in QualityCheck
    ]
    assert document["checks"][0]["status"] == "pass"
    assert QualityReport.model_validate(document) == report


def result(status: QualityCheckStatus) -> QualityCheckResult:
    return QualityCheckResult(
        check_id=QualityCheck.COMPOUND_COVERAGE, status=status, message="synthetic"
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([QualityCheckStatus.PASS], QualityCheckStatus.PASS),
        ([QualityCheckStatus.PASS, QualityCheckStatus.WARN], QualityCheckStatus.WARN),
        ([QualityCheckStatus.WARN, QualityCheckStatus.FAIL], QualityCheckStatus.FAIL),
        ([QualityCheckStatus.PASS, QualityCheckStatus.FAIL], QualityCheckStatus.FAIL),
    ],
)
def test_overall_status_is_the_worst_check(
    statuses: Sequence[QualityCheckStatus], expected: QualityCheckStatus
) -> None:
    report = QualityReport(study_id="BIO-001", checks=tuple(result(s) for s in statuses))

    assert report.overall_status is expected
    assert report.failed is (expected is QualityCheckStatus.FAIL)


def test_a_report_needs_at_least_one_check() -> None:
    with pytest.raises(ValueError, match="checks"):
        QualityReport(study_id="BIO-001", checks=())


def test_a_report_is_frozen(tmp_path: Path) -> None:
    report = evaluate_study(generated_study(tmp_path))

    with pytest.raises(ValueError, match="frozen"):
        report.study_id = "BIO-002"  # type: ignore[misc]
