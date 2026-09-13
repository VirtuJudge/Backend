from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.entities.analysis_attempt import AnalysisAttempt
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.configurations import (
    AssetModel,
    AssetVersionModel,
    ProjectModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    PracticeSessionModel,
    SessionManifestModel,
)
from app.infrastructure.repositories.session_workflow.sqlalcemyAnalysisAttemptRepository import (
    SqlAlchemyAnalysisAttemptRepository,
)


async def ensure_attempt_parents(
    session: AsyncSession,
    session_id: UUID,
) -> UUID:
    manifest_id = session.info.get(f"analysis_attempt_manifest:{session_id}")

    manifest_id = cast(
        UUID | None,
        session.info.get(f"analysis_attempt_manifest:{session_id}"),
    )
    if manifest_id is not None:
        return manifest_id

    now = datetime.now(UTC)
    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    presentation_asset_id = uuid4()
    document_asset_id = uuid4()
    presentation_version_id = uuid4()
    document_version_id = uuid4()
    manifest_id = uuid4()

    session.add_all(
        [
            UserModel(
                id=user_id,
                issuer="test",
                subject=str(user_id),
                created_at=now,
            ),
            TeamModel(id=team_id, name="Test team", created_at=now),
        ]
    )
    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Test project",
            description=None,
            created_at=now,
        )
    )
    await session.flush()

    session.add_all(
        [
            AssetModel(
                id=presentation_asset_id,
                project_id=project_id,
                kind="presentation",
                file_name="presentation.pdf",
                created_by=user_id,
                created_at=now,
            ),
            AssetModel(
                id=document_asset_id,
                project_id=project_id,
                kind="document",
                file_name="document.pdf",
                created_by=user_id,
                created_at=now,
            ),
        ]
    )
    await session.flush()

    session.add_all(
        [
            AssetVersionModel(
                id=presentation_version_id,
                asset_id=presentation_asset_id,
                storage_key=f"test/{presentation_version_id}",
                file_name="presentation.pdf",
                declared_media_type="application/pdf",
                declared_size_bytes=1,
                created_by=user_id,
                created_at=now,
                upload_expires_at=now,
            ),
            AssetVersionModel(
                id=document_version_id,
                asset_id=document_asset_id,
                storage_key=f"test/{document_version_id}",
                file_name="document.pdf",
                declared_media_type="application/pdf",
                declared_size_bytes=1,
                created_by=user_id,
                created_at=now,
                upload_expires_at=now,
            ),
            PracticeSessionModel(
                id=session_id,
                project_id=project_id,
                created_by=user_id,
                status="READY",
                version=1,
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.flush()

    session.add(
        SessionManifestModel(
            id=manifest_id,
            session_id=session_id,
            presentation_version_id=presentation_version_id,
            document_version_id=document_version_id,
        )
    )
    await session.flush()
    session.info[f"analysis_attempt_manifest:{session_id}"] = manifest_id
    return manifest_id


async def create_test_attempt(
    session: AsyncSession,
    session_id: UUID,
    *,
    attempt_number: int = 1,
    status: AnalysisAttemptStatus = AnalysisAttemptStatus.PENDING,
    idempotency_key: str | None = None,
) -> AnalysisAttempt:
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    now = datetime.now(UTC)
    manifest_id = await ensure_attempt_parents(session, session_id)

    attempt = AnalysisAttempt(
        id=uuid4(),
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key=idempotency_key,
        attempt_number=attempt_number,
        status=status,
        failure_code=None,
        failure_message=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )

    return await repository.create(attempt)


@pytest.mark.anyio
async def test_get_by_id_returns_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
    )

    result = await repository.get_by_id(attempt.id)

    assert result is not None
    assert result.id == attempt.id
    assert result.session_id == session_id
    assert result.attempt_number == attempt.attempt_number


@pytest.mark.anyio
async def test_get_by_id_returns_none_for_unknown_id(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    result = await repository.get_by_id(uuid4())

    assert result is None


@pytest.mark.anyio
async def test_get_latest_returns_highest_attempt_number(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
        attempt_number=1,
    )

    latest = await create_test_attempt(
        session,
        session_id,
        attempt_number=3,
    )

    await create_test_attempt(
        session,
        session_id,
        attempt_number=2,
    )

    result = await repository.get_latest(session_id)

    assert result is not None
    assert result.id == latest.id
    assert result.attempt_number == 3


@pytest.mark.anyio
async def test_get_latest_returns_none_when_session_has_no_attempts(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    result = await repository.get_latest(uuid4())

    assert result is None


@pytest.mark.anyio
async def test_get_active_returns_pending_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
        status=AnalysisAttemptStatus.PENDING,
    )

    result = await repository.get_active(session_id)

    assert result is not None
    assert result.id == attempt.id
    assert result.status == AnalysisAttemptStatus.PENDING


@pytest.mark.anyio
async def test_get_active_returns_running_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
        status=AnalysisAttemptStatus.RUNNING,
    )

    result = await repository.get_active(session_id)

    assert result is not None
    assert result.id == attempt.id
    assert result.status == AnalysisAttemptStatus.RUNNING


@pytest.mark.anyio
async def test_get_active_ignores_terminal_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
        status=AnalysisAttemptStatus.COMPLETED,
    )

    result = await repository.get_active(session_id)

    assert result is None


@pytest.mark.anyio
async def test_get_by_number_returns_matching_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
        attempt_number=2,
    )

    result = await repository.get_by_number(
        session_id,
        2,
    )

    assert result is not None
    assert result.id == attempt.id
    assert result.attempt_number == 2


