import argparse
import json
import os
import secrets
import sys
import time
import urllib.parse
import uuid
from datetime import UTC, datetime

from local_stack.config import QUEUE_KEY, RESULT_KEY_PREFIX


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


def check_postgres(database_url: str, check_extension: bool = True) -> None:
    import psycopg

    normalized_url = normalize_database_url(database_url)
    with (
        psycopg.connect(
            normalized_url,
            connect_timeout=5,
            options="-c statement_timeout=5000 -c lock_timeout=5000",
        ) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("SELECT 1")
        row = cur.fetchone()
        if not row or row[0] != 1:
            raise RuntimeError("Postgres SELECT 1 query verification failed")

        if check_extension:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ext_row = cur.fetchone()
            if not ext_row or ext_row[0] != "vector":
                raise RuntimeError("pgvector extension 'vector' is not installed in database")


def check_role_isolation(database_url: str, own_schema: str, other_schema: str) -> None:
    import psycopg
    from psycopg.errors import InsufficientPrivilege

    normalized_url = normalize_database_url(database_url)
    table_name = f"smoke_{own_schema}_{uuid.uuid4().hex[:8]}"

    with psycopg.connect(
        normalized_url, connect_timeout=5, options="-c statement_timeout=5000 -c lock_timeout=5000"
    ) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"CREATE TABLE {own_schema}.{table_name} (id int primary key, note text)"
                )
                cur.execute(
                    f"INSERT INTO {own_schema}.{table_name} (id, note) VALUES (%s, %s)",
                    (1, f"{own_schema}_ok"),
                )
                cur.execute(
                    f"SELECT note FROM {own_schema}.{table_name} WHERE id = %s",
                    (1,),
                )
                row = cur.fetchone()
                if not row or row[0] != f"{own_schema}_ok":
                    raise RuntimeError(
                        f"Failed to read written record from {own_schema}.{table_name}"
                    )

                try:
                    cur.execute(f"SELECT 1 FROM {other_schema}.probe_table")
                    raise RuntimeError(
                        f"Expected InsufficientPrivilege on SELECT from {other_schema}"
                    )
                except InsufficientPrivilege:
                    pass

                try:
                    cur.execute(f"INSERT INTO {other_schema}.probe_table (id) VALUES (1)")
                    raise RuntimeError(
                        f"Expected InsufficientPrivilege on INSERT into {other_schema}"
                    )
                except InsufficientPrivilege:
                    pass
            finally:
                cur.execute(f"DROP TABLE IF EXISTS {own_schema}.{table_name}")


def check_redis(redis_url: str) -> None:
    import redis

    parsed = urllib.parse.urlparse(redis_url)
    db1_url = urllib.parse.urlunparse(parsed._replace(path="/1"))
    client = redis.from_url(
        db1_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )
    test_key = f"virtujudge:local:smoke:cache:{uuid.uuid4().hex}"
    test_val = f"smoke_val_{uuid.uuid4().hex}"

    try:
        if not client.ping():
            raise RuntimeError("Redis ping failed")
        client.set(test_key, test_val, ex=60)
        retrieved = client.get(test_key)
        if retrieved != test_val:
            raise RuntimeError(
                f"Redis cache roundtrip mismatch: expected {test_val!r}, got {retrieved!r}"
            )
    finally:
        try:
            client.delete(test_key)
        finally:
            client.close()


