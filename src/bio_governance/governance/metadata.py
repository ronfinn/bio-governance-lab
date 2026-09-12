"""Governance metadata: who answers for a study, and how it is classified.

The domain model has carried :class:`~bio_governance.models.Ownership` and
:class:`~bio_governance.models.Classification` since the first milestone, but
nothing produced evidence for either, so the governance evaluation could not
check them — a check with nothing to read always passes. This module is the
evidence: one small YAML declaration per study, committed beside the code, and a
validator that says whether it is a declaration this project can rely on::

    # governance/studies/BIO-001.yaml
    study_id: BIO-001
    classification: internal
    ownership:
      owner: Avery Example
      steward: Jordan Example
      contact: bio-001-governance@example.org

Three fields and no more. ``ownership`` *is* the domain model's ``Ownership``
and ``classification`` *is* its ``Classification``, so there is one ownership
vocabulary and one classification vocabulary in the project, not a second pair
invented for a file format. ``contact`` is an email address only because
``Ownership`` requires one; the shipped declarations use ``example.org``, which
is reserved for documentation and cannot reach anybody.

A declaration is not a data contract, and deliberately shares none of the
contract machinery. A contract describes the structure of one file and is
applied to data; a declaration states who is responsible for a study and how
sensitive it is, and is itself the thing under test. They are both YAML, and
that is the whole of the resemblance.

Validation is deterministic and study-local — no clock, no network, no
directory service. It reports every problem in one pass, each attributed to the
part of the declaration it is about, so the governance evaluation can fail
ownership without also failing a classification that was declared correctly.
There is no rule language, no policy and no notion of what a classification
*permits*: the check is that the evidence exists and is well formed.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from bio_governance.models import AssetIdentifier, Classification, Ownership
from bio_governance.models.identifiers import DOMAIN_PATTERN

#: The only top-level keys a declaration may carry.
DECLARATION_FIELDS = ("study_id", "ownership", "classification")

#: The two roles an ownership block names, each of which must name someone.
_NAMED_ROLES = ("owner", "steward")


class MetadataError(Exception):
    """The declaration or the study could not be read, so there is nothing to judge."""


class MetadataField(StrEnum):
    """Which part of a declaration a problem is about.

    Closed, so a problem is attributable rather than free text. ``declaration``
    covers the document as a whole — unparseable YAML, something other than a
    mapping, a field this format does not define — and so undermines every
    claim the file makes.
    """

    DECLARATION = "declaration"
    STUDY_ID = "study_id"
    OWNERSHIP = "ownership"
    CLASSIFICATION = "classification"


class MetadataProblem(BaseModel):
    """One way in which a declaration cannot be relied on."""

    model_config = ConfigDict(frozen=True)

    field: MetadataField
    message: str = Field(min_length=1)


class GovernanceMetadata(BaseModel):
    """A study's governance declaration, once it has validated.

    This is the schema of the YAML file and the value the catalogues project.
    It is closed: a declaration with a ``retention`` or ``team`` field is
    refused rather than read around, because a field nothing checks would make
    the file look like evidence for something it is not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_id: str = Field(pattern=DOMAIN_PATTERN.pattern)
    ownership: Ownership
    classification: Classification


class MetadataValidationResult(BaseModel):
    """The outcome of validating one declaration against one study.

    ``study_id`` is the study the declaration was checked *against*, taken from
    the study directory rather than from the file, so a declaration for another
    study cannot certify this one. ``ownership`` and ``classification`` are
    carried separately and each only when that part validated: a declaration
    whose classification is wrong still says who owns the study, and the
    governance evaluation reports the two claims as two checks.

    ``passed`` is computed, as on the contract result, so the JSON evidence
    states its own verdict and a reader cannot be handed one that disagrees
    with the problems it lists.
    """

    model_config = ConfigDict(frozen=True)

    declaration: Path
    study_id: str = Field(min_length=1)
    ownership: Ownership | None = None
    classification: Classification | None = None
    problems: tuple[MetadataProblem, ...] = ()

    # The ignore is mypy's limitation, not a loose type: it does not support any
    # decorator above @property, and computed_field is what puts the verdict
    # into model_dump.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        """True when the declaration can be relied on in full."""
        return not self.problems

    def problems_about(self, *fields: MetadataField) -> tuple[MetadataProblem, ...]:
        """The problems attributed to any of ``fields``, in the order found."""
        return tuple(problem for problem in self.problems if problem.field in fields)

    @property
    def metadata(self) -> GovernanceMetadata | None:
        """The validated declaration, or ``None`` if any part of it failed."""
        if not self.passed or self.ownership is None or self.classification is None:
            return None
        return GovernanceMetadata(
            study_id=self.study_id,
            ownership=self.ownership,
            classification=self.classification,
        )


