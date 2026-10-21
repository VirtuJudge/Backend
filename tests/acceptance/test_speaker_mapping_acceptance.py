from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.ai_jobs import AIJobs
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.user import User
from app.infrastructure.database import Base
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.project_configuration import ProjectModel
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.persistence.configurations.team_configuration import TeamModel
from app.infrastructure.persistence.configurations.team_member_configuration import (
    TeamMemberModel,
)
from app.infrastructure.persistence.configurations.user_configuration import UserModel
from app.infrastructure.repositories.session_workflow.sqlalchemy_unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from app.main import create_app
from app.settings import Settings
from tests.support.fake_ai_job_queue import FakeAIJobQueue
from tests.support.fake_session_notifications import FakeSessionNotifications
from tests.support.fakes import FakeObjectStorage

SHARED_WORKER_SECRET = "test-worker-auth-secret-key-12345"


class MultiUserTokenVerifier:
    def __init__(self, users: dict[str, User]) -> None:
        self._users = users

    def verify(self, token: str) -> dict[str, Any]:
        user = self._users.get(token)
        if user is None:
            raise ValueError("Unknown token")
        return {
            "iss": user.issuer,
            "sub": user.subject,
            "email": user.email,
        }


@pytest.fixture
async def acceptance_env(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[
        async_sessionmaker[AsyncSession],
        AsyncClient,
        Settings,
        dict[str, Any],
    ]
]:
    db_path = tmp_path / "speaker_mapping_acceptance.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_fk(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    alice_id = uuid4()
    bob_id = uuid4()
    outsider_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    attempt_id = uuid4()
    job_id = uuid4()
    manifest_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()

    async with session_maker() as s:
        s.add_all(
            [
                UserModel(
                    id=alice_id,
                    email="alice@example.com",
                    issuer="test",
                    subject=f"sub-{alice_id}",
                    created_at=now,
                ),
                UserModel(
                    id=bob_id,
                    email="bob@example.com",
                    issuer="test",
                    subject=f"sub-{bob_id}",
                    created_at=now,
                ),
                UserModel(
                    id=outsider_id,
                    email="outsider@example.com",
                    issuer="test",
                    subject=f"sub-{outsider_id}",
                    created_at=now,
                ),
                TeamModel(id=team_id, name="Test Team", created_at=now, version=1),
            ]
        )
        await s.flush()

        s.add_all(
            [
                TeamMemberModel(
                    id=uuid4(),
                    team_id=team_id,
                    user_id=alice_id,
                    role="owner",
                    joined_at=now,
                ),
                TeamMemberModel(
                    id=uuid4(),
                    team_id=team_id,
                    user_id=bob_id,
                    role="member",
                    joined_at=now,
                ),
                ProjectModel(
                    id=project_id,
                    team_id=team_id,
                    name="Test Project",
                    created_at=now,
                    version=1,
                ),
            ]
        )
        await s.flush()

        s.add(
            AssetModel(
                id=asset_id,
                project_id=project_id,
                kind="presentation_video",
                file_name="pitch.mp4",
                current_version_id=None,
                created_by=alice_id,
                created_at=now,
            )
        )
        await s.flush()

        s.add(
            AssetVersionModel(
                id=version_id,
                asset_id=asset_id,
                version_number=1,
                storage_key=f"test/{asset_id}/pitch.mp4",
                file_name="pitch.mp4",
                declared_media_type="video/mp4",
                declared_size_bytes=1024,
                state="verified",
                checksum=f"sha256:{'1' * 64}",
                created_by=alice_id,
                created_at=now,
                upload_expires_at=now + timedelta(hours=1),
            )
        )
        await s.flush()

        s.add(
            PracticeSessionModel(
                id=session_id,
                name="Pitch Session",
                project_id=project_id,
                created_by=alice_id,
                status=SessionStatus.ANALYZING,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        await s.flush()

        s.add(
            SessionManifestModel(
                id=manifest_id,
                session_id=session_id,
                presentation_version_id=version_id,
                rubric_id="startup_pitch",
                rubric_version=1,
                frozen_at=now,
            )
        )
        await s.flush()

        s.add(
            AnalysisAttemptModel(
                id=attempt_id,
                session_id=session_id,
                manifest_id=manifest_id,
                attempt_number=1,
                status=AnalysisAttemptStatus.RUNNING,
                version=1,
                created_at=now,
                started_at=now,
            )
        )
        await s.flush()

        s.add(
            AnalysisJobModel(
                id=job_id,
                practice_session_id=session_id,
                attempt_id=attempt_id,
                analysis_attempt=1,
                job_type="analyze_session",
                status=AnalysisJobStatus.RUNNING,
                correlation_id=uuid4(),
                last_update_sequence=1,
                payload_version=1,
                attempts=1,
                cancel_requested=False,
                retry_count=0,
                created_at=now,
                updated_at=now,
                started_at=now,
                payload={"trace_id": "trc_test_accept_1"},
            )
        )
        await s.commit()

    db_url_for_app = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        app_env="test",
        database_url=db_url_for_app,
        ai_worker_shared_secret=SecretStr(SHARED_WORKER_SECRET),
        ai_job_dispatcher_enabled=False,
    )

    app = create_app(settings)
    app.state.session_factory = session_maker
    app.state.ai_job_queue = FakeAIJobQueue()
    app.state.session_notifications = FakeSessionNotifications()
    app.state.object_storage = FakeObjectStorage()

    alice_user = User(
        id=alice_id,
        email="alice@example.com",
        issuer="test",
        subject=f"sub-{alice_id}",
        display_name="Alice",
        created_at=now,
    )
    bob_user = User(
        id=bob_id,
        email="bob@example.com",
        issuer="test",
        subject=f"sub-{bob_id}",
        display_name="Bob",
        created_at=now,
    )
    outsider_user = User(
        id=outsider_id,
        email="outsider@example.com",
        issuer="test",
        subject=f"sub-{outsider_id}",
        display_name="Outsider",
        created_at=now,
    )

    verifier = MultiUserTokenVerifier(
        {
            "token-alice": alice_user,
            "token-bob": bob_user,
            "token-outsider": outsider_user,
        }
    )
    app.state.token_verifier = verifier

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    context = {
        "alice_id": alice_id,
        "bob_id": bob_id,
        "outsider_id": outsider_id,
        "team_id": team_id,
        "project_id": project_id,
        "session_id": session_id,
        "attempt_id": attempt_id,
        "job_id": job_id,
        "manifest_id": manifest_id,
        "alice_user": alice_user,
        "bob_user": bob_user,
        "outsider_user": outsider_user,
        "app": app,
    }

    try:
        yield session_maker, client, settings, context
    finally:
        await client.aclose()
        await engine.dispose()


def _valid_analysis_completed_payload(sequence: int = 2) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "status": "completed",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": "trc_test_accept_1",
        "payload": {
            "analysis_artifact": {
                "artifact_id": "art_analysis_001",
                "object_key": "ai/session/analysis.json",
                "checksum": "sha256:" + "a" * 64,
                "schema_version": 1,
            },
            "primary_questions": [
                {
                    "candidate_id": "q-1",
                    "text": "What evidence supports the claimed conversion rate?",
                    "reason": "Pitch states rate without source.",
                    "rubric_dimension": "pitch_content_and_evidence",
                    "evidence_ids": ["ev_1"],
                },
                {
                    "candidate_id": "q-2",
                    "text": "Which technical assumption is highest risk?",
                    "reason": "Dependencies are unranked.",
                    "rubric_dimension": "technical_feasibility",
                    "evidence_ids": ["ev_2"],
                },
                {
                    "candidate_id": "q-3",
                    "text": "How was target segment validated?",
                    "reason": "No validation presented.",
                    "rubric_dimension": "business_reasoning",
                    "evidence_ids": ["ev_3"],
                },
            ],
            "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
            "limitations": [],
        },
    }


