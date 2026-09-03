from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class HealthStatus:
    status: Literal["ok"] = "ok"
