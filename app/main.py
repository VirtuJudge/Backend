from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.routes import routers
from app.infrastructure.database import create_database_engine
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
    engine = create_database_engine(resolved_settings)
    application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    for router in routers:
        application.include_router(router)
    return application


app = create_app()
