import io
from datetime import UTC, datetime
from uuid import uuid4

from pypdf import PdfReader

from app.domain.session_workflow.entities.report import (
    FeedbackSection,
    Finding,
    MemberFeedback,
    Report,
    ScoreComponent,
)
from app.domain.session_workflow.enums.report import (
    FindingKind,
    ScoreLabel,
    ScoreStatus,
)
from app.infrastructure.pdf.pdf_generator import PyPdfReportGenerator


def _build_test_report(member_count: int = 2) -> Report:
    components = [
        ScoreComponent(
            dimension="delivery",
            status=ScoreStatus.SCORED,
            configured_weight=0.25,
            normalized_score=0.80,
            display_score=80,
            label=ScoreLabel.STRONG,
            evidence_ids=["ev_speech_01"],
            rationale="Clear vocal delivery and good eye contact.",
        ),
        ScoreComponent(
            dimension="content",
            status=ScoreStatus.SCORED,
            configured_weight=0.25,
            normalized_score=0.75,
            display_score=75,
            label=ScoreLabel.GOOD,
            evidence_ids=["ev_slide_01"],
            rationale="Comprehensive market analysis.",
        ),
        ScoreComponent(
            dimension="structure",
            status=ScoreStatus.SCORED,
            configured_weight=0.15,
            normalized_score=0.70,
            display_score=70,
            label=ScoreLabel.GOOD,
            evidence_ids=["ev_slide_02"],
            rationale="Logical progression.",
        ),
        ScoreComponent(
            dimension="visuals",
            status=ScoreStatus.SCORED,
            configured_weight=0.15,
            normalized_score=0.80,
            display_score=80,
            label=ScoreLabel.STRONG,
            evidence_ids=["ev_slide_03"],
            rationale="Clean slides.",
        ),
        ScoreComponent(
            dimension="qa",
            status=ScoreStatus.SCORED,
            configured_weight=0.20,
            normalized_score=0.75,
            display_score=75,
            label=ScoreLabel.GOOD,
            evidence_ids=["ev_qa_01"],
            rationale="Direct answers to judge inquiries.",
        ),
    ]

    team_feedback = FeedbackSection(
        summary="Solid team coordination and confident pitch delivery.",
        strengths=[
            Finding(
                id="f_t_01",
                kind=FindingKind.STRENGTH,
                title="Unified Narrative",
                detail="Seamless transitions between speakers.",
                evidence_ids=["ev_speech_01"],
                rubric_dimension="structure",
            )
        ],
        improvements=[
            Finding(
                id="f_t_02",
                kind=FindingKind.IMPROVEMENT,
                title="Clarify IP Moat",
                detail="Provide deeper defensibility specifics.",
                recommendation="Add architecture slide explaining proprietary algorithms.",
                evidence_ids=["ev_slide_02"],
                rubric_dimension="content",
            )
        ],
        score_components=components,
    )

    members: list[MemberFeedback] = []
    for i in range(member_count):
        uid = uuid4()
        members.append(
            MemberFeedback(
                user_id=uid,
                display_name=f"Presenter {chr(65 + i)} (CEO)",
                speaker_labels=[f"SPEAKER_{i:02d}"],
                summary=f"Presenter {chr(65 + i)} handled problem statement and market size.",
                strengths=[
                    Finding(
                        id=f"f_m_{i}_01",
                        kind=FindingKind.STRENGTH,
                        title="Vocal Projection",
                        detail="Maintained strong presence and steady pacing.",
                        evidence_ids=["ev_speech_01"],
                        rubric_dimension="delivery",
                    )
                ],
                improvements=[
                    Finding(
                        id=f"f_m_{i}_02",
                        kind=FindingKind.IMPROVEMENT,
                        title="Handle Skepticism",
                        detail="Keep answers concise when probed on CAC metrics.",
                        evidence_ids=["ev_qa_01"],
                        rubric_dimension="qa",
                    )
                ],
                delivery_components=[components[0]],
            )
        )

    return Report(
        id=uuid4(),
        practice_session_id=uuid4(),
        evaluation_id=uuid4(),
        title="Acme Corp Seed Pitch Evaluation",
        executive_summary="Acme Corp delivered a persuasive pitch with clear unit economics.",
        overall_score=0.76,
        score_components=components,
        team_feedback=team_feedback,
        member_feedback=members,
        transcript_timeline=[],
        document_alignment=[],
        qa_review=[],
        recommendations=[
            "Refine go-to-market slide",
            "Prepare backup slides for pilot metrics",
        ],
        limitations=[
            {"code": "audio_compression", "message": "Minor compression artifacts in Q&A"}
        ],
        generated_at=datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC),
        updated_at=datetime(2026, 9, 14, 15, 30, 0, tzinfo=UTC),
    )


def test_render_report_pdf_valid_structure() -> None:
    report = _build_test_report(member_count=2)
    generator = PyPdfReportGenerator()

    pdf_bytes = generator.render_report_pdf(report)

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")

    # Strict parser validation
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    assert len(reader.pages) >= 1

    extracted_text = "\n".join(page.extract_text() for page in reader.pages)

    # Core sections check
    assert "VIRTUJUDGE EVALUATION REPORT" in extracted_text
    assert "Acme Corp Seed Pitch Evaluation" in extracted_text
    assert "Overall Performance Score: 76 / 100" in extracted_text
    assert "QA (Weight: 20%)" in extracted_text
    assert "Direct answers to judge inquiries." in extracted_text
    assert "Solid team coordination and confident pitch delivery." in extracted_text
    assert "Presenter A (CEO)" in extracted_text
    assert "Presenter B (CEO)" in extracted_text
    assert "Refine go-to-market slide" in extracted_text
    assert "[audio_compression]" in extracted_text


def test_render_report_pdf_handles_multi_page_budget() -> None:
    # 6 presenters forces the layout across multiple pages
    report = _build_test_report(member_count=6)
    generator = PyPdfReportGenerator()

    pdf_bytes = generator.render_report_pdf(report)
    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)

    # Should safely paginate across at least 2 pages without errors
    assert len(reader.pages) >= 2

    # Every page should be extractable
    for idx, page in enumerate(reader.pages):
        text = page.extract_text()
        assert len(text.strip()) > 0
        if idx > 0:
            assert f"Page {idx + 1}" in text


def test_render_report_pdf_escapes_special_characters() -> None:
    report = _build_test_report(member_count=1)
    report.title = "Special (Parentheses) & Backslash \\ Test -- “Quotes” & ‘Apostrophe’"
    report.executive_summary = "Presents (nested (parens)) and \\backslashes\\ without crashing."

    generator = PyPdfReportGenerator()
    pdf_bytes = generator.render_report_pdf(report)

    reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
    assert len(reader.pages) >= 1
    extracted_text = reader.pages[0].extract_text()
    assert "Special (Parentheses)" in extracted_text
