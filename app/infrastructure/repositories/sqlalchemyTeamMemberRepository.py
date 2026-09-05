from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository


class SqlAlchemyTeamMemberRepository(SqlAlchemyTeamRepository):
    """Compatibility entry point for membership-specific repository wiring."""