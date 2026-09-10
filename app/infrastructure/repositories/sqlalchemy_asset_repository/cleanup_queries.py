from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.asset import AssetVersion
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository.mapping import to_utc, to_version


async def find_cleanup_candidate_versions(
    session: AsyncSession,
    cutoff_created_at: datetime,
    cutoff_expires_at: datetime,
    now: datetime,
    limit: int,
) -> list[tuple[UUID, UUID]]:
    stmt = (
        select(AssetVersionModel.asset_id, AssetVersionModel.id)
        .where(
            AssetVersionModel.state.in_(["pending_upload", "rejected", "deleting", "deleted"]),
            AssetVersionModel.upload_expires_at <= cutoff_expires_at,
            AssetVersionModel.created_at <= cutoff_created_at,
            (
                AssetVersionModel.cleanup_next_attempt_at.is_(None)
                | (AssetVersionModel.cleanup_next_attempt_at <= now)
            ),
        )
        .order_by(
            AssetVersionModel.cleanup_next_attempt_at.asc().nulls_first(),
            AssetVersionModel.created_at.asc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def claim_version_for_cleanup(
    session: AsyncSession,
    asset_id: UUID,
    version_id: UUID,
    now: datetime,
    lease_seconds: int,
    tombstone_delay_seconds: int,
    cutoff_created_at: datetime,
    cutoff_expires_at: datetime,
) -> AssetVersion | None:
    bind = session.bind
    is_pg = bind is not None and bind.dialect.name == "postgresql"

    asset_stmt = (
        select(AssetModel)
        .where(AssetModel.id == asset_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    asset_model = await session.scalar(asset_stmt)
    if asset_model is None:
        return None

    ver_stmt = (
        select(AssetVersionModel)
        .where(AssetVersionModel.id == version_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    ver_model = await session.scalar(ver_stmt)
    if ver_model is None or ver_model.asset_id != asset_id:
        return None

    if ver_model.state not in ("pending_upload", "rejected", "deleting", "deleted"):
        return None
    exp_utc = to_utc(ver_model.upload_expires_at)
    created_utc = to_utc(ver_model.created_at)
    cleanup_next_utc = to_utc(ver_model.cleanup_next_attempt_at)
    if exp_utc is not None and exp_utc > cutoff_expires_at:
        return None
    if created_utc is not None and created_utc > cutoff_created_at:
        return None
    if cleanup_next_utc is not None and cleanup_next_utc > now:
        return None

    if ver_model.state in ("pending_upload", "rejected", "deleting"):
        ver_model.state = "deleting"
        ver_model.cleanup_next_attempt_at = now + timedelta(seconds=lease_seconds)
    elif ver_model.state == "deleted":
        ver_model.cleanup_next_attempt_at = now + timedelta(seconds=tombstone_delay_seconds)

    await session.commit()
    return to_version(ver_model)


async def record_cleanup_failure(
    session: AsyncSession,
    asset_id: UUID,
    version_id: UUID,
    next_attempt_at: datetime,
) -> None:
    bind = session.bind
    is_pg = bind is not None and bind.dialect.name == "postgresql"

    asset_stmt = (
        select(AssetModel)
        .where(AssetModel.id == asset_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    asset_model = await session.scalar(asset_stmt)
    if asset_model is None:
        return

    ver_stmt = (
        select(AssetVersionModel)
        .where(AssetVersionModel.id == version_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    ver_model = await session.scalar(ver_stmt)
    if (
        ver_model is not None
        and ver_model.asset_id == asset_id
        and ver_model.state in ("deleting", "deleted")
    ):
        ver_model.cleanup_next_attempt_at = next_attempt_at
        await session.commit()


async def finalize_version_cleanup(
    session: AsyncSession,
    asset_id: UUID,
    version_id: UUID,
    next_attempt_at: datetime,
) -> None:
    bind = session.bind
    is_pg = bind is not None and bind.dialect.name == "postgresql"

    asset_stmt = (
        select(AssetModel)
        .where(AssetModel.id == asset_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    asset_model = await session.scalar(asset_stmt)
    if asset_model is None:
        return

    ver_stmt = (
        select(AssetVersionModel)
        .where(AssetVersionModel.id == version_id)
        .with_for_update(skip_locked=is_pg)
        .execution_options(populate_existing=True)
    )
    ver_model = await session.scalar(ver_stmt)
    if ver_model is None or ver_model.asset_id != asset_id:
        return
    if ver_model.state not in ("deleting", "deleted"):
        return

    ver_model.state = "deleted"
    ver_model.cleanup_next_attempt_at = next_attempt_at

    if asset_model.current_version_id == version_id:
        verified_stmt = (
            select(AssetVersionModel)
            .where(
                AssetVersionModel.asset_id == asset_id,
                AssetVersionModel.state == "verified",
                AssetVersionModel.id != version_id,
            )
            .order_by(AssetVersionModel.version_number.desc())
            .limit(1)
        )
        latest_verified = await session.scalar(verified_stmt)
        if latest_verified is not None:
            asset_model.current_version_id = latest_verified.id
            asset_model.state = "verified"
            asset_model.file_name = latest_verified.file_name
            asset_model.media_type = latest_verified.media_type
            asset_model.size_bytes = latest_verified.size_bytes
            asset_model.checksum = latest_verified.checksum
            asset_model.duration_ms = latest_verified.duration_ms
        else:
            pending_stmt = (
                select(AssetVersionModel)
                .where(
                    AssetVersionModel.asset_id == asset_id,
                    AssetVersionModel.state == "pending_upload",
                    AssetVersionModel.id != version_id,
                )
                .order_by(AssetVersionModel.version_number.desc())
                .limit(1)
            )
            latest_pending = await session.scalar(pending_stmt)
            if latest_pending is not None:
                asset_model.current_version_id = latest_pending.id
                asset_model.state = "pending_upload"
                asset_model.file_name = latest_pending.file_name
                asset_model.media_type = None
                asset_model.size_bytes = None
                asset_model.checksum = None
                asset_model.duration_ms = None
                asset_model.rejection_reason = None
            else:
                asset_model.state = "deleted"

    await session.commit()
