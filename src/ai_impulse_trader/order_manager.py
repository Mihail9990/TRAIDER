"""Broker-neutral, idempotent ordering sequences for trading Cycles."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from .broker_gateway import BrokerConfirmation, BrokerGateway
from .enums import Side
from .exceptions import BrokerError, DomainValidationError
from .formula_engine import StopLevels, TakeProfitLevels


@dataclass(frozen=True)
class InitialPositionPair:
    """Confirmed LONG and SHORT opens belonging to one Cycle."""

    long: BrokerConfirmation
    short: BrokerConfirmation


@dataclass(frozen=True)
class PositionLevelConfirmations:
    """Confirmed SL and TP updates for one position."""

    stop_loss: BrokerConfirmation
    take_profit: BrokerConfirmation


@dataclass(frozen=True)
class InitialLevelSet:
    """All four confirmed protective levels of Scenario 1."""

    long: PositionLevelConfirmations
    short: PositionLevelConfirmations


@dataclass(frozen=True)
class ReentryLevelSet:
    """Confirmed protection for Reentry and TP replacement for its opposite."""

    reentry_stop_loss: BrokerConfirmation
    reentry_take_profit: BrokerConfirmation
    opposite_take_profit: BrokerConfirmation


@dataclass(frozen=True)
class ManualTakeoverConfirmations:
    """All broker confirmations produced while removing automated protection."""

    cancelled_triggers: Tuple[BrokerConfirmation, ...]
    removed_levels: Tuple[BrokerConfirmation, ...]


class OrderSequenceError(BrokerError):
    """Report a partially completed sequence that must enter Recovery."""

    def __init__(
        self,
        *,
        sequence: str,
        failed_step: str,
        completed_request_ids: Tuple[str, ...],
    ) -> None:
        super().__init__(f"{sequence} failed at {failed_step}")
        self.sequence = sequence
        self.failed_step = failed_step
        self.completed_request_ids = completed_request_ids
        self.recovery_required = bool(completed_request_ids)


class OrderManager:
    """Run coordinated broker sequences using stable request IDs."""

    def __init__(
        self,
        *,
        broker: BrokerGateway,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not isinstance(broker, BrokerGateway):
            raise DomainValidationError("broker must implement BrokerGateway")
        self._broker = broker
        self._logger = logger or logging.getLogger(__name__)

    @property
    def broker(self) -> BrokerGateway:
        """Expose the normalized gateway for reconciliation queries."""
        return self._broker

    def open_initial_pair(
        self, *, cycle_id: str, position_size: Decimal
    ) -> InitialPositionPair:
        """Open equal LONG and SHORT positions as one recoverable sequence."""
        _require_text("cycle_id", cycle_id)
        _require_positive_decimal("position_size", position_size)
        completed = []
        try:
            long = self._broker.open_position(
                request_id=f"{cycle_id}:scenario-1:open-long",
                side=Side.LONG,
                size=position_size,
            )
            _require_open_confirmation(long, Side.LONG)
            completed.append(long.request_id)
            short = self._broker.open_position(
                request_id=f"{cycle_id}:scenario-1:open-short",
                side=Side.SHORT,
                size=position_size,
            )
            _require_open_confirmation(short, Side.SHORT)
            completed.append(short.request_id)
        except Exception as exc:
            if isinstance(exc, OrderSequenceError):
                raise
            error = OrderSequenceError(
                sequence="OPEN_INITIAL_PAIR",
                failed_step="OPEN_SHORT" if completed else "OPEN_LONG",
                completed_request_ids=tuple(completed),
            )
            raise error from exc
        self._logger.info(
            "Initial position pair confirmed", extra={"cycle_id": cycle_id}
        )
        return InitialPositionPair(long, short)

    def apply_initial_levels(
        self,
        *,
        cycle_id: str,
        long_position_id: str,
        short_position_id: str,
        stops: StopLevels,
        take_profits: TakeProfitLevels,
    ) -> InitialLevelSet:
        """Apply LONG/SHORT SL and TP, waiting for each normalized confirmation."""
        _require_text("cycle_id", cycle_id)
        _require_text("long_position_id", long_position_id)
        _require_text("short_position_id", short_position_id)
        if not isinstance(stops, StopLevels):
            raise DomainValidationError("stops must be StopLevels")
        if not isinstance(take_profits, TakeProfitLevels):
            raise DomainValidationError("take_profits must be TakeProfitLevels")

        completed = []
        operations = (
            (
                "LONG_STOP_LOSS",
                self._broker.set_stop_loss,
                f"{cycle_id}:scenario-1:set-long-sl",
                long_position_id,
                stops.long,
            ),
            (
                "LONG_TAKE_PROFIT",
                self._broker.set_take_profit,
                f"{cycle_id}:scenario-1:set-long-tp",
                long_position_id,
                take_profits.long,
            ),
            (
                "SHORT_STOP_LOSS",
                self._broker.set_stop_loss,
                f"{cycle_id}:scenario-1:set-short-sl",
                short_position_id,
                stops.short,
            ),
            (
                "SHORT_TAKE_PROFIT",
                self._broker.set_take_profit,
                f"{cycle_id}:scenario-1:set-short-tp",
                short_position_id,
                take_profits.short,
            ),
        )
        confirmations = []
        for step, operation, request_id, position_id, price in operations:
            try:
                confirmation = operation(
                    request_id=request_id,
                    position_id=position_id,
                    price=price,
                )
                _require_level_confirmation(confirmation, request_id, price)
            except Exception as exc:
                error = OrderSequenceError(
                    sequence="APPLY_INITIAL_LEVELS",
                    failed_step=step,
                    completed_request_ids=tuple(completed),
                )
                raise error from exc
            completed.append(request_id)
            confirmations.append(confirmation)

        return InitialLevelSet(
            long=PositionLevelConfirmations(confirmations[0], confirmations[1]),
            short=PositionLevelConfirmations(confirmations[2], confirmations[3]),
        )

    def create_reentry_trigger(
        self,
        *,
        cycle_id: str,
        scenario: int,
        side: Side,
        price: Decimal,
        size: Decimal,
        source_position_id: str,
    ) -> BrokerConfirmation:
        """Create an idempotent Trigger at the immutable initial entry."""
        return self._broker.create_trigger(
            request_id=f"{cycle_id}:scenario-{scenario}:create-{side.value.lower()}-trigger",
            side=side,
            price=price,
            size=size,
            source_position_id=source_position_id,
        )

    def cancel_reentry_trigger(
        self, *, cycle_id: str, scenario: int, trigger_id: str
    ) -> BrokerConfirmation:
        """Cancel the remaining Trigger after the opposite TP closes a Cycle."""
        confirmation = self._broker.cancel_trigger(
            request_id=f"{cycle_id}:scenario-{scenario}:cancel-trigger",
            trigger_id=trigger_id,
        )
        if confirmation.status != "CONFIRMED":
            raise DomainValidationError("Trigger cancellation was not confirmed")
        return confirmation

    def apply_reentry_levels(
        self,
        *,
        cycle_id: str,
        scenario: int,
        reentry_position_id: str,
        opposite_position_id: str,
        reentry_stop: Decimal,
        reentry_take_profit: Decimal,
        opposite_take_profit: Decimal,
    ) -> ReentryLevelSet:
        """Confirm Reentry SL/TP and the opposite position's replaced TP."""
        prefix = f"{cycle_id}:scenario-{scenario}"
        stop = self._broker.set_stop_loss(
            request_id=prefix + ":set-reentry-sl",
            position_id=reentry_position_id,
            price=reentry_stop,
        )
        _require_level_confirmation(stop, prefix + ":set-reentry-sl", reentry_stop)
        reentry_tp = self._broker.set_take_profit(
            request_id=prefix + ":set-reentry-tp",
            position_id=reentry_position_id,
            price=reentry_take_profit,
        )
        _require_level_confirmation(
            reentry_tp, prefix + ":set-reentry-tp", reentry_take_profit
        )
        opposite_tp = self._broker.set_take_profit(
            request_id=prefix + ":replace-opposite-tp",
            position_id=opposite_position_id,
            price=opposite_take_profit,
        )
        _require_level_confirmation(
            opposite_tp, prefix + ":replace-opposite-tp", opposite_take_profit
        )
        return ReentryLevelSet(stop, reentry_tp, opposite_tp)

    def enter_manual_mode(
        self, *, cycle_id: str, scenario: int
    ) -> ManualTakeoverConfirmations:
        """Cancel every Trigger and remove TP/SL while preserving open positions."""
        cancelled = []
        for trigger in self._broker.triggers():
            confirmation = self._broker.cancel_trigger(
                request_id=f"{cycle_id}:scenario-{scenario}:cancel-{trigger.trigger_id}",
                trigger_id=trigger.trigger_id,
            )
            if confirmation.status != "CONFIRMED":
                raise DomainValidationError("manual-mode Trigger cancellation failed")
            cancelled.append(confirmation)
        removed = []
        for position in self._broker.positions():
            if position.take_profit is not None:
                confirmation = self._broker.remove_take_profit(
                    request_id=f"{cycle_id}:scenario-{scenario}:remove-{position.position_id}-tp",
                    position_id=position.position_id,
                )
                if confirmation.status != "CONFIRMED":
                    raise DomainValidationError("manual-mode TP removal failed")
                removed.append(confirmation)
            if position.stop_loss is not None:
                confirmation = self._broker.remove_stop_loss(
                    request_id=f"{cycle_id}:scenario-{scenario}:remove-{position.position_id}-sl",
                    position_id=position.position_id,
                )
                if confirmation.status != "CONFIRMED":
                    raise DomainValidationError("manual-mode SL removal failed")
                removed.append(confirmation)
        return ManualTakeoverConfirmations(tuple(cancelled), tuple(removed))


