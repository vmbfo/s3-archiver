#!/bin/sh
set -eu

if [ -n "${TEST_S3_SOURCE_BUCKET:-}" ]; then
  awslocal s3api create-bucket --bucket "${TEST_S3_SOURCE_BUCKET}" >/dev/null 2>&1 || true
fi

if [ -n "${TEST_S3_DESTINATION_BUCKET:-}" ]; then
  awslocal s3api create-bucket --bucket "${TEST_S3_DESTINATION_BUCKET}" >/dev/null 2>&1 || true
fi
