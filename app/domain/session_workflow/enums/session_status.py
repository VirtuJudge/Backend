from enum import StrEnum


class SessionStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ANALYZING = "analyzing"
    FAILED = "failed"
    QUESTIONS_READY = "questions_ready"
    QUESTIONS_IN_PROGRESS = "questions_in_progress"
    REPORT_GENERATING = "report_generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
