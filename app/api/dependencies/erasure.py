from typing import Any, cast

from fastapi import Depends, Request

from app.api.dependencies.services import get_session
from app.application.erasure_workflow import ErasureWorkflow


def get_erasure_workflow(request: Request, session: Any = Depends(get_session)) -> ErasureWorkflow:
    return cast(ErasureWorkflow, request.app.state.erasure_workflow_factory(session))
