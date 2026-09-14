# Spec 01: Domain Models, Rubric Validation & Scoring Engine

## Problem Statement

When an AI model completes a presentation evaluation, the backend cannot trust raw model outputs without strict verification. If the system accepts arbitrary score ranges, missing or unbalanced weights, hallucinated evidence, or merged individual-team feedback, users receive inaccurate, ungrounded assessments. Furthermore, if mapped presenters are omitted or duplicated, team members cannot reliably review their individual performance.

## Solution

A framework-free domain layer in `app/domain/session_workflow/` providing plain models, score calculators, and validation rules for Evaluations and Reports:
- Enforce that score component weights total exactly 1.0 (100%).
- Enforce that the Q&A dimension contributes exactly 0.20 (20%) of the total score.
- Enforce score normalization: normalized scores must be within `[0.0, 1.0]`, mapped to rounded `0..100` display scores and standard performance labels (`needs_work`, `developing`, `good`, `strong`).
- Validate all evidence references to ensure non-empty valid types (`transcript_span`, `video_interval`, `audio_interval`, `document_span`, `answer_span`) and structured source locators.
- Enforce clean separation between whole-team feedback and individual member feedback.
- Require exactly one member-feedback section for every confirmed mapped presenter in the practice session.
- Validate rubric version and dimension definitions against the session manifest.

## User Stories

1. As a team member, I want the overall pitch score to be calculated transparently from rubric weights, so that our evaluation reflects clear evaluation criteria.
2. As a team member, I want Q&A performance to contribute exactly 20% of our total pitch score, so that question answering is weighted consistently across sessions.
3. As a team member, I want each score component to have a normalized score between 0.0 and 1.0, so that scores cannot exceed valid mathematical bounds.
4. As a team member, I want each score component to display a rounded score between 0 and 100 with an appropriate label (`needs_work`, `developing`, `good`, `strong`), so that feedback is easily understandable.
5. As a team member, I want all rubric dimensions defined in the session manifest to be validated, so that missing or unrecognized dimensions are rejected.
6. As a team member, I want every scored finding to include at least one valid evidence reference, so that feedback is grounded in our presentation and documents.
7. As a team member, I want team feedback to be kept separate from individual feedback, so that collaborative aspects are not confused with personal delivery.
8. As a mapped presenter, I want my own distinct member feedback section in the report, so that I receive targeted feedback on my speaking and answers.
9. As a team owner, I want the backend to reject reports that omit any mapped presenter, so that every active speaker receives an evaluation or documented limitation.
10. As a team owner, I want the backend to reject reports that duplicate any presenter, so that feedback sections are unambiguous.
11. As a developer, I want domain models to remain completely free of web or database frameworks, so that domain rules are testable in isolation.

## Implementation Decisions

- Plain dataclasses and Python classes in `app/domain/session_workflow/` (no FastAPI, SQLAlchemy, or Pydantic in domain).
- Domain entities: `Evaluation`, `Report`, `ScoreComponent`, `Finding`, `FeedbackSection`, `MemberFeedback`, `EvidenceReference`, `ReportRubric`.
- Scoring rules:
  - Total weight validator: sum of `configured_weight` across all components must equal 1.0 (with floating-point tolerance `1e-5`).
  - Q&A dimension validator: dimension identified as `qa` or `q_and_a` must have `configured_weight == 0.20`.
  - Normalized score bounds `0.0 <= score <= 1.0`. Display score rounded to integer `0..100`.
  - Label mapping: `[0, 40)` -> `needs_work`, `[40, 60)` -> `developing`, `[60, 80)` -> `good`, `[80, 100]` -> `strong`.
- Validation service `validate_evaluation_and_report(evaluation, report, session_manifest, speaker_mappings)` returning domain errors (`InvalidScoreWeightError`, `MissingPresenterFeedbackError`, `InvalidEvidenceReferenceError`, etc.).

## Testing Decisions

- Unit tests in `tests/unit/test_report_domain.py` verifying:
  - Weight summation checks (rejecting sums != 1.0).
  - Q&A 20% weight enforcement.
  - Score bounds, rounding, and label transitions.
  - Evidence reference shape and type checks.
  - Presenter coverage: all mapped members included, duplicates rejected, unmapped ignored.
  - Separation between team feedback and member feedback.

## Out of Scope

- Database persistence (covered in Spec 02).
- AI worker callbacks and Redis queue (covered in Spec 03).
- PDF rendering and HTTP routes (covered in Spec 04).

## Further Notes

- Respects architecture rules: domain code contains zero external dependencies.
