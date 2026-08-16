"""Validated one-minute candle storage for the Cycle entry filter."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Tuple

from .exceptions import DomainValidationError


@dataclass(frozen=True)
class MinuteCandle:
    """One UTC-aligned one-minute OHLC candle."""

    symbol: str
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    is_closed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise DomainValidationError("symbol must be a non-empty string")
        if not isinstance(self.opened_at, datetime):
            raise DomainValidationError("opened_at must be a datetime")
        if self.opened_at.tzinfo is None or self.opened_at.utcoffset() is None:
            raise DomainValidationError("opened_at must be timezone-aware")
        utc_open = self.opened_at.astimezone(timezone.utc)
        if utc_open.second != 0 or utc_open.microsecond != 0:
            raise DomainValidationError("opened_at must be aligned to a minute")
        for name in ("open", "high", "low", "close"):
            _require_positive_decimal(name, getattr(self, name))
        if self.high < max(self.open, self.close):
            raise DomainValidationError("high cannot be below open or close")
        if self.low > min(self.open, self.close):
            raise DomainValidationError("low cannot be above open or close")
        if not isinstance(self.is_closed, bool):
            raise DomainValidationError("is_closed must be boolean")

    @property
    def range(self) -> Decimal:
        """Return exact HIGH minus LOW without float conversion."""
        return self.high - self.low


class MarketDataStore:
    """Build and retain the previous closed and current forming 1m candles."""

    def __init__(self, symbol: str, logger: Optional[logging.Logger] = None) -> None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise DomainValidationError("symbol must be a non-empty string")
        self._symbol = symbol
        self._logger = logger or logging.getLogger(__name__)
        self._previous: Optional[MinuteCandle] = None
        self._current: Optional[MinuteCandle] = None
        self._lock = threading.RLock()

    def ingest_price(self, *, price: Decimal, observed_at: datetime) -> MinuteCandle:
        """Apply one confirmed market price to its UTC minute candle."""
        _require_positive_decimal("price", price)
        minute = _minute_start(observed_at)
        with self._lock:
            if self._current is not None and minute < self._current.opened_at:
                raise DomainValidationError(
                    "out-of-order price belongs to an older minute"
                )
            if self._current is None or minute > self._current.opened_at:
                if self._current is not None:
                    self._previous = replace(self._current, is_closed=True)
                self._current = MinuteCandle(
                    symbol=self._symbol,
                    opened_at=minute,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    is_closed=False,
                )
            else:
                self._current = replace(
                    self._current,
                    high=max(self._current.high, price),
                    low=min(self._current.low, price),
                    close=price,
                )
            self._logger.debug(
                "Market price added to minute candle",
                extra={"symbol": self._symbol, "range": str(self._current.range)},
            )
            return self._current

    def ingest_closed_candle(self, candle: MinuteCandle) -> None:
        """Seed or replace the last broker-confirmed fully closed candle."""
        if not isinstance(candle, MinuteCandle):
            raise DomainValidationError("candle must be a MinuteCandle")
        if candle.symbol != self._symbol:
            raise DomainValidationError("candle symbol does not match store symbol")
        if not candle.is_closed:
            raise DomainValidationError("seed candle must be closed")
        with self._lock:
            if (
                self._current is not None
                and candle.opened_at >= self._current.opened_at
            ):
                raise DomainValidationError("closed candle must precede current candle")
            self._previous = candle

    def candles(self) -> Tuple[Optional[MinuteCandle], Optional[MinuteCandle]]:
        """Return `(previous_closed, current_forming)` atomically."""
        with self._lock:
            return self._previous, self._current

    def is_fresh(self, now: datetime, *, maximum_age: timedelta) -> bool:
        """Report whether the current candle has not exceeded an allowed age."""
        current_time = _as_utc(now)
        if not isinstance(maximum_age, timedelta) or maximum_age <= timedelta(0):
            raise DomainValidationError("maximum_age must be a positive timedelta")
        with self._lock:
            if self._current is None:
                return False
            return (
                current_time
                < self._current.opened_at + timedelta(minutes=1) + maximum_age
            )


def _minute_start(value: datetime) -> datetime:
    utc_value = _as_utc(value)
    return utc_value.replace(second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_positive_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{name} must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise DomainValidationError(f"{name} must be finite and positive")
