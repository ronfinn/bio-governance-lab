"""Tests for publishing the same governed assets to DataHub.

None of these need a server. DataHub is a Docker deployment that CI has no
business starting, so the SDK boundary is mocked and what is asserted is the
thing this project actually controls: which datasets are prepared, what identity
they carry into DataHub, which aspects are proposed, which edges are published,
and that publishing twice proposes the same upserts rather than a second set of
entities.

The boundary that is faked is the emitter, not the metadata model. Every
proposal these tests inspect is a real ``MetadataChangeProposalWrapper`` holding
a real aspect class, because a test that accepted a hand-built dictionary would
prove that this project agrees with itself rather than with DataHub.

The live demonstration lives in ``tests/test_catalog_datahub_live.py``, which
skips unless a local instance is explicitly opted into.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from datahub.emitter.mce_builder import make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    DataPlatformInfoClass,
    DatasetPropertiesClass,
    SchemaMetadataClass,
    SubTypesClass,
    UpstreamLineageClass,
)
from typer.testing import CliRunner

from bio_governance.catalog import (
    CANONICAL_PROPERTY,
    DATAHUB_DEFAULT_GMS_URL,
    DATAHUB_GMS_VAR,
    DATAHUB_TOKEN_VAR,
    ENVIRONMENT,
    PLATFORM_NAME,
    PLATFORM_URN,
    CatalogError,
    DataHubConfig,
    FileFormat,
    custom_properties,
    dataset_name,
    dataset_urn,
    prepare_assets,
    study_identifiers,
    study_urns,
    upstreams,
)
from bio_governance.catalog.datahub_client import DataHubClient
from bio_governance.catalog.datahub_publish import publish_study_to_datahub
from bio_governance.cli import app
from bio_governance.models import AssetIdentifier

runner = CliRunner()

#: A token-shaped string. Never a real token: the point of one test below is
#: that whatever is configured stays out of the output.
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

#: Every edge the project claims, and no others. The same six the OpenMetadata
#: tests assert on, restated here so a change to one catalogue's edges cannot
#: pass by agreeing with the other's.
EXPECTED_EDGES = {
    (RAW[0], CURATED[0]),
    (RAW[1], CURATED[1]),
    (RAW[2], CURATED[2]),
    (RAW[0], REPORT),
    (RAW[1], REPORT),
    (RAW[2], REPORT),
}


def urn_of(uri: str) -> str:
    return dataset_urn(AssetIdentifier.parse(uri))


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_config_defaults_to_the_local_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATAHUB_GMS_VAR, raising=False)
    monkeypatch.delenv(DATAHUB_TOKEN_VAR, raising=False)

    config = DataHubConfig.from_env()

    assert config.gms_url == DATAHUB_DEFAULT_GMS_URL == "http://localhost:8080"
    assert config.token is None


def test_config_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATAHUB_GMS_VAR, "http://datahub.example.org:8080/")
    monkeypatch.setenv(DATAHUB_TOKEN_VAR, TOKEN)

    config = DataHubConfig.from_env()

    assert config.gms_url == "http://datahub.example.org:8080"
    assert config.token == TOKEN


def test_token_is_never_reported_in_full() -> None:
    hint = DataHubConfig(token=TOKEN).token_hint

    assert TOKEN not in hint
    assert "signature" not in hint
    assert hint.endswith("ture)")


# --------------------------------------------------------------------------
# Identity: bio:// down to a DataHub URN, and never the other way
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("bio://BIO-001/raw/samples", "BIO-001.raw.samples"),
        ("bio://BIO-001/curated/expression", "BIO-001.curated.expression"),
        ("bio://BIO-001/quality/dq-report", "BIO-001.quality.dq-report"),
    ],
)
def test_dataset_name_is_derived_deterministically(uri: str, expected: str) -> None:
    assert dataset_name(AssetIdentifier.parse(uri)) == expected


def test_the_urn_convention_is_the_sdk_s_own() -> None:
    """The hand-built URN string has to be the one the SDK would have built.

    The mapping builds URNs as strings so that deriving an identity costs
    nothing to import. That is only safe while the convention agrees with
    DataHub's, and this is where the SDK is the authority on whether it does.
    """
    for identifier in study_identifiers("BIO-001"):
        assert dataset_urn(identifier) == make_dataset_urn(
            platform=PLATFORM_NAME,
            name=dataset_name(identifier),
            env=ENVIRONMENT,
        )


def test_a_study_prepares_exactly_the_seven_governed_assets() -> None:
    assets = prepare_assets("BIO-001")

    assert len(assets) == 7
    assert [asset.identifier for asset in assets] == [*RAW, *CURATED, REPORT]
    assert len(set(study_urns("BIO-001"))) == 7


def test_every_dataset_keeps_its_canonical_identifier() -> None:
    for asset in prepare_assets("BIO-001"):
        assert custom_properties(asset)[CANONICAL_PROPERTY] == asset.identifier
        assert asset.identifier.startswith("bio://")
        # The URN is DataHub's address, not the project's identity.
        assert "bio://" not in urn_of(asset.identifier)


def test_custom_properties_carry_the_study_stage_format_and_size() -> None:
    asset = next(
        a for a in prepare_assets("BIO-001", sizes={RAW[0]: 4096}) if a.identifier == RAW[0]
    )

    assert custom_properties(asset) == {
        CANONICAL_PROPERTY: RAW[0],
        "study": "BIO-001",
        "lifecycle_stage": "raw",
        "file_format": "csv",
        "size_bytes": "4096",
    }


def test_an_unmeasured_file_reports_no_size_rather_than_none() -> None:
    asset = next(a for a in prepare_assets("BIO-001") if a.identifier == RAW[0])

    assert "size_bytes" not in custom_properties(asset)


def test_tabular_assets_are_csv_and_the_report_is_json() -> None:
    formats = {asset.identifier: asset.file_format for asset in prepare_assets("BIO-001")}

    assert formats[RAW[0]] is FileFormat.CSV
    assert formats[CURATED[2]] is FileFormat.CSV
    assert formats[REPORT] is FileFormat.JSON


def test_only_the_explainable_lineage_edges_are_published() -> None:
    grouped = upstreams("BIO-001")

    assert {
        (source, target) for target, sources in grouped.items() for source in sources
    } == EXPECTED_EDGES
    # The report's three inputs are one aspect, not three that overwrite.
    assert len(grouped[REPORT]) == 3
    assert len(grouped) == 4


# --------------------------------------------------------------------------
# Publication, against a recording emitter
# --------------------------------------------------------------------------


class RecordingEmitter:
    """A stand-in for the SDK's REST emitter that keeps every proposal.

    It is deliberately not a dictionary of entities: DataHub's write model is a
    stream of Metadata Change Proposals, and recording them in order is what
    lets a test say that publishing twice sent the same upserts against the same
    URNs rather than a second set of entities.
    """

    def __init__(self) -> None:
        self.proposals: list[MetadataChangeProposalWrapper] = []
        self.flushes = 0

    def emit(self, item: MetadataChangeProposalWrapper, callback: Any = None) -> None:
        self.proposals.append(item)

    def flush(self) -> None:
        self.flushes += 1

    def aspects(self, aspect_type: type) -> dict[str, Any]:
        """Every proposal of one aspect type, by the URN it was proposed against."""
        return {
            str(proposal.entityUrn): proposal.aspect
            for proposal in self.proposals
            if isinstance(proposal.aspect, aspect_type)
        }

    @property
    def signature(self) -> list[tuple[str, str, str]]:
        """What was sent, reduced to what idempotence is a claim about."""
        return [
            (
                str(proposal.entityUrn),
                type(proposal.aspect).__name__,
                str(proposal.changeType),
            )
            for proposal in self.proposals
        ]


@pytest.fixture
def emitter(monkeypatch: pytest.MonkeyPatch) -> RecordingEmitter:
    monkeypatch.delenv(DATAHUB_GMS_VAR, raising=False)
    monkeypatch.delenv(DATAHUB_TOKEN_VAR, raising=False)
    return RecordingEmitter()


def test_publication_proposes_the_platform_the_datasets_and_the_lineage(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        published = publish_study_to_datahub(client, raw, results)

    assert published.study_id == "BIO-001"
    assert published.service == PLATFORM_URN
    assert emitter.aspects(DataPlatformInfoClass).keys() == {PLATFORM_URN}
    assert set(emitter.aspects(DatasetPropertiesClass)) == set(study_urns("BIO-001"))
    assert len(emitter.aspects(DatasetPropertiesClass)) == 7
    assert published.lineage_run_id == "11111111-2222-3333-4444-555555555555"
    assert emitter.flushes >= 1


def test_published_datasets_carry_the_bio_identifier(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        publish_study_to_datahub(client, raw, results)

    for identifier in study_identifiers("BIO-001"):
        properties = emitter.aspects(DatasetPropertiesClass)[dataset_urn(identifier)]
        assert properties.qualifiedName == identifier.uri
        assert properties.customProperties[CANONICAL_PROPERTY] == identifier.uri
        assert properties.description


def test_each_dataset_is_subtyped_by_its_lifecycle_stage(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        publish_study_to_datahub(client, raw, results)

    subtypes = emitter.aspects(SubTypesClass)

    assert subtypes[urn_of(RAW[0])].typeNames == ["Raw File"]
    assert subtypes[urn_of(CURATED[0])].typeNames == ["Curated File"]
    assert subtypes[urn_of(REPORT)].typeNames == ["Quality Report"]


def test_the_contracts_become_the_schema_of_samples_and_compounds(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    """Only the two contract-backed datasets get a schema, and it is the contract's.

    The expression matrix is wide and generated; hundreds of schema fields would
    be noise, and no contract declares them. What is published is the declared
    structure, not whatever a header happened to say on the day.
    """
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        publish_study_to_datahub(client, raw, results)

    schemas = emitter.aspects(SchemaMetadataClass)

    assert set(schemas) == {
        urn_of(RAW[0]),
        urn_of(RAW[1]),
        urn_of(CURATED[0]),
        urn_of(CURATED[1]),
    }
    fields = [field.fieldPath for field in schemas[urn_of(RAW[0])].fields]
    assert "sample_id" in fields
    assert schemas[urn_of(RAW[0])].platform == PLATFORM_URN


def test_lineage_is_proposed_as_one_aspect_per_downstream_dataset(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        publish_study_to_datahub(client, raw, results)

    lineage = emitter.aspects(UpstreamLineageClass)
    published = {
        (upstream.dataset, urn) for urn, aspect in lineage.items() for upstream in aspect.upstreams
    }

    assert published == {(urn_of(source), urn_of(target)) for source, target in EXPECTED_EDGES}
    # Four aspects for six edges: the report's three inputs arrive together,
    # because an upstreamLineage aspect replaces the whole list.
    assert len(lineage) == 4
    assert len(lineage[urn_of(REPORT)].upstreams) == 3


def test_publishing_twice_is_idempotent(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    """The second publication sends the same upserts against the same URNs.

    Idempotence here is not bookkeeping: the URNs are derived from the bio://
    identifiers rather than assigned by the server, and every proposal is an
    upsert, so DataHub is left holding seven datasets and six edges either way.
    """
    raw, results = study_files

    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        first = publish_study_to_datahub(client, raw, results)
    first_signature = list(emitter.signature)

    second_emitter = RecordingEmitter()
    with DataHubClient(DataHubConfig(), emitter=second_emitter) as client:
        second = publish_study_to_datahub(client, raw, results)

    assert first.assets == second.assets
    assert first.edges == second.edges
    assert second_emitter.signature == first_signature
    assert {change for _, _, change in first_signature} == {"UPSERT"}
    assert len({urn for urn, _, _ in first_signature}) == 8  # the platform and seven datasets


def test_a_missing_curated_file_stops_publication_before_any_proposal(
    study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files
    (results / "curated" / "compounds.csv").unlink()

    with (
        DataHubClient(DataHubConfig(), emitter=emitter) as client,
        pytest.raises(CatalogError) as error,
    ):
        publish_study_to_datahub(client, raw, results)

    assert "compounds.csv" in str(error.value)
    assert emitter.proposals == []


# --------------------------------------------------------------------------
# Failure, reported usefully
# --------------------------------------------------------------------------


class DeadEmitter:
    """An emitter for a server that is not there."""

    def emit(self, item: Any, callback: Any = None) -> None:
        raise ConnectionError("Failed to connect to localhost port 8080")

    def flush(self) -> None:
        pass


def test_an_unreachable_server_names_the_gms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATAHUB_GMS_VAR, "http://localhost:8080")
    monkeypatch.delenv(DATAHUB_TOKEN_VAR, raising=False)

    with (
        DataHubClient(DataHubConfig.from_env(), emitter=DeadEmitter()) as client,
        pytest.raises(CatalogError) as error,
    ):
        client.emit_platform()

    message = str(error.value)
    assert "http://localhost:8080" in message
    assert "Failed to connect" in message


class RejectingEmitter:
    """An emitter for a deployment whose metadata service demands a token."""

    def emit(self, item: Any, callback: Any = None) -> None:
        raise PermissionError("HTTP 401: Unauthorized to perform this action.")

    def flush(self) -> None:
        pass


def test_a_rejected_token_says_so_without_printing_it() -> None:
    config = DataHubConfig(token=TOKEN)

    with (
        DataHubClient(config, emitter=RejectingEmitter()) as client,
        pytest.raises(CatalogError) as error,
    ):
        client.emit_platform()

    message = str(error.value)
    assert "401" in message
    assert TOKEN not in message
    assert "signature" not in message


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


class FakeGraph:
    """The read side of a DataHub that already holds one published study.

    Aspects are keyed by URN the way DataHub keys them, so a study published
    twice would show up here as one entry rather than two — which is the same
    reason the OpenMetadata tests key containers by fully qualified name.
    """

    def __init__(self, emitter: RecordingEmitter) -> None:
        self.aspects: dict[tuple[str, str], Any] = {
            (str(proposal.entityUrn), type(proposal.aspect).__name__): proposal.aspect
            for proposal in emitter.proposals
        }

    def get_config(self) -> dict[str, Any]:
        # Keyed the way a v1.7 quickstart keys it, which is not the key the
        # first version of the client guessed.
        return {"versions": {"acryldata/datahub": {"version": "v1.7.0"}}}

    def get_aspect(self, urn: str, aspect_type: type) -> Any:
        return self.aspects.get((urn, aspect_type.__name__))


@pytest.fixture
def published(study_files: tuple[Path, Path], emitter: RecordingEmitter) -> RecordingEmitter:
    raw, results = study_files
    with DataHubClient(DataHubConfig(), emitter=emitter) as client:
        publish_study_to_datahub(client, raw, results)
    return emitter


def test_health_reports_the_server_version(published: RecordingEmitter) -> None:
    with DataHubClient(DataHubConfig(), graph=FakeGraph(published)) as client:
        assert client.server_version() == "v1.7.0"


def test_cli_health_reports_the_version_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch, published: RecordingEmitter
) -> None:
    monkeypatch.setenv(DATAHUB_TOKEN_VAR, TOKEN)
    monkeypatch.setattr(
        "bio_governance.catalog.datahub_client.DataHubGraph",
        lambda config: FakeGraph(published),
    )

    result = runner.invoke(app, ["catalog", "datahub", "health"])

    assert result.exit_code == 0, result.output
    assert "v1.7.0" in result.output
    assert TOKEN not in result.output


def test_cli_health_exits_2_when_the_server_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(config: Any) -> Any:
        raise ConnectionError("Failed to connect to localhost port 8080")

    monkeypatch.setattr("bio_governance.catalog.datahub_client.DataHubGraph", refuse)

    result = runner.invoke(app, ["catalog", "datahub", "health"])

    assert result.exit_code == 2
    assert "Failed to connect" in result.output


def test_cli_publish_prints_seven_assets_and_six_edges(
    monkeypatch: pytest.MonkeyPatch, study_files: tuple[Path, Path], emitter: RecordingEmitter
) -> None:
    raw, results = study_files
    monkeypatch.setattr(
        "bio_governance.catalog.datahub_client.DataHubGraph",
        lambda config: FakeGraph(emitter),
    )
    monkeypatch.setattr(
        "bio_governance.catalog.datahub_client.DatahubRestEmitter",
        lambda **kwargs: emitter,
    )

    result = runner.invoke(app, ["catalog", "datahub", "publish", str(raw), str(results)])

    assert result.exit_code == 0, result.output
    assert "7 assets" in result.output
    assert "6 lineage edges" in result.output
    assert f"Platform: {PLATFORM_URN}" in result.output
    assert RAW[0] in result.output


def test_cli_get_resolves_the_seven_assets_and_the_six_edges(
    monkeypatch: pytest.MonkeyPatch, published: RecordingEmitter
) -> None:
    monkeypatch.setattr(
        "bio_governance.catalog.datahub_client.DataHubGraph",
        lambda config: FakeGraph(published),
    )

    result = runner.invoke(app, ["catalog", "datahub", "get", "BIO-001"])

    assert result.exit_code == 0, result.output
    assert "Assets: 7 of 7" in result.output
    assert "6 lineage edges" in result.output
    for identifier in study_identifiers("BIO-001"):
        assert identifier.uri in result.output
    assert f"{urn_of(RAW[0])} -> {urn_of(CURATED[0])}" in result.output
    assert f"{urn_of(RAW[1])} -> {urn_of(REPORT)}" in result.output


def test_cli_get_exits_2_when_a_study_was_never_published(
    monkeypatch: pytest.MonkeyPatch, emitter: RecordingEmitter
) -> None:
    monkeypatch.setattr(
        "bio_governance.catalog.datahub_client.DataHubGraph",
        lambda config: FakeGraph(emitter),
    )

    result = runner.invoke(app, ["catalog", "datahub", "get", "BIO-404"])

    assert result.exit_code == 2
    assert "not in DataHub" in result.output


# --------------------------------------------------------------------------
# The two integrations coexist
# --------------------------------------------------------------------------


def test_the_openmetadata_commands_are_untouched() -> None:
    result = runner.invoke(app, ["catalog", "--help"])

    assert result.exit_code == 0, result.output
    assert "openmetadata" in result.output
    assert "datahub" in result.output

    for command in ("health", "publish", "get"):
        openmetadata = runner.invoke(app, ["catalog", "openmetadata", command, "--help"])
        assert openmetadata.exit_code == 0, openmetadata.output
