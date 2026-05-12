#!/usr/bin/env bash
set -euo pipefail

unset ENV_FILE
export APP_ENV_FILE=/dev/null
export LOCALSTACK_S3_URL="${LOCALSTACK_S3_URL:-http://127.0.0.1:4566}"

docker compose --profile test build app
uv run pytest \
  tests/e2e/test_compose_archive.py \
  tests/e2e/test_compose_archive_coverage.py \
  tests/e2e/test_compose_scheduler_runtime.py \
  tests/e2e/test_compose_scheduler_timeout_runtime.py \
  tests/e2e/test_compose_stack.py \
  -m e2e
