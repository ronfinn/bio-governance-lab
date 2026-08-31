"""Tests for the OpenLineage provenance evidence.

Lineage is not deterministic the way generated data is — a run ID and a
timestamp are what make an event describe *this* execution — so these tests
assert on the event structure the spec defines rather than on bytes.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app
from bio_governance.lineage import (
    JOB_NAME,
    NAMESPACE,
    PRODUCER,
    LineageError,
    emit_curation_lineage,
)

runner = CliRunner()

RAW_DATASETS = (
    "bio://BIO-001/raw/samples",
    "bio://BIO-001/raw/compounds",
    "bio://BIO-001/raw/expression",
)
CURATED_DATASETS = (
    "bio://BIO-001/curated/samples",
    "bio://BIO-001/curated/compounds",
    "bio://BIO-001/curated/expression",
)
QUALITY_DATASET = "bio://BIO-001/quality/dq-report"


@pytest.fixture
def study(tmp_path: Path) -> Path:
    """A generated study under tmp_path, as the pipeline's raw input."""
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    return tmp_path / "data" / "BIO-001"


@pytest.fixture
def curated(study: Path, tmp_path: Path) -> Path:
    """The curated directory CURATE would have written, and its quality report."""
    directory = tmp_path / "results" / "BIO-001" / "curated"
    directory.mkdir(parents=True)
    for name in ("samples.csv", "compounds.csv", "expression.csv"):
        (directory / name).write_text((study / name).read_text(encoding="utf-8"), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "dq",
            "run",
            str(study),
            "--json-out",
            str(directory.parent / "quality" / "dq-report.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    return directory


def emit(
    study: Path, curated: Path, output: Path, quality_report: Path | None = None
) -> list[dict[str, object]]:
    """Emit a run and return the events it wrote, parsed."""
    emit_curation_lineage(study, curated, output, quality_report=quality_report)
    return read_events(output)


def read_events(output: Path) -> list[dict[str, object]]:
    lines = output.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_a_run_is_two_events(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "lineage" / "openlineage.jsonl")

    assert len(events) == 2


def test_the_first_event_starts_the_run(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    assert events[0]["eventType"] == "START"


def test_the_second_event_completes_the_run(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    assert events[1]["eventType"] == "COMPLETE"


def test_both_events_share_one_run_id(study: Path, curated: Path, tmp_path: Path) -> None:
    """START and COMPLETE are two halves of one execution, not two runs."""
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    run_ids = {event["run"]["runId"] for event in events}  # type: ignore[index]
    assert len(run_ids) == 1


def test_the_job_identity_is_stable(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    for event in events:
        assert event["job"] == {"namespace": NAMESPACE, "name": JOB_NAME, "facets": {}}


def test_the_producer_identifies_this_repository(
    study: Path, curated: Path, tmp_path: Path
) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    for event in events:
        assert event["producer"] == PRODUCER
        assert "bio-governance-lab" in str(event["producer"])


def test_the_raw_study_files_are_the_inputs(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    for event in events:
        assert [dataset["name"] for dataset in event["inputs"]] == list(RAW_DATASETS)  # type: ignore[union-attr]
        assert {dataset["namespace"] for dataset in event["inputs"]} == {NAMESPACE}  # type: ignore[union-attr]


def test_the_curated_files_are_the_outputs(study: Path, curated: Path, tmp_path: Path) -> None:
    events = emit(study, curated, tmp_path / "openlineage.jsonl")

    for event in events:
        names = [dataset["name"] for dataset in event["outputs"]]  # type: ignore[union-attr]
        assert names == list(CURATED_DATASETS)


def test_the_quality_report_is_an_output_when_it_is_given(
    study: Path, curated: Path, tmp_path: Path
) -> None:
    """The evidence that let the run happen belongs in the run's outputs."""
    report = curated.parent / "quality" / "dq-report.json"

    events = emit(study, curated, tmp_path / "openlineage.jsonl", quality_report=report)

    names = [dataset["name"] for dataset in events[0]["outputs"]]  # type: ignore[union-attr]
    assert names == [*CURATED_DATASETS, QUALITY_DATASET]


def test_an_explicit_run_id_is_preserved(study: Path, curated: Path, tmp_path: Path) -> None:
    run_id = "0198c0de-0000-4000-8000-000000000001"

    emitted = emit_curation_lineage(study, curated, tmp_path / "openlineage.jsonl", run_id=run_id)

    assert emitted.run_id == run_id
    for event in read_events(tmp_path / "openlineage.jsonl"):
        assert event["run"]["runId"] == run_id  # type: ignore[index]


def test_a_missing_curated_file_is_refused(study: Path, curated: Path, tmp_path: Path) -> None:
    """Lineage must not claim provenance for something that was never written."""
    (curated / "expression.csv").unlink()

    with pytest.raises(LineageError, match=r"expression\.csv"):
        emit_curation_lineage(study, curated, tmp_path / "openlineage.jsonl")


def test_a_missing_quality_report_is_refused(study: Path, curated: Path, tmp_path: Path) -> None:
    with pytest.raises(LineageError, match="quality report"):
        emit_curation_lineage(
            study,
            curated,
            tmp_path / "openlineage.jsonl",
            quality_report=tmp_path / "nowhere" / "dq-report.json",
        )


def test_the_cli_writes_the_jsonl_file(study: Path, curated: Path, tmp_path: Path) -> None:
    output = tmp_path / "results" / "BIO-001" / "lineage" / "openlineage.jsonl"

    result = runner.invoke(
        app,
        [
            "lineage",
            "emit",
            str(study),
            str(curated),
            "--quality-report",
            str(curated.parent / "quality" / "dq-report.json"),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    events = read_events(output)
    assert [event["eventType"] for event in events] == ["START", "COMPLETE"]
    assert events[0]["run"]["runId"] in result.output  # type: ignore[index]
    assert str(output) in result.output


def test_the_cli_reports_a_missing_input_clearly(
    study: Path, curated: Path, tmp_path: Path
) -> None:
    (curated / "samples.csv").unlink()

    result = runner.invoke(
        app,
        [
            "lineage",
            "emit",
            str(study),
            str(curated),
            "--output",
            str(tmp_path / "openlineage.jsonl"),
        ],
    )

    assert result.exit_code == 2
    assert "samples.csv" in result.output
