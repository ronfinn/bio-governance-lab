"""Smoke tests for the bio-gov command-line interface."""

import json
from pathlib import Path

from typer.testing import CliRunner

from bio_governance import __version__
from bio_governance.cli import app

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
