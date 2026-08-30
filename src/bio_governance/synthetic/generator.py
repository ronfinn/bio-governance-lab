"""Deterministic generation of a small synthetic compound-perturbation study.

The generator writes four files under ``<output_root>/<STUDY-ID>/``: study-level
metadata, a compound registry, a sample manifest and a small expression matrix.
Every value is invented. Given the same study ID, sample count, compound count
and seed the output is byte-for-byte identical, so demonstration data can be
regenerated on demand rather than committed.

Nothing here validates its own output. The injection options deliberately write
malformed rows; detecting them is a later milestone's job.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bio_governance.models.identifiers import DOMAIN_PATTERN, AssetIdentifier

DEFAULT_STUDY_ID = "BIO-001"
DEFAULT_SAMPLE_COUNT = 20
DEFAULT_COMPOUND_COUNT = 3
DEFAULT_SEED = 7
DEFAULT_GENE_COUNT = 12

STUDY_FILE = "study.json"
COMPOUNDS_FILE = "compounds.csv"
SAMPLES_FILE = "samples.csv"
EXPRESSION_FILE = "expression.csv"

ORGANISM = "Homo sapiens (synthetic)"
MODEL_SYSTEM = "immortalised cell line (synthetic)"

#: The control condition. Vehicle samples carry no compound identifier.
VEHICLE_TREATMENT = "vehicle"
DOSE_UNIT = "uM"
DOSE_LEVELS = (0.1, 1.0, 10.0)
TISSUES = ("liver", "kidney", "lung", "heart")

MECHANISM_CLASSES = (
    "kinase_inhibitor",
    "protease_inhibitor",
    "gpcr_agonist",
    "gpcr_antagonist",
    "epigenetic_modulator",
    "ion_channel_blocker",
    "nuclear_receptor_agonist",
)

#: Compound names are built from these fragments so they are pronounceable,
#: unique and obviously not real molecules.
_NAME_PREFIXES = ("zon", "ver", "lomi", "kadre", "tesu", "abri", "nyra", "oxel")
_NAME_SUFFIXES = ("axamib", "tinib", "olimus", "stat", "ciclib", "parib")

#: Values written by the injection options. Compound IDs are generated from 1
#: upwards, so ``CMP-000`` is a well-formed identifier that can never exist —
#: the defect is the dangling reference, not the format.
UNKNOWN_COMPOUND_ID = "CMP-000"
INVALID_DOSE = -1.0


class Injection(StrEnum):
    """A deliberate defect the generator can write into ``samples.csv``."""

    MISSING_SAMPLE_ID = "missing_sample_id"
    INVALID_DOSE = "invalid_dose"
    DUPLICATE_SAMPLE = "duplicate_sample"
    UNKNOWN_COMPOUND = "unknown_compound"


class Compound(BaseModel):
    """A synthetic test article in the study's compound registry."""

    model_config = ConfigDict(frozen=True)

    compound_id: str = Field(pattern=r"^CMP-\d{3,}$")
    compound_name: str = Field(min_length=1)
    mechanism_class: str = Field(min_length=1)


class Sample(BaseModel):
    """One well of cells: a study, a condition and a replicate number.

    ``compound_id`` is empty for vehicle controls, which are treated with the
    solvent alone and so reference no test article.
    """

    model_config = ConfigDict(frozen=True)

    sample_id: str = Field(min_length=1)
    study_id: str = Field(min_length=1)
    compound_id: str
    treatment: str = Field(min_length=1)
    dose: float = Field(ge=0.0)
    dose_unit: str = Field(min_length=1)
    tissue: str = Field(min_length=1)
    replicate: int = Field(ge=1)


