from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import routers
from app.api.routes.health import router as health_router
from app.application.services.projectService import ProjectService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.application.services.teamInvitationService import TeamInvitationService
from app.infrastructure.auth.provider import create_token_verifier
from app.infrastructure.database import create_database_engine
from app.infrastructure.database import get_session as infrastructure_get_session
from app.infrastructure.mail import create_mail_sender
from app.infrastructure.repositories.sqlalchemyProjectRepository import SqlAlchemyProjectRepository
from app.infrastructure.repositories.sqlalchemyTeamMemberRepository import SqlAlchemyTeamMemberRepository
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository
from app.infrastructure.repositories.sqlalchemyTeamInvitationRepository import SqlAlchemyTeamInvitationRepository
from app.infrastructure.repositories.sqlalchemyTeamMemberRepository import TeamMemberRepository
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
    engine = create_database_engine(resolved_settings)
    application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.session_dependency = infrastructure_get_session
    application.state.token_verifier = create_token_verifier(resolved_settings)
    application.state.user_service_factory = user_service_factory
    application.state.team_service_factory = team_service_factory
    application.state.project_service_factory = project_service_factory
    application.state.team_invitation_service_factory = team_invitation_service_factory
    for router in routers:
        application.include_router(router)
    application.state.mail_sender = create_mail_sender(resolved_settings)
    application.include_router(health_router)
    return application


def user_service_factory(session: AsyncSession) -> UserService:
    return UserService(SqlAlchemyUserRepository(session))


def team_service_factory(session: AsyncSession) -> TeamService:
    return TeamService(SqlAlchemyTeamRepository(session))


def project_service_factory(session: AsyncSession) -> ProjectService:
    return ProjectService(
        SqlAlchemyProjectRepository(session),
        SqlAlchemyTeamRepository(session),
    )

def team_invitation_service_factory(session: AsyncSession) -> TeamInvitationService:
    return TeamInvitationService(SqlAlchemyTeamInvitationRepository(session), SqlAlchemyTeamRepository(session), SqlAlchemyTeamMemberRepository(session))

def team_member_repository_factory(session: AsyncSession) -> TeamMemberRepository:
    return SqlAlchemyTeamMemberRepository(session)

app = create_app()
