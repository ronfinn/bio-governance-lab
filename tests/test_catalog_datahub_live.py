"""The live demonstration against a local DataHub instance.

Skipped unless ``DATAHUB_INTEGRATION_TEST=1``, so CI never needs a server and
never starts one. Run it against a local Docker quickstart with::

    DATAHUB_INTEGRATION_TEST=1 uv run pytest tests/test_catalog_datahub_live.py

What it proves is the half the mocked tests cannot: that DataHub accepts the
Metadata Change Proposals as sent, that publishing twice leaves one set of
datasets behind, and that the lineage comes back out of the SDK rather than only
looking right in the UI.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from bio_governance.catalog import (
    CANONICAL_PROPERTY,
    dataset_urn,
    lineage_edges,
    study_identifiers,
)
from bio_governance.catalog.datahub_client import DataHubClient
from bio_governance.catalog.datahub_publish import publish_study_to_datahub
from bio_governance.catalog.models import DataHubConfig
from bio_governance.models import AssetIdentifier

INTEGRATION_VAR = "DATAHUB_INTEGRATION_TEST"

pytestmark = pytest.mark.skipif(
    os.environ.get(INTEGRATION_VAR) != "1",
    reason=f"set {INTEGRATION_VAR}=1 and a local DataHub to run the live demonstration",
)


@pytest.fixture
def client() -> Iterator[DataHubClient]:
    with DataHubClient(DataHubConfig.from_env()) as live_client:
        yield live_client


def test_the_server_is_reachable(client: DataHubClient) -> None:
    assert client.server_version()


def test_publishing_twice_leaves_seven_datasets_and_six_edges(
    client: DataHubClient, study_files: tuple[Path, Path]
) -> None:
    raw, results = study_files

    first = publish_study_to_datahub(client, raw, results)
    second = publish_study_to_datahub(client, raw, results)

    assert first.assets == second.assets
    assert first.edges == second.edges

    identifiers = study_identifiers(first.study_id)
    for identifier in identifiers:
        properties = client.get_dataset_properties(dataset_urn(identifier))
        assert properties is not None, f"DataHub holds nothing for {identifier.uri}"
        # The canonical identity survives the round trip through the catalogue.
        assert properties.qualifiedName == identifier.uri
        assert (properties.customProperties or {})[CANONICAL_PROPERTY] == identifier.uri

    published = {
        (dataset_urn(AssetIdentifier.parse(edge.from_identifier)), dataset_urn(target))
        for edge in lineage_edges(first.study_id)
        for target in [AssetIdentifier.parse(edge.to_identifier)]
    }
    retrieved = {
        (upstream, dataset_urn(identifier))
        for identifier in identifiers
        for upstream in client.get_upstreams(dataset_urn(identifier))
    }

    assert retrieved == published
    assert len(retrieved) == 6
