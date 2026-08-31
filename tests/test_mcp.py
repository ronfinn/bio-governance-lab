"""Tests for the read-only MCP governance server.

Everything here runs through a real MCP client. The SDK's ``Client`` speaks the
protocol directly to an ``MCPServer`` object in the same process, so these are
tests of the server as a *client sees it* — tool names, schemas, structured
content, error results, resource templates — without a subprocess, a host
application or Claude Desktop anywhere in the loop.

The evidence is produced, never hand-written: ``build_results`` runs the same
commands ``main.nf`` does and ``build_governance_report`` runs the evaluator over
what they left, exactly as ``EVALUATE_GOVERNANCE`` does. A test then damages one
piece of it and asks the server what it now says.

Two properties matter more than the rest, because they are the milestone's
claim. Nothing exposed here can write, and the decision a client is shown is the
one the governance layer derives from its checks — a report whose JSON has been
edited to claim ``READY`` is still read as ``BLOCKED``.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from bio_governance.mcp import build_server
from conftest import build_governance_report, build_results

pytestmark = pytest.mark.anyio

#: Every tool the server exposes, in the order it is registered. A test asserts
#: on the whole set rather than on membership: the read-only boundary is about
#: what is *not* here as much as what is.
TOOL_NAMES = (
    "list_studies",
    "get_governance_report",
    "get_quality_report",
    "get_contract_results",
    "get_lineage_summary",
    "why_not_ready",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    """A results root holding one clean, evaluated study, and its path."""
    build_governance_report(build_results(tmp_path))
    return tmp_path / "results"


def payload(result: CallToolResult) -> Any:
    """The structured content of a call that was supposed to succeed."""
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    return result.structured_content


def text(result: CallToolResult) -> str:
    """Everything the call said, for asserting on an error message."""
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def damage(path: Path, edit: Any) -> None:
    """Apply ``edit`` to a JSON evidence file in place."""
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(edit(document), indent=2) + "\n", encoding="utf-8")


def blocked(root: Path) -> Path:
    """Make the study BLOCKED the way a real run would: lose its provenance."""
    results = root / "BIO-001"
    (results / "lineage" / "openlineage.jsonl").unlink()
    return build_governance_report(results)


def under_review(root: Path) -> Path:
    """Make the study REVIEW by warning one quality check, then re-evaluating.

    The generator is not changed to manufacture the warning, for the reason the
    governance tests give: none of the six quality checks currently warns, and
    inventing one there to exercise a reader would put the defect in the wrong
    place.
    """
    results = root / "BIO-001"
    damage(
        results / "quality" / "dq-report.json",
        lambda document: {
            **document,
            "checks": [{**document["checks"][0], "status": "warn"}, *document["checks"][1:]],
        },
    )
    return build_governance_report(results)


# --- the server as a client sees it ---------------------------------------


async def test_the_server_exposes_the_expected_tools(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        tools = (await client.list_tools()).tools

    assert tuple(tool.name for tool in tools) == TOOL_NAMES
    assert all(tool.description for tool in tools)


async def test_every_tool_is_declared_read_only(governed: Path) -> None:
    """The boundary, stated where a host can act on it before calling anything."""
    async with Client(build_server(governed)) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


async def test_no_tool_writes_publishes_or_reads_an_arbitrary_file(governed: Path) -> None:
    """This milestone is read-only, and the tool list is where that is enforced.

    A generic file reader would make the results root meaningless, and a write,
    approve or publish tool would put an AI client on the deciding side of the
    line the whole project draws.
    """
    async with Client(build_server(governed)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    forbidden = ("read_file", "write", "approve", "publish", "delete", "evaluate", "set_")
    assert not [name for name in names if any(word in name for word in forbidden)]


async def test_the_server_introduces_itself_through_the_protocol(governed: Path) -> None:
    """The in-memory client completes a real initialize handshake."""
    async with Client(build_server(governed)) as client:
        assert client.server_info.name == "bio-governance"
        assert client.instructions is not None
        assert "cannot calculate, override or approve" in client.instructions


# --- the six tools --------------------------------------------------------


async def test_list_studies_discovers_the_governed_study(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        studies = payload(await client.call_tool("list_studies", {}))["result"]

    assert studies == [
        {
            "study_id": "BIO-001",
            "decision": "ready",
            "detail": "5 of 5 governance checks passed",
        }
    ]


async def test_list_studies_reports_an_unevaluated_study_without_a_decision(
    governed: Path,
) -> None:
    """A run stopped at a gate has evidence and no verdict. Say so, do not guess."""
    (governed / "BIO-001" / "governance" / "governance-report.json").unlink()

    async with Client(build_server(governed)) as client:
        studies = payload(await client.call_tool("list_studies", {}))["result"]

    assert studies[0]["study_id"] == "BIO-001"
    assert studies[0]["decision"] is None
    assert "no governance decision yet" in studies[0]["detail"]


async def test_list_studies_ignores_a_directory_that_is_not_a_study(governed: Path) -> None:
    (governed / "scratch").mkdir()
    (governed / "BIO-002").mkdir()

    async with Client(build_server(governed)) as client:
        studies = payload(await client.call_tool("list_studies", {}))["result"]

    assert [study["study_id"] for study in studies] == ["BIO-001"]


async def test_get_governance_report_returns_ready_for_a_clean_run(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        report = payload(await client.call_tool("get_governance_report", {"study_id": "BIO-001"}))

    assert report["study_id"] == "BIO-001"
    assert report["decision"] == "ready"
    assert [check["check_id"] for check in report["checks"]] == [
        "samples_contract",
        "compounds_contract",
        "data_quality",
        "curated_outputs",
        "lineage_evidence",
    ]


async def test_the_decision_is_derived_even_when_the_evidence_file_claims_otherwise(
    governed: Path,
) -> None:
    """The milestone's claim, tested where a client would meet it.

    A report is deserialized into ``GovernanceReport``, whose ``decision`` is a
    computed field. The ``"decision"`` a JSON file carries is never read, so
    editing it — by hand, by a script, or by a model that had been given a way
    to write — changes nothing about what an MCP client is told.
    """
    blocked(governed)
    damage(
        governed / "BIO-001" / "governance" / "governance-report.json",
        lambda document: {**document, "decision": "ready"},
    )

    async with Client(build_server(governed)) as client:
        report = payload(await client.call_tool("get_governance_report", {"study_id": "BIO-001"}))
        explanation = payload(await client.call_tool("why_not_ready", {"study_id": "BIO-001"}))

    assert report["decision"] == "blocked"
    assert explanation["decision"] == "blocked"


async def test_get_quality_report_returns_the_evidence_the_run_produced(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        report = payload(await client.call_tool("get_quality_report", {"study_id": "BIO-001"}))

    assert report["study_id"] == "BIO-001"
    assert report["overall_status"] == "pass"
    assert len(report["checks"]) == 6
    assert {check["status"] for check in report["checks"]} == {"pass"}


async def test_get_contract_results_returns_both_structured_results(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        results = payload(await client.call_tool("get_contract_results", {"study_id": "BIO-001"}))

    assert results["study_id"] == "BIO-001"
    assert results["samples"]["contract_id"] == "bio.samples"
    assert results["compounds"]["contract_id"] == "bio.compounds"
    assert results["samples"]["passed"] is True
    assert results["compounds"]["passed"] is True
    assert results["samples"]["rows_checked"] == 20


async def test_get_lineage_summary_returns_the_run_the_job_and_the_datasets(
    governed: Path,
) -> None:
    async with Client(build_server(governed)) as client:
        summary = payload(await client.call_tool("get_lineage_summary", {"study_id": "BIO-001"}))

    assert summary["job_namespace"] == "bio-governance-lab"
    assert summary["job_name"] == "curate-study"
    assert summary["event_types"] == ["START", "COMPLETE"]
    assert summary["complete"] is True
    assert summary["run_id"]
    assert summary["inputs"] == [
        "bio://BIO-001/raw/compounds",
        "bio://BIO-001/raw/expression",
        "bio://BIO-001/raw/samples",
    ]
    assert "bio://BIO-001/curated/samples" in summary["outputs"]


# --- why_not_ready, over all three decisions ------------------------------


async def test_why_not_ready_reports_no_blockers_for_a_ready_study(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        explanation = payload(await client.call_tool("why_not_ready", {"study_id": "BIO-001"}))

    assert explanation["decision"] == "ready"
    assert explanation["blocking"] == []
    assert explanation["review"] == []
    assert "all 5 governance checks passed" in explanation["summary"]


async def test_why_not_ready_returns_the_failed_checks_for_a_blocked_study(
    governed: Path,
) -> None:
    blocked(governed)

    async with Client(build_server(governed)) as client:
        explanation = payload(await client.call_tool("why_not_ready", {"study_id": "BIO-001"}))

    assert explanation["decision"] == "blocked"
    assert [check["check_id"] for check in explanation["blocking"]] == ["lineage_evidence"]
    assert [check["status"] for check in explanation["blocking"]] == ["fail"]
    assert explanation["review"] == []
    assert "lineage_evidence" in explanation["summary"]


async def test_why_not_ready_returns_the_warning_checks_for_a_study_under_review(
    governed: Path,
) -> None:
    under_review(governed)

    async with Client(build_server(governed)) as client:
        explanation = payload(await client.call_tool("why_not_ready", {"study_id": "BIO-001"}))

    assert explanation["decision"] == "review"
    assert explanation["blocking"] == []
    assert [check["check_id"] for check in explanation["review"]] == ["data_quality"]
    assert [check["status"] for check in explanation["review"]] == ["warn"]


# --- missing evidence, and the results-root boundary ----------------------


async def test_an_unknown_study_gives_a_useful_error_not_a_traceback(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_governance_report", {"study_id": "BIO-404"})

    assert result.is_error
    assert "unknown study 'BIO-404'" in text(result)
    assert "Traceback" not in text(result)


async def test_a_missing_report_names_what_is_missing(governed: Path) -> None:
    (governed / "BIO-001" / "governance" / "governance-report.json").unlink()

    async with Client(build_server(governed)) as client:
        result = await client.call_tool("why_not_ready", {"study_id": "BIO-001"})

    assert result.is_error
    assert "governance report for BIO-001 is missing" in text(result)


async def test_malformed_evidence_gives_a_useful_error(governed: Path) -> None:
    (governed / "BIO-001" / "quality" / "dq-report.json").write_text("{not json", encoding="utf-8")

    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_quality_report", {"study_id": "BIO-001"})

    assert result.is_error
    assert "not valid JSON" in text(result)


async def test_evidence_that_is_valid_json_but_the_wrong_shape_is_reported_as_such(
    governed: Path,
) -> None:
    damage(
        governed / "BIO-001" / "quality" / "dq-report.json",
        lambda document: {"study_id": "BIO-001", "checks": "not a list of checks"},
    )

    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_quality_report", {"study_id": "BIO-001"})

    assert result.is_error
    assert "is not a QualityReport" in text(result)


async def test_lineage_that_is_not_one_run_cannot_be_summarised(governed: Path) -> None:
    """And the message points at the tool that does carry a verdict on it."""
    path = governed / "BIO-001" / "lineage" / "openlineage.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[1]["run"]["runId"] = "11111111-2222-3333-4444-555555555555"
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_lineage_summary", {"study_id": "BIO-001"})

    assert result.is_error
    assert "does not describe one curation run" in text(result)
    assert "get_governance_report" in text(result)


@pytest.mark.parametrize(
    "study_id",
    ["../BIO-001", "../../etc", "/etc/passwd", "BIO-001/../../data", "..", ".", "a/b"],
)
async def test_a_path_traversal_attempt_is_rejected(governed: Path, study_id: str) -> None:
    """No study identifier can name anything outside the results root.

    The identifier is validated as an ``AssetIdentifier`` domain before it is
    joined to a path at all, so traversal, absolute paths and multi-segment
    names are refused by the identifier convention rather than by a filter over
    strings.
    """
    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_governance_report", {"study_id": study_id})

    assert result.is_error
    assert "not a study identifier" in text(result)


async def test_a_symlink_out_of_the_results_root_is_refused(governed: Path, tmp_path: Path) -> None:
    """The identifier check is not the only line: the resolved path is confined too."""
    outside = tmp_path / "elsewhere" / "BIO-999"
    (outside / "governance").mkdir(parents=True)
    (governed / "BIO-999").symlink_to(outside, target_is_directory=True)

    async with Client(build_server(governed)) as client:
        result = await client.call_tool("get_governance_report", {"study_id": "BIO-999"})

    assert result.is_error
    assert "outside the results root" in text(result)


async def test_a_missing_results_root_is_reported_rather_than_crashing(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path / "nowhere")) as client:
        result = await client.call_tool("list_studies", {})

    assert result.is_error
    assert "results root not found" in text(result)


# --- resources ------------------------------------------------------------


async def test_the_resource_templates_are_listed(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        templates = (await client.list_resource_templates()).resource_templates

    assert {template.uri_template for template in templates} == {
        "governance://studies/{study_id}/report",
        "quality://studies/{study_id}/report",
    }
    assert all(template.mime_type == "application/json" for template in templates)


async def test_a_governance_report_can_be_read_as_a_resource(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        contents = (await client.read_resource("governance://studies/BIO-001/report")).contents

    document = json.loads(contents[0].text)
    assert document["study_id"] == "BIO-001"
    assert document["decision"] == "ready"


async def test_a_quality_report_can_be_read_as_a_resource(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        contents = (await client.read_resource("quality://studies/BIO-001/report")).contents

    document = json.loads(contents[0].text)
    assert document["study_id"] == "BIO-001"
    assert document["overall_status"] == "pass"


async def test_a_resource_for_an_unknown_study_fails_with_a_message(governed: Path) -> None:
    async with Client(build_server(governed)) as client:
        with pytest.raises(Exception, match="unknown study 'BIO-404'"):
            await client.read_resource("governance://studies/BIO-404/report")
