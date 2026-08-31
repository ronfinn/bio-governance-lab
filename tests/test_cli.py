"""Smoke tests for the bio-gov command-line interface."""

import json
from pathlib import Path

from typer.testing import CliRunner

from bio_governance import __version__
from bio_governance.cli import app
from conftest import build_results

runner = CliRunner()


def test_help_lists_the_command_name() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "bio-gov" in result.output


def test_version_reports_the_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "Usage" in result.output


def test_info_command_runs() -> None:
    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_demo_generate_writes_a_study(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path)])

    assert result.exit_code == 0
    study_dir = tmp_path / "BIO-001"
    assert sorted(path.name for path in study_dir.iterdir()) == [
        "compounds.csv",
        "expression.csv",
        "samples.csv",
        "study.json",
    ]
    assert "BIO-001" in result.output


def test_demo_generate_accepts_the_documented_options(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "demo",
            "generate",
            "--study",
            "BIO-002",
            "--samples",
            "48",
            "--compounds",
            "4",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    study = json.loads((tmp_path / "BIO-002" / "study.json").read_text(encoding="utf-8"))
    assert (study["sample_count"], study["compound_count"], study["seed"]) == (48, 4, 42)


def test_demo_generate_reports_injected_defects(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["demo", "generate", "--output", str(tmp_path), "--inject-duplicate-sample"],
    )

    assert result.exit_code == 0
    assert "duplicate_sample" in result.output


def test_demo_generate_rejects_a_malformed_study_id(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "generate", "--study", "nope", "--output", str(tmp_path)])

    assert result.exit_code != 0
    assert not tmp_path.joinpath("nope").exists()


CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def generated_study(tmp_path: Path, *injections: str) -> Path:
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path), *injections])
    assert result.exit_code == 0
    return tmp_path / "BIO-001"


def test_contract_validate_returns_zero_for_clean_samples(tmp_path: Path) -> None:
    study = generated_study(tmp_path)

    result = runner.invoke(
        app,
        ["contract", "validate", str(CONTRACTS / "samples.v1.yaml"), str(study / "samples.csv")],
    )

    assert result.exit_code == 0
    assert "Contract: bio.samples@1.0.0" in result.output
    assert "PASS" in result.output
    assert "Rows checked: 20" in result.output


def test_contract_validate_returns_zero_for_clean_compounds(tmp_path: Path) -> None:
    study = generated_study(tmp_path)

    result = runner.invoke(
        app,
        [
            "contract",
            "validate",
            str(CONTRACTS / "compounds.v1.yaml"),
            str(study / "compounds.csv"),
        ],
    )

    assert result.exit_code == 0
    assert "bio.compounds@1.0.0" in result.output
    assert "PASS" in result.output


def test_contract_validate_returns_non_zero_for_invalid_data(tmp_path: Path) -> None:
    study = generated_study(
        tmp_path,
        "--inject-missing-sample-id",
        "--inject-invalid-dose",
        "--inject-duplicate-sample",
        "--inject-unknown-compound",
    )

    result = runner.invoke(
        app,
        ["contract", "validate", str(CONTRACTS / "samples.v1.yaml"), str(study / "samples.csv")],
    )

    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "4 violations" in result.output
    for rule in ("required", "minimum", "unique", "foreign_key"):
        assert rule in result.output


