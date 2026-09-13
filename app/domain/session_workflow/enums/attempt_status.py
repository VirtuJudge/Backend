from enum import StrEnum


class AnalysisAttemptStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
