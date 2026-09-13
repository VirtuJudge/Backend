import contextlib
import json
import re
import subprocess
import sys
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, field_validator

from app.application.ai_job_contracts import (
    AIJobQueueMessage,
    AIWorkerUpdate,
    AIWorkerUpdateStatus,
    parse_queue_message,
    parse_worker_update,
)
from scripts.ai_contract import (
    ContextualValidationError,
    collect_errors,
    run_contract_checks,
    validate_contextual_rules,
    validate_schema_meta,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "contracts" / "schemas"
MANIFEST_PATH = REPO_ROOT / "contracts" / "fixtures" / "ai" / "manifest.json"

SHA256_HEX_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


class ConsumerJobType(StrEnum):
    ANALYZE_SESSION = "analyze_session"
    ANALYZE_ANSWER = "analyze_answer"
    GENERATE_REPORT = "generate_report"
    ERASE_AI_DATA = "erase_ai_data"


class ConsumerUpdateStatus(StrEnum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConsumerArtifactRef(BaseModel):
    artifact_id: str
    object_key: str
    checksum: str
    schema_version: int | None = None

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, v: str) -> str:
        if not SHA256_HEX_RE.match(v):
            raise ValueError("checksum must match 'sha256:' followed by 64 hex characters")
        return v


class ConsumerAssetInput(BaseModel):
    artifact_id: str
    object_key: str
    checksum: str
    media_type: str
    duration_ms: int | None = None

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, v: str) -> str:
        if not v.startswith("sha256:"):
            raise ValueError("checksum must start with 'sha256:'")
        return v


class ConsumerAudioAssetInput(ConsumerAssetInput):
    duration_ms: int


class ConsumerRubricRef(BaseModel):
    rubric_id: str
    version: int


class ConsumerSpeakerMapping(BaseModel):
    speaker_label: str
    user_id: str
    display_name: str


class ConsumerPrimaryQuestion(BaseModel):
    candidate_id: str
    text: str
    reason: str
    rubric_dimension: str
    evidence_ids: list[str] = Field(default_factory=list)


class ConsumerFollowUpQuestion(BaseModel):
    text: str
    reason: str
    rubric_dimension: str
    evidence_ids: list[str] = Field(default_factory=list)


class ConsumerLimitation(BaseModel):
    code: str
    scope: str
    message: str
    affected_dimensions: list[str] = Field(default_factory=list)


class ConsumerAnalyzeSessionPayload(BaseModel):
    presentation: ConsumerAssetInput
    supporting_documents: list[ConsumerAssetInput] = Field(default_factory=list)
    rubric: ConsumerRubricRef
    requested_capabilities: list[str] = Field(default_factory=list)


class ConsumerAnalyzeAnswerPayload(BaseModel):
    qa_round_id: str
    question_id: str
    answer_id: str
    answered_by: str
    audio: ConsumerAudioAssetInput
    remaining_follow_ups: int


class ConsumerGenerateReportPayload(BaseModel):
    report_id: str
    analysis_artifact: ConsumerArtifactRef
    qa_artifact: ConsumerArtifactRef
    speaker_mappings: list[ConsumerSpeakerMapping] = Field(default_factory=list)


class ConsumerEraseAIDataPayload(BaseModel):
    erasure_request_id: str
    scope: Literal["asset", "practice_session", "project", "team"]
    scope_id: str


class ConsumerSessionAnalysisCompleted(BaseModel):
    analysis_artifact: ConsumerArtifactRef
    primary_questions: list[ConsumerPrimaryQuestion]
    speaker_labels: list[str] = Field(default_factory=list)
    limitations: list[ConsumerLimitation] = Field(default_factory=list)

    @field_validator("primary_questions")
    @classmethod
    def validate_three_primary_questions(
        cls, v: list[ConsumerPrimaryQuestion]
    ) -> list[ConsumerPrimaryQuestion]:
        if len(v) != 3:
            raise ValueError(f"primary_questions must contain exactly 3 items, got {len(v)}")
        return v


