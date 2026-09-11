from enum import Enum


class SessionStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ANALYZING = "analyzing"
    FAILED = "failed"
    QUESTIONS_READY = "questions_ready"
    QUESTIONS_IN_PROGRESS = "questions_in_progress"
    REPORT_GENERATING = "report_generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"