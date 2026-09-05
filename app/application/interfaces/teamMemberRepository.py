from abc import ABC

from app.application.interfaces.teamRepository import TeamRepository


class TeamMemberRepository(TeamRepository, ABC):
    """Repository contract for team membership operations."""