class ConsumerAnswerAnalysisCompleted(BaseModel):
    answer_id: str
    transcript_artifact_id: str
    assessment_artifact_id: str
    follow_up: ConsumerFollowUpQuestion | None = None


class ConsumerReportCompleted(BaseModel):
    evaluation_artifact: ConsumerArtifactRef
    report_artifact: ConsumerArtifactRef
    member_feedback_user_ids: list[str] = Field(default_factory=list)
    limitations: list[ConsumerLimitation] = Field(default_factory=list)


class ConsumerErasureCompleted(BaseModel):
    erasure_request_id: str
    deleted_records: int = Field(ge=0)
    deleted_objects: int = Field(ge=0)


class ConsumerStartedPayload(BaseModel):
    pipeline_version: str


class ConsumerProgressPayload(BaseModel):
    stage: str
    progress: float = Field(ge=0.0, le=1.0)
    message: str


class ConsumerFailedPayload(BaseModel):
    stage: str
    code: str
    retryable: bool
    attempts: int = Field(ge=0)
    message: str


class ConsumerQueueMessage(BaseModel):
    schema_version: int = 1
    job_id: str
    job_type: ConsumerJobType
    practice_session_id: str
    analysis_attempt: int = 1
    created_at: datetime
    trace_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConsumerWorkerUpdate(BaseModel):
    schema_version: int = 1
    sequence: int
    status: ConsumerUpdateStatus
    occurred_at: datetime
    trace_id: str
    payload: Any = Field(default_factory=dict)


def load_manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def load_schema(schema_rel_path: str) -> dict[str, Any]:
    schema_file = REPO_ROOT / schema_rel_path
    return cast(dict[str, Any], json.loads(schema_file.read_text(encoding="utf-8")))


MANIFEST_DATA = load_manifest()
FIXTURES = MANIFEST_DATA.get("fixtures", [])
VALID_FIXTURES = [f for f in FIXTURES if f["valid"]]
BREAKING_FIXTURES = [f for f in FIXTURES if not f["valid"]]


@pytest.mark.parametrize("schema_file", sorted(SCHEMAS_DIR.glob("*.json")))
def test_schemas_conform_to_draft_2020_12_meta_schema(schema_file: Path) -> None:
    validate_schema_meta(schema_file)


def test_manifest_covers_all_quality_plan_categories() -> None:
    categories = {f["category"] for f in FIXTURES}
    expected = {
        "valid_job",
        "valid_completed_update",
        "valid_lifecycle_update",
        "missing_required",
        "unknown_enum",
        "ancestry_mismatch",
        "stale_attempt",
        "duplicate_sequence",
        "trace_mismatch",
        "invalid_resource",
        "empty_evidence",
        "question_count",
        "semantic_invariant",
    }
    assert expected.issubset(categories)


@pytest.mark.parametrize("fixture_def", VALID_FIXTURES, ids=[f["id"] for f in VALID_FIXTURES])
def test_valid_fixtures_pass_contract_validation(fixture_def: dict[str, Any]) -> None:
    fixture_path = REPO_ROOT / fixture_def["path"]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    schema = load_schema(fixture_def["schema"])

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(data)

    if fixture_def.get("validation_level") == "contextual":
        validate_contextual_rules(data, fixture_def.get("context", {}))


