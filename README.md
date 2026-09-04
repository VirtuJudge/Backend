# VirtuJudge Backend

FastAPI control plane following the four-layer [backend architecture](https://github.com/VirtuJudge/Docs/blob/main/Architecture/Backend-Architecture.md).

## Shared local stack

Install Docker with Compose 2.17 or newer and Python 3. Check out the scaffolded repositories side by side:

```text
VirtuJudge/
├── Backend/
├── Frontend/
└── AI-ML/
```

From Backend, start all six services:

```bash
bash scripts/local-stack.sh up
```

The script builds the actual sibling frontend and AI code, generates ignored `.env.local` credentials once with mode `0600`, and waits for every service to become healthy. It does not clone, pin, or modify the sibling repositories. Frontend needs its package manifest and lockfile; AI-ML needs its Python project and worker scaffold. The first build downloads dependencies and can take several minutes.

| Service | Local address |
|---|---|
| Frontend | http://localhost:3000 |
| Backend health / OpenAPI | http://localhost:8000/health / http://localhost:8000/docs |
| PostgreSQL with pgvector | localhost:5432, database `virtujudge` |
| Redis | localhost:6379 |
| MinIO S3 / console | http://localhost:9000 / http://localhost:9001 |

Host ports bind to loopback. PostgreSQL has separate `virtujudge_backend` and `virtujudge_ai` roles and private schemas. Redis database 0 carries diagnostic jobs under `virtujudge:local:*`; database 1 holds cache keys. PostgreSQL, Redis, and MinIO use persistent volumes.

The frontend defaults to its existing mock API (`NEXT_PUBLIC_MOCK_API=true`) because product endpoints are still being implemented. Its browser API URL is `http://localhost:8000/api/v1`; containers reach the backend at `http://backend:8000`. The local worker invokes AI-ML's existing `FakePipeline`. Diagnostic results are temporary Redis data, not canonical AI Job or Practice Session state. The product dispatcher and authenticated callbacks belong to BE-05.

## Verify and restart

```bash
bash scripts/local-stack.sh smoke
bash scripts/local-stack.sh status
bash scripts/local-stack.sh down
bash scripts/local-stack.sh up
```

The smoke check verifies PostgreSQL/pgvector, each role's own-table access and cross-role denial, Redis cache operations, an S3 object round trip, and a correlated fake analysis returning three Primary Questions. It uses synthetic data, removes its temporary records, and requires no Gmail or paid-provider credentials.

`down` preserves volumes and `.env.local`; `up` reuses both. Keep `.env.local` while retaining the database volume because changing its passwords does not update existing database roles. To diagnose startup, inspect `docker compose --env-file .env.local logs SERVICE` and check the listed host ports for conflicts. Missing sibling scaffolds produce an actionable startup error.

## Host development

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --locked
cp .env.example .env
uv run uvicorn app.main:app --reload
```

`.env` configures host processes; `.env.local` configures Compose. To connect host tools to Compose services, privately copy the generated backend password into `DATABASE_URL`, and the MinIO credentials into `OBJECT_STORAGE_ACCESS_KEY` and `OBJECT_STORAGE_SECRET_KEY` in `.env`. Stop the Compose backend or choose another host port before running a second API server. `/health` is application liveness; use the stack smoke command to verify dependencies.

## Mail

Compose always uses `MAIL_BACKEND=fake`. The fake mail adapter retains messages in memory without delivery or content logging. Invitation business workflows are not part of this setup issue.

Optional Gmail delivery runs only through an explicitly invoked host smoke command. Configure these values in ignored `.env` or your shell environment:

```dotenv
GMAIL_SMTP_HOST=smtp.gmail.com
GMAIL_SMTP_PORT=587
GMAIL_SMTP_USERNAME=
GMAIL_SMTP_PASSWORD=
GMAIL_FROM_ADDRESS=
GMAIL_SMOKE_ALLOWLIST=
```

Use a dedicated Gmail or Google Workspace sender and an app password where supported, with two-step verification enabled. The adapter uses STARTTLS with certificate verification, as described in [Google's SMTP instructions](https://support.google.com/mail/answer/7104828). `GMAIL_SMOKE_ALLOWLIST` is a comma-separated list of permitted recipient addresses. Fill in the sender credentials and allowlist privately, then explicitly send one synthetic message:

```bash
uv run python -m local_stack.gmail_smoke --recipient allowed@example.com --send
```

The command checks the allowlist and `--send` before connecting. Credentials are not included in Compose or container images. Ordinary tests use fakes and never send Gmail.

## Repository checks and migrations

```bash
uv sync --locked
bash scripts/check.sh
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
uv run alembic downgrade -1
```

Repository checks cover lint, formatting, strict types, and tests without running external services. The explicit stack smoke check covers the real service boundaries.
