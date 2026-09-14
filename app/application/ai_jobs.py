import hashlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIJobType,
    AnalyzeSessionPayload,
    AssetInput,
    RubricRef,
)
from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.entities.analysis_job import AnalysisJob
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus

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
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

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
