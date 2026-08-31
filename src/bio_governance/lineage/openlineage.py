"""Emitting OpenLineage provenance evidence for a governed curation run.

The gates in ``pipelines/nextflow/main.nf`` decide whether raw data may become
curated data. This module records the decision's *result*: one OpenLineage run
saying which raw datasets were read and which curated datasets were produced.

Nothing here invents a schema. The events are built from the OpenLineage
client's own ``event_v2`` models and written by its own ``FileTransport``, so
what lands on disk is the published spec rather than a shape only this
repository understands. The transport is deliberately the local file one: the
point of this milestone is standards-based *evidence*, not running a server.

A run is two events — START and COMPLETE — sharing one run ID. Unlike generated
data, lineage is not reproducible byte-for-byte: an event describes one
execution, and the run ID and timestamps are what make it that execution rather
than another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import (
    InputDataset,
    Job,
    OutputDataset,
    Run,
    RunEvent,
    RunState,
)
from openlineage.client.transport.file import FileConfig, FileTransport
from pydantic import BaseModel, ConfigDict, Field

from bio_governance.models.identifiers import AssetIdentifier

#: The OpenLineage namespace for both the job and its datasets. One namespace,
#: because one repository produces all of it; the ``bio://`` identifier already
#: carries the study and the lifecycle stage, so nothing is gained by splitting
#: namespaces as well.
NAMESPACE = "bio-governance-lab"

#: Stable job identity. The job is the curation *activity*, not one execution of
#: it — every run of the pipeline reports the same job name and a new run ID.
JOB_NAME = "curate-study"

#: Who produced the event, as the spec's URI-shaped ``producer`` field.
PRODUCER = "https://github.com/ronfinn/bio-governance-lab"

#: The three curated datasets, and the file each is written as. The raw side
#: uses the same names, because ``CURATE`` copies rather than transforms.
DATASET_FILES: tuple[tuple[str, str], ...] = (
    ("samples", "samples.csv"),
    ("compounds", "compounds.csv"),
    ("expression", "expression.csv"),
)

#: Lifecycle segment of the ``bio://`` identifier for each side of the run.
RAW_STAGE = "raw"
CURATED_STAGE = "curated"

#: The quality evidence, named as a dataset so a catalogue can find the report
#: that let this run happen at all.
QUALITY_DATASET = "quality/dq-report"


class LineageError(Exception):
    """A required input was missing, unreadable, or not a study directory."""


class EmittedRun(BaseModel):
    """What one emission produced, for the CLI and the tests to assert on."""

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    output: Path
    inputs: tuple[AssetIdentifier, ...] = Field(min_length=1)
    outputs: tuple[AssetIdentifier, ...] = Field(min_length=1)


def emit_curation_lineage(
    raw_dir: Path,
    curated_dir: Path,
    output: Path,
    *,
    quality_report: Path | None = None,
    run_id: str | None = None,
) -> EmittedRun:
    """Write the START and COMPLETE events for one curation run to ``output``.

    The study is identified by the raw directory's name, and the datasets by the
    ``bio://`` identifiers the rest of the project already uses. Every file the
    events claim must exist, so a run cannot assert provenance for something
    that was never written; a missing one raises :class:`LineageError`.

    ``run_id`` is generated when it is not supplied. Passing one is for tests
    and for a caller that has already minted an identity for the execution.
    """
    study_id = _study_id(raw_dir)

    inputs = tuple(
        _identifier(study_id, RAW_STAGE, name)
        for name, _ in _require_files(raw_dir, "raw study directory")
    )
    outputs = [
        _identifier(study_id, CURATED_STAGE, name)
        for name, _ in _require_files(curated_dir, "curated directory")
    ]
    if quality_report is not None:
        _require_file(quality_report, "quality report")
        outputs.append(AssetIdentifier.parse(f"bio://{study_id}/{QUALITY_DATASET}"))

    client = _client(output)
    job = Job(namespace=NAMESPACE, name=JOB_NAME)
    run = Run(runId=run_id or str(uuid.uuid4()))

    for state in (RunState.START, RunState.COMPLETE):
        client.emit(
            RunEvent(
                eventTime=datetime.now(UTC).isoformat(),
                producer=PRODUCER,
                job=job,
                run=run,
                eventType=state,
                inputs=[
                    InputDataset(namespace=NAMESPACE, name=identifier.uri) for identifier in inputs
                ],
                outputs=[
                    OutputDataset(namespace=NAMESPACE, name=identifier.uri)
                    for identifier in outputs
                ],
            )
        )

    return EmittedRun(
        study_id=study_id,
        run_id=run.runId,
        output=output,
        inputs=inputs,
        outputs=tuple(outputs),
    )


def _client(output: Path) -> OpenLineageClient:
    """A client writing both events as lines of the single file ``output``.

    ``append`` keeps the transport writing to exactly the path asked for; it
    otherwise stamps a timestamp into the name and produces one file per event.
    The file is truncated first, so a run's evidence is that run's two events
    rather than an accumulation of every run that shared the path.
    """
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("", encoding="utf-8")
    except OSError as exc:
        raise LineageError(f"cannot write {output}: {exc.strerror or exc}") from exc

    return OpenLineageClient(
        transport=FileTransport(FileConfig(log_file_path=str(output), append=True))
    )


def _study_id(raw_dir: Path) -> str:
    """Take the study identifier from the raw directory's name."""
    if not raw_dir.is_dir():
        raise LineageError(f"raw study directory not found: {raw_dir}")
    study_id = raw_dir.resolve().name
    try:
        AssetIdentifier.parse(f"bio://{study_id}/{RAW_STAGE}/samples")
    except ValueError as exc:
        raise LineageError(f"{raw_dir} is not named for a study: {exc}") from exc
    return study_id


def _identifier(study_id: str, stage: str, name: str) -> AssetIdentifier:
    return AssetIdentifier.parse(f"bio://{study_id}/{stage}/{name}")


def _require_files(directory: Path, label: str) -> tuple[tuple[str, str], ...]:
    """Check that ``directory`` holds all three dataset files, and name them."""
    if not directory.is_dir():
        raise LineageError(f"{label} not found: {directory}")
    for _, file_name in DATASET_FILES:
        _require_file(directory / file_name, label)
    return DATASET_FILES


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise LineageError(f"{label} is missing {path}")
