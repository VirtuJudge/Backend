import re

from pydantic import ValidationError

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AnswerAnalysisCompletedPayload,
    ArtifactRef,
    CompletedPayload,
    ErasureCompletedPayload,
    ReportCompletedPayload,
    SessionAnalysisCompletedPayload,
    parse_completed_payload,
)
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.exceptions import CompletedResultValidationError

UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


def validate_object_key_scope(
    key: str,
    ancestry: AIJobAncestryContext,
    field_name: str,
) -> None:
    if not key or not isinstance(key, str):
        raise CompletedResultValidationError(
            f"Artifact '{field_name}' object_key must be a non-empty string."
        )
    if ".." in key or key.startswith(("/", "\\")):
        raise CompletedResultValidationError(
            f"Artifact '{field_name}' object_key '{key}' contains invalid traversal."
        )

    parts = key.split("/")
    for idx, part in enumerate(parts):
        if part in ("teams", "team") and idx + 1 < len(parts):
            val = parts[idx + 1]
            if val != str(ancestry.team_id):
                raise CompletedResultValidationError(
                    f"Artifact '{field_name}' object_key '{key}' scope mismatch "
                    f"with team '{ancestry.team_id}'."
                )
        elif part in ("projects", "project") and idx + 1 < len(parts):
            val = parts[idx + 1]
            if val != str(ancestry.project_id):
                raise CompletedResultValidationError(
                    f"Artifact '{field_name}' object_key '{key}' scope mismatch "
                    f"with project '{ancestry.project_id}'."
                )
        elif part in ("sessions", "session") and idx + 1 < len(parts):
            val = parts[idx + 1]
            if UUID_PATTERN.match(val) and val != str(ancestry.session.id):
                raise CompletedResultValidationError(
                    f"Artifact '{field_name}' object_key '{key}' scope mismatch "
                    f"with session '{ancestry.session.id}'."
                )
        elif part in ("attempts", "attempt") and idx + 1 < len(parts):
            val = parts[idx + 1]
            if val.isdigit() and int(val) != ancestry.attempt.attempt_number:
                raise CompletedResultValidationError(
                    f"Artifact '{field_name}' object_key '{key}' scope mismatch "
                    f"with attempt '{ancestry.attempt.attempt_number}'."
                )

    found_uuids = {u.lower() for u in UUID_PATTERN.findall(key)}
    if found_uuids:
        allowed_uuids = {
            str(ancestry.team_id).lower(),
            str(ancestry.project_id).lower(),
            str(ancestry.session.id).lower(),
            str(ancestry.attempt.id).lower(),
            str(ancestry.job.id).lower(),
        }
        if ancestry.manifest_id:
            allowed_uuids.add(str(ancestry.manifest_id).lower())
        for u in found_uuids:
            if u not in allowed_uuids:
                raise CompletedResultValidationError(
                    f"Artifact '{field_name}' object_key '{key}' references "
                    f"unauthorized resource '{u}'."
                )


def validate_artifact_ref(
    artifact: ArtifactRef,
    ancestry: AIJobAncestryContext,
    field_name: str,
) -> None:
    if not artifact.checksum or not SHA256_PATTERN.match(artifact.checksum):
        raise CompletedResultValidationError(
            f"Artifact '{field_name}' checksum must match 'sha256:' followed by 64 hex characters."
        )
    if artifact.schema_version != 1:
        raise CompletedResultValidationError(
            f"Artifact '{field_name}' schema_version must be 1, got '{artifact.schema_version}'."
        )
    validate_object_key_scope(artifact.object_key, ancestry, field_name)


