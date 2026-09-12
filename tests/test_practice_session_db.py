from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.persistence.configurations.projectConfigration import (
    ProjectModel,
)
from app.infrastructure.persistence.configurations.teamConfigration import TeamModel
from app.infrastructure.persistence.configurations.userConfigration import UserModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.entities.session_practice import PracticeSession
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.exceptions import StaleEntityVersion
from app.infrastructure.persistence.configurations.session_workflow import (
    PracticeSessionModel,
)
from app.infrastructure.repositories.session_workflow.sqlalchemyPracticeSessionRepository import (
    SqlAlchemyPracticeSessionRepository,
)


async def ensure_session_parents(
    session: AsyncSession,
) -> tuple[UUID, UUID]:
    parent_ids = session.info.get("practice_session_parents")

    if parent_ids is not None:
        return parent_ids

    now = datetime.now(UTC)

    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()

    session.add_all(
        [
            UserModel(
                id=user_id,
                issuer="test",
                subject=str(user_id),
                created_at=now,
            ),
            TeamModel(
                id=team_id,
                name="Test team",
                created_at=now,
            ),
        ],
    )

    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Test project",
            description=None,
            created_at=now,
        ),
    )

    await session.flush()

    parent_ids = (project_id, user_id)
    session.info["practice_session_parents"] = parent_ids

    return parent_ids


async def create_test_session(
    session: AsyncSession,
    name: str | None = "Test Session",
    project_id: UUID | None = None,
    created_by: UUID | None = None,
) -> PracticeSession:
    if project_id is None or created_by is None:
        project_id, created_by = await ensure_session_parents(session)

    now = datetime.now(UTC)

    practice_session = PracticeSession(
        id=uuid4(),
        name=name,
        project_id=project_id,
        created_by=created_by,
        status=SessionStatus.DRAFT,
        version=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        consent_granted=False,
    )

    repository = SqlAlchemyPracticeSessionRepository(session)

    return await repository.create(practice_session)


async def create_test_project(
    session: AsyncSession,
) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)

    user_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()

    session.add_all(
        [
            UserModel(
                id=user_id,
                issuer="test",
                subject=str(user_id),
                created_at=now,
            ),
            TeamModel(
                id=team_id,
                name="Test team",
                created_at=now,
            ),
        ],
    )

    await session.flush()

    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="Test project",
            description=None,
            created_at=now,
        ),
    )

    await session.flush()

    return project_id, user_id