def test_contract_validate_reports_a_malformed_contract(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    contract = tmp_path / "broken.yaml"
    contract.write_text("contract_id: bio.samples\n  version: [1\n", encoding="utf-8")

    result = runner.invoke(app, ["contract", "validate", str(contract), str(study / "samples.csv")])

    assert result.exit_code == 2
    assert "not valid YAML" in result.output


def test_contract_validate_rejects_a_missing_dataset(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["contract", "validate", str(CONTRACTS / "samples.v1.yaml"), str(tmp_path / "absent.csv")],
    )

    assert result.exit_code != 0


def test_contract_help_is_listed(tmp_path: Path) -> None:
    result = runner.invoke(app, ["contract", "--help"])

    assert result.exit_code == 0
    assert "validate" in result.output


def test_dq_help_is_listed() -> None:
    result = runner.invoke(app, ["dq", "--help"])

    assert result.exit_code == 0
    assert "run" in result.output


def test_lineage_help_is_listed() -> None:
    result = runner.invoke(app, ["lineage", "--help"])

    assert result.exit_code == 0
    assert "emit" in result.output


def test_dq_run_returns_zero_for_a_clean_study(tmp_path: Path) -> None:
    study = generated_study(tmp_path)

    result = runner.invoke(app, ["dq", "run", str(study)])

    assert result.exit_code == 0
    assert "Study: BIO-001" in result.output
    assert "Data quality: PASS" in result.output
    for check in (
        "sample_count_consistency",
        "vehicle_control_presence",
        "compound_coverage",
        "expression_sample_alignment",
        "expression_completeness",
        "expression_gene_count",
    ):
        assert f"PASS  {check}" in result.output


def test_dq_run_returns_one_for_a_failing_study(tmp_path: Path) -> None:
    study = generated_study(tmp_path, "--samples", "2", "--compounds", "3")

    result = runner.invoke(app, ["dq", "run", str(study)])

    assert result.exit_code == 1
    assert "Data quality: FAIL" in result.output
    assert "FAIL  compound_coverage" in result.output
    assert "CMP-002, CMP-003" in result.output


def test_dq_run_writes_the_structured_report(tmp_path: Path) -> None:
    study = generated_study(tmp_path)
    report_path = tmp_path / "results" / "BIO-001" / "quality" / "dq-report.json"

    result = runner.invoke(app, ["dq", "run", str(study), "--json-out", str(report_path)])

    assert result.exit_code == 0
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["study_id"] == "BIO-001"
    assert document["overall_status"] == "pass"
    assert len(document["checks"]) == 6
    assert str(report_path) in result.output


def test_dq_run_writes_the_report_for_a_failing_study_too(tmp_path: Path) -> None:
    """The evidence is most wanted exactly when the gate refuses to open."""
    study = generated_study(tmp_path, "--samples", "2", "--compounds", "3")
    report_path = tmp_path / "dq-report.json"

    result = runner.invoke(app, ["dq", "run", str(study), "--json-out", str(report_path)])

    assert result.exit_code == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["overall_status"] == "fail"


def test_dq_run_returns_two_for_a_directory_that_is_not_a_study(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dq", "run", str(tmp_path)])

    assert result.exit_code == 2
    assert "study.json" in result.output


def test_dq_run_rejects_a_missing_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dq", "run", str(tmp_path / "absent")])

    assert result.exit_code != 0


def test_contract_validate_writes_the_structured_result(tmp_path: Path) -> None:
    """The evidence the governance layer reads is the validator's own result."""
    study = generated_study(tmp_path)
    result_path = tmp_path / "results" / "BIO-001" / "contracts" / "samples.contract.json"

    result = runner.invoke(
        app,
        [
            "contract",
            "validate",
            str(CONTRACTS / "samples.v1.yaml"),
            str(study / "samples.csv"),
            "--json-out",
            str(result_path),
        ],
    )

    assert result.exit_code == 0
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["contract_id"] == "bio.samples"
    assert document["version"] == "1.0.0"
    assert document["rows_checked"] == 20
    assert document["violations"] == []
    assert document["passed"] is True
    assert str(result_path) in result.output


def test_contract_validate_writes_the_result_for_a_failing_dataset_too(tmp_path: Path) -> None:
    study = generated_study(tmp_path, "--inject-invalid-dose")
    result_path = tmp_path / "samples.contract.json"

    result = runner.invoke(
        app,
        [
            "contract",
            "validate",
            str(CONTRACTS / "samples.v1.yaml"),
            str(study / "samples.csv"),
            "--json-out",
            str(result_path),
        ],
    )

    assert result.exit_code == 1
    document = json.loads(result_path.read_text(encoding="utf-8"))
    assert document["passed"] is False
    assert [violation["rule"] for violation in document["violations"]] == ["minimum"]


def test_governance_help_is_listed() -> None:
    result = runner.invoke(app, ["governance", "--help"])

    assert result.exit_code == 0
    assert "evaluate" in result.output


def test_governance_evaluate_returns_zero_for_a_ready_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)

    result = runner.invoke(app, ["governance", "evaluate", str(results)])

    assert result.exit_code == 0
    assert "Study: BIO-001" in result.output
    assert "Decision: READY" in result.output
    for check in (
        "samples_contract",
        "compounds_contract",
        "data_quality",
        "curated_outputs",
        "lineage_evidence",
    ):
        assert f"PASS  {check}" in result.output


def test_governance_evaluate_returns_one_for_a_blocked_study(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    (results / "lineage" / "openlineage.jsonl").unlink()

    result = runner.invoke(app, ["governance", "evaluate", str(results)])

    assert result.exit_code == 1
    assert "Decision: BLOCKED" in result.output
    assert "FAIL  lineage_evidence" in result.output


def test_governance_evaluate_writes_the_structured_report(tmp_path: Path) -> None:
    results = build_results(tmp_path)
    report_path = results / "governance" / "governance-report.json"

    result = runner.invoke(
        app, ["governance", "evaluate", str(results), "--json-out", str(report_path)]
    )

    assert result.exit_code == 0
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["decision"] == "ready"
    assert len(document["checks"]) == 5
    assert str(report_path) in result.output


def test_governance_evaluate_returns_two_for_a_directory_that_is_not_a_study(
    tmp_path: Path,
) -> None:
    result = runner.invoke(app, ["governance", "evaluate", str(tmp_path)])

    assert result.exit_code == 2
    assert "not named for a study" in result.output


def test_mcp_help_is_listed() -> None:
    result = runner.invoke(app, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "serve" in result.output


def test_mcp_serve_help_says_which_transport_it_speaks() -> None:
    """A single word, because Rich wraps a help panel to the terminal's width.

    The other help tests assert on one unbreakable word for the same reason; a
    flag name or a phrase can be split across lines by a narrower terminal than
    the one the test was written on.
    """
    result = runner.invoke(app, ["mcp", "serve", "--help"])

    assert result.exit_code == 0
    assert "stdio" in result.output


def test_mcp_serve_rejects_a_results_root_that_does_not_exist(tmp_path: Path) -> None:
    """Also what proves --results-root exists, without reading it out of the help."""
    result = runner.invoke(app, ["mcp", "serve", "--results-root", str(tmp_path / "nowhere")])

    assert result.exit_code == 2
