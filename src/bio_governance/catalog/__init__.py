"""Publishing governed assets to a local metadata catalogue.

Two of them: OpenMetadata and DataHub, side by side and neither behind an
interface. What is exported here is everything either publication needs that
does not cost anything to import. ``DataHubClient`` and
``publish_study_to_datahub`` are deliberately *not* exported: they pull in the
DataHub SDK's metadata model, which costs about half a second, and every
``bio-gov`` command — including the six the pipeline shells out to on every run
— would pay it for nothing. Their callers import them by module, the way the
CLI already imports the MCP server.
"""

from bio_governance.catalog.client import OpenMetadataClient
from bio_governance.catalog.datahub_mapping import (
    CANONICAL_PROPERTY,
    ENVIRONMENT,
    PLATFORM_NAME,
    PLATFORM_URN,
    custom_properties,
    dataset_name,
    dataset_urn,
    study_urns,
    subtype,
    upstreams,
)
from bio_governance.catalog.mapping import (
    SERVICE_NAME,
    SERVICE_TYPE,
    entity_name,
    fully_qualified_name,
    lineage_edges,
    prepare_assets,
    study_identifiers,
)
from bio_governance.catalog.models import (
    DATAHUB_DEFAULT_GMS_URL,
    DATAHUB_GMS_VAR,
    DATAHUB_TOKEN_VAR,
    DEFAULT_HOST,
    HOST_VAR,
    TOKEN_VAR,
    CatalogAsset,
    CatalogColumn,
    CatalogError,
    DataHubConfig,
    FileFormat,
    LineageEdge,
    OpenMetadataConfig,
    PublishedCatalog,
)
from bio_governance.catalog.publish import DEFAULT_CONTRACT_DIR, publish_study

__all__ = [
    "CANONICAL_PROPERTY",
    "DATAHUB_DEFAULT_GMS_URL",
    "DATAHUB_GMS_VAR",
    "DATAHUB_TOKEN_VAR",
    "DEFAULT_CONTRACT_DIR",
    "DEFAULT_HOST",
    "ENVIRONMENT",
    "HOST_VAR",
    "PLATFORM_NAME",
    "PLATFORM_URN",
    "SERVICE_NAME",
    "SERVICE_TYPE",
    "TOKEN_VAR",
    "CatalogAsset",
    "CatalogColumn",
    "CatalogError",
    "DataHubConfig",
    "FileFormat",
    "LineageEdge",
    "OpenMetadataClient",
    "OpenMetadataConfig",
    "PublishedCatalog",
    "custom_properties",
    "dataset_name",
    "dataset_urn",
    "entity_name",
    "fully_qualified_name",
    "lineage_edges",
    "prepare_assets",
    "publish_study",
    "study_identifiers",
    "study_urns",
    "subtype",
    "upstreams",
]
