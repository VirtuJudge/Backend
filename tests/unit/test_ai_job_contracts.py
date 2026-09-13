import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    AnalyzeAnswerPayload,
    AnalyzeAnswerQueueMessage,
    AnalyzeSessionPayload,
    AnalyzeSessionQueueMessage,
    AnswerAnalysisCompletedPayload,
    AnswerAnalysisCompletedWorkerUpdate,
    CancelledPayload,
    CancelledWorkerUpdate,
    EraseAIDataPayload,
    EraseAIDataQueueMessage,
    ErasureCompletedPayload,
    ErasureCompletedWorkerUpdate,
    ErasureScope,
    FailedPayload,
    FailedWorkerUpdate,
    GenerateReportPayload,
    GenerateReportQueueMessage,
    ProgressPayload,
    ProgressWorkerUpdate,
    ReportCompletedPayload,
    ReportCompletedWorkerUpdate,
    SessionAnalysisCompletedPayload,
    SessionAnalysisCompletedWorkerUpdate,
    StartedPayload,
    StartedWorkerUpdate,
    get_completed_payload_model,
    get_completed_update_model,
    get_job_payload_model,
    parse_completed_payload,
    parse_queue_message,
    parse_worker_update,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "contracts" / "fixtures" / "ai"


def _load_fixture(filename: str) -> dict[str, Any]:
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_analyze_session_valid_fixture_parses() -> None:
    data = _load_fixture("job_analyze_session_valid.json")
    msg = AIJobQueueMessage.model_validate(data)

    assert msg.schema_version == 1
    assert msg.job_type == AIJobType.ANALYZE_SESSION
    assert isinstance(msg.payload, AnalyzeSessionPayload)
    assert msg.payload.presentation.duration_ms == 180000
    assert len(msg.payload.supporting_documents) == 1
    assert msg.payload.rubric.rubric_id == "rubric_seed_default"

    typed = parse_queue_message(data)
    assert isinstance(typed, AnalyzeSessionQueueMessage)
    assert typed.payload == msg.payload

    method_typed = msg.to_typed_message()
    assert isinstance(method_typed, AnalyzeSessionQueueMessage)


def test_analyze_answer_valid_fixture_parses() -> None:
    data = _load_fixture("job_analyze_answer_valid.json")
    msg = AIJobQueueMessage.model_validate(data)

    assert msg.schema_version == 1
    assert msg.job_type == AIJobType.ANALYZE_ANSWER
    assert isinstance(msg.payload, AnalyzeAnswerPayload)
    assert msg.payload.audio.duration_ms == 45000
    assert msg.payload.remaining_follow_ups == 2

    typed = parse_queue_message(data)
    assert isinstance(typed, AnalyzeAnswerQueueMessage)
    assert typed.payload == msg.payload


def test_generate_report_valid_fixture_parses() -> None:
    data = _load_fixture("job_generate_report_valid.json")
    msg = AIJobQueueMessage.model_validate(data)

    assert msg.schema_version == 1
    assert msg.job_type == AIJobType.GENERATE_REPORT
    assert isinstance(msg.payload, GenerateReportPayload)
    assert msg.payload.report_id == "rep_001"
    assert len(msg.payload.speaker_mappings) == 1
    assert msg.payload.speaker_mappings[0].speaker_label == "SPEAKER_00"

    typed = parse_queue_message(data)
    assert isinstance(typed, GenerateReportQueueMessage)
    assert typed.payload == msg.payload


def test_erase_ai_data_valid_fixture_parses() -> None:
    data = _load_fixture("job_erase_ai_data_valid.json")
    msg = AIJobQueueMessage.model_validate(data)

    assert msg.schema_version == 1
    assert msg.job_type == AIJobType.ERASE_AI_DATA
    assert isinstance(msg.payload, EraseAIDataPayload)
    assert msg.payload.scope == ErasureScope.PRACTICE_SESSION

    typed = parse_queue_message(data)
    assert isinstance(typed, EraseAIDataQueueMessage)
    assert typed.payload == msg.payload


