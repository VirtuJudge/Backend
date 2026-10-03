from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.ports.team_invitation_repository import TeamInvitationRepository
from app.application.services.team_invitation_service import InvitationPreconditionFailed
from app.domain.idempotency import InvitationCreationIdempotency
from app.domain.team_invitation import InvitationStatus, TeamInvitation
from app.domain.team_member import TeamMember
from app.infrastructure.persistence.configurations import (
    InvitationCreationIdempotencyModel,
)
from app.infrastructure.persistence.configurations.team_configuration import TeamModel
from app.infrastructure.persistence.configurations.team_invitation_configuration import (
    TeamInvitationModel,
)
from app.infrastructure.persistence.configurations.team_member_configuration import TeamMemberModel


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
            resend_idempotency_keys=[],
            version=model.version,
        )

    async def create(self, invitation: TeamInvitation, idempotency_key: str) -> TeamInvitation:

        stmt = insert(TeamInvitationModel).values(
            id=invitation.id,
            role=invitation.role,
            team_id=invitation.team_id,
            email=invitation.email,
            idempotency_key=invitation.idempotency_key,
            status=invitation.status,
            token_hash=invitation.token_hash,
            delivery_status=invitation.delivery_status,
            delivery_attempts=invitation.delivery_attempts,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return invitation

    async def exists_pending_invitation(self, team_id: UUID, email: str) -> bool:
        stmt = select(TeamInvitationModel).where(
            TeamInvitationModel.team_id == team_id,
            TeamInvitationModel.email == email,
            TeamInvitationModel.status == InvitationStatus.PENDING,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def list_by_team(
        self, team_id: UUID, cursor: UUID | None = None, limit: int = 20
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

    async def get_creation_idempotency(
        self, actor_id: UUID, team_id: UUID, operation: str, key: str
    ) -> InvitationCreationIdempotency | None:
        model = await self.session.get(
            InvitationCreationIdempotencyModel, (actor_id, team_id, operation, key)
        )
        if model is None:
            return None
        return InvitationCreationIdempotency(
            model.actor_id,
            model.team_id,
            model.operation,
            model.key,
            model.request_hash,
            model.invitation_id,
        )

    async def create_with_idempotency(
        self, invitation: TeamInvitation, record: InvitationCreationIdempotency
    ) -> InvitationCreationIdempotency:
        try:
            async with self.session.begin_nested():
                await self.create(invitation, record.key)
                self.session.add(
                    InvitationCreationIdempotencyModel(
                        actor_id=record.actor_id,
                        team_id=record.team_id,
                        operation=record.operation,
                        key=record.key,
                        request_hash=record.request_hash,
                        invitation_id=record.invitation_id,
                    )
                )
                await self.session.flush()
        except IntegrityError:
            previous = await self.get_creation_idempotency(
                record.actor_id, record.team_id, record.operation, record.key
            )
            if previous is None:
                raise
            return previous
        await self.session.commit()
        return record

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
                token_hash=invitation.token_hash,
                version=invitation.version + 1,
            )
            .returning(TeamInvitationModel)
        )

        result = await self.session.execute(stmt)
        await self.session.commit()

        updated_model = result.scalar_one_or_none()

        if updated_model is None:
            raise InvitationPreconditionFailed

        return self._invitation(updated_model)

    async def get_invitation_by_token(self, token: str) -> tuple[str, str, TeamInvitation] | None:
        stmt = (
            select(TeamInvitationModel)
            .where(TeamInvitationModel.token_hash == token)
            .options(
                selectinload(TeamInvitationModel.team)
                .selectinload(TeamModel.members)
                .selectinload(TeamMemberModel.user)
            )
        )
        model = await self.session.scalar(stmt)

        if model is None:
            return None

        team = model.team

        owner = next(member for member in team.members if member.role == "owner")
        owner_name = owner.user.display_name or owner.user.email or "Team owner"

        invitation = self._invitation(model)

        return team.name, owner_name, invitation

    async def accept(self, invitation: TeamInvitation, membership: TeamMember) -> bool:
        consume = (
            update(TeamInvitationModel)
            .where(
                TeamInvitationModel.id == invitation.id,
                TeamInvitationModel.version == invitation.version,
                TeamInvitationModel.status == InvitationStatus.PENDING,
            )
            .values(
                status=InvitationStatus.ACCEPTED,
                version=invitation.version + 1,
            )
            .returning(TeamInvitationModel.id)
        )
        consumed_id = await self.session.scalar(consume)
        if consumed_id is None:
            await self.session.rollback()
            return False

        await self.session.execute(
            insert(TeamMemberModel).values(
                id=membership.id,
                team_id=membership.team_id,
                user_id=membership.user_id,
                role=membership.role,
                joined_at=membership.joined_at,
            )
        )
        await self.session.commit()
        return True