@pytest.mark.anyio
async def test_endpoint_speaker_mappings_full_lifecycle(
    acceptance_env: tuple[
        async_sessionmaker[AsyncSession],
        AsyncClient,
        Settings,
        dict[str, Any],
    ],
) -> None:
    session_maker, client, _, ctx = acceptance_env
    job_id = ctx["job_id"]
    session_id = ctx["session_id"]
    attempt_id = ctx["attempt_id"]
    alice_id = ctx["alice_id"]
    bob_id = ctx["bob_id"]

    # 1. Before worker callback: session is still ANALYZING -> PUT returns 409 Conflict
    res_early_put = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={"Authorization": "Bearer token-alice", "If-Match": '"1"'},
        json={
            "mappings": [
                {"speaker_label": "SPEAKER_00", "user_id": str(alice_id)},
            ]
        },
    )
    assert res_early_put.status_code == 409
    assert res_early_put.json()["code"] == "analysis_not_ready"

    # 2. Worker callback completes presentation analysis with speaker labels
    res_cb = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers={"Authorization": f"Bearer {SHARED_WORKER_SECRET}"},
        json=_valid_analysis_completed_payload(),
    )
    assert res_cb.status_code == 200
    assert res_cb.json()["status"] == "completed"

    # Verify session transitioned and version updated
    async with session_maker() as db:
        session_row = await db.get(PracticeSessionModel, session_id)
        assert session_row is not None
        assert session_row.status == SessionStatus.QUESTIONS_IN_PROGRESS
        session_version = session_row.version

    # 3. PUT speaker mappings through normal get_session_workflow construction
    mapping_payload = {
        "mappings": [
            {"speaker_label": "SPEAKER_00", "user_id": str(alice_id)},
            {"speaker_label": "SPEAKER_01", "user_id": str(bob_id)},
        ]
    }
    res_put = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',
        },
        json=mapping_payload,
    )
    assert res_put.status_code == 200
    assert "ETag" in res_put.headers
    mappings_data = res_put.json()
    assert len(mappings_data) == 2
    label_to_user = {m["speaker_label"]: m["user_id"] for m in mappings_data}
    assert label_to_user["SPEAKER_00"] == str(alice_id)
    assert label_to_user["SPEAKER_01"] == str(bob_id)

    # 4. Precondition check: Stale ETag returns 412 Precondition Failed
    res_stale = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',  # Old version
        },
        json=mapping_payload,
    )
    assert res_stale.status_code == 412
    assert res_stale.json()["code"] == "precondition_failed"

    # 5. Precondition check: Malformed If-Match returns 400 Bad Request
    res_bad_etag = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": "invalid-no-quotes",
        },
        json=mapping_payload,
    )
    assert res_bad_etag.status_code == 400

    # 6. Propagation verification into report-generation job presenter mapping
    async with session_maker() as db:
        uow = SqlAlchemyUnitOfWork(db)
        ai_jobs = AIJobs(uow, storage=FakeObjectStorage())
        session = await uow.sessions.get_by_id(session_id)
        assert session is not None
        attempt = await uow.attempts.get_by_id(attempt_id)
        assert attempt is not None
        round_ = await uow.qa.get_round_by_session(session_id)
        assert round_ is not None

        # Verify saved mappings in DB
        persisted_mappings = await uow.speaker_mappings.get_by_attempt_id(attempt_id)
        assert len(persisted_mappings) == 2
        persisted_map = {m.speaker_label: str(m.user_id) for m in persisted_mappings}
        assert persisted_map["SPEAKER_00"] == str(alice_id)
        assert persisted_map["SPEAKER_01"] == str(bob_id)

        # Create report job and verify propagation
        report_job = await ai_jobs.create_report_job(
            session=session,
            round_=round_,
            attempt=attempt,
            now=datetime.now(UTC),
        )
        assert report_job is not None
        assert report_job.payload is not None
        contract_mappings = report_job.payload["payload"]["speaker_mappings"]
        assert len(contract_mappings) == 2
        contract_map = {m["speaker_label"]: m["user_id"] for m in contract_mappings}
        assert contract_map["SPEAKER_00"] == str(alice_id)
        assert contract_map["SPEAKER_01"] == str(bob_id)


