from asyncio import Protocol
from uuid import UUID

from app.domain.session_workflow.entities.session_manifest import SessionManifest


class SessionManifestRepository(Protocol):
    async def get_by_session_id(
        self,
        session_id: UUID,
    ) -> SessionManifest | None: ...

    async def get_by_id(
        self,
        manifest_id: UUID,
    ) -> SessionManifest | None: ...

    async def create(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest: ...

    async def update(
        self,
        manifest: SessionManifest,
    ) -> SessionManifest: ...
