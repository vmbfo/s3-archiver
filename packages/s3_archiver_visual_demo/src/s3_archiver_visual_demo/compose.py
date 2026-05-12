"""Compose-backed CLI for the manual visual demo."""

from __future__ import annotations

import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from s3_archiver_cli.archive_payload_utils import JsonValue
from s3_archiver_core.s3 import S3Client
from s3_archiver_core.settings import AppSettings
from s3_archiver_localstack_support import last_json_object
from s3_archiver_localstack_support.buckets import (
    delete_localstack_bucket_pair,
    ensure_localstack_bucket_pair,
    localstack_admin_client,
)
from s3_archiver_localstack_support.compose import (
    find_repo_root,
    run_app_compose,
)
from s3_archiver_localstack_support.harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    LocalstackS3AdminClient,
    compose_runtime_log_dir,
    localstack_compose_env,
    localstack_test_env,
    new_localstack_bucket_pair,
    write_localstack_env_file,
)
from s3_archiver_localstack_support.objects import listed_keys, localstack_s3_client
from s3_archiver_localstack_support.readiness import wait_for_localstack_readiness

from s3_archiver_visual_demo import terminal
from s3_archiver_visual_demo.data import (
    DEMO_ARCHIVE_START_AGE_DAYS,
    DEMO_SEEDED_OBJECT_COUNT,
    archive_demo_days,
    demo_config_json,
    expected_archive_members,
    expected_direct_destination_keys,
    invalid_demo_keys,
    newer_demo_keys,
    seed_daily_demo_objects,
    target_day_demo_cases,
)
from s3_archiver_visual_demo.summary import print_verified_summary
from s3_archiver_visual_demo.verify import verify_demo_result
from s3_archiver_visual_demo.walkthrough import run_visual_demo as run_visual_walkthrough

_DEMO_PREFIX = "compose-demo"


def run(*, keep_compose: bool = False) -> None:
    """Run the compose-backed visual demo and verify its output."""

    repo_root = find_repo_root()
    with tempfile.TemporaryDirectory(prefix="s3-archiver-visual-demo-") as temp_dir:
        tmp_path = Path(temp_dir)
        bucket_pair = new_localstack_bucket_pair()
        compose_env = _compose_env(tmp_path, bucket_pair)
        localstack_endpoint = compose_env.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT)
        _ = run_demo_compose(compose_env, "down", "-v", "--remove-orphans", check=False)
        demo_error: Exception | None = None
        cleanup_error: Exception | None = None
        try:
            _ = run_demo_compose(compose_env, "up", "-d", "localstack")
            wait_for_localstack_readiness(
                endpoint=localstack_endpoint,
                log_dir=str(repo_root / ".local" / "visual-demo-readiness"),
            )
            _ensure_bucket_pair(tmp_path, bucket_pair, localstack_endpoint)
            _seed_run_and_verify(tmp_path, repo_root, compose_env, bucket_pair, localstack_endpoint)
        except Exception as exc:
            demo_error = exc
            raise
        finally:
            try:
                _delete_bucket_pair(tmp_path, bucket_pair, localstack_endpoint)
            except RuntimeError as exc:
                cleanup_error = exc
            finally:
                if not keep_compose:
                    _ = run_demo_compose(compose_env, "down", "-v", "--remove-orphans", check=False)
            if cleanup_error is not None and demo_error is None:
                raise cleanup_error