def test_worker_update_started_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_started_valid.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.schema_version == 1
    assert update.sequence == 1
    assert update.status == AIWorkerUpdateStatus.STARTED
    assert isinstance(update.payload, StartedPayload)
    assert update.payload.pipeline_version == "1.0.0"

    typed = parse_worker_update(data)
    assert isinstance(typed, StartedWorkerUpdate)
    assert typed.payload.pipeline_version == "1.0.0"


def test_worker_update_progress_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_progress_valid.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.PROGRESS
    assert isinstance(update.payload, ProgressPayload)
    assert update.payload.stage == "transcription"
    assert update.payload.progress == 0.5

    typed = parse_worker_update(data)
    assert isinstance(typed, ProgressWorkerUpdate)


def test_worker_update_failed_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_failed_valid.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.FAILED
    assert isinstance(update.payload, FailedPayload)
    assert update.payload.code == "JOB_PROCESSING_FAILED"
    assert not update.payload.retryable

    typed = parse_worker_update(data)
    assert isinstance(typed, FailedWorkerUpdate)


def test_worker_update_cancelled_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_cancelled_valid.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.CANCELLED
    assert isinstance(update.payload, CancelledPayload)

    typed = parse_worker_update(data)
    assert isinstance(typed, CancelledWorkerUpdate)


def test_worker_update_completed_analyze_session_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_completed_analyze_session.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.COMPLETED
    completed_payload = update.parse_completed_payload(AIJobType.ANALYZE_SESSION)
    assert isinstance(completed_payload, SessionAnalysisCompletedPayload)
    assert len(completed_payload.primary_questions) == 3
    assert len(completed_payload.speaker_labels) == 2
    assert len(completed_payload.limitations) == 1

    typed = parse_worker_update(data, job_type=AIJobType.ANALYZE_SESSION)
    assert isinstance(typed, SessionAnalysisCompletedWorkerUpdate)
    assert typed.payload == completed_payload


def test_worker_update_completed_analyze_answer_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_completed_analyze_answer.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.COMPLETED
    completed_payload = update.parse_completed_payload(AIJobType.ANALYZE_ANSWER)
    assert isinstance(completed_payload, AnswerAnalysisCompletedPayload)
    assert completed_payload.answer_id == "ans_001"
    assert completed_payload.follow_up is not None

    typed = parse_worker_update(data, job_type=AIJobType.ANALYZE_ANSWER)
    assert isinstance(typed, AnswerAnalysisCompletedWorkerUpdate)
    assert typed.payload == completed_payload


def test_worker_update_completed_generate_report_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_completed_generate_report.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.COMPLETED
    completed_payload = update.parse_completed_payload(AIJobType.GENERATE_REPORT)
    assert isinstance(completed_payload, ReportCompletedPayload)
    assert completed_payload.member_feedback_user_ids == ["user_founder_01"]

    typed = parse_worker_update(data, job_type=AIJobType.GENERATE_REPORT)
    assert isinstance(typed, ReportCompletedWorkerUpdate)
    assert typed.payload == completed_payload


def test_worker_update_completed_erase_ai_data_valid_fixture_parses() -> None:
    data = _load_fixture("worker_update_completed_erase_ai_data.json")
    update = AIWorkerUpdate.model_validate(data)

    assert update.status == AIWorkerUpdateStatus.COMPLETED
    completed_payload = update.parse_completed_payload(AIJobType.ERASE_AI_DATA)
    assert isinstance(completed_payload, ErasureCompletedPayload)
    assert completed_payload.deleted_records == 14
    assert completed_payload.deleted_objects == 6

    typed = parse_worker_update(data, job_type=AIJobType.ERASE_AI_DATA)
    assert isinstance(typed, ErasureCompletedWorkerUpdate)
    assert typed.payload == completed_payload


