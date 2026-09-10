import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.application.ports.media_verifier import MediaVerifierPort
from app.domain.asset import AssetCorrupt
from app.domain.user import User
from tests.support import (
    PROJECT_ID,
    FakeAssetRepository,
    FakeObjectStorage,
    create_test_client,
)


class MediaStorage(FakeObjectStorage):
    def __init__(self, media_type: str) -> None:
        super().__init__()
        self.media_type = media_type

    async def stream_to_disk(
        self, storage_key: str, target_path: Path, max_bytes: int
    ) -> tuple[int, str, str]:
        size, checksum, _ = await super().stream_to_disk(storage_key, target_path, max_bytes)
        return size, checksum, self.media_type


class MeasuredMedia(MediaVerifierPort):
    def __init__(self, reject: bool) -> None:
        self.reject = reject

    async def verify_media(self, file_path: Path, media_type: str, kind: str) -> int:
        assert file_path.read_bytes() == b"synthetic-media"
        if self.reject:
            raise AssetCorrupt("overlong_media")
        return 1000


@pytest.mark.anyio
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize(
    ("kind", "name", "mime", "limit"),
    [
        ("presentation_video", "pitch.mp4", "video/mp4", 500),
        ("presentation_video", "pitch.webm", "video/webm", 500),
        ("answer_audio", "answer.webm", "audio/webm", 25),
        ("answer_audio", "answer.ogg", "audio/ogg", 25),
        ("answer_audio", "answer.m4a", "audio/mp4", 25),
        ("answer_audio", "answer.wav", "audio/wav", 25),
    ],
)
async def test_media_upload_workflow(
    member_user: User, kind: str, name: str, mime: str, limit: int, reject: bool
) -> None:
    repo, storage = FakeAssetRepository(), MediaStorage(mime)
    payload = {
        "kind": kind,
        "file_name": name,
        "declared_media_type": mime,
        "declared_size_bytes": len(b"synthetic-media"),
    }
    async with create_test_client(member_user, repo, storage, MeasuredMedia(reject)) as client:
        root = f"/api/v1/projects/{PROJECT_ID}/assets/upload-intents"
        too_large = await client.post(
            root,
            json={**payload, "declared_size_bytes": limit * 1024**2 + 1},
            headers={"Idempotency-Key": "large"},
        )
        assert too_large.status_code == 413
        response = await client.post(root, json=payload, headers={"Idempotency-Key": "media"})
        assert response.status_code == 201
        intent = response.json()
        assert intent["required_headers"]["content-type"] == mime
        assert intent["maximum_size_bytes"] == limit * 1024**2
        version = repo.versions[UUID(intent["asset_version_id"])]
        storage.objects[version.storage_key] = b"synthetic-media"
        asset_path = f"/api/v1/assets/{intent['asset_id']}"
        completion = await client.post(
            asset_path + f"/versions/{version.id}/complete",
            json={
                "size_bytes": len(b"synthetic-media"),
                "checksum": "sha256:" + hashlib.sha256(b"synthetic-media").hexdigest(),
            },
            headers={"Idempotency-Key": "complete"},
        )
        assert completion.status_code == (422 if reject else 202)
        downloaded = await client.post(asset_path + "/download-intents")
        assert downloaded.status_code == (409 if reject else 200)
        if not reject:
            assert completion.json()["duration_ms"] == version.duration_ms == 1000
            asset = repo.assets[UUID(intent["asset_id"])]
            assert asset.retention_expires_at == asset.created_at + timedelta(days=30)
