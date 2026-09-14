import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from app.application.ai_job_contracts import AIJobQueueMessage, AIJobType
from app.application.ports.ai_queue import (
    AIJobQueueAccepted,
    AIJobQueueError,
    AIJobQueuePort,
    AIJobQueueTemporaryFailure,
    AIQueueAccepted,
    AIQueueError,
    AIQueuePort,
    AIQueueTemporaryFailure,
)
from tests.support import FakeAIJobQueue, RecordingAIJobQueue

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "ai"


def _load_fixture(filename: str) -> dict[str, object]:

    path = FIXTURES_DIR / filename
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def test_ai_queue_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        AIJobQueuePort()  # type: ignore[abstract]


def test_ai_queue_aliases() -> None:
    assert AIQueuePort is AIJobQueuePort
    assert AIJobQueueTemporaryFailure is AIQueueTemporaryFailure
    assert AIJobQueueError is AIQueueError
    assert issubclass(AIQueueTemporaryFailure, AIQueueError)
    assert issubclass(AIQueueError, Exception)
    assert AIQueueAccepted is AIJobQueueAccepted


def test_ai_queue_temporary_failure_attributes() -> None:
    cause = RuntimeError("network dropped")
    err = AIQueueTemporaryFailure(
        "Service unreachable",
        retry_after_seconds=5.0,
        cause=cause,
    )
    assert str(err) == "Service unreachable"
    assert err.message == "Service unreachable"
    assert err.retry_after_seconds == 5.0
    assert err.cause is cause


def test_ai_job_queue_accepted_dataclass() -> None:
    now = datetime.now(UTC)
    receipt = AIJobQueueAccepted(
        job_id="01J00000000000000000000001",
        enqueued_at=now,
        message_id="msg-123",
        trace_id="trc-456",
    )
    assert receipt.job_id == "01J00000000000000000000001"
    assert receipt.enqueued_at == now
    assert receipt.message_id == "msg-123"
    assert receipt.trace_id == "trc-456"
    assert bool(receipt) is True


@pytest.mark.anyio
async def test_fake_ai_job_queue_successful_enqueue() -> None:
    fake = FakeAIJobQueue()
    assert isinstance(fake, AIJobQueuePort)
    assert fake.count == 0
    assert fake.last_message is None

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    receipt = await fake.enqueue(envelope)

    assert isinstance(receipt, AIJobQueueAccepted)
    assert receipt.job_id == str(envelope.job_id)
    assert receipt.trace_id == envelope.trace_id
    assert receipt.message_id is not None
    assert fake.count == 1
    assert fake.last_message == envelope
    assert fake.last_envelope == envelope
    assert fake.messages == [envelope]
    assert fake.enqueued_messages == [envelope]


