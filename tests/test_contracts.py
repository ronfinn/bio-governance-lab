"""Tests for loading YAML data contracts and validating CSV datasets against them.

The generator supplies the fixtures, but never the expectations: every rule
under test comes from the YAML contracts in ``contracts/``, so a passing run
says the data matches an independent description of it rather than saying the
generator agrees with itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_governance.contracts import (
    ColumnContract,
    ColumnType,
    ContractError,
    ContractValidationResult,
    DataContract,
    DatasetError,
    ExtraColumnPolicy,
    Reference,
    Rule,
    load_contract,
    validate_dataset,
)
from bio_governance.synthetic import Injection, generate_study
from bio_governance.synthetic.generator import COMPOUNDS_FILE, SAMPLES_FILE

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
SAMPLES_CONTRACT = CONTRACTS / "samples.v1.yaml"
COMPOUNDS_CONTRACT = CONTRACTS / "compounds.v1.yaml"


@pytest.fixture
def samples_contract() -> DataContract:
    return load_contract(SAMPLES_CONTRACT)


@pytest.fixture
def compounds_contract() -> DataContract:
    return load_contract(COMPOUNDS_CONTRACT)


def study_files(tmp_path: Path, *injections: Injection) -> tuple[Path, Path]:
    """Generate a study and return its ``samples.csv`` and ``compounds.csv``."""
    generated = generate_study(tmp_path, injections=injections)
    return generated.directory / SAMPLES_FILE, generated.directory / COMPOUNDS_FILE


def rules(result: ContractValidationResult) -> set[Rule]:
    """The distinct rules a result reported, which is what most tests assert on."""
    return {violation.rule for violation in result.violations}


def write_contract(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Contract loading
# --------------------------------------------------------------------------


def test_samples_contract_loads(samples_contract: DataContract) -> None:
    assert samples_contract.contract_id == "bio.samples"
    assert samples_contract.version == "1.0.0"
    assert samples_contract.label == "bio.samples@1.0.0"
    assert samples_contract.extra_columns is ExtraColumnPolicy.FORBID

    columns = {column.name: column for column in samples_contract.columns}
    assert columns["sample_id"].required is True
    assert columns["sample_id"].unique is True
    assert columns["compound_id"].required is False
    assert columns["compound_id"].references == Reference(
        file="compounds.csv", column="compound_id"
    )
    assert columns["dose"].type is ColumnType.NUMBER
    assert columns["dose"].minimum == 0
    assert columns["replicate"].type is ColumnType.INTEGER


def test_compounds_contract_loads(compounds_contract: DataContract) -> None:
    assert compounds_contract.label == "bio.compounds@1.0.0"

    columns = {column.name: column for column in compounds_contract.columns}
    assert set(columns) == {"compound_id", "compound_name", "mechanism_class"}
    assert columns["compound_id"].unique is True
    assert columns["compound_id"].pattern == r"^CMP-\d{3,}$"
    assert columns["mechanism_class"].required is True
    assert all(column.references is None for column in compounds_contract.columns)


def test_malformed_yaml_fails_clearly(tmp_path: Path) -> None:
    path = write_contract(tmp_path / "broken.yaml", "contract_id: bio.samples\n  version: [1\n")

    with pytest.raises(ContractError, match="not valid YAML"):
        load_contract(path)


def test_contract_that_is_not_a_mapping_fails_clearly(tmp_path: Path) -> None:
    path = write_contract(tmp_path / "list.yaml", "- bio.samples\n- 1.0.0\n")

    with pytest.raises(ContractError, match="mapping at the top level"):
        load_contract(path)


def test_empty_contract_fails_clearly(tmp_path: Path) -> None:
    path = write_contract(tmp_path / "empty.yaml", "# nothing here\n")

    with pytest.raises(ContractError, match="is empty"):
        load_contract(path)


def test_contract_missing_required_fields_names_them(tmp_path: Path) -> None:
    path = write_contract(tmp_path / "partial.yaml", "contract_id: bio.samples\nversion: 1.0.0\n")

    with pytest.raises(ContractError) as caught:
        load_contract(path)

    message = str(caught.value)
    assert "not a valid contract" in message
    assert "asset" in message
    assert "columns" in message


def test_contract_with_no_columns_is_rejected(tmp_path: Path) -> None:
    path = write_contract(
        tmp_path / "bare.yaml",
        "contract_id: bio.samples\nversion: 1.0.0\nasset: bio://BIO-001/raw/samples\ncolumns: []\n",
    )

    with pytest.raises(ContractError, match="columns"):
        load_contract(path)


def test_contract_with_a_malformed_version_is_rejected(tmp_path: Path) -> None:
    path = write_contract(
        tmp_path / "version.yaml",
        "contract_id: bio.samples\n"
        "version: v1\n"
        "asset: bio://BIO-001/raw/samples\n"
        "columns:\n"
        "  - name: sample_id\n",
    )

    with pytest.raises(ContractError, match="version"):
        load_contract(path)


def test_contract_with_an_unknown_column_type_is_rejected(tmp_path: Path) -> None:
    path = write_contract(
        tmp_path / "type.yaml",
        "contract_id: bio.samples\n"
        "version: 1.0.0\n"
        "asset: bio://BIO-001/raw/samples\n"
        "columns:\n"
        "  - name: dose\n"
        "    type: decimal\n",
    )

    with pytest.raises(ContractError, match="type"):
        load_contract(path)


def test_contract_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="cannot read contract"):
        load_contract(tmp_path / "absent.yaml")


def test_minimum_on_a_string_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="needs a numeric type"):
        ColumnContract(name="tissue", type=ColumnType.STRING, minimum=0)


def test_an_invalid_pattern_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid 'pattern'"):
        ColumnContract(name="sample_id", pattern="[unclosed")


def test_a_reference_to_a_path_rather_than_a_file_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="bare file name"):
        Reference(file="../other/compounds.csv", column="compound_id")


def test_a_duplicated_column_declaration_is_rejected() -> None:
    with pytest.raises(ValueError, match="more than once"):
        DataContract(
            contract_id="bio.samples",
            version="1.0.0",
            asset="bio://BIO-001/raw/samples",
            columns=(ColumnContract(name="sample_id"), ColumnContract(name="sample_id")),
        )


# --------------------------------------------------------------------------
# Clean data
# --------------------------------------------------------------------------


def test_clean_samples_pass(tmp_path: Path, samples_contract: DataContract) -> None:
    samples, _ = study_files(tmp_path)

    result = validate_dataset(samples_contract, samples)

    assert result.passed is True
    assert result.violations == ()
    assert result.rows_checked == 20
    assert result.dataset == samples
    assert result.label == "bio.samples@1.0.0"


def test_clean_compounds_pass(tmp_path: Path, compounds_contract: DataContract) -> None:
    _, compounds = study_files(tmp_path)

    result = validate_dataset(compounds_contract, compounds)

    assert result.passed is True
    assert result.rows_checked == 3


def test_a_larger_clean_study_also_passes(tmp_path: Path, samples_contract: DataContract) -> None:
    generated = generate_study(tmp_path, samples=48, compounds=4, seed=42)

    result = validate_dataset(samples_contract, generated.directory / SAMPLES_FILE)

    assert result.passed is True
    assert result.rows_checked == 48


def test_vehicle_controls_pass_with_a_blank_compound_id(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    assert ",,vehicle," in samples.read_text(encoding="utf-8")

    assert validate_dataset(samples_contract, samples).passed is True


# --------------------------------------------------------------------------
# Deliberate defects from the milestone-2 injection options
# --------------------------------------------------------------------------


def test_missing_sample_id_fails_the_required_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path, Injection.MISSING_SAMPLE_ID)

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert rules(result) == {Rule.REQUIRED}
    violation = result.violations[0]
    assert violation.column == "sample_id"
    assert violation.row == 3
    assert "blank" in violation.message


def test_invalid_dose_fails_the_minimum_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path, Injection.INVALID_DOSE)

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert rules(result) == {Rule.MINIMUM}
    violation = result.violations[0]
    assert violation.column == "dose"
    assert violation.value == "-1.00"
    assert "below 0" in violation.message


def test_duplicate_sample_fails_the_unique_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path, Injection.DUPLICATE_SAMPLE)

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert rules(result) == {Rule.UNIQUE}
    violation = result.violations[0]
    assert violation.column == "sample_id"
    assert violation.row == result.rows_checked + 1
    assert violation.value == "BIO-001-S001"


def test_unknown_compound_fails_referential_integrity(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path, Injection.UNKNOWN_COMPOUND)

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert rules(result) == {Rule.FOREIGN_KEY}
    violation = result.violations[0]
    assert violation.column == "compound_id"
    assert violation.value == "CMP-000"
    assert "compounds.csv" in violation.message


def test_all_four_defects_are_reported_together(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    """Validation reports every defect, rather than stopping at the first."""
    samples, _ = study_files(
        tmp_path,
        Injection.MISSING_SAMPLE_ID,
        Injection.INVALID_DOSE,
        Injection.DUPLICATE_SAMPLE,
        Injection.UNKNOWN_COMPOUND,
    )

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert rules(result) == {Rule.REQUIRED, Rule.MINIMUM, Rule.UNIQUE, Rule.FOREIGN_KEY}
    assert len(result.violations) == 4
    assert [violation.row for violation in result.violations] == sorted(
        violation.row for violation in result.violations if violation.row is not None
    )


def test_a_well_formed_but_unknown_compound_id_fails_only_the_foreign_key(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    """CMP-000 satisfies the pattern; the defect is the dangling reference alone."""
    samples, _ = study_files(tmp_path, Injection.UNKNOWN_COMPOUND)

    result = validate_dataset(samples_contract, samples)

    assert Rule.PATTERN not in rules(result)


# --------------------------------------------------------------------------
# Structural failures
# --------------------------------------------------------------------------


def test_a_missing_referenced_file_is_one_clear_violation(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, compounds = study_files(tmp_path)
    compounds.unlink()

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    file_level = [violation for violation in result.violations if violation.row is None]
    assert len(file_level) == 1
    assert file_level[0].rule is Rule.FOREIGN_KEY
    assert "cannot read referenced file compounds.csv" in file_level[0].message


def test_a_referenced_file_without_the_key_column_is_reported(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, compounds = study_files(tmp_path)
    compounds.write_text("id,compound_name,mechanism_class\nCMP-001,Zonaxamib,kinase\n", "utf-8")

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    assert any(
        "has no column 'compound_id'" in violation.message for violation in result.violations
    )


def test_missing_required_columns_are_reported(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    dataset = tmp_path / "samples.csv"
    dataset.write_text("sample_id,study_id\nBIO-001-S001,BIO-001\n", encoding="utf-8")

    result = validate_dataset(samples_contract, dataset)

    missing = {
        violation.column for violation in result.violations if violation.rule is Rule.COLUMN_MISSING
    }
    assert missing == {"compound_id", "treatment", "dose", "dose_unit", "tissue", "replicate"}
    assert all(violation.row is None for violation in result.violations)


def test_extra_columns_are_rejected_under_the_forbid_policy(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    header, *rest = samples.read_text(encoding="utf-8").splitlines()
    samples.write_text(
        "\n".join([f"{header},operator", *(f"{line},alice" for line in rest)]) + "\n",
        encoding="utf-8",
    )

    result = validate_dataset(samples_contract, samples)

    assert result.passed is False
    unexpected = [
        violation for violation in result.violations if violation.rule is Rule.COLUMN_UNEXPECTED
    ]
    assert [violation.column for violation in unexpected] == ["operator"]
    assert "not declared by the contract" in unexpected[0].message


def test_extra_columns_are_ignored_under_the_allow_policy(tmp_path: Path) -> None:
    samples, _ = study_files(tmp_path)
    header, *rest = samples.read_text(encoding="utf-8").splitlines()
    samples.write_text(
        "\n".join([f"{header},operator", *(f"{line},alice" for line in rest)]) + "\n",
        encoding="utf-8",
    )
    permissive = load_contract(SAMPLES_CONTRACT).model_copy(
        update={"extra_columns": ExtraColumnPolicy.ALLOW}
    )

    assert validate_dataset(permissive, samples).passed is True


def test_an_empty_file_reports_every_column_as_missing(
    tmp_path: Path, compounds_contract: DataContract
) -> None:
    dataset = tmp_path / "compounds.csv"
    dataset.write_text("", encoding="utf-8")

    result = validate_dataset(compounds_contract, dataset)

    assert result.rows_checked == 0
    assert rules(result) == {Rule.COLUMN_MISSING}
    assert len(result.violations) == 3


def test_a_header_only_file_passes_with_no_rows(
    tmp_path: Path, compounds_contract: DataContract
) -> None:
    dataset = tmp_path / "compounds.csv"
    dataset.write_text("compound_id,compound_name,mechanism_class\n", encoding="utf-8")

    result = validate_dataset(compounds_contract, dataset)

    assert result.passed is True
    assert result.rows_checked == 0


def test_a_missing_dataset_raises(tmp_path: Path, compounds_contract: DataContract) -> None:
    with pytest.raises(DatasetError):
        validate_dataset(compounds_contract, tmp_path / "absent.csv")


def test_a_non_numeric_value_fails_the_type_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    samples.write_text(
        samples.read_text(encoding="utf-8").replace(",0.00,uM,", ",low,uM,", 1),
        encoding="utf-8",
    )

    result = validate_dataset(samples_contract, samples)

    assert rules(result) == {Rule.TYPE}
    assert "not a valid number" in result.violations[0].message


def test_a_fractional_replicate_fails_the_integer_type(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    lines = samples.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ",1.5"
    samples.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = validate_dataset(samples_contract, samples)

    assert rules(result) == {Rule.TYPE}
    assert result.violations[0].column == "replicate"


def test_an_unknown_tissue_fails_the_allowed_values_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    samples.write_text(
        samples.read_text(encoding="utf-8").replace(",liver,", ",spleen,", 1), encoding="utf-8"
    )

    result = validate_dataset(samples_contract, samples)

    assert rules(result) == {Rule.ALLOWED_VALUES}
    assert "spleen" in result.violations[0].message


def test_a_malformed_sample_id_fails_the_pattern_rule(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    samples.write_text(
        samples.read_text(encoding="utf-8").replace("BIO-001-S002", "sample two", 1),
        encoding="utf-8",
    )

    result = validate_dataset(samples_contract, samples)

    assert rules(result) == {Rule.PATTERN}


def test_a_duplicate_compound_id_fails_the_compounds_contract(
    tmp_path: Path, compounds_contract: DataContract
) -> None:
    _, compounds = study_files(tmp_path)
    with compounds.open("a", encoding="utf-8") as handle:
        handle.write("CMP-001,Duplicated,kinase_inhibitor\n")

    result = validate_dataset(compounds_contract, compounds)

    assert rules(result) == {Rule.UNIQUE}
    assert result.violations[0].value == "CMP-001"


def test_a_blank_mechanism_class_fails_the_compounds_contract(
    tmp_path: Path, compounds_contract: DataContract
) -> None:
    _, compounds = study_files(tmp_path)
    lines = compounds.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ","
    compounds.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = validate_dataset(compounds_contract, compounds)

    assert rules(result) == {Rule.REQUIRED}
    assert result.violations[0].column == "mechanism_class"


def test_validation_leaves_the_dataset_untouched(
    tmp_path: Path, samples_contract: DataContract
) -> None:
    samples, _ = study_files(tmp_path)
    before = samples.read_bytes()

    validate_dataset(samples_contract, samples)

    assert samples.read_bytes() == before
