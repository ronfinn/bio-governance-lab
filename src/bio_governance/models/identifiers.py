"""Stable, URI-style identity for governed assets."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, model_serializer, model_validator

SCHEME = "bio"

#: Study or domain code, e.g. ``BIO-001``.
DOMAIN_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")

#: A single path segment, e.g. ``raw`` or ``sample_events``.
SEGMENT_PATTERN = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")

_URI_PATTERN = re.compile(rf"^{SCHEME}://(?P<domain>[^/]+)/(?P<path>.+)$")


class AssetIdentifier(BaseModel):
    """A parsed ``bio://<domain>/<path>`` asset identifier.

    Accepts either the URI string or a mapping of its parts, and always
    serializes back to the canonical URI string::

        bio://BIO-001/raw/samples
    """

    model_config = {"frozen": True}

    domain: str
    path: tuple[str, ...]

    @model_validator(mode="before")
    @classmethod
    def _accept_uri_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _split_uri(value)
        return value

    @model_validator(mode="after")
    def _check_parts(self) -> AssetIdentifier:
        if not DOMAIN_PATTERN.match(self.domain):
            raise ValueError(
                f"invalid domain {self.domain!r}: expected upper-case codes such as 'BIO-001'"
            )
        if not self.path:
            raise ValueError("identifier must have at least one path segment")
        for segment in self.path:
            if not SEGMENT_PATTERN.match(segment):
                raise ValueError(
                    f"invalid path segment {segment!r}: expected lower-case codes such as 'raw'"
                )
        return self

    @model_serializer
    def _serialize(self) -> str:
        return self.uri

    @classmethod
    def parse(cls, uri: str) -> AssetIdentifier:
        """Build an identifier from its URI string form."""
        return cls.model_validate(uri)

    @property
    def uri(self) -> str:
        """The canonical ``bio://`` string form of this identifier."""
        return f"{SCHEME}://{self.domain}/{'/'.join(self.path)}"

    def __str__(self) -> str:
        return self.uri


def _split_uri(uri: str) -> dict[str, Any]:
    match = _URI_PATTERN.match(uri)
    if match is None:
        raise ValueError(f"invalid asset identifier {uri!r}: expected '{SCHEME}://<domain>/<path>'")
    return {"domain": match["domain"], "path": tuple(match["path"].split("/"))}
