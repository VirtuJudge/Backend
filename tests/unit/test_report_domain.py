from uuid import uuid4

import pytest

from app.domain.session_workflow.entities.report import (
    Evaluation,
    EvidenceReference,
    FeedbackSection,
    Finding,
    MemberFeedback,
    Report,
    ScoreComponent,
)
from app.domain.session_workflow.enums.report import (
    EvidenceType,
    FindingKind,
    ScoreLabel,
    ScoreStatus,
)
from app.domain.session_workflow.exceptions import (
    DuplicatePresenterFeedbackError,
    InvalidEvidenceReferenceError,
    InvalidScoreLabelError,
    InvalidScoreRangeError,
    InvalidScoreWeightError,
    MissingPresenterFeedbackError,
    QAWeightError,
    RubricMismatchError,
)
from app.domain.session_workflow.scoring import (
    calculate_display_score,
    score_to_label,
    validate_evaluation_and_report,
    validate_evidence_references,
    validate_score_components,
)


def _make_valid_components() -> list[ScoreComponent]:
    return [
        ScoreComponent(
            dimension="problem_solution",
            status=ScoreStatus.SCORED,
            configured_weight=0.40,
            normalized_score=0.85,
            display_score=85,
            label=ScoreLabel.STRONG,
            effective_weight=0.40,
            evidence_ids=["ev_1"],
            rationale="Clear articulation of the problem and value proposition.",
        ),
        ScoreComponent(
            dimension="business_model",
            status=ScoreStatus.SCORED,
            configured_weight=0.40,
            normalized_score=0.72,
            display_score=72,
            label=ScoreLabel.GOOD,
            effective_weight=0.40,
            evidence_ids=["ev_2"],
            rationale="Sound pricing structure and market size.",
        ),
        ScoreComponent(
            dimension="q_and_a",
            status=ScoreStatus.SCORED,
            configured_weight=0.20,
            normalized_score=0.90,
            display_score=90,
            label=ScoreLabel.STRONG,
            effective_weight=0.20,
            evidence_ids=["ev_3"],
            rationale="Concise and convincing answers.",
        ),
    ]


def test_score_to_label_and_display_score() -> None:
    assert calculate_display_score(0.0) == 0
    assert calculate_display_score(0.395) == 40
    assert calculate_display_score(0.742) == 74
    assert calculate_display_score(1.0) == 100

    with pytest.raises(InvalidScoreRangeError):
        calculate_display_score(-0.1)
    with pytest.raises(InvalidScoreRangeError):
        calculate_display_score(1.05)

    assert score_to_label(0) is ScoreLabel.NEEDS_WORK
    assert score_to_label(39) is ScoreLabel.NEEDS_WORK
    assert score_to_label(40) is ScoreLabel.DEVELOPING
    assert score_to_label(59) is ScoreLabel.DEVELOPING
    assert score_to_label(60) is ScoreLabel.GOOD
    assert score_to_label(79) is ScoreLabel.GOOD
    assert score_to_label(80) is ScoreLabel.STRONG
    assert score_to_label(100) is ScoreLabel.STRONG


def test_validate_score_components_weights_must_total_100() -> None:
    comps = _make_valid_components()
    comps[0].configured_weight = 0.50
    with pytest.raises(InvalidScoreWeightError):
        validate_score_components(comps)


def test_validate_score_components_qa_must_be_20_percent() -> None:
    comps = [
        ScoreComponent(
            dimension="problem_solution",
            status=ScoreStatus.SCORED,
            configured_weight=0.70,
            normalized_score=0.8,
            display_score=80,
            label=ScoreLabel.STRONG,
            evidence_ids=["ev_1"],
            rationale="Good.",
        ),
        ScoreComponent(
            dimension="q_and_a",
            status=ScoreStatus.SCORED,
            configured_weight=0.30,
            normalized_score=0.8,
            display_score=80,
            label=ScoreLabel.STRONG,
            evidence_ids=["ev_2"],
            rationale="Good.",
        ),
    ]
    with pytest.raises(QAWeightError):
        validate_score_components(comps)


def test_validate_score_components_missing_qa() -> None:
    comps = [
        ScoreComponent(
            dimension="delivery",
            status=ScoreStatus.SCORED,
            configured_weight=1.0,
            normalized_score=0.8,
            display_score=80,
            label=ScoreLabel.STRONG,
            evidence_ids=["ev_1"],
            rationale="Good.",
        ),
    ]
    with pytest.raises(QAWeightError):
        validate_score_components(comps)