def check_s3(
    endpoint_url: str,
    bucket_name: str,
    access_key: str,
    secret_key: str,
) -> None:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    config = Config(
        s3={"addressing_style": "path"},
        connect_timeout=5,
        read_timeout=5,
        retries={"max_attempts": 2},
    )
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config,
        region_name="us-east-1",
    )

    try:
        s3_client.create_bucket(Bucket=bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise

    test_key = f"smoke/{uuid.uuid4().hex}.txt"
    test_content = b"synthetic-smoke-payload"

    try:
        s3_client.put_object(Bucket=bucket_name, Key=test_key, Body=test_content)
        resp = s3_client.get_object(Bucket=bucket_name, Key=test_key)
        with resp["Body"] as response_body:
            body = response_body.read()
        if body != test_content:
            raise RuntimeError("S3 synthetic object roundtrip content mismatch")
    finally:
        try:
            s3_client.delete_object(Bucket=bucket_name, Key=test_key)
        finally:
            s3_client.close()


def check_ai_worker(redis_url: str, timeout_seconds: float = 30.0) -> None:
    import redis

    client = redis.from_url(
        redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )
    value = (time.time_ns() // 1_000_000) << 80 | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    job_id = "".join(alphabet[(value >> shift) & 31] for shift in range(125, -1, -5))
    result_key = f"{RESULT_KEY_PREFIX}{job_id}"

    message = {
        "schema_version": 1,
        "job_id": job_id,
        "job_type": "analyze_session",
        "practice_session_id": job_id,
        "analysis_attempt": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "trace_id": job_id,
        "payload": {
            "presentation": {
                "artifact_id": job_id,
                "object_key": "raw/smoke_presentation.mp4",
                "checksum": "sha256:" + "0" * 64,
                "media_type": "video/mp4",
            },
            "supporting_documents": [],
            "rubric": {
                "rubric_id": "rubric-default",
                "version": 1,
            },
            "requested_capabilities": [],
        },
    }

    deadline = time.monotonic() + timeout_seconds
    serialized = json.dumps(message)
    try:
        client.rpush(QUEUE_KEY, serialized)
        raw_result: str | None = None
        while time.monotonic() < deadline:
            val = client.get(result_key)
            if val is not None:
                raw_result = str(val)
                break
            time.sleep(0.5)

        if raw_result is None:
            raise TimeoutError(f"Timed out waiting for AI worker result on {result_key}")

        result_data = json.loads(raw_result)
        if result_data.get("trace_id") != message["trace_id"]:
            raise RuntimeError("AI worker result correlation mismatch")
        status = result_data.get("status")
        if status != "completed":
            raise RuntimeError(
                f"AI worker returned terminal status '{status}', expected 'completed'"
            )

        primary_questions = result_data.get("payload", {}).get("primary_questions", [])
        if len(primary_questions) != 3:
            raise RuntimeError(
                f"Expected exactly 3 primary questions, got {len(primary_questions)}"
            )

        for idx, q in enumerate(primary_questions):
            text = q.get("text")
            evidence_ids = q.get("evidence_ids")
            if not text or not isinstance(text, str):
                raise RuntimeError(f"Question {idx} missing valid text")
            if not evidence_ids or not isinstance(evidence_ids, list):
                raise RuntimeError(f"Question {idx} is not grounded (missing evidence_ids)")
    finally:
        try:
            client.lrem(QUEUE_KEY, 1, serialized)
            client.delete(result_key)
        finally:
            client.close()


def run_smoke(role: str) -> None:
    if role in ("backend", "ai"):
        db_url = os.environ["DATABASE_URL"]
        check_postgres(db_url)
        print("[OK] PostgreSQL connectivity and pgvector")
        check_role_isolation(db_url, role, "ai" if role == "backend" else "backend")
        print(f"[OK] {role} role isolation")
    if role in ("backend", "storage"):
        check_s3(
            os.environ["OBJECT_STORAGE_ENDPOINT"],
            os.environ["OBJECT_STORAGE_BUCKET"],
            os.environ["OBJECT_STORAGE_ACCESS_KEY"],
            os.environ["OBJECT_STORAGE_SECRET_KEY"],
        )
        print("[OK] S3 bucket and object round trip")
    if role == "backend":
        check_redis(os.environ["REDIS_CACHE_URL"])
        print("[OK] Redis cache round trip")
        check_ai_worker(os.environ["REDIS_URL"])
        print("[OK] Correlated AI analysis with three grounded Primary Questions")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VirtuJudge local stack smoke diagnostic")
    parser.add_argument("role", choices=["backend", "ai", "storage"])
    role = parser.parse_args(argv).role
    try:
        run_smoke(role)
        return 0
    except Exception:
        print(
            f"Smoke diagnostic failed for {role}; check service health and configuration",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
