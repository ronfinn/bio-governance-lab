"""The live demonstration against a local OpenMetadata instance.

Skipped unless ``OPENMETADATA_INTEGRATION_TEST=1``, so CI never needs a server
and never starts one. Run it against a local Docker deployment with::

    export OPENMETADATA_JWT_TOKEN=...          # see docs/openmetadata.md
    OPENMETADATA_INTEGRATION_TEST=1 uv run pytest tests/test_catalog_live.py

What it proves is the half the mocked tests cannot: that OpenMetadata accepts
the entities as sent, that publishing twice leaves one set behind, that each
container comes back carrying its classification tag and no owner, that the
lineage comes back out of the API rather than only looking right in the UI, and
that a changed declaration reclassifies every container without disturbing a
tag somebody else put there.

The entities are the deterministic BIO-001 ones, as in every other run of this
file, and the lifecycle test leaves them as the committed declaration describes
them: classified ``internal``, with nothing else attached.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from bio_governance.catalog import (
    CLASSIFICATION_NAME,
    OpenMetadataClient,
    OpenMetadataConfig,
    classification_tag_fqn,
    fully_qualified_name,
    lineage_edges,
    publish_study,
    study_identifiers,
)
from bio_governance.cli import app
from bio_governance.models import Classification
from conftest import declare_classification, validate_declaration

INTEGRATION_VAR = "OPENMETADATA_INTEGRATION_TEST"

pytestmark = pytest.mark.skipif(
    os.environ.get(INTEGRATION_VAR) != "1",
    reason=f"set {INTEGRATION_VAR}=1 and a local OpenMetadata to run the live demonstration",
)

runner = CliRunner()

#: A tag of the system PII classification every OpenMetadata ships with. It
#: stands for a tag a steward adds in the UI, which publication must not touch.
STEWARD_TAG = "PII.NonSensitive"

#: The classification changes the lifecycle test walks through, in order. The
#: study starts with no project classification at all.
TRANSITIONS = (
    Classification.INTERNAL,  # none -> internal
    Classification.INTERNAL,  # internal -> internal
    Classification.CONFIDENTIAL,  # internal -> confidential
    Classification.RESTRICTED,  # confidential -> restricted
    Classification.PUBLIC,  # restricted -> public
)


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
    validate_declaration(raw, results)
    return raw, results


def retrieved_edges(client: OpenMetadataClient, study_id: str) -> set[tuple[str, str]]:
    """The lineage edges OpenMetadata holds among a study's containers, as bio:// pairs."""
    identifiers = study_identifiers(study_id)
    fqns = {fully_qualified_name(identifier): identifier.uri for identifier in identifiers}
    retrieved = set()
    by_id = {}
    for identifier in identifiers:
        graph = client.get_lineage(fully_qualified_name(identifier))
        by_id[str(graph["entity"]["id"])] = identifier.uri
        for node in graph.get("nodes", []):
            uri = fqns.get(str(node.get("fullyQualifiedName")))
            if uri is not None:
                by_id[str(node["id"])] = uri
        for edge in graph.get("downstreamEdges", []) + graph.get("upstreamEdges", []):
            retrieved.add((str(edge["fromEntity"]), str(edge["toEntity"])))
    return {(by_id[source], by_id[target]) for source, target in retrieved}


def read_back(client: OpenMetadataClient, study_id: str) -> dict[str, dict[str, Any]]:
    """What the catalogue holds for each container: version, identity, tags and owners."""
    state = {}
    for identifier in study_identifiers(study_id):
        container = client.get_container(fully_qualified_name(identifier))
        state[identifier.uri] = {
            "version": container["version"],
            "fullPath": container.get("fullPath"),
            "tags": sorted(tag["tagFQN"] for tag in container.get("tags") or []),
            "owners": container["owners"],
        }
    return state


