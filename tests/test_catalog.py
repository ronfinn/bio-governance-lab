"""Tests for publishing governed assets to OpenMetadata.

None of these need a server. OpenMetadata is a Docker deployment that CI has no
business starting, so the HTTP layer is mocked and what is asserted is the thing
this project actually controls: which entities are prepared, what identity they
carry, which edges are published, and that publishing twice sends the same
create-or-update requests rather than a second set of creates.

The live demonstration lives in ``tests/test_catalog_live.py``, which skips
unless a local instance is explicitly opted into.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from bio_governance.catalog import (
    DEFAULT_HOST,
    HOST_VAR,
    SERVICE_NAME,
    SERVICE_TYPE,
    TOKEN_VAR,
    CatalogError,
    FileFormat,
    OpenMetadataClient,
    OpenMetadataConfig,
    entity_name,
    fully_qualified_name,
    lineage_edges,
    prepare_assets,
    publish_study,
)
from bio_governance.cli import app
from bio_governance.models import AssetIdentifier

runner = CliRunner()

#: A JWT-shaped string. Never a real token: the point of several tests below
#: is that whatever is configured stays out of the output.
TOKEN = "header.payload.signature"

RAW = (
    "bio://BIO-001/raw/samples",
    "bio://BIO-001/raw/compounds",
    "bio://BIO-001/raw/expression",
)
CURATED = (
    "bio://BIO-001/curated/samples",
    "bio://BIO-001/curated/compounds",
    "bio://BIO-001/curated/expression",
)
REPORT = "bio://BIO-001/quality/dq-report"

#: Every edge the project claims, and no others: each raw file to the curated
#: copy made from it, and all three raw files to the report that judged them.
EXPECTED_EDGES = {
    (RAW[0], CURATED[0]),
    (RAW[1], CURATED[1]),
    (RAW[2], CURATED[2]),
    (RAW[0], REPORT),
    (RAW[1], REPORT),
    (RAW[2], REPORT),
}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_config_defaults_to_the_local_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HOST_VAR, raising=False)
    monkeypatch.delenv(TOKEN_VAR, raising=False)

    config = OpenMetadataConfig.from_env()

    assert config.host == DEFAULT_HOST == "http://localhost:8585/api"
    assert config.token is None


def test_config_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOST_VAR, "http://om.example.org:8585/api/")
    monkeypatch.setenv(TOKEN_VAR, TOKEN)

    config = OpenMetadataConfig.from_env()

    assert config.host == "http://om.example.org:8585/api"
    assert config.token == TOKEN


def test_missing_token_is_a_clear_error_naming_the_variable() -> None:
    config = OpenMetadataConfig()

    with pytest.raises(CatalogError) as error:
        config.require_token()

    assert TOKEN_VAR in str(error.value)


def test_token_is_never_reported_in_full() -> None:
    hint = OpenMetadataConfig(token=TOKEN).token_hint

    assert TOKEN not in hint
    assert "signature" not in hint
    assert hint.endswith("ture)")


@respx.mock
def test_a_write_without_a_token_fails_before_the_request(study_files: tuple[Path, Path]) -> None:
    route = respx.put(f"{DEFAULT_HOST}/v1/services/storageServices")
    raw, results = study_files

    with OpenMetadataClient(OpenMetadataConfig()) as client, pytest.raises(CatalogError) as error:
        publish_study(client, raw, results)

    assert TOKEN_VAR in str(error.value)
    assert not route.called


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("bio://BIO-001/raw/samples", "BIO-001_raw_samples"),
        ("bio://BIO-001/curated/expression", "BIO-001_curated_expression"),
        ("bio://BIO-001/quality/dq-report", "BIO-001_quality_dq-report"),
    ],
)
def test_entity_name_is_derived_deterministically(uri: str, expected: str) -> None:
    identifier = AssetIdentifier.parse(uri)

    assert entity_name(identifier) == expected
    assert entity_name(identifier) == entity_name(AssetIdentifier.parse(uri))
    assert fully_qualified_name(identifier) == f"{SERVICE_NAME}.{expected}"


def test_a_study_prepares_exactly_the_seven_governed_assets() -> None:
    assets = prepare_assets("BIO-001")

    assert len(assets) == 7
    assert tuple(asset.identifier for asset in assets) == (*RAW, *CURATED, REPORT)


def test_every_asset_keeps_its_canonical_identifier() -> None:
    for asset in prepare_assets("BIO-001"):
        # The entity name is a derivation; the bio:// URI is the identity, and
        # it is what the container's fullPath will carry.
        assert asset.identifier.startswith("bio://BIO-001/")
        assert asset.name == entity_name(AssetIdentifier.parse(asset.identifier))


def test_tabular_assets_are_csv_and_the_report_is_json() -> None:
    formats = {asset.identifier: asset.file_format for asset in prepare_assets("BIO-001")}

    for uri in (*RAW, *CURATED):
        assert formats[uri] is FileFormat.CSV
    assert formats[REPORT] is FileFormat.JSON


def test_contract_columns_become_the_container_data_model() -> None:
    from bio_governance.contracts import load_contract

    contracts = {"samples": load_contract(Path("contracts/samples.v1.yaml"))}
    assets = {a.identifier: a for a in prepare_assets("BIO-001", contracts=contracts)}

    columns = assets["bio://BIO-001/raw/samples"].columns
    assert [column.name for column in columns] == [
        "sample_id",
        "study_id",
        "compound_id",
        "treatment",
        "dose",
        "dose_unit",
        "tissue",
        "replicate",
    ]
    types = {column.name: column.data_type for column in columns}
    assert types["sample_id"] == "STRING"
    assert types["dose"] == "DOUBLE"
    assert types["replicate"] == "INT"
    # The wide generated matrix has no contract, and gets no invented columns.
    assert assets["bio://BIO-001/raw/expression"].columns == ()


def test_only_the_explainable_lineage_edges_are_published() -> None:
    edges = lineage_edges("BIO-001")

    assert len(edges) == 6
    assert {(edge.from_identifier, edge.to_identifier) for edge in edges} == EXPECTED_EDGES


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


@pytest.fixture
def study_files(tmp_path: Path) -> tuple[Path, Path]:
    """A generated study and the results directory the pipeline would leave."""
    result = runner.invoke(app, ["demo", "generate", "--output", str(tmp_path / "data")])
    assert result.exit_code == 0, result.output
    raw = tmp_path / "data" / "BIO-001"

    results = tmp_path / "results" / "BIO-001"
    curated = results / "curated"
    curated.mkdir(parents=True)
    for name in ("samples.csv", "compounds.csv", "expression.csv"):
        (curated / name).write_bytes((raw / name).read_bytes())

    report = results / "quality" / "dq-report.json"
    dq = runner.invoke(app, ["dq", "run", str(raw), "--json-out", str(report)])
    assert dq.exit_code == 0, dq.output

    events = results / "lineage" / "openlineage.jsonl"
    emitted = runner.invoke(
        app,
        [
            "lineage",
            "emit",
            str(raw),
            str(curated),
            "--output",
            str(events),
            "--quality-report",
            str(report),
            "--run-id",
            "11111111-2222-3333-4444-555555555555",
        ],
    )
    assert emitted.exit_code == 0, emitted.output
    return raw, results


class FakeOpenMetadata:
    """A stand-in server that records every request and never forgets an entity.

    Entities are keyed the way OpenMetadata keys them — services by name,
    containers by fully qualified name, edges by their endpoints — so a second
    publication that creates duplicates would show up here as a second entry
    rather than as an overwrite.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.edges: list[tuple[str, str]] = []

    def install(self, router: respx.Router) -> None:
        router.get(f"{DEFAULT_HOST}/v1/system/version").mock(
            side_effect=lambda request: self._record(request, {"version": "1.13.4"})
        )
        router.put(f"{DEFAULT_HOST}/v1/services/storageServices").mock(side_effect=self._service)
        router.put(f"{DEFAULT_HOST}/v1/containers").mock(side_effect=self._container)
        router.put(f"{DEFAULT_HOST}/v1/lineage").mock(side_effect=self._lineage)

    @property
    def ids(self) -> dict[str, str]:
        """Container entity IDs by the bio:// identifier each carries."""
        return {body["fullPath"]: body["id"] for body in self.containers.values()}

    @property
    def published_edges(self) -> set[tuple[str, str]]:
        """The edges, translated back from entity IDs to bio:// identifiers."""
        uris = {entity_id: uri for uri, entity_id in self.ids.items()}
        return {(uris[source], uris[target]) for source, target in self.edges}

    def _record(self, request: httpx.Request, payload: dict[str, Any]) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    def _service(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        body["fullyQualifiedName"] = body["name"]
        self.services[body["name"]] = body
        return self._record(request, body)

    def _container(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fqn = f"{body['service']}.{body['name']}"
        body["fullyQualifiedName"] = fqn
        body["id"] = self.containers.get(fqn, {}).get("id") or f"id-{body['name']}"
        self.containers[fqn] = body
        return self._record(request, body)

    def _lineage(self, request: httpx.Request) -> httpx.Response:
        edge = json.loads(request.content)["edge"]
        self.edges.append((edge["fromEntity"]["id"], edge["toEntity"]["id"]))
        return self._record(request, {})


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> FakeOpenMetadata:
    monkeypatch.setenv(TOKEN_VAR, TOKEN)
    monkeypatch.delenv(HOST_VAR, raising=False)
    return FakeOpenMetadata()


def test_publication_upserts_the_service_the_assets_and_the_edges(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    with respx.mock as router:
        catalog.install(router)
        with OpenMetadataClient(OpenMetadataConfig.from_env()) as client:
            published = publish_study(client, raw, results)

    assert published.study_id == "BIO-001"
    assert catalog.services[SERVICE_NAME]["serviceType"] == SERVICE_TYPE
    assert len(catalog.containers) == 7
    assert catalog.published_edges == EXPECTED_EDGES
    assert published.lineage_run_id == "11111111-2222-3333-4444-555555555555"


def test_published_containers_carry_the_bio_identifier_as_full_path(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    with respx.mock as router:
        catalog.install(router)
        with OpenMetadataClient(OpenMetadataConfig.from_env()) as client:
            publish_study(client, raw, results)

    assert set(catalog.ids) == {*RAW, *CURATED, REPORT}
    for fqn, body in catalog.containers.items():
        assert fqn == f"{SERVICE_NAME}.{body['name']}"
        assert body["fullPath"].startswith("bio://BIO-001/")
    formats = {body["fullPath"]: body["fileFormats"] for body in catalog.containers.values()}
    assert formats[RAW[0]] == ["csv"]
    assert formats[REPORT] == ["json"]


def test_publishing_twice_is_idempotent(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    with respx.mock as router:
        catalog.install(router)
        with OpenMetadataClient(OpenMetadataConfig.from_env()) as client:
            publish_study(client, raw, results)
            first = list(catalog.requests)
            publish_study(client, raw, results)

    # Idempotence is a property of the requests: every write is a create-or-
    # update PUT, so the second run addresses the same entities as the first.
    assert {method for method, _ in catalog.requests} <= {"GET", "PUT"}
    assert catalog.requests[len(first) :] == first
    assert len(catalog.containers) == 7
    assert catalog.published_edges == EXPECTED_EDGES


def test_a_missing_curated_file_stops_publication_before_any_request(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files
    (results / "curated" / "expression.csv").unlink()

    with respx.mock as router:
        catalog.install(router)
        with (
            OpenMetadataClient(OpenMetadataConfig.from_env()) as client,
            pytest.raises(CatalogError) as error,
        ):
            publish_study(client, raw, results)

    assert "expression.csv" in str(error.value)
    assert catalog.requests == []


# --------------------------------------------------------------------------
# HTTP failures
# --------------------------------------------------------------------------


@respx.mock
def test_an_unreachable_server_names_the_host() -> None:
    respx.get(f"{DEFAULT_HOST}/v1/system/version").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with OpenMetadataClient(OpenMetadataConfig()) as client, pytest.raises(CatalogError) as error:
        client.version()

    assert "http://localhost:8585/api" in str(error.value)
    assert "Connection refused" in str(error.value)


@respx.mock
def test_a_rejected_token_says_so_without_printing_it() -> None:
    respx.put(f"{DEFAULT_HOST}/v1/containers").mock(return_value=httpx.Response(401))

    client = OpenMetadataClient(OpenMetadataConfig(token=TOKEN))
    with pytest.raises(CatalogError) as error:
        client.upsert_container(prepare_assets("BIO-001")[0], service=SERVICE_NAME)

    assert "401" in str(error.value)
    assert TOKEN not in str(error.value)


@respx.mock
def test_a_server_error_reports_what_the_server_said() -> None:
    respx.put(f"{DEFAULT_HOST}/v1/containers").mock(
        return_value=httpx.Response(500, json={"message": "storage service not found"})
    )

    client = OpenMetadataClient(OpenMetadataConfig(token=TOKEN))
    with pytest.raises(CatalogError) as error:
        client.upsert_container(prepare_assets("BIO-001")[0], service=SERVICE_NAME)

    assert "500" in str(error.value)
    assert "storage service not found" in str(error.value)


@respx.mock
def test_a_non_openmetadata_server_is_recognised() -> None:
    respx.get(f"{DEFAULT_HOST}/v1/system/version").mock(
        return_value=httpx.Response(200, json={"hello": "world"})
    )

    with OpenMetadataClient(OpenMetadataConfig()) as client, pytest.raises(CatalogError) as error:
        client.version()

    assert "not an OpenMetadata server" in str(error.value)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_health_reports_the_version_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VAR, TOKEN)
    monkeypatch.delenv(HOST_VAR, raising=False)

    with respx.mock as router:
        router.get(f"{DEFAULT_HOST}/v1/system/version").mock(
            return_value=httpx.Response(200, json={"version": "1.13.4"})
        )
        result = runner.invoke(app, ["catalog", "openmetadata", "health"])

    assert result.exit_code == 0, result.output
    assert "1.13.4" in result.output
    assert TOKEN not in result.output


def test_cli_health_exits_2_when_the_server_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.delenv(HOST_VAR, raising=False)

    with respx.mock as router:
        router.get(f"{DEFAULT_HOST}/v1/system/version").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        result = runner.invoke(app, ["catalog", "openmetadata", "health"])

    assert result.exit_code == 2
    assert "Token: not set" in result.output


def test_cli_publish_prints_seven_assets_and_six_edges(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    with respx.mock as router:
        catalog.install(router)
        result = runner.invoke(app, ["catalog", "openmetadata", "publish", str(raw), str(results)])

    assert result.exit_code == 0, result.output
    assert "7 assets" in result.output
    assert "6 lineage edges" in result.output
    assert "BIO-001_quality_dq-report" in result.output
    assert "bio://BIO-001/raw/samples" in result.output


def test_cli_get_reads_the_published_assets_back(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    with respx.mock as router:
        catalog.install(router)
        published = runner.invoke(
            app, ["catalog", "openmetadata", "publish", str(raw), str(results)]
        )
        assert published.exit_code == 0, published.output

        for fqn, body in catalog.containers.items():
            router.get(f"{DEFAULT_HOST}/v1/containers/name/{fqn}").mock(
                return_value=httpx.Response(200, json=body)
            )
        root = catalog.containers[f"{SERVICE_NAME}.BIO-001_raw_samples"]
        router.get(f"{DEFAULT_HOST}/v1/lineage/container/name/{root['fullyQualifiedName']}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "entity": {"id": root["id"]},
                    "nodes": [
                        {"id": body["id"], "fullyQualifiedName": fqn}
                        for fqn, body in catalog.containers.items()
                    ],
                    "downstreamEdges": [
                        {"fromEntity": source, "toEntity": target}
                        for source, target in catalog.edges
                        if source == root["id"]
                    ],
                },
            )
        )
        result = runner.invoke(app, ["catalog", "openmetadata", "get", "BIO-001"])

    assert result.exit_code == 0, result.output
    assert "Assets: 7" in result.output
    assert "bio://BIO-001/raw/samples" in result.output
    assert f"{SERVICE_NAME}.BIO-001_curated_samples" in result.output
    assert f"{SERVICE_NAME}.BIO-001_quality_dq-report" in result.output