@pytest.mark.parametrize("fixture_def", VALID_FIXTURES, ids=[f["id"] for f in VALID_FIXTURES])
def test_valid_fixtures_parse_into_consumer_pydantic_models(fixture_def: dict[str, Any]) -> None:
    fixture_path = REPO_ROOT / fixture_def["path"]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    if "ai_job" in fixture_def["schema"]:
        msg = ConsumerQueueMessage.model_validate(data)
        if msg.job_type == ConsumerJobType.ANALYZE_SESSION:
            ConsumerAnalyzeSessionPayload.model_validate(msg.payload)
        elif msg.job_type == ConsumerJobType.ANALYZE_ANSWER:
            ConsumerAnalyzeAnswerPayload.model_validate(msg.payload)
        elif msg.job_type == ConsumerJobType.GENERATE_REPORT:
            ConsumerGenerateReportPayload.model_validate(msg.payload)
        elif msg.job_type == ConsumerJobType.ERASE_AI_DATA:
            ConsumerEraseAIDataPayload.model_validate(msg.payload)
        else:
            pytest.fail(f"Unhandled job_type: {msg.job_type}")
    elif "ai_worker_update" in fixture_def["schema"]:
        update = ConsumerWorkerUpdate.model_validate(data)
        if update.status == ConsumerUpdateStatus.STARTED:
            ConsumerStartedPayload.model_validate(update.payload)
        elif update.status == ConsumerUpdateStatus.PROGRESS:
            ConsumerProgressPayload.model_validate(update.payload)
        elif update.status == ConsumerUpdateStatus.FAILED:
            ConsumerFailedPayload.model_validate(update.payload)
        elif update.status == ConsumerUpdateStatus.CANCELLED:
            assert update.payload == {}
        elif update.status == ConsumerUpdateStatus.COMPLETED:
            completed_matches = []
            for model_cls in (
                ConsumerSessionAnalysisCompleted,
                ConsumerAnswerAnalysisCompleted,
                ConsumerReportCompleted,
                ConsumerErasureCompleted,
            ):
                with contextlib.suppress(Exception):
                    completed_matches.append(model_cls.model_validate(update.payload))
            assert len(completed_matches) == 1, (
                f"Expected payload to parse into exactly 1 completed shape, "
                f"parsed into: {len(completed_matches)}"
            )
        else:
            pytest.fail(f"Unhandled update status: {update.status}")


@pytest.mark.parametrize("fixture_def", VALID_FIXTURES, ids=[f["id"] for f in VALID_FIXTURES])
def test_valid_fixtures_parse_into_backend_runtime_models(fixture_def: dict[str, Any]) -> None:
    fixture_path = REPO_ROOT / fixture_def["path"]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    if "ai_job" in fixture_def["schema"]:
        msg = AIJobQueueMessage.model_validate(data)
        typed_msg = parse_queue_message(data)
        assert msg.job_type == typed_msg.job_type
    elif "ai_worker_update" in fixture_def["schema"]:
        update = AIWorkerUpdate.model_validate(data)
        if update.status == AIWorkerUpdateStatus.COMPLETED:
            res_type = fixture_def["result_type"]
            typed_update = parse_worker_update(data, job_type=res_type)
            assert typed_update.status == AIWorkerUpdateStatus.COMPLETED
        else:
            typed_update = parse_worker_update(data)
            assert typed_update.status == update.status


@pytest.mark.parametrize("fixture_def", BREAKING_FIXTURES, ids=[f["id"] for f in BREAKING_FIXTURES])
def test_breaking_fixtures_fail_with_expected_diagnostics(fixture_def: dict[str, Any]) -> None:
    fixture_path = REPO_ROOT / fixture_def["path"]
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    schema = load_schema(fixture_def["schema"])

    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validation_level = fixture_def.get("validation_level", "schema")

    expected_error = fixture_def.get("expected_error", {})
    expected_path = expected_error.get("path")
    expected_message = expected_error.get("message")

    if validation_level == "schema":
        errors = list(validator.iter_errors(data))
        assert len(errors) > 0, f"Expected validation failure for {fixture_def['id']}, but passed"

        all_errors: list[tuple[str, str]] = []
        for err in errors:
            all_errors.extend(collect_errors(err))

        matching_errors = all_errors
        if expected_path is not None:
            matching_errors = [
                (p, m)
                for p, m in matching_errors
                if p == expected_path or p.startswith(expected_path)
            ]
        if expected_message is not None:
            matching_errors = [
                (p, m) for p, m in matching_errors if expected_message.lower() in m.lower()
            ]

        assert len(matching_errors) > 0, (
            f"{fixture_def['id']}: expected error at path '{expected_path}' "
            f"matching '{expected_message}', but actual errors were: {all_errors[:3]}"
        )
    elif validation_level == "contextual":
        validator.validate(data)
        with pytest.raises(ContextualValidationError) as exc_info:
            validate_contextual_rules(data, fixture_def.get("context", {}))
        if expected_path is not None:
            assert exc_info.value.path == expected_path
        if expected_message is not None:
            assert expected_message.lower() in exc_info.value.message.lower()


