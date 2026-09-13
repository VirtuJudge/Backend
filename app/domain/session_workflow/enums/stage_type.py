from enum import StrEnum


class StageType(StrEnum):
    TRANSCRIPTION = "transcription"
    DIARIZATION = "diarization"
    DOCUMENT_ANALYSIS = "document_analysis"
    SCORING = "scoring"
    REPORT = "report"
