import math
from uuid import UUID

from app.domain.session_workflow.entities.report import (
    Evaluation,
    EvidenceReference,
    Report,
    ScoreComponent,
)
from app.domain.session_workflow.enums.report import ScoreLabel, ScoreStatus
from app.domain.session_workflow.exceptions import (
    DuplicatePresenterFeedbackError,
    InvalidEvidenceReferenceError,
    InvalidScoreRangeError,
    InvalidScoreWeightError,
    MissingPresenterFeedbackError,
    QAWeightError,
    ReportValidationError,
    RubricMismatchError,
)

QA_DIMENSION_NAMES = {"qa", "qa_quality", "q_and_a", "q&a", "questions_and_answers"}


def calculate_display_score(normalized_score: float) -> int:
    if not (0.0 <= normalized_score <= 1.0):
        raise InvalidScoreRangeError(f"Normalized score {normalized_score} out of range [0.0, 1.0]")
    return max(0, min(100, int(normalized_score * 100 + 0.5)))


def score_to_label(display_score: int) -> ScoreLabel:
    if not (0 <= display_score <= 100):
        raise InvalidScoreRangeError(f"Display score {display_score} out of range [0, 100]")
    if display_score < 40:
        return ScoreLabel.NEEDS_WORK
    if display_score < 60:
        return ScoreLabel.DEVELOPING
    if display_score < 80:
        return ScoreLabel.GOOD
    return ScoreLabel.STRONG


def validate_score_components(components: list[ScoreComponent]) -> None:
    if not components:
        raise ReportValidationError("Score components list cannot be empty.")

    total_weight = sum(c.configured_weight for c in components)
    if not math.isclose(total_weight, 1.0, rel_tol=1e-4, abs_tol=1e-4):
        raise InvalidScoreWeightError(
            f"Score component weights must total 1.0 (100%), got {total_weight}"
        )

    qa_components = [c for c in components if c.dimension.lower() in QA_DIMENSION_NAMES]
    if not qa_components:
        raise QAWeightError("Q&A rubric dimension is missing from score components.")
    qa_weight = sum(c.configured_weight for c in qa_components)
    if not math.isclose(qa_weight, 0.20, rel_tol=1e-4, abs_tol=1e-4):
        raise QAWeightError(
            f"Q&A dimension must contribute exactly 0.20 (20%) of the total score, got {qa_weight}"
        )

    for c in components:
        if c.status == ScoreStatus.SCORED:
            if c.normalized_score is None or not (0.0 <= c.normalized_score <= 1.0):
                raise InvalidScoreRangeError(
                    f"Component '{c.dimension}' normalized_score {c.normalized_score} out of bounds"
                )
            expected_display = calculate_display_score(c.normalized_score)
            if c.display_score is not None and c.display_score != expected_display:
                msg = (
                    f"Component '{c.dimension}' display_score {c.display_score} "
                    f"!= expected {expected_display}"
                )
                raise InvalidScoreRangeError(msg)
            expected_label = score_to_label(expected_display)
            c.label = expected_label
            if not c.evidence_ids:
                raise InvalidEvidenceReferenceError(
                    f"Scored component '{c.dimension}' must have at least one evidence reference."
                )
            if not c.rationale or not c.rationale.strip():
                raise ReportValidationError(
                    f"Scored component '{c.dimension}' must have a non-empty rationale."
                )
        elif c.status == ScoreStatus.NOT_EVALUATED and (
            not c.limitation_code or not c.limitation_code.strip()
        ):
            raise ReportValidationError(
                f"Unscored component '{c.dimension}' must specify a limitation_code."
            )


def validate_evidence_references(references: list[EvidenceReference]) -> None:
    for ref in references:
        if not ref.id or not ref.id.strip():
            raise InvalidEvidenceReferenceError("Evidence reference id cannot be empty.")
        if not ref.source:
            raise InvalidEvidenceReferenceError(
                f"Evidence reference '{ref.id}' source cannot be empty."
            )


def validate_evaluation_and_report(
    *,
    evaluation: Evaluation,
    report: Report,
    expected_rubric_id: str,
    expected_rubric_version: int,
    mapped_user_ids: set[UUID],
) -> None:
    if evaluation.rubric_id != expected_rubric_id:
        raise RubricMismatchError(
            f"Evaluation rubric_id '{evaluation.rubric_id}' != session '{expected_rubric_id}'"
        )
    if evaluation.rubric_version != expected_rubric_version:
        raise RubricMismatchError(
            f"Rubric version {evaluation.rubric_version} != expected {expected_rubric_version}"
        )

    validate_score_components(evaluation.components)
    validate_score_components(report.score_components)

    if not (0.0 <= evaluation.overall_score <= 1.0):
        raise InvalidScoreRangeError(
            f"Overall evaluation score {evaluation.overall_score} out of bounds"
        )
    if not (0.0 <= report.overall_score <= 1.0):
        raise InvalidScoreRangeError(f"Overall report score {report.overall_score} out of bounds")

    if (
        not report.team_feedback.summary or not report.team_feedback.summary.strip()
    ) and not report.team_feedback.limitations:
        raise ReportValidationError("Team feedback must contain a summary or a limitation.")

    # Presenter mapping verification
    eval_user_ids = [m.user_id for m in evaluation.member_feedback]
    rep_user_ids = [m.user_id for m in report.member_feedback]

    if len(eval_user_ids) != len(set(eval_user_ids)):
        raise DuplicatePresenterFeedbackError(
            "Evaluation contains duplicate member feedback entries."
        )
    if len(rep_user_ids) != len(set(rep_user_ids)):
        raise DuplicatePresenterFeedbackError("Report contains duplicate member feedback entries.")

    eval_id_set = set(eval_user_ids)
    rep_id_set = set(rep_user_ids)

    missing_in_eval = mapped_user_ids - eval_id_set
    if missing_in_eval:
        raise MissingPresenterFeedbackError(
            f"Evaluation member feedback missing mapped presenter(s): {missing_in_eval}"
        )

    missing_in_rep = mapped_user_ids - rep_id_set
    if missing_in_rep:
        raise MissingPresenterFeedbackError(
            f"Report member feedback missing mapped presenter(s): {missing_in_rep}"
        )

    for member in report.member_feedback:
        if not member.display_name or not member.display_name.strip():
            raise ReportValidationError(
                f"Member feedback for {member.user_id} has empty display_name."
            )
        if not member.speaker_labels:
            raise ReportValidationError(
                f"Member feedback for {member.user_id} has no speaker_labels."
            )
        if not member.summary or not member.summary.strip():
            has_limitation = False
            for comp in member.delivery_components:
                if comp.status == ScoreStatus.NOT_EVALUATED and comp.limitation_code:
                    has_limitation = True
                    break
            if not has_limitation:
                raise ReportValidationError(
                    f"Member feedback for {member.user_id} must include a summary or limitation."
                )
