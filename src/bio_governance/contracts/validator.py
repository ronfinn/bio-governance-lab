"""Applying a contract to a CSV file.

The validator reads the file itself and checks it against the rules in the
contract. It deliberately does *not* reuse the generator's ``Sample`` and
``Compound`` models: if the thing that wrote the data also defined what correct
data looks like, a passing run would prove only that the generator is
self-consistent. The YAML contract is the independent statement of correctness.

Every rule is evaluated for every row. Validation does not stop at the first
failure, because the point of a contract report is to describe everything that
is wrong with a dataset in one pass.
"""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from bio_governance.contracts.models import (
    ColumnContract,
    ColumnType,
    ContractValidationResult,
    DataContract,
    ExtraColumnPolicy,
    Rule,
    Violation,
)

#: An integer column takes digits only. ``float()`` would accept '1.5' and
#: '1e3', which are not the same claim as 'this is a replicate number'.
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")


class DatasetError(Exception):
    """The dataset under validation could not be read at all."""


def validate_dataset(contract: DataContract, dataset: Path) -> ContractValidationResult:
    """Check ``dataset`` against ``contract`` and return a structured result.

    Raises :class:`DatasetError` if the dataset file itself cannot be read. A
    file that reads but breaks the contract — including one whose referenced
    sibling file is missing — comes back as a failing result, not an exception.
    """
    header, rows = _read_csv(dataset)

    violations: list[Violation] = []
    present = set(header)
    declared = {column.name: column for column in contract.columns}

    for column in contract.columns:
        if column.name not in present:
            violations.append(
                Violation(
                    rule=Rule.COLUMN_MISSING,
                    column=column.name,
                    message=f"required column {column.name!r} is not in the file header",
                )
            )
    if contract.extra_columns is ExtraColumnPolicy.FORBID:
        violations.extend(
            Violation(
                rule=Rule.COLUMN_UNEXPECTED,
                column=name,
                message=f"column {name!r} is not declared by the contract",
            )
            for name in header
            if name not in declared
        )

    checkable = tuple(column for column in contract.columns if column.name in present)
    references, reference_violations = _load_references(checkable, dataset)
    violations.extend(reference_violations)

    seen: dict[str, dict[str, int]] = {column.name: {} for column in checkable if column.unique}

    for offset, row in enumerate(rows):
        line = offset + 2  # row 1 is the header
        for column in checkable:
            violations.extend(_check_value(column, _cell(row, column.name), line, seen, references))

    return ContractValidationResult(
        contract_id=contract.contract_id,
        version=contract.version,
        dataset=dataset,
        rows_checked=len(rows),
        violations=tuple(violations),
    )


def _check_value(
    column: ColumnContract,
    value: str,
    line: int,
    seen: dict[str, dict[str, int]],
    references: dict[str, frozenset[str]],
) -> Iterator[Violation]:
    """Yield every violation one cell commits.

    A blank value in a column that permits blanks is not checked further: a
    vehicle control legitimately has no compound, and running the type, pattern
    or foreign-key rules over that absence would report noise. A value of the
    wrong type is likewise reported once rather than failing every rule that
    assumed it parsed.
    """
    if not value:
        if column.required:
            yield _violation(Rule.REQUIRED, column, line, value, "value is blank")
        return

    if column.type is not ColumnType.STRING:
        number = _parse_number(value, column.type)
        if number is None:
            yield _violation(
                Rule.TYPE, column, line, value, f"{value!r} is not a valid {column.type.value}"
            )
            return
        if column.minimum is not None and number < column.minimum:
            yield _violation(
                Rule.MINIMUM, column, line, value, f"{value} is below {column.minimum:g}"
            )

    if column.allowed_values is not None and value not in column.allowed_values:
        allowed = ", ".join(column.allowed_values)
        yield _violation(
            Rule.ALLOWED_VALUES, column, line, value, f"{value!r} is not one of: {allowed}"
        )

    if column.pattern is not None and not re.match(column.pattern, value):
        yield _violation(
            Rule.PATTERN, column, line, value, f"{value!r} does not match {column.pattern}"
        )

    if column.unique:
        first = seen[column.name].setdefault(value, line)
        if first != line:
            yield _violation(
                Rule.UNIQUE, column, line, value, f"duplicate {value} (first seen at row {first})"
            )

    reference = column.references
    if reference is not None and value not in references.get(column.name, frozenset()):
        yield _violation(
            Rule.FOREIGN_KEY,
            column,
            line,
            value,
            f"{value} not found in {reference.file} column {reference.column!r}",
        )


def _violation(
    rule: Rule, column: ColumnContract, line: int, value: str, message: str
) -> Violation:
    return Violation(rule=rule, column=column.name, row=line, value=value, message=message)


def _load_references(
    columns: Sequence[ColumnContract], dataset: Path
) -> tuple[dict[str, frozenset[str]], list[Violation]]:
    """Read the referenced key columns from files beside ``dataset``.

    A referenced file that is missing or that lacks the referenced column is a
    single file-level violation. The per-row foreign-key check then finds no key
    to match, so the report names the real problem once and the consequences
    afterwards.
    """
    references: dict[str, frozenset[str]] = {}
    violations: list[Violation] = []

    for column in columns:
        reference = column.references
        if reference is None:
            continue
        path = dataset.parent / reference.file
        try:
            header, rows = _read_csv(path)
        except DatasetError as exc:
            violations.append(
                Violation(
                    rule=Rule.FOREIGN_KEY,
                    column=column.name,
                    message=f"cannot read referenced file {reference.file}: {exc}",
                )
            )
            continue
        if reference.column not in header:
            violations.append(
                Violation(
                    rule=Rule.FOREIGN_KEY,
                    column=column.name,
                    message=(
                        f"referenced file {reference.file} has no column {reference.column!r}"
                    ),
                )
            )
            continue
        references[column.name] = frozenset(
            value for row in rows if (value := _cell(row, reference.column))
        )

    return references, violations


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str | None]]]:
    """Return a file's header and rows, raising :class:`DatasetError` if unreadable."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows: list[dict[str, str | None]] = list(reader)
            header = tuple(reader.fieldnames or ())
    except OSError as exc:
        raise DatasetError(exc.strerror or str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise DatasetError(f"not valid UTF-8: {exc.reason}") from exc
    return header, rows


def _cell(row: dict[str, str | None], name: str) -> str:
    """Return a cell's value, stripped. A short row reads as blank, not as missing."""
    value = row.get(name)
    return value.strip() if isinstance(value, str) else ""


def _parse_number(value: str, column_type: ColumnType) -> float | None:
    """Parse ``value`` as the given numeric type, or return ``None``."""
    if column_type is ColumnType.INTEGER:
        return float(value) if _INTEGER_PATTERN.match(value) else None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
