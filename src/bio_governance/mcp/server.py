"""A read-only MCP server over a study's governance evidence.

Every earlier milestone put its evidence on disk and its verdict in a computed
field. This one gives an AI client a way to *read* that material — over the
Model Context Protocol, so any MCP host can ask a governed study what its
decision is and what stands behind it.

The boundary is the whole design:

    **Deterministic code decides. AI explains.**

There is no tool here that computes a governance decision, overrides one,
approves an asset, edits a report, emits lineage, publishes to a catalogue or
writes a file. Every tool is a reader, and each is annotated ``read_only_hint``
so a host can see that before it calls anything. The decision an AI client is
shown is the one
:class:`~bio_governance.governance.models.GovernanceReport` derives from its
checks, deserialized from the evaluator's own JSON — a model consuming this
server can describe a verdict and cannot reach the thing that produced it.

``why_not_ready`` is the tool that most looks like an explanation and most
carefully is not one. It partitions a report's existing checks by their existing
statuses. No language model is called, no finding is invented, and the same
study always yields the same answer.

Six tools and two resources, over stdio. The resources are there because a
governance report and a quality report are *documents* — addressable, quotable
things a host may want to attach to a conversation — and modelling them as
resources as well as tools is the honest use of the second MCP concept rather
than a duplication of the first for its own sake.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from bio_governance import __version__
from bio_governance.governance import GovernanceReport
from bio_governance.mcp.evidence import (
    DEFAULT_RESULTS_ROOT,
    ContractResults,
    EvidenceError,
    LineageSummary,
    ReadinessExplanation,
    StudySummary,
    contract_results,
    discover_studies,
    governance_report,
    lineage_summary,
    quality_report,
    readiness,
)
from bio_governance.quality import QualityReport

SERVER_NAME = "bio-governance"

#: What one evidence reader returns. The readers share a shape — results root,
#: usually a study identifier, one model out — so they share one wrapper.
EvidenceT = TypeVar("EvidenceT")

#: What every tool on this server is. ``read_only_hint`` is the declaration a
#: host can act on: nothing here modifies governance state, so nothing here
#: needs to be gated behind a confirmation. ``open_world_hint`` is false because
#: the answers come from one local directory and no network at all.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

INSTRUCTIONS = """\
Read-only access to the governance evidence bio-governance-lab produces for a
synthetic life-sciences study.

The governance decision — READY, REVIEW or BLOCKED — is computed by
deterministic code from evidence on disk, and is exposed here as it stands. You
can read and explain it. You cannot calculate, override or approve it, and no
tool on this server writes anything.

Start with 'list_studies'. For one study, 'get_governance_report' is the
verdict, 'why_not_ready' is what stands behind it, and 'get_quality_report',
'get_contract_results' and 'get_lineage_summary' are the evidence it rests on.\
"""


def build_server(results_root: Path = DEFAULT_RESULTS_ROOT) -> MCPServer[None]:
    """Build the MCP server, reading studies from ``results_root`` and nowhere else.

    The root is closed over rather than taken as a tool argument, so no client
    can point the server at another directory. Study identifiers still arrive
    from outside, and every tool passes them through
    :func:`~bio_governance.mcp.evidence.study_directory`, which validates them
    as asset identifiers and confines the resolved path to the root.
    """
    server: MCPServer[None] = MCPServer(
        name=SERVER_NAME,
        title="bio-governance-lab",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    @server.tool(annotations=READ_ONLY)
    def list_studies() -> list[StudySummary]:
        """List the governed studies under the results root, with their decisions.

        A study whose pipeline run stopped at a gate is listed with no decision:
        it was never evaluated, which is different from having passed.
        """
        return list(_evidence(discover_studies, results_root))

    @server.tool(annotations=READ_ONLY)
    def get_governance_report(study_id: str) -> GovernanceReport:
        """Return the study's governance report: the decision and the five checks behind it.

        The decision is derived from the checks by the governance layer itself.
        Report it; do not recompute it, and do not disagree with it.
        """
        return _evidence(governance_report, results_root, study_id)

    @server.tool(annotations=READ_ONLY)
    def get_quality_report(study_id: str) -> QualityReport:
        """Return the study-level data-quality report: six checks and their overall status."""
        return _evidence(quality_report, results_root, study_id)

    @server.tool(annotations=READ_ONLY)
    def get_contract_results(study_id: str) -> ContractResults:
        """Return both contract validation results for the study, samples and compounds.

        A contract asks whether one file conforms to its declared structure, one
        row at a time; every violation is reported, not just the first.
        """
        return _evidence(contract_results, results_root, study_id)

    @server.tool(annotations=READ_ONLY)
    def get_lineage_summary(study_id: str) -> LineageSummary:
        """Summarise the OpenLineage run that produced the study's curated outputs.

        The run and job identities and the bio:// datasets on either side of the
        curation, rather than the raw events.
        """
        return _evidence(lineage_summary, results_root, study_id)

    @server.tool(annotations=READ_ONLY)
    def why_not_ready(study_id: str) -> ReadinessExplanation:
        """Say which governance checks stand between the study and READY.

        Deterministic: the report's own checks, partitioned by the statuses they
        already carry. BLOCKED returns the checks that failed, REVIEW the checks
        that warned, and READY neither. Nothing is inferred and no model is
        consulted.
        """
        return _evidence(readiness, results_root, study_id)

    @server.resource(
        "governance://studies/{study_id}/report",
        name="governance-report",
        title="Governance report",
        description="The READY/REVIEW/BLOCKED decision for one study, and its five checks.",
        mime_type="application/json",
    )
    def governance_resource(study_id: str) -> str:
        return _document(_read(governance_report, results_root, study_id))

    @server.resource(
        "quality://studies/{study_id}/report",
        name="quality-report",
        title="Data-quality report",
        description="The six study-level data-quality checks and their overall status.",
        mime_type="application/json",
    )
    def quality_resource(study_id: str) -> str:
        return _document(_read(quality_report, results_root, study_id))

    return server


def _evidence(reader: Callable[..., EvidenceT], *args: object) -> EvidenceT:
    """Call a reader for a tool, reporting an evidence problem as a tool error.

    :class:`~mcp.server.mcpserver.exceptions.ToolError` is the SDK's channel for
    a failure the tool anticipated: the client gets the message as an error
    result and the server logs one line rather than a traceback. A study that
    was never evaluated is an ordinary state of this repository, not a crash.
    """
    try:
        return reader(*args)
    except EvidenceError as exc:
        raise ToolError(str(exc)) from exc


def _read(reader: Callable[..., EvidenceT], *args: object) -> EvidenceT:
    """The same, for a resource, whose anticipated-failure channel is its own."""
    try:
        return reader(*args)
    except EvidenceError as exc:
        raise ResourceError(str(exc)) from exc


def _document(report: BaseModel) -> str:
    """One evidence document as the JSON an MCP resource carries."""
    return json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