def test_completed_results_are_distinguished_by_job_type_without_guessing() -> None:
    session_data = _load_fixture("worker_update_completed_analyze_session.json")
    answer_data = _load_fixture("worker_update_completed_analyze_answer.json")
    report_data = _load_fixture("worker_update_completed_generate_report.json")
    erasure_data = _load_fixture("worker_update_completed_erase_ai_data.json")

    assert get_completed_payload_model(AIJobType.ANALYZE_SESSION) is SessionAnalysisCompletedPayload
    assert get_completed_payload_model(AIJobType.ANALYZE_ANSWER) is AnswerAnalysisCompletedPayload
    assert get_completed_payload_model(AIJobType.GENERATE_REPORT) is ReportCompletedPayload
    assert get_completed_payload_model(AIJobType.ERASE_AI_DATA) is ErasureCompletedPayload

    assert (
        get_completed_update_model(AIJobType.ANALYZE_SESSION)
        is SessionAnalysisCompletedWorkerUpdate
    )
    assert (
        get_completed_update_model(AIJobType.ANALYZE_ANSWER) is AnswerAnalysisCompletedWorkerUpdate
    )
    assert get_completed_update_model(AIJobType.GENERATE_REPORT) is ReportCompletedWorkerUpdate
    assert get_completed_update_model(AIJobType.ERASE_AI_DATA) is ErasureCompletedWorkerUpdate

    with pytest.raises(ValidationError):
        parse_completed_payload(AIJobType.ANALYZE_ANSWER, session_data["payload"])

    with pytest.raises(ValidationError):
        parse_worker_update(session_data, job_type=AIJobType.ANALYZE_ANSWER)

    with pytest.raises(ValidationError):
        parse_completed_payload(AIJobType.GENERATE_REPORT, answer_data["payload"])

    with pytest.raises(ValidationError):
        parse_worker_update(answer_data, job_type=AIJobType.GENERATE_REPORT)

    with pytest.raises(ValidationError):
        parse_completed_payload(AIJobType.ERASE_AI_DATA, report_data["payload"])

    with pytest.raises(ValidationError):
        parse_worker_update(report_data, job_type=AIJobType.ERASE_AI_DATA)

    with pytest.raises(ValidationError):
        parse_completed_payload(AIJobType.ANALYZE_SESSION, erasure_data["payload"])

    with pytest.raises(ValidationError):
        parse_worker_update(erasure_data, job_type=AIJobType.ANALYZE_SESSION)


def test_job_payload_models_mapping() -> None:
    assert get_job_payload_model(AIJobType.ANALYZE_SESSION) is AnalyzeSessionPayload
    assert get_job_payload_model(AIJobType.ANALYZE_ANSWER) is AnalyzeAnswerPayload
    assert get_job_payload_model(AIJobType.GENERATE_REPORT) is GenerateReportPayload
    assert get_job_payload_model(AIJobType.ERASE_AI_DATA) is EraseAIDataPayload


def test_job_missing_required_fails() -> None:
    data = _load_fixture("job_missing_required.json")
    with pytest.raises(ValidationError) as exc:
        AIJobQueueMessage.model_validate(data)
    assert "rubric" in str(exc.value)


def test_job_unknown_enum_fails() -> None:
    data = _load_fixture("job_unknown_enum.json")
    with pytest.raises(ValidationError) as exc:
        AIJobQueueMessage.model_validate(data)
    assert "job_type" in str(exc.value)


def test_job_erase_ai_data_unknown_scope_fails() -> None:
    data = _load_fixture("job_erase_ai_data_unknown_scope.json")
    with pytest.raises(ValidationError) as exc:
        AIJobQueueMessage.model_validate(data)
    assert "scope" in str(exc.value)


def test_job_audio_missing_duration_fails() -> None:
    data = _load_fixture("job_audio_missing_duration.json")
    with pytest.raises(ValidationError) as exc:
        AIJobQueueMessage.model_validate(data)
    assert "duration_ms" in str(exc.value)


