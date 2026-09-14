from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.analysis_job_repository import (
    AnalysisJobRepository,
)
from app.domain.session_workflow.entities.analysis_job import (
    AIJobAncestryContext,
    AnalysisJob,
)
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.persistence.configurations import ProjectModel
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.persistence.mappers.session_practice.analysis_attempt_mapper import (
    to_domain as to_attempt_domain,
)
from app.infrastructure.persistence.mappers.session_practice.analysis_job_mapper import (
    to_domain,
    to_model,
)
from app.infrastructure.persistence.mappers.session_practice.session_manifest_mapper import (
    to_domain as to_manifest_domain,
)
from app.infrastructure.persistence.mappers.session_practice.session_practice_mapper import (
    to_domain as to_session_domain,
)


class SqlAlchemyAnalysisJobRepository(AnalysisJobRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        stmt = select(AnalysisJobModel).where(AnalysisJobModel.id == job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return None if model is None else to_domain(model)

    async def get_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AnalysisJob | None:
        stmt = select(AnalysisJobModel).where(AnalysisJobModel.attempt_id == attempt_id)
        stmt = stmt.where(AnalysisJobModel.job_type == "analyze_session")
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return None if model is None else to_domain(model)

    async def get_by_answer_id(self, answer_id: UUID) -> AnalysisJob | None:
        model = await self._session.scalar(
            select(AnalysisJobModel).where(AnalysisJobModel.answer_id == answer_id)
        )
        return None if model is None else to_domain(model)

    async def create(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob:
        model = to_model(job)
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Analysis job already exists or conflict.") from exc
        return to_domain(model)

    async def update(
        self,
        job: AnalysisJob,
    ) -> AnalysisJob:
        stmt = (
            update(AnalysisJobModel)
            .where(AnalysisJobModel.id == job.id)
            .values(
                status=job.status,
                last_update_sequence=job.last_update_sequence,
                payload_version=job.payload_version,
                attempts=job.attempts,
                cancel_requested=job.cancel_requested,
                retry_count=job.retry_count,
                last_error=job.last_error,
                updated_at=job.updated_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                payload=job.payload,
                queued_at=job.queued_at,
                next_dispatch_at=job.next_dispatch_at,
                dispatch_retry_count=job.dispatch_retry_count,
                last_dispatch_error_category=job.last_dispatch_error_category,
                completed_result=job.completed_result,
            )
        )
        await self._session.execute(stmt)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Integrity conflict on analysis job update.") from exc
        return job

    async def get_eligible_pending_jobs(
        self,
        now: datetime,
        limit: int = 10,
        *,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[AnalysisJob]:
        stmt = (
            select(AnalysisJobModel)
            .where(
                AnalysisJobModel.status == AnalysisJobStatus.PENDING,
                AnalysisJobModel.cancel_requested.is_(False),
                (
                    AnalysisJobModel.next_dispatch_at.is_(None)
                    | (AnalysisJobModel.next_dispatch_at <= now)
                ),
            )
            .order_by(
                AnalysisJobModel.next_dispatch_at.asc().nulls_first(),
                AnalysisJobModel.created_at.asc(),
                AnalysisJobModel.id.asc(),
            )
            .limit(limit)
        )
        bind = self._session.bind
        is_pg = bind is not None and bind.dialect.name == "postgresql"
        if for_update:
            stmt = stmt.with_for_update(skip_locked=(skip_locked and is_pg))

        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [to_domain(m) for m in models]

    load_eligible_pending_batch = get_eligible_pending_jobs

    async def change_pending_to_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> AnalysisJob | None:
        values: dict[str, Any] = {
            "status": AnalysisJobStatus.QUEUED,
            "queued_at": now,
            "updated_at": now,
        }
        if payload is not None:
            values["payload"] = payload

        stmt = (
            update(AnalysisJobModel)
            .where(
                AnalysisJobModel.id == job_id,
                AnalysisJobModel.status == AnalysisJobStatus.PENDING,
                AnalysisJobModel.cancel_requested.is_(False),
            )
            .values(**values)
        )
        result = cast(
            CursorResult[Any],
            await self._session.execute(stmt),
        )
        if result.rowcount == 0:
            return None
        await self._session.flush()
        return await self.get_by_id(job_id)

    async def mark_as_queued(
        self,
        job_id: UUID,
        now: datetime,
        *,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        job = await self.change_pending_to_queued(job_id, now, payload=payload)
        return job is not None

    async def record_dispatch_failure(
        self,
        job_id: UUID,
        now: datetime,
        next_eligible_at: datetime,
        error_category: str,
        error_message: str | None = None,
    ) -> AnalysisJob | None:
        stmt = (
            update(AnalysisJobModel)
            .where(AnalysisJobModel.id == job_id)
            .values(
                status=AnalysisJobStatus.PENDING,
                dispatch_retry_count=AnalysisJobModel.dispatch_retry_count + 1,
                next_dispatch_at=next_eligible_at,
                last_dispatch_error_category=error_category,
                last_error=error_message,
                updated_at=now,
            )
        )
        result = cast(
            CursorResult[Any],
            await self._session.execute(stmt),
        )
        if result.rowcount == 0:
            return None
        await self._session.flush()
        return await self.get_by_id(job_id)

    async def apply_callback_update(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> AnalysisJob | None:
        effective_now = now or occurred_at

        status_str = status.value if isinstance(status, AnalysisJobStatus) else str(status)
        if status_str in ("started", "progress"):
            target_status = AnalysisJobStatus.RUNNING
        elif status_str == "completed":
            target_status = AnalysisJobStatus.COMPLETED
        elif status_str == "failed":
            target_status = AnalysisJobStatus.FAILED
        elif status_str == "cancelled":
            target_status = AnalysisJobStatus.CANCELLED
        else:
            try:
                target_status = AnalysisJobStatus(status_str)
            except ValueError:
                target_status = AnalysisJobStatus.RUNNING

        values: dict[str, Any] = {
            "last_update_sequence": sequence,
            "status": target_status,
            "updated_at": effective_now,
        }

        if target_status == AnalysisJobStatus.RUNNING:
            values["started_at"] = func.coalesce(AnalysisJobModel.started_at, occurred_at)
        elif target_status in (
            AnalysisJobStatus.COMPLETED,
            AnalysisJobStatus.FAILED,
            AnalysisJobStatus.CANCELLED,
        ):
            values["completed_at"] = occurred_at

        if target_status == AnalysisJobStatus.COMPLETED and payload is not None:
            values["completed_result"] = payload
        elif target_status == AnalysisJobStatus.FAILED:
            if error_message is not None:
                values["last_error"] = error_message
            elif payload is not None and "message" in payload:
                values["last_error"] = str(payload["message"])
            if attempts is not None:
                values["attempts"] = attempts
            elif (
                payload is not None
                and "attempts" in payload
                and isinstance(payload["attempts"], int)
            ):
                values["attempts"] = payload["attempts"]
        elif target_status == AnalysisJobStatus.CANCELLED:
            values["cancel_requested"] = True

        stmt = (
            update(AnalysisJobModel)
            .where(
                AnalysisJobModel.id == job_id,
                sequence > AnalysisJobModel.last_update_sequence,
            )
            .values(**values)
        )
        result = cast(
            CursorResult[Any],
            await self._session.execute(stmt),
        )
        if result.rowcount == 0:
            return None
        await self._session.flush()
        return await self.get_by_id(job_id)

    async def apply_callback(
        self,
        job_id: UUID,
        sequence: int,
        status: AnalysisJobStatus | str,
        occurred_at: datetime,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
    ) -> bool:
        job = await self.apply_callback_update(
            job_id,
            sequence,
            status,
            occurred_at,
            now=now,
            payload=payload,
            error_message=error_message,
            attempts=attempts,
        )
        return job is not None

    async def _load_ancestry_context(
        self,
        filter_expr: Any,
    ) -> AIJobAncestryContext | None:
        stmt = (
            select(
                AnalysisJobModel,
                AnalysisAttemptModel,
                PracticeSessionModel,
                ProjectModel.team_id,
                ProjectModel.id,
                SessionManifestModel,
            )
            .join(
                AnalysisAttemptModel,
                AnalysisAttemptModel.id == AnalysisJobModel.attempt_id,
            )
            .join(
                PracticeSessionModel,
                PracticeSessionModel.id == AnalysisJobModel.practice_session_id,
            )
            .join(
                ProjectModel,
                ProjectModel.id == PracticeSessionModel.project_id,
            )
            .outerjoin(
                SessionManifestModel,
                SessionManifestModel.id == AnalysisAttemptModel.manifest_id,
            )
            .where(filter_expr)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None

        (
            job_model,
            attempt_model,
            session_model,
            team_id,
            project_id,
            manifest_model,
        ) = row

        return AIJobAncestryContext(
            job=to_domain(job_model),
            attempt=to_attempt_domain(attempt_model),
            session=to_session_domain(session_model),
            project_id=project_id,
            team_id=team_id,
            manifest_id=attempt_model.manifest_id,
            manifest=(to_manifest_domain(manifest_model) if manifest_model is not None else None),
        )

    async def get_ancestry_context(
        self,
        job_id: UUID,
    ) -> AIJobAncestryContext | None:
        return await self._load_ancestry_context(AnalysisJobModel.id == job_id)

    load_ancestry_context = get_ancestry_context

    async def get_ancestry_context_by_attempt_id(
        self,
        attempt_id: UUID,
    ) -> AIJobAncestryContext | None:
        return await self._load_ancestry_context(AnalysisJobModel.attempt_id == attempt_id)
