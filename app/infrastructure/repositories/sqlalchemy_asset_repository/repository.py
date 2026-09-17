from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.asset_repository import AssetRepository
from app.domain.asset import Asset, AssetIdempotencyConflict, AssetNotFound, AssetVersion
from app.domain.idempotency import AssetUploadIdempotency
from app.domain.project import Project
from app.infrastructure.persistence.cascade_deletion import delete_asset_records
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetUploadIdempotencyModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.project_configuration import ProjectModel
from app.infrastructure.persistence.configurations.project_erasure_request import (
    ProjectErasureRequestModel,
)
from app.infrastructure.persistence.configurations.team_member_configuration import TeamMemberModel
from app.infrastructure.repositories.sqlalchemy_asset_repository import cleanup_queries
from app.infrastructure.repositories.sqlalchemy_asset_repository.mapping import (
    to_asset,
    to_utc,
    to_version,
)


class SqlAlchemyAssetRepository(AssetRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

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
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            checksum=asset.checksum,
            created_by=asset.created_by,
            created_at=asset.created_at,
            retention_expires_at=asset.retention_expires_at,
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
            media_type=version.media_type,
            size_bytes=version.size_bytes,
            checksum=version.checksum,
            completed_at=version.completed_at,
            created_by=version.created_by,
            created_at=version.created_at,
            upload_expires_at=version.upload_expires_at
            or (version.created_at + timedelta(seconds=3600)),
            cleanup_next_attempt_at=version.cleanup_next_attempt_at,
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
                    now = datetime.now(UTC)
                    if existing_version.state != "pending_upload":
                        st = existing_version.state
                        raise AssetIdempotencyConflict(
                            f"Cannot replay upload intent for version in state '{st}'"
                        ) from exc
                    exp_utc = to_utc(existing_version.upload_expires_at)
                    if exp_utc is not None and exp_utc <= now:
                        raise AssetIdempotencyConflict("Upload intent has expired") from exc
                    return existing_asset, existing_version
            raise

        return asset, version

    async def get_asset(self, asset_id: UUID) -> Asset | None:
        model = await self.session.get(AssetModel, asset_id)
        return to_asset(model) if model is not None else None

    async def get_version(self, version_id: UUID) -> AssetVersion | None:
        model = await self.session.get(AssetVersionModel, version_id)
        return to_version(model) if model is not None else None

    async def get_version_for_update(self, version_id: UUID) -> AssetVersion | None:
        stmt = (
            select(AssetVersionModel)
            .where(AssetVersionModel.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        model = await self.session.scalar(stmt)
        return to_version(model) if model is not None else None

    async def save_replacement_version(
        self,
        asset_id: UUID,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        stmt = (
            select(AssetModel)
            .where(AssetModel.id == asset_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        asset_model = await self.session.scalar(stmt)
        if asset_model is None or asset_model.state == "deleted":
            raise AssetNotFound

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
                )
            existing_ver = await self.get_version(existing.version_id)
            if existing_ver is not None:
                now = datetime.now(UTC)
                if existing_ver.state != "pending_upload":
                    raise AssetIdempotencyConflict(
                        f"Cannot replay upload intent for version in state '{existing_ver.state}'"
                    )
                exp_utc = to_utc(existing_ver.upload_expires_at)
                if exp_utc is not None and exp_utc <= now:
                    raise AssetIdempotencyConflict("Upload intent has expired")
                return to_asset(asset_model), existing_ver

        max_v_stmt = select(func.coalesce(func.max(AssetVersionModel.version_number), 0)).where(
            AssetVersionModel.asset_id == asset_id
        )
        current_max = await self.session.scalar(max_v_stmt)
        next_version_num = (current_max or 0) + 1
        version.version_number = next_version_num

        version_model = AssetVersionModel(
            id=version.id,
            asset_id=asset_id,
            version_number=version.version_number,
            state=version.state,
            storage_key=version.storage_key,
            file_name=version.file_name,
            declared_media_type=version.declared_media_type,
            declared_size_bytes=version.declared_size_bytes,
            created_by=version.created_by,
            created_at=version.created_at,
            upload_expires_at=version.upload_expires_at
            or (version.created_at + timedelta(seconds=3600)),
            cleanup_next_attempt_at=version.cleanup_next_attempt_at,
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
                existing_ver = await self.get_version(existing.version_id)
                if existing_ver is not None:
                    now = datetime.now(UTC)
                    if existing_ver.state != "pending_upload":
                        st = existing_ver.state
                        raise AssetIdempotencyConflict(
                            f"Cannot replay upload intent for version in state '{st}'"
                        ) from exc
                    exp_utc = to_utc(existing_ver.upload_expires_at)
                    if exp_utc is not None and exp_utc <= now:
                        raise AssetIdempotencyConflict("Upload intent has expired") from exc
                    return to_asset(asset_model), existing_ver
            raise

        await self.session.commit()
        return to_asset(asset_model), version

    async def get_asset_and_version_for_completion(
        self, asset_id: UUID, version_id: UUID
    ) -> tuple[Asset | None, AssetVersion | None, AssetVersion | None]:
        asset_stmt = (
            select(AssetModel)
            .where(AssetModel.id == asset_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        asset_model = await self.session.scalar(asset_stmt)
        if asset_model is None:
            return None, None, None

        ver_stmt = (
            select(AssetVersionModel)
            .where(AssetVersionModel.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        version_model = await self.session.scalar(ver_stmt)
        if version_model is None or version_model.asset_id != asset_id:
            return to_asset(asset_model), None, None

        current_ver: AssetVersion | None = None
        if asset_model.current_version_id is not None:
            if asset_model.current_version_id == version_model.id:
                current_ver = to_version(version_model)
            else:
                cur_stmt = (
                    select(AssetVersionModel)
                    .where(AssetVersionModel.id == asset_model.current_version_id)
                    .execution_options(populate_existing=True)
                )
                cur_model = await self.session.scalar(cur_stmt)
                if cur_model is not None:
                    current_ver = to_version(cur_model)

        return to_asset(asset_model), to_version(version_model), current_ver

    async def list_versions(
        self,
        asset_id: UUID,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[AssetVersion], UUID | None]:
        stmt = (
            select(AssetVersionModel)
            .where(AssetVersionModel.asset_id == asset_id)
            .order_by(AssetVersionModel.version_number.asc(), AssetVersionModel.id.asc())
            .limit(limit + 1)
        )
        if cursor is not None:
            cursor_ver = await self.get_version(cursor)
            if cursor_ver is not None:
                stmt = stmt.where(
                    (AssetVersionModel.version_number > cursor_ver.version_number)
                    | (
                        (AssetVersionModel.version_number == cursor_ver.version_number)
                        & (AssetVersionModel.id > cursor)
                    )
                )
            else:
                stmt = stmt.where(AssetVersionModel.id > cursor)

        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
        return [to_version(row) for row in rows], next_cursor

    async def save_version_completion(self, version: AssetVersion, asset: Asset) -> None:
        await self.session.execute(
            update(AssetVersionModel)
            .where(AssetVersionModel.id == version.id)
            .values(
                state=version.state,
                size_bytes=version.size_bytes,
                checksum=version.checksum,
                media_type=version.media_type,
                duration_ms=version.duration_ms,
                completed_at=version.completed_at,
            )
        )
        await self.session.execute(
            update(AssetModel)
            .where(AssetModel.id == asset.id)
            .values(
                state=asset.state,
                file_name=asset.file_name,
                size_bytes=asset.size_bytes,
                checksum=asset.checksum,
                media_type=asset.media_type,
                duration_ms=asset.duration_ms,
                current_version_id=asset.current_version_id,
                retention_expires_at=asset.retention_expires_at,
                rejection_reason=asset.rejection_reason,
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
        else:
            stmt = stmt.where(AssetModel.state != "deleted")

        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None
        return [to_asset(row) for row in rows], next_cursor

    async def find_cleanup_candidate_versions(
        self,
        cutoff_created_at: datetime,
        cutoff_expires_at: datetime,
        now: datetime,
        limit: int,
    ) -> list[tuple[UUID, UUID]]:
        return await cleanup_queries.find_cleanup_candidate_versions(
            self.session, cutoff_created_at, cutoff_expires_at, now, limit
        )

    async def claim_version_for_cleanup(
        self,
        asset_id: UUID,
        version_id: UUID,
        now: datetime,
        lease_seconds: int,
        tombstone_delay_seconds: int,
        cutoff_created_at: datetime,
        cutoff_expires_at: datetime,
    ) -> AssetVersion | None:
        return await cleanup_queries.claim_version_for_cleanup(
            self.session,
            asset_id,
            version_id,
            now,
            lease_seconds,
            tombstone_delay_seconds,
            cutoff_created_at,
            cutoff_expires_at,
        )

    async def record_cleanup_failure(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        await cleanup_queries.record_cleanup_failure(
            self.session, asset_id, version_id, next_attempt_at
        )

    async def finalize_version_cleanup(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        await cleanup_queries.finalize_version_cleanup(
            self.session, asset_id, version_id, next_attempt_at
        )

    async def delete_asset(self, asset_id: UUID) -> bool:
        success = await delete_asset_records(self.session, asset_id)
        await self.session.flush()
        return success
