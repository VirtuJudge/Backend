# VirtuJudge Backend

FastAPI backend for authentication, teams, projects, uploads, Practice Sessions, AI Jobs, Q&A, reports, Gmail invitations, and deletion.

The service follows the four-layer design in the [backend architecture](https://github.com/VirtuJudge/Docs/blob/main/Architecture/Backend-Architecture.md).

## Run locally

Install the pinned dependencies and start the API:

```bash
uv sync --locked
cp .env.example .env
uv run uvicorn app.main:app --reload
```

Replace `[YOUR-PASSWORD]` in `.env` with the Supabase database password. Keep `.env` out of source control. The service itself starts without production credentials and can be checked at `GET /health`.

## Check the repository

```bash
uv sync --locked
bash scripts/check.sh
```

Create and apply database migrations with Alembic:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
uv run alembic downgrade -1
```
