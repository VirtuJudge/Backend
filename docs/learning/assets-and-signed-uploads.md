# Assets and Signed Uploads: Architecture, Verification, and Concurrency

## Learning Mission

This guide is the reference for understanding, maintaining, and extending the asset and signed upload subsystem introduced in Backend issue #5 (BE-03). The subsystem coordinates private uploads for presentation documents, video recordings, and interview answer audio.

By working through this guide and its retrieval exercises, you will understand:
- How the API control plane delegates client uploads directly to object storage, while the verification subsystem streams stored content under strict bounds.
- The lifecycle differences between an Asset container and its immutable AssetVersion records.
- How AWS SigV4 presigned URLs enforce size, type, and collision guarantees at the storage boundary.
- How multi-tier verification guards the backend against malformed, oversized, or malicious uploads using isolated worker processes.
- How database transactions, row locks, savepoints, and scoped idempotency prevent race conditions during concurrent uploads.
- The operational requirements, migration strategies, and regression fixes supporting this implementation.

---

## Primary Sources

The concepts in this guide reflect established standards and our codebase implementation:

- **ECMA-376 / ISO/IEC 29500**: [ECMA-376 Office Open XML File Formats](https://www.ecma-international.org/publications-and-standards/standards/ecma-376/) defines Open Packaging Conventions (OPC) ZIP packaging and PresentationML structures.
- **AWS S3 Conditional Writes**: [Amazon S3 Conditional Writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) documents precondition headers like `If-None-Match: *` to prevent overwriting existing keys.
- **AWS S3 Presigned URLs**: [Using Presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html) documents time-limited bearer capabilities binding HTTP methods, headers, and query parameters.
- **Boto3 S3 Client Signing**: [Boto3 generate_presigned_url Reference](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_url.html) covers parameter binding for SigV4 presigning.
- **PyPDF Robustness**: [PyPDF Robustness Guide](https://pypdf.readthedocs.io/en/stable/user/robustness.html) covers strict-mode structural traversal and exception handling for valid PDF documents.
- **FFmpeg Diagnostics**: [FFprobe Documentation](https://ffmpeg.org/ffprobe.html) details container probing and stream inspection.
- **PostgreSQL Explicit Row Locking**: [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html) describes `SELECT ... FOR UPDATE` behavior, lock queues, and deadlock prevention through ordered locking.
- **SQLAlchemy Identity Map Refreshing**: [SQLAlchemy Query Guide on populate_existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing) explains why `populate_existing=True` is required when refreshing ORM attributes for rows already loaded in an active session.
- **Python Asyncio Subprocesses**: [Asyncio Subprocess Documentation](https://docs.python.org/3/library/asyncio-subprocess.html) covers process spawning, streaming output buffers, timeouts, signals, and process reaping.

---

## Glossary of Terms

- **Asset**: Logical file entity tracking project ownership, overall state, and the active version pointer.
- **AssetVersion**: Immutable snapshot of an upload, recording storage key, declared and verified metadata, checksum, and completion time.
- **UploadIntent**: Short-lived ticket with a presigned PUT URL and required headers for direct storage upload.
- **DownloadIntent**: Short-lived ticket with a presigned GET URL for an authorized verified object.
- **Storage Key**: Private bucket path (`teams/{team_id}/projects/{project_id}/assets/{asset_id}/{version_id}.{ext}`).
- **Presigned URL**: Temporary S3 SigV4 URL granting restricted access without sharing cloud credentials.
- **Bearer Capability**: Authorization granted purely by possessing the token. Presigned URLs are never persisted in databases.
- **SigV4 Signed Headers (`X-Amz-SignedHeaders`)**: Request headers cryptographically bound to the signature. Storage rejects any mismatch.
- **Conditional Write (`If-None-Match: *`)**: HTTP precondition requiring the upload target key to not exist before writing.
- **Idempotency Fingerprint**: Deterministic SHA-256 hash of canonicalized request parameters, separating retries from conflicting parameter reuse.
- **Savepoint (`session.begin_nested()`)**: Subtransaction boundary rolling back only nested inserts during unique constraint races.
- **Row-Level Lock (`FOR UPDATE`)**: Database lock preventing concurrent transactions from mutating or locking matching rows until commit.
- **Populate Existing (`populate_existing=True`)**: ORM option forcing SQLAlchemy to refresh loaded identity map attributes with fresh locked row data.
- **404 Concealment**: Security policy returning HTTP 404 instead of 403 to prevent outsiders from enumerating private resource existence.
- **Demuxer vs. Codec**: A demuxer unpacks containers (MP4, WebM); a codec compresses or decompresses raw audio or video streams within.
- **Fail-Closed**: Operational policy returning retryable HTTP 503 on ambiguous errors rather than accepting corrupt content or rejecting valid files.
- **Cleanup Lease**: A retry deadline stored while a version is `deleting`, allowing another cleanup pass to recover work after a worker or storage failure.
- **Tombstone Sweep**: A delayed second delete for an already deleted version, used to catch an upload request that reached storage after cleanup's first delete.

---

## 1. Architectural Overview and Layered Boundaries

VirtuJudge organizes backend code into four inward-facing layers under `app/`:

```
API (app/api/routes/asset.py, app/api/schemas/asset.py)
  -> Application (app/application/services/assetStore.py, ports)
    -> Infrastructure (repositories, s3ObjectStorage.py, verifiers)
      -> Domain (app/domain/asset.py, app/domain/idempotency.py)
```

### Layer Responsibilities

1. **Domain Layer** ([`app/domain/asset.py`](../../app/domain/asset.py), [`app/domain/idempotency.py`](../../app/domain/idempotency.py)): Plain models (`Asset`, `AssetVersion`, `UploadIntent`, `DownloadIntent`, `AssetUploadIdempotency`) and domain errors (`AssetNotFound`, `AssetCorrupt`, `AssetIdempotencyConflict`, `StorageUnavailable`).
2. **Application Layer** ([`app/application/services/assetStore.py`](../../app/application/services/assetStore.py)): `AssetStore` boundary coordinating authorization, storage keys, presigning, fingerprint checks, streaming verification, and version progression across ports ([`AssetRepository`](../../app/application/interfaces/assetRepository.py), [`ObjectStoragePort`](../../app/application/interfaces/objectStorage.py), [`DocumentVerifierPort`](../../app/application/interfaces/documentVerifier.py), [`MediaVerifierPort`](../../app/application/interfaces/mediaVerifier.py)).
3. **API Layer** ([`app/api/routes/asset.py`](../../app/api/routes/asset.py), [`app/api/schemas/asset.py`](../../app/api/schemas/asset.py)): FastAPI routes validating `Idempotency-Key` headers, deserializing requests, invoking `AssetStore`, and formatting RFC 7807 problem details.
4. **Infrastructure Layer**: SQLAlchemy repositories ([`SqlAlchemyAssetRepository`](../../app/infrastructure/repositories/sqlalchemyAssetRepository.py)), S3/MinIO client ([`S3ObjectStorage`](../../app/infrastructure/storage/s3ObjectStorage.py)), document parsing workers ([`document_verifier.py`](../../app/infrastructure/documents/document_verifier.py), [`document_worker.py`](../../app/infrastructure/documents/document_worker.py)), and media verification ([`ffmpegVerifier.py`](../../app/infrastructure/media/ffmpegVerifier.py), [`worker_launcher.py`](../../app/infrastructure/media/worker_launcher.py)).

### Team Ancestry and 404 Concealment

All asset operations enforce multi-tenant isolation through team membership ancestry:

```
Team Membership (User + Team) -> Project (Team ID) -> Asset (Project ID) -> AssetVersion (Asset ID)
```

In [`AssetStore._authorize_project`](../../app/application/services/assetStore.py):
```python
async def _authorize_project(self, project_id: UUID, user_id: UUID) -> tuple[UUID, UUID]:
    project = await self.repository.get_project(project_id)
    if project is None or not await self.repository.is_team_member(project.team_id, user_id):
        raise AssetNotFound
    if await self.repository.is_project_erasure_requested(project_id):
        raise AssetNotFound
    return project.id, project.team_id
```

When a user is not an active team member, or when the project has entered the erasure pipeline, the system raises `AssetNotFound` instead of `Forbidden`. Returning `404 Not Found` prevents outsiders from probing whether private project IDs or team assets exist.

### Asset vs. Immutable Version Lifecycle

An `Asset` represents the ongoing business entity, while an `AssetVersion` represents an immutable snapshot of content:
- **Initial Version Pointer**: When an asset is created, `Asset.current_version_id` initially points to the initial pending version.
- **Immutable Bytes and Declared Metadata**: Once inserted, `storage_key`, `declared_size_bytes`, `declared_media_type`, and `version_number` are fixed. Stored object bytes cannot be modified in place.
- **Lifecycle Transitions**: While stored bytes are immutable, version records transition from `pending_upload` to `verified` or `rejected`, recording verified metadata (`size_bytes`, `checksum`, `duration_ms`).
- **Subsequent Version Replacement**: New document versions start in `pending_upload`. `Asset.current_version_id` stays pointed to the active verified version until the new version completes verification.
- **Contract Compatibility**: Contracts define `UploadIntentResponse` with `asset_version_id`, while `AssetResponse` exposes `version_id` (pointing to `asset.current_version_id`).
- **Frontend Mock Drift**: The temporary frontend mock used non-normative state names (`pending`, `uploading`, `failed`, `erased`). The live backend enforces normative states: `pending_upload`, `verified`, and `rejected`.

---

## 2. Storage Protocol and Presigned Upload Lifecycle

Clients upload binary payloads directly to object storage via presigned S3 URLs, bypassing FastAPI to avoid consuming web server memory and event loop threads. During completion, the backend streams a bounded GET from private storage to temporary disk for verification before accepting the version:

```
Client (Browser)             Backend API (Control Plane)           Object Storage (S3 / MinIO)
     |                                    |                                     |
     | 1. POST .../upload-intents         |                                     |
     |----------------------------------->|                                     |
     |                                    | 2. Insert pending asset/version,    |
     |                                    |    generate presigned PUT URL       |
     | 3. Returns UploadIntent            |                                     |
     |<-----------------------------------|                                     |
     |                                                                          |
     | 4. PUT direct to upload_url (Body: raw file bytes)                       |
     |    Headers: Content-Type, If-None-Match: *                               |
     |------------------------------------------------------------------------->|
     |                                                                          | 5. S3 checks SigV4
     | 6. HTTP 200 OK / 204 No Content                                          |
     |<-------------------------------------------------------------------------|
     |                                    |                                     |
     | 7. POST .../complete               |                                     |
     |----------------------------------->|                                     |
     |                                    | 8. Lock DB, stream from storage,    |
     |                                    |    run verification, advance asset  |
     | 9. HTTP 202 Accepted (Asset)       |                                     |
     |<-----------------------------------|                                     |
```

### Presigned URLs as Short-Lived Bearer Capabilities

A presigned URL is a bearer capability: whoever holds the URL can execute the signed action until expiration:
- URL TTL is bounded between 60 and 3,600 seconds (default 900 seconds in [`app/application/services/assetStore.py`](../../app/application/services/assetStore.py)).
- Presigned URLs are generated on demand and never stored in the database.
- Download URLs are only issued for assets in the `verified` state. Unverified or rejected versions cannot receive download intents.

### Signature Binding and Browser Header Restrictions

In [`S3ObjectStorage.generate_upload_url`](../../app/infrastructure/storage/s3ObjectStorage.py), the backend signs `Bucket`, `Key`, `ContentType`, `ContentLength`, and `IfNoneMatch: "*"` into the PUT URL. The adapter confirms that S3 SigV4 signed all required headers:
```python
signed_headers = [h.strip().lower() for h in query.get("X-Amz-SignedHeaders", [""])[0].split(";")]
if not {"content-type", "content-length", "if-none-match"}.issubset(signed_headers):
    raise StorageUnavailable("S3 SigV4 signed PUT missing required signed headers")
```

#### Why `Content-Length` is Omitted from `required_headers`

`UploadIntentResponse.required_headers` includes `content-type` and `if-none-match`, but omits `content-length`. The W3C `fetch` specification forbids JavaScript from setting `Content-Length` manually; browsers attach it automatically from the body `Blob`. S3 validates the browser-supplied header against the signed parameter, rejecting mismatches with HTTP 403.

---

## 3. File Verification Pipeline

Storage signature validation guarantees that byte length and declared MIME type match the upload intent. However, storage engines do not inspect file contents.

The backend executes a three-tier verification pipeline during `complete_upload`:
1. **Pre-upload signature verification**: S3 enforces size, MIME, and conditional overwrite rules.
2. **Storage streaming check**: Streamed from private storage to disk in 64 KiB chunks, enforcing kind-specific size limits, computing SHA-256 in real time, and validating S3 `ContentType`.
3. **Deep content verification**: Isolated child processes validate structural integrity and decode media frames before marking the version verified.

### Download-to-Temp Streaming and Storage Writer Cancellation

Verification begins with [`S3ObjectStorage.stream_to_disk`](../../app/infrastructure/storage/s3ObjectStorage.py). The backend fetches the uploaded object using its private internal endpoint (`object_storage_endpoint`) and streams it to a temporary file in 64 KiB increments, accumulating byte counts and updating a SHA-256 hash.

#### Storage Writer Cancellation

Reading the storage stream runs in a worker thread via `asyncio.to_thread(_read_sync)`. If the calling task is cancelled on client disconnect or timeout, cancelling without coordinating leaves the thread writing while the caller unlinks the file.

[`S3ObjectStorage.stream_to_disk`](../../app/infrastructure/storage/s3ObjectStorage.py) coordinates cancellation using a shared event and shielded task completion:
```python
cancelled = Event()


def _read_sync() -> tuple[int, str, str]:
    with target_path.open("wb") as f:
        while True:
            if cancelled.is_set():
                return 0, "", ""
            chunk = body.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)


transfer = asyncio.create_task(asyncio.to_thread(_read_sync))
try:
    return await asyncio.shield(transfer)
except asyncio.CancelledError:
    cancelled.set()
    while not transfer.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(transfer)
    with contextlib.suppress(Exception):
        transfer.result()
    raise
```
Setting `cancelled.set()` signals the worker thread to halt. Shielding `transfer` ensures the wrapper waits until the thread exits and closes file descriptors before reraising `CancelledError`, allowing cleanup to safely unlink the file.

### Document Verification: PDF and Strict PPTX

Inspection of documents occurs in an isolated child process spawned via [`asyncio.create_subprocess_exec`](../../app/infrastructure/documents/document_verifier.py) with POSIX limits (`RLIMIT_AS` 256 MiB, `RLIMIT_CPU` 5s).

In [`app/infrastructure/documents/document_worker.py`](../../app/infrastructure/documents/document_worker.py):

#### PDF Validation (`validate_pdf`)
Checks the first 1,024 bytes for `%PDF-`, parses with strict `pypdf.PdfReader`, rejects encrypted or zero-page documents, and traverses mediabox, page contents, and text extraction to trigger stream decompression.

#### PPTX Validation (`validate_pptx`)
Enforces strict structural and relationship checks according to ECMA-376 Open Packaging Conventions:
- **Magic Signature and Archive Bounds**: Validates initial bytes `b"PK"`. Caps archives at 1,000 entries, 50 MiB uncompressed size, and 15 MiB per XML part. Rejects ratios exceeding 100:1 and duplicate archive paths.
- **Path Traversal and Macro Rejection**: Blocks absolute paths, null bytes, drive letters, and `..` traversal in archive names and relationship targets via `is_unsafe_archive_path` and `resolve_rel_target`. Rejects VBA binaries (`vbaproject.bin`, `vba.bin`), macros, and macro MIME types.
- **Package Relationships**: Validates `[Content_Types].xml` and `_rels/.rels` via `defusedxml.ElementTree`. Enforces unique relationship IDs (`rId`) and verifies `officeDocument` targets `ppt/presentation.xml` with PresentationML content type.
- **Slide References and Hierarchy**: Parses `ppt/presentation.xml` `<p:sldIdLst>`. Resolves each slide in `ppt/_rels/presentation.xml.rels`, verifying target existence under `ppt/` and slide content type. Verifies `<p:sld>` contains `<p:cSld>` and `<p:spTree>`.
- **Slide Embed Relationships**: Parses `ppt/slides/_rels/<slide>.rels`. Confirms referenced relationship IDs (e.g. `<a:blip r:embed="..."/>` or hyperlinks) resolve. Internal targets must exist in the archive; external hyperlinks are permitted.

> [!NOTE]
> This worker performs structural and relationship integrity checks based on ECMA-376. It does not perform full XML schema validation or antivirus and malware scanning.

### Media Verification: Video and Audio

Media files (presentation video and answer audio) are analyzed by [`FFmpegMediaVerifier`](../../app/infrastructure/media/ffmpegVerifier.py), which is wired into `asset_store_factory` in [`app/main.py`](../../app/main.py). The verifier enforces limits defined in `ASSET_KIND_LIMITS`:

| Asset Kind | Declared Media Type | Permitted Containers | Permitted Video Codecs | Permitted Audio Codecs | Max Size | Max Duration |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `presentation_video` | `video/mp4` | `mov,mp4,m4a...`, `mp4` | H.264, HEVC, AV1, VP9 | AAC, MP3, Opus, FLAC | 500 MiB | 600 s |
| `presentation_video` | `video/webm` | `matroska,webm`, `webm` | VP8, VP9, AV1 | Opus, Vorbis | 500 MiB | 600 s |
| `answer_audio` | `audio/webm` | `matroska,webm`, `webm` | None | Opus, Vorbis | 25 MiB | 120 s |
| `answer_audio` | `audio/ogg` | `ogg` | None | Opus, Vorbis, FLAC | 25 MiB | 120 s |
| `answer_audio` | `audio/mp4` | `mov,mp4,m4a...`, `mp4` | None | AAC, MP3, Opus, FLAC, ALAC | 25 MiB | 120 s |
| `answer_audio` | `audio/wav` | `wav` | None | PCM variants | 25 MiB | 120 s |

#### Container vs. Codec Inspection

Container formats do not guarantee codec compatibility. The verifier checks both:
1. **Magic Signatures**: Validates container headers: WebM checks EBML doctype; MP4 checks `ftyp` compatible brands (rejecting `3gp`/`qt`); Ogg checks `OggS`; WAV checks `RIFF....WAVE`.
2. **Metadata Inspection via FFprobe**: Sandboxed `ffprobe` inspects stream counts, codec names, and stream types. Audio uploads with video streams or disallowed codecs are rejected.
3. **Full Decode Pass via FFmpeg**: Browser-recorded WebM often omits duration headers. A full decode pass verifies stream integrity:
   ```bash
   ffmpeg -v error -threads 1 -xerror -err_detect explode -protocol_whitelist file           -f <demuxer> -i <target_file> -map 0:v? -map 0:a? -f null - -progress pipe:1
   ```
   - `-xerror -err_detect explode`: Aborts on packet corruption.
   - `-progress pipe:1`: Streams decode progress (`out_time_us`), terminating if duration exceeds limits.
   - Compares decoded duration against container header duration with a 5% (or 2-second) tolerance.

#### Bounded Workers and Process Limits

The media verifier launches tools through [`worker_launcher.py`](../../app/infrastructure/media/worker_launcher.py) applying POSIX resource limits: `RLIMIT_AS` (1,024 MiB), `RLIMIT_CPU` (660s), `RLIMIT_FSIZE` (10 MiB), and a 900-second asyncio timeout. Those production defaults leave headroom above the 600-second presentation limit while still bounding expensive decodes; tests inject much shorter limits. Output pipes are truncated at 64 KiB.

#### Fail-Closed Error Handling vs. Permanent Rejection

- **Corrupt File**: If verification reveals bad CRC, unapproved codecs, overlong duration, or corrupt structures, `AssetCorrupt` is raised. The database records `version.state = "rejected"` and `version.rejection_reason = reason`. Future retries of this version are blocked.
- **Infrastructure Error**: If object storage is unreachable (`StorageUnavailable`), if a worker times out, or if system binaries are missing, the version remains in `pending_upload`. The API returns HTTP 503 Service Unavailable (`code: "service_unavailable"`), allowing client retries.

---

## 4. Concurrency, Idempotency, and Database Integrity

Handling asset uploads requires protecting against network retries, double submits, and race conditions across distributed clients.

### Idempotency Scopes and Canonical Fingerprints

Idempotency records are scoped to `(user_id, project_id, operation, key)` via a unique constraint in [`asset_upload_idempotency`](../../app/infrastructure/persistence/configurations/assetConfiguration.py).

In [`app/application/services/assetStore.py`](../../app/application/services/assetStore.py), the fingerprint canonicalizes parameters into sorted JSON:
```python
def compute_upload_fingerprint(kind: str, file_name: str, media_type: str, size_bytes: int) -> str:
    payload = {
        "file_name": file_name.strip(),
        "kind": kind,
        "media_type": media_type.strip().lower(),
        "size_bytes": size_bytes,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
```

When an incoming request reuses an `Idempotency-Key`:
- If `request_hash` matches and the version is still pending, the repository returns the existing IDs. The service signs another URL for only the whole seconds remaining before the original `upload_expires_at`; a retry never extends that absolute deadline.
- If the original deadline has passed, or the version is already `verified`, `rejected`, `deleting`, or `deleted`, the replay returns HTTP 409 instead of reviving the upload.
- If `request_hash` differs, the service raises `AssetIdempotencyConflict` (HTTP 409 `idempotency_conflict`).

For example, an intent created at 12:00 with a 15-minute TTL stores a 12:15 deadline. A matching replay at 12:10 may receive a URL valid for about five minutes. Replaying at 12:16 fails even if the caller still has the same idempotency key. Both initial and replay paths use the same `_build_upload_intent` helper, which prevents them from drifting apart.

### Database Savepoints (`begin_nested`)

When two requests with the same key arrive simultaneously, both may pass initial lookups and attempt inserts. In [`SqlAlchemyAssetRepository.save_asset_with_initial_version`](../../app/infrastructure/repositories/sqlalchemyAssetRepository.py):
```python
try:
    async with self.session.begin_nested():
        self.session.add_all([asset_model, version_model, idempotency_model])
        await self.session.flush()
except IntegrityError as exc:
    existing = await self.get_upload_idempotency(
        idempotency.user_id, idempotency.project_id, idempotency.operation, idempotency.key
    )
    if existing is not None:
        if existing.request_hash != idempotency.request_hash:
            raise AssetIdempotencyConflict(...) from exc
        return existing_asset, existing_version
    raise
```
`begin_nested()` creates a database savepoint. If the unique constraint fails, only the savepoint rolls back, leaving the enclosing session healthy to return the winning record.

### Row-Level Locking and Ordered Lock Acquisition

During `complete_upload`, [`SqlAlchemyAssetRepository.get_asset_and_version_for_completion`](../../app/infrastructure/repositories/sqlalchemyAssetRepository.py) applies row-level locks:
```python
asset_stmt = (
    select(AssetModel)
    .where(AssetModel.id == asset_id)
    .with_for_update()
    .execution_options(populate_existing=True)
)
asset_model = await self.session.scalar(asset_stmt)

ver_stmt = (
    select(AssetVersionModel)
    .where(AssetVersionModel.id == version_id)
    .with_for_update()
    .execution_options(populate_existing=True)
)
version_model = await self.session.scalar(ver_stmt)
```

1. **Deadlock Prevention**: Hierarchical locks acquire parent `AssetModel` before child `AssetVersionModel`, eliminating circular wait conditions.
2. **`populate_existing=True`**: Forces SQLAlchemy to refresh loaded identity map attributes with fresh locked row values.
3. **Database Engine Rules**: SQLite does not support row-level locks (`FOR UPDATE` translates to database locks); concurrency tests must run against PostgreSQL.

### Newer-Current Version Ordering and Completion Replay

When multiple versions are uploaded in parallel, version 1 might complete verification after version 2 has already verified.

In [`AssetStore.complete_upload`](../../app/application/services/assetStore.py):
```python
if (
    current_ver is None
    or current_ver.state != "verified"
    or version.version_number >= current_ver.version_number
):
    locked_asset.state = "verified"
    locked_asset.file_name = version.file_name
    locked_asset.size_bytes = observed_bytes
    locked_asset.checksum = formatted_checksum
    locked_asset.media_type = version.declared_media_type
    locked_asset.current_version_id = version.id
    locked_asset.duration_ms = verified_duration_ms
    if (
        locked_asset.kind in ("presentation_video", "answer_audio")
        and locked_asset.retention_expires_at is None
    ):
        locked_asset.retention_expires_at = (locked_asset.created_at or now) + timedelta(days=30)
```
If an older version finishes late, its record is marked `verified`, but parent `Asset.current_version_id` is not regressed.

Repeated calls to `complete_upload` on a `verified` version succeed idempotently if `checksum` and `size_bytes` match; otherwise, the service raises `AssetCompletionConflict` (HTTP 409).

### Retention Metadata on Assets

The `retention_expires_at` column is stored on the parent `Asset` table (via migration `c4d5e6f7a8b9_add_asset_retention_expires_at.py`).
- Populated as 30 days from creation (`now + timedelta(days=30)`) for `presentation_video` and `answer_audio`.
- Operates purely as tracking metadata; physical raw-media purging belongs to a separate future workflow.

---

## 5. Concrete End-to-End Walkthroughs

### Walkthrough 1: Valid PDF Upload and Replacement

#### Step 1: Initial Upload Intent (Document Version 1)

```http
POST /api/v1/projects/550e8400-e29b-41d4-a716-446655440000/assets/upload-intents HTTP/1.1
Idempotency-Key: client-req-001
Content-Type: application/json

{"kind": "supporting_document", "file_name": "architecture-diagram.pdf", "declared_media_type": "application/pdf", "declared_size_bytes": 1048576}
```

**Predicted Response (HTTP 201 Created)**:
```http
HTTP/1.1 201 Created
Content-Type: application/json

{
  "asset_id": "8a31c6a2-6f2b-4ec6-89d5-71bbd7a46c10",
  "asset_version_id": "9b42d7b3-7a3c-5fd7-90e6-82ccd8b57d21",
  "upload_url": "http://localhost:9000/virtujudge/.../9b42d7b3.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&...",
  "method": "PUT",
  "required_headers": {"content-type": "application/pdf", "if-none-match": "*"},
  "expires_at": "2026-09-07T12:45:00Z",
  "maximum_size_bytes": 26214400
}
```

#### Step 2: Binary Transfer to Storage

The client issues an HTTP PUT directly to `upload_url`:
```http
PUT /virtujudge/.../9b42d7b3.pdf?X-Amz-Algorithm=... HTTP/1.1
Content-Type: application/pdf
If-None-Match: *
Content-Length: 1048576

<binary pdf stream of exactly 1048576 bytes>
```
**Response from Object Storage**: `HTTP/1.1 200 OK` (or `204 No Content`).

#### Step 3: Complete Upload Intent

```http
POST /api/v1/assets/8a31c6a2-6f2b-4ec6-89d5-71bbd7a46c10/versions/9b42d7b3-7a3c-5fd7-90e6-82ccd8b57d21/complete HTTP/1.1
Idempotency-Key: client-req-002
Content-Type: application/json

{"checksum": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "size_bytes": 1048576}
```

**Predicted Response (HTTP 202 Accepted)**:
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "id": "8a31c6a2-6f2b-4ec6-89d5-71bbd7a46c10",
  "version_id": "9b42d7b3-7a3c-5fd7-90e6-82ccd8b57d21",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "kind": "supporting_document",
  "state": "verified",
  "file_name": "architecture-diagram.pdf",
  "media_type": "application/pdf",
  "size_bytes": 1048576,
  "checksum": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "duration_ms": null,
  "retention_expires_at": null,
  "created_by": "11111111-2222-3333-4444-555555555555",
  "created_at": "2026-09-07T12:30:00Z",
  "rejection_reason": null
}
```

#### Step 4: Replacement Document (Version 2 Upload Intent)

Supporting documents permit revisions via `/versions/upload-intents`:
```http
POST /api/v1/assets/8a31c6a2-6f2b-4ec6-89d5-71bbd7a46c10/versions/upload-intents HTTP/1.1
Idempotency-Key: client-req-003
Content-Type: application/json

{"file_name": "architecture-diagram-v2.pdf", "declared_media_type": "application/pdf", "declared_size_bytes": 2097152}
```
**Predicted Response (HTTP 201 Created)**: Returns a new `asset_version_id` (with `version_number: 2`). Parent `Asset.current_version_id` remains pointed to version 1 until version 2 completes verification.

---

### Walkthrough 2: Failure Modes and Status Codes

1. **Unsupported Extension or Media Type (HTTP 415)**: Request specifies unsupported filename or MIME type (e.g. `slides.key`), returning HTTP 415 (`code: "unsupported_media_type"`).
2. **Payload Too Large (HTTP 413)**: Request declares size exceeding kind bounds (e.g. 30 MiB for a document), returning HTTP 413 (`code: "payload_too_large"`).
3. **Corrupt or Encrypted File (HTTP 422)**: Upload reaches S3, but `complete_upload` detects encrypted PDF or broken slide references. Marks `version.state = "rejected"` and returns HTTP 422.
4. **Transient Storage Outage (HTTP 503)**: Storage disconnects during `complete_upload`. Version remains `pending_upload` and returns HTTP 503 (`code: "service_unavailable"`), allowing retry.

---

## 6. Migrations, Configuration, and Regressions

### Database Migrations and SQLite Batch Mode

The asset subsystem tables are managed across Alembic migrations:
- [`b2c3d4e5f6a7_add_assets_and_upload_idempotency.py`](../../migrations/versions/b2c3d4e5f6a7_add_assets_and_upload_idempotency.py): Creates `assets`, `asset_versions`, and `asset_upload_idempotency` tables with cascading foreign keys and unique indexes.
- [`c4d5e6f7a8b9_add_asset_retention_expires_at.py`](../../migrations/versions/c4d5e6f7a8b9_add_asset_retention_expires_at.py): Adds nullable column `retention_expires_at` to the `assets` table.
- [`d5e6f7a8b9c0_add_asset_version_upload_expires_at_and_cleanup.py`](../../migrations/versions/d5e6f7a8b9c0_add_asset_version_upload_expires_at_and_cleanup.py): Adds the non-null absolute upload deadline and nullable cleanup retry time to each version, plus the index used by the sweeper. Existing rows receive a rollout-safe deadline one hour after migration time before the non-null constraint is applied.

#### SQLite Batch Alter Compatibility

PostgreSQL natively supports `ALTER TABLE ... ALTER COLUMN`, but SQLite cannot alter constraints directly. For SQLite unit tests:
1. In [`migrations/env.py`](../../migrations/env.py), `render_as_batch=True` is set for offline and online migration runners.
2. Migrations ([`9d6a1b2c3e4f`](../../migrations/versions/9d6a1b2c3e4f_add_team_creation_idempotency.py) and [`a1b2c3d4e5f6`](../../migrations/versions/a1b2c3d4e5f6_add_project_versions_and_erasure_requests.py)) use `with op.batch_alter_table(...) as batch_op:` for column alterations.

### Runtime Docker and CI Environments

Media inspection requires system tools (`ffmpeg` and `ffprobe`):
- **Dockerfile** ([`Dockerfile`](../../Dockerfile)): Adds `RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*`.
- **GitHub Actions** ([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)): Adds step `sudo apt-get update && sudo apt-get install -y ffmpeg`.
- **Python dependencies** ([`pyproject.toml`](../../pyproject.toml), [`uv.lock`](../../uv.lock)): Pin the PDF, XML, S3, multipart, and database packages used by upload parsing, signing, and the live smoke workflow.

[`compose.yaml`](../../compose.yaml) deliberately configures two storage endpoints. Backend reads and deletes use the Docker-network address `http://minio:9000`; URLs handed to a browser are signed with `OBJECT_STORAGE_PUBLIC_ENDPOINT=http://localhost:9000`, which the host can actually reach. The endpoint changes the URL's signed host, so replacing it after signing would invalidate SigV4.

### Cleanup Configuration

[`app/infrastructure/settings.py`](../../app/infrastructure/settings.py) bounds the cleanup interval, batch size, retention window, lease, and tombstone delay. [`.env.example`](../../.env.example) documents their environment names and defaults. Cleanup is enabled by default outside tests; tests must opt in explicitly so an unrelated background sweep cannot make acceptance runs nondeterministic. [`README.md`](../../README.md) gives operators the lifecycle and smoke command in one place.

### Auth ORM Lookup Regression

Commit `b9d4761` fixed an ORM query regression in [`SqlAlchemyUserRepository.get_by_identity`](../../app/infrastructure/repositories/sqlalchemyUserRepositories.py):
```python
# DEFECTIVE:
row = (await self.session.execute(stmt)).mappings().first()
return User(id=row["id"], ...)  # Failed with AttributeError / TypeError

# FIXED:
row = await self.session.scalar(stmt)
return User(id=row.id, issuer=row.issuer, subject=row.subject, email=row.email, created_at=row.created_at) if row else None
```
Selecting an ORM entity model (`select(UserModel)`) returns ORM instances, not dictionary mappings. Attempting to use `result.mappings().first()` caused lookup failures during user authentication. This is permanently covered by regression test [`tests/test_user_repository_db.py`](../../tests/test_user_repository_db.py).

---

## 7. Test Architecture and Verification Evidence

The test suite covers the upload subsystem across all layers:

1. **Acceptance Tests** ([`tests/test_asset_acceptance.py`](../../tests/test_asset_acceptance.py), [`tests/test_media_upload_acceptance.py`](../../tests/test_media_upload_acceptance.py)): End-to-end workflows, 404 concealment, parameter validation, verification, idempotency replays, and pagination via `httpx.AsyncClient`.
2. **Repository Integration Tests** ([`tests/test_asset_repository_db.py`](../../tests/test_asset_repository_db.py)): Database persistence, unique constraint collisions, concurrent create races, savepoint rollbacks, and cursor pagination.
3. **Storage Adapter Tests** ([`tests/test_asset_storage_adapter.py`](../../tests/test_asset_storage_adapter.py), [`tests/test_storage_cancellation.py`](../../tests/test_storage_cancellation.py)): SigV4 signed header enforcement, streaming truncation, mid-stream read failures, worker timeouts, and thread cancellation safety.
4. **Media Verifier Tests** ([`tests/test_media_verifier.py`](../../tests/test_media_verifier.py)): Container/codec matrices, missing WebM duration headers, overlong media bounds, resource limit signals, and output truncation.
5. **Database Migration Tests** ([`tests/test_migrations.py`](../../tests/test_migrations.py)): Forward and backward Alembic migration execution across all schema versions.
6. **Cleanup Tests** ([`tests/test_asset_cleanup.py`](../../tests/test_asset_cleanup.py), [`tests/test_cleanup_review_regressions.py`](../../tests/test_cleanup_review_regressions.py)): Absolute expiry, retry leases, scheduler recovery, tombstone ordering, and preservation of verified or fresh pending replacements.

### Verification Commands

```bash
# Run all asset, storage, repository, and media tests
uv run pytest -v tests/test_asset_acceptance.py                  tests/test_asset_repository_db.py                  tests/test_asset_storage_adapter.py                  tests/test_media_verifier.py                  tests/test_media_upload_acceptance.py                  tests/test_storage_cancellation.py                  tests/test_user_repository_db.py

# Run migration tests
uv run pytest -v tests/test_migrations.py

# Run abandoned-upload cleanup and review regressions
uv run pytest -v tests/test_asset_cleanup.py tests/test_cleanup_review_regressions.py

# Lint and typecheck
uv run ruff check
uv run mypy .
```

The explicit smoke script provisions a unique PostgreSQL database and MinIO bucket, runs the HTTP and storage workflow, rolls every migration down and up, and removes those disposable resources in `finally` blocks:

```bash
uv run python scripts/smoke-assets.py --env-file .env.local
```

---

## 8. Abandoned Upload Cleanup

The FastAPI lifespan starts a periodic cleanup task outside the request path. A version becomes eligible only after both its absolute upload deadline and its retention grace period have passed. The repository claims work by locking the parent asset and version, then moves the version to `deleting` with a lease before object storage is called. This keeps a slow network delete out of the database transaction.

Missing objects count as a successful delete. Other storage failures are logged, leave the version in `deleting`, and schedule a later retry. After a successful delete, the repository marks the version `deleted` and schedules a delayed tombstone sweep. That later sweep catches an upload that reached storage after the first delete because its signed request was already in flight.

Finalization preserves the logical asset whenever another usable version exists. A verified version remains current; if there is no verified version but a fresh pending replacement exists, that replacement becomes current. The asset is marked `deleted` only when neither survives. These transitions share the same parent-first lock order as version creation and completion, so cleanup cannot erase a replacement that is being created concurrently.

---

## 9. Codebase File-to-Purpose Map

| File Path | Layer | Primary Responsibility |
| :--- | :--- | :--- |
| [`app/domain/asset.py`](../../app/domain/asset.py) | Domain | Models (`Asset`, `AssetVersion`, `UploadIntent`) and domain error hierarchy. |
| [`app/domain/idempotency.py`](../../app/domain/idempotency.py) | Domain | Model `AssetUploadIdempotency` for scoped keys and hashes. |
| [`app/application/interfaces/assetRepository.py`](../../app/application/interfaces/assetRepository.py) | Application | Port for persistence, locking, and query contracts. |
| [`app/application/interfaces/objectStorage.py`](../../app/application/interfaces/objectStorage.py) | Application | Port for presigning and streaming read contracts. |
| [`app/application/interfaces/documentVerifier.py`](../../app/application/interfaces/documentVerifier.py) | Application | Port for document structural inspection. |
| [`app/application/interfaces/mediaVerifier.py`](../../app/application/interfaces/mediaVerifier.py) | Application | Port for media container, codec, and duration checks. |
| [`app/application/services/assetStore.py`](../../app/application/services/assetStore.py) | Application | Deep boundary for authorization, idempotency, presigning, and verification. |
| [`app/infrastructure/persistence/configurations/assetConfiguration.py`](../../app/infrastructure/persistence/configurations/assetConfiguration.py) | Infrastructure | SQLAlchemy ORM models (`AssetModel`, `AssetVersionModel`, `AssetUploadIdempotencyModel`). |
| [`app/infrastructure/repositories/sqlalchemyAssetRepository.py`](../../app/infrastructure/repositories/sqlalchemyAssetRepository.py) | Infrastructure | SQLAlchemy repository with row locks and savepoints. |
| [`app/infrastructure/storage/s3ObjectStorage.py`](../../app/infrastructure/storage/s3ObjectStorage.py) | Infrastructure | S3 adapter for SigV4 presigning, streaming reads, and cancellation. |
| [`app/infrastructure/documents/document_verifier.py`](../../app/infrastructure/documents/document_verifier.py) | Infrastructure | Asyncio runner for isolated document verification workers. |
| [`app/infrastructure/documents/document_worker.py`](../../app/infrastructure/documents/document_worker.py) | Infrastructure | Sandboxed worker validating PDF and PPTX via pypdf and defusedxml. |
| [`app/infrastructure/media/ffmpegVerifier.py`](../../app/infrastructure/media/ffmpegVerifier.py) | Infrastructure | FFmpeg verifier checking containers, codecs, and frame decodes. |
| [`app/infrastructure/media/worker_launcher.py`](../../app/infrastructure/media/worker_launcher.py) | Infrastructure | Process launcher applying POSIX resource limits before execution. |
| [`app/infrastructure/repositories/sqlalchemyUserRepositories.py`](../../app/infrastructure/repositories/sqlalchemyUserRepositories.py) | Infrastructure | User repository with corrected ORM scalar identity query. |
| [`app/api/routes/asset.py`](../../app/api/routes/asset.py) | API | FastAPI routes translating requests to `AssetStore` calls and problem details. |
| [`app/api/schemas/asset.py`](../../app/api/schemas/asset.py) | API | Pydantic request and response schemas for assets and intents. |
| [`app/api/dependencies/services.py`](../../app/api/dependencies/services.py), [`app/api/routes/__init__.py`](../../app/api/routes/__init__.py) | API wiring | Expose the asset service dependency and register its router. |
| [`app/infrastructure/settings.py`](../../app/infrastructure/settings.py), [`.env.example`](../../.env.example) | Configuration | Bound upload URL and cleanup controls with documented defaults. |
| [`app/main.py`](../../app/main.py) | Application | Entry point wiring the verifier and cancellation-safe periodic cleanup task. |
| [`migrations/versions/b2c3d4e5f6a7_add_assets_and_upload_idempotency.py`](../../migrations/versions/b2c3d4e5f6a7_add_assets_and_upload_idempotency.py) | Infrastructure | Alembic migration creating asset tables and foreign keys. |
| [`migrations/versions/c4d5e6f7a8b9_add_asset_retention_expires_at.py`](../../migrations/versions/c4d5e6f7a8b9_add_asset_retention_expires_at.py) | Infrastructure | Alembic migration adding `retention_expires_at` to `assets`. |
| [`migrations/versions/d5e6f7a8b9c0_add_asset_version_upload_expires_at_and_cleanup.py`](../../migrations/versions/d5e6f7a8b9c0_add_asset_version_upload_expires_at_and_cleanup.py) | Infrastructure | Adds absolute upload deadlines, cleanup retry scheduling, backfill, and indexes. |
| [`scripts/smoke-assets.py`](../../scripts/smoke-assets.py) | Verification | Runs the disposable real PostgreSQL, MinIO, HTTP, media, cleanup, and migration smoke flow. |
| [`migrations/env.py`](../../migrations/env.py) | Infrastructure | Alembic configuration enabling `render_as_batch=True`. |
| [`Dockerfile`](../../Dockerfile), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), [`pyproject.toml`](../../pyproject.toml), [`uv.lock`](../../uv.lock) | Runtime | Install and pin FFmpeg and Python packages required by the verifier and storage adapters. |
| [`compose.yaml`](../../compose.yaml) | Local stack | Separates the internal MinIO endpoint used by backend traffic from the browser-reachable endpoint embedded in signed URLs. |

---

## 10. Retrieval Exercises and Practice

Test your understanding of the asset and signed upload architecture. Formulate your answer mentally before revealing the solution.

### Exercise 1: Storage Signatures and Browser Headers

**Question**: Why does the backend include `content-length` in the S3 SigV4 presigned URL's signed headers parameter (`X-Amz-SignedHeaders`), but omit it from the `required_headers` dictionary returned to the API caller in `UploadIntentResponse`?

<details>
<summary>Reveal answer</summary>

W3C security rules forbid client-side JavaScript from setting `Content-Length` in `fetch()` or `XMLHttpRequest`; browsers set it automatically. Signing `content-length` into SigV4 ensures S3 validates the browser-calculated header against authorized size, while omitting it from `required_headers` avoids browser header exceptions.

</details>

### Exercise 2: Transient Failures vs. Upload Rejections

**Question**: During `complete_upload`, the backend streams the uploaded object from S3 and invokes the document verifier. If S3 drops the connection mid-stream, or if the verifier worker times out due to CPU contention, why does the backend return HTTP 503 and leave the version in `pending_upload` rather than marking it `rejected`?

<details>
<summary>Reveal answer</summary>

Rejection is reserved for deterministically invalid content (corrupt structures, invalid codecs, size mismatches). Transient infrastructure issues (network drops, timeouts, process startup failures) mean the upload could not be evaluated. Leaving the version in `pending_upload` with HTTP 503 allows clients or background retries without permanently rejecting valid files.

</details>

### Exercise 3: Database Concurrency and Deadlocks

**Question**: In `get_asset_and_version_for_completion`, what two database techniques are used to prevent deadlocks and stale in-memory reads when two concurrent transactions attempt to complete uploads for the same asset?

<details>
<summary>Reveal answer</summary>

First, a strict hierarchical lock order acquires `FOR UPDATE` on `AssetModel` before `AssetVersionModel`, eliminating circular wait deadlocks. Second, `.execution_options(populate_existing=True)` forces SQLAlchemy to refresh in-memory identity map attributes with the newly locked row values.

</details>

### Exercise 4: Version Advancement Ordering

**Question**: Suppose version 1 of a supporting document is uploaded and takes 10 seconds to verify. Meanwhile, version 2 is uploaded and completes verification in 2 seconds. When version 1 finally completes verification, what does the backend do with `Asset.current_version_id`?

<details>
<summary>Reveal answer</summary>

The backend marks version 1 as `verified` in `asset_versions`, but leaves `Asset.current_version_id` pointing to version 2. The check `version.version_number >= current_ver.version_number` ensures older versions finishing out of order never regress the active version pointer.

</details>

### Exercise 5: Cleanup and an In-Flight Upload

**Question**: Cleanup deletes an expired object and marks its version `deleted`, but a PUT that was already in flight reaches storage just afterward. Why does this not leave the object behind forever, and what happens if a fresh replacement version exists on the same asset?

<details>
<summary>Reveal answer</summary>

Finalization schedules `cleanup_next_attempt_at` for a delayed tombstone sweep. When that time arrives, the same private key is deleted again, so a late PUT is eventually removed. While finalizing the old version, the repository locks the parent asset first and searches for survivors. It keeps the newest verified version current, or promotes the newest pending replacement when no verified version exists. Only an asset with neither is marked `deleted` and concealed from later reads.

</details>

---

## 11. Spaced Practice Schedule

Review this material according to the following spaced schedule:

- **Day 1 (Core Lifecycle)**: Re-read Section 2 (Storage Protocol) and Section 4 (Concurrency). Trace SQL execution during `save_asset_with_initial_version` and `complete_upload`.
- **Day 3 (Failure Modes)**: Review Section 3 (Verification Pipeline). Trace malformed PPTX and WebM payloads through `document_worker.py` and `ffmpegVerifier.py`.
- **Day 7 (Extension Practice)**: Trace the code path for adding a new document or media kind across domain models, limits, verifiers, and schemas.

Revisit the relevant sections and retrieval exercises whenever you encounter unexpected behavior while maintaining the codebase.
