import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.application.ai_job_contracts import (
    AIWorkerUpdateStatus,
    ArtifactRef,
    ReportCompletedPayload,
)
from app.application.ai_jobs import AIJobs
from app.domain.asset import StorageUnavailable
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.entities.qa_round import Answer, QARound, Question
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.entities.speaker_mapping import SpeakerMapping
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.qa import (
    AnswerStatus,
    QARoundState,
    QuestionKind,
    QuestionState,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import CompletedResultValidationError
from tests.support import FakeObjectStorage, FakeUnitOfWork
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fake_analysis_attempt_repository import FakeAnalysisAttemptRepository
from tests.support.fake_analysis_job_repository import FakeAnalysisJobRepository
from tests.support.fake_report_repository import FakeReportRepository
from tests.unit.test_ai_jobs_record_update import _make_update

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


class _FakeSessionsRepo:
    def __init__(self, sessions: list[PracticeSession]) -> None:
        self.sessions = {s.id: s for s in sessions}

    async def get_by_id(self, session_id: Any) -> PracticeSession | None:
        return self.sessions.get(session_id)

    async def update(
        self, session: PracticeSession, *, expected_version: int | None = None
    ) -> PracticeSession:
        self.sessions[session.id] = session
        return session


def _setup_report_pipeline() -> tuple[
    AIJobs,
    FakeUnitOfWork,
    PracticeSession,
    AnalysisAttempt,
    AnalysisJob,
    QARound,
]:
    session_id = uuid4()
    project_id = uuid4()
    attempt_id = uuid4()
    round_id = uuid4()
    actor_id = uuid4()
    manifest_id = uuid4()
    job_id = uuid4()

    session = PracticeSession(
        id=session_id,
        project_id=project_id,
        created_by=actor_id,
        name="Pitch Session",
        status=SessionStatus.REPORT_GENERATING,
        version=3,
        created_at=NOW,
        updated_at=NOW,
        consent_granted=True,
        started_at=NOW,
        completed_at=None,
        cancelled_at=None,
    )

    attempt = AnalysisAttempt(
        id=attempt_id,
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key=None,
        attempt_number=1,
        status=AnalysisAttemptStatus.RUNNING,
        failure_code=None,
        failure_message=None,
        created_at=NOW,
        started_at=NOW,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )

    manifest = SessionManifest(
        id=manifest_id,
        session_id=session_id,
        presentation_version_id=uuid4(),
        supporting_document_version_ids=[],
        rubric_id="startup_pitch",
        rubric_version=1,
        snapshot={},
        frozen_at=NOW,
    )

    round_ = QARound(
        id=round_id,
        practice_session_id=session_id,
        analysis_attempt_id=attempt_id,
        state=QARoundState.COMPLETED,
        current_question_id=None,
        follow_up_count=0,
        version=2,
        created_at=NOW,
        updated_at=NOW,
    )
    question = Question(
        id=uuid4(),
        qa_round_id=round_id,
        practice_session_id=session_id,
        kind=QuestionKind.PRIMARY,
        position=1,
        text="How will you acquire your first customers?",
        reason="Tests the go-to-market plan.",
        rubric_dimension="business_reasoning",
        evidence_ids=["ev_01"],
        parent_answer_id=None,
        state=QuestionState.ANSWERED,
        created_at=NOW,
    )
    answer = Answer(
        id=uuid4(),
        qa_round_id=round_id,
        question_id=question.id,
        answered_by=actor_id,
        status=AnswerStatus.SUBMITTED,
        audio_asset_version_id=uuid4(),
        duration_ms=42_000,
        transcript_artifact_id="transcript_01",
        assessment_artifact_id="assessment_01",
        submitted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )

    job = AnalysisJob(
        id=job_id,
        practice_session_id=session_id,
        attempt_id=attempt_id,
        analysis_attempt=1,
        job_type="generate_report",
        status=AnalysisJobStatus.RUNNING,
        correlation_id=uuid4(),
        last_update_sequence=1,
        payload_version=1,
        attempts=1,
        cancel_requested=False,
        retry_count=0,
        last_error=None,
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
        completed_at=None,
        payload={"trace_id": "trc_test_report_01", "payload": {"report_id": str(uuid4())}},
    )

    presenter_id = uuid4()
    speaker_mapping = SpeakerMapping(
        id=uuid4(),
        attempt_id=attempt_id,
        speaker_label="SPEAKER_00",
        member_id=uuid4(),
        mapped_by=actor_id,
        mapped_at=NOW,
        user_id=presenter_id,
    )

    sessions_repo = _FakeSessionsRepo([session])
    attempts_repo = FakeAnalysisAttemptRepository([attempt])
    ancestry = AIJobAncestryContext(
        job=job,
        attempt=attempt,
        session=session,
        project_id=project_id,
        team_id=uuid4(),
    )
    jobs_repo = FakeAnalysisJobRepository([job], ancestry_contexts={job.id: ancestry})
    reports_repo = FakeReportRepository()

    uow = FakeUnitOfWork(
        sessions=sessions_repo,
        attempts=attempts_repo,
        jobs=jobs_repo,
        reports=reports_repo,
    )
    uow.manifests = MagicMock()
    uow.manifests.get_by_session_id = AsyncMock(return_value=manifest)
    uow.qa.get_round_by_session = AsyncMock(return_value=round_)
    uow.qa.list_questions = AsyncMock(return_value=[question])
    uow.qa.list_answers = AsyncMock(return_value=[answer])
    uow.speaker_mappings = MagicMock()
    uow.speaker_mappings.get_by_attempt_id = AsyncMock(return_value=[speaker_mapping])

    notifications = AsyncMock()
    notifications.publish = AsyncMock()

    storage = FakeObjectStorage()
    artifact_prefix = f"ai/session/{session.id}/answers/{answer.id}"
    storage.objects[f"{artifact_prefix}/transcript.json"] = json.dumps(
        {"transcript": {"text": "We will win our first customers through partners."}}
    ).encode()
    storage.objects[f"{artifact_prefix}/assessment.json"] = json.dumps(
        {"assessment": {"text": "Specific go-to-market plan.", "evidence_ids": ["ev_01"]}}
    ).encode()
    service = AIJobs(
        uow,
        queue=FakeAIJobQueue(),
        notifications=notifications,
        storage=storage,
    )
    return service, uow, session, attempt, job, round_


def _evaluation_artifact(user_id: str) -> bytes:
    component_specs = [
        ("pitch_content_and_evidence", 0.25, 0.60, 60, "good"),
        ("business_and_problem_solution_reasoning", 0.20, 0.50, 50, "developing"),
        ("technical_feasibility", 0.15, 0.40, 40, "developing"),
        ("delivery_and_body_language", 0.15, 0.85, 85, "strong"),
        ("timing_and_speech_mechanics", 0.05, 0.77, 77, "good"),
        ("qa_quality", 0.20, 0.75, 75, "good"),
    ]
    components = [
        {
            "dimension": dimension,
            "status": "scored",
            "configured_weight": weight,
            "normalized_score": score,
            "display_score": display,
            "label": label,
            "effective_weight": weight,
            "evidence_ids": [f"ev_{index}"],
            "rationale": f"Grounded rationale for {dimension}.",
            "limitation_code": None,
        }
        for index, (dimension, weight, score, display, label) in enumerate(component_specs, start=1)
    ]
    strength = {
        "id": "team-strength",
        "kind": "strength",
        "title": "Clear phased roadmap",
        "detail": "The presentation provides a clear phased roadmap.",
        "recommendation": "Keep the milestones visible in future pitches.",
        "evidence_ids": ["ev_1"],
        "rubric_dimension": "pitch_content_and_evidence",
        "speaker_labels": [],
    }
    improvement = {
        "id": "team-improvement",
        "kind": "improvement",
        "title": "Add technical detail",
        "detail": "The technical feasibility explanation needs concrete details.",
        "recommendation": "Add a technical architecture diagram.",
        "evidence_ids": ["ev_3"],
        "rubric_dimension": "technical_feasibility",
        "speaker_labels": [],
    }
    payload = {
        "id": "01M2K8N0A5TD92YKD6ENT93B7V",
        "schema_version": 1,
        "analysis_attempt_id": "worker-attempt",
        "qa_round_id": "worker-round",
        "rubric": {"rubric_id": "startup_pitch", "version": 1},
        "overall_score": 0.6251,
        "components": components,
        "findings": [strength, improvement],
        "team_feedback": {
            "summary": "The pitch has a clear phased roadmap but needs stronger technical detail.",
            "strengths": [strength],
            "improvements": [improvement],
            "score_components": components,
            "limitations": [],
        },
        "member_feedback": [
            {
                "user_id": user_id,
                "display_name": "Presenter (SPEAKER_00)",
                "speaker_labels": ["SPEAKER_00"],
                "summary": "Delivery was confident with opportunities to reduce filler words.",
                "strengths": [strength],
                "improvements": [improvement],
                "delivery_components": components[3:5],
            }
        ],
        "limitations": [],
        "reproducibility": {"pipeline_version": "0.1.0"},
    }
    return json.dumps(payload).encode()


@pytest.mark.asyncio
async def test_record_update_report_completed_persists_canonical_report_and_evaluation() -> None:
    service, uow, session, attempt, job, round_ = _setup_report_pipeline()
    storage = service._storage
    assert isinstance(storage, FakeObjectStorage)
    presenter_id = uow.speaker_mappings.get_by_attempt_id.return_value[0].user_id
    evaluation_key = (
        f"projects/{session.project_id}/sessions/{session.id}/attempts/1/evaluation.json"
    )
    evaluation_bytes = _evaluation_artifact(str(presenter_id))
    storage.objects[evaluation_key] = evaluation_bytes

    payload = ReportCompletedPayload(
        evaluation_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000071",
            object_key=evaluation_key,
            checksum=f"sha256:{hashlib.sha256(evaluation_bytes).hexdigest()}",
            schema_version=1,
        ),
        report_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000072",
            object_key=f"projects/{session.project_id}/sessions/{session.id}/attempts/1/report.json",
            checksum="sha256:" + "b" * 64,
            schema_version=1,
        ),
        member_feedback_user_ids=[str(presenter_id)],
        limitations=[],
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
        trace_id="trc_test_report_01",
    )

    result = await service.record_update(job.id, update)

    assert result is not None
    assert result.status is AnalysisJobStatus.COMPLETED

    # Verify session and attempt advanced to COMPLETED
    updated_session = await uow.sessions.get_by_id(session.id)
    assert updated_session is not None
    assert updated_session.status is SessionStatus.COMPLETED

    updated_attempt = await uow.attempts.get_by_id(attempt.id)
    assert updated_attempt is not None
    assert updated_attempt.status is AnalysisAttemptStatus.COMPLETED

    # Verify report and evaluation persisted
    stored_report = await uow.reports.get_report_by_session(session.id)
    assert stored_report is not None
    assert stored_report.overall_score == 0.6251
    assert stored_report.title == "Pitch Session — Final Evaluation Report"
    assert len(stored_report.score_components) == 6
    assert stored_report.team_feedback.summary.startswith("The pitch has a clear phased roadmap")

    # Check 20% Q&A weight constraint
    qa_component = next(
        (c for c in stored_report.score_components if c.dimension == "qa_quality"), None
    )
    assert qa_component is not None
    assert qa_component.configured_weight == 0.20

    stored_eval = await uow.reports.get_evaluation_by_session(session.id)
    assert stored_eval is not None
    assert stored_eval.rubric_id == "startup_pitch"

    # Presenter feedback exists for mapped presenter
    assert len(stored_report.member_feedback) == 1
    assert len(stored_eval.member_feedback) == 1

    # Notification published
    notifications: Any = service._notifications
    assert notifications is not None
    notifications.publish.assert_awaited_once()
    published_notification = notifications.publish.call_args[0][0]
    assert published_notification.event_name == "report.ready.v1"
    assert published_notification.payload["status"] == "ready"
    assert published_notification.payload["report_id"] == str(stored_report.id)


