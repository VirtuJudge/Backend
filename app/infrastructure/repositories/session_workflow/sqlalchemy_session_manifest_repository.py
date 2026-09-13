from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.ports.session_practice.session_manifest_repository import (
    SessionManifestRepository,
)
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.domain.session_workflow.exceptions import IdempotencyConflict
from app.infrastructure.persistence.mappers.session_practice.session_manifest_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.session_manifest_configuration import (
    SessionManifestModel,
)
from ...persistence.configurations.session_workflow.session_manifest_document_configuration import (
    SessionManifestDocumentModel,
)


class SqlAlchemySessionManifestRepository(SessionManifestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_session_id(
        self,
        session_id: UUID,
    ) -> SessionManifest | None:
        stmt = (
            select(SessionManifestModel)
            .options(selectinload(SessionManifestModel.supporting_documents))
            .where(SessionManifestModel.session_id == session_id)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return None if model is None else to_domain(model)

    async def get_by_id(
        self,
        manifest_id: UUID,
    ) -> SessionManifest | None:
        stmt = (
            select(SessionManifestModel)
            .options(selectinload(SessionManifestModel.supporting_documents))
            .where(SessionManifestModel.id == manifest_id)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return None if model is None else to_domain(model)

    async def create(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest:
        model = to_model(manifest)
        self._session.add(model)
        now = datetime.now(UTC)
        for doc_id in manifest.supporting_document_version_ids:
            doc_model = SessionManifestDocumentModel(
                id=uuid4(),
                manifest_id=manifest.id,
                document_version_id=doc_id,
                created_at=now,
            )
            self._session.add(doc_model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Session manifest already exists or conflict.") from exc
        return manifest

    async def update(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest:
        doc_id = (
            manifest.supporting_document_version_ids[0]
            if manifest.supporting_document_version_ids
            else None
        )

        stmt = (
            update(SessionManifestModel)
            .where(SessionManifestModel.id == manifest.id)
            .values(
                presentation_version_id=manifest.presentation_version_id,
                document_version_id=doc_id,
                rubric_id=manifest.rubric_id,
                rubric_version=manifest.rubric_version,
                snapshot=manifest.snapshot,
                frozen_at=manifest.frozen_at,
            )
        )
        await self._session.execute(stmt)

        await self._session.execute(
            delete(SessionManifestDocumentModel).where(
                SessionManifestDocumentModel.manifest_id == manifest.id
            )
        )
        now = datetime.now(UTC)
        for doc_version_id in manifest.supporting_document_version_ids:
            self._session.add(
                SessionManifestDocumentModel(
                    id=uuid4(),
                    manifest_id=manifest.id,
                    document_version_id=doc_version_id,
                    created_at=now,
                )
            )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise IdempotencyConflict("Integrity conflict on manifest update.") from exc
        return manifest
