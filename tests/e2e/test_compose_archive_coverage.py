"""Additional compose archive coverage for temp file transfers."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Literal, TypedDict, cast

import pytest
from s3_archiver_core.s3 import S3Client

from tests.e2e.compose_helpers import run_compose
from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    compose_runtime_log_dir,
    localstack_test_env,
)
from tests.integration.localstack_object_helpers import (
    listed_keys,
    localstack_s3_client,
    read_tar_gz_members_text,
)

_COMPOSE_RETRYABLE_MESSAGES = (
    "HeadBucket operation: Not Found",
    "Connection was closed before we received a valid response",
    'optional dependency "localstack" failed to start',
    "exited (137)",
    "unable to upgrade to tcp, received 404",
    "app is missing dependency localstack",
)
_COMPOSE_RETRYABLE_RETURNCODES = (137,)


class TempFileProbePayload(TypedDict):
    ok: bool
    strategy: str
    temp_dir_files: list[str]


@pytest.mark.e2e()
def test_compose_runtime_probe_executes_temp_file_backed_transfer(
    tmp_path: Path,
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    bucket_pair = localstack_bucket_pair
    key = "compose-temp-file/2099-11-02T00-00-00-runtime.txt"
    archive_key = "compose-temp-file/2099-11-02.tar.gz"
    env_file = _write_archive_env_file(tmp_path, bucket_pair)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)
    probe = textwrap.dedent(
        """
        /opt/venv/bin/python - <<'PY'
        import json
        import os
        import sys
        from datetime import UTC, datetime
        from pathlib import Path

        from botocore.exceptions import ClientError
        from s3_archiver_core.archive import ArchiveRoute, run_archive
        from s3_archiver_core.archive_s3 import S3ArchiveBucket
        from s3_archiver_core.s3 import S3TransferCapabilities, build_s3_client
        from s3_archiver_core.settings import AppSettings

        settings = AppSettings.from_env(dict(os.environ))
        temp_dir = Path("/tmp/s3-archiver-compose-temp-file")
        key = "compose-temp-file/2099-11-02T00-00-00-runtime.txt"
        decisions = []
        source_client = build_s3_client(settings.routes[0].source)
        destination_client = build_s3_client(settings.routes[0].destination)

        for client, bucket in (
            (source_client, settings.routes[0].source.bucket),
            (destination_client, settings.routes[0].destination.bucket),
        ):
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise

        source_client.put_object(Bucket=settings.routes[0].source.bucket, Key=key, Body=b"probe\\n")
        destination = S3ArchiveBucket(
            destination_client,
            settings.routes[0].destination.bucket,
            temp_dir,
        )
        route = settings.routes[0]
        source = S3ArchiveBucket(source_client, settings.routes[0].source.bucket, temp_dir)
        result = run_archive(
            (
                ArchiveRoute(
                    route.name,
                    source,
                    destination,
                    parser_kind=route.parser.value,
                    copy_mode=route.copy_mode.value,
                    source_path=route.source.path,
                    destination_path=route.destination.path,
                    source_identity=route.source.storage_identity(),
                    destination_identity=route.destination.storage_identity(),
                    transfer_capabilities=S3TransferCapabilities(
                    native_copy=False,
                    multipart_copy=False,
                    streaming_upload=True,
                    temp_file_backed=True,
                    streaming_limit_bytes=1,
                ),
                ),
            ),
            run_timeout=settings.run_timeout,
            run_started_at_utc=datetime(2100, 1, 1, tzinfo=UTC),
            debug_logger=lambda _entry, strategy: decisions.append(strategy),
        )
        if not result.ok:
            failures = (
                list(result.list.failures)
                + list(result.copy.failures)
                + list(result.verify.failures)
            )
            print("\\n".join(failures), file=sys.stderr)
            raise SystemExit(1)
        files = [] if not temp_dir.exists() else sorted(path.name for path in temp_dir.iterdir())
        payload = {
            "ok": result.ok,
            "strategy": decisions[0] if decisions else "missing",
            "temp_dir_files": files,
        }
        print(json.dumps(payload, sort_keys=True))
        PY
        """
    ).strip()

    result = _run_compose(
        run_env,
        "run",
        "--no-deps",
        "--rm",
        "--entrypoint",
        "sh",
        "app",
        "-lc",
        probe,
    )
    payload = _temp_file_payload(result.stdout)

    assert payload["ok"] is True
    assert payload["strategy"] == "deterministic_tar_gzip"
    assert payload["temp_dir_files"] == []
    assert listed_keys(_client(tmp_path, bucket_pair, "destination"), bucket_pair.destination) == {
        archive_key
    }
    assert read_tar_gz_members_text(
        _client(tmp_path, bucket_pair, "destination"),
        bucket_pair.destination,
        archive_key,
    ) == {key: "probe\n"}


def _write_archive_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
    )
    env_file = tmp_path / "compose-coverage.env"
    _ = env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    return env_file


def _client(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    side: Literal["source", "destination"],
) -> S3Client:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_HOST_ENDPOINT,
        log_dir=str(tmp_path / "host-logs"),
    )
    return localstack_s3_client(env, side)


def _run_compose(
    env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return run_compose(
        env,
        *args,
        check=check,
        retryable_messages=_COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=_COMPOSE_RETRYABLE_RETURNCODES,
    )


def _temp_file_payload(output: str) -> TempFileProbePayload:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(TempFileProbePayload, json.loads(json_line))
