import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import routers
from app.api.routes.health import router as health_router
from app.application.services.assetStore import AssetStore
from app.application.services.projectService import ProjectService
from app.application.services.teamInvitationService import TeamInvitationService
from app.application.services.teamService import TeamService
from app.application.services.userService import UserService
from app.infrastructure.auth.provider import create_token_verifier
from app.infrastructure.database import create_database_engine
from app.infrastructure.database import get_session as infrastructure_get_session
from app.infrastructure.documents.document_verifier import DocumentVerifier
from app.infrastructure.mail import create_mail_sender
from app.infrastructure.media.ffmpegVerifier import FFmpegMediaVerifier
from app.infrastructure.redis.rate_limiter import RedisRateLimiter
from app.infrastructure.repositories.sqlalchemyAssetRepository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemyInvitationResendKeyRepository import (
    SqlalchemyInvitationResendKeyRepository,
)
from app.infrastructure.repositories.sqlalchemyProjectRepository import SqlAlchemyProjectRepository
from app.infrastructure.repositories.sqlalchemyTeamInvitationRepository import (
    SqlAlchemyTeamInvitationRepository,
)
from app.infrastructure.repositories.sqlalchemyTeamMemberRepository import (
    SqlAlchemyTeamMemberRepository,
    TeamMemberRepository,
)
from app.infrastructure.repositories.sqlalchemyTeamRepository import SqlAlchemyTeamRepository
from app.infrastructure.repositories.sqlalchemyUserRepositories import SqlAlchemyUserRepository
from app.infrastructure.storage.s3ObjectStorage import S3ObjectStorage
from app.settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    engine = create_database_engine(resolved_settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    cleanup_enabled = (
        resolved_settings.asset_cleanup_enabled
        if resolved_settings.asset_cleanup_enabled is not None
        else (resolved_settings.app_env != "test")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cleanup_task: asyncio.Task[None] | None = None
        if cleanup_enabled:

            async def _cleanup_loop() -> None:
                while True:
                    try:
                        await asyncio.sleep(resolved_settings.asset_cleanup_interval_seconds)
                        async with session_factory() as session:
                            factory = getattr(app.state, "asset_store_factory", None)
                            store = (
                                factory(session)
                                if factory is not None
                                else asset_store_factory(session, resolved_settings)
                            )
                            await store.cleanup_abandoned_uploads(
                                batch_size=resolved_settings.asset_cleanup_batch_size,
                                retention_seconds=resolved_settings.asset_cleanup_retention_seconds,
                                lease_seconds=resolved_settings.asset_cleanup_lease_seconds,
                                tombstone_delay_seconds=resolved_settings.asset_cleanup_tombstone_delay_seconds,
                            )
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:
                        logger.warning(
                            "Periodic asset cleanup encountered error: %s",
                            type(exc).__name__,
                        )

            cleanup_task = asyncio.create_task(_cleanup_loop())

        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup_task

            await engine.dispose()

    application = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    application.state.session_factory = session_factory
    application.state.session_dependency = infrastructure_get_session
    application.state.token_verifier = create_token_verifier(resolved_settings)
    application.state.user_service_factory = user_service_factory
    application.state.team_service_factory = team_service_factory
    application.state.project_service_factory = project_service_factory
    application.state.asset_store_factory = lambda session: asset_store_factory(
        session, resolved_settings
    )
    application.state.team_invitation_service_factory = team_invitation_service_factory
    redis = Redis.from_url(
        resolved_settings.redis_url,
        decode_responses=True,
    )
    application.state.redis = RedisRateLimiter(redis)
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


def team_invitation_service_factory(session: AsyncSession) -> TeamInvitationService:
    return TeamInvitationService(
        SqlAlchemyTeamInvitationRepository(session),
        SqlAlchemyTeamRepository(session),
        SqlAlchemyTeamMemberRepository(session),
        SqlAlchemyUserRepository(session),
        SqlalchemyInvitationResendKeyRepository(session),
    )


def team_member_repository_factory(session: AsyncSession) -> TeamMemberRepository:
    return SqlAlchemyTeamMemberRepository(session)


app = create_app()
