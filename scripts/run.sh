#!/usr/bin/env bash
set -euo pipefail

env_file="${ENV_FILE:-.env}"
uv_bin="${UV:-uv}"
endpoint_override="${S3_ENDPOINT_URL:-}"

if [[ ! -f "${env_file}" ]]; then
  echo "missing env file: ${env_file}" >&2
  exit 1
fi

set -a
source "${env_file}"
set +a

if [[ -n "${endpoint_override}" ]]; then
  export S3_ENDPOINT_URL="${endpoint_override}"
fi

exec "${uv_bin}" run s3-archiver check
