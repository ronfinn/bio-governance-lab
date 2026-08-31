"""Shared fixtures and helpers for the test suite."""

import csv
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.cli import app
from bio_governance.models import (
    Asset,
    AssetIdentifier,
    AssetType,
    Classification,
    LifecycleStage,
    Ownership,
    Provenance,
)


@pytest.fixture
def ownership() -> Ownership:
    return Ownership(
        owner="Translational Data Platform",
        steward="Ron Finn",
        contact="steward@example.org",
    )


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        source_system="synthetic-generator",
        generated_by="bio-gov",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def asset(ownership: Ownership, provenance: Provenance) -> Asset:
    return Asset(
        identifier=AssetIdentifier.parse("bio://BIO-001/raw/samples"),
        name="Raw samples",
        asset_type=AssetType.DATASET,
        lifecycle_stage=LifecycleStage.RAW,
        classification=Classification.INTERNAL,
        ownership=ownership,
        provenance=provenance,
    )


Rows = list[dict[str, str]]


def rewrite_csv(path: Path, transform: Callable[[Rows], Rows]) -> None:
    """Apply ``transform`` to a CSV file's rows, keeping its header and dialect."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or ())
        rows = transform([dict(row) for row in reader])
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def drop_vehicle_rows(study: Path) -> None:
    """Remove a study's control samples, leaving every remaining row well-formed.

    This is the demonstration defect for the two governance layers: it breaks no
    contract rule, because the rows that stay are exactly as valid as they were,
    but it leaves a study with nothing to compare its treatments against.
    """
    rewrite_csv(
        study / "samples.csv",
        lambda rows: [row for row in rows if row["treatment"] != "vehicle"],
    )


CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"

#: The datasets the pipeline gates, and the contract each is gated by.
GATED_DATASETS = (("samples", "samples.v1.yaml"), ("compounds", "compounds.v1.yaml"))


def build_results(tmp_path: Path) -> Path:
    """Produce, under tmp_path, the results directory a clean pipeline run leaves.

    The governance layer evaluates evidence, so its tests need evidence that was
    actually produced: every artefact here is written by the same command
    ``main.nf`` runs, rather than hand-authored into the shape the evaluator
    hopes to find. A test then damages one piece of it and asserts on the
    verdict.
    """
    runner = CliRunner()

    def run(*args: str) -> None:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output

    study = tmp_path / "data" / "BIO-001"
    run("demo", "generate", "--output", str(study.parent))

    results = tmp_path / "results" / "BIO-001"
    for dataset, contract in GATED_DATASETS:
        run(
            "contract",
            "validate",
            str(CONTRACTS_DIR / contract),
            str(study / f"{dataset}.csv"),
            "--json-out",
            str(results / "contracts" / f"{dataset}.contract.json"),
        )

    quality = results / "quality" / "dq-report.json"
    run("dq", "run", str(study), "--json-out", str(quality))

    curated = results / "curated"
    curated.mkdir(parents=True)
    for name in ("samples.csv", "compounds.csv", "expression.csv"):
        shutil.copyfile(study / name, curated / name)

    run(
        "lineage",
        "emit",
        str(study),
        str(curated),
        "--quality-report",
        str(quality),
        "--output",
        str(results / "lineage" / "openlineage.jsonl"),
    )
    return results
