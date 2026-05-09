#!/bin/sh
set -eu

provider_file="/opt/code/localstack/localstack-core/localstack/services/s3/provider.py"
marker="s3-archiver-test-last-modified"

if grep -q "${marker}" "${provider_file}"; then
  exit 0
fi

/opt/code/localstack/.venv/bin/python - <<'PY'
from pathlib import Path

provider = Path("/opt/code/localstack/localstack-core/localstack/services/s3/provider.py")
source = provider.read_text(encoding="utf-8")
needle = "            s3_stored_object.write(body)\n"
patch = """            s3_stored_object.write(body)
            test_last_modified = s3_object.user_metadata.pop(
                "s3-archiver-test-last-modified", None
            )
            if test_last_modified:
                s3_object.last_modified = datetime.datetime.fromisoformat(
                    test_last_modified.replace("Z", "+00:00")
                ).astimezone(ZoneInfo("GMT"))
                s3_object.internal_last_modified = s3_stored_object.last_modified
"""
if needle not in source:
    raise SystemExit("LocalStack S3 provider patch target not found")
provider.write_text(source.replace(needle, patch), encoding="utf-8")
PY