def test_smoke_producer_payload_conforms_to_job_schema() -> None:
    smoke_payload = {
        "schema_version": 1,
        "job_id": "01J00000000000000000000001",
        "job_type": "analyze_session",
        "practice_session_id": "01J00000000000000000000001",
        "analysis_attempt": 1,
        "created_at": "2026-09-10T12:00:00Z",
        "trace_id": "01J00000000000000000000001",
        "payload": {
            "presentation": {
                "artifact_id": "01J00000000000000000000001",
                "object_key": "raw/smoke_presentation.mp4",
                "checksum": "sha256:" + "0" * 64,
                "media_type": "video/mp4",
            },
            "supporting_documents": [],
            "rubric": {
                "rubric_id": "rubric-default",
                "version": 1,
            },
            "requested_capabilities": [],
        },
    }
    schema = load_schema("contracts/schemas/ai_job.schema.json")
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(smoke_payload)


def test_smoke_consumer_worker_update_conforms_to_schema() -> None:
    worker_update = {
        "schema_version": 1,
        "sequence": 1,
        "status": "completed",
        "occurred_at": "2026-09-10T12:00:00Z",
        "trace_id": "01J00000000000000000000001",
        "payload": {
            "analysis_artifact": {
                "artifact_id": "01J00000000000000000000001",
                "object_key": "ai/session/analysis.json",
                "checksum": "sha256:" + "0" * 64,
            },
            "primary_questions": [
                {
                    "candidate_id": "1",
                    "text": "Question 1?",
                    "reason": "Reason 1",
                    "rubric_dimension": "dim1",
                    "evidence_ids": ["ev1"],
                },
                {
                    "candidate_id": "2",
                    "text": "Question 2?",
                    "reason": "Reason 2",
                    "rubric_dimension": "dim2",
                    "evidence_ids": ["ev2"],
                },
                {
                    "candidate_id": "3",
                    "text": "Question 3?",
                    "reason": "Reason 3",
                    "rubric_dimension": "dim3",
                    "evidence_ids": ["ev3"],
                },
            ],
        },
    }
    schema = load_schema("contracts/schemas/ai_worker_update.schema.json")
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(worker_update)


def test_cli_runner_succeeds_in_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ai_contract.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert result.returncode == 0
    assert "AI contract fixtures passed" in result.stdout


def test_cli_runner_fails_on_corrupt_fixture(tmp_path: Path) -> None:
    schemas_dir = REPO_ROOT / "contracts" / "schemas"
    bad_manifest = tmp_path / "manifest.json"
    bad_fixture = tmp_path / "bad.json"
    bad_fixture.write_text(json.dumps({"schema_version": 1, "invalid": True}))

    bad_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fixtures": [
                    {
                        "id": "broken",
                        "path": str(bad_fixture.relative_to(tmp_path)),
                        "schema": "contracts/schemas/ai_job.schema.json",
                        "category": "missing_required_field",
                        "valid": True,
                        "validation_level": "schema",
                    }
                ],
            }
        )
    )

    ok, messages = run_contract_checks(schemas_dir, bad_manifest, tmp_path)
    assert not ok
    assert any("[FAIL]" in m for m in messages)
