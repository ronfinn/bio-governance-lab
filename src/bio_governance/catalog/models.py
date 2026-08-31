"""Configuration and result models for publishing to a local OpenMetadata.

The catalogue is the first layer in this project that talks to something
outside the working directory, so the models here are about two things the
earlier layers never needed: where the server is and how to authenticate, and
what one publication actually did.

Nothing here knows HTTP. :mod:`bio_governance.catalog.mapping` decides what to
publish, :mod:`bio_governance.catalog.client` sends it, and
:mod:`bio_governance.catalog.publish` puts the two together.
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


class CatalogError(Exception):
    """The catalogue could not be reached, authenticated to, or written to."""


class FileFormat(StrEnum):
    """The OpenMetadata container file formats this project publishes.

    OpenMetadata's ``containerFileFormat`` vocabulary is longer than this. Only
    the two formats the governed outputs are actually written in are named, so
    a container cannot claim a format nothing here produces.
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
    """One governed asset, as it will be published as an OpenMetadata container.

    ``identifier`` is the project's own ``bio://`` identity and ``name`` is the
    OpenMetadata entity name derived from it. Both are kept: the derivation is
    one-way by design, and the catalogue is not allowed to become the place
    where asset identity is defined.
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
