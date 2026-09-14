import hashlib
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    AnalyzeSessionPayload,
    AssetInput,
    RubricRef,
)
from app.application.ports.ai_queue import (
    AIJobQueuePort,
    AIQueueTemporaryFailure,
)
from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.exceptions import (
    InvalidAttemptState,
    InvalidJobStatusTransition,
    StaleEntityVersion,
)

logger = logging.getLogger(__name__)

_ALLOWED_JOB_TRANSITIONS: dict[AnalysisJobStatus, set[AIWorkerUpdateStatus]] = {
    AnalysisJobStatus.PENDING: {
        AIWorkerUpdateStatus.STARTED,
        AIWorkerUpdateStatus.FAILED,
        AIWorkerUpdateStatus.CANCELLED,
    },
    AnalysisJobStatus.QUEUED: {
        AIWorkerUpdateStatus.STARTED,
        AIWorkerUpdateStatus.FAILED,
        AIWorkerUpdateStatus.CANCELLED,
    },
    AnalysisJobStatus.RUNNING: {
        AIWorkerUpdateStatus.PROGRESS,
        AIWorkerUpdateStatus.FAILED,
        AIWorkerUpdateStatus.CANCELLED,
        AIWorkerUpdateStatus.COMPLETED,
    },
    AnalysisJobStatus.CANCELLED: {
        AIWorkerUpdateStatus.CANCELLED,
    },
    AnalysisJobStatus.FAILED: set(),
    AnalysisJobStatus.COMPLETED: set(),
}


@dataclass(frozen=True, slots=True)
class RedispatchResult:
    processed: int = 0
    queued: int = 0
    failed: int = 0

    def __int__(self) -> int:
        return self.queued

    def __bool__(self) -> bool:
        return self.processed > 0

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.queued == other
        if isinstance(other, RedispatchResult):
            return (
                self.processed == other.processed
                and self.queued == other.queued
                and self.failed == other.failed
            )
        return False


def _calculate_backoff_delay(
    attempt_count: int,
    base_seconds: float = 30.0,
    factor: float = 2.0,
    max_seconds: float = 3600.0,
    explicit_retry_after: float | None = None,
) -> float:
    if explicit_retry_after is not None and explicit_retry_after > 0:
        return min(float(explicit_retry_after), max_seconds)
    delay = base_seconds * (factor ** max(0, attempt_count - 1))
    return min(delay, max_seconds)


DEFAULT_REQUESTED_CAPABILITIES: list[str] = [
    "speech",
    "diarization",
    "vision",
    "audio",
    "documents",
    "questions",
]


def _normalize_sha256(raw: Any) -> str:
    s = str(raw or "").strip()
    if s.startswith("sha256:") and len(s) == 71:
        hex_part = s[7:]
        if all(c in "0123456789abcdefABCDEF" for c in hex_part):
            return s.lower()
    if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
        return f"sha256:{s.lower()}"
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _build_asset_input(
    snap: dict[str, Any] | None,
    fallback_id: UUID | str,
    default_media_type: str = "video/mp4",
) -> AssetInput:
    data = snap or {}
    art_id = str(
        data.get("artifact_id")
        or data.get("asset_version_id")
        or data.get("asset_id")
        or fallback_id
    )
    obj_key = str(data.get("object_key") or data.get("storage_key") or f"assets/{art_id}")
    checksum = _normalize_sha256(data.get("checksum"))
    media_type = str(
        data.get("media_type") or data.get("declared_media_type") or default_media_type
    )
    duration_ms = data.get("duration_ms")
    if duration_ms is not None:
        try:
            val = int(duration_ms)
            duration_ms = val if val >= 0 else None
        except (ValueError, TypeError):
            duration_ms = None

    return AssetInput(
        artifact_id=art_id,
        object_key=obj_key,
        checksum=checksum,
        media_type=media_type,
        duration_ms=duration_ms,
    )


