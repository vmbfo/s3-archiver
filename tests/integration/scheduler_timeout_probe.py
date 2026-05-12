"""Subprocess probes for scheduler integration tests."""

from __future__ import annotations

import textwrap


def timeout_probe_script() -> str:
    """Return a child script that writes an archive, holds a lock, then times out."""

    return textwrap.dedent(
        """
        import gzip
        import json
        import os
        import tarfile
        import time
        from datetime import UTC, datetime, timedelta
        from io import BytesIO
        from pathlib import Path

        from s3_archiver_core.archive_lock import FileArchiveRunLock
        from s3_archiver_core.s3 import build_s3_client
        from s3_archiver_core.settings import AppSettings


        def archive_payload(member_key: str, member_payload: str) -> bytes:
            buffer = BytesIO()
            with (
                gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gzip_file,
                tarfile.open(fileobj=gzip_file, mode="w:") as archive,
            ):
                data = member_payload.encode()
                info = tarfile.TarInfo(member_key)
                info.size = len(data)
                info.mtime = 0
                archive.addfile(info, BytesIO(data))
            return buffer.getvalue()


        lock = FileArchiveRunLock(Path(os.environ["LOG_DIR"]) / "archive.lock")
        if not lock.acquire(
            run_id="timed-out-run",
            run_started_at_utc=datetime.now(tz=UTC),
            timeout=timedelta(seconds=1),
        ):
            raise SystemExit("failed to acquire archive lock")
        settings = AppSettings.from_env(os.environ)
        destination = build_s3_client(settings.routes[0].destination)
        timeout_archive_key = os.environ["S3_ARCHIVER_TIMEOUT_ARCHIVE_KEY"]
        member_key = os.environ["S3_ARCHIVER_TIMEOUT_MEMBER_KEY"]
        member_payload = os.environ["S3_ARCHIVER_TIMEOUT_MEMBER_PAYLOAD"]
        destination.put_object(
            Bucket=settings.routes[0].destination.bucket,
            Key=timeout_archive_key,
            Body=archive_payload(member_key, member_payload),
        )
        print(
            json.dumps(
                {
                    "lock_acquired": True,
                    "timeout_child_archive_key": timeout_archive_key,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        time.sleep(10)
        """
    ).strip()
