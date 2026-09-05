from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.team import router as team_router
from app.api.routes.user import router as user_router

routers: list[APIRouter] = [health_router, user_router, team_router]

__all__ = ["routers"]

