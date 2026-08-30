"""Evaluating a generated study for data quality.

Contract validation asks whether one file conforms to its declared structure.
These checks ask whether the *study* is internally consistent and usable: does
it hold the number of samples it claims, is there a control to compare against,
is every registered compound actually tested, and does the expression matrix
line up with the manifest.

The two layers are separate on purpose. A file can satisfy every rule in its
contract and still describe a study nobody can analyse — delete the vehicle
controls from ``samples.csv`` and the contract still passes, because every
remaining row is well-formed. That is why these checks are not a restatement of
the contract rules: a rule already enforced per-row by a contract has no reason
to be repeated here.

Evaluation is deterministic and reads only the four files of one study
directory. Nothing here consults a clock, a database or previous runs.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from bio_governance.quality.models import (
    QualityCheck,
    QualityCheckResult,
    QualityCheckStatus,
    QualityReport,
)

STUDY_FILE = "study.json"
COMPOUNDS_FILE = "compounds.csv"
SAMPLES_FILE = "samples.csv"
EXPRESSION_FILE = "expression.csv"

#: The control condition. Restated here rather than imported from the
#: generator: these checks describe what a usable study looks like, and reusing
#: the generator's own constants would make a passing report say only that the
#: generator agrees with itself.
VEHICLE_TREATMENT = "vehicle"

#: Columns of ``expression.csv`` that describe the gene rather than a sample.
#: Everything else in the header is taken to be a sample column.
GENE_COLUMNS = ("gene_id", "gene_symbol")

#: How many identifiers a message lists before it stops naming them.
_LIST_LIMIT = 5

#: One CSV row. ``DictReader`` yields ``None`` for a column a short row omits.
_Row = dict[str, str | None]


class StudyError(Exception):
    """The study directory could not be read as a study."""


class _Declaration(NamedTuple):
    """What ``study.json`` claims, which the files are then checked against."""

    study_id: str
    sample_count: int
    gene_count: int


def evaluate_study(directory: Path) -> QualityReport:
    """Evaluate the study in ``directory`` and return its quality report.

    Raises :class:`StudyError` if any of the four files is missing or cannot be
    read as what it claims to be. A study that reads but is inconsistent comes
    back as a failing report, not an exception: an inconsistency is a finding,
    and findings belong in the evidence.
    """
    declared = _read_declaration(directory / STUDY_FILE)
    _, compounds = _read_csv(directory / COMPOUNDS_FILE)
    _, samples = _read_csv(directory / SAMPLES_FILE)
    expression_header, expression = _read_csv(directory / EXPRESSION_FILE)
    sample_columns = tuple(name for name in expression_header if name not in GENE_COLUMNS)

    return QualityReport(
        study_id=declared.study_id,
        checks=(
            _check_sample_count(declared, samples),
            _check_vehicle_control(samples),
            _check_compound_coverage(compounds, samples),
            _check_expression_alignment(samples, sample_columns),
            _check_expression_completeness(expression, sample_columns),
            _check_gene_count(declared, expression),
        ),
    )


def _check_sample_count(declared: _Declaration, samples: Sequence[_Row]) -> QualityCheckResult:
    """Cross-file: does ``samples.csv`` hold as many rows as the study declares?

    No contract can answer this. A contract sees one file, and the number of
    rows a study was supposed to produce is written in another.
    """
    actual = len(samples)
    return _result(
        QualityCheck.SAMPLE_COUNT_CONSISTENCY,
        actual == declared.sample_count,
        passed=f"{SAMPLES_FILE} holds the {actual} rows {STUDY_FILE} declares",
        failed=(
            f"{SAMPLES_FILE} holds {actual} rows but {STUDY_FILE} declares {declared.sample_count}"
        ),
        observed=str(actual),
        expected=str(declared.sample_count),
    )


def _check_vehicle_control(samples: Sequence[_Row]) -> QualityCheckResult:
    """Is there a control to compare the treated samples against?

    A study of treatments with nothing to compare them to is well-formed and
    useless, which is exactly the sort of defect a contract cannot see.
    """
    controls = sum(1 for row in samples if _cell(row, "treatment") == VEHICLE_TREATMENT)
    return _result(
        QualityCheck.VEHICLE_CONTROL_PRESENCE,
        controls > 0,
        passed=f"{controls} sample(s) carry the {VEHICLE_TREATMENT!r} control treatment",
        failed=f"no sample carries the {VEHICLE_TREATMENT!r} control treatment",
        observed=str(controls),
        expected="at least 1",
    )


def _check_compound_coverage(
    compounds: Sequence[_Row], samples: Sequence[_Row]
) -> QualityCheckResult:
    """Is every registered compound actually tested somewhere?

    The samples contract's foreign key runs the other way — it rejects a sample
    referencing a compound that does not exist. A compound registered and never
    used breaks no rule in either file.
    """
    registered = _unique(_cell(row, "compound_id") for row in compounds)
    tested = {_cell(row, "compound_id") for row in samples} - {""}
    untested = tuple(compound for compound in registered if compound not in tested)
    return _result(
        QualityCheck.COMPOUND_COVERAGE,
        not untested,
        passed=f"all {len(registered)} registered compounds have at least one treated sample",
        failed=(
            f"{len(untested)} registered compound(s) have no treated sample: {_summarise(untested)}"
        ),
        observed=f"{len(registered) - len(untested)} of {len(registered)} covered",
        expected=f"all {len(registered)} covered",
    )


def _check_expression_alignment(
    samples: Sequence[_Row], sample_columns: Sequence[str]
) -> QualityCheckResult:
    """Do the expression matrix's columns match the sample manifest exactly?

    Both directions matter and are reported separately: a sample with no
    measurements is missing data, and a measured sample nobody registered is
    data of unknown origin.
    """
    manifest = _unique(_cell(row, "sample_id") for row in samples)
    measured = _unique(sample_columns)
    missing = tuple(sample for sample in manifest if sample not in set(measured))
    unexpected = tuple(sample for sample in measured if sample not in set(manifest))

    parts = []
    if missing:
        parts.append(f"{len(missing)} missing from {EXPRESSION_FILE} ({_summarise(missing)})")
    if unexpected:
        parts.append(f"{len(unexpected)} not in {SAMPLES_FILE} ({_summarise(unexpected)})")

    return _result(
        QualityCheck.EXPRESSION_SAMPLE_ALIGNMENT,
        not parts,
        passed=f"{EXPRESSION_FILE} measures exactly the {len(manifest)} manifested samples",
        failed="; ".join(parts),
        observed=f"{len(measured)} column(s)",
        expected=f"{len(manifest)} sample(s)",
    )


def _check_expression_completeness(
    expression: Sequence[_Row], sample_columns: Sequence[str]
) -> QualityCheckResult:
    """Is every measurement present and a finite number?

    Counted rather than enumerated: a matrix has thousands of cells, and a
    report with one finding per bad cell is a report nobody reads.
    """
    total = len(expression) * len(sample_columns)
    unusable = sum(
        1
        for row in expression
        for column in sample_columns
        if not _is_finite_number(_cell(row, column))
    )
    return _result(
        QualityCheck.EXPRESSION_COMPLETENESS,
        unusable == 0,
        passed=f"all {total} measurements are finite numbers",
        failed=f"{unusable} of {total} measurements are blank or not a finite number",
        observed=str(unusable),
        expected="0",
    )


def _check_gene_count(declared: _Declaration, expression: Sequence[_Row]) -> QualityCheckResult:
    """Cross-file: does the matrix carry as many genes as the study declares?"""
    actual = len(expression)
    return _result(
        QualityCheck.EXPRESSION_GENE_COUNT,
        actual == declared.gene_count,
        passed=f"{EXPRESSION_FILE} holds the {actual} genes {STUDY_FILE} declares",
        failed=(
            f"{EXPRESSION_FILE} holds {actual} genes but {STUDY_FILE} declares "
            f"{declared.gene_count}"
        ),
        observed=str(actual),
        expected=str(declared.gene_count),
    )


def _result(
    check_id: QualityCheck,
    satisfied: bool,
    *,
    passed: str,
    failed: str,
    observed: str,
    expected: str,
) -> QualityCheckResult:
    """Build a check result from the two messages the check can report."""
    return QualityCheckResult(
        check_id=check_id,
        status=QualityCheckStatus.PASS if satisfied else QualityCheckStatus.FAIL,
        message=passed if satisfied else failed,
        observed=observed,
        expected=expected,
    )


def _read_declaration(path: Path) -> _Declaration:
    """Read the three claims ``study.json`` makes that the checks test.

    The file is read as plain JSON rather than through the generator's
    ``StudyMetadata``. What is wanted here is the study's own declaration, and
    borrowing the generator's model to read it would tie this layer to the one
    thing it is meant to be independent of.
    """
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise StudyError(f"{path} is not valid UTF-8: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"{path} is not valid JSON: {exc.msg} (line {exc.lineno})") from exc

    if not isinstance(document, dict):
        raise StudyError(f"{path} must contain a JSON object, got {type(document).__name__}")

    study_id = document.get("study_id")
    if not isinstance(study_id, str) or not study_id:
        raise StudyError(f"{path} has no usable 'study_id'")
    return _Declaration(
        study_id=study_id,
        sample_count=_declared_count(path, document, "sample_count"),
        gene_count=_declared_count(path, document, "gene_count"),
    )


def _declared_count(path: Path, document: dict[str, Any], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudyError(f"{path} has no usable {field!r}: expected a whole number")
    return value


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[_Row]]:
    """Return a file's header and rows, raising :class:`StudyError` if unreadable."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows: list[_Row] = list(reader)
            header = tuple(reader.fieldnames or ())
    except OSError as exc:
        raise StudyError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise StudyError(f"{path} is not valid UTF-8: {exc.reason}") from exc
    if not header:
        raise StudyError(f"{path} has no header row")
    return header, rows


def _cell(row: _Row, name: str) -> str:
    """Return a cell's value, stripped. A short row reads as blank, not as missing."""
    value = row.get(name)
    return value.strip() if isinstance(value, str) else ""


def _is_finite_number(value: str) -> bool:
    """True when ``value`` is a measurement something can be computed from."""
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    """The non-blank values, in order, without repeats."""
    return tuple(dict.fromkeys(value for value in values if value))


def _summarise(values: Sequence[str]) -> str:
    """Name the first few values, then say how many more there are."""
    if len(values) <= _LIST_LIMIT:
        return ", ".join(values)
    listed = ", ".join(values[:_LIST_LIMIT])
    return f"{listed}, and {len(values) - _LIST_LIMIT} more"
