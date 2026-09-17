from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.domain.asset import Asset, AssetVersion
from app.domain.idempotency import AssetUploadIdempotency
from app.domain.project import Project


class AssetRepository(ABC):
    @abstractmethod
    async def get_project(self, project_id: UUID) -> Project | None:
        pass

    @abstractmethod
    async def is_team_member(self, team_id: UUID, user_id: UUID) -> bool:
        pass

    @abstractmethod
    async def is_project_erasure_requested(self, project_id: UUID) -> bool:
        pass

    @abstractmethod
    async def get_upload_idempotency(
        self, user_id: UUID, project_id: UUID, operation: str, key: str
    ) -> AssetUploadIdempotency | None:
        pass

    @abstractmethod
    async def save_asset_with_initial_version(
        self,
        asset: Asset,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        pass

    @abstractmethod
    async def get_asset(self, asset_id: UUID) -> Asset | None:
        pass

    @abstractmethod
    async def get_version(self, version_id: UUID) -> AssetVersion | None:
        pass

    @abstractmethod
    async def get_version_for_update(self, version_id: UUID) -> AssetVersion | None:
        pass

    @abstractmethod
    async def save_version_completion(self, version: AssetVersion, asset: Asset) -> None:
        pass

    @abstractmethod
    async def save_version_rejection(self, version: AssetVersion, asset: Asset) -> None:
        pass

    @abstractmethod
    async def save_replacement_version(
        self,
        asset_id: UUID,
        version: AssetVersion,
        idempotency: AssetUploadIdempotency,
    ) -> tuple[Asset, AssetVersion]:
        pass

    @abstractmethod
    async def get_asset_and_version_for_completion(
        self, asset_id: UUID, version_id: UUID
    ) -> tuple[Asset | None, AssetVersion | None, AssetVersion | None]:
        pass

    @abstractmethod
    async def list_versions(
        self,
        asset_id: UUID,
        cursor: UUID | None,
        limit: int,
    ) -> tuple[list[AssetVersion], UUID | None]:
        pass

    @abstractmethod
    async def list_assets(
        self,
        project_id: UUID,
        cursor: UUID | None,
        limit: int,
        kind: str | None,
        state: str | None,
    ) -> tuple[list[Asset], UUID | None]:
        pass

    @abstractmethod
    async def find_cleanup_candidate_versions(
        self,
        cutoff_created_at: datetime,
        cutoff_expires_at: datetime,
        now: datetime,
        limit: int,
    ) -> list[tuple[UUID, UUID]]:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def record_cleanup_failure(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        pass

    @abstractmethod
    async def finalize_version_cleanup(
        self,
        asset_id: UUID,
        version_id: UUID,
        next_attempt_at: datetime,
    ) -> None:
        pass

    @abstractmethod
    async def delete_asset(self, asset_id: UUID) -> bool:
        pass
