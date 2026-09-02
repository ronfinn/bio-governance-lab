"""Talking to a local DataHub, through its official Python SDK.

The OpenMetadata client in this package deliberately does *not* use that
project's SDK: ``openmetadata-ingestion`` resolves to around 130 transitive
packages for five kinds of request, and the REST API it wraps is documented and
stable. The decision goes the other way here, and for reasons worth writing
down rather than for consistency's sake:

* ``acryl-datahub`` resolves to about 60 packages, none of them a dbt or a
  Kubernetes client, and it installs cleanly on this project's Python 3.14.
* DataHub's write model is not a REST entity model. A write is a **Metadata
  Change Proposal** — an aspect, a change type and an entity URN — and the
  aspects are code-generated Avro-backed classes. Hand-rolling that JSON would
  mean hand-rolling a schema the SDK already holds correctly, which is the
  opposite of the trade the OpenMetadata client makes.
* Learning the difference between the two models is a stated purpose of this
  project, and the SDK is where DataHub's model is actually expressed.

(DataHub's own abbreviation for a Metadata Change Proposal is "MCP". This
repository already has a Model Context Protocol server. The two are unrelated,
and the abbreviation is not used for either in this package.)

Every write here is an ``UPSERT`` proposal against a deterministic URN, so
publishing twice updates the same seven datasets rather than creating a second
set. Idempotence is a property of the URNs and the change type, not of any
bookkeeping this module does.

The SDK is imported at this module's top level and this module is imported
lazily by its callers, for the reason the CLI already imports the MCP SDK
lazily: the metadata model costs about half a second to import, and the six
``bio-gov`` commands the pipeline shells out to on every run must not pay it.

The token, where a deployment requires one, is never logged, echoed, or
included in an error message.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from types import TracebackType
from typing import Any, TypeVar

from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
from datahub.metadata.schema_classes import (
    DataPlatformInfoClass,
    DatasetLineageTypeClass,
    DatasetPropertiesClass,
    OtherSchemaClass,
    PlatformTypeClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    SubTypesClass,
    UpstreamClass,
    UpstreamLineageClass,
)

from bio_governance.catalog.datahub_mapping import (
    NAME_DELIMITER,
    PLATFORM_DISPLAY_NAME,
    PLATFORM_NAME,
    PLATFORM_URN,
    custom_properties,
    dataset_name,
    dataset_urn,
    subtype,
)
from bio_governance.catalog.models import CatalogAsset, CatalogError, DataHubConfig
from bio_governance.models import AssetIdentifier

#: How long any single call may take. A local server that has not answered in
#: half a minute is not a slow server, it is a stopped one.
DEFAULT_TIMEOUT = 30.0

T = TypeVar("T")


class DataHubClient:
    """Writes and reads one DataHub instance's metadata through the SDK.

    Constructed with a :class:`DataHubConfig`. ``emitter`` and ``graph`` exist
    for the same reason the OpenMetadata client takes a transport: the tests
    substitute them, so the whole publication path can be asserted on without a
    server. Neither is built until it is needed, because constructing either
    one opens a connection.
    """

    def __init__(
        self,
        config: DataHubConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        emitter: Any | None = None,
        graph: Any | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._emitter = emitter
        self._graph = graph

    def __enter__(self) -> DataHubClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Flush anything the emitter is holding and drop both connections.

        Closing must not raise: a stopped server is exactly when a caller is
        unwinding, and a failure to flush is not news then.
        """
        if self._emitter is not None:
            with suppress(Exception):
                self._emitter.flush()
        self._emitter = None
        self._graph = None

    def server_version(self) -> str:
        """The running GMS's version, and so a proof it is reachable.

        DataHub reports its version through the same ``/config`` endpoint that
        says whether the metadata service requires authentication, so the
        answer doubles as the reason a token may or may not be needed.

        The ``versions`` block is keyed by component — a v1.7 quickstart names
        itself ``acryldata/datahub`` — so the key is not assumed. Any component
        reporting a version answers the question this method is really asking,
        which is whether a DataHub is there.
        """
        config = self._call("read the server configuration", lambda: self._client().get_config())
        versions = config.get("versions")
        if isinstance(versions, dict):
            for component in versions.values():
                version = component.get("version") if isinstance(component, dict) else None
                if isinstance(version, str) and version:
                    return version
        # Older and slimmer deployments answer /config without a version block.
        # A configuration that came back at all still proves reachability.
        return "unknown"

    def emit_platform(self) -> str:
        """Register the data platform the seven datasets belong to.

        DataHub will accept datasets on an unregistered platform URN and show
        them under a bare identifier, so this is not strictly required. It is
        sent anyway, because a platform with no ``dataPlatformInfo`` is the
        DataHub equivalent of a container with no storage service: the entity
        exists but nothing says what it is.
        """
        info = DataPlatformInfoClass(
            name=PLATFORM_NAME,
            displayName=PLATFORM_DISPLAY_NAME,
            type=PlatformTypeClass.OTHERS,
            datasetNameDelimiter=NAME_DELIMITER,
        )
        self._emit(MetadataChangeProposalWrapper(entityUrn=PLATFORM_URN, aspect=info), PLATFORM_URN)
        return PLATFORM_URN

    def emit_dataset(self, asset: CatalogAsset) -> str:
        """Publish one governed asset as a DataHub dataset, and return its URN.

        Three aspects, each a separate Metadata Change Proposal against the same
        URN: what the dataset is and the canonical identity it carries, which
        lifecycle stage it belongs to, and — where a contract declares one — its
        schema. Sending them separately is DataHub's model rather than a choice;
        an aspect is the unit a write replaces.
        """
        identifier = AssetIdentifier.parse(asset.identifier)
        urn = dataset_urn(identifier)

        properties = DatasetPropertiesClass(
            name=asset.display_name,
            description=asset.description,
            qualifiedName=asset.identifier,
            customProperties=custom_properties(asset),
        )
        aspects: list[Any] = [properties, SubTypesClass(typeNames=[subtype(asset)])]
        if asset.columns:
            aspects.append(
                SchemaMetadataClass(
                    schemaName=dataset_name(identifier),
                    platform=PLATFORM_URN,
                    version=0,
                    hash="",
                    platformSchema=OtherSchemaClass(rawSchema=""),
                    fields=[
                        SchemaFieldClass(
                            fieldPath=column.name,
                            # Both contract-backed datasets are CSV, where
                            # every value arrives as text. DataHub is told the
                            # field is read as a string and the contract's
                            # declared type is kept as the native one, so the
                            # catalogue does not claim a parse nobody performed.
                            type=SchemaFieldDataTypeClass(type=StringTypeClass()),
                            nativeDataType=column.data_type,
                            description=column.description,
                        )
                        for column in asset.columns
                    ],
                )
            )

        for aspect in aspects:
            self._emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect), urn)
        return urn

    def emit_upstreams(self, *, urn: str, upstream_urns: tuple[str, ...]) -> None:
        """Record every dataset that is upstream of one dataset, in one write.

        DataHub's ``upstreamLineage`` aspect is the whole upstream list, not one
        edge, so the three raw inputs of the quality report are sent together.
        Sending them one at a time would leave the report with one upstream and
        no error: each write would replace the last.
        """
        aspect = UpstreamLineageClass(
            upstreams=[
                UpstreamClass(dataset=upstream, type=DatasetLineageTypeClass.COPY)
                for upstream in upstream_urns
            ]
        )
        self._emit(MetadataChangeProposalWrapper(entityUrn=urn, aspect=aspect), urn)

    def flush(self) -> None:
        """Make sure everything proposed so far has actually been sent."""
        self._call("flush the emitter", lambda: self._rest_emitter().flush())

    def get_dataset_properties(self, urn: str) -> DatasetPropertiesClass | None:
        """Read one dataset's properties back, or ``None`` if DataHub has none.

        This is the verification half of publication: it asks the catalogue what
        it holds rather than trusting what was sent.
        """
        aspect: DatasetPropertiesClass | None = self._call(
            f"read {urn}",
            lambda: self._client().get_aspect(urn, DatasetPropertiesClass),
        )
        return aspect

    def get_upstreams(self, urn: str) -> tuple[str, ...]:
        """The URNs DataHub holds as upstream of one dataset."""
        aspect = self._call(
            f"read the lineage of {urn}",
            lambda: self._client().get_aspect(urn, UpstreamLineageClass),
        )
        if aspect is None:
            return ()
        return tuple(upstream.dataset for upstream in aspect.upstreams)

    def _emit(self, proposal: MetadataChangeProposalWrapper, urn: str) -> None:
        self._call(f"write to {urn}", lambda: self._rest_emitter().emit(proposal))

    def _rest_emitter(self) -> Any:
        if self._emitter is None:
            self._emitter = DatahubRestEmitter(
                gms_server=self._config.gms_url,
                token=self._config.token,
                timeout_sec=self._timeout,
            )
        return self._emitter

    def _client(self) -> Any:
        """The read side. Constructing it tests the connection, so it is lazy."""
        if self._graph is None:
            self._graph = self._call(
                "connect",
                lambda: DataHubGraph(
                    DatahubClientConfig(
                        server=self._config.gms_url,
                        token=self._config.token,
                        timeout_sec=self._timeout,
                    )
                ),
            )
        return self._graph

    def _call(self, what: str, action: Callable[[], T]) -> T:
        """Run one SDK call, and turn every failure into a `CatalogError`.

        The SDK raises its own exception types over a stopped server, a rejected
        token and a refused write alike, and they are not part of this project's
        vocabulary. Callers get a message naming what was being attempted and
        where; the token appears in no branch of this method's output.
        """
        try:
            result: T = action()
        except CatalogError:
            # Already explained, by an inner call such as connecting.
            raise
        except Exception as exc:
            raise CatalogError(self._explanation(what, exc)) from exc
        return result

    def _explanation(self, what: str, exc: Exception) -> str:
        detail = str(exc).strip() or exc.__class__.__name__
        message = f"cannot {what} in DataHub at {self._config.gms_url}: {detail[:500]}"
        if "401" in detail or "403" in detail or "unauthor" in detail.lower():
            return f"{message} (the token is {self._config.token_hint})"
        return message
