from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.assetRepository import AssetRepository
from app.domain.asset import Asset, AssetIdempotencyConflict, AssetVersion
from app.domain.idempotency import AssetUploadIdempotency
from app.domain.project import Project
from app.infrastructure.persistence.configurations.assetConfiguration import (
    AssetModel,
    AssetUploadIdempotencyModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.projectConfigration import ProjectModel
from app.infrastructure.persistence.configurations.projectErasureRequest import (
    ProjectErasureRequestModel,
)
from app.infrastructure.persistence.configurations.teamMemberCongfigration import TeamMemberModel


class SqlAlchemyAssetRepository(AssetRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _to_asset(model: AssetModel) -> Asset:
        return Asset(
            id=model.id,
            project_id=model.project_id,
            kind=model.kind,
            state=model.state,
            file_name=model.file_name,
            current_version_id=model.current_version_id,
            created_by=model.created_by,
            created_at=model.created_at,
            media_type=model.media_type,
            size_bytes=model.size_bytes,
            checksum=model.checksum,
            duration_ms=model.duration_ms,
            rejection_reason=model.rejection_reason,
        )

    @staticmethod
    def _to_version(model: AssetVersionModel) -> AssetVersion:
        return AssetVersion(
            id=model.id,
            asset_id=model.asset_id,
            version_number=model.version_number,
            state=model.state,
            storage_key=model.storage_key,
            file_name=model.file_name,
            declared_media_type=model.declared_media_type,
            declared_size_bytes=model.declared_size_bytes,
            created_by=model.created_by,
            created_at=model.created_at,
            media_type=model.media_type,
            size_bytes=model.size_bytes,
            checksum=model.checksum,
            duration_ms=model.duration_ms,
            rejection_reason=model.rejection_reason,
            completed_at=model.completed_at,
        )

    async def get_project(self, project_id: UUID) -> Project | None:
        model = await self.session.get(ProjectModel, project_id)
        if model is None:
            return None
        return Project(
            id=model.id,
            team_id=model.team_id,
            name=model.name,
            description=model.description,
            created_at=model.created_at,
            version=model.version,
        )

    async def is_team_member(self, team_id: UUID, user_id: UUID) -> bool:
        stmt = select(TeamMemberModel.id).where(
            TeamMemberModel.team_id == team_id,
            TeamMemberModel.user_id == user_id,
        )
        return (await self.session.scalar(stmt)) is not None

    async def is_project_erasure_requested(self, project_id: UUID) -> bool:
        stmt = select(ProjectErasureRequestModel.id).where(
            ProjectErasureRequestModel.project_id == project_id
        )
        return (await self.session.scalar(stmt)) is not None

    async def get_upload_idempotency(
        self, user_id: UUID, project_id: UUID, operation: str, key: str
    ) -> AssetUploadIdempotency | None:
        stmt = select(AssetUploadIdempotencyModel).where(
            AssetUploadIdempotencyModel.user_id == user_id,
            AssetUploadIdempotencyModel.project_id == project_id,
            AssetUploadIdempotencyModel.operation == operation,
            AssetUploadIdempotencyModel.key == key,
        )
        model = await self.session.scalar(stmt)
        if model is None:
            return None
        return AssetUploadIdempotency(
            user_id=model.user_id,
            project_id=model.project_id,
            operation=model.operation,
            key=model.key,
            request_hash=model.request_hash,
            asset_id=model.asset_id,
            version_id=model.version_id,
        )

    async def save_asset_with_initial_version(
        self,
        asset: Asset,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        asset_model = AssetModel(
            id=asset.id,
            project_id=asset.project_id,
            kind=asset.kind,
            state=asset.state,
            file_name=asset.file_name,
            current_version_id=asset.current_version_id,
            created_by=asset.created_by,
            created_at=asset.created_at,
        )
        version_model = AssetVersionModel(
            id=version.id,
            asset_id=version.asset_id,
            version_number=version.version_number,
            state=version.state,
            storage_key=version.storage_key,
            file_name=version.file_name,
            declared_media_type=version.declared_media_type,
            declared_size_bytes=version.declared_size_bytes,
            created_by=version.created_by,
            created_at=version.created_at,
        )
        idempotency_model = AssetUploadIdempotencyModel(
            user_id=idempotency.user_id,
            project_id=idempotency.project_id,
            operation=idempotency.operation,
            key=idempotency.key,
            request_hash=idempotency.request_hash,
            asset_id=idempotency.asset_id,
            version_id=idempotency.version_id,
            created_at=version.created_at,
        )

        try:
            async with self.session.begin_nested():
                self.session.add(asset_model)
                self.session.add(version_model)
                await self.session.flush()
                self.session.add(idempotency_model)
                await self.session.flush()
        except IntegrityError as exc:
            existing = await self.get_upload_idempotency(
                idempotency.user_id,
                idempotency.project_id,
                idempotency.operation,
                idempotency.key,
            )
            if existing is not None:
                if existing.request_hash != idempotency.request_hash:
                    raise AssetIdempotencyConflict(
                        "Idempotency key already used with different parameters"
                    ) from exc
                existing_asset = await self.get_asset(existing.asset_id)
                existing_version = await self.get_version(existing.version_id)
                if existing_asset is not None and existing_version is not None:
                    return existing_asset, existing_version
            raise

        return asset, version

    async def get_asset(self, asset_id: UUID) -> Asset | None:
        model = await self.session.get(AssetModel, asset_id)
        return self._to_asset(model) if model is not None else None

    async def get_version(self, version_id: UUID) -> AssetVersion | None:
        model = await self.session.get(AssetVersionModel, version_id)
        return self._to_version(model) if model is not None else None

    async def get_version_for_update(self, version_id: UUID) -> AssetVersion | None:
        stmt = select(AssetVersionModel).where(AssetVersionModel.id == version_id).with_for_update()
        model = await self.session.scalar(stmt)
        return self._to_version(model) if model is not None else None

    async def save_version_completion(self, version: AssetVersion, asset: Asset) -> None:
        await self.session.execute(
            update(AssetVersionModel)
            .where(AssetVersionModel.id == version.id)
            .values(
                state=version.state,
                size_bytes=version.size_bytes,
                checksum=version.checksum,
                media_type=version.media_type,
                completed_at=version.completed_at,
            )
        )
        await self.session.execute(
            update(AssetModel)
            .where(AssetModel.id == asset.id)
            .values(
                state=asset.state,
                size_bytes=asset.size_bytes,
                checksum=asset.checksum,
                media_type=asset.media_type,
                current_version_id=asset.current_version_id,
            )
        )
        await self.session.commit()

    async def save_version_rejection(self, version: AssetVersion, asset: Asset) -> None:
        await self.session.execute(
            update(AssetVersionModel)
            .where(AssetVersionModel.id == version.id)
            .values(
                state=version.state,
                rejection_reason=version.rejection_reason,
            )
        )
        await self.session.execute(
            update(AssetModel)
            .where(AssetModel.id == asset.id)
            .values(
                state=asset.state,
                rejection_reason=asset.rejection_reason,
            )
        )
        await self.session.commit()

    async def list_assets(
        self,
        project_id: UUID,
        cursor: UUID | None,
        limit: int,
        kind: str | None,
        state: str | None,
    ) -> tuple[list[Asset], UUID | None]:
        stmt = (
            select(AssetModel)
            .where(AssetModel.project_id == project_id)
            .order_by(AssetModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(AssetModel.id > cursor)
        if kind is not None:
            stmt = stmt.where(AssetModel.kind == kind)
        if state is not None:
            stmt = stmt.where(AssetModel.state == state)

        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
        return [self._to_asset(row) for row in rows], next_cursor
