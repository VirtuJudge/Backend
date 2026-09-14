import json
import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import jsonschema
import pytest
from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.application.ai_job_contracts import AIJobQueueMessage
from app.application.ports.ai_queue import (
    AIJobQueueAccepted,
    AIJobQueuePort,
    AIQueueTemporaryFailure,
)
from app.infrastructure.queues.celery_ai_job_queue import (
    ALLOWED_ENVELOPE_FIELDS,
    CeleryAIJobQueue,
    CeleryAIQueue,
    create_celery_app,
)
from app.settings import Settings

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "schemas" / "ai_job.schema.json"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures" / "ai"


@pytest.fixture
def ai_job_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _load_fixture(filename: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8")))


def test_celery_ai_job_queue_implements_port() -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(celery_app=mock_celery)
    assert isinstance(queue, AIJobQueuePort)
    assert CeleryAIQueue is CeleryAIJobQueue


def test_celery_ai_job_queue_requires_app_or_broker_url() -> None:
    with pytest.raises(ValueError, match="Either celery_app or broker_url must be provided"):
        CeleryAIJobQueue()


def test_create_celery_app_defaults() -> None:
    app = create_celery_app("redis://localhost:6379/0")
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.result_serializer == "json"
    assert app.conf.task_default_queue == "ai_jobs"
    assert app.conf.task_ignore_result is True


def test_celery_ai_job_queue_from_settings() -> None:
    settings = Settings(
        celery_broker_url="redis://custom-host:6379/2",
        ai_worker_task_name="custom.worker.task",
        ai_worker_queue_name="custom_ai_jobs",
    )
    mock_app = MagicMock()
    queue = CeleryAIJobQueue.from_settings(settings, celery_app=mock_app)
    assert queue.task_name == "custom.worker.task"
    assert queue.queue_name == "custom_ai_jobs"
    assert "redis://custom-host:6379/2" not in repr(queue)
    assert "CeleryAIJobQueue" in repr(queue)


@pytest.mark.anyio
async def test_enqueue_preserves_stable_job_id_and_task_name(
    ai_job_schema: dict[str, Any],
) -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(
        celery_app=mock_celery,
        task_name="app.worker.process_job",
        queue_name="ai_jobs",
    )

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    receipt = await queue.enqueue(envelope)

    assert isinstance(receipt, AIJobQueueAccepted)
    assert receipt.job_id == data["job_id"]
    assert receipt.trace_id == data["trace_id"]
    assert receipt.enqueued_at is not None

    mock_celery.send_task.assert_called_once()
    call_kwargs = mock_celery.send_task.call_args.kwargs
    call_args = mock_celery.send_task.call_args.args

    # Check task name
    assert call_kwargs["name"] == "app.worker.process_job"
    # Check stable task_id matches job_id
    assert call_kwargs["task_id"] == data["job_id"]
    # Check queue routing
    assert call_kwargs["queue"] == "ai_jobs"

    # Check payload is passed in args[0]
    sent_dict = call_kwargs.get("args", call_args[1] if len(call_args) > 1 else None)[0]
    assert sent_dict["job_id"] == data["job_id"]
    assert sent_dict["job_type"] == "analyze_session"
    assert sent_dict["schema_version"] == 1

    # Verify conforming to authoritative JSON schema
    jsonschema.validate(instance=sent_dict, schema=ai_job_schema)


@pytest.mark.anyio
async def test_publish_alias_delegates_to_enqueue() -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(celery_app=mock_celery)

    data = _load_fixture("job_analyze_answer_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    receipt = await queue.publish(envelope)

    assert receipt.job_id == data["job_id"]
    mock_celery.send_task.assert_called_once()
    assert mock_celery.send_task.call_args.kwargs["task_id"] == data["job_id"]


@pytest.mark.anyio
async def test_avoids_embedding_backend_only_fields() -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(celery_app=mock_celery)

    data = _load_fixture("job_analyze_session_valid.json")
    data_with_backend_fields = dict(data)
    data_with_backend_fields["backend_status"] = "pending"
    data_with_backend_fields["cancel_requested"] = False
    data_with_backend_fields["team_id"] = "01JTEAM00000000000000000001"
    data_with_backend_fields["project_id"] = "01JPROJ00000000000000000002"
    data_with_backend_fields["internal_retry_count"] = 3

    await queue.enqueue(data_with_backend_fields)  # type: ignore[arg-type]

    sent_dict = mock_celery.send_task.call_args.kwargs["args"][0]
    assert set(sent_dict.keys()) == ALLOWED_ENVELOPE_FIELDS
    assert "backend_status" not in sent_dict
    assert "cancel_requested" not in sent_dict
    assert "team_id" not in sent_dict
    assert "project_id" not in sent_dict
    assert "internal_retry_count" not in sent_dict


@pytest.mark.anyio
async def test_custom_task_options_are_passed() -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(
        celery_app=mock_celery,
        task_options={"priority": 9, "time_limit": 600},
    )

    data = _load_fixture("job_erase_ai_data_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    await queue.enqueue(envelope)

    call_kwargs = mock_celery.send_task.call_args.kwargs
    assert call_kwargs["priority"] == 9
    assert call_kwargs["time_limit"] == 600
    assert call_kwargs["queue"] == "ai_jobs"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "broker_exc",
    [
        KombuOperationalError("Cannot connect to redis"),
        RedisConnectionError("Connection refused"),
        CeleryError("Task publish timeout"),
        TimeoutError("Timed out writing to broker socket"),
        OSError("Network unreachable"),
    ],
)
async def test_safe_failure_mapping(broker_exc: Exception) -> None:
    mock_celery = MagicMock()
    mock_celery.send_task.side_effect = broker_exc
    queue = CeleryAIJobQueue(celery_app=mock_celery)

    data = _load_fixture("job_generate_report_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    with pytest.raises(AIQueueTemporaryFailure) as exc_info:
        await queue.enqueue(envelope)

    assert isinstance(exc_info.value.cause, type(broker_exc))
    assert str(exc_info.value.cause) == str(broker_exc)
    assert data["job_id"] in str(exc_info.value)
    # Ensure raw secret details or sensitive payloads are not in message
    assert "payload" not in str(exc_info.value).lower()


@pytest.mark.anyio
async def test_never_logs_message_payload_or_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(celery_app=mock_celery)

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    with caplog.at_level(logging.DEBUG):
        await queue.enqueue(envelope)

    # Verify success log does not include payload
    log_text = caplog.text
    assert "raw/presentation.mp4" not in log_text
    assert "sha256:aaaaaaaaaaaaaaaa" not in log_text

    caplog.clear()

    # Now verify failure log does not include payload or credentials
    mock_celery.send_task.side_effect = KombuOperationalError("redis://:secret_pw@localhost:6379/0")

    with caplog.at_level(logging.DEBUG), pytest.raises(AIQueueTemporaryFailure):
        await queue.enqueue(envelope)

    failure_log = caplog.text
    assert "secret_pw" not in failure_log
    assert "raw/presentation.mp4" not in failure_log


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fixture_name",
    [
        "job_analyze_session_valid.json",
        "job_analyze_answer_valid.json",
        "job_generate_report_valid.json",
        "job_erase_ai_data_valid.json",
    ],
)
async def test_all_contract_job_types_enqueue_successfully(
    fixture_name: str,
    ai_job_schema: dict[str, Any],
) -> None:
    mock_celery = MagicMock()
    queue = CeleryAIJobQueue(celery_app=mock_celery)

    data = _load_fixture(fixture_name)
    envelope = AIJobQueueMessage.model_validate(data)

    receipt = await queue.enqueue(envelope)
    assert receipt.job_id == data["job_id"]

    sent_dict = mock_celery.send_task.call_args.kwargs["args"][0]
    jsonschema.validate(instance=sent_dict, schema=ai_job_schema)