@pytest.mark.anyio
async def test_get_by_id_returns_session(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    created = await create_test_session(session)

    result = await repository.get_by_id(created.id)

    assert result is not None
    assert result.id == created.id
    assert result.project_id == created.project_id
    assert result.created_by == created.created_by
    assert result.name == "Test Session"
    assert result.status == SessionStatus.DRAFT


@pytest.mark.anyio
async def test_get_by_id_returns_none_for_unknown_id(
    async_db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPracticeSessionRepository(async_db_session)

    result = await repository.get_by_id(uuid4())

    assert result is None


@pytest.mark.anyio
async def test_create_persists_session(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    session_id = uuid4()
    project_id, created_by = await ensure_session_parents(
        session,
    )

    now = datetime.now(UTC)

    practice_session = PracticeSession(
        id=session_id,
        name="Created Session",
        project_id=project_id,
        created_by=created_by,
        status=SessionStatus.DRAFT,
        version=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        consent_granted=False,
    )

    created = await repository.create(practice_session)

    persisted = await session.scalar(
        select(PracticeSessionModel).where(
            PracticeSessionModel.id == session_id,
        )
    )

    assert created.id == session_id
    assert created.project_id == project_id
    assert created.created_by == created_by

    assert persisted is not None
    assert persisted.id == session_id
    assert persisted.project_id == project_id
    assert persisted.created_by == created_by
    assert persisted.name == "Created Session"
    assert persisted.status == SessionStatus.DRAFT
    assert persisted.version == 1
    assert persisted.consent_granted is False


@pytest.mark.anyio
async def test_update_changes_session(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    practice_session = await create_test_session(session)

    original_version = practice_session.version

    practice_session.status = SessionStatus.DRAFT
    practice_session.started_at = datetime.now(UTC)
    practice_session.updated_at = datetime.now(UTC)
    practice_session.consent_granted = True

    updated = await repository.update(
        practice_session,
        expected_version=original_version,
    )

    assert updated.id == practice_session.id
    assert updated.status == SessionStatus.DRAFT
    assert updated.version == original_version + 1

    persisted = await session.scalar(
        select(PracticeSessionModel).where(
            PracticeSessionModel.id == practice_session.id,
        )
    )

    assert persisted is not None
    assert persisted.status == SessionStatus.DRAFT
    assert persisted.version == original_version + 1
    assert persisted.consent_granted is True
    assert persisted.started_at is not None


@pytest.mark.anyio
async def test_update_raises_stale_entity_version(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    practice_session = await create_test_session(session)

    stale_version = practice_session.version - 1

    with pytest.raises(StaleEntityVersion):
        await repository.update(
            practice_session,
            expected_version=stale_version,
        )


@pytest.mark.anyio
async def test_exists_returns_true_for_existing_session(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    practice_session = await create_test_session(session)

    result = await repository.exists(practice_session.id)

    assert result is True


@pytest.mark.anyio
async def test_exists_returns_false_for_unknown_session(
    async_db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPracticeSessionRepository(async_db_session)

    result = await repository.exists(uuid4())

    assert result is False


@pytest.mark.anyio
async def test_list_by_project_returns_sessions(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    first = await create_test_session(
        session,
        name="First Session",
    )

    second = await create_test_session(
        session,
        name="Second Session",
    )

    result, cursor = await repository.list_by_project(
        first.project_id,
    )

    assert len(result) == 2
    assert {item.id for item in result} == {
        first.id,
        second.id,
    }
    assert {item.name for item in result} == {
        "First Session",
        "Second Session",
    }
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_does_not_return_other_projects(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    first_project_id, first_user_id = await create_test_project(session)
    other_project_id, other_user_id = await create_test_project(session)

    first = await create_test_session(
        session,
        name="My Session",
        project_id=first_project_id,
        created_by=first_user_id,
    )

    other = await create_test_session(
        session,
        name="Other Session",
        project_id=other_project_id,
        created_by=other_user_id,
    )

    result, cursor = await repository.list_by_project(
        first.project_id,
    )

    assert len(result) == 1
    assert result[0].id == first.id
    assert result[0].id != other.id
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_search_filters_by_name(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    first = await create_test_session(
        session,
        name="Python Practice",
    )

    result, cursor = await repository.list_by_project(
        first.project_id,
        search="Python",
    )

    assert len(result) == 1
    assert result[0].id == first.id
    assert result[0].name == "Python Practice"
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_search_is_case_insensitive(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session

    first = await create_test_session(
        session,
        name="Python Practice",
    )

    repository = SqlAlchemyPracticeSessionRepository(session)

    result, cursor = await repository.list_by_project(
        first.project_id,
        search="python",
    )

    assert len(result) == 1
    assert result[0].id == first.id
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_search_returns_empty_for_no_match(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session

    practice_session = await create_test_session(
        session,
        name="Python Practice",
    )

    repository = SqlAlchemyPracticeSessionRepository(session)

    result, cursor = await repository.list_by_project(
        practice_session.project_id,
        search="Java",
    )

    assert result == []
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_returns_none_cursor_on_last_page(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session

    practice_session = await create_test_session(session)

    repository = SqlAlchemyPracticeSessionRepository(session)

    result, cursor = await repository.list_by_project(
        practice_session.project_id,
        limit=20,
    )

    assert len(result) == 1
    assert cursor is None


@pytest.mark.anyio
async def test_list_by_project_paginates(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    first = await create_test_session(
        session,
        name="Session 1",
    )

    project_id = first.project_id
    created_by = first.created_by

    sessions = [first]

    for number in range(2, 4):
        session_id = uuid4()
        now = datetime.now(UTC)

        practice_session = PracticeSession(
            id=session_id,
            name=f"Session {number}",
            project_id=project_id,
            created_by=created_by,
            status=SessionStatus.DRAFT,
            version=1,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            consent_granted=False,
        )

        sessions.append(
            await repository.create(practice_session),
        )

    first_page, cursor = await repository.list_by_project(
        project_id,
        limit=2,
    )

    assert len(first_page) == 2
    assert cursor is not None
    assert cursor == str(first_page[-1].id)

    second_page, next_cursor = await repository.list_by_project(
        project_id,
        cursor=str(cursor),
        limit=2,
    )

    assert len(second_page) == 1
    assert next_cursor is None

    returned_ids = {item.id for item in first_page + second_page}

    expected_ids = {item.id for item in sessions}

    assert returned_ids == expected_ids


@pytest.mark.anyio
async def test_list_by_project_cursor_excludes_previous_rows(
    async_db_session: AsyncSession,
) -> None:
    session = async_db_session
    repository = SqlAlchemyPracticeSessionRepository(session)

    first = await create_test_session(
        session,
        name="Session 1",
    )

    project_id = first.project_id
    created_by = first.created_by

    for number in range(2, 4):
        now = datetime.now(UTC)

        practice_session = PracticeSession(
            id=uuid4(),
            name=f"Session {number}",
            project_id=project_id,
            created_by=created_by,
            status=SessionStatus.DRAFT,
            version=1,
            created_at=now,
            updated_at=now,
            started_at=None,
            completed_at=None,
            cancelled_at=None,
            consent_granted=False,
        )

        await repository.create(practice_session)

    first_page, cursor = await repository.list_by_project(
        project_id,
        limit=2,
    )

    assert cursor is not None

    second_page, next_cursor = await repository.list_by_project(
        project_id,
        cursor=str(cursor),
        limit=2,
    )

    first_ids = {item.id for item in first_page}
    second_ids = {item.id for item in second_page}

    assert first_ids.isdisjoint(second_ids)
    assert len(second_page) == 1
    assert next_cursor is None
