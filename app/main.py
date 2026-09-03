from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
    application.include_router(health_router)
    return application


app = create_app()
