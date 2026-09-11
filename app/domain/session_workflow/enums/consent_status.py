from enum import Enum

class ConsentStatus(str, Enum):
    ACCEPTED = "accepted"
    REVOKED = "revoked"