from fastapi import FastAPI

from app.api.routes import routers
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
    for router in routers:
        application.include_router(router, prefix="/api/v1")
    return application


app = create_app()