class AIJobs:
    def __init__(
        self,
        uow: UnitOfWork,
        queue: AIJobQueuePort | None = None,
    ) -> None:
        self._uow = uow
        self._queue = queue

    @property
    def queue(self) -> AIJobQueuePort | None:
        return self._queue

    @queue.setter
    def queue(self, val: AIJobQueuePort | None) -> None:
        self._queue = val

    async def create_pending_job(
        self,
        practice_session_id: UUID,
        attempt: AnalysisAttempt,
        now: datetime,
        *,
        manifest: SessionManifest | None = None,
        snapshots: dict[UUID, dict[str, Any]] | None = None,
        correlation_id: UUID | None = None,
    ) -> AnalysisJob:
        if manifest is None:
            manifest = await self._uow.manifests.get_by_session_id(practice_session_id)

        pres_data: dict[str, Any] | None = None
        doc_data_list: list[dict[str, Any]] = []
        rubric_id = "startup_pitch"
        rubric_version = 1

        if manifest is not None:
            rubric_id = manifest.rubric_id or rubric_id
            rubric_version = manifest.rubric_version or rubric_version
            if isinstance(manifest.snapshot, dict):
                pres_data = manifest.snapshot.get("presentation")
                raw_docs = manifest.snapshot.get("supporting_documents")
                if isinstance(raw_docs, list):
                    doc_data_list = [d for d in raw_docs if isinstance(d, dict)]
                rubric_snap = manifest.snapshot.get("rubric")
                if isinstance(rubric_snap, dict):
                    rubric_id = rubric_snap.get("rubric_id") or rubric_id
                    rubric_version = rubric_snap.get("version") or rubric_version

            if pres_data is None and snapshots and manifest.presentation_version_id in snapshots:
                pres_data = snapshots[manifest.presentation_version_id]

            presentation_input = _build_asset_input(
                pres_data, manifest.presentation_version_id, "video/mp4"
            )

            supporting_docs: list[AssetInput] = []
            if doc_data_list:
                for idx, doc_snap in enumerate(doc_data_list):
                    fallback = (
                        manifest.supporting_document_version_ids[idx]
                        if idx < len(manifest.supporting_document_version_ids)
                        else uuid4()
                    )
                    supporting_docs.append(
                        _build_asset_input(doc_snap, fallback, "application/pdf")
                    )
            elif manifest.supporting_document_version_ids:
                for doc_id in manifest.supporting_document_version_ids:
                    snap = snapshots.get(doc_id) if snapshots else None
                    supporting_docs.append(_build_asset_input(snap, doc_id, "application/pdf"))
        else:
            presentation_input = _build_asset_input(None, uuid4(), "video/mp4")
            supporting_docs = []

        rubric_ref = RubricRef(
            rubric_id=str(rubric_id),
            version=int(rubric_version),
        )

        job_id = uuid4()
        cid = correlation_id or uuid4()
        trace_id = f"trc_{cid.hex}"

        envelope = AIJobQueueMessage(
            schema_version=1,
            job_id=str(job_id),
            job_type=AIJobType.ANALYZE_SESSION,
            practice_session_id=str(practice_session_id),
            analysis_attempt=attempt.attempt_number,
            created_at=now,
            trace_id=trace_id,
            payload=AnalyzeSessionPayload(
                presentation=presentation_input,
                supporting_documents=supporting_docs,
                rubric=rubric_ref,
                requested_capabilities=DEFAULT_REQUESTED_CAPABILITIES,
            ),
        )
        envelope_dict = envelope.model_dump(mode="json", exclude_none=True)

        job = AnalysisJob(
            id=job_id,
            practice_session_id=practice_session_id,
            attempt_id=attempt.id,
            analysis_attempt=attempt.attempt_number,
            job_type="analyze_session",
            status=AnalysisJobStatus.PENDING,
            correlation_id=cid,
            last_update_sequence=0,
            payload_version=1,
            attempts=0,
            cancel_requested=False,
            retry_count=0,
            last_error=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
            payload=envelope_dict,
            queued_at=None,
            next_dispatch_at=None,
            dispatch_retry_count=0,
            last_dispatch_error_category=None,
            completed_result=None,
        )
        await self._uow.jobs.create(job)
        return job

    async def get_job(self, job_id: UUID) -> AnalysisJob | None:
        return await self._uow.jobs.get_by_id(job_id)

    async def get_job_status(self, job_id: UUID) -> AnalysisJob | None:
        return await self._uow.jobs.get_by_id(job_id)

    async def record_update(
        self,
        job_id: UUID,
        update: AIWorkerUpdate,
        *,
        now: datetime | None = None,
    ) -> AnalysisJob | None:
        job = await self._uow.jobs.get_by_id(job_id)
        if job is None:
            return None

        if update.sequence <= job.last_update_sequence:
            return job

        allowed = _ALLOWED_JOB_TRANSITIONS.get(job.status, set())
        if update.status not in allowed:
            raise InvalidJobStatusTransition(
                f"Cannot transition job {job.id} status '{job.status}' on update '{update.status}'."
            )

        effective_now = now or datetime.now(UTC)
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=UTC)

        occurred_at = update.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)

        attempt: AnalysisAttempt | None = None
        initial_attempt_version = 0
        attempts_repo = getattr(self._uow, "attempts", None)
        if attempts_repo is not None and hasattr(attempts_repo, "get_by_id"):
            get_att = attempts_repo.get_by_id
            if callable(get_att):
                res = get_att(job.attempt_id)
                if inspect.isawaitable(res):
                    attempt = await res
                elif isinstance(res, AnalysisAttempt):
                    attempt = res
                if attempt is not None:
                    initial_attempt_version = attempt.version

        payload = update.payload
        if job.payload is None:
            job.payload = {}

        try:
            if update.status == AIWorkerUpdateStatus.STARTED:
                job.status = AnalysisJobStatus.RUNNING
                if job.started_at is None:
                    job.started_at = occurred_at
                pipeline_ver = (
                    getattr(payload, "pipeline_version", None)
                    if hasattr(payload, "pipeline_version")
                    else (payload.get("pipeline_version") if isinstance(payload, dict) else None)
                )
                if pipeline_ver is not None:
                    job.payload["pipeline_version"] = str(pipeline_ver)
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.RUNNING, at=occurred_at)

            elif update.status == AIWorkerUpdateStatus.PROGRESS:
                stage = getattr(payload, "stage", None) or (
                    payload.get("stage") if isinstance(payload, dict) else None
                )
                progress_val = (
                    getattr(payload, "progress", None)
                    if hasattr(payload, "progress")
                    else (payload.get("progress") if isinstance(payload, dict) else None)
                )
                message = getattr(payload, "message", None) or (
                    payload.get("message") if isinstance(payload, dict) else None
                )
                job.payload["progress"] = {
                    "stage": str(stage) if stage is not None else None,
                    "progress": float(progress_val) if progress_val is not None else None,
                    "message": str(message) if message is not None else None,
                }
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.RUNNING, at=occurred_at)

            elif update.status == AIWorkerUpdateStatus.FAILED:
                job.status = AnalysisJobStatus.FAILED
                job.completed_at = occurred_at
                stage = getattr(payload, "stage", None) or (
                    payload.get("stage") if isinstance(payload, dict) else None
                )
                code = getattr(payload, "code", None) or (
                    payload.get("code") if isinstance(payload, dict) else None
                )
                retryable = (
                    getattr(payload, "retryable", None)
                    if hasattr(payload, "retryable")
                    else (payload.get("retryable") if isinstance(payload, dict) else None)
                )
                raw_msg = getattr(payload, "message", None) or (
                    payload.get("message") if isinstance(payload, dict) else None
                )
                safe_msg = (
                    str(raw_msg)
                    if raw_msg is not None
                    else (str(code) if code is not None else "Worker reported failure")
                )
                job.last_error = safe_msg

                worker_attempts = (
                    getattr(payload, "attempts", None)
                    if hasattr(payload, "attempts")
                    else (payload.get("attempts") if isinstance(payload, dict) else None)
                )
                if worker_attempts is not None:
                    job.attempts = max(job.attempts, int(worker_attempts))

                job.payload["failure"] = {
                    "stage": str(stage) if stage is not None else None,
                    "code": str(code) if code is not None else None,
                    "retryable": bool(retryable) if retryable is not None else None,
                    "attempts": job.attempts,
                    "message": safe_msg,
                }
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.FAILED, at=occurred_at)
                    attempt.failure_code = str(code) if code is not None else None
                    attempt.failure_message = safe_msg
                    attempt.failed_at = occurred_at
                    attempt.completed_at = occurred_at

            elif update.status == AIWorkerUpdateStatus.CANCELLED:
                job.status = AnalysisJobStatus.CANCELLED
                job.cancel_requested = True
                job.completed_at = occurred_at
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.CANCELLED, at=occurred_at)
                    attempt.cancelled_at = occurred_at
                    attempt.completed_at = occurred_at

            elif update.status == AIWorkerUpdateStatus.COMPLETED:
                job.status = AnalysisJobStatus.COMPLETED
                job.completed_at = occurred_at
                if attempt is not None:
                    attempt.transition_to(AnalysisAttemptStatus.COMPLETED, at=occurred_at)
                    attempt.completed_at = occurred_at
        except InvalidAttemptState as exc:
            raise InvalidJobStatusTransition(str(exc)) from exc

        job.last_update_sequence = update.sequence
        job.updated_at = effective_now

        try:
            update_job = self._uow.jobs.update(job)
            if inspect.isawaitable(update_job):
                await update_job
            if attempt is not None and attempts_repo is not None:
                update_fn = getattr(attempts_repo, "update", None)
                if callable(update_fn):
                    update_att = update_fn(attempt, expected_version=initial_attempt_version)
                    if inspect.isawaitable(update_att):
                        await update_att

            commit_res = self._uow.commit()
            if inspect.isawaitable(commit_res):
                await commit_res
        except StaleEntityVersion:
            rollback_res = self._uow.rollback()
            if inspect.isawaitable(rollback_res):
                await rollback_res
            reloaded_job = await self._uow.jobs.get_by_id(job_id)
            if reloaded_job is not None and reloaded_job.last_update_sequence >= update.sequence:
                return reloaded_job
            raise

        return job

    async def request_cancellation(
        self,
        attempt_id: UUID,
        now: datetime,
    ) -> AnalysisJob | None:
        job = await self._uow.jobs.get_by_attempt_id(attempt_id)
        if job is None:
            return None
        job.cancel_requested = True
        job.updated_at = now
        if job.status not in {
            AnalysisJobStatus.COMPLETED,
            AnalysisJobStatus.FAILED,
            AnalysisJobStatus.CANCELLED,
        }:
            job.status = AnalysisJobStatus.CANCELLED
            job.completed_at = now
        await self._uow.jobs.update(job)
        return job

    async def dispatch(
        self,
        job_or_id: AnalysisJob | UUID,
        *,
        now: datetime | None = None,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 3600.0,
        backoff_factor: float = 2.0,
    ) -> bool:
        if isinstance(job_or_id, UUID):
            job = await self._uow.jobs.get_by_id(job_or_id)
            if job is None:
                return False
        else:
            job = job_or_id

        if job.status != AnalysisJobStatus.PENDING or job.cancel_requested:
            return False

        if not job.payload:
            return False

        if self._queue is None:
            return False

        now_time = now or datetime.now(UTC)
        if now_time.tzinfo is None:
            now_time = now_time.replace(tzinfo=UTC)

        if job.next_dispatch_at is not None:
            job_next = (
                job.next_dispatch_at.replace(tzinfo=UTC)
                if job.next_dispatch_at.tzinfo is None
                else job.next_dispatch_at
            )
            if now_time < job_next:
                return False

        try:
            envelope = (
                AIJobQueueMessage.model_validate(job.payload)
                if isinstance(job.payload, dict)
                else job.payload
            )
            await self._queue.enqueue(envelope)
        except Exception as exc:
            error_category = (
                "temporary_queue_failure"
                if isinstance(exc, AIQueueTemporaryFailure)
                else exc.__class__.__name__
            )
            retry_after = getattr(exc, "retry_after_seconds", None)
            retry_delay = _calculate_backoff_delay(
                attempt_count=job.dispatch_retry_count + 1,
                base_seconds=base_backoff_seconds,
                factor=backoff_factor,
                max_seconds=max_backoff_seconds,
                explicit_retry_after=retry_after,
            )
            next_eligible_at = now_time + timedelta(seconds=retry_delay)
            error_msg = str(exc)[:255] if str(exc) else exc.__class__.__name__
            try:
                updated_job = await self._uow.jobs.record_dispatch_failure(
                    job.id,
                    now_time,
                    next_eligible_at,
                    error_category,
                    error_msg,
                )
                await self._uow.commit()
                if updated_job is not None:
                    job.dispatch_retry_count = updated_job.dispatch_retry_count
                    job.next_dispatch_at = updated_job.next_dispatch_at
                    job.last_dispatch_error_category = updated_job.last_dispatch_error_category
                    job.last_error = updated_job.last_error
                    job.updated_at = updated_job.updated_at
                else:
                    job.dispatch_retry_count += 1
                    job.next_dispatch_at = next_eligible_at
                    job.last_dispatch_error_category = error_category
                    job.last_error = error_msg
                    job.updated_at = now_time
            except Exception as rec_exc:
                logger.warning(
                    "Failed to record dispatch failure for job %s: %s",
                    job.id,
                    rec_exc,
                )
            return False

        try:
            updated_job = await self._uow.jobs.change_pending_to_queued(job.id, now_time)
            await self._uow.commit()
            if updated_job is not None:
                job.status = updated_job.status
                job.queued_at = updated_job.queued_at
                job.updated_at = updated_job.updated_at
                return True
            return False
        except Exception as commit_exc:
            logger.warning(
                "Failed to commit queued status for job %s: %s",
                job.id,
                commit_exc,
            )
            return False

    dispatch_pending_job = dispatch

    async def redispatch_pending(
        self,
        limit: int = 10,
        *,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        batch_size: int | None = None,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 3600.0,
        backoff_factor: float = 2.0,
    ) -> RedispatchResult:
        effective_limit = batch_size if batch_size is not None else limit
        bounded_limit = max(1, min(effective_limit, 1000))

        now_time = clock() if clock is not None else (now or datetime.now(UTC))
        if now_time.tzinfo is None:
            now_time = now_time.replace(tzinfo=UTC)

        if self._queue is None:
            return RedispatchResult(0, 0, 0)

        get_batch = getattr(self._uow.jobs, "get_eligible_pending_jobs", None) or getattr(
            self._uow.jobs, "load_eligible_pending_batch", None
        )
        if get_batch is None:
            return RedispatchResult(0, 0, 0)

        jobs = await get_batch(now_time, limit=bounded_limit)

        processed = 0
        queued = 0
        failed = 0

        for job in jobs:
            processed += 1
            try:
                success = await self.dispatch(
                    job,
                    now=now_time,
                    base_backoff_seconds=base_backoff_seconds,
                    max_backoff_seconds=max_backoff_seconds,
                    backoff_factor=backoff_factor,
                )
                if success:
                    queued += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.warning("Unexpected error redispatching job %s: %s", job.id, exc)
                failed += 1

        return RedispatchResult(processed=processed, queued=queued, failed=failed)
