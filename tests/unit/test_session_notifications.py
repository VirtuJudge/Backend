import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application.ai_job_contracts import ErasureScope
from app.application.session_notification_contracts import (
    AnswerStatus,
    ErasureStatus,
    ErasureUpdatedNotification,
    NotificationEventName,
    PracticeSessionAnalysisProgressedNotification,
    PracticeSessionResyncRequiredNotification,
    PracticeSessionUpdatedNotification,
    PublicStage,
    QAAnswerUpdatedNotification,
    QAQuestionAvailableNotification,
    QuestionKind,
    QuestionState,
    ReportReadyNotification,
    ReportStatus,
    ResyncReason,
    map_to_public_stage,
    parse_notification,
)
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.enums.stage_status import StageStatus
from app.domain.session_workflow.enums.stage_type import StageType

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
SESSION_ID = str(uuid4())
ATTEMPT_ID = str(uuid4())
QA_ROUND_ID = str(uuid4())
QUESTION_ID = str(uuid4())
ANSWER_ID = str(uuid4())
REPORT_ID = str(uuid4())
EVAL_ID = str(uuid4())
ERASURE_ID = str(uuid4())
TRACE_ID = "trc_test_001"


# --- 1. Valid Creation and Serialization for All 7 Notifications ---


def test_practice_session_updated_valid() -> None:
    notif = PracticeSessionUpdatedNotification(
        sequence=1,
        practice_session_id=SESSION_ID,
        version=2,
        state=SessionStatus.ANALYZING,
        current_attempt=1,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.PRACTICE_SESSION_UPDATED
    assert notif.sequence == 1
    assert notif.version == 2
    assert notif.state == SessionStatus.ANALYZING
    assert notif.current_attempt == 1

    dumped = notif.model_dump()
    assert dumped["sequence"] == 1
    assert dumped["version"] == 2
    assert dumped["state"] == "analyzing"
    assert dumped["current_attempt"] == 1

    frame = notif.to_sse_frame()
    assert frame.startswith(
        f"id: 1\nevent: {NotificationEventName.PRACTICE_SESSION_UPDATED}\ndata: "
    )
    assert frame.endswith("\n\n")


def test_practice_session_analysis_progressed_valid() -> None:
    notif = PracticeSessionAnalysisProgressedNotification(
        sequence=2,
        practice_session_id=SESSION_ID,
        analysis_attempt_id=ATTEMPT_ID,
        analysis_attempt_number=1,
        stage=PublicStage.SPEECH,
        status=StageStatus.RUNNING,
        progress=0.45,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.PRACTICE_SESSION_ANALYSIS_PROGRESSED
    assert notif.stage == PublicStage.SPEECH
    assert notif.progress == 0.45
    assert notif.status == StageStatus.RUNNING

    data = json.loads(notif.model_dump_json())
    assert data["stage"] == "speech"
    assert data["progress"] == 0.45
    assert "message" not in data


def test_qa_question_available_valid() -> None:
    notif = QAQuestionAvailableNotification(
        sequence=3,
        practice_session_id=SESSION_ID,
        qa_round_id=QA_ROUND_ID,
        question_id=QUESTION_ID,
        position=1,
        kind=QuestionKind.PRIMARY,
        state=QuestionState.ACTIVE,
        version=1,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.QA_QUESTION_AVAILABLE
    assert notif.position == 1
    assert notif.kind == QuestionKind.PRIMARY
    assert notif.state == QuestionState.ACTIVE


def test_qa_answer_updated_valid() -> None:
    notif = QAAnswerUpdatedNotification(
        sequence=4,
        practice_session_id=SESSION_ID,
        qa_round_id=QA_ROUND_ID,
        question_id=QUESTION_ID,
        answer_id=ANSWER_ID,
        status=AnswerStatus.SUBMITTED,
        version=2,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.QA_ANSWER_UPDATED
    assert notif.status == AnswerStatus.SUBMITTED


def test_report_ready_valid() -> None:
    notif = ReportReadyNotification(
        sequence=5,
        practice_session_id=SESSION_ID,
        report_id=REPORT_ID,
        evaluation_id=EVAL_ID,
        status=ReportStatus.READY,
        version=10,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.REPORT_READY
    assert notif.status == ReportStatus.READY


def test_erasure_updated_valid() -> None:
    notif = ErasureUpdatedNotification(
        sequence=6,
        practice_session_id=SESSION_ID,
        erasure_request_id=ERASURE_ID,
        scope=ErasureScope.PRACTICE_SESSION,
        status=ErasureStatus.IN_PROGRESS,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.ERASURE_UPDATED
    assert notif.scope == ErasureScope.PRACTICE_SESSION
    assert notif.status == ErasureStatus.IN_PROGRESS

    # Also test ancestor-scoped erasure
    team_erasure = ErasureUpdatedNotification(
        sequence=7,
        practice_session_id=SESSION_ID,
        erasure_request_id=ERASURE_ID,
        scope=ErasureScope.TEAM,
        status=ErasureStatus.IN_PROGRESS,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert team_erasure.scope == ErasureScope.TEAM


def test_practice_session_resync_required_valid() -> None:
    notif = PracticeSessionResyncRequiredNotification(
        sequence=189,
        practice_session_id=SESSION_ID,
        reason=ResyncReason.CURSOR_TRIMMED,
        current_sequence=189,
        requested_sequence=120,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.event_name == NotificationEventName.PRACTICE_SESSION_RESYNC_REQUIRED
    assert notif.sequence == 189
    assert notif.current_sequence == 189
    assert notif.requested_sequence == 120
    assert notif.reason == ResyncReason.CURSOR_TRIMMED


def test_resync_allows_sequence_zero_for_empty_stream() -> None:
    notif = PracticeSessionResyncRequiredNotification(
        sequence=0,
        practice_session_id=SESSION_ID,
        reason=ResyncReason.CURSOR_MISSING,
        current_sequence=0,
        requested_sequence=None,
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.sequence == 0
    assert notif.current_sequence == 0
    assert notif.requested_sequence is None


# --- 2. Stage Mapping Rules ---


def test_map_to_public_stage_internal_to_processing() -> None:
    # Internal StageType enum values must map to processing if internal
    assert map_to_public_stage(StageType.TRANSCRIPTION) == PublicStage.PROCESSING
    assert map_to_public_stage(StageType.DOCUMENT_ANALYSIS) == PublicStage.PROCESSING
    assert map_to_public_stage(StageType.SCORING) == PublicStage.PROCESSING

    # String values of internal stages
    assert map_to_public_stage("transcription") == PublicStage.PROCESSING
    assert map_to_public_stage("document_analysis") == PublicStage.PROCESSING
    assert map_to_public_stage("scoring") == PublicStage.PROCESSING


def test_map_to_public_stage_recognized_public_stages() -> None:
    assert map_to_public_stage("ingestion") == PublicStage.INGESTION
    assert map_to_public_stage("speech") == PublicStage.SPEECH
    assert map_to_public_stage("diarization") == PublicStage.DIARIZATION
    assert map_to_public_stage(StageType.DIARIZATION) == PublicStage.DIARIZATION
    assert map_to_public_stage("vision") == PublicStage.VISION
    assert map_to_public_stage("audio_features") == PublicStage.AUDIO_FEATURES
    assert map_to_public_stage("documents") == PublicStage.DOCUMENTS
    assert map_to_public_stage("aggregation") == PublicStage.AGGREGATION
    assert map_to_public_stage("grounding") == PublicStage.GROUNDING
    assert map_to_public_stage("questions") == PublicStage.QUESTIONS
    assert map_to_public_stage("answers") == PublicStage.ANSWERS
    assert map_to_public_stage("report") == PublicStage.REPORT
    assert map_to_public_stage(StageType.REPORT) == PublicStage.REPORT
    assert map_to_public_stage("processing") == PublicStage.PROCESSING


def test_map_to_public_stage_unknown_defaults_to_processing() -> None:
    assert map_to_public_stage("unknown_stage_name") == PublicStage.PROCESSING
    assert map_to_public_stage("custom_whisper_step") == PublicStage.PROCESSING
    assert map_to_public_stage("") == PublicStage.PROCESSING
    assert map_to_public_stage(None) == PublicStage.PROCESSING


def test_from_worker_progress_drops_message_and_maps_stage() -> None:
    notif = PracticeSessionAnalysisProgressedNotification.from_worker_progress(
        sequence=10,
        practice_session_id=SESSION_ID,
        analysis_attempt_id=ATTEMPT_ID,
        analysis_attempt_number=1,
        worker_stage="transcription",
        status="running",
        progress=0.75,
        worker_message="Transcribing raw audio via whisper",
        occurred_at=NOW,
        trace_id=TRACE_ID,
    )
    assert notif.stage == PublicStage.PROCESSING
    assert notif.progress == 0.75
    # The serialized payload must NOT contain worker_message or message
    data = notif.model_dump()
    assert "message" not in data
    assert "worker_message" not in data


# --- 3. Sequence Validation ---


def test_persisted_notification_rejects_zero_or_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        PracticeSessionUpdatedNotification(
            sequence=0,
            practice_session_id=SESSION_ID,
            version=1,
            state=SessionStatus.READY,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )

    with pytest.raises(ValidationError):
        PracticeSessionAnalysisProgressedNotification(
            sequence=-1,
            practice_session_id=SESSION_ID,
            analysis_attempt_id=ATTEMPT_ID,
            analysis_attempt_number=1,
            stage=PublicStage.VISION,
            status=StageStatus.RUNNING,
            progress=0.5,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )


def test_resync_rejects_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        PracticeSessionResyncRequiredNotification(
            sequence=-1,
            practice_session_id=SESSION_ID,
            reason=ResyncReason.CURSOR_MISSING,
            current_sequence=0,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )


# --- 4. Resource Version Validation ---


def test_persisted_notification_rejects_invalid_version() -> None:
    with pytest.raises(ValidationError):
        PracticeSessionUpdatedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            version=0,
            state=SessionStatus.READY,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )

    with pytest.raises(ValidationError):
        QAQuestionAvailableNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            qa_round_id=QA_ROUND_ID,
            question_id=QUESTION_ID,
            position=1,
            kind=QuestionKind.PRIMARY,
            state=QuestionState.ACTIVE,
            version=-2,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )


# --- 5. Progress Boundary Validation ---


def test_progress_outside_zero_to_one_rejected() -> None:
    with pytest.raises(ValidationError):
        PracticeSessionAnalysisProgressedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            analysis_attempt_id=ATTEMPT_ID,
            analysis_attempt_number=1,
            stage=PublicStage.SPEECH,
            status=StageStatus.RUNNING,
            progress=-0.01,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )

    with pytest.raises(ValidationError):
        PracticeSessionAnalysisProgressedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            analysis_attempt_id=ATTEMPT_ID,
            analysis_attempt_number=1,
            stage=PublicStage.SPEECH,
            status=StageStatus.RUNNING,
            progress=1.01,
            occurred_at=NOW,
            trace_id=TRACE_ID,
        )


# --- 6. Strict Exclusion of Private Data & Extraneous Fields ---


@pytest.mark.parametrize(
    "leaked_field, leaked_value",
    [
        ("transcript", "Candidate said hello world during pitch"),
        ("transcripts", ["segment 1", "segment 2"]),
        ("prompt", "You are a judging AI, evaluate this strictly"),
        ("prompts", ["system prompt"]),
        ("evidence", {"citation": "page 4", "content": "secret text"}),
        ("evidence_content", "pitch text"),
        ("object_key", "uploads/team_123/session_456/pitch.mp4"),
        ("signed_url", "https://s3.amazonaws.com/bucket/file?sig=secret"),
        ("download_url", "https://s3.amazonaws.com/bucket/file?sig=secret"),
        ("message", "Internal worker progress note"),
        ("worker_message", "Transcription running on worker-4"),
        ("provider_response", {"model": "gemini", "tokens": 1200}),
        ("raw_error", "Traceback (most recent call last): Exception: boom"),
        ("stack_trace", "Traceback..."),
    ],
)
def test_all_models_forbid_sensitive_and_private_fields(
    leaked_field: str, leaked_value: object
) -> None:
    # 1. PracticeSessionUpdatedNotification
    with pytest.raises(ValidationError):
        PracticeSessionUpdatedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            version=1,
            state=SessionStatus.READY,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 2. PracticeSessionAnalysisProgressedNotification
    with pytest.raises(ValidationError):
        PracticeSessionAnalysisProgressedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            analysis_attempt_id=ATTEMPT_ID,
            analysis_attempt_number=1,
            stage=PublicStage.SPEECH,
            status=StageStatus.RUNNING,
            progress=0.5,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 3. QAQuestionAvailableNotification
    with pytest.raises(ValidationError):
        QAQuestionAvailableNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            qa_round_id=QA_ROUND_ID,
            question_id=QUESTION_ID,
            position=1,
            kind=QuestionKind.PRIMARY,
            state=QuestionState.ACTIVE,
            version=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 4. QAAnswerUpdatedNotification
    with pytest.raises(ValidationError):
        QAAnswerUpdatedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            qa_round_id=QA_ROUND_ID,
            question_id=QUESTION_ID,
            answer_id=ANSWER_ID,
            status=AnswerStatus.SUBMITTED,
            version=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 5. ReportReadyNotification
    with pytest.raises(ValidationError):
        ReportReadyNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            report_id=REPORT_ID,
            evaluation_id=EVAL_ID,
            status=ReportStatus.READY,
            version=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 6. ErasureUpdatedNotification
    with pytest.raises(ValidationError):
        ErasureUpdatedNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            erasure_request_id=ERASURE_ID,
            scope=ErasureScope.PRACTICE_SESSION,
            status=ErasureStatus.IN_PROGRESS,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )

    # 7. PracticeSessionResyncRequiredNotification
    with pytest.raises(ValidationError):
        PracticeSessionResyncRequiredNotification(
            sequence=1,
            practice_session_id=SESSION_ID,
            reason=ResyncReason.CURSOR_MISSING,
            current_sequence=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            **{leaked_field: leaked_value},
        )


# --- 7. Parser / Dispatcher Helper & Event Name Validation ---


def test_parse_notification_valid() -> None:
    data = {
        "event_name": "practice_session.updated.v1",
        "sequence": 1,
        "practice_session_id": SESSION_ID,
        "version": 1,
        "state": "ready",
        "occurred_at": NOW.isoformat(),
        "trace_id": TRACE_ID,
    }
    notif = parse_notification(data)
    assert isinstance(notif, PracticeSessionUpdatedNotification)
    assert notif.event_name == NotificationEventName.PRACTICE_SESSION_UPDATED


def test_parse_notification_rejects_invalid_event_name() -> None:
    data = {
        "event_name": "invalid.event.name.v1",
        "sequence": 1,
        "practice_session_id": SESSION_ID,
        "occurred_at": NOW.isoformat(),
        "trace_id": TRACE_ID,
    }
    with pytest.raises(ValueError, match="Invalid or unknown event name"):
        parse_notification(data)
