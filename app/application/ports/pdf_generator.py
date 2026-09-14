from typing import Protocol

from app.domain.session_workflow.entities.report import Report


class PDFGeneratorPort(Protocol):
    def render_report_pdf(self, report: Report) -> bytes: ...
