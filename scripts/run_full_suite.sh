#!/usr/bin/env bash
set -euo pipefail

coverage_file="$(mktemp "${TMPDIR:-/tmp}/s3-archiver.coverage.XXXXXX")"
cleanup() {
  rm -f "${coverage_file}"
}
trap cleanup EXIT

docker compose --profile test build app
PYTHONDONTWRITEBYTECODE=1 \
COVERAGE_FILE="${coverage_file}" \
uv run pytest \
  -p no:cacheprovider \
  --cov \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100
