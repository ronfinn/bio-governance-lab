"""The governance decision model.

Every earlier layer produced *evidence*: a contract result saying whether a file
conformed, a quality report saying whether the study hung together, a curated
directory, a pair of OpenLineage events. None of them answered the question a
data steward actually asks — may this study be used?

These models are that answer, and they are deliberately small. A
:class:`GovernanceReport` is a study identifier and a list of named checks; the
decision is *derived* from those checks and cannot be set. That is the whole
point of the layer:

    **Deterministic code decides. AI explains.**

A later milestone may put a language model in front of a report to say what the
verdict means and what to do about it. It must never be able to compute the
verdict, and it cannot: there is no field to write it to.

There is no numeric score, for the same reason there is none in the quality
layer. A score needs weights nobody agreed on, and "0.82" does not tell anybody
whether the study may be used.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GovernanceCheck(StrEnum):
    """The named checks a study's evidence is evaluated against.

    Closed, like the contract rules and the quality checks: a report has to be a
    set of identifiers something downstream can act on, not free text. Adding a
    check means adding a member here.
    """

    SAMPLES_CONTRACT = "samples_contract"
    COMPOUNDS_CONTRACT = "compounds_contract"
    DATA_QUALITY = "data_quality"
    CURATED_OUTPUTS = "curated_outputs"
    LINEAGE_EVIDENCE = "lineage_evidence"


class GovernanceCheckStatus(StrEnum):
    """The outcome of one governance check.

    ``WARN`` is the status for evidence that is present and coherent but reports
    something a human should look at — a quality report that warned. It must not
    block, and it must not be silent either.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class GovernanceDecision(StrEnum):
    """What the evidence, taken together, says about a study.

    ``READY`` means every check passed and the study may be used. ``REVIEW``
    means nothing failed but something wants a human's attention. ``BLOCKED``
    means at least one check failed and the study must not be used.
    """

    READY = "ready"
    REVIEW = "review"
    BLOCKED = "blocked"


#: Worst-first, and paired with the decision each status forces. ``decision`` is
#: the first entry whose status any check reported.
_SEVERITY: tuple[tuple[GovernanceCheckStatus, GovernanceDecision], ...] = (
    (GovernanceCheckStatus.FAIL, GovernanceDecision.BLOCKED),
    (GovernanceCheckStatus.WARN, GovernanceDecision.REVIEW),
    (GovernanceCheckStatus.PASS, GovernanceDecision.READY),
)


class GovernanceCheckResult(BaseModel):
    """What one governance check found.

    ``message`` is always populated, including for a passing check: a governance
    record that says only "pass" leaves a reader to guess what was looked at.
    """

    model_config = ConfigDict(frozen=True)

    check_id: GovernanceCheck
    status: GovernanceCheckStatus
    message: str = Field(min_length=1)

    @property
    def passed(self) -> bool:
        """True when the check found nothing to report."""
        return self.status is GovernanceCheckStatus.PASS


class GovernanceReport(BaseModel):
    """The governance verdict for one study, and the checks behind it.

    ``decision`` is a computed field, not a stored one. A caller cannot
    construct a report claiming ``READY`` while one of its checks fails, because
    there is nowhere to make that claim — the decision is read out of the checks
    every time it is asked for, including when the report is serialized.
    """

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    checks: tuple[GovernanceCheckResult, ...] = Field(min_length=1)

    # The ignore is mypy's limitation, not a loose type: it does not support any
    # decorator above @property, and computed_field is how the derived verdict
    # reaches model_dump so the JSON evidence carries it.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision(self) -> GovernanceDecision:
        """BLOCKED if any check failed, REVIEW if any warned, READY otherwise."""
        reported = {check.status for check in self.checks}
        return next(decision for status, decision in _SEVERITY if status in reported)

    @property
    def ready(self) -> bool:
        """True when every check passed and the study may be used."""
        return self.decision is GovernanceDecision.READY
