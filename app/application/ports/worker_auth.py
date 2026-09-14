from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkerAuthVerifier(Protocol):
    def verify(self, supplied_credential: str) -> bool: ...
