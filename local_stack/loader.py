import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ProcessJobCallable = Callable[[Any, Any], Awaitable[Any]]


@dataclass(frozen=True)
class AIComponents:
    queue_message_cls: type[Any]
    process_job: ProcessJobCallable
    fake_pipeline_cls: type[Any]


def load_ai_components() -> AIComponents:
    return AIComponents(
        importlib.import_module("app.contracts").QueueMessage,
        importlib.import_module("app.worker").process_job,
        importlib.import_module("app.pipeline").FakePipeline,
    )
