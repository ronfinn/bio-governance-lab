"""Tests for the governance metadata declaration and its validator.

The declaration is evidence, so the tests are about what the validator will and
will not accept as evidence: the shipped declarations validate, and every way a
declaration can fail to say who owns a study and how it is classified is
reported — attributed to the part of the file it is about, all at once, and
never as a crash. The study each declaration is checked against is generated,
not hand-made, because the study directory is what makes a mismatch detectable.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from bio_governance.cli import app
from bio_governance.governance import (
    GovernanceMetadata,
    MetadataError,
    MetadataField,
    MetadataValidationResult,
    validate_metadata,
)
from bio_governance.models import Classification, Ownership
from conftest import GOVERNANCE_DIR

runner = CliRunner()

VALID = """\
study_id: BIO-001
classification: internal
ownership:
  owner: Avery Example
  steward: Jordan Example
  contact: bio-001-governance@example.org
"""


@pytest.fixture
def study(tmp_path: Path) -> Path:
    """A generated study directory named BIO-001, which a declaration must match."""
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    return tmp_path / "data" / "BIO-001"


def declare(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "declaration.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def fields(result: MetadataValidationResult) -> list[MetadataField]:
    return [problem.field for problem in result.problems]


# --- what validates --------------------------------------------------------


@pytest.mark.parametrize("declaration", sorted(GOVERNANCE_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_shipped_declaration_validates_for_its_study(
    tmp_path: Path, declaration: Path
) -> None:
    """The committed files are what is under test, not an inline copy of one."""
    generated = runner.invoke(
        app, ["demo", "generate", "--study", declaration.stem, "--output", str(tmp_path)]
    )
    assert generated.exit_code == 0, generated.output

    result = validate_metadata(declaration, tmp_path / declaration.stem)

    assert result.passed, result.problems
    assert result.study_id == declaration.stem
    assert result.classification is Classification.INTERNAL
    assert result.ownership is not None
    assert result.ownership.contact.endswith("@example.org")


def test_a_valid_declaration_is_the_domain_model_s_ownership_and_classification(
    tmp_path: Path, study: Path
) -> None:
    result = validate_metadata(declare(tmp_path, VALID), study)

    assert result.metadata == GovernanceMetadata(
        study_id="BIO-001",
        ownership=Ownership(
            owner="Avery Example",
            steward="Jordan Example",
            contact="bio-001-governance@example.org",
        ),
        classification=Classification.INTERNAL,
    )


def test_the_result_round_trips_as_json_evidence(tmp_path: Path, study: Path) -> None:
    result = validate_metadata(declare(tmp_path, VALID), study)

    document = json.loads(result.model_dump_json())

    assert document["passed"] is True
    assert document["classification"] == "internal"
    assert MetadataValidationResult.model_validate(document) == result


# --- what does not, and where the problem is attributed ---------------------


@pytest.mark.parametrize(
    ("text", "field", "fragment"),
    [
        (VALID.replace("study_id: BIO-001\n", ""), MetadataField.STUDY_ID, "missing"),
        (VALID.replace("BIO-001\n", "bio-001\n", 1), MetadataField.STUDY_ID, "not a study"),
        (VALID.replace("classification: internal\n", ""), MetadataField.CLASSIFICATION, "missing"),
        (VALID.replace("internal", "secret"), MetadataField.CLASSIFICATION, "'secret'"),
        (VALID.replace("internal", "Internal"), MetadataField.CLASSIFICATION, "'Internal'"),
        (VALID.split("ownership:")[0], MetadataField.OWNERSHIP, "ownership is missing"),
        (VALID.replace("  steward: Jordan Example\n", ""), MetadataField.OWNERSHIP, "steward"),
        (VALID.replace("Avery Example", "'  '"), MetadataField.OWNERSHIP, "does not name"),
        (
            VALID.replace("bio-001-governance@example.org", "nobody"),
            MetadataField.OWNERSHIP,
            "contact",
        ),
    ],
    ids=[
        "missing-study",
        "malformed-study",
        "missing-classification",
        "unknown-classification",
        "wrong-case-classification",
        "missing-ownership",
        "missing-steward",
        "blank-owner",
        "invalid-contact",
    ],
)
def test_a_defective_declaration_names_the_part_that_is_wrong(
    tmp_path: Path, study: Path, text: str, field: MetadataField, fragment: str
) -> None:
    result = validate_metadata(declare(tmp_path, text), study)

    assert not result.passed
    assert result.metadata is None
    assert fields(result) == [field]
    assert fragment in result.problems[0].message


def test_a_declaration_for_another_study_is_refused(tmp_path: Path, study: Path) -> None:
    """A valid declaration about BIO-002 is no evidence about BIO-001."""
    result = validate_metadata(declare(tmp_path, VALID.replace("BIO-001", "BIO-002")), study)

    assert fields(result) == [MetadataField.STUDY_ID]
    assert "BIO-002" in result.problems[0].message
    assert "BIO-001" in result.problems[0].message


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("study_id: [BIO-001\n", "not valid YAML"),
        ("", "empty"),
        ("- BIO-001\n- internal\n", "mapping"),
    ],
    ids=["malformed-yaml", "empty", "not-a-mapping"],
)
def test_a_document_that_is_not_a_declaration_fails_as_a_whole(
    tmp_path: Path, study: Path, text: str, fragment: str
) -> None:
    result = validate_metadata(declare(tmp_path, text), study)

    assert fields(result) == [MetadataField.DECLARATION]
    assert fragment in result.problems[0].message
    assert result.ownership is None
    assert result.classification is None


def test_unsupported_structure_is_refused_rather_than_ignored(tmp_path: Path, study: Path) -> None:
    """A field nothing checks would make the file look like evidence it is not."""
    text = VALID + "retention_days: 30\n"
    text = text.replace("  steward:", "  team: Oncology\n  steward:")

    result = validate_metadata(declare(tmp_path, text), study)

    messages = [problem.message for problem in result.problems]
    assert fields(result) == [MetadataField.DECLARATION, MetadataField.OWNERSHIP]
    assert "retention_days" in messages[0]
    assert "ownership.team" in messages[1]


def test_every_problem_is_reported_in_one_pass(tmp_path: Path, study: Path) -> None:
    text = """\
