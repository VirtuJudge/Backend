import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.session_workflow.entities.qa_round import QARound, Question
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.qa import QARoundState, QuestionKind, QuestionState
from app.domain.session_workflow.enums.session_status import SessionStatus
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
from app.infrastructure.repositories.session_workflow.sqlalchemy_qa_repository import (
    SqlAlchemyQARepository,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("ASSET_TEST_DATABASE_URL"),
    reason="Q&A persistence integration tests require PostgreSQL",
)


async def _seed_round_parents(session: AsyncSession) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    user_id, team_id, project_id, session_id, attempt_id = (uuid4() for _ in range(5))
    asset_id, asset_version_id, manifest_id = (uuid4() for _ in range(3))
    session.add_all(
        [
            UserModel(id=user_id, issuer="test", subject=str(user_id), created_at=now),
            TeamModel(id=team_id, name="QA Team", created_at=now),
        ]
    )
    await session.flush()
    session.add(
        ProjectModel(
            id=project_id,
            team_id=team_id,
            name="QA Project",
            description=None,
            created_at=now,
        )
    )
    await session.flush()
    session.add(
        AssetModel(
            id=asset_id,
            project_id=project_id,
            kind="presentation",
            file_name="pitch.mp4",
            created_by=user_id,
            created_at=now,
        )
    )
    await session.flush()
    session.add(
        AssetVersionModel(
            id=asset_version_id,
            asset_id=asset_id,
            storage_key=f"uploads/{team_id}/{project_id}/pitch.mp4",
            file_name="pitch.mp4",
            declared_media_type="video/mp4",
            declared_size_bytes=1024,
            created_by=user_id,
            created_at=now,
            upload_expires_at=now,
        )
    )
    await session.flush()
    session.add(
        PracticeSessionModel(
            id=session_id,
            project_id=project_id,
            created_by=user_id,
            status=SessionStatus.QUESTIONS_IN_PROGRESS,
            version=1,
            created_at=now,
            updated_at=now,
            consent_granted=True,
        )
    )
    await session.flush()
    session.add(
        SessionManifestModel(
            id=manifest_id,
            session_id=session_id,
            presentation_version_id=asset_version_id,
            rubric_id="startup_pitch",
            rubric_version=1,
            snapshot={"version": 1},
            frozen_at=now,
        )
    )
    await session.flush()
    session.add(
        AnalysisAttemptModel(
            id=attempt_id,
            session_id=session_id,
            manifest_id=manifest_id,
            attempt_number=1,
            status=AnalysisAttemptStatus.COMPLETED,
            version=1,
            created_at=now,
        )
    )
    await session.flush()
    return session_id, attempt_id


@pytest.mark.anyio
async def test_round_questions_and_optimistic_version_are_persisted(
    async_db_session: AsyncSession,
) -> None:
    session_id, attempt_id = await _seed_round_parents(async_db_session)
    now = datetime.now(UTC)
    round_ = QARound(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=attempt_id,
        state=QARoundState.IN_PROGRESS,
        current_question_id=None,
        follow_up_count=0,
        version=1,
        created_at=now,
        updated_at=now,
    )
    questions = [
        Question(
            id=uuid4(),
            qa_round_id=round_.id,
            practice_session_id=session_id,
            kind=QuestionKind.PRIMARY,
            position=position,
            text=f"Question {position}?",
            reason="Grounded reason",
            rubric_dimension="business_reasoning",
            evidence_ids=[f"ev-{position}"],
            parent_answer_id=None,
            state=QuestionState.ACTIVE if position == 1 else QuestionState.PENDING,
            created_at=now,
        )
        for position in range(1, 4)
    ]
    round_.current_question_id = questions[0].id
    repository = SqlAlchemyQARepository(async_db_session)

    await repository.create_round(round_)
    await repository.create_questions(questions)
    loaded = await repository.get_round_for_update(round_.id)
    persisted_questions = await repository.list_questions(round_.id)

    assert loaded is not None
    assert loaded.current_question_id == questions[0].id
    assert [question.position for question in persisted_questions] == [1, 2, 3]

    loaded.version = 2
    await repository.update_round(loaded, expected_version=1)
    loaded.version = 3
    with pytest.raises(StaleEntityVersion):
        await repository.update_round(loaded, expected_version=1)
