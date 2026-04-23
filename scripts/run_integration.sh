#!/usr/bin/env bash
set -euo pipefail

unset ENV_FILE
unset APP_ENV_FILE
export LOCALSTACK_S3_URL="${LOCALSTACK_S3_URL:-http://127.0.0.1:4566}"

uv run pytest tests/integration -m integration