def validate_metadata(declaration: Path, study_dir: Path) -> MetadataValidationResult:
    """Validate the governance declaration at ``declaration`` for the study in ``study_dir``.

    ``study_dir`` is a study directory named for the study, such as
    ``data/raw/BIO-001``; the declaration must name the same study. Every part
    of the declaration is checked, so the result lists everything wrong with it
    rather than the first thing.

    Raises :class:`MetadataError` only when there is nothing to judge: the file
    cannot be read, or the directory is not a study. A declaration that is
    malformed, incomplete or about another study is a *result* that did not
    pass — that is what the validator exists to report.
    """
    study_id = _study_id(study_dir)
    try:
        text = declaration.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetadataError(
            f"cannot read governance metadata {declaration}: {exc.strerror or exc}"
        ) from exc

    def failed(message: str) -> MetadataValidationResult:
        problem = MetadataProblem(field=MetadataField.DECLARATION, message=message)
        return MetadataValidationResult(
            declaration=declaration, study_id=study_id, problems=(problem,)
        )

    try:
        document: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return failed(f"not valid YAML: {_yaml_detail(exc)}")
    if document is None:
        return failed("the declaration is empty")
    if not isinstance(document, dict):
        return failed(f"expected a mapping at the top level, got {type(document).__name__}")

    problems = [
        MetadataProblem(
            field=MetadataField.DECLARATION,
            message=f"unsupported field {key!r}: a declaration holds only "
            + ", ".join(DECLARATION_FIELDS),
        )
        for key in sorted(str(key) for key in document if key not in DECLARATION_FIELDS)
    ]
    problems += _study_problems(document, study_id)
    ownership, ownership_problems = _ownership(document)
    classification, classification_problems = _classification(document)
    problems += ownership_problems + classification_problems

    return MetadataValidationResult(
        declaration=declaration,
        study_id=study_id,
        ownership=ownership,
        classification=classification,
        problems=tuple(problems),
    )


def _study_id(study_dir: Path) -> str:
    """Take the study identifier from the directory's name, as every layer does."""
    if not study_dir.is_dir():
        raise MetadataError(f"study directory not found: {study_dir}")
    study_id = study_dir.resolve().name
    try:
        AssetIdentifier.parse(f"bio://{study_id}/raw/samples")
    except ValueError as exc:
        raise MetadataError(f"{study_dir} is not named for a study: {exc}") from exc
    return study_id


def _study_problems(document: dict[Any, Any], study_id: str) -> list[MetadataProblem]:
    """Does the declaration name a study, and is it this one?"""

    def problem(message: str) -> list[MetadataProblem]:
        return [MetadataProblem(field=MetadataField.STUDY_ID, message=message)]

    if "study_id" not in document:
        return problem("study_id is missing")
    declared = document["study_id"]
    if not isinstance(declared, str) or not DOMAIN_PATTERN.match(declared):
        return problem(
            f"study_id {declared!r} is not a study identifier: expected a code such as 'BIO-001'"
        )
    if declared != study_id:
        return problem(f"study_id {declared} does not match the study being checked, {study_id}")
    return []


def _ownership(document: dict[Any, Any]) -> tuple[Ownership | None, list[MetadataProblem]]:
    """The declared ownership, parsed into the domain model, or why it is not usable."""

    def problems(*messages: str) -> tuple[None, list[MetadataProblem]]:
        return None, [
            MetadataProblem(field=MetadataField.OWNERSHIP, message=message) for message in messages
        ]

    if "ownership" not in document:
        return problems("ownership is missing")
    declared = document["ownership"]

    messages: list[str] = []
    try:
        ownership = Ownership.model_validate(declared)
    except ValidationError as exc:
        messages += _field_errors("ownership", exc)
    # Ownership's min_length=1 lets "  " through, and a role that names nobody
    # is not ownership. Checked on the raw mapping, so it is reported alongside
    # any other problem rather than hidden behind it.
    if isinstance(declared, dict):
        messages += [
            f"ownership.{role} does not name anyone"
            for role in _NAMED_ROLES
            if isinstance(declared.get(role), str)
            and declared[role]
            and not any(character.isalnum() for character in declared[role])
        ]
    if messages:
        return problems(*messages)
    return ownership, []


def _classification(
    document: dict[Any, Any],
) -> tuple[Classification | None, list[MetadataProblem]]:
    """The declared classification, from the domain model's vocabulary and no other."""
    if "classification" not in document:
        message = "classification is missing"
    else:
        declared = document["classification"]
        try:
            return Classification(declared), []
        except ValueError:
            allowed = ", ".join(member.value for member in Classification)
            message = f"classification {declared!r} is not one of {allowed}"
    return None, [MetadataProblem(field=MetadataField.CLASSIFICATION, message=message)]


def _field_errors(prefix: str, exc: ValidationError) -> list[str]:
    """Pydantic's errors as ``prefix.field: message``, one per problem."""
    messages = []
    for error in exc.errors():
        location = ".".join((prefix, *(str(part) for part in error["loc"])))
        messages.append(f"{location}: {error['msg']}")
    return messages


def _yaml_detail(exc: yaml.YAMLError) -> str:
    """Reduce a PyYAML error to its problem and location."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"{exc.problem or 'parse error'} (line {mark.line + 1}, column {mark.column + 1})"
    return str(exc).replace("\n", " ").strip()
