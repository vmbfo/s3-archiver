#!/bin/sh
set -eu

# Keep the ready hook limited to an S3 API smoke check.
awslocal s3api list-buckets >/dev/null 2>&1
