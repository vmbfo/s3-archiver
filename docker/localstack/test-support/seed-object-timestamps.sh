#!/bin/sh
set -eu

: "${TEST_S3_SOURCE_BUCKET:?set TEST_S3_SOURCE_BUCKET}"

prefix="${TEST_TIMESTAMP_SEED_PREFIX:-retention}"
days="${TEST_TIMESTAMP_SEED_DAYS:-0 59 60 61}"
seed_now="${TEST_TIMESTAMP_SEED_NOW:-2100-01-01T00:00:00+00:00}"
timestamp_rows="$(
  TEST_TIMESTAMP_SEED_NOW="${seed_now}" TEST_TIMESTAMP_SEED_DAYS="${days}" python - <<'PY'
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import os

seed_now = datetime.fromisoformat(os.environ["TEST_TIMESTAMP_SEED_NOW"].replace("Z", "+00:00"))
for raw_age_days in os.environ["TEST_TIMESTAMP_SEED_DAYS"].split():
    target = seed_now.astimezone(UTC) - timedelta(days=int(raw_age_days))
    target = target.replace(microsecond=0)
    print(f"{raw_age_days}\t{target.isoformat()}\t{format_datetime(target, usegmt=True)}")
PY
)"

printf '%s\n' "${timestamp_rows}" | while IFS='	' read -r age_days target_last_modified expected_head_last_modified; do
  key="${prefix}/age-${age_days}-days.txt"
  body="/tmp/s3-archiver-${age_days}.txt"
  printf 'age_days=%s\n' "${age_days}" >"${body}"
  awslocal s3api put-object \
    --bucket "${TEST_S3_SOURCE_BUCKET}" \
    --key "${key}" \
    --body "${body}" \
    --metadata "s3-archiver-test-age-days=${age_days},s3-archiver-test-last-modified=${target_last_modified}" \
    >/dev/null
  rm -f "${body}"
  metadata_age="$(awslocal s3api head-object \
    --bucket "${TEST_S3_SOURCE_BUCKET}" \
    --key "${key}" \
    --query 'Metadata."s3-archiver-test-age-days"' \
    --output text)"
  last_modified="$(awslocal s3api head-object \
    --bucket "${TEST_S3_SOURCE_BUCKET}" \
    --key "${key}" \
    --query 'LastModified' \
    --output text)"
  test "${metadata_age}" = "${age_days}"
  test "${last_modified}" = "${expected_head_last_modified}"
  printf '%s\t%s\t%s\n' "${key}" "${age_days}" "${last_modified}"
done