def run_demo_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Docker Compose with visual-demo retry rules."""

    return run_app_compose(
        env,
        *args,
        check=check,
        repo_root=find_repo_root() if repo_root is None else repo_root,
    )


def _seed_run_and_verify(
    tmp_path: Path,
    repo_root: Path,
    compose_env: dict[str, str],
    bucket_pair: LocalstackBucketPair,
    localstack_endpoint: str,
) -> None:
    source_client = _demo_client(tmp_path, bucket_pair, "source", localstack_endpoint)
    destination_client = _demo_client(tmp_path, bucket_pair, "destination", localstack_endpoint)
    seed_now = datetime.now(tz=UTC)
    target_day = (seed_now.astimezone(UTC) - timedelta(days=DEMO_ARCHIVE_START_AGE_DAYS)).date()
    archive_days = archive_demo_days(seed_now)
    demo_cases = [case for day in archive_days for case in target_day_demo_cases(_DEMO_PREFIX, day)]
    source_keys = (
        {case.key for case in demo_cases}
        | set(newer_demo_keys(_DEMO_PREFIX, target_day))
        | set(invalid_demo_keys(_DEMO_PREFIX, target_day))
    )
    archive_members = expected_archive_members(_DEMO_PREFIX, archive_days)
    archive_keys = set(archive_members)
    direct_keys = expected_direct_destination_keys(_DEMO_PREFIX, archive_days)
    source_by_destination = {
        case.destination_key: case.key for case in demo_cases if case.route.copy_mode == "direct"
    }
    seed_daily_demo_objects(
        source_client,
        bucket_pair.source,
        prefix=_DEMO_PREFIX,
        seed_now=seed_now,
    )
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(_write_demo_env_file(tmp_path, bucket_pair))
    run_env["ARCHIVER_CONFIG_JSON"] = demo_config_json(bucket_pair, prefix=_DEMO_PREFIX)
    terminal.print_image_build_intro()
    build_result = run_demo_compose(run_env, "build", "app", check=False)
    if build_result.returncode != 0:
        raise RuntimeError(terminal.build_failure_message(build_result.stdout, build_result.stderr))
    terminal.print_demo_intro(seeded_count=DEMO_SEEDED_OBJECT_COUNT)
    demo_lines: list[str] = []
    printer = terminal.VisualDemoPrinter(DEMO_ARCHIVE_START_AGE_DAYS)

    def emit(line: str) -> None:
        demo_lines.append(line)
        printer.emit(line)

    host_env = _demo_host_env(tmp_path, bucket_pair, localstack_endpoint)
    host_env["ARCHIVER_CONFIG_JSON"] = demo_config_json(bucket_pair, prefix=_DEMO_PREFIX)
    host_settings = AppSettings.from_env(host_env)
    payload = run_visual_walkthrough(
        host_settings,
        host_settings.log_dir / "s3-archiver-visual-demo.log",
        archive_runner=lambda _settings, _log_file: _run_archive(run_env, repo_root),
        emit=emit,
    )
    printer.finish()
    output = "\n".join(demo_lines)
    verify_demo_result(
        output=output,
        payload=cast(dict[str, object], payload),
        destination_client=destination_client,
        bucket_pair=bucket_pair,
        archive_days=archive_days,
        archive_members=archive_members,
        direct_keys=direct_keys,
        source_by_destination=source_by_destination,
        source_keys=source_keys,
    )
    actual_source_keys = listed_keys(source_client, bucket_pair.source)
    if actual_source_keys != source_keys:
        raise RuntimeError(f"unexpected source keys: {actual_source_keys!r} != {source_keys!r}")
    print_verified_summary(
        cast(dict[str, object], payload),
        total_count=DEMO_SEEDED_OBJECT_COUNT,
        copied_count=len(archive_keys | direct_keys),
        remaining_source_count=len(source_keys),
    )


def _compose_env(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    return localstack_compose_env(
        bucket_pair,
        app_env_file=_write_demo_env_file(tmp_path, bucket_pair),
    )


def _write_demo_env_file(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> Path:
    return write_localstack_env_file(
        tmp_path,
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
        filename="compose-demo.env",
        overrides={
            "ARCHIVER_CONFIG_JSON": demo_config_json(bucket_pair, prefix=_DEMO_PREFIX),
            "LOG_LEVEL": "WARNING",
        },
    )


def _demo_client(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    side: Literal["source", "destination"],
    endpoint: str,
) -> S3Client:
    return localstack_s3_client(_demo_host_env(tmp_path, bucket_pair, endpoint), side)


def _ensure_bucket_pair(tmp_path: Path, bucket_pair: LocalstackBucketPair, endpoint: str) -> None:
    ensure_localstack_bucket_pair(_demo_admin_client(tmp_path, bucket_pair, endpoint), bucket_pair)


def _delete_bucket_pair(tmp_path: Path, bucket_pair: LocalstackBucketPair, endpoint: str) -> None:
    delete_localstack_bucket_pair(
        _demo_admin_client(tmp_path, bucket_pair, endpoint),
        bucket_pair,
        context="visual demo buckets",
    )


def _demo_host_env(
    tmp_path: Path, bucket_pair: LocalstackBucketPair, endpoint: str
) -> dict[str, str]:
    return localstack_test_env(
        bucket_pair,
        endpoint=endpoint,
        log_dir=str(tmp_path / "host-logs"),
    )


def _demo_admin_client(
    tmp_path: Path, bucket_pair: LocalstackBucketPair, endpoint: str
) -> LocalstackS3AdminClient:
    return localstack_admin_client(
        AppSettings.from_env(_demo_host_env(tmp_path, bucket_pair, endpoint))
    )


def _run_archive(env: dict[str, str], repo_root: Path) -> dict[str, JsonValue]:
    result = run_demo_compose(
        env,
        "run",
        "--rm",
        "-e",
        "ARCHIVER_CONFIG_JSON",
        "app",
        "archive",
        check=False,
        repo_root=repo_root,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "\n".join(
                (
                    f"archive command failed with exit code {result.returncode}",
                    f"stdout:\n{result.stdout}",
                    f"stderr:\n{result.stderr}",
                )
            )
        )
    return cast(dict[str, JsonValue], _demo_payload(result.stdout))


def _demo_payload(output: str) -> dict[str, object]:
    return last_json_object(output)
