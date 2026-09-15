from uuid import UUID

from app.application.ports.session_practice.report_repository import ReportRepository
from app.domain.session_workflow.entities.report import Evaluation, Report, ReportExport


class FakeReportRepository(ReportRepository):
    def __init__(self) -> None:
        self.evaluations: dict[UUID, Evaluation] = {}
        self.reports: dict[UUID, Report] = {}
        self.exports: dict[UUID, ReportExport] = {}

    async def save_evaluation(self, evaluation: Evaluation) -> None:
        self.evaluations[evaluation.id] = evaluation

    async def get_evaluation(self, evaluation_id: UUID) -> Evaluation | None:
        return self.evaluations.get(evaluation_id)

    async def get_evaluation_by_session(self, session_id: UUID) -> Evaluation | None:
        for ev in self.evaluations.values():
            if ev.practice_session_id == session_id:
                return ev
        return None

    async def save_report(self, report: Report) -> None:
        self.reports[report.id] = report

    async def get_report(self, report_id: UUID) -> Report | None:
        return self.reports.get(report_id)

    async def get_report_by_session(self, session_id: UUID) -> Report | None:
        for rep in self.reports.values():
            if rep.practice_session_id == session_id:
                return rep
        return None

    async def save_report_export(self, export: ReportExport) -> None:
        self.exports[export.id] = export

    async def get_report_export(self, export_id: UUID) -> ReportExport | None:
        return self.exports.get(export_id)

    async def get_report_export_by_session(self, session_id: UUID) -> ReportExport | None:
        for export in sorted(self.exports.values(), key=lambda e: e.created_at, reverse=True):
            if export.practice_session_id == session_id:
                return export
        return None

    async def update_report_export(self, export: ReportExport) -> None:
        self.exports[export.id] = export
