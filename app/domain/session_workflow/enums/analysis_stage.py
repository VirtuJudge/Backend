from enum import Enum

class AnalysisStage(str, Enum):
    TRANSCRIPTION = "transcription"
    DIARIZATION = "diarization"
    DOCUMENT_ANALYSIS = "document_analysis"
    SCORING = "scoring"
    REPORT = "report"