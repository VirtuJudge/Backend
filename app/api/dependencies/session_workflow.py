from fastapi import Depends

from app.application.ports.session_practice.unit_of_work_repository import UnitOfWork
from app.application.session_workflow import SessionWorkflow

from .services import get_unit_of_work_repository


def get_session_workflow(
    uow: UnitOfWork = Depends(get_unit_of_work_repository),
) -> SessionWorkflow:
    return SessionWorkflow(uow=uow)
