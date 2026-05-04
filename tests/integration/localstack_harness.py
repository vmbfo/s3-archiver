"""LocalStack-only test harness helpers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError

LOCALSTACK_HOST_ENDPOINT = "http://127.0.0.1:4566"
LOCALSTACK_COMPOSE_ENDPOINT = "http://localstack:4566"
LOCALSTACK_ENDPOINT_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "localstack", "localstack-alt", "localhost.localstack.cloud"}
)
_RETRYABLE_LOCALSTACK_ERRORS = (
    "Connection was closed before we received a valid response",
    "Could not connect to the endpoint URL",
)


@dataclass(frozen=True, slots=True)
class LocalstackBucketPair:
    source: str
    destination: str


class LocalstackS3AdminClient(Protocol):
    def head_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        ...

    def create_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        ...

    def list_buckets(self) -> object: ...

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]: ...

    def list_object_versions(self, **kwargs: object) -> dict[str, object]: ...

    def delete_objects(self, *, Bucket: str, Delete: dict[str, object]) -> object:  # noqa: N803
        ...

    def delete_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        ...


def new_localstack_bucket_pair() -> LocalstackBucketPair:
    suffix = uuid.uuid4().hex
    return LocalstackBucketPair(
        source=f"s3-archiver-source-{suffix}",
        destination=f"s3-archiver-destination-{suffix}",
    )


def bucket_pair_from_env(env: dict[str, str]) -> LocalstackBucketPair:
    return LocalstackBucketPair(env["TEST_S3_SOURCE_BUCKET"], env["TEST_S3_DESTINATION_BUCKET"])


def localstack_test_env(
    bucket_pair: LocalstackBucketPair,
    *,
    endpoint: str,
    log_dir: str,
) -> dict[str, str]:
    env = {
        "APP_ENV_FILE": "/dev/null",
        "S3_SOURCE_PROVIDER": "localstack",
        "S3_SOURCE_ACCESS_KEY_ID": "source-test",
        "S3_SOURCE_SECRET_ACCESS_KEY": "source-test",
        "S3_SOURCE_REGION": "us-east-1",
        "S3_SOURCE_BUCKET": bucket_pair.source,
        "S3_SOURCE_ENDPOINT_URL": endpoint,
        "S3_SOURCE_ADDRESSING_STYLE": "path",
        "S3_DESTINATION_PROVIDER": "localstack",
        "S3_DESTINATION_ACCESS_KEY_ID": "destination-test",
        "S3_DESTINATION_SECRET_ACCESS_KEY": "destination-test",
        "S3_DESTINATION_REGION": "us-east-1",
        "S3_DESTINATION_BUCKET": bucket_pair.destination,
        "S3_DESTINATION_ENDPOINT_URL": endpoint,
        "S3_DESTINATION_ADDRESSING_STYLE": "path",
        "ARCHIVER_CONFIG_JSON": _localstack_config_json(bucket_pair, endpoint=endpoint),
        "ARCHIVER_RUN_TIMEOUT": "7d",
        "LOG_LEVEL": "INFO",
        "LOG_DIR": log_dir,
    }
    assert_localstack_test_target(env)
    return env


def compose_runtime_log_dir(bucket_pair: LocalstackBucketPair) -> str:
    return f"/var/log/s3-archiver/{bucket_pair.source}"


def _localstack_config_json(bucket_pair: LocalstackBucketPair, *, endpoint: str) -> str:
    return (
        '[{"name":"localstack-daily","parser":"filename_timestamp",'
        '"copy_mode":"daily_tar_gz",'
        f'"source":{{"provider":"localstack","endpoint_url":"{endpoint}",'
        f'"region":"us-east-1","bucket":"{bucket_pair.source}","path":"",'
        '"access_key_id":"source-test","secret_access_key":"source-test",'
        '"addressing_style":"path"},"destination":{'
        f'"provider":"localstack","endpoint_url":"{endpoint}",'
        f'"region":"us-east-1","bucket":"{bucket_pair.destination}","path":"",'
        '"access_key_id":"destination-test","secret_access_key":"destination-test",'
        '"addressing_style":"path"}}]'
    )


def write_localstack_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    *,
    endpoint: str,
    log_dir: str,
) -> Path:
    env = localstack_test_env(bucket_pair, endpoint=endpoint, log_dir=log_dir)
    env_file = tmp_path / "localstack.env"
    _ = env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    return env_file


def assert_localstack_test_target(env: dict[str, str]) -> None:
    for field in ("S3_SOURCE_PROVIDER", "S3_DESTINATION_PROVIDER"):
        if env.get(field) != "localstack":
            raise RuntimeError(f"{field} must be 'localstack' for integration/e2e tests")
    for field in ("S3_SOURCE_ENDPOINT_URL", "S3_DESTINATION_ENDPOINT_URL"):
        _assert_localstack_endpoint(field, env.get(field))


def ensure_localstack_bucket(client: LocalstackS3AdminClient, bucket: str) -> None:
    try:
        _ = client.create_bucket(Bucket=bucket)
    except ClientError as exc:
        error_obj: object = exc.response.get("Error", {})
        error = cast(dict[str, object], error_obj)
        if error.get("Code") not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise


def delete_localstack_bucket(client: LocalstackS3AdminClient, bucket: str) -> None:
    for attempt in range(5):
        try:
            _delete_all_versions(client, bucket)
            _delete_current_objects(client, bucket)
            _ = client.delete_bucket(Bucket=bucket)
            return
        except (BotoCoreError, ClientError) as exc:
            if (
                isinstance(exc, ClientError)
                and exc.response.get("Error", {}).get("Code") == "NoSuchBucket"
            ):
                return
            if attempt == 4 or not _is_retryable_localstack_error(exc):
                raise RuntimeError(
                    f"Failed to delete LocalStack test bucket {bucket!r}: {exc}"
                ) from exc
            time.sleep(0.5)


def _assert_localstack_endpoint(field: str, endpoint: str | None) -> None:
    if endpoint is None:
        raise RuntimeError(f"{field} must be set for integration/e2e tests")
    host = urlparse(endpoint).hostname
    if host not in LOCALSTACK_ENDPOINT_HOSTS:
        raise RuntimeError(f"{field} host {host!r} is not allowed for integration/e2e tests")


def _delete_all_versions(client: LocalstackS3AdminClient, bucket: str) -> None:
    key_marker: str | None = None
    version_marker: str | None = None
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "MaxKeys": 1000}
        if key_marker is not None:
            kwargs["KeyMarker"] = key_marker
        if version_marker is not None:
            kwargs["VersionIdMarker"] = version_marker
        page = client.list_object_versions(**kwargs)
        objects = _version_delete_entries(page)
        if objects:
            _ = client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        if page.get("IsTruncated") is not True:
            return
        key_marker = _optional_string(page.get("NextKeyMarker"))
        version_marker = _optional_string(page.get("NextVersionIdMarker"))


def _delete_current_objects(client: LocalstackS3AdminClient, bucket: str) -> None:
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": "", "MaxKeys": 1000}
        if continuation_token is not None:
            kwargs["ContinuationToken"] = continuation_token
        page = client.list_objects_v2(**kwargs)
        objects = _current_delete_entries(page)
        if objects:
            _ = client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        if page.get("IsTruncated") is not True:
            return
        continuation_token = _optional_string(page.get("NextContinuationToken"))


def _version_delete_entries(page: dict[str, object]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for section in ("Versions", "DeleteMarkers"):
        for item in _object_entries(page.get(section)):
            key = _optional_string(item.get("Key"))
            version_id = _optional_string(item.get("VersionId"))
            if key is not None and version_id is not None:
                entries.append({"Key": key, "VersionId": version_id})
    return entries


def _current_delete_entries(page: dict[str, object]) -> list[dict[str, str]]:
    return [
        {"Key": key}
        for item in _object_entries(page.get("Contents"))
        if (key := _optional_string(item.get("Key"))) is not None
    ]


def _object_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    entries = cast(list[object], value)
    return [cast(dict[str, object], entry) for entry in entries if isinstance(entry, dict)]


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_retryable_localstack_error(exc: Exception) -> bool:
    message = str(exc)
    return any(detail in message for detail in _RETRYABLE_LOCALSTACK_ERRORS)
