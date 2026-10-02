from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import TeamInvitation


class InvitationResendKeyRepository(ABC):
    @abstractmethod
    async def get_by_resend_idempotency_key(
        self,
        actor_id: UUID,
        team_id: UUID,
        invitation_id: UUID,
        operation: str,
        resend_idempotency_key: str,
    ) -> InvitationResendIdempotency | None:
        pass

    @abstractmethod
    async def create(
        self, invitation: TeamInvitation, record: InvitationResendIdempotency
    ) -> InvitationResendIdempotency:
        """Atomically commit the token rotation and key, or return the winning record."""
        pass
