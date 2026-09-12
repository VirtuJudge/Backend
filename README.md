# VirtuJudge Backend

FastAPI control plane following the four-layer [backend architecture](https://github.com/VirtuJudge/Docs/blob/main/Architecture/Backend-Architecture.md).

## Architecture

The backend follows a strict four-layer architecture with dependencies pointing inward:

- **`domain`** (`app/domain/`): Plain models, business entities, validation rules, and domain error definitions. Strictly framework-free with no dependencies on FastAPI, SQLAlchemy, or external SDKs.
- **`application`** (`app/application/`): Use cases, orchestrators, and services (`asset_store`, `project_service`, `team_service`, `user_service`). Defines abstract interfaces and contracts under `app/application/ports/` (`asset_repository.py`, `object_storage.py`, `media_verifier.py`, `document_verifier.py`, `project_repository.py`, `team_repository.py`, `user_repository.py`).
- **`api`** (`app/api/`): HTTP presentation layer containing FastAPI routers (`health.py`, `user.py`, `asset.py`, `project.py`, `team.py`), request schemas, authentication dependencies, and mapped Problem Details error responses (`errors.py`). Calls application use cases and converts domain errors to HTTP status codes.
- **`infrastructure`** (`app/infrastructure/`): Concrete adapter implementations of application ports. Contains SQLAlchemy ORM models, mappings, and repositories (`app/infrastructure/repositories/`), S3 object storage client (`app/infrastructure/storage/`), media inspection with ffmpeg/ffprobe (`app/infrastructure/media/`), pypdf document verifier, database engine configuration, and settings.

Wiring and dependency injection take place at the composition root in `app/main.py`.

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

For the prototype production deployment on Render with Cloudflare R2, follow
[the Render and R2 deployment guide](docs/deployment-render-r2.md). The repository includes a
Render Blueprint, startup-time migrations, API CORS configuration, and a credential-safe R2
smoke check.

`.env` configures host processes; `.env.local` configures Compose. To connect host tools to Compose services, privately copy the generated backend password into `DATABASE_URL`, and the MinIO credentials into `OBJECT_STORAGE_ACCESS_KEY` and `OBJECT_STORAGE_SECRET_KEY` in `.env`. Stop the Compose backend or choose another host port before running a second API server. `/health` is application liveness; use the stack smoke command to verify dependencies.

## Mail

Compose always uses `MAIL_BACKEND=fake`. The fake mail adapter retains messages in memory without delivery or content logging. Invitation business workflows are not part of this setup issue.

Production on Render Free uses the real Resend HTTPS adapter because Render blocks outbound SMTP
ports on free web services. Verify a domain in Resend and configure these values only in Render's
Environment page or another secret store:

```dotenv
MAIL_BACKEND=resend
RESEND_API_KEY=re_...
RESEND_FROM_ADDRESS=VirtuJudge <noreply@mail.example.com>
```

The sender must belong to the verified domain. Secrets are not stored in `render.yaml`; the
Blueprint prompts for them during setup.

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

### Test taxonomy

Tests are categorized under `tests/` aligned with the four-layer architecture:

- **Architecture tests** (`tests/architecture/`): Enforce dependency directions, ensuring ports and domain do not import outer layers.
- **Unit tests** (`tests/unit/`): Fast, in-memory validation of domain rules, asset metadata, and container signatures without I/O or database overhead.
- **Integration tests** (`tests/integration/`):
  - `api/`: Route handlers, status codes, and Problem Details error responses.
  - `adapters/`: Adapter implementations against synthetic or subprocess boundaries (FFmpeg media verifier, pypdf document verifier, S3 storage client, mail adapter).
  - `persistence/`: Database operations, concurrent updates, locks, and migration lifecycle tests with SQLite or PostgreSQL test databases.
- **Acceptance tests** (`tests/acceptance/`): End-to-end API workflows and business processes (asset upload intents, verification completions, version progression, project/team authorization, and abandoned upload cleanup).
- **System tests** (`tests/system/`): Local-stack smoke checks, container CLI interactions, Redis diagnostic queues, and Gmail smoke verification.
- **Test support** (`tests/support/`): Shared test doubles (`FakeAssetRepository`, `RecordingStorage`, `FakeSyncRedis`, `FakeTokenVerifier`), synthetic document builders, client helpers, and constants.

Individual test categories can be executed selectively:

```bash
uv run pytest tests/architecture
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/acceptance
uv run pytest tests/system
```

## Asset uploads and abandoned cleanup

Assets support direct private uploads for documents and media with immutable versions.

### Upload lifetime and idempotency

- Upload intents persist an absolute `upload_expires_at` deadline at creation using clamped TTL (`OBJECT_STORAGE_UPLOAD_URL_TTL_SECONDS`, default 900s).
- First creation and replayed intents sign only the floored remaining seconds before `upload_expires_at`. Replays never renew beyond the deadline.
- Replaying an expired intent or an intent for a non-pending version (`verified`, `rejected`, `deleting`, `deleted`) returns safe `409 conflict`.
- Completing a valid `pending_upload` version succeeds after URL expiry until cleanup claims the version. A claimed version returns `409 conflict`; if cleanup deletes the logical asset because no usable version survives, later access is concealed with `404 not found`. Repeated completion of verified versions remains idempotent.

### Automated cleanup lifecycle

A background periodic task in the FastAPI lifespan automatically claims and cleans abandoned uploads:
- **Eligibility**: Requires both the upload deadline to have expired (`upload_expires_at <= now`) and a retention grace period to have passed (`created_at <= now - retention_seconds`). Verified objects are never eligible.
- **State transitions**: Short transaction locks asset and version (PostgreSQL `SKIP LOCKED`), marks version `deleting` with a lease (`ASSET_CLEANUP_LEASE_SECONDS`, default 300s), deletes object from storage (missing object is treated as success), and finalizes version state to `deleted`.
- **Tombstone re-sweep**: Finalized `deleted` versions schedule `cleanup_next_attempt_at` for delayed re-sweep (`ASSET_CLEANUP_TOMBSTONE_DELAY_SECONDS`, default 86400s) to catch late-arriving PUT requests without starving fresh candidates.
- **Preservation**: If a replacement version is abandoned, surviving verified document current version pointers are preserved. If a newer pending replacement still exists, it becomes current; the logical asset is marked `deleted` only when no verified or pending version survives.
- **Scheduler**: Enabled by default in development/production, disabled in test (`app_env == "test"` unless `ASSET_CLEANUP_ENABLED=true`). Runs every `ASSET_CLEANUP_INTERVAL_SECONDS` (default 300s) in batches of `ASSET_CLEANUP_BATCH_SIZE` (default 100) using independent database sessions.

### Schema migration

Migration `d5e6f7a8b9c0` adds `upload_expires_at` and `cleanup_next_attempt_at` with indices to `asset_versions`. Existing rows are backfilled to `migration_time + 3600s` before applying `NOT NULL` to preserve legacy replays during rollout. Downgrade safely removes the columns and indices.

### Explicit smoke check

To run real PostgreSQL and MinIO integration checks against an isolated disposable database and bucket:

```bash
uv run python scripts/smoke-assets.py --env-file .env.local
```

The script requires an explicit `--env-file`, provisions unique resources, exercises concurrent uploads, fail-closed signature enforcement, media verification, and cleanup transitions, and drops all created resources upon completion without modifying application data.
