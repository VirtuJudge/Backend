import uuid
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.invitationResendKeyRepository import InvitationResendKeyRepository
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import TeamInvitation
from app.infrastructure.persistence.configurations.invitationResendIdompotancyConfiguration import (
    InvitationResendIdempotencyModel,
)


class SqlalchemyInvitationResendKeyRepository(InvitationResendKeyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, invitation: TeamInvitation, resend_idempotency_key: str
    ) -> InvitationResendIdempotency:
        record_id = uuid.uuid4()
        stmt = insert(InvitationResendIdempotencyModel).values(
            id=record_id,
            invitation_id=invitation.id,
            key=resend_idempotency_key,
            created_at=datetime.now(UTC),
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return InvitationResendIdempotency(
            id=record_id,
            invitation_id=invitation.id,
            key=resend_idempotency_key,
            created_at=datetime.now(UTC),
        )

    async def get_by_resend_idempotency_key(
        self,
        resend_idempotency_key: str,
    ) -> InvitationResendIdempotency | None:
        result = await self.session.execute(
            select(InvitationResendIdempotencyModel).where(
                InvitationResendIdempotencyModel.key == resend_idempotency_key
            )
        )
        model = result.scalars().first()
        if model is None:
            return None
        return InvitationResendIdempotency(
            id=model.id,
            invitation_id=model.invitation_id,
            key=model.key,
            created_at=model.created_at,
        )
