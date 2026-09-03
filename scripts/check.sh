#!/usr/bin/env bash
set -euo pipefail

bash scripts/validate-repository.sh
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests
uv run pytest
