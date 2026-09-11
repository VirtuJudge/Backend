#!/bin/sh
set -eu

alembic upgrade head

exec uvicorn app.main:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${PORT:-${APP_PORT:-8000}}"
