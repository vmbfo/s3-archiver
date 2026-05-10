"""Reusable health-check test helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import cast

from botocore.exceptions import ClientError, EndpointConnectionError


class SuccessfulClient:
    """Minimal client double for successful requests."""

    called_bucket: str | None = None
    _versioning_status: str | None

    def __init__(self, versioning_status: str | None = "Enabled") -> None:
        self._versioning_status = versioning_status

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        self.called_bucket = Bucket

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        self.called_bucket = Bucket
        if self._versioning_status is None:
            return {}
        return {"Status": self._versioning_status}


class AuthFailingClient:
    """Minimal client double for authentication failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket
        raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "HeadBucket")

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        return {"Status": "Enabled"}


class ConnectivityFailingClient:
    """Minimal client double for connectivity failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket
        raise EndpointConnectionError(endpoint_url="http://localstack:4566")

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        return {"Status": "Enabled"}


class VersioningFailingClient:
    """Minimal client double for source versioning failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "GetBucketVersioning")


def multi_route_env(env: dict[str, str]) -> dict[str, str]:
    updated = dict(env)
    routes = cast(list[dict[str, object]], json.loads(updated["ARCHIVER_CONFIG_JSON"]))
    first = routes[0]
    second = deepcopy(first)
    second["name"] = "secondary"
    second["parser"] = "direct"
    second["copy_mode"] = "direct"
    second_source = cast(dict[str, object], second["source"])
    second_source["bucket"] = "second-source-bucket"
    second_source["path"] = "raw/"
    second_destination = cast(dict[str, object], second["destination"])
    second_destination["bucket"] = "second-destination-bucket"
    second_destination["path"] = "mirror/"
    routes.append(second)
    updated["ARCHIVER_CONFIG_JSON"] = json.dumps(routes)
    return updated
