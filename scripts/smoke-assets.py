"""Explicit smoke check for asset signed uploads, media validation, and abandoned cleanup.

Requires an explicit --env-file containing local stack credentials.
Provisions unique disposable PostgreSQL database and MinIO bucket,
exercises end-to-end HTTP/storage/database flows, and cleanly tears down resources.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from dotenv import dotenv_values
from psycopg import sql
from pydantic import SecretStr
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.engine import URL

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.settings import Settings  # noqa: E402

from app.infrastructure.persistence.configurations import AssetVersionModel  # noqa: E402
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage  # noqa: E402
from app.main import create_app  # noqa: E402


def expect(response: httpx.Response, expected_status: int) -> dict[str, Any]:
    assert response.status_code == expected_status, (
        f"Expected HTTP status {expected_status}, got {response.status_code}"
    )
    return cast(dict[str, Any], response.json())


def pdf_bytes(width: int = 100) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def synthesize_mp4(target: Path, duration_seconds: int = 1) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_seconds}:size=320x240:rate=10",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(target),
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if res.returncode != 0 or not target.exists():
        raise RuntimeError("Failed to synthesize minimal MP4 test video with ffmpeg")


def synthesize_wav(target: Path, duration_seconds: int = 1) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=1000:duration={duration_seconds}",
        str(target),
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if res.returncode != 0 or not target.exists():
        raise RuntimeError("Failed to synthesize minimal WAV test audio with ffmpeg")


async def exercise(settings: Settings) -> None:
    app = create_app(settings)
    storage_inspector = S3ObjectStorage(settings)
    marker = uuid4().hex[:8]

    async def object_exists(storage_key: str) -> bool:
        try:
            await asyncio.to_thread(
                storage_inspector.internal_client.head_object,
                Bucket=settings.object_storage_bucket,
                Key=storage_key,
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    class SyntheticTokenVerifier:
        def verify(self, token: str) -> dict[str, str]:
            return {
                "iss": "https://synthetic.example",
                "sub": f"{marker}-{token}",
                "email": f"{token}@example.com",
            }

    app.state.token_verifier = SyntheticTokenVerifier()

    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer member"},
            ) as api,
            httpx.AsyncClient() as http,
        ):
            # 1. Identity, team, project
            expect(await api.get("/api/v1/me"), 200)
            team = expect(
                await api.post(
                    "/api/v1/teams",
                    json={"name": f"Smoke Team {marker}"},
                    headers={"Idempotency-Key": f"team-{marker}"},
                ),
                201,
            )
            project = expect(
                await api.post(
                    f"/api/v1/teams/{team['id']}/projects",
                    json={"name": "Smoke Project"},
                ),
                201,
            )
            root = f"/api/v1/projects/{project['id']}/assets"

            # 2. Concurrent upload intent creation
            doc_data = pdf_bytes(100)
            doc_payload = {
                "kind": "supporting_document",
                "file_name": "test.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": len(doc_data),
            }
            intent_responses = await asyncio.gather(
                *(
                    api.post(
                        f"{root}/upload-intents",
                        json=doc_payload,
                        headers={"Idempotency-Key": f"key-same-{marker}"},
                    )
                    for _ in range(2)
                )
            )
            intents = [expect(r, 201) for r in intent_responses]
            intent = intents[0]
            assert intent["asset_id"] == intents[1]["asset_id"]
            assert intent["asset_version_id"] == intents[1]["asset_version_id"]

            # 3. SigV4 PUT fail-closed verification
            # 3a. Wrong MIME type must be rejected by storage signature
            bad_mime_res = await http.put(
                intent["upload_url"],
                headers={**intent["required_headers"], "content-type": "text/plain"},
                content=doc_data,
            )
            assert bad_mime_res.status_code == 403

            # 3b. Size mismatch must be rejected
            bad_size_res = await http.put(
                intent["upload_url"],
                headers={**intent["required_headers"], "content-length": str(len(doc_data) + 1)},
                content=doc_data + b"\x00",
            )
            assert bad_size_res.status_code in (400, 403)

            # 3c. Tampered key in URL must be rejected
            tampered_url = intent["upload_url"].replace(".pdf?", "-tampered.pdf?")
            bad_key_res = await http.put(
                tampered_url,
                headers=intent["required_headers"],
                content=doc_data,
            )
            assert bad_key_res.status_code == 403

            # 3d. Wrong HTTP method to signed PUT URL must be rejected
            bad_method_res = await http.get(intent["upload_url"])
            assert 400 <= bad_method_res.status_code < 500

            # 3e. Valid PUT succeeds
            valid_put = await http.put(
                intent["upload_url"],
                headers=intent["required_headers"],
                content=doc_data,
            )
            assert valid_put.status_code == 200

            # 3f. Overwrite denial via if-none-match: *
            overwrite_res = await http.put(
                intent["upload_url"],
                headers=intent["required_headers"],
                content=doc_data,
            )
            assert overwrite_res.status_code in (412, 403)

            # 4. Concurrent completion
            doc_checksum = f"sha256:{hashlib.sha256(doc_data).hexdigest()}"
            asset_path = f"/api/v1/assets/{intent['asset_id']}"
            completion_path = f"{asset_path}/versions/{intent['asset_version_id']}/complete"
            comp_responses = await asyncio.gather(
                *(
                    api.post(
                        completion_path,
                        json={"checksum": doc_checksum, "size_bytes": len(doc_data)},
                        headers={"Idempotency-Key": f"comp-{i}-{marker}"},
                    )
                    for i in range(2)
                )
            )
            for response in comp_responses:
                assert expect(response, 202)["state"] == "verified"

            # 5. Download and outsider access denial
            expect(await api.get(asset_path, headers={"Authorization": "Bearer outsider"}), 404)
            download = expect(await api.post(f"{asset_path}/download-intents"), 200)
            dl_res = await http.get(download["download_url"])
            assert dl_res.content == doc_data

            # 6. Immutable replacement versions & monotonic current pointer
            allocated: list[tuple[dict[str, Any], bytes]] = []
            for index in (2, 3):
                rev_body = pdf_bytes(200 + index)
                rev_payload = {
                    "file_name": f"revision-{index}.pdf",
                    "declared_media_type": "application/pdf",
                    "declared_size_bytes": len(rev_body),
                }
                rev_intent = expect(
                    await api.post(
                        f"{asset_path}/versions/upload-intents",
                        json=rev_payload,
                        headers={"Idempotency-Key": f"rev-{index}-{marker}"},
                    ),
                    201,
                )
                put_res = await http.put(
                    rev_intent["upload_url"],
                    headers=rev_intent["required_headers"],
                    content=rev_body,
                )
                assert put_res.status_code == 200
                allocated.append((rev_intent, rev_body))

            # Complete revisions in reverse order (v3 then v2) to verify monotonic current pointer
            for rev_intent, rev_body in reversed(allocated):
                v_endpoint = f"{asset_path}/versions/{rev_intent['asset_version_id']}/complete"
                v_checksum = f"sha256:{hashlib.sha256(rev_body).hexdigest()}"
                v_comp = expect(
                    await api.post(
                        v_endpoint,
                        json={"checksum": v_checksum, "size_bytes": len(rev_body)},
                        headers={"Idempotency-Key": f"vcomp-{marker}"},
                    ),
                    202,
                )
                assert v_comp["version_id"] == rev_intent["asset_version_id"]

            current_doc = expect(await api.get(asset_path), 200)
            assert current_doc["version_id"] == allocated[-1][0]["asset_version_id"]

            # Older version download remains accessible
            orig_v_path = f"{asset_path}/versions/{intent['asset_version_id']}"
            orig_dl = expect(await api.post(f"{orig_v_path}/download-intents"), 200)
            assert (await http.get(orig_dl["download_url"])).content == doc_data

            # 7. Actual synthetic media: MP4 video and WAV audio
            with tempfile.TemporaryDirectory() as tmp_dir:
                mp4_file = Path(tmp_dir) / "pres.mp4"
                wav_file = Path(tmp_dir) / "ans.wav"
                synthesize_mp4(mp4_file, duration_seconds=1)
                synthesize_wav(wav_file, duration_seconds=1)
                mp4_bytes = mp4_file.read_bytes()
                wav_bytes = wav_file.read_bytes()

            # 7a. Video upload and completion
            video_intent = expect(
                await api.post(
                    f"{root}/upload-intents",
                    json={
                        "kind": "presentation_video",
                        "file_name": "pres.mp4",
                        "declared_media_type": "video/mp4",
                        "declared_size_bytes": len(mp4_bytes),
                    },
                    headers={"Idempotency-Key": f"vid-intent-{marker}"},
                ),
                201,
            )
            v_put = await http.put(
                video_intent["upload_url"],
                headers=video_intent["required_headers"],
                content=mp4_bytes,
            )
            assert v_put.status_code == 200

            vid_checksum = f"sha256:{hashlib.sha256(mp4_bytes).hexdigest()}"
            vid_comp = expect(
                await api.post(
                    f"/api/v1/assets/{video_intent['asset_id']}/versions/{video_intent['asset_version_id']}/complete",
                    json={"checksum": vid_checksum, "size_bytes": len(mp4_bytes)},
                    headers={"Idempotency-Key": f"vid-comp-{marker}"},
                ),
                202,
            )
            assert vid_comp["state"] == "verified"
            assert vid_comp["duration_ms"] is not None and vid_comp["duration_ms"] > 0
            assert vid_comp["retention_expires_at"] is not None

            # 7b. Audio upload and completion
            audio_intent = expect(
                await api.post(
                    f"{root}/upload-intents",
                    json={
                        "kind": "answer_audio",
                        "file_name": "ans.wav",
                        "declared_media_type": "audio/wav",
                        "declared_size_bytes": len(wav_bytes),
                    },
                    headers={"Idempotency-Key": f"aud-intent-{marker}"},
                ),
                201,
            )
            a_put = await http.put(
                audio_intent["upload_url"],
                headers=audio_intent["required_headers"],
                content=wav_bytes,
            )
            assert a_put.status_code == 200

            aud_checksum = f"sha256:{hashlib.sha256(wav_bytes).hexdigest()}"
            aud_comp = expect(
                await api.post(
                    f"/api/v1/assets/{audio_intent['asset_id']}/versions/{audio_intent['asset_version_id']}/complete",
                    json={"checksum": aud_checksum, "size_bytes": len(wav_bytes)},
                    headers={"Idempotency-Key": f"aud-comp-{marker}"},
                ),
                202,
            )
            assert aud_comp["state"] == "verified"
            assert aud_comp["duration_ms"] is not None and aud_comp["duration_ms"] > 0
            assert aud_comp["retention_expires_at"] is not None

            # 8. Abandoned upload cleanup vs completion
            abandoned_intent = expect(
                await api.post(
                    f"{root}/upload-intents",
                    json={
                        "kind": "supporting_document",
                        "file_name": "abandoned.pdf",
                        "declared_media_type": "application/pdf",
                        "declared_size_bytes": len(doc_data),
                    },
                    headers={"Idempotency-Key": f"abandoned-{marker}"},
                ),
                201,
            )
            # Put object so storage has an item to delete
            abandoned_put = await http.put(
                abandoned_intent["upload_url"],
                headers=abandoned_intent["required_headers"],
                content=doc_data,
            )
            assert abandoned_put.status_code == 200

            # Backdate timestamps to simulate passed retention cutoff and upload deadline
            async with app.state.session_factory() as session:
                ver_stmt = select(AssetVersionModel).where(
                    AssetVersionModel.id == UUID(abandoned_intent["asset_version_id"])
                )
                ver_row = await session.scalar(ver_stmt)
                if ver_row is not None:
                    ver_row.created_at = datetime.now(UTC) - timedelta(days=2)
                    ver_row.upload_expires_at = datetime.now(UTC) - timedelta(days=1)
                    await session.commit()

                    abandoned_storage_key = ver_row.storage_key
                else:
                    raise AssertionError("Abandoned version was not persisted")

            async def run_cleanup() -> int:
                async with app.state.session_factory() as cleanup_session:
                    store = app.state.asset_store_factory(cleanup_session)
                    return cast(
                        int,
                        await store.cleanup_abandoned_uploads(
                            retention_seconds=86400,
                            lease_seconds=300,
                        ),
                    )

            cleaned, raced_completion = await asyncio.gather(
                run_cleanup(),
                api.post(
                    f"/api/v1/assets/{abandoned_intent['asset_id']}/versions/{abandoned_intent['asset_version_id']}/complete",
                    json={"checksum": doc_checksum, "size_bytes": len(doc_data)},
                    headers={"Idempotency-Key": f"race-comp-{marker}"},
                ),
            )
            if raced_completion.status_code == 202:
                assert cleaned == 0
                assert await object_exists(abandoned_storage_key)
            else:
                assert raced_completion.status_code in (404, 409)
                assert cleaned == 1
                assert not await object_exists(abandoned_storage_key)

            # 9. A late PUT after deletion is removed by the delayed tombstone sweep.
            tombstone_intent = expect(
                await api.post(
                    f"{root}/upload-intents",
                    json={
                        "kind": "supporting_document",
                        "file_name": "tombstone.pdf",
                        "declared_media_type": "application/pdf",
                        "declared_size_bytes": len(doc_data),
                    },
                    headers={"Idempotency-Key": f"tombstone-{marker}"},
                ),
                201,
            )
            tombstone_put = await http.put(
                tombstone_intent["upload_url"],
                headers=tombstone_intent["required_headers"],
                content=doc_data,
            )
            assert tombstone_put.status_code == 200

            async with app.state.session_factory() as session:
                tombstone_stmt = select(AssetVersionModel).where(
                    AssetVersionModel.id == UUID(tombstone_intent["asset_version_id"])
                )
                tombstone_row = await session.scalar(tombstone_stmt)
                if tombstone_row is None:
                    raise AssertionError("Tombstone version was not persisted")
                tombstone_row.created_at = datetime.now(UTC) - timedelta(days=2)
                tombstone_row.upload_expires_at = datetime.now(UTC) - timedelta(days=1)
                tombstone_storage_key = tombstone_row.storage_key
                await session.commit()

            assert await run_cleanup() >= 1
            assert not await object_exists(tombstone_storage_key)

            late_put = await http.put(
                tombstone_intent["upload_url"],
                headers=tombstone_intent["required_headers"],
                content=doc_data,
            )
            assert late_put.status_code == 200
            assert await object_exists(tombstone_storage_key)

            async with app.state.session_factory() as session:
                tombstone_row = await session.scalar(tombstone_stmt)
                assert tombstone_row is not None
                tombstone_row.cleanup_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()

            assert await run_cleanup() >= 1
            assert not await object_exists(tombstone_storage_key)

            print(
                "[PASS] Real HTTP/PG/MinIO: concurrent create/complete, SigV4 fail-closed, "
                "media validation, and abandoned cleanup"
            )
    finally:
        storage_inspector.internal_client.close()
        bind = app.state.session_factory.kw["bind"]
        await bind.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit smoke test for asset uploads and abandoned cleanup."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Path to environment file with credentials (e.g. .env.local)",
    )
    args = parser.parse_args()

    env_path: Path = args.env_file
    if not env_path.is_file():
        sys.exit(f"Error: Environment file not found at {env_path}")

    config = dotenv_values(env_path)
    required_keys = [
        "POSTGRES_PASSWORD",
        "BACKEND_DB_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ]
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        sys.exit(f"Error: Missing required keys in {env_path}: {', '.join(missing)}")

    name = f"vj_smoke_{uuid4().hex[:10]}"
    bucket = name.replace("_", "-")

    # 1. Provision isolated PostgreSQL database
    with psycopg.connect(
        host="127.0.0.1",
        port=5432,
        user="postgres",
        password=config["POSTGRES_PASSWORD"],
        dbname="virtujudge",
        autocommit=True,
    ) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER virtujudge_backend").format(sql.Identifier(name))
        )

    try:
        # 2. Create backend schema in the disposable database
        with psycopg.connect(
            host="127.0.0.1",
            port=5432,
            user="virtujudge_backend",
            password=config["BACKEND_DB_PASSWORD"],
            dbname=name,
            autocommit=True,
        ) as connection:
            connection.execute("CREATE SCHEMA backend AUTHORIZATION virtujudge_backend")

        db_url = URL.create(
            "postgresql+asyncpg",
            username="virtujudge_backend",
            password=config["BACKEND_DB_PASSWORD"],
            host="127.0.0.1",
            port=5432,
            database=name,
        )

        settings = Settings(
            _env_file=None,
            app_env="test",
            database_url=db_url.render_as_string(hide_password=False),
            object_storage_endpoint="http://127.0.0.1:9000",
            object_storage_bucket=bucket,
            object_storage_access_key=SecretStr(config["MINIO_ROOT_USER"] or ""),
            object_storage_secret_key=SecretStr(config["MINIO_ROOT_PASSWORD"] or ""),
            asset_cleanup_enabled=False,
        )

        # 3. Apply migrations to disposable database
        migration = Config("alembic.ini")
        migration.set_main_option(
            "sqlalchemy.url",
            db_url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        command.upgrade(migration, "head")

        # 4. Provision isolated MinIO bucket
        storage = S3ObjectStorage(settings)
        storage.internal_client.create_bucket(Bucket=bucket)

        try:
            asyncio.run(exercise(settings))
        finally:
            # Drop all objects and bucket in disposable storage
            contents = storage.internal_client.list_objects_v2(Bucket=bucket).get("Contents", [])
            for obj in contents:
                storage.internal_client.delete_object(Bucket=bucket, Key=obj["Key"])
            storage.internal_client.delete_bucket(Bucket=bucket)

        # 5. Full migration rollback and re-apply on disposable database only
        command.downgrade(migration, "base")
        command.upgrade(migration, "head")
        print("[PASS] Real PostgreSQL migration downgrade and upgrade on disposable database")

    finally:
        # 6. Drop disposable PostgreSQL database
        with psycopg.connect(
            host="127.0.0.1",
            port=5432,
            user="postgres",
            password=config["POSTGRES_PASSWORD"],
            dbname="virtujudge",
            autocommit=True,
        ) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
