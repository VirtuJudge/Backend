from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.diarization.default_diarization_reader import (
    DefaultDiarizationResultReader,
)
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    PracticeSessionModel,
    SessionManifestModel,
)


async def _seed_pipeline(
    session: AsyncSession,
    *,
    session_status: SessionStatus = SessionStatus.QUESTIONS_IN_PROGRESS,
    attempt_status: AnalysisAttemptStatus = AnalysisAttemptStatus.RUNNING,
    attempt_number: int = 1,
    job_type: str = "analyze_session",
    job_status: AnalysisJobStatus = AnalysisJobStatus.COMPLETED,
    job_analysis_attempt: int = 1,
    cancel_requested: bool = False,
    completed_result: dict[str, Any] | None = None,
    session_access_revoked: bool = False,
    project_access_revoked: bool = False,
) -> tuple[UUID, UUID, UUID, UUID]:
    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    session_id = uuid4()
    manifest_id = uuid4()
    asset_id = uuid4()
    version_id = uuid4()
    attempt_id = uuid4()
    job_id = uuid4()

    session.add_all(
        [
            UserModel(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                issuer="test",
                subject=f"sub-{user_id.hex[:6]}",
                created_at=now,
            ),
            TeamModel(id=team_id, name="Test Team", created_at=now, version=1),
        ]
    )
    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Test Project",
            created_at=now,
            access_revoked_at=now if project_access_revoked else None,
            version=1,
        )
    )
    await session.flush()

    session.add(
        AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="presentation_video",
            file_name="pitch.mp4",
            current_version_id=None,
            created_by=user_id,
            created_at=now,
        )
    )
    await session.flush()

    session.add(
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
            created_by=user_id,
            created_at=now,
            upload_expires_at=now + timedelta(hours=1),
        )
    )
    await session.flush()

    session.add(
        PracticeSessionModel(
            id=session_id,
            name="Pitch Session",
            project_id=project_id,
            created_by=user_id,
            status=session_status,
            version=1,
            created_at=now,
            updated_at=now,
            access_revoked_at=now if session_access_revoked else None,
        )
    )
    await session.flush()

    session.add(
        SessionManifestModel(
            id=manifest_id,
            session_id=session_id,
            presentation_version_id=version_id,
            rubric_id="startup_pitch",
            rubric_version=1,
            frozen_at=now,
        )
    )
    await session.flush()

    session.add(
        AnalysisAttemptModel(
            id=attempt_id,
            session_id=session_id,
            manifest_id=manifest_id,
            attempt_number=attempt_number,
            status=attempt_status,
            version=1,
            created_at=now,
            started_at=now,
        )
    )
    await session.flush()

    session.add(
        AnalysisJobModel(
            id=job_id,
            practice_session_id=session_id,
            attempt_id=attempt_id,
            analysis_attempt=job_analysis_attempt,
            job_type=job_type,
            status=job_status,
            correlation_id=uuid4(),
            last_update_sequence=1,
            payload_version=1,
            attempts=1,
            cancel_requested=cancel_requested,
            retry_count=0,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now if job_status == AnalysisJobStatus.COMPLETED else None,
            completed_result=completed_result,
        )
    )
    await session.commit()

    return attempt_id, job_id, session_id, manifest_id