def test_job_invalid_checksum_fails() -> None:
    data = _load_fixture("job_invalid_checksum.json")
    with pytest.raises(ValidationError) as exc:
        AIJobQueueMessage.model_validate(data)
    assert "checksum" in str(exc.value)


def test_worker_update_missing_required_fails() -> None:
    data = _load_fixture("worker_update_missing_required.json")
    with pytest.raises(ValidationError) as exc:
        AIWorkerUpdate.model_validate(data)
    assert "pipeline_version" in str(exc.value)


def test_worker_update_unknown_enum_fails() -> None:
    data = _load_fixture("worker_update_unknown_enum.json")
    with pytest.raises(ValidationError) as exc:
        AIWorkerUpdate.model_validate(data)
    assert "status" in str(exc.value)


def test_worker_update_progress_out_of_range_fails() -> None:
    data = _load_fixture("worker_update_progress_out_of_range.json")
    with pytest.raises(ValidationError) as exc:
        AIWorkerUpdate.model_validate(data)
    assert "progress" in str(exc.value)


def test_worker_update_invalid_checksum_fails() -> None:
    data = _load_fixture("worker_update_invalid_checksum.json")
    with pytest.raises(ValidationError) as exc:
        parse_worker_update(data, job_type=AIJobType.ANALYZE_SESSION)
    assert "checksum" in str(exc.value)


def test_worker_update_empty_evidence_fails() -> None:
    data = _load_fixture("worker_update_empty_evidence.json")
    with pytest.raises(ValidationError) as exc:
        parse_worker_update(data, job_type=AIJobType.ANALYZE_SESSION)
    assert "evidence_ids" in str(exc.value)


def test_worker_update_question_count_under_fails() -> None:
    data = _load_fixture("worker_update_question_count_under.json")
    with pytest.raises(ValidationError) as exc:
        parse_worker_update(data, job_type=AIJobType.ANALYZE_SESSION)
    assert "primary_questions" in str(exc.value)


def test_worker_update_question_count_over_fails() -> None:
    data = _load_fixture("worker_update_question_count_over.json")
    with pytest.raises(ValidationError) as exc:
        parse_worker_update(data, job_type=AIJobType.ANALYZE_SESSION)
    assert "primary_questions" in str(exc.value)


def test_worker_update_negative_deleted_records_fails() -> None:
    data = _load_fixture("worker_update_negative_deleted_records.json")
    with pytest.raises(ValidationError) as exc:
        parse_worker_update(data, job_type=AIJobType.ERASE_AI_DATA)
    assert "deleted_records" in str(exc.value)


def test_cancelled_payload_forbids_extra_properties() -> None:
    with pytest.raises(ValidationError):
        CancelledPayload.model_validate({"unexpected": "field"})


def test_completed_payloads_forbid_extra_properties() -> None:
    session_data = _load_fixture("worker_update_completed_analyze_session.json")
    payload_with_extra = dict(session_data["payload"])
    payload_with_extra["unexpected_extra"] = 123
    with pytest.raises(ValidationError):
        SessionAnalysisCompletedPayload.model_validate(payload_with_extra)


def test_contextual_fixtures_are_syntactically_valid_runtime_messages() -> None:
    ancestry = _load_fixture("job_ancestry_mismatch.json")
    msg_ancestry = AIJobQueueMessage.model_validate(ancestry)
    assert msg_ancestry.job_id is not None

    stale = _load_fixture("job_stale_attempt.json")
    msg_stale = AIJobQueueMessage.model_validate(stale)
    assert msg_stale.analysis_attempt == 1

    dup_seq = _load_fixture("worker_update_duplicate_sequence.json")
    update_dup = AIWorkerUpdate.model_validate(dup_seq)
    assert update_dup.sequence == 1

    trace_mismatch = _load_fixture("worker_update_trace_mismatch.json")
    update_trace = AIWorkerUpdate.model_validate(trace_mismatch)
    assert update_trace.trace_id == "trc_mismatch_001"
