from enum import Enum

class QuestionStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    SKIPPED = "skipped"