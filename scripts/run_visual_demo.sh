#!/usr/bin/env bash
set -euo pipefail

uv run --package s3-archiver-visual-demo s3-archiver-visual-demo "$@"
