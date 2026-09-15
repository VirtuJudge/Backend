# Spec 02: Database Persistence, Alembic Migration & Repositories

## Problem Statement

Reports and Evaluations must be durably stored in PostgreSQL, queryable by session and team, linked by proper foreign keys, and protected by optimistic concurrency. Currently, the database contains no tables for evaluations, reports, or report exports.

## Solution

Add persistence infrastructure for reports and evaluations:
- SQLAlchemy models in `app/infrastructure/persistence/configurations/session_workflow/`:
  - `EvaluationModel` (id, session_id, attempt_id, qa_round_id, rubric_id, rubric_version, overall_score, payload, created_at)
  - `ReportModel` (id, session_id, evaluation_id, title, executive_summary, overall_score, payload, created_at, updated_at)
  - `ReportExportModel` (id, report_id, session_id, format, status, asset_version_id, failure_reason, created_at, completed_at)
- Alembic migration chained after `7b4e1a6d2c8f` that creates `evaluations`, `reports`, and `report_exports` tables with foreign keys and unique constraints.
- Repository ports in `app/application/ports/session_practice/report_repository.py`.
- SQLAlchemy repository implementation in `app/infrastructure/repositories/session_workflow/sqlalchemy_report_repository.py`.
- Integration into `UnitOfWork`.

## User Stories

1. As a team member, I want my generated report to be persisted permanently, so that I can revisit past session results.
2. As a team member, I want to access both the high-level report and the detailed evaluation, so that I can inspect full scoring breakdowns.
3. As a developer, I want database foreign keys to enforce referential integrity between practice sessions, attempts, evaluations, and reports.
4. As a developer, I want report export jobs to be tracked in `report_exports` with distinct statuses (`queued`, `rendering`, `ready`, `failed`), so that export progress can be monitored.
5. As a developer, I want repository methods to support querying reports by practice session ID, so that report lookup is indexed and fast.
6. As a developer, I want migration scripts to be idempotent and compatible with both PostgreSQL and SQLite (for tests).

## Implementation Decisions

- Tables:
  - `evaluations`: `id` (UUID PK), `practice_session_id` (UUID FK), `analysis_attempt_id` (UUID FK), `qa_round_id` (UUID FK), `rubric_id` (VARCHAR), `rubric_version` (INT), `overall_score` (FLOAT), `payload` (JSON/JSONB), `created_at` (TIMESTAMP).
  - `reports`: `id` (UUID PK), `practice_session_id` (UUID FK, UNIQUE), `evaluation_id` (UUID FK, UNIQUE), `title` (VARCHAR), `executive_summary` (TEXT), `overall_score` (FLOAT), `payload` (JSON/JSONB), `created_at` (TIMESTAMP), `updated_at` (TIMESTAMP).
  - `report_exports`: `id` (UUID PK), `report_id` (UUID FK), `practice_session_id` (UUID FK), `format` (VARCHAR), `status` (VARCHAR), `asset_version_id` (UUID FK nullable), `failure_reason` (TEXT nullable), `created_at` (TIMESTAMP), `completed_at` (TIMESTAMP nullable).
- Repository port `ReportRepository`:
  - `save_evaluation(evaluation: Evaluation) -> None`
  - `get_evaluation(evaluation_id: UUID) -> Evaluation | None`
  - `get_evaluation_by_session(session_id: UUID) -> Evaluation | None`
  - `save_report(report: Report) -> None`
  - `get_report(report_id: UUID) -> Report | None`
  - `get_report_by_session(session_id: UUID) -> Report | None`
  - `save_report_export(export: ReportExport) -> None`
  - `get_report_export(export_id: UUID) -> ReportExport | None`
- UnitOfWork updated with `reports: ReportRepository`.

## Testing Decisions

- Repository integration tests testing CRUD, constraints, and relations against SQLite and PostgreSQL test fixtures.
- Alembic migration tests ensuring upgrades and downgrades execute cleanly.

## Out of Scope

- Worker callbacks and queue dispatch (covered in Spec 03).
- PDF rendering and FastAPI routes (covered in Spec 04).

## Further Notes

- Repositories map cleanly between SQLAlchemy models and domain entities.
