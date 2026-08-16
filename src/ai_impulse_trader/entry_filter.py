"""Pure decision gate for the user-confirmed minute-candle entry rule."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .exceptions import DomainValidationError
from .market_data import MinuteCandle


@dataclass(frozen=True)
class EntryReadiness:
    """System invariants that must all permit a new Cycle."""

    broker_connected: bool
    market_data_fresh: bool
    settings_loaded: bool
    previous_cycle_finished: bool
    no_active_cycle: bool
    no_open_positions: bool
    no_active_triggers: bool
    no_active_orders: bool
    recovery_inactive: bool
    stop_after_current_cycle: bool

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, bool):
                raise DomainValidationError(f"{name} must be boolean")

    @property
    def permits_start(self) -> bool:
        """Return true only when every safety invariant permits entry."""
        return (
            self.broker_connected
            and self.market_data_fresh
            and self.settings_loaded
            and self.previous_cycle_finished
            and self.no_active_cycle
            and self.no_open_positions
            and self.no_active_triggers
            and self.no_active_orders
            and self.recovery_inactive
            and not self.stop_after_current_cycle
        )


@dataclass(frozen=True)
class EntryDecision:
    """Auditable result returned for every Entry Filter evaluation."""

    start_cycle: bool
    reason: str
    candle_source: Optional[str] = None
    candle_range: Optional[Decimal] = None


class EntryFilter:
    """Emit at most one START_CYCLE until explicitly reset after a Cycle."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._start_emitted = False
        self._lock = threading.RLock()

    def evaluate(
        self,
        *,
        threshold: Decimal,
        readiness: EntryReadiness,
        previous_candle: Optional[MinuteCandle],
        current_candle: Optional[MinuteCandle],
    ) -> EntryDecision:
        """Check the closed candle first, then the forming candle, using strict `>`."""
        _require_positive_decimal("threshold", threshold)
        if not isinstance(readiness, EntryReadiness):
            raise DomainValidationError("readiness must be EntryReadiness")
        _validate_candle_role("previous_candle", previous_candle, must_be_closed=True)
        _validate_candle_role("current_candle", current_candle, must_be_closed=False)
        if (
            previous_candle is not None
            and current_candle is not None
            and previous_candle.symbol != current_candle.symbol
        ):
            raise DomainValidationError("entry candles must have the same symbol")

        with self._lock:
            if self._start_emitted:
                return EntryDecision(False, "START_ALREADY_EMITTED")

            signal = _find_signal(threshold, previous_candle, current_candle)
            if signal is None:
                return EntryDecision(False, "RANGE_NOT_EXCEEDED")
            source, candle_range = signal
            if not readiness.permits_start:
                reason = (
                    "STOP_AFTER_CURRENT_CYCLE"
                    if readiness.stop_after_current_cycle
                    else "SYSTEM_NOT_READY"
                )
                return EntryDecision(False, reason, source, candle_range)

            self._start_emitted = True
            self._logger.info(
                "Minute candle permitted START_CYCLE",
                extra={"source": source, "range": str(candle_range)},
            )
            return EntryDecision(True, "START_CYCLE", source, candle_range)

    def reset_after_cycle(self) -> None:
        """Permit one new signal after the active Cycle has fully completed."""
        with self._lock:
            self._start_emitted = False

    @property
    def start_emitted(self) -> bool:
        """Expose the latch for monitoring and tests."""
        with self._lock:
            return self._start_emitted


def _find_signal(
    threshold: Decimal,
    previous: Optional[MinuteCandle],
    current: Optional[MinuteCandle],
) -> Optional[tuple]:
    if previous is not None and previous.range > threshold:
        return "PREVIOUS_CLOSED", previous.range
    if current is not None and current.range > threshold:
        return "CURRENT_FORMING", current.range
    return None


def _validate_candle_role(
    name: str, candle: Optional[MinuteCandle], *, must_be_closed: bool
) -> None:
    if candle is None:
        return
    if not isinstance(candle, MinuteCandle):
        raise DomainValidationError(f"{name} must be a MinuteCandle or None")
    if candle.is_closed is not must_be_closed:
        state = "closed" if must_be_closed else "forming"
        raise DomainValidationError(f"{name} must be {state}")


def _require_positive_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{name} must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise DomainValidationError(f"{name} must be finite and positive")