@pytest.mark.anyio
async def test_reader_returns_labels_for_valid_running_attempt_with_completed_job(
    async_db_session: AsyncSession,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        attempt_status=AnalysisAttemptStatus.RUNNING,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00", "SPEAKER_01"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == {"SPEAKER_00", "SPEAKER_01"}


@pytest.mark.anyio
async def test_reader_returns_empty_when_no_session() -> None:
    reader = DefaultDiarizationResultReader(None)
    labels = await reader.get_speaker_labels(uuid4())
    assert labels == set()


@pytest.mark.anyio
async def test_reader_returns_empty_when_no_job_exists(
    async_db_session: AsyncSession,
) -> None:
    attempt_id, job_id, _, _ = await _seed_pipeline(async_db_session)
    job = await async_db_session.get(AnalysisJobModel, job_id)
    assert job is not None
    await async_db_session.delete(job)
    await async_db_session.commit()

    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "non_completed_status",
    [
        AnalysisJobStatus.PENDING,
        AnalysisJobStatus.QUEUED,
        AnalysisJobStatus.RUNNING,
        AnalysisJobStatus.FAILED,
        AnalysisJobStatus.CANCELLED,
    ],
)
async def test_reader_rejects_non_completed_job_status(
    async_db_session: AsyncSession,
    non_completed_status: AnalysisJobStatus,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        job_status=non_completed_status,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
async def test_reader_rejects_job_when_cancel_requested(
    async_db_session: AsyncSession,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        job_status=AnalysisJobStatus.COMPLETED,
        cancel_requested=True,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_attempt_status",
    [AnalysisAttemptStatus.FAILED, AnalysisAttemptStatus.CANCELLED],
)
async def test_reader_rejects_terminal_attempt_status_even_with_completed_job(
    async_db_session: AsyncSession,
    invalid_attempt_status: AnalysisAttemptStatus,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        attempt_status=invalid_attempt_status,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_session_status",
    [SessionStatus.FAILED, SessionStatus.CANCELLED],
)
async def test_reader_rejects_terminal_session_status_even_with_completed_job(
    async_db_session: AsyncSession,
    invalid_session_status: SessionStatus,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        session_status=invalid_session_status,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_result",
    [
        None,
        {},
        {"speaker_labels": []},
        {"speaker_labels": 123},
        {"speaker_labels": ["", "   "]},
    ],
)
async def test_reader_rejects_missing_or_invalid_speaker_labels(
    async_db_session: AsyncSession,
    bad_result: Any,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result=bad_result,
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
async def test_reader_rejects_stale_attempt_after_retry(
    async_db_session: AsyncSession,
) -> None:
    attempt1_id, _, session_id, manifest_id = await _seed_pipeline(
        async_db_session,
        attempt_number=1,
        attempt_status=AnalysisAttemptStatus.COMPLETED,
        job_analysis_attempt=1,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00", "SPEAKER_01"]},
    )

    now = datetime.now(UTC)
    attempt2_id = uuid4()
    async_db_session.add(
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
    await async_db_session.commit()

    reader = DefaultDiarizationResultReader(async_db_session)
    # Attempt 1 is superseded/stale by attempt 2
    assert await reader.get_speaker_labels(attempt1_id) == set()
    # Attempt 2 has no completed analyze_session job
    assert await reader.get_speaker_labels(attempt2_id) == set()


@pytest.mark.anyio
@pytest.mark.parametrize("revocation_target", ["session", "project"])
async def test_reader_rejects_revoked_access(
    async_db_session: AsyncSession,
    revocation_target: str,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
        session_access_revoked=(revocation_target == "session"),
        project_access_revoked=(revocation_target == "project"),
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
@pytest.mark.parametrize("wrong_type", ["analyze_answer", "generate_report"])
async def test_reader_rejects_non_analyze_session_job_type(
    async_db_session: AsyncSession,
    wrong_type: str,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        job_type=wrong_type,
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()


@pytest.mark.anyio
async def test_reader_rejects_mismatched_attempt_number(
    async_db_session: AsyncSession,
) -> None:
    attempt_id, _, _, _ = await _seed_pipeline(
        async_db_session,
        attempt_number=1,
        job_analysis_attempt=2,  # mismatched
        job_status=AnalysisJobStatus.COMPLETED,
        completed_result={"speaker_labels": ["SPEAKER_00"]},
    )
    reader = DefaultDiarizationResultReader(async_db_session)
    labels = await reader.get_speaker_labels(attempt_id)
    assert labels == set()