@pytest.mark.anyio
async def test_fake_ai_job_queue_publish_alias() -> None:
    fake = RecordingAIJobQueue()
    data = _load_fixture("job_analyze_answer_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    receipt = await fake.publish(envelope)

    assert receipt.job_id == str(envelope.job_id)
    assert fake.count == 1
    assert fake.last_message == envelope


@pytest.mark.anyio
async def test_fake_ai_job_queue_accepts_named_args() -> None:
    fake = FakeAIJobQueue()
    data1 = _load_fixture("job_generate_report_valid.json")
    env1 = AIJobQueueMessage.model_validate(data1)

    receipt1 = await fake.enqueue(message=env1)
    assert receipt1.job_id == str(env1.job_id)

    data2 = _load_fixture("job_erase_ai_data_valid.json")
    env2 = AIJobQueueMessage.model_validate(data2)

    receipt2 = await fake.enqueue(envelope=env2)
    assert receipt2.job_id == str(env2.job_id)

    assert fake.count == 2


@pytest.mark.anyio
async def test_fake_ai_job_queue_rejects_missing_envelope() -> None:
    fake = FakeAIJobQueue()
    with pytest.raises(ValueError, match="Must provide message or envelope"):
        await fake.enqueue()


@pytest.mark.anyio
async def test_fake_ai_job_queue_accepts_dict_envelope() -> None:
    fake = FakeAIJobQueue()
    data = _load_fixture("job_analyze_session_valid.json")

    receipt = await fake.enqueue(data)  # type: ignore[arg-type]
    assert receipt.job_id == data["job_id"]
    assert fake.count == 1
    assert isinstance(fake.last_message, AIJobQueueMessage)


@pytest.mark.anyio
async def test_fake_ai_job_queue_inspection_helpers() -> None:
    fake = FakeAIJobQueue()
    session_data = _load_fixture("job_analyze_session_valid.json")
    answer_data = _load_fixture("job_analyze_answer_valid.json")

    session_msg = AIJobQueueMessage.model_validate(session_data)
    answer_msg = AIJobQueueMessage.model_validate(answer_data)

    await fake.enqueue(session_msg)
    await fake.enqueue(answer_msg)

    assert fake.count == 2
    assert fake.get_by_job_id(session_msg.job_id) == session_msg
    assert fake.get_by_job_id(answer_msg.job_id) == answer_msg
    assert fake.get_by_job_id("non-existent") is None

    session_jobs = fake.filter_by_job_type(AIJobType.ANALYZE_SESSION)
    assert session_jobs == [session_msg]

    answer_jobs = fake.filter_by_job_type("analyze_answer")
    assert answer_jobs == [answer_msg]

    payloads = fake.get_payloads()
    assert len(payloads) == 2
    assert payloads[0] == session_msg.payload
    assert payloads[1] == answer_msg.payload


@pytest.mark.anyio
async def test_fake_ai_job_queue_persistent_failure() -> None:
    fake = FakeAIJobQueue()
    fake.simulate_failure = True

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    with pytest.raises(AIQueueTemporaryFailure) as exc_info:
        await fake.enqueue(envelope)

    assert "Simulated AI queue temporary failure" in str(exc_info.value)
    assert fake.count == 0


@pytest.mark.anyio
async def test_fake_ai_job_queue_transient_failure_flag() -> None:
    fake = FakeAIJobQueue()
    fake.transient_failure = True

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    with pytest.raises(AIQueueTemporaryFailure):
        await fake.enqueue(envelope)

    assert fake.count == 0


@pytest.mark.anyio
async def test_fake_ai_job_queue_custom_exception() -> None:
    fake = FakeAIJobQueue()
    custom_exc = AIQueueTemporaryFailure("Custom redis connection timeout", retry_after_seconds=3.0)
    fake.simulate_failure = True
    fake.failure_to_raise = custom_exc

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    with pytest.raises(AIQueueTemporaryFailure) as exc_info:
        await fake.enqueue(envelope)

    assert exc_info.value is custom_exc
    assert exc_info.value.retry_after_seconds == 3.0


@pytest.mark.anyio
async def test_fake_ai_job_queue_fail_next_n_times() -> None:
    fake = FakeAIJobQueue()
    fake.fail_next(count=2)

    data = _load_fixture("job_analyze_session_valid.json")
    envelope = AIJobQueueMessage.model_validate(data)

    # First attempt: fails
    with pytest.raises(AIQueueTemporaryFailure):
        await fake.enqueue(envelope)

    # Second attempt: fails
    with pytest.raises(AIQueueTemporaryFailure):
        await fake.enqueue(envelope)

    # Third attempt: succeeds
    receipt = await fake.enqueue(envelope)
    assert receipt.job_id == str(envelope.job_id)
    assert fake.count == 1


@pytest.mark.anyio
async def test_fake_ai_job_queue_conditional_failure() -> None:
    fake = FakeAIJobQueue()
    fake.fail_if(lambda msg: getattr(msg, "job_type", None) == AIJobType.ANALYZE_ANSWER)

    session_msg = AIJobQueueMessage.model_validate(_load_fixture("job_analyze_session_valid.json"))
    answer_msg = AIJobQueueMessage.model_validate(_load_fixture("job_analyze_answer_valid.json"))

    # Session message succeeds
    receipt = await fake.enqueue(session_msg)
    assert receipt.job_id == str(session_msg.job_id)

    # Answer message fails
    with pytest.raises(AIQueueTemporaryFailure):
        await fake.enqueue(answer_msg)

    assert fake.count == 1
    assert fake.messages == [session_msg]


@pytest.mark.anyio
async def test_fake_ai_job_queue_clear_and_reset() -> None:
    fake = FakeAIJobQueue()
    fake.simulate_failure = True
    fake.fail_next(5)

    fake.clear()

    assert fake.count == 0
    assert fake.simulate_failure is False
    assert fake.failures_remaining == 0

    envelope = AIJobQueueMessage.model_validate(_load_fixture("job_analyze_session_valid.json"))
    receipt = await fake.enqueue(envelope)
    assert receipt.job_id == str(envelope.job_id)
    assert fake.count == 1

    fake.reset()
    assert fake.count == 0