@pytest.mark.anyio
async def test_get_by_number_returns_none_for_unknown_number(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
        attempt_number=1,
    )

    result = await repository.get_by_number(
        session_id,
        99,
    )

    assert result is None


@pytest.mark.anyio
async def test_get_next_attempt_number_starts_at_one(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    result = await repository.get_next_attempt_number(uuid4())

    assert result == 1


@pytest.mark.anyio
async def test_get_next_attempt_number_returns_max_plus_one(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
        attempt_number=1,
    )

    await create_test_attempt(
        session,
        session_id,
        attempt_number=4,
    )

    result = await repository.get_next_attempt_number(session_id)

    assert result == 5


@pytest.mark.anyio
async def test_create_persists_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()
    now = datetime.now(UTC)
    manifest_id = await ensure_attempt_parents(session, session_id)

    attempt = AnalysisAttempt(
        id=uuid4(),
        session_id=session_id,
        manifest_id=manifest_id,
        idempotency_key="key-123",
        attempt_number=1,
        status=AnalysisAttemptStatus.PENDING,
        failure_code=None,
        failure_message=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        failed_at=None,
        cancelled_at=None,
        version=1,
    )

    created = await repository.create(attempt)

    assert created.id == attempt.id
    assert created.session_id == session_id
    assert created.idempotency_key == "key-123"

    persisted = await session.scalar(
        select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.id == attempt.id,
        )
    )

    assert persisted is not None
    assert persisted.session_id == session_id
    assert persisted.attempt_number == 1
    assert persisted.status == AnalysisAttemptStatus.PENDING
    assert persisted.idempotency_key == "key-123"


@pytest.mark.anyio
async def test_update_changes_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
        status=AnalysisAttemptStatus.PENDING,
    )

    original_version = attempt.version

    attempt.status = AnalysisAttemptStatus.RUNNING

    updated = await repository.update(
        attempt,
        expected_version=original_version,
    )

    assert updated.id == attempt.id
    assert updated.status == AnalysisAttemptStatus.RUNNING
    assert updated.version == original_version + 1

    persisted = await session.scalar(
        select(AnalysisAttemptModel).where(
            AnalysisAttemptModel.id == attempt.id,
        )
    )

    assert persisted is not None
    assert persisted.status == AnalysisAttemptStatus.RUNNING
    assert persisted.version == original_version + 1


@pytest.mark.anyio
async def test_update_raises_stale_entity_version(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
    )

    stale_version = attempt.version - 1

    with pytest.raises(StaleEntityVersion):
        await repository.update(
            attempt,
            expected_version=stale_version,
        )


@pytest.mark.anyio
async def test_get_by_idempotency_key_returns_matching_attempt(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()
    key = f"key-{uuid4().hex}"

    attempt = await create_test_attempt(
        session,
        session_id,
        idempotency_key=key,
    )

    result = await repository.get_by_idempotency_key(
        session_id,
        key,
    )

    assert result is not None
    assert result.id == attempt.id
    assert result.idempotency_key == key


@pytest.mark.anyio
async def test_get_by_idempotency_key_returns_none_for_unknown_key(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
        idempotency_key="existing-key",
    )

    result = await repository.get_by_idempotency_key(
        session_id,
        "unknown-key",
    )

    assert result is None


@pytest.mark.anyio
async def test_get_all_by_session_id_returns_attempts(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    first = await create_test_attempt(
        session,
        session_id,
        attempt_number=1,
    )

    second = await create_test_attempt(
        session,
        session_id,
        attempt_number=2,
    )

    result, next_cursor = await repository.get_all_by_session_id(
        session_id,
    )

    assert len(result) == 2
    assert next_cursor is None

    returned_ids = {attempt.id for attempt in result}

    assert returned_ids == {
        first.id,
        second.id,
    }


@pytest.mark.anyio
async def test_get_all_by_session_id_does_not_return_other_sessions(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()
    other_session_id = uuid4()

    attempt = await create_test_attempt(
        session,
        session_id,
    )

    await create_test_attempt(
        session,
        other_session_id,
    )

    result, next_cursor = await repository.get_all_by_session_id(
        session_id,
    )

    assert len(result) == 1
    assert result[0].id == attempt.id
    assert next_cursor is None


@pytest.mark.anyio
async def test_get_all_by_session_id_paginates(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    attempts = [
        await create_test_attempt(
            session,
            session_id,
            attempt_number=number,
        )
        for number in range(1, 4)
    ]

    first_page, next_cursor = await repository.get_all_by_session_id(
        session_id,
        limit=2,
    )

    assert len(first_page) == 2
    assert next_cursor is not None

    second_page, final_cursor = await repository.get_all_by_session_id(
        session_id,
        next_cursor=next_cursor,
        limit=2,
    )

    assert len(second_page) == 1
    assert final_cursor is None

    returned_ids = [attempt.id for attempt in first_page + second_page]

    assert set(returned_ids) == {attempt.id for attempt in attempts}


@pytest.mark.anyio
async def test_get_all_by_session_id_returns_none_cursor_on_last_page(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyAnalysisAttemptRepository(session)

    session_id = uuid4()

    await create_test_attempt(
        session,
        session_id,
    )

    result, next_cursor = await repository.get_all_by_session_id(
        session_id,
        limit=20,
    )

    assert len(result) == 1
    assert next_cursor is None
