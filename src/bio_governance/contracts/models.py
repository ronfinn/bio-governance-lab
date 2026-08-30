"""The contract-definition and validation-result models.

A *data contract* here is a small, explicit description of one CSV file: the
columns it must carry, what a value in each column has to look like, and which
column values must exist in another file. The vocabulary is deliberately closed
— there is no expression language and no inheritance — because every rule has to
be reportable as a named violation rather than as "an expression returned
false".

Contracts are loaded from YAML by :mod:`bio_governance.contracts.loader` and
applied by :mod:`bio_governance.contracts.validator`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Contract identifier, e.g. ``bio.samples``.
CONTRACT_ID_PATTERN = r"^[a-z0-9]+(?:\.[a-z0-9_]+)+$"

#: Semantic-looking contract version, e.g. ``1.0.0``. Nothing resolves ranges;
#: a contract is referred to by its exact version or not at all.
CONTRACT_VERSION_PATTERN = r"^\d+\.\d+\.\d+$"


class ColumnType(StrEnum):
    """The value types a contract can require of a column."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"


class ExtraColumnPolicy(StrEnum):
    """What the contract says about columns it does not declare.

    ``FORBID`` treats an undeclared column as a violation: the file has grown a
    field nobody agreed to. ``ALLOW`` ignores it.
    """

    FORBID = "forbid"
    ALLOW = "allow"


class Rule(StrEnum):
    """The named rule a violation is attributed to.

    Every violation names one of these, so a failure report is a set of rule
    identifiers rather than free text that has to be parsed back.
    """

    COLUMN_MISSING = "column_missing"
    COLUMN_UNEXPECTED = "column_unexpected"
    REQUIRED = "required"
    TYPE = "type"
    UNIQUE = "unique"
    MINIMUM = "minimum"
    ALLOWED_VALUES = "allowed_values"
    PATTERN = "pattern"
    FOREIGN_KEY = "foreign_key"


class Reference(BaseModel):
    """A foreign key onto a column of a sibling CSV file.

    ``file`` is a bare file name resolved next to the dataset being validated —
    ``samples.csv`` references ``compounds.csv`` in the same study directory.
    Paths are not accepted: there are no data-source connectors here, only the
    one generated study directory.
    """

    model_config = ConfigDict(frozen=True)

    file: str = Field(min_length=1)
    column: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_file_is_a_bare_name(self) -> Reference:
        if Path(self.file).name != self.file or self.file in {".", ".."}:
            raise ValueError(
                f"invalid reference file {self.file!r}: expected a bare file name "
                "resolved beside the dataset, such as 'compounds.csv'"
            )
        return self


class ColumnContract(BaseModel):
    """The rules a contract places on one column.

    A declared column must always be present in the file's header; ``required``
    is about the *values*, not the header. A blank value in a column that
    permits blanks is skipped by every other check — a vehicle control has no
    compound, and reporting a type, pattern or foreign-key failure for that
    absence would be noise.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    type: ColumnType = ColumnType.STRING
    description: str | None = None
    required: bool = True
    unique: bool = False
    minimum: float | None = None
    allowed_values: tuple[str, ...] | None = None
    pattern: str | None = None
    references: Reference | None = None

    @model_validator(mode="after")
    def _check_rules_are_applicable(self) -> ColumnContract:
        if self.minimum is not None and self.type is ColumnType.STRING:
            raise ValueError(
                f"column {self.name!r}: 'minimum' needs a numeric type, not {self.type.value!r}"
            )
        if self.allowed_values is not None and not self.allowed_values:
            raise ValueError(f"column {self.name!r}: 'allowed_values' must not be empty")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"column {self.name!r}: invalid 'pattern': {exc}") from exc
        return self


class DataContract(BaseModel):
    """A contract describing the expected shape and content of one CSV file."""

    model_config = ConfigDict(frozen=True)

    contract_id: str = Field(pattern=CONTRACT_ID_PATTERN)
    version: str = Field(pattern=CONTRACT_VERSION_PATTERN)
    asset: str = Field(min_length=1)
    description: str | None = None
    extra_columns: ExtraColumnPolicy = ExtraColumnPolicy.FORBID
    columns: tuple[ColumnContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_column_names_are_unique(self) -> DataContract:
        seen: set[str] = set()
        for column in self.columns:
            if column.name in seen:
                raise ValueError(f"column {column.name!r} is declared more than once")
            seen.add(column.name)
        return self

    @property
    def label(self) -> str:
        """The ``<contract_id>@<version>`` form used in reports."""
        return f"{self.contract_id}@{self.version}"


class Violation(BaseModel):
    """One way in which a dataset failed its contract.

    ``row`` is the 1-based line number in the CSV file, so the header is row 1
    and the first data row is row 2 — the number an editor shows. It is ``None``
    for violations about the file as a whole, such as a missing column.
    """

    model_config = ConfigDict(frozen=True)

    rule: Rule
    message: str = Field(min_length=1)
    column: str | None = None
    row: int | None = None
    value: str | None = None


class ContractValidationResult(BaseModel):
    """The outcome of validating one dataset against one contract.

    Validation is binary. There is no score, no weighting and no severity: the
    dataset either satisfies the contract or it does not. Grading data quality
    on a scale is a later milestone's job.
    """

    model_config = ConfigDict(frozen=True)

    contract_id: str
    version: str
    dataset: Path
    rows_checked: int = Field(ge=0)
    violations: tuple[Violation, ...] = ()

    @property
    def passed(self) -> bool:
        """True when the dataset satisfied every rule in the contract."""
        return not self.violations

    @property
    def label(self) -> str:
        """The ``<contract_id>@<version>`` form used in reports."""
        return f"{self.contract_id}@{self.version}"
