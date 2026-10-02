from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.invitation_resend_key_repository import InvitationResendKeyRepository
from app.application.services.team_invitation_service import InvitationPreconditionFailed
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import InvitationStatus, TeamInvitation
from app.infrastructure.persistence.configurations import (
    InvitationResendIdempotencyModel,
    TeamInvitationModel,
)


class SqlalchemyInvitationResendKeyRepository(InvitationResendKeyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, invitation: TeamInvitation, record: InvitationResendIdempotency
    ) -> InvitationResendIdempotency:
        try:
            async with self.session.begin_nested():
                await self.session.execute(
                    insert(InvitationResendIdempotencyModel).values(
                        id=record.id,
                        actor_id=record.actor_id,
                        team_id=record.team_id,
                        invitation_id=record.invitation_id,
                        operation=record.operation,
                        key=record.key,
                        request_hash=record.request_hash,
                        created_at=record.created_at,
                    )
                )
                updated_id = await self.session.scalar(
                    update(TeamInvitationModel)
                    .where(
                        TeamInvitationModel.id == record.invitation_id,
                        TeamInvitationModel.team_id == record.team_id,
                        TeamInvitationModel.version == invitation.version,
                        TeamInvitationModel.status == InvitationStatus.PENDING,
                    )
                    .values(
                        token_hash=invitation.token_hash,
                        delivery_status=invitation.delivery_status,
                        delivery_attempts=invitation.delivery_attempts,
                        version=invitation.version + 1,
                    )
                    .returning(TeamInvitationModel.id)
                )
                if updated_id is None:
                    raise InvitationPreconditionFailed
        except IntegrityError:
            previous = await self.get_by_resend_idempotency_key(
                record.actor_id, record.team_id, record.invitation_id, record.operation, record.key
            )
            if previous is None:
                raise
            return previous
        await self.session.commit()
        return record

    async def get_by_resend_idempotency_key(
        self,
        actor_id: UUID,
        team_id: UUID,
        invitation_id: UUID,
        operation: str,
        resend_idempotency_key: str,
    ) -> InvitationResendIdempotency | None:
        model = await self.session.scalar(
            select(InvitationResendIdempotencyModel).where(
                InvitationResendIdempotencyModel.actor_id == actor_id,
                InvitationResendIdempotencyModel.team_id == team_id,
                InvitationResendIdempotencyModel.invitation_id == invitation_id,
                InvitationResendIdempotencyModel.operation == operation,
                InvitationResendIdempotencyModel.key == resend_idempotency_key,
            )
        )
        if model is None:
            return None
        return InvitationResendIdempotency(
            id=model.id,
            actor_id=model.actor_id,
            team_id=model.team_id,
            invitation_id=model.invitation_id,
            operation=model.operation,
            key=model.key,
            request_hash=model.request_hash,
            created_at=model.created_at,
        )
