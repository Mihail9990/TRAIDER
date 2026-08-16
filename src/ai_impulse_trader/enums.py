"""Stable string enums used by persisted trading domain models."""

from enum import Enum


class StringEnum(str, Enum):
    """Python 3.9-compatible string enum with readable string conversion."""

    def __str__(self) -> str:
        return self.value


class Side(StringEnum):
    """Trading position direction."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(StringEnum):
    """Lifecycle state of a broker position."""

    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


class TriggerStatus(StringEnum):
    """Lifecycle state of a Reentry trigger order."""

    CREATE_REQUESTED = "CREATE_REQUESTED"
    WAITING = "WAITING"
    EXECUTED = "EXECUTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class CycleState(StringEnum):
    """Lifecycle state of one trading Cycle."""

    CREATED = "CREATED"
    WAIT_ENTRY = "WAIT_ENTRY"
    PREPARE = "PREPARE"
    OPENING = "OPENING"
    VERIFY_OPEN = "VERIFY_OPEN"
    RUNNING = "RUNNING"
    FINISHING = "FINISHING"
    FINISHED = "FINISHED"
    ARCHIVED = "ARCHIVED"
    RECOVERY = "RECOVERY"
    MANUAL_MODE = "MANUAL_MODE"