@pytest.mark.anyio
async def test_endpoint_speaker_mappings_auth_and_validation_scopes(
    acceptance_env: tuple[
        async_sessionmaker[AsyncSession],
        AsyncClient,
        Settings,
        dict[str, Any],
    ],
) -> None:
    session_maker, client, _, ctx = acceptance_env
    job_id = ctx["job_id"]
    session_id = ctx["session_id"]
    alice_id = ctx["alice_id"]
    outsider_id = ctx["outsider_id"]

    # Complete analysis job first
    res_cb = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers={"Authorization": f"Bearer {SHARED_WORKER_SECRET}"},
        json=_valid_analysis_completed_payload(),
    )
    assert res_cb.status_code == 200

    async with session_maker() as db:
        session_row = await db.get(PracticeSessionModel, session_id)
        assert session_row is not None
        session_version = session_row.version

    # 1. Outsider user (not in team) -> 403 Forbidden
    res_outsider = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-outsider",
            "If-Match": f'"{session_version}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(alice_id)}]},
    )
    assert res_outsider.status_code == 403

    # 2. Non-existent session -> 404 Not Found
    res_not_found = await client.put(
        f"/api/v1/practice-sessions/{uuid4()}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(alice_id)}]},
    )
    assert res_not_found.status_code == 404

    # 3. User ID that is NOT a member of the project team -> 422 Unprocessable Content
    res_invalid_member = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(outsider_id)}]},
    )
    assert res_invalid_member.status_code == 422
    assert res_invalid_member.json()["code"] == "invalid_member"

    # 4. Unknown speaker label (not in produced labels) -> 422 Unprocessable Content
    res_unknown_label = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_99", "user_id": str(alice_id)}]},
    )
    assert res_unknown_label.status_code == 422
    assert res_unknown_label.json()["code"] == "invalid_label"

    # 5. Duplicate team member mapped twice -> 422 Unprocessable Content
    res_dup_member = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{session_version}"',
        },
        json={
            "mappings": [
                {"speaker_label": "SPEAKER_00", "user_id": str(alice_id)},
                {"speaker_label": "SPEAKER_01", "user_id": str(alice_id)},
            ]
        },
    )
    assert res_dup_member.status_code == 422
    assert res_dup_member.json()["code"] == "invalid_label"


