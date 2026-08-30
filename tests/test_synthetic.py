"""Tests for the deterministic synthetic study generator."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bio_governance.synthetic import (
    DEFAULT_COMPOUND_COUNT,
    DEFAULT_SAMPLE_COUNT,
    DEFAULT_STUDY_ID,
    Compound,
    Injection,
    Sample,
    generate_study,
)
from bio_governance.synthetic.generator import (
    COMPOUNDS_FILE,
    EXPRESSION_FILE,
    SAMPLES_FILE,
    STUDY_FILE,
    UNKNOWN_COMPOUND_ID,
    VEHICLE_TREATMENT,
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_study(path: Path) -> dict[str, object]:
    parsed: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def test_default_generation_writes_the_four_files(tmp_path: Path) -> None:
    generated = generate_study(tmp_path)

    assert generated.directory == tmp_path / DEFAULT_STUDY_ID
    assert [path.name for path in generated.files] == [
        STUDY_FILE,
        COMPOUNDS_FILE,
        SAMPLES_FILE,
        EXPRESSION_FILE,
    ]
    assert all(path.is_file() for path in generated.files)

    study = read_study(generated.directory / STUDY_FILE)
    assert study["study_id"] == DEFAULT_STUDY_ID
    assert study["sample_count"] == DEFAULT_SAMPLE_COUNT
    assert study["compound_count"] == DEFAULT_COMPOUND_COUNT
    assert study["synthetic"] is True
    assert study["injected_defects"] == []
    assert "bio://BIO-001/raw/samples" in study["assets"]  # type: ignore[operator]


def test_custom_study_id_names_the_directory_and_the_samples(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, study_id="BIO-042")

    assert generated.directory == tmp_path / "BIO-042"
    rows = read_rows(generated.directory / SAMPLES_FILE)
    assert all(row["study_id"] == "BIO-042" for row in rows)
    assert rows[0]["sample_id"] == "BIO-042-S001"


def test_requested_sample_count_is_honoured(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=48)

    rows = read_rows(generated.directory / SAMPLES_FILE)
    assert len(rows) == 48
    assert read_study(generated.directory / STUDY_FILE)["sample_count"] == 48


def test_requested_compound_count_is_honoured(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, compounds=4)

    rows = read_rows(generated.directory / COMPOUNDS_FILE)
    assert [row["compound_id"] for row in rows] == ["CMP-001", "CMP-002", "CMP-003", "CMP-004"]
    assert read_study(generated.directory / STUDY_FILE)["compound_count"] == 4


def test_expression_matrix_has_a_column_per_sample(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=12, seed=3)

    rows = read_rows(generated.directory / EXPRESSION_FILE)
    sample_ids = [row["sample_id"] for row in read_rows(generated.directory / SAMPLES_FILE)]
    assert len(rows) == read_study(generated.directory / STUDY_FILE)["gene_count"]
    assert list(rows[0]) == ["gene_id", "gene_symbol", *sample_ids]


def test_same_inputs_are_byte_for_byte_reproducible(tmp_path: Path) -> None:
    first = generate_study(tmp_path / "first", study_id="BIO-007", samples=16, compounds=3, seed=42)
    second = generate_study(
        tmp_path / "second", study_id="BIO-007", samples=16, compounds=3, seed=42
    )

    for left, right in zip(first.files, second.files, strict=True):
        assert left.read_bytes() == right.read_bytes(), left.name


def test_different_seeds_produce_different_expression_values(tmp_path: Path) -> None:
    first = generate_study(tmp_path / "first", seed=1)
    second = generate_study(tmp_path / "second", seed=2)

    assert (first.directory / EXPRESSION_FILE).read_text(encoding="utf-8") != (
        second.directory / EXPRESSION_FILE
    ).read_text(encoding="utf-8")
    # The sample manifest keeps the same identifiers; only the values move.
    first_ids = [row["sample_id"] for row in read_rows(first.directory / SAMPLES_FILE)]
    second_ids = [row["sample_id"] for row in read_rows(second.directory / SAMPLES_FILE)]
    assert first_ids == second_ids


def test_sample_ids_are_unique_without_injection(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=48, compounds=4)

    sample_ids = [row["sample_id"] for row in read_rows(generated.directory / SAMPLES_FILE)]
    assert len(set(sample_ids)) == len(sample_ids)
    assert all(sample_ids)


def test_samples_only_reference_compounds_that_exist(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=48, compounds=4)

    known = {row["compound_id"] for row in read_rows(generated.directory / COMPOUNDS_FILE)}
    referenced = {
        row["compound_id"]
        for row in read_rows(generated.directory / SAMPLES_FILE)
        if row["compound_id"]
    }
    assert referenced <= known
    assert referenced == known


def test_vehicle_controls_are_present(tmp_path: Path) -> None:
    generated = generate_study(tmp_path)

    rows = read_rows(generated.directory / SAMPLES_FILE)
    vehicles = [row for row in rows if row["treatment"] == VEHICLE_TREATMENT]
    assert vehicles
    assert all(row["compound_id"] == "" for row in vehicles)
    assert all(float(row["dose"]) == 0.0 for row in vehicles)


def test_a_single_sample_study_is_still_a_control(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=1)

    rows = read_rows(generated.directory / SAMPLES_FILE)
    assert [row["treatment"] for row in rows] == [VEHICLE_TREATMENT]


def test_inject_missing_sample_id(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, injections=[Injection.MISSING_SAMPLE_ID])

    rows = read_rows(generated.directory / SAMPLES_FILE)
    assert sum(1 for row in rows if row["sample_id"] == "") == 1
    assert read_study(generated.directory / STUDY_FILE)["injected_defects"] == ["missing_sample_id"]


def test_inject_invalid_dose(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, injections=[Injection.INVALID_DOSE])

    doses = [float(row["dose"]) for row in read_rows(generated.directory / SAMPLES_FILE)]
    assert sum(1 for dose in doses if dose < 0) == 1


def test_inject_duplicate_sample(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, samples=20, injections=[Injection.DUPLICATE_SAMPLE])

    rows = read_rows(generated.directory / SAMPLES_FILE)
    sample_ids = [row["sample_id"] for row in rows]
    assert len(rows) == 21
    assert len(set(sample_ids)) == 20
    # The extra row repeats an existing sample verbatim.
    duplicated = rows[-1]["sample_id"]
    assert next(row for row in rows if row["sample_id"] == duplicated) == rows[-1]


def test_inject_unknown_compound(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, injections=[Injection.UNKNOWN_COMPOUND])

    known = {row["compound_id"] for row in read_rows(generated.directory / COMPOUNDS_FILE)}
    referenced = [row["compound_id"] for row in read_rows(generated.directory / SAMPLES_FILE)]
    assert UNKNOWN_COMPOUND_ID in referenced
    assert UNKNOWN_COMPOUND_ID not in known


def test_injections_are_independently_observable(tmp_path: Path) -> None:
    generated = generate_study(tmp_path, injections=list(Injection))

    rows = read_rows(generated.directory / SAMPLES_FILE)
    assert sum(1 for row in rows if row["sample_id"] == "") == 1
    assert sum(1 for row in rows if float(row["dose"]) < 0) == 1
    assert sum(1 for row in rows if row["compound_id"] == UNKNOWN_COMPOUND_ID) == 1
    assert len(rows) == DEFAULT_SAMPLE_COUNT + 1


def test_injection_is_the_only_difference_from_a_clean_run(tmp_path: Path) -> None:
    clean = generate_study(tmp_path / "clean")
    dirty = generate_study(tmp_path / "dirty", injections=[Injection.INVALID_DOSE])

    for name in (COMPOUNDS_FILE, EXPRESSION_FILE):
        assert (clean.directory / name).read_bytes() == (dirty.directory / name).read_bytes()


def test_invalid_study_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid study id"):
        generate_study(tmp_path, study_id="bio 001")


@pytest.mark.parametrize("kwargs", [{"samples": 0}, {"compounds": 0}, {"genes": 0}])
def test_non_positive_counts_are_rejected(tmp_path: Path, kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must be at least 1"):
        generate_study(tmp_path, **kwargs)  # type: ignore[arg-type]


def test_compound_accepts_a_well_formed_record() -> None:
    compound = Compound(
        compound_id="CMP-001", compound_name="Zonaxamib", mechanism_class="kinase_inhibitor"
    )

    assert compound.compound_id == "CMP-001"


def test_compound_rejects_a_malformed_identifier() -> None:
    with pytest.raises(ValidationError):
        Compound(compound_id="CMP-1", compound_name="Zonaxamib", mechanism_class="kinase_inhibitor")


def test_sample_rejects_a_negative_dose() -> None:
    with pytest.raises(ValidationError):
        Sample(
            sample_id="BIO-001-S001",
            study_id="BIO-001",
            compound_id="CMP-001",
            treatment="Zonaxamib",
            dose=-1.0,
            dose_unit="uM",
            tissue="liver",
            replicate=1,
        )
