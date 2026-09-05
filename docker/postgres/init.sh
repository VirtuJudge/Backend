#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v database="$POSTGRES_DB" \
  -v backend_password="$BACKEND_DB_PASSWORD" \
  -v ai_password="$AI_DB_PASSWORD" <<-'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;

CREATE ROLE virtujudge_backend WITH LOGIN PASSWORD :'backend_password';
CREATE ROLE virtujudge_ai WITH LOGIN PASSWORD :'ai_password';

CREATE SCHEMA IF NOT EXISTS backend AUTHORIZATION virtujudge_backend;
CREATE SCHEMA IF NOT EXISTS ai AUTHORIZATION virtujudge_ai;

REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO virtujudge_backend, virtujudge_ai;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO virtujudge_backend, virtujudge_ai;

ALTER ROLE virtujudge_backend SET search_path = backend, public;
ALTER ROLE virtujudge_ai SET search_path = ai, public;

REVOKE ALL ON SCHEMA backend FROM PUBLIC, virtujudge_ai;
REVOKE ALL ON SCHEMA ai FROM PUBLIC, virtujudge_backend;
EOSQL
