"""Domain exceptions raised by AI Impulse Trader."""


class FormulaValidationError(ValueError):
    """Raised when a formula receives an invalid trading value."""


class ConfigurationError(ValueError):
    """Raised when application configuration is missing or invalid."""


class ConfigurationStorageError(OSError):
    """Raised when configuration cannot be read or atomically persisted."""


class DomainValidationError(ValueError):
    """Raised when a domain model would violate a state invariant."""


class StateStorageError(OSError):
    """Raised when SQLite state cannot be initialized or persisted."""


class StateCorruptionError(ValueError):
    """Raised when persisted state cannot be decoded into valid models."""


class BrokerError(RuntimeError):
    """Base class for deterministic broker-adapter failures."""


class BrokerRejectedError(BrokerError):
    """Raised when the simulated or real broker rejects a request."""


class BrokerTimeoutError(BrokerError):
    """Raised when request outcome must be recovered by request ID."""

    def __init__(self, request_id: str, may_have_executed: bool) -> None:
        super().__init__(f"broker request timed out: {request_id}")
        self.request_id = request_id
        self.may_have_executed = may_have_executed


class BrokerAmbiguousOutcomeError(BrokerError):
    """Raised when a non-idempotent write may have reached the broker."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"broker write has an ambiguous outcome: {request_id}")
        self.request_id = request_id


class BrokerNotSupportedError(BrokerError):
    """Raised instead of guessing an operation absent from the broker contract."""