@pytest.mark.anyio
async def test_endpoint_speaker_mappings_retry_rejects_old_attempt_labels(
    acceptance_env: tuple[
        async_sessionmaker[AsyncSession],
        AsyncClient,
        Settings,
        dict[str, Any],
    ],
) -> None:
    session_maker, client, _, ctx = acceptance_env
    job_id = ctx["job_id"]
    session_id = ctx["session_id"]
    manifest_id = ctx["manifest_id"]
    alice_id = ctx["alice_id"]

    # 1. Complete attempt 1 analysis
    res_cb = await client.post(
        f"/internal/v1/ai-jobs/{job_id}/updates",
        headers={"Authorization": f"Bearer {SHARED_WORKER_SECRET}"},
        json=_valid_analysis_completed_payload(sequence=2),
    )
    assert res_cb.status_code == 200

    # 2. Simulate retry: attempt 2 is created and running; session transitions to ANALYZING
    now = datetime.now(UTC)
    attempt2_id = uuid4()
    job2_id = uuid4()

    async with session_maker() as db:
        session_row = await db.get(PracticeSessionModel, session_id)
        assert session_row is not None
        session_row.status = SessionStatus.ANALYZING
        session_row.version += 1
        current_version = session_row.version

        db.add(
            AnalysisAttemptModel(
                id=attempt2_id,
                session_id=session_id,
                manifest_id=manifest_id,
                attempt_number=2,
                status=AnalysisAttemptStatus.RUNNING,
                version=1,
                created_at=now,
                started_at=now,
            )
        )
        db.add(
            AnalysisJobModel(
                id=job2_id,
                practice_session_id=session_id,
                attempt_id=attempt2_id,
                analysis_attempt=2,
                job_type="analyze_session",
                status=AnalysisJobStatus.RUNNING,
                correlation_id=uuid4(),
                last_update_sequence=1,
                payload_version=1,
                attempts=1,
                cancel_requested=False,
                retry_count=0,
                created_at=now,
                updated_at=now,
                started_at=now,
                payload={"trace_id": "trc_test_accept_2"},
            )
        )
        await db.commit()

    # 3. Before attempt 2 completes: attempt 1 is stale, attempt 2 is running -> PUT returns 409
    res_stale_put = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{current_version}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(alice_id)}]},
    )
    assert res_stale_put.status_code == 409
    assert res_stale_put.json()["code"] == "analysis_not_ready"

    # 4. Now send completed update for attempt 2
    res_cb2 = await client.post(
        f"/internal/v1/ai-jobs/{job2_id}/updates",
        headers={"Authorization": f"Bearer {SHARED_WORKER_SECRET}"},
        json={
            "schema_version": 1,
            "sequence": 2,
            "status": "completed",
            "occurred_at": datetime.now(UTC).isoformat(),
            "trace_id": "trc_test_accept_2",
            "payload": {
                "analysis_artifact": {
                    "artifact_id": "art_analysis_002",
                    "object_key": "ai/session/analysis_2.json",
                    "checksum": "sha256:" + "b" * 64,
                    "schema_version": 1,
                },
                "primary_questions": [
                    {
                        "candidate_id": "q-1",
                        "text": "Question 1?",
                        "reason": "Reason 1",
                        "rubric_dimension": "pitch_content_and_evidence",
                        "evidence_ids": ["ev_1"],
                    },
                    {
                        "candidate_id": "q-2",
                        "text": "Question 2?",
                        "reason": "Reason 2",
                        "rubric_dimension": "technical_feasibility",
                        "evidence_ids": ["ev_2"],
                    },
                    {
                        "candidate_id": "q-3",
                        "text": "Question 3?",
                        "reason": "Reason 3",
                        "rubric_dimension": "business_reasoning",
                        "evidence_ids": ["ev_3"],
                    },
                ],
                "speaker_labels": ["SPEAKER_00"],
                "limitations": [],
            },
        },
    )
    assert res_cb2.status_code == 200

    async with session_maker() as db:
        session_row = await db.get(PracticeSessionModel, session_id)
        assert session_row is not None
        v2 = session_row.version

    # 5. Now PUT succeeds for attempt 2
    res_put2 = await client.put(
        f"/api/v1/practice-sessions/{session_id}/speaker-mappings",
        headers={
            "Authorization": "Bearer token-alice",
            "If-Match": f'"{v2}"',
        },
        json={"mappings": [{"speaker_label": "SPEAKER_00", "user_id": str(alice_id)}]},
    )
    assert res_put2.status_code == 200
    assert res_put2.json()[0]["speaker_label"] == "SPEAKER_00"