@pytest.mark.asyncio
async def test_report_job_can_start_after_session_analysis_completed() -> None:
    service, uow, session, attempt, job, round_ = _setup_report_pipeline()
    attempt.status = AnalysisAttemptStatus.RUNNING
    report_job = await service.create_report_job(
        session=session,
        round_=round_,
        attempt=attempt,
        now=NOW,
    )
    report_job.status = AnalysisJobStatus.QUEUED

    result = await service.record_update(
        report_job.id,
        _make_update(AIWorkerUpdateStatus.STARTED, sequence=1),
    )

    assert result is not None
    assert result.status is AnalysisJobStatus.RUNNING
    assert attempt.status is AnalysisAttemptStatus.RUNNING


@pytest.mark.asyncio
async def test_report_job_uploads_qa_artifact_before_queueing_with_matching_checksum() -> None:
    service, uow, session, attempt, _job, round_ = _setup_report_pipeline()
    storage = service._storage
    assert isinstance(storage, FakeObjectStorage)

    report_job = await service.create_report_job(
        session=session,
        round_=round_,
        attempt=attempt,
        now=NOW,
    )

    expected_key = (
        f"projects/{session.project_id}/sessions/{session.id}/attempts/"
        f"{attempt.attempt_number}/qa.json"
    )
    uploaded = storage.objects[expected_key]
    assert report_job.payload is not None
    payload = report_job.payload["payload"]

    assert payload["qa_artifact"]["object_key"] == expected_key
    assert payload["qa_artifact"]["checksum"] == f"sha256:{hashlib.sha256(uploaded).hexdigest()}"
    assert json.loads(uploaded) == {
        "analysis_attempt": attempt.attempt_number,
        "answers": [
            {
                "answered_by": str(uow.qa.list_answers.return_value[0].answered_by),
                "assessment_artifact_id": "assessment_01",
                "audio_asset_version_id": str(
                    uow.qa.list_answers.return_value[0].audio_asset_version_id
                ),
                "duration_ms": 42_000,
                "id": str(uow.qa.list_answers.return_value[0].id),
                "question_id": str(uow.qa.list_questions.return_value[0].id),
                "status": "submitted",
                "submitted_at": NOW.isoformat(),
                "transcript": "We will win our first customers through partners.",
                "transcript_artifact_id": "transcript_01",
            }
        ],
        "assessments": [
            {
                "assessment_artifact_id": "assessment_01",
                "assessment_text": "Specific go-to-market plan.",
                "evidence_ids": ["ev_01"],
                "question_id": str(uow.qa.list_questions.return_value[0].id),
            }
        ],
        "artifact_id": payload["qa_artifact"]["artifact_id"],
        "metadata": {
            "completed_at": NOW.isoformat(),
            "question_count": 1,
            "round_state": "completed",
        },
        "practice_session_id": str(session.id),
        "qa_round_id": str(round_.id),
        "questions": [
            {
                "answer_id": str(uow.qa.list_answers.return_value[0].id),
                "evidence_ids": ["ev_01"],
                "id": str(uow.qa.list_questions.return_value[0].id),
                "kind": "primary",
                "parent_answer_id": None,
                "position": 1,
                "reason": "Tests the go-to-market plan.",
                "rubric_dimension": "business_reasoning",
                "state": "answered",
                "text": "How will you acquire your first customers?",
            }
        ],
        "schema_version": 1,
    }

    assert await service.dispatch(report_job, now=NOW) is True
    queue = service.queue
    assert isinstance(queue, FakeAIJobQueue)
    queued = queue.last_message
    assert queued is not None
    queued_artifact = queued.model_dump()["payload"]["qa_artifact"]
    assert queued_artifact["object_key"] == expected_key
    assert queued_artifact["checksum"] == f"sha256:{hashlib.sha256(uploaded).hexdigest()}"


