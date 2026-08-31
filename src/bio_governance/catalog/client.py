"""A small REST client for a local OpenMetadata instance.

OpenMetadata ships an official Python SDK, ``openmetadata-ingestion``. It is
not used here. Resolving it for this project's Python brings in around 130
transitive packages — dbt-core, boto3, grpcio, numpy and the Kubernetes client
among them — for a milestone that issues five kinds of request against four
documented endpoints. The published REST API is the same interface the SDK
calls, so this module calls it directly over ``httpx`` and the project keeps a
dependency list a reader can hold in their head.

Every write is a ``PUT``. OpenMetadata's ``PUT`` routes are create-or-update, so
publishing twice updates the same entities instead of creating a second set;
idempotence is a property of the requests, not of bookkeeping this module does.

The token is never logged, never echoed and never included in an error message.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from bio_governance.catalog.models import CatalogAsset, CatalogError, OpenMetadataConfig

#: How long any single call may take. A local server that has not answered in
#: half a minute is not a slow server, it is a stopped one.
DEFAULT_TIMEOUT = 30.0

#: OpenMetadata's entity type name for a container, used by the lineage API.
CONTAINER_TYPE = "container"


class OpenMetadataClient:
    """Talks to one OpenMetadata instance over its REST API.

    Constructed with an :class:`OpenMetadataConfig`. Pass ``transport`` to run
    the client against something other than a live server; the tests use it to
    assert on the requests this module makes.
    """

    def __init__(
        self,
        config: OpenMetadataConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.host,
            timeout=timeout,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    def __enter__(self) -> OpenMetadataClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def version(self) -> str:
        """The running server's version, and so a proof it is reachable.

        Deliberately unauthenticated: "is the server up?" and "do I have a
        token?" are different questions, and answering the first without the
        second is what makes ``health`` useful while a token is being obtained.
        """
        payload = self._request("GET", "/v1/system/version", authenticated=False)
        version = payload.get("version")
        if not isinstance(version, str):
            raise CatalogError(
                f"{self._config.host} answered /v1/system/version without a version: "
                "it is probably not an OpenMetadata server"
            )
        return version

    def upsert_storage_service(
        self,
        *,
        name: str,
        service_type: str,
        display_name: str,
        description: str,
    ) -> str:
        """Create or update the storage service, and return its FQN.

        The connection config carries only its own discriminating ``type``.
        There is nothing for OpenMetadata to connect *to*: the files are local
        pipeline output and this project pushes them, rather than asking the
        catalogue to go and crawl them.
        """
        payload = self._request(
            "PUT",
            "/v1/services/storageServices",
            json={
                "name": name,
                "displayName": display_name,
                "description": description,
                "serviceType": service_type,
                "connection": {"config": {"type": service_type}},
            },
        )
        return _text(payload, "fullyQualifiedName", "storage service")

    def upsert_container(self, asset: CatalogAsset, *, service: str) -> str:
        """Create or update one container, and return its entity ID.

        The ID is what the lineage API works in, so publishing an edge needs
        the containers to exist first.
        """
        body: dict[str, Any] = {
            "name": asset.name,
            "displayName": asset.display_name,
            "description": asset.description,
            "service": service,
            "fullPath": asset.identifier,
            "fileFormats": [asset.file_format.value],
            "numberOfObjects": 1,
        }
        if asset.size_bytes is not None:
            body["size"] = asset.size_bytes
        if asset.columns:
            body["dataModel"] = {
                "isPartitioned": False,
                "columns": [
                    {
                        "name": column.name,
                        "dataType": column.data_type,
                        **({"description": column.description} if column.description else {}),
                    }
                    for column in asset.columns
                ],
            }

        payload = self._request("PUT", "/v1/containers", json=body)
        return _text(payload, "id", f"container {asset.name}")

    def add_lineage(self, *, from_id: str, to_id: str) -> None:
        """Record that one container is upstream of another.

        ``PUT /v1/lineage`` is create-or-update on the edge, so re-publishing
        an edge that already exists leaves one edge behind, not two.
        """
        self._request(
            "PUT",
            "/v1/lineage",
            json={
                "edge": {
                    "fromEntity": {"id": from_id, "type": CONTAINER_TYPE},
                    "toEntity": {"id": to_id, "type": CONTAINER_TYPE},
                }
            },
        )

    def get_container(self, fqn: str) -> dict[str, Any]:
        """Fetch a published container by its fully qualified name."""
        return self._request("GET", f"/v1/containers/name/{fqn}")

    def get_lineage(self, fqn: str, *, upstream: int = 1, downstream: int = 1) -> dict[str, Any]:
        """Fetch the lineage graph around a container, as OpenMetadata holds it."""
        return self._request(
            "GET",
            f"/v1/lineage/{CONTAINER_TYPE}/name/{fqn}",
            params={"upstreamDepth": upstream, "downstreamDepth": downstream},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Issue one request, and turn every failure into a `CatalogError`.

        Callers get a message naming the endpoint and, where the server
        explained itself, what the server said. The token appears in no branch
        of this method's output.
        """
        headers = {}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._config.require_token()}"

        try:
            response = self._client.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise CatalogError(
                f"cannot reach OpenMetadata at {self._config.host}{path}: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise CatalogError(
                f"OpenMetadata rejected the token for {method} {path} "
                f"(HTTP {response.status_code}); the token is {self._config.token_hint}"
            )
        if response.status_code >= 400:
            raise CatalogError(
                f"OpenMetadata returned HTTP {response.status_code} for {method} {path}: "
                f"{_explanation(response)}"
            )

        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise CatalogError(f"{method} {path} did not return JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CatalogError(f"{method} {path} returned {type(payload).__name__}, not an object")
        return payload


def _explanation(response: httpx.Response) -> str:
    """The server's own account of a failure, trimmed to something readable."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = body.get("message") or body.get("responseMessage")
        if isinstance(message, str) and message:
            return message[:500]
    return response.text[:500] or "(no response body)"


def _text(payload: dict[str, Any], key: str, subject: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"OpenMetadata returned no {key} for {subject}")
    return value
