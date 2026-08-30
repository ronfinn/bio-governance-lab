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