def steward_sets_tags(client: OpenMetadataClient, fqn: str, tag_fqns: list[str]) -> None:
    """Replace a container's tags the way somebody editing it in the UI would.

    Test-only, and deliberately not a client method: publication never removes
    a tag outside its own classification, and nothing in ``src`` should be able
    to. The UI's own mechanism is a JSON Patch of ``/tags``.
    """
    container = client.get_container(fqn)
    config = OpenMetadataConfig.from_env()
    response = httpx.patch(
        f"{config.host}/v1/containers/{container['id']}",
        headers={
            "Authorization": f"Bearer {config.require_token()}",
            "Content-Type": "application/json-patch+json",
        },
        json=[
            {
                "op": "add",
                "path": "/tags",
                "value": [
                    {
                        "tagFQN": tag_fqn,
                        "source": "Classification",
                        "labelType": "Manual",
                        "state": "Confirmed",
                    }
                    for tag_fqn in tag_fqns
                ],
            }
        ],
        timeout=30,
    )
    assert response.status_code == 200, response.text


def test_the_server_is_reachable(client: OpenMetadataClient) -> None:
    assert client.version()


def test_publishing_twice_leaves_seven_assets_and_six_edges(
    client: OpenMetadataClient, study: tuple[Path, Path]
) -> None:
    raw, results = study

    first = publish_study(client, raw, results)
    after_first = read_back(client, first.study_id)
    second = publish_study(client, raw, results)

    assert first.assets == second.assets
    assert first.edges == second.edges
    # The second publication changed nothing the catalogue holds, versions included.
    assert read_back(client, first.study_id) == after_first

    for uri, container in after_first.items():
        # The canonical identity survives the round trip through the catalogue.
        assert container["fullPath"] == uri
        # So does the classification; ownership was never sent, and the
        # server, asked for owners, says there are none.
        assert classification_tag_fqn(Classification.INTERNAL) in container["tags"]
        assert container["owners"] == []

    published = {
        (edge.from_identifier, edge.to_identifier) for edge in lineage_edges(first.study_id)
    }
    assert retrieved_edges(client, first.study_id) == published


def test_a_changed_declaration_reclassifies_and_keeps_a_steward_tag(
    client: OpenMetadataClient, study: tuple[Path, Path], tmp_path: Path
) -> None:
    """none -> internal -> internal -> confidential -> restricted -> public.

    Before the walk every container is left as milestone 7 left it — no project
    classification — except that a steward has tagged it ``PII.NonSensitive``.
    After each publication every container holds exactly the declared value of
    the project's classification, never the previous one, and still holds the
    steward's tag; a second publication of the same declaration changes
    nothing. The six edges and the seven containers survive the whole walk.
    """
    raw, results = study
    study_id = raw.name
    fqns = [fully_qualified_name(identifier) for identifier in study_identifiers(study_id)]
    publish_study(client, raw, results)
    for fqn in fqns:
        steward_sets_tags(client, fqn, [STEWARD_TAG])

    try:
        previous: Classification | None = None
        for classification in TRANSITIONS:
            declare_classification(raw, results, tmp_path, classification)
            publish_study(client, raw, results)
            state = read_back(client, study_id)

            for uri, container in state.items():
                project = [tag for tag in container["tags"] if tag.startswith(CLASSIFICATION_NAME)]
                assert project == [classification_tag_fqn(classification)], (uri, previous)
                assert STEWARD_TAG in container["tags"], (uri, classification)
                assert container["owners"] == []
                assert container["fullPath"] == uri

            publish_study(client, raw, results)
            assert read_back(client, study_id) == state, f"republishing {classification} moved"
            previous = classification

        assert len(state) == 7
        published = {(edge.from_identifier, edge.to_identifier) for edge in lineage_edges(study_id)}
        assert retrieved_edges(client, study_id) == published
    finally:
        # Back to what the committed declaration says, and nothing else.
        validate_declaration(raw, results)
        publish_study(client, raw, results)
        for fqn in fqns:
            steward_sets_tags(client, fqn, [classification_tag_fqn(Classification.INTERNAL)])