def validate_completed_update(
    job: AnalysisJob,
    update: AIWorkerUpdate,
    ancestry: AIJobAncestryContext | None,
    current_attempt_number: int | None,
) -> CompletedPayload:
    if update.schema_version != 1:
        raise CompletedResultValidationError(
            f"Unsupported schema version '{update.schema_version}'. Expected 1."
        )

    expected_trace_id: str | None = None
    if isinstance(job.payload, dict) and job.payload.get("trace_id"):
        expected_trace_id = str(job.payload["trace_id"])
    elif job.correlation_id:
        expected_trace_id = f"trc_{job.correlation_id.hex}"

    allowed_trace_ids = {
        t
        for t in [
            expected_trace_id,
            f"trc_{job.correlation_id.hex}" if job.correlation_id else None,
            str(job.correlation_id) if job.correlation_id else None,
        ]
        if t is not None
    }
    if allowed_trace_ids and update.trace_id not in allowed_trace_ids:
        raise CompletedResultValidationError(
            f"Callback trace_id '{update.trace_id}' does not match "
            f"dispatched job trace_id '{expected_trace_id}'."
        )

    if ancestry is None:
        raise CompletedResultValidationError(
            f"AI Job ancestry context could not be resolved for job '{job.id}'."
        )
    if ancestry.session.id != job.practice_session_id:
        raise CompletedResultValidationError(
            f"Job practice_session_id '{job.practice_session_id}' does not match "
            f"session ancestry '{ancestry.session.id}'."
        )
    if ancestry.attempt.id != job.attempt_id:
        raise CompletedResultValidationError(
            f"Job attempt_id '{job.attempt_id}' does not match "
            f"attempt ancestry '{ancestry.attempt.id}'."
        )
    if ancestry.attempt.session_id != job.practice_session_id:
        raise CompletedResultValidationError(
            f"Attempt session_id '{ancestry.attempt.session_id}' does not match "
            f"job practice_session_id '{job.practice_session_id}'."
        )
    if ancestry.attempt.attempt_number != job.analysis_attempt:
        raise CompletedResultValidationError(
            f"Attempt number '{ancestry.attempt.attempt_number}' does not match "
            f"job attempt number '{job.analysis_attempt}'."
        )
    if ancestry.project_id != ancestry.session.project_id:
        raise CompletedResultValidationError(
            f"Ancestry project_id '{ancestry.project_id}' does not match "
            f"session project_id '{ancestry.session.project_id}'."
        )

    if current_attempt_number is not None and job.analysis_attempt < current_attempt_number:
        raise CompletedResultValidationError(
            f"Attempt {job.analysis_attempt} is stale; current attempt is {current_attempt_number}."
        )
    if ancestry.attempt.status == AnalysisAttemptStatus.CANCELLED or job.cancel_requested:
        raise CompletedResultValidationError(
            f"Cannot complete cancelled job '{job.id}' or attempt '{job.attempt_id}'."
        )

    try:
        payload = parse_completed_payload(job.job_type, update.payload)
    except (ValidationError, ValueError, KeyError) as exc:
        raise CompletedResultValidationError(
            f"Completed payload validation failed for job_type '{job.job_type}': {exc}"
        ) from exc

    if isinstance(payload, SessionAnalysisCompletedPayload):
        validate_artifact_ref(payload.analysis_artifact, ancestry, "analysis_artifact")
        if len(payload.primary_questions) != 3:
            raise CompletedResultValidationError(
                f"primary_questions must contain exactly 3 items, "
                f"got {len(payload.primary_questions)}."
            )
        candidate_ids = [q.candidate_id for q in payload.primary_questions]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise CompletedResultValidationError(
                f"primary_questions must have unique candidate_ids, "
                f"got duplicates: {candidate_ids}."
            )
        for idx, q in enumerate(payload.primary_questions):
            if not q.evidence_ids:
                raise CompletedResultValidationError(
                    f"primary_questions[{idx}].evidence_ids must not be empty."
                )
            for eid in q.evidence_ids:
                if not eid or not eid.strip():
                    raise CompletedResultValidationError(
                        f"primary_questions[{idx}].evidence_ids contains empty evidence identifier."
                    )

    elif isinstance(payload, AnswerAnalysisCompletedPayload):
        if not payload.answer_id or not payload.answer_id.strip():
            raise CompletedResultValidationError("answer_id must not be empty.")
        if job.answer_id is not None and payload.answer_id != str(job.answer_id):
            raise CompletedResultValidationError(
                f"answer_id '{payload.answer_id}' does not match job answer '{job.answer_id}'."
            )
        if payload.follow_up is not None:
            if not payload.follow_up.evidence_ids:
                raise CompletedResultValidationError(
                    "follow_up.evidence_ids must contain grounding Evidence."
                )
            if not payload.follow_up.text or not payload.follow_up.text.strip():
                raise CompletedResultValidationError("follow_up text must not be empty.")
            if not payload.follow_up.reason or not payload.follow_up.reason.strip():
                raise CompletedResultValidationError("follow_up reason must not be empty.")
            if (
                not payload.follow_up.rubric_dimension
                or not payload.follow_up.rubric_dimension.strip()
            ):
                raise CompletedResultValidationError(
                    "follow_up rubric_dimension must not be empty."
                )
            if payload.follow_up.evidence_ids is not None:
                for eid in payload.follow_up.evidence_ids:
                    if not eid or not eid.strip():
                        raise CompletedResultValidationError(
                            "follow_up.evidence_ids contains empty evidence identifier."
                        )

    elif isinstance(payload, ReportCompletedPayload):
        validate_artifact_ref(payload.evaluation_artifact, ancestry, "evaluation_artifact")
        validate_artifact_ref(payload.report_artifact, ancestry, "report_artifact")
        for idx, uid in enumerate(payload.member_feedback_user_ids):
            if not uid or not uid.strip():
                raise CompletedResultValidationError(
                    f"member_feedback_user_ids[{idx}] must not be empty."
                )

    elif isinstance(payload, ErasureCompletedPayload):
        if payload.deleted_records < 0:
            raise CompletedResultValidationError(
                f"deleted_records must be non-negative, got {payload.deleted_records}."
            )
        if payload.deleted_objects < 0:
            raise CompletedResultValidationError(
                f"deleted_objects must be non-negative, got {payload.deleted_objects}."
            )

    return payload
