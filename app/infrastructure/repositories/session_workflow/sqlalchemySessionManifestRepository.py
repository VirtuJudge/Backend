from sqlalchemy import UUID, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.session_practice.session_manifest_repository import (
    SessionManifestRepository,
)
from app.domain.session_workflow.entities.session_manifest import SessionManifest
from app.infrastructure.persistence.mappers.session_practice.session_manifest_mapper import (
    to_domain,
    to_model,
)

from ...persistence.configurations.session_workflow.sessionManifestConfiguration import (
    SessionManifestModel,
)


class SqlAlchemySessionManifestRepository(SessionManifestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_session_id(
        self,
        session_id: UUID,
    ) -> SessionManifest | None:

        stmt = select(SessionManifestModel).where(SessionManifestModel.session_id == session_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def get_by_id(
        self,
        manifest_id: UUID,
    ) -> SessionManifest | None:

        stmt = select(SessionManifestModel).where(SessionManifestModel.id == manifest_id)

        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()

        return None if model is None else to_domain(model)

    async def create(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest:

        model = to_model(manifest)

        self._session.add(model)
        await self._session.flush()

        return to_domain(model)

    async def update(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest:

        stmt = (
            update(SessionManifestModel)
            .where(SessionManifestModel.id == manifest.id)
            .values(
                presentation_version_id=manifest.presentation_version_id,
                document_version_id=manifest.document_version_id,
                frozen_at=manifest.frozen_at,
            )
        )

        await self._session.execute(stmt)
        await self._session.flush()

        return manifest