def _require_open_confirmation(
    confirmation: BrokerConfirmation, expected_side: Side
) -> None:
    if not isinstance(confirmation, BrokerConfirmation):
        raise DomainValidationError("broker returned an invalid confirmation")
    if confirmation.status != "CONFIRMED":
        raise DomainValidationError("position open was not confirmed")
    if confirmation.position_id is None or confirmation.execution_price is None:
        raise DomainValidationError("open confirmation is incomplete")
    if confirmation.side is not expected_side:
        raise DomainValidationError("broker confirmed the wrong position side")
    if confirmation.execution_price <= 0:
        raise DomainValidationError("confirmed execution price must be positive")
    if expected_side not in {Side.LONG, Side.SHORT}:
        raise DomainValidationError("expected_side is invalid")


def _require_level_confirmation(
    confirmation: BrokerConfirmation, request_id: str, price: Decimal
) -> None:
    if not isinstance(confirmation, BrokerConfirmation):
        raise DomainValidationError("broker returned an invalid confirmation")
    if confirmation.request_id != request_id or confirmation.status != "CONFIRMED":
        raise DomainValidationError("position level was not confirmed")
    if confirmation.execution_price != price:
        raise DomainValidationError("broker confirmed a different position level")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be a non-empty string")


def _require_positive_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise DomainValidationError(f"{name} must be a finite positive Decimal")
