#!/usr/bin/env bash
set -euo pipefail

: "${ASSET_TEST_DATABASE_URL:?Set ASSET_TEST_DATABASE_URL to a disposable PostgreSQL database}"
export PROJECT_TEST_DATABASE_URL="${PROJECT_TEST_DATABASE_URL:-$ASSET_TEST_DATABASE_URL}"

bash scripts/validate-repository.sh
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests local_stack
uv run python scripts/ai_contract.py --check
uv run python scripts/openapi_contract.py --check
uv run pytest --require-postgres
