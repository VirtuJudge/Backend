#!/bin/sh
set -eu

python -m app.infrastructure.deployment_environment
alembic upgrade head

exec uvicorn app.main:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${PORT:-${APP_PORT:-8000}}"
