from enum import StrEnum


class ScoreLabel(StrEnum):
    NEEDS_WORK = "needs_work"
    DEVELOPING = "developing"
    GOOD = "good"
    STRONG = "strong"


class ScoreStatus(StrEnum):
    SCORED = "scored"
    NOT_EVALUATED = "not_evaluated"


class FindingKind(StrEnum):
    STRENGTH = "strength"
    IMPROVEMENT = "improvement"
    ALIGNMENT = "alignment"
    CONTRADICTION = "contradiction"
    OMISSION = "omission"
    OBSERVATION = "observation"


class ReportExportStatus(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class ReportExportFormat(StrEnum):
    PDF = "pdf"


class EvidenceType(StrEnum):
    TRANSCRIPT_SPAN = "transcript_span"
    VIDEO_INTERVAL = "video_interval"
    AUDIO_INTERVAL = "audio_interval"
    DOCUMENT_SPAN = "document_span"
    ANSWER_SPAN = "answer_span"
