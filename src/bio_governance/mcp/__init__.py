"""A read-only MCP server exposing a governed study's evidence."""

from bio_governance.mcp.evidence import (
    DEFAULT_RESULTS_ROOT,
    EVIDENCE_FILES,
    GOVERNANCE_REPORT,
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
    study_directory,
)
from bio_governance.mcp.server import SERVER_NAME, build_server

__all__ = [
    "DEFAULT_RESULTS_ROOT",
    "EVIDENCE_FILES",
    "GOVERNANCE_REPORT",
    "SERVER_NAME",
    "ContractResults",
    "EvidenceError",
    "LineageSummary",
    "ReadinessExplanation",
    "StudySummary",
    "build_server",
    "contract_results",
    "discover_studies",
    "governance_report",
    "lineage_summary",
    "quality_report",
    "readiness",
    "study_directory",
]
