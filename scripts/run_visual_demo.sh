#!/usr/bin/env bash
set -euo pipefail

docker compose --profile test build app
uv run pytest tests/e2e/test_compose_visual_demo.py -m e2e -s
