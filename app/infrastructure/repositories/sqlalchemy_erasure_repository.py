from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ai_job_contracts import (
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    EraseAIDataPayload,
    EraseAIDataQueueMessage,
    ErasureCompletedPayload,
)
from app.domain.erasure import (
    Erasure,
    ErasureConflict,
    ErasureForbidden,
    ErasureJobState,
    ErasureNotFound,
    ErasureStep,
)
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.infrastructure.persistence.cascade_deletion import (
    delete_asset_records,
    delete_practice_sessions,
    delete_project_records,
)
from app.infrastructure.persistence.configurations import (
    AIJobModel,
    AssetModel,
    AssetUploadIdempotencyModel,
    AssetVersionModel,
    ErasureCommandIdempotencyModel,
    ErasureItemModel,
    ErasureRequestModel,
    ErasureStepModel,
    PracticeSessionModel,
    ProjectErasureRequestModel,
    ProjectModel,
    SessionManifestModel,
    TeamMemberModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisStageModel,
    AnswerModel,
    EvaluationModel,
    QARoundModel,
    QuestionModel,
    ReportExportModel,
    ReportModel,
    SessionCommandIdempotencyModel,
    SessionManifestDocumentModel,
    SpeakerMappingModel,
)

STORES = ("pending_work", "ai_data", "objects", "generated_pdfs", "redis", "backend_records")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SqlAlchemyErasureRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _domain(self, model: ErasureRequestModel) -> Erasure:
        rows = (
            await self.session.scalars(
                select(ErasureStepModel).where(ErasureStepModel.request_id == model.id)
            )
        ).all()
        by_store = {row.store: row for row in rows}
        return Erasure(
            id=model.id,
            team_id=model.team_id,
            scope=model.scope,
            scope_id=model.scope_id,
            requested_by=model.requested_by,
            requested_at=utc(model.requested_at),
            deadline_at=utc(model.deadline_at),
            status=model.status,
            origin=model.origin,
            completed_at=utc(model.completed_at) if model.completed_at else None,
            inventory=model.inventory,
            lease_token=model.lease_token,
            steps=[
                ErasureStep(
                    store=row.store,
                    status=row.status,
                    attempts=row.attempts,
                    deleted_records=row.deleted_records,
                    deleted_objects=row.deleted_objects,
                    failure_code=row.failure_code,
                    completed_at=utc(row.completed_at) if row.completed_at else None,
                )
                for store in STORES
                if (row := by_store.get(store)) is not None
            ],
        )

    async def _membership(self, team_id: UUID, actor_id: UUID) -> str:
        role = await self.session.scalar(
            select(TeamMemberModel.role).where(
                TeamMemberModel.team_id == team_id,
                TeamMemberModel.user_id == actor_id,
            )
        )
        if role is None:
            raise ErasureNotFound
        return role

    async def get(self, request_id: UUID, actor_id: UUID) -> Erasure:
        model = await self.session.get(ErasureRequestModel, request_id)
        if model is None:
            raise ErasureNotFound
        await self._membership(model.team_id, actor_id)
        return await self._domain(model)

    async def _record_command(
        self,
        request: ErasureRequestModel,
        scope: str,
        scope_id: UUID,
        actor_id: UUID | None,
        key: str,
        digest: str,
    ) -> None:
        if actor_id is None:
            return
        previous = await self.session.get(
            ErasureCommandIdempotencyModel, (actor_id, scope, scope_id, key)
        )
        if previous is not None:
            if previous.request_hash != digest:
                raise ErasureConflict("idempotency_conflict")
            return
        if (
            request.requested_by == actor_id
            and request.scope == scope
            and request.scope_id == scope_id
            and request.idempotency_key == key
            and request.request_hash != digest
        ):
            raise ErasureConflict("idempotency_conflict")
        self.session.add(
            ErasureCommandIdempotencyModel(
                actor_id=actor_id,
                scope=scope,
                scope_id=scope_id,
                idempotency_key=key,
                request_hash=digest,
                request_id=request.id,
            )
        )

    async def accept(
        self,
        scope: str,
        scope_id: UUID,
        actor_id: UUID | None,
        key: str,
        confirmation: str | None,
        now: datetime,
        *,
        retention: bool = False,
        expected_team_id: UUID | None = None,
        expected_project_id: UUID | None = None,
    ) -> Erasure:
        digest = sha256((confirmation or "").encode()).hexdigest()
        previous = await self.session.scalar(
            select(ErasureRequestModel)
            .where(
                ErasureRequestModel.scope == scope,
                ErasureRequestModel.scope_id == scope_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            if (expected_team_id is not None and previous.team_id != expected_team_id) or (
                expected_project_id is not None and previous.project_id != expected_project_id
            ):
                raise ErasureNotFound
            if (
                actor_id is not None
                and await self._membership(previous.team_id, actor_id) != "owner"
            ):
                raise ErasureForbidden
            await self._record_command(previous, scope, scope_id, actor_id, key, digest)
            await self.session.commit()
            return await self._domain(previous)

        project_id: UUID | None
        if scope == "project":
            project_id = scope_id
        elif scope == "practice_session":
            project_id = await self.session.scalar(
                select(PracticeSessionModel.project_id).where(
                    PracticeSessionModel.id == scope_id,
                )
            )
        elif scope == "asset" and retention:
            project_id = await self.session.scalar(
                select(AssetModel.project_id).where(
                    AssetModel.id == scope_id,
                )
            )
        else:
            raise ErasureConflict("invalid_scope")
        if project_id is None:
            raise ErasureNotFound
        project = await self.session.scalar(
            select(ProjectModel)
            .where(
                ProjectModel.id == project_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise ErasureNotFound
        if (expected_team_id is not None and project.team_id != expected_team_id) or (
            expected_project_id is not None and project.id != expected_project_id
        ):
            raise ErasureNotFound
        if actor_id is not None:
            if await self._membership(project.team_id, actor_id) != "owner":
                raise ErasureForbidden
            if scope == "project" and confirmation != project.name:
                raise ErasureConflict("confirmation_required")

        previous = await self.session.scalar(
            select(ErasureRequestModel)
            .where(
                ErasureRequestModel.scope == scope,
                ErasureRequestModel.scope_id == scope_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            await self._record_command(previous, scope, scope_id, actor_id, key, digest)
            await self.session.commit()
            return await self._domain(previous)
        ancestor = await self.session.scalar(
            select(ErasureRequestModel).where(
                ErasureRequestModel.scope == "project",
                ErasureRequestModel.scope_id == project_id,
            )
        )
        if ancestor is not None:
            return await self._domain(ancestor)

        if scope == "project":
            sessions = list(
                (
                    await self.session.scalars(
                        select(PracticeSessionModel.id).where(
                            PracticeSessionModel.project_id == project.id,
                        )
                    )
                ).all()
            )
            assets = list(
                (
                    await self.session.scalars(
                        select(AssetModel.id).where(
                            AssetModel.project_id == project.id,
                        )
                    )
                ).all()
            )
        elif scope == "practice_session":
            target = await self.session.get(PracticeSessionModel, scope_id)
            if target is None:
                raise ErasureNotFound
            sessions = [scope_id]
            assets = await self._session_assets(scope_id, project.id)
        else:
            asset = await self.session.get(AssetModel, scope_id)
            if (
                asset is None
                or asset.kind not in ("presentation_video", "answer_audio")
                or asset.retention_expires_at is None
                or utc(asset.retention_expires_at) > now
            ):
                raise ErasureConflict("not_due")
            assets = [scope_id]
            versions = select(AssetVersionModel.id).where(AssetVersionModel.asset_id == scope_id)
            sessions = list(
                (
                    await self.session.scalars(
                        select(SessionManifestModel.session_id).where(
                            SessionManifestModel.presentation_version_id.in_(versions),
                        )
                    )
                ).all()
            )
            answer_sessions = (
                await self.session.scalars(
                    select(QARoundModel.practice_session_id)
                    .join(AnswerModel, AnswerModel.qa_round_id == QARoundModel.id)
                    .where(AnswerModel.audio_asset_version_id.in_(versions))
                )
            ).all()
            sessions = list(set(sessions) | set(answer_sessions))

        versions_rows = (
            await self.session.execute(
                select(
                    AssetVersionModel.id,
                    AssetVersionModel.storage_key,
                    AssetModel.kind,
                    AssetVersionModel.upload_expires_at,
                )
                .join(AssetModel)
                .where(AssetModel.id.in_(assets))
            )
        ).all()
        version_ids = [row[0] for row in versions_rows]
        answer_query = (
            select(AnswerModel.id)
            .join(QARoundModel)
            .where(
                QARoundModel.practice_session_id.in_(sessions),
            )
        )
        if retention:
            answer_query = answer_query.where(AnswerModel.audio_asset_version_id.in_(version_ids))
        answers = list((await self.session.scalars(answer_query)).all())
        job_ids = list(
            (
                await self.session.scalars(
                    select(AIJobModel.id).where(
                        AIJobModel.practice_session_id.in_(sessions),
                        AIJobModel.job_type != "erase_ai_data",
                    )
                )
            ).all()
        )
        inventory: dict[str, Any] = {
            "project_id": str(project.id),
            "session_ids": [str(s) for s in sessions],
            "asset_ids": [str(a) for a in assets],
            "version_ids": [str(v) for v in version_ids],
            "answer_ids": [str(a) for a in answers],
            "job_ids": [str(j) for j in job_ids],
            "upload_deadline": max(
                [now] + [utc(row[3]) for row in versions_rows if row[2] != "report_pdf"]
            ).isoformat(),
            "object_prefixes": (
                [f"teams/{project.team_id}/projects/{project.id}/", f"projects/{project.id}/"]
                if scope == "project"
                else [
                    prefix
                    for asset_id in assets
                    for prefix in (
                        f"teams/{project.team_id}/projects/{project.id}/assets/{asset_id}/",
                        f"projects/{project.id}/assets/{asset_id}/",
                    )
                ]
                + ([] if retention else [f"projects/{project.id}/sessions/{scope_id}/"])
            ),
        }
        request = ErasureRequestModel(
            id=uuid4(),
            team_id=project.team_id,
            project_id=project.id,
            scope=scope,
            scope_id=scope_id,
            requested_by=actor_id,
            origin="retention" if retention else "user_request",
            idempotency_key=key,
            request_hash=digest,
            status="pending",
            requested_at=now,
            deadline_at=now + timedelta(hours=24),
            next_attempt_at=now,
            inventory=inventory,
        )
        self.session.add(request)
        await self.session.flush()
        self.session.add_all(
            [ErasureStepModel(request_id=request.id, store=store) for store in STORES]
        )
        self.session.add_all(
            [
                ErasureItemModel(
                    id=uuid4(),
                    request_id=request.id,
                    store="generated_pdfs" if row[2] == "report_pdf" else "objects",
                    storage_key=row[1],
                )
                for row in versions_rows
            ]
        )
        if scope == "project":
            project.access_revoked_at = now
        if not retention:
            await self.session.execute(
                update(PracticeSessionModel)
                .where(
                    PracticeSessionModel.id.in_(sessions),
                )
                .values(access_revoked_at=now)
            )
        await self.session.execute(
            update(AssetModel).where(AssetModel.id.in_(assets)).values(state="deleting")
        )
        await self.session.execute(
            update(AssetVersionModel)
            .where(
                AssetVersionModel.asset_id.in_(assets),
            )
            .values(state="deleting")
        )
        await self.session.execute(
            update(AIJobModel)
            .where(
                AIJobModel.id.in_(job_ids),
                AIJobModel.status.in_(
                    [
                        AnalysisJobStatus.PENDING,
                        AnalysisJobStatus.QUEUED,
                        AnalysisJobStatus.RUNNING,
                    ]
                ),
            )
            .values(
                status=AnalysisJobStatus.CANCELLED,
                cancel_requested=True,
                completed_at=now,
                updated_at=now,
            )
        )
        job_id = uuid4()
        message = EraseAIDataQueueMessage(
            job_id=str(job_id),
            created_at=now,
            trace_id=str(request.id),
            payload=EraseAIDataPayload(
                erasure_request_id=str(request.id),
                scope=scope,
                scope_id=str(scope_id),
                practice_session_ids=inventory["session_ids"],
                answer_ids=inventory["answer_ids"],
                asset_version_ids=inventory["version_ids"],
                retention_only=retention,
            ),
        )
        self.session.add(
            AIJobModel(
                id=job_id,
                erasure_request_id=request.id,
                job_type="erase_ai_data",
                status=AnalysisJobStatus.PENDING,
                correlation_id=request.id,
                analysis_attempt=1,
                payload=message.model_dump(mode="json", exclude_none=True),
                created_at=now,
                updated_at=now,
            )
        )
        await self._record_command(request, scope, scope_id, actor_id, key, digest)
        await self.session.commit()
        return await self._domain(request)

    async def _session_assets(self, session_id: UUID, project_id: UUID) -> list[UUID]:
        presentation = select(SessionManifestModel.presentation_version_id).where(
            SessionManifestModel.session_id == session_id,
        )
        audio = (
            select(AnswerModel.audio_asset_version_id)
            .join(QARoundModel)
            .where(
                QARoundModel.practice_session_id == session_id,
            )
        )
        pdf = select(ReportExportModel.asset_version_id).where(
            ReportExportModel.practice_session_id == session_id
        )
        documents = (
            select(SessionManifestDocumentModel.document_version_id)
            .join(
                SessionManifestModel,
                SessionManifestModel.id == SessionManifestDocumentModel.manifest_id,
            )
            .where(SessionManifestModel.session_id == session_id)
        )
        legacy_document = select(SessionManifestModel.document_version_id).where(
            SessionManifestModel.session_id == session_id
        )
        candidates = list(
            (
                await self.session.scalars(
                    select(AssetVersionModel.asset_id)
                    .join(AssetModel)
                    .where(
                        AssetModel.project_id == project_id,
                        AssetVersionModel.id.in_(
                            presentation.union(audio, pdf, documents, legacy_document)
                        ),
                    )
                    .distinct()
                )
            ).all()
        )
        eligible = []
        for asset_id in candidates:
            version_ids = select(AssetVersionModel.id).where(AssetVersionModel.asset_id == asset_id)
            other_presentation = await self.session.scalar(
                select(SessionManifestModel.id)
                .where(
                    SessionManifestModel.session_id != session_id,
                    SessionManifestModel.presentation_version_id.in_(version_ids),
                )
                .limit(1)
            )
            other_audio = await self.session.scalar(
                select(AnswerModel.id)
                .join(QARoundModel)
                .where(
                    QARoundModel.practice_session_id != session_id,
                    AnswerModel.audio_asset_version_id.in_(version_ids),
                )
                .limit(1)
            )
            other_document = await self.session.scalar(
                select(SessionManifestModel.id)
                .where(
                    SessionManifestModel.session_id != session_id,
                    SessionManifestModel.document_version_id.in_(version_ids)
                    | SessionManifestModel.id.in_(
                        select(SessionManifestDocumentModel.manifest_id).where(
                            SessionManifestDocumentModel.document_version_id.in_(version_ids)
                        )
                    ),
                )
                .limit(1)
            )
            other_pdf = await self.session.scalar(
                select(ReportExportModel.id)
                .where(
                    ReportExportModel.practice_session_id != session_id,
                    ReportExportModel.asset_version_id.in_(version_ids),
                )
                .limit(1)
            )
            if all(
                ref is None for ref in (other_presentation, other_audio, other_document, other_pdf)
            ):
                eligible.append(asset_id)
        return eligible

    async def claim(self, now: datetime, lease_seconds: int) -> Erasure | None:
        model = await self.session.scalar(
            select(ErasureRequestModel)
            .where(
                ErasureRequestModel.status != "completed",
                ErasureRequestModel.next_attempt_at <= now,
                (
                    ErasureRequestModel.lease_until.is_(None)
                    | (ErasureRequestModel.lease_until <= now)
                ),
            )
            .order_by(ErasureRequestModel.deadline_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if model is None:
            await self.session.rollback()
            return None
        model.lease_until = now + timedelta(seconds=lease_seconds)
        model.lease_token = uuid4()
        model.status = "in_progress"
        await self.session.commit()
        return await self._domain(model)

    async def _leased(self, request: Erasure) -> ErasureRequestModel:
        model = await self.session.scalar(
            select(ErasureRequestModel)
            .where(
                ErasureRequestModel.id == request.id,
                ErasureRequestModel.lease_token == request.lease_token,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if model is None:
            raise ErasureConflict("lease_lost")
        return model

    async def renew(self, request: Erasure, now: datetime, lease_seconds: int) -> None:
        model = await self._leased(request)
        model.lease_until = now + timedelta(seconds=lease_seconds)
        await self.session.commit()

    async def finish_step(
        self,
        request: Erasure,
        store: str,
        now: datetime,
        *,
        records: int = 0,
        objects: int = 0,
        failure: str | None = None,
    ) -> None:
        if failure:
            # A failed SQL operation leaves PostgreSQL's transaction aborted.
            # Roll it back before saving a durable, safe failure for retry.
            await self.session.rollback()
        await self._leased(request)
        step = await self.session.get(ErasureStepModel, (request.id, store))
        assert step is not None
        if step.status != "completed":
            step.attempts += 1
            step.status = "failed" if failure else "completed"
            step.failure_code = failure
            step.deleted_records = records
            if store in ("objects", "generated_pdfs"):
                objects = int(
                    await self.session.scalar(
                        select(func.count())
                        .select_from(ErasureItemModel)
                        .where(
                            ErasureItemModel.request_id == request.id,
                            ErasureItemModel.store == store,
                            ErasureItemModel.status == "completed",
                        )
                    )
                    or 0
                )
            step.deleted_objects = objects
            step.completed_at = None if failure else now
        await self.session.commit()
        request.steps = (await self._domain(await self._leased(request))).steps

    async def release(self, request: Erasure, now: datetime, retry_seconds: int) -> None:
        model = await self._leased(request)
        steps = (await self._domain(model)).steps
        completed = all(step.status == "completed" for step in steps)
        model.status = (
            "completed"
            if completed
            else ("failed" if any(step.status == "failed" for step in steps) else "in_progress")
        )
        model.next_attempt_at = now + timedelta(seconds=retry_seconds)
        model.lease_until = None
        model.lease_token = None
        if completed:
            model.completed_at = now
            model.inventory = {}
            await self.session.execute(
                delete(ErasureItemModel).where(ErasureItemModel.request_id == request.id)
            )
            await self.session.execute(
                update(AIJobModel)
                .where(
                    AIJobModel.erasure_request_id == request.id,
                )
                .values(payload=None)
            )
        await self.session.commit()
        request.status = model.status

    async def object_items(self, request: Erasure, store: str) -> list[tuple[UUID, str]]:
        rows = (
            await self.session.execute(
                select(ErasureItemModel.id, ErasureItemModel.storage_key).where(
                    ErasureItemModel.request_id == request.id,
                    ErasureItemModel.store == store,
                    ErasureItemModel.status != "completed",
                )
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def add_objects(self, request: Erasure, keys: list[str]) -> None:
        await self._leased(request)
        existing = set(
            (
                await self.session.scalars(
                    select(ErasureItemModel.storage_key).where(
                        ErasureItemModel.request_id == request.id
                    )
                )
            ).all()
        )
        for key in set(keys) - existing:
            if not any(key.startswith(prefix) for prefix in request.inventory["object_prefixes"]):
                raise ErasureConflict("invalid_object_scope")
            self.session.add(
                ErasureItemModel(
                    id=uuid4(), request_id=request.id, store="objects", storage_key=key
                )
            )
        await self.session.commit()

    async def finish_object(self, request: Erasure, item_id: UUID) -> None:
        await self._leased(request)
        await self.session.execute(
            update(ErasureItemModel)
            .where(
                ErasureItemModel.id == item_id,
                ErasureItemModel.request_id == request.id,
            )
            .values(status="completed")
        )
        await self.session.commit()

    async def job_message(self, request: Erasure, now: datetime) -> EraseAIDataQueueMessage | None:
        job = await self.session.scalar(
            select(AIJobModel).where(AIJobModel.erasure_request_id == request.id)
        )
        assert job is not None
        if job.status == AnalysisJobStatus.COMPLETED:
            return None
        if job.status in (AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING) and utc(
            job.updated_at
        ) > now - timedelta(minutes=10):
            return None
        if job.status in (AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED):
            job.status = AnalysisJobStatus.PENDING
            job.cancel_requested = False
            job.retry_count += 1
            await self.session.commit()
        return EraseAIDataQueueMessage.model_validate(job.payload)

    async def mark_dispatched(self, request: Erasure, now: datetime) -> None:
        await self._leased(request)
        await self.session.execute(
            update(AIJobModel)
            .where(
                AIJobModel.erasure_request_id == request.id,
                AIJobModel.status == AnalysisJobStatus.PENDING,
            )
            .values(status=AnalysisJobStatus.QUEUED, queued_at=now, updated_at=now)
        )
        await self.session.commit()

    @staticmethod
    def _job_state(job: AIJobModel) -> ErasureJobState:
        return ErasureJobState(
            job.id, job.status.value, job.last_update_sequence, job.cancel_requested
        )

    async def job_state(self, job_id: UUID) -> ErasureJobState | None:
        job = await self.session.scalar(
            select(AIJobModel).where(
                AIJobModel.id == job_id,
                AIJobModel.job_type == "erase_ai_data",
            )
        )
        return self._job_state(job) if job else None

    async def record_update(
        self, job_id: UUID, worker_update: AIWorkerUpdate, now: datetime
    ) -> ErasureJobState | None:
        request_id = await self.session.scalar(
            select(AIJobModel.erasure_request_id).where(
                AIJobModel.id == job_id, AIJobModel.job_type == "erase_ai_data"
            )
        )
        if request_id is None:
            return None
        # Match the coordinator's request -> job lock order.
        await self.session.scalar(
            select(ErasureRequestModel)
            .where(ErasureRequestModel.id == request_id)
            .with_for_update()
        )
        job = await self.session.scalar(
            select(AIJobModel)
            .where(
                AIJobModel.id == job_id,
                AIJobModel.job_type == "erase_ai_data",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            return None
        if worker_update.trace_id != str(job.correlation_id):
            raise ErasureConflict("trace_mismatch")
        if (
            worker_update.sequence <= job.last_update_sequence
            or job.status == AnalysisJobStatus.COMPLETED
        ):
            return self._job_state(job)
        if worker_update.status == AIWorkerUpdateStatus.COMPLETED:
            try:
                payload = ErasureCompletedPayload.model_validate(worker_update.payload)
            except ValidationError:
                raise ErasureConflict("invalid_erasure_result") from None
            if payload.erasure_request_id != str(job.erasure_request_id):
                raise ErasureConflict("erasure_request_mismatch")
            job.status = AnalysisJobStatus.COMPLETED
            job.completed_at = now
            job.completed_result = payload.model_dump(mode="json")
            step = await self.session.get(ErasureStepModel, (job.erasure_request_id, "ai_data"))
            assert step is not None
            step.status = "completed"
            step.attempts += 1
            step.deleted_records = payload.deleted_records
            step.deleted_objects = payload.deleted_objects
            step.completed_at = now
            step.failure_code = None
        elif worker_update.status in (AIWorkerUpdateStatus.FAILED, AIWorkerUpdateStatus.CANCELLED):
            job.status = AnalysisJobStatus.FAILED
            step = await self.session.get(ErasureStepModel, (job.erasure_request_id, "ai_data"))
            assert step is not None
            step.status = "failed"
            step.attempts += 1
            step.failure_code = "ai_erasure_failed"
        else:
            job.status = AnalysisJobStatus.RUNNING
        job.last_update_sequence = worker_update.sequence
        job.updated_at = now
        await self.session.execute(
            update(ErasureRequestModel)
            .where(
                ErasureRequestModel.id == job.erasure_request_id,
            )
            .values(next_attempt_at=now)
        )
        await self.session.commit()
        return self._job_state(job)

    async def purge_records(self, request: Erasure) -> int:
        await self._leased(request)
        if request.origin == "retention":
            ids = [UUID(v) for v in request.inventory["asset_ids"]]
            await self.session.execute(
                update(AssetVersionModel)
                .where(
                    AssetVersionModel.asset_id.in_(ids),
                )
                .values(state="deleted")
            )
            await self.session.execute(
                update(AssetModel).where(AssetModel.id.in_(ids)).values(state="deleted")
            )
            count = 0  # Safe asset tombstones are updated, not physically deleted.
        elif request.scope == "project":
            count = await self._record_count(request)
            await delete_project_records(self.session, request.scope_id)
        else:
            count = await self._record_count(request)
            await delete_practice_sessions(self.session, [request.scope_id])
            for asset_id in request.inventory["asset_ids"]:
                await delete_asset_records(self.session, UUID(asset_id))
        await self.session.flush()
        return count

    async def _record_count(self, request: Erasure) -> int:
        sessions = [UUID(value) for value in request.inventory["session_ids"]]
        assets = [UUID(value) for value in request.inventory["asset_ids"]]
        attempts = select(AnalysisAttemptModel.id).where(
            AnalysisAttemptModel.session_id.in_(sessions)
        )
        rounds = select(QARoundModel.id).where(QARoundModel.practice_session_id.in_(sessions))
        manifests = select(SessionManifestModel.id).where(
            SessionManifestModel.session_id.in_(sessions)
        )
        queries = [
            select(PracticeSessionModel.id).where(PracticeSessionModel.id.in_(sessions)),
            select(AnalysisAttemptModel.id).where(AnalysisAttemptModel.session_id.in_(sessions)),
            select(AnalysisStageModel.id).where(AnalysisStageModel.attempt_id.in_(attempts)),
            select(SpeakerMappingModel.id).where(SpeakerMappingModel.attempt_id.in_(attempts)),
            select(SessionManifestModel.id).where(SessionManifestModel.session_id.in_(sessions)),
            select(SessionManifestDocumentModel.id).where(
                SessionManifestDocumentModel.manifest_id.in_(manifests)
            ),
            select(SessionCommandIdempotencyModel.id).where(
                SessionCommandIdempotencyModel.session_id.in_(sessions)
            ),
            select(QARoundModel.id).where(QARoundModel.practice_session_id.in_(sessions)),
            select(QuestionModel.id).where(QuestionModel.practice_session_id.in_(sessions)),
            select(AnswerModel.id).where(AnswerModel.qa_round_id.in_(rounds)),
            select(EvaluationModel.id).where(EvaluationModel.practice_session_id.in_(sessions)),
            select(ReportModel.id).where(ReportModel.practice_session_id.in_(sessions)),
            select(ReportExportModel.id).where(ReportExportModel.practice_session_id.in_(sessions)),
            select(AIJobModel.id).where(
                AIJobModel.practice_session_id.in_(sessions) | AIJobModel.attempt_id.in_(attempts)
            ),
            select(AssetModel.id).where(AssetModel.id.in_(assets)),
            select(AssetVersionModel.id).where(AssetVersionModel.asset_id.in_(assets)),
            select(AssetUploadIdempotencyModel.id).where(
                AssetUploadIdempotencyModel.asset_id.in_(assets)
            ),
        ]
        if request.scope == "project":
            queries.append(select(ProjectModel.id).where(ProjectModel.id == request.scope_id))
            queries.append(
                select(ProjectErasureRequestModel.id).where(
                    ProjectErasureRequestModel.project_id == request.scope_id
                )
            )
        return sum(
            [
                int(
                    await self.session.scalar(select(func.count()).select_from(query.subquery()))
                    or 0
                )
                for query in queries
            ]
        )

    async def retention_candidates(self, now: datetime, limit: int) -> list[UUID]:
        return list(
            (
                await self.session.scalars(
                    select(AssetModel.id)
                    .where(
                        AssetModel.kind.in_(["presentation_video", "answer_audio"]),
                        AssetModel.retention_expires_at <= now,
                        AssetModel.state.not_in(["deleting", "deleted"]),
                    )
                    .order_by(AssetModel.retention_expires_at)
                    .limit(limit)
                )
            ).all()
        )

    async def adopt_legacy(self, now: datetime, limit: int) -> int:
        legacy_rows = (
            await self.session.scalars(
                select(ProjectErasureRequestModel)
                .where(
                    ~ProjectErasureRequestModel.project_id.in_(
                        select(ErasureRequestModel.scope_id).where(
                            ErasureRequestModel.scope == "project"
                        )
                    )
                )
                .order_by(ProjectErasureRequestModel.created_at)
                .limit(limit)
            )
        ).all()
        accepted = 0
        adopted_projects: set[UUID] = set()
        for legacy in legacy_rows:
            if legacy.project_id in adopted_projects:
                continue
            adopted_projects.add(legacy.project_id)
            project = await self.session.get(ProjectModel, legacy.project_id)
            if project is None:
                continue
            request = await self.accept(
                "project", legacy.project_id, None, legacy.idempotency_key, project.name, now
            )
            model = await self.session.get(ErasureRequestModel, request.id)
            assert model is not None
            model.requested_by = legacy.requested_by
            model.requested_at = utc(legacy.created_at)
            model.deadline_at = utc(legacy.created_at) + timedelta(hours=24)
            await self.session.commit()
            accepted += 1
        return accepted
