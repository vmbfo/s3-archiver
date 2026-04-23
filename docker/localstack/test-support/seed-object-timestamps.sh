#!/bin/sh
set -eu

: "${TEST_S3_SOURCE_BUCKET:?set TEST_S3_SOURCE_BUCKET}"

prefix="${TEST_TIMESTAMP_SEED_PREFIX:-retention}"
days="${TEST_TIMESTAMP_SEED_DAYS:-0 59 60 61}"

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
done
