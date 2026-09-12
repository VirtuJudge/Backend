"""Verify the configured bucket using the same presigned flow as the API.

The check writes one uniquely named object and always attempts to delete it.
It never creates or deletes a bucket and never prints credentials or signed URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.infrastructure.storage.s3_object_storage import S3ObjectStorage  # noqa: E402
from app.settings import Settings  # noqa: E402


async def exercise(settings: Settings) -> None:
    storage = S3ObjectStorage(settings)
    storage_key = f"deployment-smoke/{uuid4().hex}.txt"
    payload = b"VirtuJudge R2 deployment smoke check\n"

    try:
        upload_url, required_headers, _ = storage.generate_upload_url(
            storage_key=storage_key,
            content_type="text/plain",
            content_length=len(payload),
            ttl_seconds=60,
        )
        async with httpx.AsyncClient(timeout=20) as client:
            upload = await client.put(upload_url, headers=required_headers, content=payload)
            if upload.status_code not in {200, 201}:
                raise RuntimeError(f"R2 presigned upload failed with HTTP {upload.status_code}")

            overwrite = await client.put(upload_url, headers=required_headers, content=payload)
            if overwrite.status_code not in {403, 412}:
                raise RuntimeError(
                    "R2 did not enforce the signed If-None-Match overwrite protection; "
                    f"received HTTP {overwrite.status_code}"
                )

            download_url, _ = storage.generate_download_url(storage_key, ttl_seconds=60)
            download = await client.get(download_url)
            if download.status_code != 200:
                raise RuntimeError(f"R2 presigned download failed with HTTP {download.status_code}")
            if download.content != payload:
                raise RuntimeError("R2 returned content that differs from the uploaded object")
    finally:
        await storage.delete_object(storage_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
        help="Ignored env file containing the R2 OBJECT_STORAGE_* values",
    )
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    asyncio.run(exercise(settings))
    print("R2 presigned upload, overwrite protection, download, and cleanup passed.")


if __name__ == "__main__":
    main()