study_id: BIO-002
classification: secret
ownership:
  owner: "  "
  contact: not-an-address
"""
    result = validate_metadata(declare(tmp_path, text), study)

    assert set(fields(result)) == {
        MetadataField.STUDY_ID,
        MetadataField.OWNERSHIP,
        MetadataField.CLASSIFICATION,
    }
    messages = " ".join(problem.message for problem in result.problems)
    assert "ownership.steward" in messages
    assert "ownership.contact" in messages
    assert "ownership.owner does not name anyone" in messages


def test_one_bad_part_does_not_discard_the_parts_that_validated(
    tmp_path: Path, study: Path
) -> None:
    """Ownership is still evidence when only the classification is wrong."""
    result = validate_metadata(declare(tmp_path, VALID.replace("internal", "secret")), study)

    assert result.ownership is not None
    assert result.ownership.owner == "Avery Example"
    assert result.classification is None
    assert result.metadata is None


def test_passed_is_derived_from_the_problems_and_cannot_be_asserted(
    tmp_path: Path, study: Path
) -> None:
    failed = validate_metadata(declare(tmp_path, VALID.replace("internal", "secret")), study)
    document = json.loads(failed.model_dump_json())
    document["passed"] = True

    assert MetadataValidationResult.model_validate(document).passed is False


# --- the models --------------------------------------------------------------


def test_the_declaration_model_is_closed() -> None:
    with pytest.raises(ValidationError):
        GovernanceMetadata.model_validate(
            {
                "study_id": "BIO-001",
                "classification": "internal",
                "ownership": {"owner": "A", "steward": "B", "contact": "c@example.org"},
                "retention": "7y",
            }
        )


# --- nothing to judge ---------------------------------------------------------


def test_an_unreadable_declaration_is_an_error_not_a_result(tmp_path: Path, study: Path) -> None:
    with pytest.raises(MetadataError, match="cannot read"):
        validate_metadata(tmp_path / "absent.yaml", study)


def test_a_directory_that_is_not_a_study_is_an_error(tmp_path: Path) -> None:
    unnamed = tmp_path / "not-a-study"
    unnamed.mkdir()

    with pytest.raises(MetadataError, match="not named for a study"):
        validate_metadata(declare(tmp_path, VALID), unnamed)


# --- the CLI ----------------------------------------------------------------


def test_cli_accepts_a_valid_declaration(tmp_path: Path, study: Path) -> None:
    result = runner.invoke(
        app,
        [
            "governance",
            "metadata",
            "validate",
            str(GOVERNANCE_DIR / "BIO-001.yaml"),
            "--study-dir",
            str(study),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "Owner: Avery Example" in result.output
    assert "Classification: internal" in result.output


def test_cli_exits_1_and_still_writes_the_evidence_for_an_invalid_declaration(
    tmp_path: Path, study: Path
) -> None:
    evidence = tmp_path / "results" / "metadata" / "governance-metadata.json"

    result = runner.invoke(
        app,
        [
            "governance",
            "metadata",
            "validate",
            str(declare(tmp_path, VALID.replace("internal", "secret"))),
            "--study-dir",
            str(study),
            "--json-out",
            str(evidence),
        ],
    )

    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "classification  classification 'secret'" in result.output
    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["passed"] is False
    assert document["problems"][0]["field"] == "classification"


def test_cli_exits_2_for_a_missing_declaration(tmp_path: Path, study: Path) -> None:
    result = runner.invoke(
        app,
        [
            "governance",
            "metadata",
            "validate",
            str(tmp_path / "BIO-001.yaml"),
            "--study-dir",
            str(study),
        ],
    )

    assert result.exit_code == 2


def test_cli_exits_2_for_a_study_directory_not_named_for_a_study(tmp_path: Path) -> None:
    unnamed = tmp_path / "scratch"
    unnamed.mkdir()

    result = runner.invoke(
        app,
        [
            "governance",
            "metadata",
            "validate",
            str(declare(tmp_path, VALID)),
            "--study-dir",
            str(unnamed),
        ],
    )

    assert result.exit_code == 2
    assert "not named for a study" in result.output
