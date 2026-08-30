"""The data-quality result models.

A *contract* asks whether one file conforms to its declared structure. A
*quality report* asks a different question: whether the study, taken as a whole,
holds the data it says it holds and hangs together across its files. The two
layers are deliberately separate, so these models do not reuse
:class:`~bio_governance.contracts.models.Violation` — a contract violation names
a row and a column, and a quality finding names neither.

A report is a list of named checks and the worst status among them. There is no
numeric score: a score would need weights nobody agreed on, and a number cannot
tell a steward what to do next.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class QualityCheck(StrEnum):
    """The named checks a study is evaluated against.

    The vocabulary is closed for the same reason the contract rules are: a
    report has to be a set of identifiers a later milestone can act on, not
    free text.
    """

    SAMPLE_COUNT_CONSISTENCY = "sample_count_consistency"
    VEHICLE_CONTROL_PRESENCE = "vehicle_control_presence"
    COMPOUND_COVERAGE = "compound_coverage"
    EXPRESSION_SAMPLE_ALIGNMENT = "expression_sample_alignment"
    EXPRESSION_COMPLETENESS = "expression_completeness"
    EXPRESSION_GENE_COUNT = "expression_gene_count"


class QualityCheckStatus(StrEnum):
    """The outcome of a check, and of a report as a whole.

    ``WARN`` is for a finding that is worth recording but must not stop a
    pipeline. None of the six checks currently emits one — every defect they
    look for makes the study unusable — but the distinction belongs in the
    model, because the alternative is a later non-blocking check having to
    choose between silence and failing the gate.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


#: Worst-first. ``overall_status`` is the first of these any check reported.
_SEVERITY = (QualityCheckStatus.FAIL, QualityCheckStatus.WARN, QualityCheckStatus.PASS)


class QualityCheckResult(BaseModel):
    """What one check found.

    ``observed`` and ``expected`` carry the values behind the message where a
    reader would otherwise have to parse them back out of the prose. They are
    strings because a check may observe a count, a list of identifiers or a
    ratio, and inventing a union of those would buy nothing.
    """

    model_config = ConfigDict(frozen=True)

    check_id: QualityCheck
    status: QualityCheckStatus
    message: str = Field(min_length=1)
    observed: str | None = None
    expected: str | None = None

    @property
    def passed(self) -> bool:
        """True when the check found nothing to report."""
        return self.status is QualityCheckStatus.PASS


class QualityReport(BaseModel):
    """The quality evidence for one study.

    ``overall_status`` is derived, not stored: FAIL if any check failed, WARN if
    none failed but at least one warned, PASS otherwise. Deriving it means a
    report cannot claim a status its checks do not support.
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    checks: tuple[QualityCheckResult, ...] = Field(min_length=1)

    # The ignore is mypy's limitation, not a loose type: it does not support any
    # decorator above @property, and computed_field is how a derived value gets
    # into model_dump so the JSON evidence carries the verdict.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall_status(self) -> QualityCheckStatus:
        """The worst status any check reported."""
        reported = {check.status for check in self.checks}
        return next(status for status in _SEVERITY if status in reported)

    @property
    def failed(self) -> bool:
        """True when at least one check failed, and the study must not proceed."""
        return self.overall_status is QualityCheckStatus.FAIL
