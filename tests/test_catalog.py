"""Tests for publishing governed assets to OpenMetadata.

None of these need a server. OpenMetadata is a Docker deployment that CI has no
business starting, so the HTTP layer is mocked and what is asserted is the thing
this project actually controls: which entities are prepared, what identity they
carry, which edges are published, that publishing twice sends the same
create-or-update requests rather than a second set of creates, and that a
changed declaration replaces the project's classification on every container
while leaving every other tag alone.

The live demonstration lives in ``tests/test_catalog_live.py``, which skips
unless a local instance is explicitly opted into.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from bio_governance.catalog import (
    CLASSIFICATION_NAME,
    DEFAULT_HOST,
    HOST_VAR,
    SERVICE_NAME,
    SERVICE_TYPE,
    TOKEN_VAR,
    CatalogError,
    FileFormat,
    OpenMetadataClient,
    OpenMetadataConfig,
    classification_tag_fqn,
    classification_tag_label,
    classified_tags,
    entity_name,
    fully_qualified_name,
    lineage_edges,
    prepare_assets,
    publish_study,
)
from bio_governance.cli import app
from bio_governance.models import AssetIdentifier, Classification
from conftest import (
    GOVERNANCE_DIR,
    REFUSALS,
    damage_governance_evidence,
    declare_classification,
    validate_declaration,
)

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


#: A tag of a classification every OpenMetadata ships with, as the server
#: returns it once a steward has applied it in the UI. It stands for everything
#: on a container that this project does not own.
STEWARD_TAG = {
    "tagFQN": "PII.NonSensitive",
    "name": "NonSensitive",
    "source": "Classification",
    "labelType": "Manual",
    "state": "Confirmed",
    "appliedBy": "a-steward",
}


class FakeOpenMetadata:
    """A stand-in server that records every request and never forgets an entity.

    Entities are keyed the way OpenMetadata keys them — services and
    classifications by name, tags and containers by fully qualified name, edges
    by their endpoints — so a second publication that creates duplicates would
    show up here as a second entry rather than as an overwrite. Like the real
    server, it refuses a container carrying a tag that does not exist yet.

    Its tag semantics are the ones observed on a live 1.13.4 server, not
    assumed: a container ``PUT`` *merges* the request's tags into the ones
    already held, a ``PATCH`` of ``/tags`` *replaces* them, and either is
    refused with HTTP 400 if it would leave two tags of a mutually exclusive
    classification on one container. It also ships the system ``PII``
    classification, as every real server does.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, Any]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.classifications: dict[str, dict[str, Any]] = {
            "PII": {"name": "PII", "mutuallyExclusive": True}
        }
        self.tags: dict[str, dict[str, Any]] = {
            f"PII.{name}": {"name": name, "classification": "PII"}
            for name in ("None", "NonSensitive", "Sensitive")
        }
        self.edges: list[tuple[str, str]] = []

    def install(self, router: respx.Router) -> None:
        router.get(f"{DEFAULT_HOST}/v1/system/version").mock(
            side_effect=lambda request: self._record(request, {"version": "1.13.4"})
        )
        router.put(f"{DEFAULT_HOST}/v1/services/storageServices").mock(side_effect=self._service)
        router.put(f"{DEFAULT_HOST}/v1/classifications").mock(side_effect=self._classification)
        router.put(f"{DEFAULT_HOST}/v1/tags").mock(side_effect=self._tag)
        router.put(f"{DEFAULT_HOST}/v1/containers").mock(side_effect=self._container)
        by_id = rf"^{DEFAULT_HOST}/v1/containers/(?P<container_id>[^/?]+)(\?.*)?$"
        router.get(url__regex=by_id).mock(side_effect=self._get_container)
        router.patch(url__regex=by_id).mock(side_effect=self._patch_container)
        router.put(f"{DEFAULT_HOST}/v1/lineage").mock(side_effect=self._lineage)

    def tag_container(self, fqn: str, label: dict[str, Any]) -> None:
        """Apply a tag the way somebody using the UI would, outside publication."""
        self.containers[fqn]["tags"] = [*self.containers[fqn].get("tags", []), dict(label)]

    def tags_of(self, fqn: str) -> list[str]:
        return [label["tagFQN"] for label in self.containers[fqn].get("tags", [])]

    @property
    def ids(self) -> dict[str, str]:
        """Container entity IDs by the bio:// identifier each carries."""
        return {body["fullPath"]: body["id"] for body in self.containers.values()}

    @property
    def published_edges(self) -> set[tuple[str, str]]:
        """The edges, translated back from entity IDs to bio:// identifiers."""
        uris = {entity_id: uri for uri, entity_id in self.ids.items()}
        return {(uris[source], uris[target]) for source, target in self.edges}

    def _record(
        self, request: httpx.Request, payload: dict[str, Any], status: int = 200
    ) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        self.sent.append((request.method, request.url.path, json.loads(request.content or b"null")))
        return httpx.Response(status, json=payload)

    def _refusal(self, labels: list[dict[str, Any]]) -> tuple[int, str] | None:
        """What the real server says about a tag list it will not hold."""
        unknown = [label["tagFQN"] for label in labels if label["tagFQN"] not in self.tags]
        if unknown:
            return 404, f"tag instance for {unknown[0]} not found"
        seen: dict[str, str] = {}
        for label in labels:
            parent = label["tagFQN"].rsplit(".", 1)[0]
            if not self.classifications.get(parent, {}).get("mutuallyExclusive"):
                continue
            if parent in seen and seen[parent] != label["tagFQN"]:
                return 400, (
                    f"Tag labels {seen[parent]} and {label['tagFQN']} are mutually "
                    "exclusive and can't be assigned together"
                )
            seen[parent] = label["tagFQN"]
        return None

    def _service(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        body["fullyQualifiedName"] = body["name"]
        self.services[body["name"]] = body
        return self._record(request, body)

    def _classification(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        body["fullyQualifiedName"] = body["name"]
        self.classifications[body["name"]] = body
        return self._record(request, body)

    def _tag(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fqn = f"{body['classification']}.{body['name']}"
        body["fullyQualifiedName"] = fqn
        self.tags[fqn] = body
        return self._record(request, body)

    def _container(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fqn = f"{body['service']}.{body['name']}"
        existing = self.containers.get(fqn, {})
        # A PUT merges: every tag already held stays, and the request's are added.
        held = list(existing.get("tags", []))
        merged = held + [
            label
            for label in body.pop("tags", [])
            if label["tagFQN"] not in {tag["tagFQN"] for tag in held}
        ]
        refused = self._refusal(merged)
        if refused:
            return self._record(request, {"message": refused[1]}, refused[0])
        body["fullyQualifiedName"] = fqn
        body["id"] = existing.get("id") or f"id-{body['name']}"
        self.containers[fqn] = {**body, **({"tags": merged} if merged else {})}
        # Like the real server, the response carries no tags unless the request did.
        return self._record(request, body)

    def _by_id(self, container_id: str) -> str:
        return next(fqn for fqn, body in self.containers.items() if body["id"] == container_id)

    def _get_container(self, request: httpx.Request, container_id: str) -> httpx.Response:
        container = self.containers[self._by_id(container_id)]
        return self._record(request, {**container, "tags": container.get("tags", [])})

    def _patch_container(self, request: httpx.Request, container_id: str) -> httpx.Response:
        if request.headers["content-type"] != "application/json-patch+json":
            return self._record(request, {"message": "unsupported media type"}, 415)
        fqn = self._by_id(container_id)
        # Only the one operation the client sends is implemented: anything
        # else it started sending would fail here rather than pass untested.
        [operation] = json.loads(request.content)
        assert (operation["op"], operation["path"]) == ("add", "/tags")
        refused = self._refusal(operation["value"])
        if refused:
            return self._record(request, {"message": refused[1]}, refused[0])
        # A PATCH replaces: what the list says is what the container holds.
        self.containers[fqn]["tags"] = operation["value"]
        return self._record(request, self.containers[fqn])

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

    # Idempotence is a property of the requests: every PUT is a create-or-
    # update, and every PATCH sets the classification to a value rather than
    # changing it by a delta, so the second run sends what the first did.
    assert {method for method, _ in catalog.requests} <= {"GET", "PUT", "PATCH"}
    assert catalog.requests[len(first) :] == first
    assert len(catalog.containers) == 7
    assert len(catalog.classifications) == 2  # ours, and the server's own PII
    assert {fqn for fqn in catalog.tags if fqn.startswith(CLASSIFICATION_NAME)} == {
        classification_tag_fqn(value) for value in Classification
    }
    assert catalog.published_edges == EXPECTED_EDGES
    # 1 service + 1 classification + 4 tags + 7 x (PUT + GET + PATCH) + 6 edges.
    assert len(first) == 33
    assert sum(method == "PATCH" for method, _ in first) == 7


# --------------------------------------------------------------------------
# Governance metadata: classification projected, ownership deliberately not
# --------------------------------------------------------------------------


def publish(
    catalog: FakeOpenMetadata, raw: Path, results: Path, *, times: int = 1
) -> list[tuple[str, str]]:
    """Publish ``times`` times against the fake, and return each run's requests."""
    with respx.mock as router:
        catalog.install(router)
        with OpenMetadataClient(OpenMetadataConfig.from_env()) as client:
            runs = []
            for _ in range(times):
                start = len(catalog.requests)
                publish_study(client, raw, results)
                runs.append(catalog.requests[start:])
    return [request for run in runs for request in run]


def test_the_classification_vocabulary_is_one_mutually_exclusive_classification(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    publish(catalog, raw, results)

    assert catalog.classifications[CLASSIFICATION_NAME]["mutuallyExclusive"] is True
    # The whole vocabulary, named by its own values, not only the one in use.
    assert {fqn for fqn in catalog.tags if fqn.startswith(f"{CLASSIFICATION_NAME}.")} == {
        f"{CLASSIFICATION_NAME}.{value}" for value in Classification
    }


def test_every_container_carries_the_declared_classification_as_a_tag(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    publish(catalog, raw, results)

    expected = classification_tag_fqn(Classification.INTERNAL)
    assert expected == "bio_governance_classification.internal"
    for body in catalog.containers.values():
        assert body["tags"] == [
            {
                "tagFQN": expected,
                "source": "Classification",
                "labelType": "Manual",
                "state": "Confirmed",
            }
        ]


def test_the_declared_value_is_what_is_projected(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata, tmp_path: Path
) -> None:
    """The tag follows the declaration, not a default."""
    raw, results = study_files
    declaration = tmp_path / "BIO-001.yaml"
    declaration.write_text(
        (GOVERNANCE_DIR / "BIO-001.yaml")
        .read_text(encoding="utf-8")
        .replace("internal", "confidential"),
        encoding="utf-8",
    )
    validate_declaration(raw, results, declaration)

    publish(catalog, raw, results)

    tags = {tag["tagFQN"] for body in catalog.containers.values() for tag in body["tags"]}
    assert tags == {"bio_governance_classification.confidential"}


def test_ownership_is_not_sent_to_openmetadata(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    """OpenMetadata owners are server UUIDs of existing users or teams.

    The declaration names people, and this project provisions no accounts, so
    no request carries an owner — and no description is quietly rewritten to
    carry one instead.
    """
    raw, results = study_files

    published = publish(catalog, raw, results)

    assert published
    for body in [*catalog.containers.values(), *catalog.services.values()]:
        assert "owners" not in body
        assert "Avery Example" not in json.dumps(body)


def test_tags_exist_before_any_container_is_classified(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    requests = publish(catalog, raw, results)

    paths = [path for _, path in requests]
    last_tag = max(index for index, path in enumerate(paths) if path == "/api/v1/tags")
    first_patch = next(index for index, (method, _) in enumerate(requests) if method == "PATCH")
    assert last_tag < paths.index("/api/v1/containers") < first_patch


@pytest.mark.parametrize(("damage", "message"), REFUSALS, ids=[d for d, _ in REFUSALS])
def test_publication_refuses_governance_metadata_it_cannot_rely_on(
    study_files: tuple[Path, Path],
    catalog: FakeOpenMetadata,
    tmp_path: Path,
    damage: str,
    message: str,
) -> None:
    """Only a validated declaration for this study is projected — checked before any request."""
    raw, results = study_files
    damage_governance_evidence(raw, results, tmp_path, damage)

    with respx.mock as router:
        catalog.install(router)
        with (
            OpenMetadataClient(OpenMetadataConfig.from_env()) as client,
            pytest.raises(CatalogError) as error,
        ):
            publish_study(client, raw, results)

    assert message in str(error.value)
    assert catalog.requests == []


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
# Classification lifecycle: a changed declaration reclassifies, and only the
# project's own classification is ever touched
# --------------------------------------------------------------------------


def project_labels(catalog: FakeOpenMetadata, fqn: str) -> list[dict[str, Any]]:
    return [
        label
        for label in catalog.containers[fqn]["tags"]
        if label["tagFQN"].startswith(f"{CLASSIFICATION_NAME}.")
    ]


def test_classified_tags_replaces_the_project_classification_and_keeps_the_rest() -> None:
    glossary = {
        "tagFQN": "Assays.Transcriptomics",
        "source": "Glossary",
        "labelType": "Manual",
        "state": "Confirmed",
    }
    current = [STEWARD_TAG, classification_tag_label(Classification.INTERNAL), glossary]

    tags = classified_tags(current, Classification.CONFIDENTIAL)

    # Every label outside the namespace comes back exactly as it was, in order.
    assert tags == [STEWARD_TAG, glossary, classification_tag_label(Classification.CONFIDENTIAL)]


def test_classified_tags_adds_a_classification_to_an_unclassified_container() -> None:
    assert classified_tags([], Classification.INTERNAL) == [
        classification_tag_label(Classification.INTERNAL)
    ]
    assert classified_tags([STEWARD_TAG], Classification.INTERNAL) == [
        STEWARD_TAG,
        classification_tag_label(Classification.INTERNAL),
    ]


def test_the_project_owns_its_classification_not_every_label_with_its_prefix() -> None:
    """A glossary term that happens to share the name is somebody else's label."""
    lookalike = {
        "tagFQN": f"{CLASSIFICATION_NAME}.internal",
        "source": "Glossary",
        "labelType": "Manual",
        "state": "Confirmed",
    }

    assert lookalike in classified_tags([lookalike], Classification.PUBLIC)


LIFECYCLE = [
    pytest.param(None, Classification.INTERNAL, id="none-to-internal"),
    pytest.param(Classification.INTERNAL, Classification.INTERNAL, id="internal-to-internal"),
    pytest.param(
        Classification.INTERNAL, Classification.CONFIDENTIAL, id="internal-to-confidential"
    ),
    pytest.param(
        Classification.CONFIDENTIAL, Classification.RESTRICTED, id="confidential-to-restricted"
    ),
    pytest.param(Classification.RESTRICTED, Classification.PUBLIC, id="restricted-to-public"),
]


@pytest.mark.parametrize(("before", "after"), LIFECYCLE)
def test_publication_sets_exactly_the_declared_classification(
    study_files: tuple[Path, Path],
    catalog: FakeOpenMetadata,
    tmp_path: Path,
    before: Classification | None,
    after: Classification,
) -> None:
    """Every transition ends in one project classification, the declared one.

    Each container also carries a tag a steward added by hand, and that tag
    comes through every transition untouched. Publishing the resulting state
    again changes nothing and sends the same requests.
    """
    raw, results = study_files
    if before is None:
        # As milestone 7 left the live server: containers, and no classification.
        with respx.mock as router:
            catalog.install(router)
            with OpenMetadataClient(OpenMetadataConfig.from_env()) as client:
                for asset in prepare_assets("BIO-001"):
                    client.upsert_container(asset, service=SERVICE_NAME)
    else:
        declare_classification(raw, results, tmp_path, before)
        publish(catalog, raw, results)
    for fqn in catalog.containers:
        catalog.tag_container(fqn, STEWARD_TAG)

    declare_classification(raw, results, tmp_path, after)
    first = publish(catalog, raw, results)
    state = copy.deepcopy(catalog.containers)
    second = publish(catalog, raw, results)

    assert len(catalog.containers) == 7
    for fqn in catalog.containers:
        assert project_labels(catalog, fqn) == [classification_tag_label(after)]
        assert STEWARD_TAG in catalog.containers[fqn]["tags"]
        if before is not None and before is not after:
            assert classification_tag_fqn(before) not in catalog.tags_of(fqn)
    assert catalog.published_edges == EXPECTED_EDGES
    assert second == first
    assert catalog.containers == state


def test_the_container_put_carries_no_tags_and_the_patch_touches_only_tags(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    raw, results = study_files

    publish(catalog, raw, results)

    puts = [
        body
        for method, path, body in catalog.sent
        if (method, path) == ("PUT", "/api/v1/containers")
    ]
    patches = [body for method, _, body in catalog.sent if method == "PATCH"]
    assert len(puts) == 7
    assert all("tags" not in body for body in puts)
    assert len(patches) == 7
    for body in patches:
        assert [(operation["op"], operation["path"]) for operation in body] == [("add", "/tags")]


def test_the_fake_refuses_a_put_reclassification_as_the_live_server_did(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata
) -> None:
    """The lifecycle tests are only as good as the fake's tag semantics.

    OpenMetadata 1.13.4 answered a container PUT carrying ``confidential``, on a
    container holding ``internal``, with this HTTP 400. A fake that overwrote
    tags on PUT would let a PUT-only publication pass every test above.
    """
    raw, results = study_files
    publish(catalog, raw, results)

    with respx.mock as router:
        catalog.install(router)
        response = httpx.put(
            f"{DEFAULT_HOST}/v1/containers",
            json={
                "name": "BIO-001_raw_samples",
                "service": SERVICE_NAME,
                "tags": [classification_tag_label(Classification.CONFIDENTIAL)],
            },
        )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "Tag labels bio_governance_classification.internal and "
        "bio_governance_classification.confidential are mutually exclusive "
        "and can't be assigned together"
    )
    assert catalog.tags_of(f"{SERVICE_NAME}.BIO-001_raw_samples") == [
        classification_tag_fqn(Classification.INTERNAL)
    ]


def test_a_reclassification_that_did_not_validate_sends_nothing(
    study_files: tuple[Path, Path], catalog: FakeOpenMetadata, tmp_path: Path
) -> None:
    raw, results = study_files
    publish(catalog, raw, results)
    state = copy.deepcopy(catalog.containers)
    sent = len(catalog.requests)
    declaration = tmp_path / "bad" / "BIO-001.yaml"
    declaration.parent.mkdir()
    declaration.write_text(
        (GOVERNANCE_DIR / "BIO-001.yaml")
        .read_text(encoding="utf-8")
        .replace("classification: internal", "classification: secret"),
        encoding="utf-8",
    )
    validate_declaration(raw, results, declaration, expect=1)

    with pytest.raises(CatalogError, match="did not validate"):
        publish(catalog, raw, results)

    assert len(catalog.requests) == sent
    assert catalog.containers == state


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
    assert "Classification: internal -> tag bio_governance_classification.internal" in result.output
    assert "Ownership: not published" in result.output


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
    assert "bio_governance_classification.internal" in result.output
