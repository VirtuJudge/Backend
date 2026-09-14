from enum import StrEnum


class QARoundState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class QuestionKind(StrEnum):
    PRIMARY = "primary"
    FOLLOW_UP = "follow_up"


class QuestionState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class AnswerStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"
