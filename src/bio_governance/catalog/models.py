"""Configuration and result models for publishing to a metadata catalogue.

The catalogue is the first layer in this project that talks to something
outside the working directory, so the models here are about two things the
earlier layers never needed: where the server is and how to authenticate, and
what one publication actually did.

Two catalogues are published to, and they get one configuration model each
because they are configured differently — OpenMetadata insists on a token for
every write, DataHub's local quickstart does not. Everything downstream of the
connection is shared: an asset, a column, an edge and a publication mean the
same thing whichever catalogue they are sent to, which is what makes the two
integrations comparable at all.

Nothing here knows HTTP. The ``mapping`` modules decide what to publish, the
``client`` modules send it, and the ``publish`` modules put the two together.
"""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: Where a local Docker quickstart answers. The path is included because every
#: OpenMetadata route lives under ``/api``; the client appends ``/v1/...``.
DEFAULT_HOST = "http://localhost:8585/api"

#: The environment variables the CLI reads. A token is never a flag, so it
#: cannot end up in a shell history or a process listing.
HOST_VAR = "OPENMETADATA_HOST"
TOKEN_VAR = "OPENMETADATA_JWT_TOKEN"

#: Where a local DataHub quickstart's GMS answers. No path: DataHub's routes
#: are rooted at the server, and the SDK appends its own.
DATAHUB_DEFAULT_GMS_URL = "http://localhost:8080"

#: DataHub's own two variables, named the way its documentation and its CLI
#: name them, so a reader who already runs DataHub does not have to learn ours.
DATAHUB_GMS_VAR = "DATAHUB_GMS_URL"
DATAHUB_TOKEN_VAR = "DATAHUB_GMS_TOKEN"


class CatalogError(Exception):
    """The catalogue could not be reached, authenticated to, or written to."""


class FileFormat(StrEnum):
    """The file formats this project publishes assets in.

    OpenMetadata's ``containerFileFormat`` vocabulary is longer than this, and
    DataHub does not constrain the string at all. Only the two formats the
    governed outputs are actually written in are named, so no catalogue entry
    can claim a format nothing here produces.
    """

    CSV = "csv"
    JSON = "json"


class OpenMetadataConfig(BaseModel):
    """Where OpenMetadata is and how to prove who we are.

    ``token`` is optional because reachability is not an authenticated
    question: ``health`` should be able to say "the server is up but I have no
    token" rather than refusing to look. Every write demands one, and
    :meth:`require_token` is where that demand is made.
    """

    model_config = ConfigDict(frozen=True)

    host: str = Field(default=DEFAULT_HOST, min_length=1)
    token: str | None = None

    @classmethod
    def from_env(cls) -> OpenMetadataConfig:
        """Read the configuration from the environment, with the local default."""
        host = os.environ.get(HOST_VAR) or DEFAULT_HOST
        token = os.environ.get(TOKEN_VAR) or None
        return cls(host=host.rstrip("/"), token=token)

    def require_token(self) -> str:
        """The token, or a message saying exactly what to set and where from."""
        if not self.token:
            raise CatalogError(
                f"no OpenMetadata token: set {TOKEN_VAR} to a JWT for a bot or admin user "
                "(see docs/openmetadata.md for the one local step that obtains one)"
            )
        return self.token

    @property
    def token_hint(self) -> str:
        """A safe thing to print. Never the token, and never most of it."""
        if not self.token:
            return "not set"
        return f"set ({len(self.token)} characters, ending {self.token[-4:]})"


class CatalogColumn(BaseModel):
    """One column of a container's data model, in OpenMetadata's vocabulary."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    description: str | None = None


class CatalogAsset(BaseModel):
    """One governed asset, as it will be published to a catalogue.

    ``identifier`` is the project's own ``bio://`` identity and ``name`` is the
    OpenMetadata entity name derived from it. Both are kept: the derivation is
    one-way by design, and no catalogue is allowed to become the place where
    asset identity is defined. DataHub derives its own dataset name from
    ``identifier`` in the same one-way fashion and ignores ``name``, because two
    catalogues with different naming rules must not be made to share one
    derivation.
    """

    model_config = ConfigDict(frozen=True)

    identifier: str = Field(min_length=1)
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    file_format: FileFormat
    columns: tuple[CatalogColumn, ...] = ()
    size_bytes: int | None = None


class LineageEdge(BaseModel):
    """A directed edge between two published assets, named by ``bio://`` URI."""

    model_config = ConfigDict(frozen=True)

    from_identifier: str = Field(min_length=1)
    to_identifier: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"{self.from_identifier} -> {self.to_identifier}"


class PublishedCatalog(BaseModel):
    """What one publication did, for the CLI to print and the tests to assert on."""

    model_config = ConfigDict(frozen=True)

    study_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    assets: tuple[CatalogAsset, ...] = Field(min_length=1)
    edges: tuple[LineageEdge, ...] = Field(min_length=1)
    lineage_run_id: str | None = None


class DataHubConfig(BaseModel):
    """Where DataHub's metadata service is and, if it asks, how to prove who we are.

    The difference from :class:`OpenMetadataConfig` is not cosmetic. A default
    local DataHub quickstart has metadata-service authentication switched off,
    so a token is genuinely optional here rather than merely optional for the
    health check: publishing works without one until the deployment is
    configured to demand it. The token is still read from the environment and
    still never printed, because a token that is optional today is not optional
    on the deployment this code will meet next.
    """

    model_config = ConfigDict(frozen=True)

    gms_url: str = Field(default=DATAHUB_DEFAULT_GMS_URL, min_length=1)
    token: str | None = None

    @classmethod
    def from_env(cls) -> DataHubConfig:
        """Read the configuration from the environment, with the local default."""
        gms_url = os.environ.get(DATAHUB_GMS_VAR) or DATAHUB_DEFAULT_GMS_URL
        token = os.environ.get(DATAHUB_TOKEN_VAR) or None
        return cls(gms_url=gms_url.rstrip("/"), token=token)

    @property
    def token_hint(self) -> str:
        """A safe thing to print. Never the token, and never most of it."""
        if not self.token:
            return "not set (a default local DataHub does not require one)"
        return f"set ({len(self.token)} characters, ending {self.token[-4:]})"