@pytest.mark.asyncio
async def test_report_job_does_not_dispatch_when_qa_artifact_upload_fails() -> None:
    service, uow, session, attempt, _job, round_ = _setup_report_pipeline()
    storage = service._storage
    assert isinstance(storage, FakeObjectStorage)
    storage.transient_failure = True
    assert isinstance(uow.jobs, FakeAnalysisJobRepository)
    existing_job_ids = set(uow.jobs.jobs)

    with pytest.raises(StorageUnavailable):
        await service.create_report_job(
            session=session,
            round_=round_,
            attempt=attempt,
            now=NOW,
        )

    assert set(uow.jobs.jobs) == existing_job_ids
    queue = service.queue
    assert isinstance(queue, FakeAIJobQueue)
    assert queue.count == 0


@pytest.mark.asyncio
async def test_record_update_rejects_mismatched_trace_id() -> None:
    service, uow, session, attempt, job, round_ = _setup_report_pipeline()

    payload = ReportCompletedPayload(
        evaluation_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000071",
            object_key=f"projects/{session.project_id}/sessions/{session.id}/attempts/1/evaluation.json",
            checksum="sha256:" + "a" * 64,
            schema_version=1,
        ),
        report_artifact=ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000072",
            object_key=f"projects/{session.project_id}/sessions/{session.id}/attempts/1/report.json",
            checksum="sha256:" + "b" * 64,
            schema_version=1,
        ),
        member_feedback_user_ids=[],
        limitations=[],
    )
    update = _make_update(
        AIWorkerUpdateStatus.COMPLETED,
        sequence=2,
        payload=payload,
        trace_id="trc_wrong_trace_id",
    )

    with pytest.raises(CompletedResultValidationError) as exc:
        await service.record_update(job.id, update)

    assert "trace_id" in str(exc.value)


@pytest.mark.asyncio
async def test_record_update_rejects_invalid_artifact_checksum() -> None:
    service, uow, session, attempt, job, round_ = _setup_report_pipeline()

    with pytest.raises(ValueError):
        ArtifactRef(
            artifact_id="01JEXAMPLE0000000000000071",
            object_key=f"projects/{session.project_id}/sessions/{session.id}/attempts/1/evaluation.json",
            checksum="invalid_checksum_format",
            schema_version=1,
        )
