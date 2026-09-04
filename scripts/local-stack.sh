#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ENV_LOCAL="${REPO_ROOT}/.env.local"

generate_env_local() {
  if [[ ! -f "${ENV_LOCAL}" ]]; then
    python3 -c '
import os
import secrets
import sys

env_file = sys.argv[1]
if os.path.exists(env_file):
    exit(0)

secrets_data = {
    "POSTGRES_PASSWORD": secrets.token_hex(16),
    "BACKEND_DB_PASSWORD": secrets.token_hex(16),
    "AI_DB_PASSWORD": secrets.token_hex(16),
    "MINIO_ROOT_USER": secrets.token_hex(8),
    "MINIO_ROOT_PASSWORD": secrets.token_hex(16),
}

flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
fd = os.open(env_file, flags, 0o600)
with open(fd, "w", encoding="utf-8") as f:
    for key, val in secrets_data.items():
        f.write(f"{key}={val}\n")
os.chmod(env_file, 0o600)
' "${ENV_LOCAL}"
  fi
}

check_prerequisites() {
  local frontend_dir="${REPO_ROOT}/../Frontend"
  local aiml_dir="${REPO_ROOT}/../AI-ML"

  if [[ ! -d "${frontend_dir}" || ! -f "${frontend_dir}/package.json" || ! -f "${frontend_dir}/package-lock.json" ]]; then
    echo "Error: Sibling Frontend checkout missing or incomplete at ${frontend_dir}." >&2
    echo "Expected directory with package.json and package-lock.json." >&2
    echo "Clone or check out the Frontend repository alongside Backend before starting the local stack." >&2
    exit 1
  fi

  if [[ ! -d "${aiml_dir}" || ! -f "${aiml_dir}/pyproject.toml" || ! -f "${aiml_dir}/app/worker.py" ]]; then
    echo "Error: Sibling AI-ML checkout missing or incomplete at ${aiml_dir}." >&2
    echo "Expected directory with pyproject.toml and app/worker.py." >&2
    echo "Clone or check out the AI-ML repository alongside Backend before starting the local stack." >&2
    exit 1
  fi
}

case "${1:-}" in
  up)
    check_prerequisites
    generate_env_local
    docker compose --env-file .env.local up --build --wait --wait-timeout 180
    ;;
  down)
    if [[ -f "${ENV_LOCAL}" ]]; then
      docker compose --env-file .env.local down
    else
      docker compose down
    fi
    ;;
  status)
    if [[ -f "${ENV_LOCAL}" ]]; then
      docker compose --env-file .env.local ps
    else
      docker compose ps
    fi
    ;;
  *)
    echo "Usage: $0 {up|down|status}" >&2
    exit 1
    ;;
esac
