from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.teamInvitationRepository import TeamInvitationRepository
from app.domain.team_invitation import TeamInvitation
from app.infrastructure.persistence.configurations.teamInvitationConfigurations import (
    TeamInvitationModel,
)


class SqlAlchemyTeamInvitationRepository(TeamInvitationRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _invitation(model: TeamInvitationModel) -> TeamInvitation:
        return TeamInvitation(
            id=model.id,
            team_id=model.team_id,
            email=model.email,
            token_hash=model.token_hash,
            role=model.role,
            idempotency_key=model.idempotency_key,
            status=model.status,
            delivery_status=model.delivery_status,
            delivery_attempts=model.delivery_attempts,
            created_at=model.created_at,
            expires_at=model.expires_at,
        )

    async def create(self, invitation: TeamInvitation) -> TeamInvitation:

        stmt = insert(TeamInvitationModel).values(
            id=invitation.id,
            role=invitation.role,
            team_id=invitation.team_id,
            email=invitation.email,
            idempotency_key=invitation.idempotency_key,
            status=invitation.status,
            delivery_status=invitation.delivery_status,
            delivery_attempts=invitation.delivery_attempts,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return self._invitation(result.scalar())

    async def exists_pending_invitation(self, team_id: UUID, email: str) -> bool:
        stmt = select(TeamInvitationModel).where(
            TeamInvitationModel.team_id == team_id,
            TeamInvitationModel.email == email,
            TeamInvitationModel.status == TeamInvitation.status.PENDING,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def list_by_team(
        self, team_id: UUID, limit: int = 20, cursor: UUID | None = None
    ) -> tuple[list[TeamInvitation], UUID | None]:
        stmt = (
            select(TeamInvitationModel)
            .where(
                TeamInvitationModel.team_id == team_id,
            )
            .order_by(TeamInvitationModel.id)
            .limit(limit + 1)
        )

        if cursor is not None:
            stmt = stmt.where(TeamInvitationModel.id > cursor)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = rows[-1].id if has_more else None

        return [self._invitation(invitation) for invitation in rows], next_cursor

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> TeamInvitation | None:

        stmt = select(TeamInvitationModel).where(
            TeamInvitationModel.idempotency_key == idempotency_key
        )

        model = await self.session.scalar(stmt)

        if model is None:
            return None

        return self._invitation(model)

    async def get_by_id(self, invitation_id: UUID) -> TeamInvitation | None:
        stmt = select(TeamInvitationModel).where(TeamInvitationModel.id == invitation_id)

        model = await self.session.scalar(stmt)

        if model is None:
            return None

        return self._invitation(model)

    async def update(self, invitation: TeamInvitation) -> TeamInvitation:
        stmt = (
            update(TeamInvitationModel)
            .where(
                TeamInvitationModel.id == invitation.id,
                TeamInvitationModel.version == invitation.version,
            )
            .values(
                status=invitation.status,
                delivery_status=invitation.delivery_status,
                delivery_attempts=invitation.delivery_attempts,
                version=invitation.version + 1,
            )
            .returning(TeamInvitationModel)
        )

        result: CursorResult = await self.session.execute(stmt)
        await self.session.commit()

        updated_model = result.scalar_one_or_none()

        if updated_model is None:
            raise ValueError(f"Invitation with ID {invitation.id} not found.")

        return self._invitation(updated_model)

    async def get_invitation_by_token(self, token: str) -> tuple[str, str, TeamInvitation] | None:
        stmt = select(TeamInvitationModel).where(TeamInvitationModel.token_hash == token)

        model = await self.session.scalar(stmt)

        if model is None:
            return None

        team = model.team

        owner_name = next(member.name for member in team.members if member.role == "owner")

        invitation = self._invitation(model)

        return team.name, owner_name, invitation

    async def get_by_resend_idempotency_key(
        self,
        resend_idempotency_key: str,
    ) -> TeamInvitation | None:
        stmt = select(TeamInvitationModel).where(
            TeamInvitationModel.resend_idempotency_key == resend_idempotency_key
        )

        model = await self.session.scalar(stmt)

        if model is None:
            return None

        return self._invitation(model)
