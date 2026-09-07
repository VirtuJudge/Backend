from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import routers
from app.api.routes.health import router as health_router
from app.application.services.assetStore import AssetStore
from app.application.services.projectService import ProjectService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.infrastructure.auth.provider import create_token_verifier
from app.infrastructure.database import create_database_engine
from app.infrastructure.database import get_session as infrastructure_get_session
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.mail import create_mail_sender
from app.infrastructure.media.ffmpegVerifier import FFmpegMediaVerifier
from app.infrastructure.repositories.sqlalchemyAssetRepository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemyProjectRepository import SqlAlchemyProjectRepository
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository
from app.infrastructure.settings import Settings
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage


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
    application.state.asset_store_factory = lambda session: asset_store_factory(
        session, resolved_settings
    )
    for router in routers:
        application.include_router(router)
    application.state.mail_sender = create_mail_sender(resolved_settings)
    application.include_router(health_router)

    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from fastapi.requests import Request
    from fastapi.responses import JSONResponse

    @application.exception_handler(RequestValidationError)
    async def asset_request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if "/assets" in request.url.path:
            return JSONResponse(
                status_code=422,
                content={
                    "type": "https://docs.virtujudge.org/problems/validation-failed",
                    "title": "Validation failed",
                    "status": 422,
                    "detail": "The request parameters failed validation.",
                    "instance": request.url.path,
                    "code": "validation_failed",
                },
                media_type="application/problem+json",
            )
        return await request_validation_exception_handler(request, exc)

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


def asset_store_factory(session: AsyncSession, settings: Settings) -> AssetStore:
    return AssetStore(
        repository=SqlAlchemyAssetRepository(session),
        storage=S3ObjectStorage(settings),
        document_verifier=DocumentVerifier(),
        media_verifier=FFmpegMediaVerifier(),
        upload_ttl_seconds=settings.object_storage_upload_url_ttl_seconds,
        download_ttl_seconds=settings.object_storage_download_url_ttl_seconds,
    )


app = create_app()
