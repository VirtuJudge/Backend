class InvalidSessionStatusTransition(Exception):
    """Exception raised when an invalid transition between session statuses is attempted."""

    pass


class ManifestAlreadyFrozen(Exception):
    """Exception raised when attempting to freeze a manifest that is already frozen."""

    pass


class ConsentRequiredForSession(Exception):
    """Exception raised when a session requires consent but it has not been provided."""

    pass


class ConsentPolicyOutdated(Exception):
    """Exception raised when the consent policy is outdated."""

    pass


class UnverifiedAsset(Exception):
    """Exception raised when an asset is unverified."""

    pass


class InvalidAttemptState(Exception):
    """Exception raised when an invalid state is encountered in an analysis attempt."""

    pass


class AttemptAlreadyExists(Exception):
    pass


class SessionCancelled(Exception):
    """Exception raised when an operation is attempted on a cancelled session."""

    pass


class StaleEntityVersion(Exception):
    """Exception raised when an entity version is stale."""

    pass


class InvalidSpeakerLabel(Exception):
    """Exception raised when an invalid speaker label is encountered."""

    pass


class UnauthorizedSessionAction(Exception):
    """Exception raised when an unauthorized action is attempted on a session."""

    pass


class SessionNotFoundError(Exception):
    """Exception raised when a session is not found."""

    pass


class SessionPreconditionFailed(Exception):
    """Exception raised when a precondition for a session operation fails."""

    pass


class SessionNotReadyError(Exception):
    """Exception raised when a session is not in a ready state for the requested operation."""

    pass


class ConsentRequiredError(Exception):
    """Exception raised when consent is required for a session operation."""

    pass


class RetryNotAllowed(Exception):
    """Exception raised when the latest analysis attempt cannot be retried."""

    pass
