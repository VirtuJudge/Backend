from typing import cast

from fastapi import Request

from app.settings import Settings


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)
