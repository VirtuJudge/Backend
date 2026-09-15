import io
import textwrap

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.ports.pdf_generator import PDFGeneratorPort
from app.domain.session_workflow.entities.report import Report


def _escape_pdf_text(text: str) -> str:
    sanitized = (
        str(text)
        .replace("—", "--")
        .replace("–", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("…", "...")
    )
    sanitized = sanitized.encode("latin-1", "replace").decode("latin-1")
    return sanitized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class PyPdfReportGenerator(PDFGeneratorPort):
    PAGE_WIDTH = 595.0
    PAGE_HEIGHT = 842.0
    MARGIN_LEFT = 50.0
    MARGIN_RIGHT = 545.0
    MARGIN_TOP = 790.0
    MARGIN_BOTTOM = 50.0

    def render_report_pdf(self, report: Report) -> bytes:
        writer = PdfWriter()
        pages_content: list[list[str]] = []
        current_commands: list[str] = []
        current_y = self.MARGIN_TOP
        page_num = 1

        def start_new_page() -> None:
            nonlocal current_commands, current_y, page_num
            if current_commands:
                pages_content.append(current_commands)
            current_commands = []
            page_num += 1
            current_y = self.MARGIN_TOP
            header_text = f"VirtuJudge Pitch Report - Page {page_num}"
            current_commands.append(
                f"BT /F3 8 Tf {self.MARGIN_LEFT} {self.MARGIN_TOP} Td ({header_text}) Tj ET"
            )
            current_y -= 25.0

        def ensure_space(height: float) -> None:
            nonlocal current_y
            if current_y - height < self.MARGIN_BOTTOM:
                start_new_page()

        def add_line(
            text: str,
            font: str = "/F1",
            size: float = 10.0,
            line_height: float = 14.0,
            x_offset: float = 0.0,
        ) -> None:
            nonlocal current_y
            ensure_space(line_height)
            escaped = _escape_pdf_text(text)
            x = self.MARGIN_LEFT + x_offset
            current_commands.append(f"BT {font} {size} Tf {x} {current_y} Td ({escaped}) Tj ET")
            current_y -= line_height

        def add_wrapped(
            text: str,
            font: str = "/F1",
            size: float = 10.0,
            line_height: float = 14.0,
            x_offset: float = 0.0,
            width: int = 80,
        ) -> None:
            for line in textwrap.wrap(text, width=width):
                add_line(line, font=font, size=size, line_height=line_height, x_offset=x_offset)

        def add_spacer(height: float = 10.0) -> None:
            nonlocal current_y
            ensure_space(height)
            current_y -= height

        def add_divider() -> None:
            nonlocal current_y
            ensure_space(10.0)
            current_y -= 4.0
            line_cmd = (
                f"0.8 w 0.7 0.7 0.7 RG {self.MARGIN_LEFT} {current_y} m "
                f"{self.MARGIN_RIGHT} {current_y} l S 0 0 0 RG"
            )
            current_commands.append(line_cmd)
            current_y -= 8.0

        # Title Block
        add_line("VIRTUJUDGE EVALUATION REPORT", font="/F2", size=18.0, line_height=24.0)
        add_line(report.title, font="/F2", size=13.0, line_height=18.0)
        add_spacer(4.0)
        meta_line = (
            f"Session ID: {report.practice_session_id}  |  Evaluation ID: {report.evaluation_id}"
        )
        add_line(meta_line, font="/F1", size=9.0, line_height=13.0)
        gen_line = f"Generated At: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        add_line(gen_line, font="/F1", size=9.0, line_height=13.0)
        add_divider()

        # Overall Score
        overall_display = int(round(report.overall_score * 100))
        add_line("1. Executive Summary & Overall Score", font="/F2", size=13.0, line_height=18.0)
        score_val = f"{report.overall_score:.2f}"
        score_msg = f"Overall Performance Score: {overall_display} / 100 ({score_val})"
        add_line(score_msg, font="/F2", size=11.0, line_height=16.0, x_offset=10.0)
        add_spacer(4.0)
        add_wrapped(
            report.executive_summary,
            font="/F1",
            size=10.0,
            line_height=14.0,
            x_offset=10.0,
            width=82,
        )
        add_spacer(8.0)

        # Rubric Dimension Components
        add_line("2. Rubric Dimension Breakdown", font="/F2", size=13.0, line_height=18.0)
        add_spacer(4.0)
        for comp in report.score_components:
            weight_pct = int(round(comp.configured_weight * 100))
            score_str = (
                f"{comp.display_score} / 100" if comp.display_score is not None else "Not Evaluated"
            )
            label_str = f"({comp.label.value})" if comp.label is not None else ""
            title_text = (
                f"- {comp.dimension.upper()} (Weight: {weight_pct}%) : {score_str} {label_str}"
            ).strip()
            add_line(title_text, font="/F2", size=10.0, line_height=15.0, x_offset=10.0)
            if comp.rationale:
                add_wrapped(
                    f"Rationale: {comp.rationale}",
                    font="/F1",
                    size=9.0,
                    line_height=13.0,
                    x_offset=20.0,
                    width=78,
                )
            if comp.evidence_ids:
                add_line(
                    f"Evidence: {', '.join(comp.evidence_ids)}",
                    font="/F3",
                    size=8.0,
                    line_height=12.0,
                    x_offset=20.0,
                )
            if comp.limitation_code:
                add_line(
                    f"Limitation: {comp.limitation_code}",
                    font="/F3",
                    size=8.0,
                    line_height=12.0,
                    x_offset=20.0,
                )
            add_spacer(3.0)
        add_spacer(6.0)

        # Whole-Team Feedback
        add_line("3. Whole-Team Feedback", font="/F2", size=13.0, line_height=18.0)
        add_spacer(4.0)
        if report.team_feedback.summary:
            add_wrapped(
                report.team_feedback.summary,
                font="/F1",
                size=10.0,
                line_height=14.0,
                x_offset=10.0,
                width=82,
            )
            add_spacer(4.0)

        if report.team_feedback.strengths:
            add_line("Key Strengths:", font="/F2", size=10.0, line_height=14.0, x_offset=10.0)
            for s in report.team_feedback.strengths:
                add_line(f"* {s.title}", font="/F2", size=9.0, line_height=13.0, x_offset=20.0)
                add_wrapped(
                    s.detail,
                    font="/F1",
                    size=9.0,
                    line_height=13.0,
                    x_offset=30.0,
                    width=75,
                )
                if s.rubric_dimension:
                    add_line(
                        f"Dimension: {s.rubric_dimension}",
                        font="/F3",
                        size=8.0,
                        line_height=11.0,
                        x_offset=30.0,
                    )
                add_spacer(2.0)

        if report.team_feedback.improvements:
            add_line(
                "Areas for Improvement:",
                font="/F2",
                size=10.0,
                line_height=14.0,
                x_offset=10.0,
            )
            for imp in report.team_feedback.improvements:
                add_line(f"* {imp.title}", font="/F2", size=9.0, line_height=13.0, x_offset=20.0)
                add_wrapped(
                    imp.detail,
                    font="/F1",
                    size=9.0,
                    line_height=13.0,
                    x_offset=30.0,
                    width=75,
                )
                if imp.recommendation:
                    add_wrapped(
                        f"Action: {imp.recommendation}",
                        font="/F3",
                        size=8.5,
                        line_height=12.0,
                        x_offset=30.0,
                        width=75,
                    )
                add_spacer(2.0)
        add_spacer(6.0)

        # Individual Presenter Sections
        add_line("4. Individual Presenter Evaluations", font="/F2", size=13.0, line_height=18.0)
        add_spacer(4.0)
        for idx, member in enumerate(report.member_feedback):
            speaker_labels = (
                ", ".join(member.speaker_labels) if member.speaker_labels else "Unassigned"
            )
            header = f"4.{idx + 1} {member.display_name} (Labels: {speaker_labels})"
            add_line(header, font="/F2", size=11.0, line_height=16.0, x_offset=10.0)
            add_line(
                f"User ID: {member.user_id}",
                font="/F3",
                size=8.0,
                line_height=11.0,
                x_offset=15.0,
            )
            if member.summary:
                add_wrapped(
                    member.summary,
                    font="/F1",
                    size=9.5,
                    line_height=13.5,
                    x_offset=15.0,
                    width=80,
                )
                add_spacer(3.0)

            if member.strengths:
                add_line(
                    "Presenter Strengths:",
                    font="/F2",
                    size=9.0,
                    line_height=13.0,
                    x_offset=15.0,
                )
                for st in member.strengths:
                    add_line(f"+ {st.title}", font="/F2", size=8.5, line_height=12.0, x_offset=25.0)
                    add_wrapped(
                        st.detail,
                        font="/F1",
                        size=8.5,
                        line_height=12.0,
                        x_offset=32.0,
                        width=73,
                    )
                    add_spacer(2.0)

            if member.improvements:
                add_line(
                    "Presenter Improvements:",
                    font="/F2",
                    size=9.0,
                    line_height=13.0,
                    x_offset=15.0,
                )
                for imp in member.improvements:
                    add_line(
                        f"- {imp.title}",
                        font="/F2",
                        size=8.5,
                        line_height=12.0,
                        x_offset=25.0,
                    )
                    add_wrapped(
                        imp.detail,
                        font="/F1",
                        size=8.5,
                        line_height=12.0,
                        x_offset=32.0,
                        width=73,
                    )
                    add_spacer(2.0)
            add_spacer(4.0)
        add_spacer(4.0)

        # Recommendations
        if report.recommendations:
            add_line("5. Recommendations", font="/F2", size=13.0, line_height=18.0)
            add_spacer(3.0)
            for rec in report.recommendations:
                add_wrapped(
                    f"- {rec}",
                    font="/F1",
                    size=9.5,
                    line_height=13.5,
                    x_offset=10.0,
                    width=80,
                )
            add_spacer(6.0)

        # Limitations
        if report.limitations:
            add_line("6. Limitations & Caveats", font="/F2", size=13.0, line_height=18.0)
            add_spacer(3.0)
            for lim in report.limitations:
                code = lim.get("code", "unknown")
                msg = lim.get("message", "")
                add_wrapped(
                    f"- [{code}] {msg}",
                    font="/F3",
                    size=9.0,
                    line_height=13.0,
                    x_offset=10.0,
                    width=80,
                )
            add_spacer(6.0)

        # Finalize last page
        if current_commands:
            pages_content.append(current_commands)

        font_dict = DictionaryObject(
            {
                NameObject("/F1"): DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                ),
                NameObject("/F2"): DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
                    }
                ),
                NameObject("/F3"): DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica-Oblique"),
                    }
                ),
            }
        )
        resources = DictionaryObject(
            {
                NameObject("/Font"): font_dict,
            }
        )

        for cmds in pages_content:
            page = writer.add_blank_page(width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT)
            page[NameObject("/Resources")] = resources
            stream = DecodedStreamObject()
            content_str = " \n".join(cmds)
            stream.set_data(content_str.encode("latin-1"))
            page[NameObject("/Contents")] = stream

        output_buffer = io.BytesIO()
        writer.write(output_buffer)
        return output_buffer.getvalue()
