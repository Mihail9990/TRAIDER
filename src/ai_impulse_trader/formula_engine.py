"""Pure Decimal-based calculations for the trading strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .exceptions import FormulaValidationError


@dataclass(frozen=True)
class StopLevels:
    """Calculated stop-loss levels for the initial position pair."""

    long: Decimal
    short: Decimal


@dataclass(frozen=True)
class TakeProfitLevels:
    """Calculated take-profit levels for the LONG and SHORT positions."""

    long: Decimal
    short: Decimal


class FormulaEngine:
    """Calculate strategy values without broker or persistence side effects."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """Create an engine using the supplied logger or the module logger."""
        self._logger = logger or logging.getLogger(__name__)
        self._logger.debug("FormulaEngine initialized")

    def calculate_initial_stops(
        self,
        *,
        long_entry: Decimal,
        short_entry: Decimal,
        stop_distance: Decimal,
    ) -> StopLevels:
        """Return initial LONG and SHORT stop-loss levels."""
        self._logger.info("Calculating initial stop-loss levels")
        self._require_positive("long_entry", long_entry)
        self._require_positive("short_entry", short_entry)
        self._require_positive("stop_distance", stop_distance)

        long_stop = long_entry - stop_distance
        if long_stop <= 0:
            raise FormulaValidationError("long stop-loss must be positive")

        return StopLevels(long=long_stop, short=short_entry + stop_distance)

    def calculate_base_coverage(
        self,
        *,
        spread: Decimal,
        initial_long_close_commission: Decimal,
        initial_short_close_commission: Decimal,
        actual_initial_slippage: Decimal,
        target_profit: Decimal,
    ) -> Decimal:
        """Return the immutable cost and profit coverage for Scenario 1."""
        self._logger.info("Calculating base coverage")
        values = {
            "spread": spread,
            "initial_long_close_commission": initial_long_close_commission,
            "initial_short_close_commission": initial_short_close_commission,
            "actual_initial_slippage": actual_initial_slippage,
            "target_profit": target_profit,
        }
        for name, value in values.items():
            self._require_non_negative(name, value)

        coverage = sum(values.values(), start=Decimal("0"))
        if coverage <= 0:
            raise FormulaValidationError("base coverage must be positive")
        return coverage

    def calculate_initial_take_profits(
        self,
        *,
        long_stop: Decimal,
        short_stop: Decimal,
        base_coverage: Decimal,
    ) -> TakeProfitLevels:
        """Return initial take-profit levels derived from opposite stops."""
        self._logger.info("Calculating initial take-profit levels")
        self._require_positive("long_stop", long_stop)
        self._require_positive("short_stop", short_stop)
        self._require_positive("base_coverage", base_coverage)

        short_take_profit = long_stop - base_coverage
        if short_take_profit <= 0:
            raise FormulaValidationError("short take-profit must be positive")

        return TakeProfitLevels(
            long=short_stop + base_coverage,
            short=short_take_profit,
        )

    def calculate_reentry_cost(
        self,
        *,
        entry_stop_distance: Decimal,
        reentry_close_commission: Decimal,
        actual_reentry_slippage: Decimal,
    ) -> Decimal:
        """Return the cost increment produced by one confirmed Reentry."""
        self._logger.info("Calculating Reentry cost")
        self._require_positive("entry_stop_distance", entry_stop_distance)
        self._require_non_negative("reentry_close_commission", reentry_close_commission)
        self._require_non_negative("actual_reentry_slippage", actual_reentry_slippage)
        return entry_stop_distance + reentry_close_commission + actual_reentry_slippage

    def calculate_next_take_profits(
        self,
        *,
        saved_long_take_profit: Decimal,
        saved_short_take_profit: Decimal,
        reentry_cost: Decimal,
    ) -> TakeProfitLevels:
        """Apply one Reentry cost to the last broker-confirmed TP levels."""
        self._logger.info("Calculating next take-profit levels")
        self._require_positive("saved_long_take_profit", saved_long_take_profit)
        self._require_positive("saved_short_take_profit", saved_short_take_profit)
        self._require_positive("reentry_cost", reentry_cost)

        short_take_profit = saved_short_take_profit - reentry_cost
        if short_take_profit <= 0:
            raise FormulaValidationError("short take-profit must be positive")

        return TakeProfitLevels(
            long=saved_long_take_profit + reentry_cost,
            short=short_take_profit,
        )

    @staticmethod
    def _require_positive(name: str, value: Decimal) -> None:
        FormulaEngine._require_decimal(name, value)
        if value <= 0:
            raise FormulaValidationError(f"{name} must be positive")

    @staticmethod
    def _require_non_negative(name: str, value: Decimal) -> None:
        FormulaEngine._require_decimal(name, value)
        if value < 0:
            raise FormulaValidationError(f"{name} must be non-negative")

    @staticmethod
    def _require_decimal(name: str, value: Decimal) -> None:
        if not isinstance(value, Decimal):
            raise FormulaValidationError(f"{name} must be a Decimal")
        if not value.is_finite():
            raise FormulaValidationError(f"{name} must be finite")
