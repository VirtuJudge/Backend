# Spec 03: SessionWorkflow, AI Job Integration & Report Generation Pipeline

## Problem Statement

When the Q&A round finishes (all three primary questions and any follow-up questions are answered or skipped), the practice session must transition to report generation, enqueue a `generate_report` AIJob, and wait for the AI worker. When the worker responds with a completed update, the backend must validate the artifacts and payloads according to our domain rules, persist canonical records, advance the session to `completed`, and notify connected clients via SSE.

## Solution

- In `SessionWorkflow`:
  - Detect when Q&A round is complete (`round_.state is QARoundState.COMPLETED`).
  - Transition session status: `SessionStatus.QUESTIONS_IN_PROGRESS` -> `SessionStatus.REPORT_GENERATING`.
  - Build `GenerateReportPayload` referencing analysis artifact, QA artifact, and confirmed speaker mappings.
  - Create and dispatch `AIJob` with `job_type="generate_report"`.
- In `AIJobs.record_update`:
  - Handle `ReportCompletedPayload`.
  - Validate the update and payload using `validate_completed_update` and domain evaluation validator.
  - Store the validated `Report` and `Evaluation` using the report repository.
  - Transition session status to `SessionStatus.COMPLETED`.
  - Transition attempt status to `AnalysisAttemptStatus.COMPLETED`.
  - Emit SSE event `report.ready.v1` with report ID, evaluation ID, and session status.

## User Stories

1. As a team member, I want the session to automatically start generating the report once all Q&A questions are finalized, so that I don't need manual steps to trigger report generation.
2. As a worker, I want the `generate_report` job payload to include the analysis artifact, QA artifact, and speaker mappings, so that report generation has all context.
3. As a developer, I want the backend to reject invalid worker updates (mismatched trace IDs, missing fields, invalid schema versions), so that worker contract drift is prevented.
4. As a team member, I want the session to advance to `completed` once report generation succeeds, so that the dashboard reflects the finished state.
5. As a frontend client, I want to receive an SSE `report.ready.v1` event when the report is stored, so that the UI can navigate to the report immediately.
6. As a team member, I want an error in report generation to transition the session safely to `failed` without corrupting existing practice session data.

## Implementation Decisions

- In `app/application/ai_jobs.py`:
  - Add `_dispatch_generate_report(session, round_, attempt, uow)` when Q&A round completes.
  - In `record_update`: handle `isinstance(validated_payload, ReportCompletedPayload)`.
  - Parse and validate evaluation and report objects.
  - Persist canonical entities via `uow.reports.save_evaluation` and `uow.reports.save_report`.
  - Emit `NotificationEventName.REPORT_READY`.
- In `app/application/session_notification_contracts.py`:
  - Ensure `report.ready.v1` schema and factory are properly wired.

## Testing Decisions

- Unit tests in `tests/unit/test_report_generation_workflow.py` testing:
  - Q&A completion triggering `generate_report` job creation.
  - Worker callback processing: handling valid `ReportCompletedPayload`.
  - Worker callback validation failures: rejecting invalid weights, missing member feedback, or stale sequences.
  - State transitions from `REPORT_GENERATING` to `COMPLETED`.
  - Emission of `report.ready.v1` event.

## Out of Scope

- PDF generation and download URLs (covered in Spec 04).

## Further Notes

- Maintains idempotency and sequence number checks in `AIJobs.record_update`.
