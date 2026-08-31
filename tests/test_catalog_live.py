"""The live demonstration against a local OpenMetadata instance.

Skipped unless ``OPENMETADATA_INTEGRATION_TEST=1``, so CI never needs a server
and never starts one. Run it against a local Docker deployment with::

    export OPENMETADATA_JWT_TOKEN=...          # see docs/openmetadata.md
    OPENMETADATA_INTEGRATION_TEST=1 uv run pytest tests/test_catalog_live.py

What it proves is the half the mocked tests cannot: that OpenMetadata accepts
the entities as sent, that publishing twice leaves one set behind, and that the
lineage comes back out of the API rather than only looking right in the UI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bio_governance.catalog import (
    OpenMetadataClient,
    OpenMetadataConfig,
    fully_qualified_name,
    lineage_edges,
    publish_study,
    study_identifiers,
)
from bio_governance.cli import app

INTEGRATION_VAR = "OPENMETADATA_INTEGRATION_TEST"

pytestmark = pytest.mark.skipif(
    os.environ.get(INTEGRATION_VAR) != "1",
    reason=f"set {INTEGRATION_VAR}=1 and a local OpenMetadata to run the live demonstration",
)

runner = CliRunner()


@pytest.fixture
def client() -> Iterator[OpenMetadataClient]:
    config = OpenMetadataConfig.from_env()
    with OpenMetadataClient(config) as open_client:
        yield open_client


@pytest.fixture
def study(tmp_path: Path) -> tuple[Path, Path]:
    """A generated study and pipeline-shaped results, built under tmp_path."""
    assert runner.invoke(app, ["demo", "generate", "--output", str(tmp_path)]).exit_code == 0
    raw = tmp_path / "BIO-001"

    results = tmp_path / "results"
    curated = results / "curated"
    curated.mkdir(parents=True)
    for name in ("samples.csv", "compounds.csv", "expression.csv"):
        (curated / name).write_bytes((raw / name).read_bytes())

    report = results / "quality" / "dq-report.json"
    assert runner.invoke(app, ["dq", "run", str(raw), "--json-out", str(report)]).exit_code == 0
    return raw, results


def test_the_server_is_reachable(client: OpenMetadataClient) -> None:
    assert client.version()


def test_publishing_twice_leaves_seven_assets_and_six_edges(
    client: OpenMetadataClient, study: tuple[Path, Path]
) -> None:
    raw, results = study

    first = publish_study(client, raw, results)
    second = publish_study(client, raw, results)

    assert first.assets == second.assets
    assert first.edges == second.edges

    identifiers = study_identifiers(first.study_id)
    for identifier in identifiers:
        container = client.get_container(fully_qualified_name(identifier))
        # The canonical identity survives the round trip through the catalogue.
        assert container["fullPath"] == identifier.uri

    published = {
        (edge.from_identifier, edge.to_identifier) for edge in lineage_edges(first.study_id)
    }
    retrieved = set()
    by_id = {}
    for identifier in identifiers:
        graph = client.get_lineage(fully_qualified_name(identifier))
        by_id[str(graph["entity"]["id"])] = identifier.uri
        for node in graph.get("nodes", []):
            fqns = {fully_qualified_name(candidate): candidate.uri for candidate in identifiers}
            uri = fqns.get(str(node.get("fullyQualifiedName")))
            if uri is not None:
                by_id[str(node["id"])] = uri
        for edge in graph.get("downstreamEdges", []) + graph.get("upstreamEdges", []):
            retrieved.add((str(edge["fromEntity"]), str(edge["toEntity"])))

    assert {(by_id[source], by_id[target]) for source, target in retrieved} == published
