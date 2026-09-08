from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.services import get_asset_store
from app.api.errors import handle_asset_error
from app.api.schemas.asset import (
    AssetPage,
    AssetResponse,
    AssetVersionPage,
    AssetVersionResponse,
    CompleteUploadRequest,
    DownloadIntentResponse,
    UploadIntentRequest,
    UploadIntentResponse,
    VersionUploadIntentRequest,
)
from app.application.services.asset_store import AssetStore
from app.domain.asset import (
    Asset,
    AssetDomainError,
    AssetVersion,
)
from app.domain.user import User

router = APIRouter(prefix="/api/v1")


def asset_to_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        version_id=asset.current_version_id,
        project_id=asset.project_id,
        kind=asset.kind,
        state=asset.state,
        file_name=asset.file_name,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        checksum=asset.checksum,
        duration_ms=asset.duration_ms,
        created_by=asset.created_by,
        created_at=asset.created_at,
        retention_expires_at=asset.retention_expires_at,
        rejection_reason=asset.rejection_reason,
    )


def version_to_response(version: AssetVersion) -> AssetVersionResponse:
    return AssetVersionResponse(
        id=version.id,
        asset_id=version.asset_id,
        version_number=version.version_number,
        state=version.state,
        file_name=version.file_name,
        declared_media_type=version.declared_media_type,
        declared_size_bytes=version.declared_size_bytes,
        media_type=version.media_type,
        size_bytes=version.size_bytes,
        checksum=version.checksum,
        duration_ms=version.duration_ms,
        created_by=version.created_by,
        created_at=version.created_at,
        completed_at=version.completed_at,
        rejection_reason=version.rejection_reason,
    )


@router.post(
    "/projects/{project_id}/assets/upload-intents",
    response_model=UploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def create_upload_intent(
    project_id: UUID,
    payload: UploadIntentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        intent = await service.create_upload_intent(
            project_id=project_id,
            user_id=current_user.id,
            kind=payload.kind,
            file_name=payload.file_name,
            declared_media_type=payload.declared_media_type,
            declared_size_bytes=payload.declared_size_bytes,
            idempotency_key=idempotency_key,
        )
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return UploadIntentResponse(
        asset_id=intent.asset_id,
        asset_version_id=intent.asset_version_id,
        upload_url=intent.upload_url,
        method=intent.method,
        required_headers=intent.required_headers,
        expires_at=intent.expires_at,
        maximum_size_bytes=intent.maximum_size_bytes,
    )


@router.post(
    "/assets/{asset_id}/versions/upload-intents",
    response_model=UploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def create_version_upload_intent(
    asset_id: UUID,
    payload: VersionUploadIntentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        intent = await service.create_version_upload_intent(
            asset_id=asset_id,
            user_id=current_user.id,
            file_name=payload.file_name,
            declared_media_type=payload.declared_media_type,
            declared_size_bytes=payload.declared_size_bytes,
            idempotency_key=idempotency_key,
        )
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return UploadIntentResponse(
        asset_id=intent.asset_id,
        asset_version_id=intent.asset_version_id,
        upload_url=intent.upload_url,
        method=intent.method,
        required_headers=intent.required_headers,
        expires_at=intent.expires_at,
        maximum_size_bytes=intent.maximum_size_bytes,
    )


@router.post(
    "/assets/{asset_id}/versions/{version_id}/complete",
    response_model=AssetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["assets"],
)
async def complete_upload(
    asset_id: UUID,
    version_id: UUID,
    payload: CompleteUploadRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> Any:
    try:
        asset = await service.complete_upload(
            asset_id=asset_id,
            version_id=version_id,
            user_id=current_user.id,
            checksum=payload.checksum,
            size_bytes=payload.size_bytes,
            idempotency_key=idempotency_key,
        )
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return asset_to_response(asset)


@router.get(
    "/projects/{project_id}/assets",
    response_model=AssetPage,
    tags=["assets"],
)
async def list_project_assets(
    project_id: UUID,
    request: Request,
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    kind: str | None = Query(default=None),
    state: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        assets, next_cursor = await service.list_assets(
            project_id=project_id,
            user_id=current_user.id,
            cursor=cursor,
            limit=limit,
            kind=kind,
            state=state,
        )
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return AssetPage(
        items=[asset_to_response(asset) for asset in assets],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/assets/{asset_id}",
    response_model=AssetResponse,
    tags=["assets"],
)
async def get_asset(
    asset_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        asset = await service.get_asset(asset_id, current_user.id)
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return asset_to_response(asset)


@router.get(
    "/assets/{asset_id}/versions",
    response_model=AssetVersionPage,
    tags=["assets"],
)
async def list_asset_versions(
    asset_id: UUID,
    request: Request,
    cursor: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        versions, next_cursor = await service.list_versions(
            asset_id=asset_id,
            user_id=current_user.id,
            cursor=cursor,
            limit=limit,
        )
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return AssetVersionPage(
        items=[version_to_response(v) for v in versions],
        next_cursor=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/assets/{asset_id}/versions/{version_id}",
    response_model=AssetVersionResponse,
    tags=["assets"],
)
async def get_asset_version(
    asset_id: UUID,
    version_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        version = await service.get_version(asset_id, version_id, current_user.id)
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return version_to_response(version)


@router.post(
    "/assets/{asset_id}/versions/{version_id}/download-intents",
    response_model=DownloadIntentResponse,
    tags=["assets"],
)
async def create_version_download_intent(
    asset_id: UUID,
    version_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        intent = await service.create_version_download_intent(asset_id, version_id, current_user.id)
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return DownloadIntentResponse(
        asset_id=intent.asset_id,
        asset_version_id=intent.asset_version_id,
        download_url=intent.download_url,
        expires_at=intent.expires_at,
        media_type=intent.media_type,
        size_bytes=intent.size_bytes,
        file_name=intent.file_name,
    )


@router.post(
    "/assets/{asset_id}/download-intents",
    response_model=DownloadIntentResponse,
    tags=["assets"],
)
async def create_download_intent(
    asset_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: AssetStore = Depends(get_asset_store),
) -> Any:
    try:
        intent = await service.create_download_intent(asset_id, current_user.id)
    except AssetDomainError as err:
        return handle_asset_error(err, request.url.path)

    return DownloadIntentResponse(
        asset_id=intent.asset_id,
        asset_version_id=intent.asset_version_id,
        download_url=intent.download_url,
        expires_at=intent.expires_at,
        media_type=intent.media_type,
        size_bytes=intent.size_bytes,
        file_name=intent.file_name,
    )