class StudyMetadata(BaseModel):
    """Study-level metadata, serialized to ``study.json``.

    ``sample_count`` is the number of samples that were *requested*. Injecting a
    duplicate adds a row to ``samples.csv`` without changing this number — the
    disagreement is the defect.
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(pattern=DOMAIN_PATTERN.pattern)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    organism: str = Field(min_length=1)
    model_system: str = Field(min_length=1)
    seed: int
    sample_count: int = Field(ge=1)
    compound_count: int = Field(ge=1)
    gene_count: int = Field(ge=1)
    synthetic: bool = True
    injected_defects: tuple[Injection, ...] = ()
    assets: tuple[AssetIdentifier, ...] = ()


class GeneratedStudy(BaseModel):
    """What a generation run produced and where it put it."""

    model_config = ConfigDict(frozen=True)

    study: StudyMetadata
    directory: Path
    files: tuple[Path, ...]


def generate_study(
    output_root: Path,
    *,
    study_id: str = DEFAULT_STUDY_ID,
    samples: int = DEFAULT_SAMPLE_COUNT,
    compounds: int = DEFAULT_COMPOUND_COUNT,
    seed: int = DEFAULT_SEED,
    genes: int = DEFAULT_GENE_COUNT,
    injections: Iterable[Injection] = (),
) -> GeneratedStudy:
    """Write a synthetic study under ``output_root/<study_id>/``.

    Raises ``ValueError`` for a malformed study ID or a non-positive count.
    """
    if not DOMAIN_PATTERN.match(study_id):
        raise ValueError(f"invalid study id {study_id!r}: expected a code such as 'BIO-001'")
    for label, count in (("samples", samples), ("compounds", compounds), ("genes", genes)):
        if count < 1:
            raise ValueError(f"{label} must be at least 1, got {count}")

    requested = tuple(dict.fromkeys(injections))
    rng = random.Random(f"{study_id}:{samples}:{compounds}:{genes}:{seed}")

    compound_records = _build_compounds(rng, compounds)
    sample_records = _build_samples(rng, study_id, samples, compound_records)
    expression_rows = _build_expression(rng, genes, sample_records)

    metadata = StudyMetadata(
        study_id=study_id,
        name=f"{study_id} compound perturbation screen",
        description=(
            "Synthetic compound-perturbation screen: cells treated with a vehicle "
            "control or one of several test articles across a dose range, with a "
            "small expression readout. Generated data, for demonstration only."
        ),
        organism=ORGANISM,
        model_system=MODEL_SYSTEM,
        seed=seed,
        sample_count=samples,
        compound_count=compounds,
        gene_count=genes,
        injected_defects=requested,
        assets=tuple(
            AssetIdentifier.parse(f"bio://{study_id}/raw/{name}")
            for name in ("study", "compounds", "samples", "expression")
        ),
    )

    directory = output_root / study_id
    directory.mkdir(parents=True, exist_ok=True)

    study_path = directory / STUDY_FILE
    compounds_path = directory / COMPOUNDS_FILE
    samples_path = directory / SAMPLES_FILE
    expression_path = directory / EXPRESSION_FILE

    _write_text(study_path, json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n")
    _write_csv(
        compounds_path,
        ("compound_id", "compound_name", "mechanism_class"),
        [
            [compound.compound_id, compound.compound_name, compound.mechanism_class]
            for compound in compound_records
        ],
    )
    _write_csv(
        samples_path,
        _SAMPLE_COLUMNS,
        [
            [row[column] for column in _SAMPLE_COLUMNS]
            for row in _inject(_sample_rows(sample_records), requested)
        ],
    )
    _write_csv(
        expression_path,
        ("gene_id", "gene_symbol", *(sample.sample_id for sample in sample_records)),
        expression_rows,
    )

    return GeneratedStudy(
        study=metadata,
        directory=directory,
        files=(study_path, compounds_path, samples_path, expression_path),
    )


_SAMPLE_COLUMNS = (
    "sample_id",
    "study_id",
    "compound_id",
    "treatment",
    "dose",
    "dose_unit",
    "tissue",
    "replicate",
)


def _build_compounds(rng: random.Random, count: int) -> tuple[Compound, ...]:
    return tuple(
        Compound(
            compound_id=f"CMP-{index + 1:03d}",
            compound_name=_compound_name(index),
            mechanism_class=_pick(rng, MECHANISM_CLASSES),
        )
        for index in range(count)
    )


#: Prefixes and suffixes advance together, so names stay distinct for this many
#: compounds before a numeric suffix is needed to keep them unique.
_NAME_CYCLE = math.lcm(len(_NAME_PREFIXES), len(_NAME_SUFFIXES))


def _compound_name(index: int) -> str:
    prefix = _NAME_PREFIXES[index % len(_NAME_PREFIXES)]
    suffix = _NAME_SUFFIXES[index % len(_NAME_SUFFIXES)]
    cycle = index // _NAME_CYCLE
    name = f"{prefix}{suffix}".capitalize()
    return name if cycle == 0 else f"{name}-{cycle}"


def _build_samples(
    rng: random.Random,
    study_id: str,
    count: int,
    compounds: Sequence[Compound],
) -> tuple[Sample, ...]:
    """Assign ``count`` samples round-robin across the study's conditions.

    The vehicle control is the first condition, so any study with at least one
    sample has a control.
    """
    conditions: list[tuple[str, str, float]] = [("", VEHICLE_TREATMENT, 0.0)]
    conditions.extend(
        (compound.compound_id, compound.compound_name, dose)
        for compound in compounds
        for dose in DOSE_LEVELS
    )

    samples: list[Sample] = []
    for index in range(count):
        compound_id, treatment, dose = conditions[index % len(conditions)]
        samples.append(
            Sample(
                sample_id=f"{study_id}-S{index + 1:03d}",
                study_id=study_id,
                compound_id=compound_id,
                treatment=treatment,
                dose=dose,
                dose_unit=DOSE_UNIT,
                tissue=_pick(rng, TISSUES),
                replicate=index // len(conditions) + 1,
            )
        )
    return tuple(samples)


def _build_expression(
    rng: random.Random,
    genes: int,
    samples: Sequence[Sample],
) -> list[list[str]]:
    """Build a small gene-by-sample matrix of invented expression values.

    A gene has a baseline level and a per-compound response scaled by dose, plus
    noise. This is a plausible *shape*, not a model of transcriptomics.
    """
    max_dose = max(DOSE_LEVELS)
    compound_ids = tuple(dict.fromkeys(sample.compound_id for sample in samples))
    rows: list[list[str]] = []
    for index in range(genes):
        baseline = 4.0 + rng.random() * 6.0
        response = {compound_id: (rng.random() - 0.5) * 4.0 for compound_id in compound_ids}
        values = []
        for sample in samples:
            effect = response[sample.compound_id] * (sample.dose / max_dose)
            noise = (rng.random() - 0.5) * 0.4
            values.append(f"{max(0.0, baseline + effect + noise):.3f}")
        rows.append([f"SYNG{index + 1:03d}", _gene_symbol(index), *values])
    return rows


def _gene_symbol(index: int) -> str:
    return f"SYN{chr(ord('A') + index % 26)}{index // 26 + 1}"


def _sample_rows(samples: Sequence[Sample]) -> list[dict[str, str]]:
    return [
        {
            "sample_id": sample.sample_id,
            "study_id": sample.study_id,
            "compound_id": sample.compound_id,
            "treatment": sample.treatment,
            "dose": f"{sample.dose:.2f}",
            "dose_unit": sample.dose_unit,
            "tissue": sample.tissue,
            "replicate": str(sample.replicate),
        }
        for sample in samples
    ]


def _inject(rows: list[dict[str, str]], injections: Sequence[Injection]) -> list[dict[str, str]]:
    """Return a copy of ``rows`` carrying one defect per requested injection.

    Each defect targets a different row where the study is large enough, so the
    defects stay independently observable when several are requested at once.
    """
    if not injections:
        return rows

    corrupted = [dict(row) for row in rows]
    treated = [index for index, row in enumerate(corrupted) if row["compound_id"]] or [
        index for index, _ in enumerate(corrupted)
    ]

    if Injection.MISSING_SAMPLE_ID in injections:
        corrupted[treated[0]]["sample_id"] = ""
    if Injection.INVALID_DOSE in injections:
        corrupted[treated[1 % len(treated)]]["dose"] = f"{INVALID_DOSE:.2f}"
    if Injection.UNKNOWN_COMPOUND in injections:
        corrupted[treated[2 % len(treated)]]["compound_id"] = UNKNOWN_COMPOUND_ID
    if Injection.DUPLICATE_SAMPLE in injections:
        source = next((row for row in corrupted if row["sample_id"]), corrupted[0])
        corrupted.append(dict(source))
    return corrupted


def _pick(rng: random.Random, options: Sequence[str]) -> str:
    """Choose from ``options`` using only ``random()``, whose stream is stable."""
    return options[int(rng.random() * len(options)) % len(options)]


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    _write_text(path, buffer.getvalue())
