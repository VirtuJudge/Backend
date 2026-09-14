from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.session_workflow.entities.report import Evaluation, Report, ReportExport


class ReportRepository(ABC):
    @abstractmethod
    async def save_evaluation(self, evaluation: Evaluation) -> None:
        pass

    @abstractmethod
    async def get_evaluation(self, evaluation_id: UUID) -> Evaluation | None:
        pass

    @abstractmethod
    async def get_evaluation_by_session(self, session_id: UUID) -> Evaluation | None:
        pass

    @abstractmethod
    async def save_report(self, report: Report) -> None:
        pass

    @abstractmethod
    async def get_report(self, report_id: UUID) -> Report | None:
        pass

    @abstractmethod
    async def get_report_by_session(self, session_id: UUID) -> Report | None:
        pass

    @abstractmethod
    async def save_report_export(self, export: ReportExport) -> None:
        pass

    @abstractmethod
    async def get_report_export(self, export_id: UUID) -> ReportExport | None:
        pass

    @abstractmethod
    async def update_report_export(self, export: ReportExport) -> None:
        pass
