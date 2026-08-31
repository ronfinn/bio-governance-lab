"""Tests for the governance-gated Nextflow pipeline.

The pipeline's claim is that raw data cannot reach the curated directory unless
both gates pass — the contracts, and then data quality — so the tests that
matter run Nextflow for real. They are skipped where Nextflow is not installed;
the static checks below still hold the pipeline's parameter surface, process
names and gate ordering in place.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app
from conftest import drop_vehicle_rows

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


def test_the_gates_are_named_in_the_process_names() -> None:
    script = PIPELINE.read_text(encoding="utf-8")

    assert "process CONTRACT_GATE_COMPOUNDS" in script
    assert "process CONTRACT_GATE_SAMPLES" in script
    assert "process RUN_DATA_QUALITY" in script
    assert "process CURATE" in script
    assert "process EMIT_OPENLINEAGE" in script
    assert "process EVALUATE_GOVERNANCE" in script


def test_the_pipeline_declares_the_documented_parameters() -> None:
    config = CONFIG.read_text(encoding="utf-8")

    for name in ("study_dir", "samples_contract", "compounds_contract", "outdir"):
        assert name in config


def test_curation_never_runs_before_the_gates() -> None:
    """Each stage must consume the previous one's output, not the study directly."""
    script = PIPELINE.read_text(encoding="utf-8")

    quality_call = script.index("RUN_DATA_QUALITY(\n")
    assert "samples_passed" in script[quality_call : quality_call + 200]

    curate_call = script.index("CURATE(\n", quality_call)
    assert "quality_passed" in script[curate_call : curate_call + 200]

    lineage_call = script.index("EMIT_OPENLINEAGE(\n", curate_call)
    assert "curated" in script[lineage_call : lineage_call + 200]

    governance_call = script.index("EVALUATE_GOVERNANCE(\n", lineage_call)
    assert "lineage" in script[governance_call : governance_call + 400]


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
    assert json.loads((reports / "samples.contract.json").read_text(encoding="utf-8"))["passed"]
    assert json.loads((reports / "compounds.contract.json").read_text(encoding="utf-8"))["passed"]
    quality = tmp_path / "results" / "BIO-001" / "quality" / "dq-report.json"
    assert json.loads(quality.read_text(encoding="utf-8"))["overall_status"] == "pass"

    lineage = tmp_path / "results" / "BIO-001" / "lineage" / "openlineage.jsonl"
    events = [json.loads(line) for line in lineage.read_text(encoding="utf-8").splitlines()]
    assert [event["eventType"] for event in events] == ["START", "COMPLETE"]
    assert len({event["run"]["runId"] for event in events}) == 1
    assert "bio://BIO-001/raw/samples" in [d["name"] for d in events[0]["inputs"]]
    assert "bio://BIO-001/curated/samples" in [d["name"] for d in events[1]["outputs"]]

    governance = tmp_path / "results" / "BIO-001" / "governance" / "governance-report.json"
    report = json.loads(governance.read_text(encoding="utf-8"))
    assert report["decision"] == "ready"
    assert [check["status"] for check in report["checks"]] == ["pass"] * 5


@needs_nextflow
def test_contract_invalid_data_stops_at_the_contract_gate(tmp_path: Path) -> None:
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
    assert "Error executing process > 'CONTRACT_GATE_SAMPLES" in output
    assert "FAIL" in output
    results = tmp_path / "results" / "BIO-001"
    assert not (results / "quality").exists()
    assert not (results / "curated").exists()
    assert not (results / "lineage").exists()
    assert not (results / "governance").exists()


@needs_nextflow
def test_contract_valid_but_low_quality_data_stops_at_the_quality_gate(tmp_path: Path) -> None:
    """The layers are different, and the pipeline shows where each one acts.

    Removing the controls breaks no contract rule, so both contract gates pass
    and the run stops one process later.
    """
    study = generated_study(tmp_path)
    drop_vehicle_rows(study)

    result = run_pipeline(tmp_path, study)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "Error executing process > 'RUN_DATA_QUALITY" in output
    assert "vehicle" in output
    results = tmp_path / "results" / "BIO-001"
    assert "PASS" in (results / "contracts" / "samples.contract.txt").read_text(encoding="utf-8")
    assert not (results / "curated").exists()
    assert not (results / "lineage").exists()
    assert not (results / "governance").exists()
