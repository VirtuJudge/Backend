from fastapi import Depends, Request

from app.application.ports.ai_job_queue import AIJobQueuePort
from app.application.ports.session_notification import SessionNotificationPort
from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.application.session_workflow import SessionWorkflow

from .services import get_unit_of_work_repository


def get_session_workflow(
    request: Request,
    uow: UnitOfWork = Depends(get_unit_of_work_repository),
) -> SessionWorkflow:
    queue: AIJobQueuePort | None = getattr(request.app.state, "ai_job_queue", None)
    notifications: SessionNotificationPort | None = getattr(
        request.app.state, "session_notifications", None
    )
    return SessionWorkflow(
        uow=uow,
        queue=queue,
        notifications=notifications,
        trace_id=getattr(request.state, "correlation_id", "unknown"),
    )
