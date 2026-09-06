from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

<<<<<<< HEAD
from app.api.routes import routers
from app.application.services.projectService import ProjectService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.infrastructure.auth.provider import create_token_verifier
from app.infrastructure.database import create_database_engine
from app.infrastructure.database import get_session as infrastructure_get_session
from app.infrastructure.repositories.sqlalchemyProjectRepository import SqlAlchemyProjectRepository
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository
=======
from app.api.routes.health import router as health_router
from app.infrastructure.mail import create_mail_sender
>>>>>>> origin/main
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
<<<<<<< HEAD
    engine = create_database_engine(resolved_settings)
    application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    application.state.session_dependency = infrastructure_get_session
    application.state.token_verifier = create_token_verifier(resolved_settings)
    application.state.user_service_factory = user_service_factory
    application.state.team_service_factory = team_service_factory
    application.state.project_service_factory = project_service_factory
    for router in routers:
        application.include_router(router)
=======
    application.state.mail_sender = create_mail_sender(resolved_settings)
    application.include_router(health_router)
>>>>>>> origin/main
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


app = create_app()
