
from datetime import UTC, date, datetime
import uuid

from sqlalchemy import delete, select, update,insert
from sqlalchemy.ext.asyncio import AsyncSession


from app.application.interfaces.invitationResendKeyRepository import InvitationResendKeyRepository
from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import TeamInvitation
from app.infrastructure.persistence.configurations.invitationResendIdompotancyConfiguration import InvitationResendIdempotencyModel
from app.infrastructure.persistence.configurations.invitationResendIdompotancyConfiguration import InvitationResendIdempotencyModel


class SqlalchemyInvitationResendKeyRepository(InvitationResendKeyRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, invitation: TeamInvitation, resend_idempotency_key: str) -> InvitationResendIdempotency:
        stmt = insert(InvitationResendIdempotencyModel).values(
            id=uuid.uuid4(),
            invitation_id=invitation.id,
            key=resend_idempotency_key,
            created_at=datetime.now(UTC),
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar()

    async def get_by_resend_idempotency_key(
        self,
        resend_idempotency_key: str,
    ) -> InvitationResendIdempotency | None:
        result = await self.session.execute(
            select(InvitationResendIdempotencyModel).where(
                InvitationResendIdempotencyModel.key == resend_idempotency_key
            )
        )
        return result.scalars().first()
