"""Tests for the contract-gated Nextflow pipeline.

The pipeline's claim is that raw data cannot reach the curated directory unless
the contract gate passes, so the tests that matter run Nextflow for real. They
are skipped where Nextflow is not installed; the static checks below still hold
the pipeline's parameter surface and process names in place.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "pipelines" / "nextflow" / "main.nf"
CONFIG = REPO_ROOT / "pipelines" / "nextflow" / "nextflow.config"
CONTRACTS = REPO_ROOT / "contracts"

runner = CliRunner()

needs_nextflow = pytest.mark.skipif(
    shutil.which("nextflow") is None, reason="nextflow is not installed"
)


def generated_study(tmp_path: Path, *injections: str) -> Path:
    """Write the demonstration study under tmp_path and return its directory."""
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path), *injections])
    assert result.exit_code == 0
    return tmp_path / "BIO-001"


def run_pipeline(tmp_path: Path, study_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the pipeline over study_dir, keeping every artefact inside tmp_path."""
    return subprocess.run(
        [
            "nextflow",
            "-log",
            str(tmp_path / "nextflow.log"),
            "run",
            str(PIPELINE),
            "-ansi-log",
            "false",
            "-work-dir",
            str(tmp_path / "work"),
            "--study_dir",
            str(study_dir),
            "--samples_contract",
            str(CONTRACTS / "samples.v1.yaml"),
            "--compounds_contract",
            str(CONTRACTS / "compounds.v1.yaml"),
            "--outdir",
            str(tmp_path / "results"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )


def test_pipeline_files_are_present() -> None:
    assert PIPELINE.is_file()
    assert CONFIG.is_file()


def test_the_gate_is_named_in_the_process_names() -> None:
    script = PIPELINE.read_text(encoding="utf-8")

    assert "process CONTRACT_GATE_COMPOUNDS" in script
    assert "process CONTRACT_GATE_SAMPLES" in script
    assert "process CURATE" in script


def test_the_pipeline_declares_the_documented_parameters() -> None:
    config = CONFIG.read_text(encoding="utf-8")

    for name in ("study_dir", "samples_contract", "compounds_contract", "outdir"):
        assert name in config


def test_curation_never_runs_before_the_gate() -> None:
    """CURATE must consume the samples gate's output, not the study directly."""
    script = PIPELINE.read_text(encoding="utf-8")

    curate_call = script.index("CURATE(\n")
    assert "samples_passed" in script[curate_call : curate_call + 200]


@needs_nextflow
def test_clean_data_passes_the_gate_and_is_curated(tmp_path: Path) -> None:
    study = generated_study(tmp_path)

    result = run_pipeline(tmp_path, study)

    assert result.returncode == 0, result.stdout + result.stderr
    curated = tmp_path / "results" / "BIO-001" / "curated"
    assert sorted(path.name for path in curated.iterdir()) == [
        "compounds.csv",
        "expression.csv",
        "samples.csv",
    ]
    reports = tmp_path / "results" / "BIO-001" / "contracts"
    assert "PASS" in (reports / "samples.contract.txt").read_text(encoding="utf-8")


@needs_nextflow
def test_broken_data_stops_at_the_gate_and_is_not_curated(tmp_path: Path) -> None:
    study = generated_study(
        tmp_path,
        "--inject-missing-sample-id",
        "--inject-invalid-dose",
        "--inject-duplicate-sample",
        "--inject-unknown-compound",
    )

    result = run_pipeline(tmp_path, study)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "CONTRACT_GATE_SAMPLES" in output
    assert "FAIL" in output
    assert not (tmp_path / "results" / "BIO-001" / "curated").exists()
