from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.infrastructure.mail import create_mail_sender
from app.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    application = FastAPI(title=resolved_settings.app_name)
    application.state.mail_sender = create_mail_sender(resolved_settings)
    application.include_router(health_router)
    return application


app = create_app()
