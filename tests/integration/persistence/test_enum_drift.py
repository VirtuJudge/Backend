import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import Enum

from app.domain.session_workflow.enums.attempt_status import AnalysisAttemptStatus
from app.domain.session_workflow.enums.job_status import AnalysisJobStatus
from app.domain.session_workflow.enums.session_status import SessionStatus
from app.domain.session_workflow.enums.stage_status import StageStatus
from app.domain.session_workflow.enums.stage_type import StageType
from app.infrastructure.persistence.configurations import (
    AnalysisAttemptModel,
    AnalysisJobModel,
    AnalysisStageModel,
    PracticeSessionModel,
)


def _get_migration_module() -> Any:
    path = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "versions"
        / "ef143c2a901b_complete_session_workflow.py"
    )
    spec = importlib.util.spec_from_file_location("migration_complete_session_workflow", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_session_status_enum_matches_migration() -> None:
    col = PracticeSessionModel.__table__.columns["status"]
    assert isinstance(col.type, Enum)
    assert col.type.name == "sessionstatus"

    # Verify Enum(...).enums are all lowercase contract values
    expected_values = [e.value for e in SessionStatus]
    assert list(col.type.enums) == expected_values
    for val in col.type.enums:
        assert val == val.lower()


def test_analysis_attempt_status_enum_matches_migration() -> None:
    col = AnalysisAttemptModel.__table__.columns["status"]
    assert isinstance(col.type, Enum)
    assert col.type.name == "analysisattemptstatus"

    expected_values = [e.value for e in AnalysisAttemptStatus]
    assert list(col.type.enums) == expected_values
    for val in col.type.enums:
        assert val == val.lower()


def test_analysis_job_status_enum_matches_migration() -> None:
    col = AnalysisJobModel.__table__.columns["status"]
    assert isinstance(col.type, Enum)
    assert col.type.name == "analysisjobstatus"

    migration_mod = _get_migration_module()
    expected_values = [e.value for e in AnalysisJobStatus]
    assert list(col.type.enums) == expected_values
    assert list(migration_mod.analysisjobstatus_enum.enums) == expected_values
    for val in col.type.enums:
        assert val == val.lower()


def test_analysis_stage_type_and_status_match_migration() -> None:
    migration_mod = _get_migration_module()
    stage_col = AnalysisStageModel.__table__.columns["stage"]
    assert isinstance(stage_col.type, Enum)
    assert stage_col.type.name == "stagetype"

    expected_stages = [e.value for e in StageType]
    assert list(stage_col.type.enums) == expected_stages
    assert list(migration_mod.stagetype_enum.enums) == expected_stages
    for val in stage_col.type.enums:
        assert val == val.lower()

    status_col = AnalysisStageModel.__table__.columns["status"]
    assert isinstance(status_col.type, Enum)
    assert status_col.type.name == "stagestatus"

    expected_statuses = [e.value for e in StageStatus]
    assert list(status_col.type.enums) == expected_statuses
    assert list(migration_mod.stagestatus_enum.enums) == expected_statuses
    for val in status_col.type.enums:
        assert val == val.lower()
