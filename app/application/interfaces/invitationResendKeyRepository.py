
from abc import ABC, abstractmethod

from app.domain.invitation_resend_idompotency_key import InvitationResendIdempotency
from app.domain.team_invitation import TeamInvitation


class InvitationResendKeyRepository(ABC):
    @abstractmethod
    async def get_by_resend_idempotency_key(
        self,
        resend_idempotency_key: str,
    ) -> InvitationResendIdempotency | None:
        pass

    @abstractmethod
    async def create(self, invitation: TeamInvitation, resend_idempotency_key: str) -> InvitationResendIdempotency:
        pass