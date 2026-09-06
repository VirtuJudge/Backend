import asyncio
import hashlib
import os
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import botocore.exceptions  # type: ignore[import-untyped]
from botocore.client import BaseClient  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from app.application.interfaces.objectStorage import ObjectStoragePort
from app.domain.asset import (
    AssetSizeLimitExceeded,
    StorageObjectNotFound,
    StorageUnavailable,
)
from app.infrastructure.settings import Settings

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")


class S3ObjectStorage(ObjectStoragePort):
    def __init__(self, settings: Settings):
        self.bucket = settings.object_storage_bucket
        self.endpoint_url = settings.object_storage_endpoint
        self.public_endpoint_url = (
            settings.object_storage_public_endpoint or settings.object_storage_endpoint
        )
        self.region = settings.object_storage_region
        self.access_key = (
            settings.object_storage_access_key.get_secret_value()
            if settings.object_storage_access_key
            else None
        )
        self.secret_key = (
            settings.object_storage_secret_key.get_secret_value()
            if settings.object_storage_secret_key
            else None
        )
        self._internal_client: BaseClient | None = None
        self._public_client: BaseClient | None = None

    def _get_client(self, endpoint: str) -> BaseClient:
        if not self.access_key or not self.secret_key:
            raise StorageUnavailable("Object storage credentials are not configured")
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3.0,
            read_timeout=10.0,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=config,
        )

    @property
    def internal_client(self) -> BaseClient:
        if self._internal_client is None:
            self._internal_client = self._get_client(self.endpoint_url)
        return self._internal_client

    @property
    def public_client(self) -> BaseClient:
        if self._public_client is None:
            self._public_client = self._get_client(self.public_endpoint_url)
        return self._public_client

    def generate_upload_url(
        self,
        storage_key: str,
        content_type: str,
        content_length: int,
        ttl_seconds: int,
    ) -> tuple[str, dict[str, str], datetime]:
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        params = {
            "Bucket": self.bucket,
            "Key": storage_key,
            "ContentType": content_type,
            "ContentLength": content_length,
            "IfNoneMatch": "*",
        }
        try:
            url: str = self.public_client.generate_presigned_url(
                ClientMethod="put_object",
                Params=params,
                ExpiresIn=ttl_seconds,
            )
        except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as err:
            raise StorageUnavailable("Failed to generate upload URL") from err

        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        signed_headers_param = query.get("X-Amz-SignedHeaders", [""])[0]
        signed_headers = [h.strip().lower() for h in signed_headers_param.split(";")]

        required_headers = {
            "content-type": content_type,
            "if-none-match": "*",
        }

        if not (
            "content-type" in signed_headers
            and "content-length" in signed_headers
            and "if-none-match" in signed_headers
        ):
            raise StorageUnavailable("S3 SigV4 signed PUT missing required signed headers")

        return url, required_headers, expires_at

    def generate_download_url(
        self,
        storage_key: str,
        ttl_seconds: int,
    ) -> tuple[str, datetime]:
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        params = {
            "Bucket": self.bucket,
            "Key": storage_key,
        }
        try:
            url: str = self.public_client.generate_presigned_url(
                ClientMethod="get_object",
                Params=params,
                ExpiresIn=ttl_seconds,
            )
        except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as err:
            raise StorageUnavailable("Failed to generate download URL") from err
        return url, expires_at

    async def stream_to_disk(
        self,
        storage_key: str,
        target_path: Path,
        max_bytes: int,
    ) -> tuple[int, str, str]:
        def _read_sync() -> tuple[int, str, str]:
            try:
                response: dict[str, Any] = self.internal_client.get_object(
                    Bucket=self.bucket, Key=storage_key
                )
            except botocore.exceptions.ClientError as err:
                code = err.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    raise StorageObjectNotFound("Object not found in storage") from err
                raise StorageUnavailable("Storage service unavailable") from err
            except (botocore.exceptions.BotoCoreError, OSError) as err:
                raise StorageUnavailable("Failed to connect to storage service") from err

            observed_content_type = response.get("ContentType", "")
            body = response.get("Body")
            hasher = hashlib.sha256()
            total_bytes = 0

            try:
                with target_path.open("wb") as f:
                    while True:
                        try:
                            chunk = body.read(64 * 1024) if body is not None else b""
                        except (botocore.exceptions.BotoCoreError, OSError) as read_err:
                            raise StorageUnavailable(
                                "Storage read error during stream"
                            ) from read_err
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise AssetSizeLimitExceeded(
                                "Object size exceeds maximum allowed bytes"
                            )
                        hasher.update(chunk)
                        f.write(chunk)
            finally:
                if body is not None and hasattr(body, "close"):
                    import contextlib

                    with contextlib.suppress(Exception):
                        body.close()

            return total_bytes, hasher.hexdigest(), observed_content_type

        return await asyncio.to_thread(_read_sync)
