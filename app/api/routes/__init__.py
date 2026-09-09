from fastapi import APIRouter

from app.api.routes.asset import router as asset_router
from app.api.routes.health import router as health_router
from app.api.routes.project import router as project_router
from app.api.routes.team import router as team_router
from app.api.routes.user import router as user_router
from app.api.routes.invitation import router as invitation_router

routers: list[APIRouter] = [health_router, user_router, team_router, project_router, asset_router,invitation_router]

__all__ = ["routers"]
