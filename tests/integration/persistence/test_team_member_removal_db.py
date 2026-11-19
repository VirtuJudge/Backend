from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.services.team_service import TeamService
from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.infrastructure.persistence.configurations import (
    ProjectModel,
    TeamMemberModel,
    TeamModel,
    UserModel,
)
from app.infrastructure.persistence.configurations.asset_configuration import (
    AssetModel,
    AssetVersionModel,
)
from app.infrastructure.persistence.configurations.session_workflow import (
    AnalysisAttemptModel,
    PracticeSessionModel,
    SessionManifestModel,
    SpeakerMappingModel,
)
from app.infrastructure.repositories.sqlalchemy_team_repository import SqlAlchemyTeamRepository

NOW = datetime.now(UTC)


async def _seed(session: AsyncSession) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    owner, mapped, unmapped = (
        UserModel(id=uuid4(), issuer="https://identity.example", subject=name, created_at=NOW)
        for name in ("owner", "mapped", "unmapped")
    )
    team = TeamModel(id=uuid4(), name="Team", created_at=NOW)
    session.add_all([owner, mapped, unmapped, team])
    await session.flush()
    mapped_member = TeamMemberModel(
        id=uuid4(), team_id=team.id, user_id=mapped.id, role="member", joined_at=NOW
    )
    project = ProjectModel(id=uuid4(), team_id=team.id, name="Project", created_at=NOW)
    session.add_all(
        [
            TeamMemberModel(team_id=team.id, user_id=owner.id, role="owner", joined_at=NOW),
            TeamMemberModel(team_id=team.id, user_id=unmapped.id, role="member", joined_at=NOW),
            mapped_member,
            project,
        ]
    )
    await session.flush()
    asset = AssetModel(
        id=uuid4(),
        project_id=project.id,
        kind="presentation_video",
        state="verified",
        file_name="pitch.mp4",
        created_by=owner.id,
        created_at=NOW,
    )
    session.add(asset)
    await session.flush()
    version = AssetVersionModel(
        id=uuid4(),
        asset_id=asset.id,
        version_number=1,
        state="verified",
        storage_key=f"projects/{project.id}/assets/{asset.id}/v1.mp4",
        file_name="pitch.mp4",
        declared_media_type="video/mp4",
        declared_size_bytes=1000,
        created_by=owner.id,
        created_at=NOW,
        upload_expires_at=NOW,
    )
    practice_session = PracticeSessionModel(
        id=uuid4(),
        name="Session",
        project_id=project.id,
        created_by=owner.id,
        status=SessionStatus.QUESTIONS_IN_PROGRESS,
        version=1,
        created_at=NOW,
        updated_at=NOW,
        consent_granted=True,
    )
    session.add_all([version, practice_session])
    await session.flush()
    manifest = SessionManifestModel(
        id=uuid4(),
        session_id=practice_session.id,
        presentation_version_id=version.id,
        rubric_id="startup_pitch",
        rubric_version=1,
        frozen_at=NOW,
    )
    session.add(manifest)
    await session.flush()
    attempt = AnalysisAttemptModel(
        id=uuid4(),
        session_id=practice_session.id,
        manifest_id=manifest.id,
        attempt_number=1,
        status=AnalysisAttemptStatus.COMPLETED,
        created_at=NOW,
        version=1,
    )
    session.add(attempt)
    await session.flush()
    mapping = SpeakerMappingModel(
        id=uuid4(),
        attempt_id=attempt.id,
        speaker_label="SPEAKER_00",
        member_id=mapped_member.id,
        mapped_by=owner.id,
        mapped_at=NOW,
    )
    session.add(mapping)
    await session.commit()
    return team.id, mapped.id, unmapped.id, owner.id, mapping.id


@pytest.mark.anyio
async def test_removing_mapped_and_unmapped_members_keeps_anonymous_attribution(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_session_factory() as session:
        team_id, mapped_id, unmapped_id, owner_id, mapping_id = await _seed(session)

    async with db_session_factory() as session:
        service = TeamService(SqlAlchemyTeamRepository(session))
        assert await service.remove_member(team_id, mapped_id)
        assert await service.remove_member(team_id, unmapped_id)
        await session.commit()

    async with db_session_factory() as session:
        members = await session.scalars(
            select(TeamMemberModel.user_id).where(TeamMemberModel.team_id == team_id)
        )
        assert list(members) == [owner_id]
        mapping = await session.get(SpeakerMappingModel, mapping_id)
        assert mapping is not None
        assert mapping.speaker_label == "SPEAKER_00"
        assert mapping.member_id is None
