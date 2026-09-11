from enum import Enum


class AnalysisAttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"