#!/usr/bin/env python3
"""Container entrypoint that repairs writable mounts before dropping privileges."""

from __future__ import annotations

import grp
import os
import pwd
import shutil
import sys
from pathlib import Path

APP_USER = "app"
APP_GROUP = "app"
DEFAULT_TEMP_DIR = "/tmp/s3-archiver"
DEFAULT_LOG_DIR = "/var/log/s3-archiver"


def main() -> None:
    """Prepare runtime directories and execute the CLI as the app user."""

    args = ["s3-archiver", *sys.argv[1:]]
    if os.getuid() != 0:
        os.execvp(args[0], args)

    user = pwd.getpwnam(APP_USER)
    group = grp.getgrnam(APP_GROUP)
    uid = user.pw_uid
    gid = group.gr_gid

    for path in _runtime_dirs():
        _try_prepare_runtime_dir(path, uid, gid)

    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.execvp(args[0], args)


def _runtime_dirs() -> tuple[Path, ...]:
    temp_dir = os.environ.get("ARCHIVER_TEMP_DIR", DEFAULT_TEMP_DIR)
    log_dir = os.environ.get("LOG_DIR", DEFAULT_LOG_DIR)
    return (Path(temp_dir), Path(log_dir))


def _try_prepare_runtime_dir(path: Path, uid: int, gid: int) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        _chown_tree(path, uid, gid)
    except OSError:
        return


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for name in dirs:
            os.chown(Path(root) / name, uid, gid)
        for name in files:
            os.chown(Path(root) / name, uid, gid)


if __name__ == "__main__":
    if shutil.which("s3-archiver") is None:
        raise SystemExit("s3-archiver executable not found on PATH")
    main()
