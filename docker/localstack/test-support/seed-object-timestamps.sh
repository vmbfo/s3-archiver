#!/bin/sh
set -eu

: "${TEST_S3_SOURCE_BUCKET:?set TEST_S3_SOURCE_BUCKET}"

prefix="${TEST_TIMESTAMP_SEED_PREFIX:-retention}"
days="${TEST_TIMESTAMP_SEED_DAYS:-0 59 60 61}"

# LocalStack 4.8.1 does not expose a supported S3 API for backdating
# LastModified. Keep tests deterministic by storing the intended age in
# metadata and asserting that LocalStack returns a concrete LastModified for
# each seeded object.
for age_days in ${days}; do
  key="${prefix}/age-${age_days}-days.txt"
  body="/tmp/s3-archiver-${age_days}.txt"
  printf 'age_days=%s\n' "${age_days}" >"${body}"
  awslocal s3api put-object \
    --bucket "${TEST_S3_SOURCE_BUCKET}" \
    --key "${key}" \
    --body "${body}" \
    --metadata "s3-archiver-test-age-days=${age_days}" \
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
  test -n "${last_modified}"
  test "${last_modified}" != "None"
  printf '%s\t%s\t%s\n' "${key}" "${age_days}" "${last_modified}"
done