def test_validate_score_components_invalid_label_mismatch() -> None:
    comps = _make_valid_components()
    comps[0].label = ScoreLabel.NEEDS_WORK
    with pytest.raises(InvalidScoreLabelError):
        validate_score_components(comps)


def test_validate_evidence_references() -> None:
    valid = [
        EvidenceReference(
            id="ev_1",
            type=EvidenceType.DOCUMENT_SPAN,
            source={"asset_id": "a1", "page": 2},
        )
    ]
    validate_evidence_references(valid)

    with pytest.raises(InvalidEvidenceReferenceError):
        validate_evidence_references(
            [
                EvidenceReference(
                    id="",
                    type=EvidenceType.DOCUMENT_SPAN,
                    source={"page": 1},
                )
            ]
        )

    with pytest.raises(InvalidEvidenceReferenceError):
        validate_evidence_references(
            [
                EvidenceReference(
                    id="ev_2",
                    type=EvidenceType.DOCUMENT_SPAN,
                    source={},
                )
            ]
        )


def test_validate_evaluation_and_report_presenter_mapping_and_rubric() -> None:
    session_id = uuid4()
    attempt_id = uuid4()
    round_id = uuid4()
    user_id_1 = uuid4()
    user_id_2 = uuid4()

    member_feedback = [
        MemberFeedback(
            user_id=user_id_1,
            display_name="Presenter 1",
            speaker_labels=["SPEAKER_00"],
            summary="Strong delivery.",
            strengths=[
                Finding(
                    id="f1",
                    kind=FindingKind.STRENGTH,
                    title="Pacing",
                    detail="Even pace.",
                    evidence_ids=["ev_1"],
                )
            ],
            improvements=[],
            delivery_components=[],
        ),
        MemberFeedback(
            user_id=user_id_2,
            display_name="Presenter 2",
            speaker_labels=["SPEAKER_01"],
            summary="Handled Q&A with clarity.",
            strengths=[],
            improvements=[],
            delivery_components=[],
        ),
    ]

    team_feedback = FeedbackSection(
        summary="Cohesive team presentation.",
        strengths=[],
        improvements=[],
    )

    eval_obj = Evaluation(
        id=uuid4(),
        practice_session_id=session_id,
        analysis_attempt_id=attempt_id,
        qa_round_id=round_id,
        rubric_id="startup_pitch",
        rubric_version=1,
        overall_score=0.82,
        components=_make_valid_components(),
        findings=[],
        team_feedback=team_feedback,
        member_feedback=member_feedback,
    )

    rep_obj = Report(
        id=uuid4(),
        practice_session_id=session_id,
        evaluation_id=eval_obj.id,
        title="Demo Pitch Report",
        executive_summary="Executive summary here.",
        overall_score=0.82,
        score_components=_make_valid_components(),
        team_feedback=team_feedback,
        member_feedback=member_feedback,
    )

    # Valid validation passes
    validate_evaluation_and_report(
        evaluation=eval_obj,
        report=rep_obj,
        expected_rubric_id="startup_pitch",
        expected_rubric_version=1,
        mapped_user_ids={user_id_1, user_id_2},
    )

    # Missing mapped presenter fails
    with pytest.raises(MissingPresenterFeedbackError):
        validate_evaluation_and_report(
            evaluation=eval_obj,
            report=rep_obj,
            expected_rubric_id="startup_pitch",
            expected_rubric_version=1,
            mapped_user_ids={user_id_1, user_id_2, uuid4()},
        )

    # Duplicate presenter fails
    rep_obj_duplicate = Report(
        id=uuid4(),
        practice_session_id=session_id,
        evaluation_id=eval_obj.id,
        title="Demo",
        executive_summary="Summary",
        overall_score=0.82,
        score_components=_make_valid_components(),
        team_feedback=team_feedback,
        member_feedback=[member_feedback[0], member_feedback[0]],
    )
    with pytest.raises(DuplicatePresenterFeedbackError):
        validate_evaluation_and_report(
            evaluation=eval_obj,
            report=rep_obj_duplicate,
            expected_rubric_id="startup_pitch",
            expected_rubric_version=1,
            mapped_user_ids={user_id_1},
        )

    # Rubric mismatch fails
    with pytest.raises(RubricMismatchError):
        validate_evaluation_and_report(
            evaluation=eval_obj,
            report=rep_obj,
            expected_rubric_id="other_rubric",
            expected_rubric_version=1,
            mapped_user_ids={user_id_1, user_id_2},
        )
