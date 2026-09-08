from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TeamCreationIdempotency:
    user_id: UUID
    key: str
    request_hash: str
    team_id: UUID


@dataclass(frozen=True, slots=True)
class AssetUploadIdempotency:
    user_id: UUID
    project_id: UUID
    operation: str
    key: str
    request_hash: str
    asset_id: UUID
    version_id: UUID
