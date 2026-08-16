"""Deterministic in-memory broker simulator for strategy and recovery tests."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..broker_gateway import BrokerConfirmation
from ..enums import PositionStatus, Side, TriggerStatus
from ..exceptions import (
    BrokerRejectedError,
    BrokerTimeoutError,
    DomainValidationError,
)
from ..models import Position, Trigger


@dataclass(frozen=True)
class _Fault:
    kind: str
    message: str = "simulated broker failure"


class BrokerSimulator:
    """Simulate positions, levels, triggers, confirmations, and request faults."""

    def __init__(
        self,
        *,
        bid: Decimal,
        ask: Decimal,
        slippage: Decimal = Decimal("0"),
        close_commission: Decimal = Decimal("0"),
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Create a simulator with an initial valid Bid/Ask quote."""
        _require_positive_decimal("bid", bid)
        _require_positive_decimal("ask", ask)
        _require_non_negative_decimal("slippage", slippage)
        _require_non_negative_decimal("close_commission", close_commission)
        if ask < bid:
            raise DomainValidationError("ask must be greater than or equal to bid")
        self._bid = bid
        self._ask = ask
        self._previous_bid = bid
        self._previous_ask = ask
        self._slippage = slippage
        self._close_commission = close_commission
        self._logger = logger or logging.getLogger(__name__)
        self._positions: Dict[str, Position] = {}
        self._triggers: Dict[str, Trigger] = {}
        self._confirmations: Dict[str, BrokerConfirmation] = {}
        self._events: List[BrokerConfirmation] = []
        self._faults: Dict[str, List[_Fault]] = {}
        self._position_sequence = 0
        self._trigger_sequence = 0
        self._event_sequence = 0
        self._lock = threading.RLock()

    def quote(self) -> Tuple[Decimal, Decimal]:
        """Return the current Bid and Ask quote."""
        self._logger.info("Reading simulator quote")
        with self._lock:
            return self._bid, self._ask

    def set_market_price(
        self, *, bid: Decimal, ask: Decimal
    ) -> Tuple[BrokerConfirmation, ...]:
        """Update Bid/Ask and synchronously process crossings, SL, and TP."""
        self._logger.info("Updating simulator market price")
        _require_positive_decimal("bid", bid)
        _require_positive_decimal("ask", ask)
        if ask < bid:
            raise DomainValidationError("ask must be greater than or equal to bid")
        with self._lock:
            before = len(self._events)
            previous_bid, previous_ask = self._bid, self._ask
            self._previous_bid, self._previous_ask = previous_bid, previous_ask
            self._bid, self._ask = bid, ask
            self._execute_crossed_triggers(previous_bid, previous_ask)
            self._execute_position_levels()
            return tuple(self._events[before:])

    def open_position(
        self, *, request_id: str, side: Side, size: Decimal
    ) -> BrokerConfirmation:
        """Open one market position idempotently using adverse slippage."""
        self._logger.info(
            "Opening simulated position", extra={"request_id": request_id}
        )
        _require_text("request_id", request_id)
        _require_side(side)
        _require_positive_decimal("size", size)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            fault = self._before_operation("OPEN_POSITION", request_id)
            confirmation = self._open_position(
                request_id=request_id,
                side=side,
                size=size,
                initial_entry=None,
                reason="MARKET_OPEN",
            )
            self._after_operation(fault, request_id)
            return confirmation

    def set_stop_loss(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        """Set or replace a positive stop-loss level idempotently."""
        return self._set_level(
            request_id=request_id,
            position_id=position_id,
            price=price,
            level="STOP_LOSS",
        )

    def set_take_profit(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        """Set or replace a positive take-profit level idempotently."""
        return self._set_level(
            request_id=request_id,
            position_id=position_id,
            price=price,
            level="TAKE_PROFIT",
        )

    def remove_stop_loss(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        """Remove Stop Loss idempotently without closing the position."""
        return self._remove_level(
            request_id=request_id, position_id=position_id, level="STOP_LOSS"
        )

    def remove_take_profit(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        """Remove Take Profit idempotently without closing the position."""
        return self._remove_level(
            request_id=request_id, position_id=position_id, level="TAKE_PROFIT"
        )

    def close_position(
        self, *, request_id: str, position_id: str, reason: str = "MANUAL"
    ) -> BrokerConfirmation:
        """Close an open position idempotently at the current executable quote."""
        self._logger.info(
            "Closing simulated position",
            extra={"request_id": request_id, "event": reason},
        )
        _require_text("request_id", request_id)
        _require_text("position_id", position_id)
        _require_text("reason", reason)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            fault = self._before_operation("CLOSE_POSITION", request_id)
            confirmation = self._close_position(
                request_id=request_id,
                position_id=position_id,
                reason=reason,
            )
            self._after_operation(fault, request_id)
            return confirmation

    def create_trigger(
        self,
        *,
        request_id: str,
        side: Side,
        price: Decimal,
        size: Decimal,
        source_position_id: str,
    ) -> BrokerConfirmation:
        """Create one waiting trigger at the requested crossing price."""
        self._logger.info(
            "Creating simulated trigger", extra={"request_id": request_id}
        )
        _require_text("request_id", request_id)
        _require_side(side)
        _require_positive_decimal("price", price)
        _require_positive_decimal("size", size)
        _require_text("source_position_id", source_position_id)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            fault = self._before_operation("CREATE_TRIGGER", request_id)
            self._trigger_sequence += 1
            trigger_id = f"trigger-{self._trigger_sequence}"
            now = _utc_now()
            trigger = Trigger(
                trigger_id=trigger_id,
                broker_order_id=f"sim-{trigger_id}",
                side=side,
                price=price,
                size=size,
                status=TriggerStatus.WAITING,
                source_position_id=source_position_id,
                created_at=now,
                updated_at=now,
            )
            self._triggers[trigger_id] = trigger
            confirmation = self._confirmation(
                request_id=request_id,
                operation="CREATE_TRIGGER",
                trigger_id=trigger_id,
                execution_price=price,
            )
            self._remember(confirmation)
            self._after_operation(fault, request_id)
            return confirmation

    def cancel_trigger(self, *, request_id: str, trigger_id: str) -> BrokerConfirmation:
        """Cancel a waiting trigger idempotently."""
        self._logger.info(
            "Cancelling simulated trigger", extra={"request_id": request_id}
        )
        _require_text("request_id", request_id)
        _require_text("trigger_id", trigger_id)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            fault = self._before_operation("CANCEL_TRIGGER", request_id)
            trigger = self._require_trigger(trigger_id)
            if trigger.status is not TriggerStatus.WAITING:
                raise BrokerRejectedError("only a waiting trigger can be cancelled")
            updated = replace(
                trigger,
                status=TriggerStatus.CANCELLED,
                updated_at=_utc_now(),
            )
            self._triggers[trigger_id] = updated
            confirmation = self._confirmation(
                request_id=request_id,
                operation="CANCEL_TRIGGER",
                trigger_id=trigger_id,
            )
            self._remember(confirmation)
            self._after_operation(fault, request_id)
            return confirmation

    def execute_trigger(self, trigger_id: str) -> Tuple[BrokerConfirmation, ...]:
        """Force one waiting Trigger in deterministic strategy simulations."""
        _require_text("trigger_id", trigger_id)
        with self._lock:
            trigger = self._require_trigger(trigger_id)
            if trigger.status is not TriggerStatus.WAITING:
                raise BrokerRejectedError("only a waiting trigger can execute")
            before = len(self._events)
            self._execute_trigger(trigger)
            return tuple(self._events[before:])

    def positions(self, *, include_closed: bool = False) -> Tuple[Position, ...]:
        """Return positions ordered by generated ID."""
        self._logger.info("Listing simulated positions")
        with self._lock:
            values = self._positions.values()
            if not include_closed:
                values = (
                    position
                    for position in values
                    if position.status is not PositionStatus.CLOSED
                )
            return tuple(sorted(values, key=lambda item: item.position_id))

    def triggers(self, *, include_inactive: bool = False) -> Tuple[Trigger, ...]:
        """Return triggers ordered by generated ID."""
        self._logger.info("Listing simulated triggers")
        with self._lock:
            values = self._triggers.values()
            if not include_inactive:
                values = (
                    trigger
                    for trigger in values
                    if trigger.status is TriggerStatus.WAITING
                )
            return tuple(sorted(values, key=lambda item: item.trigger_id))

    def confirmations(self) -> Tuple[BrokerConfirmation, ...]:
        """Return all emitted confirmations in order."""
        self._logger.info("Listing simulated confirmations")
        with self._lock:
            return tuple(self._events)

    def confirmation_for(self, request_id: str) -> Optional[BrokerConfirmation]:
        """Return a known request outcome for timeout recovery."""
        self._logger.info(
            "Reading simulated confirmation", extra={"request_id": request_id}
        )
        _require_text("request_id", request_id)
        with self._lock:
            return self._confirmations.get(request_id)

    def reject_next(self, operation: str, message: str = "simulated rejection") -> None:
        """Queue a deterministic rejection before the next matching operation."""
        self._logger.info("Queueing simulator rejection", extra={"event": operation})
        self._queue_fault(operation, _Fault("REJECT", message))

    def timeout_next(self, operation: str, *, after_execution: bool = False) -> None:
        """Queue a timeout before or after the next matching operation."""
        self._logger.info("Queueing simulator timeout", extra={"event": operation})
        kind = "TIMEOUT_AFTER" if after_execution else "TIMEOUT_BEFORE"
        self._queue_fault(operation, _Fault(kind))

    def _set_level(
        self, *, request_id: str, position_id: str, price: Decimal, level: str
    ) -> BrokerConfirmation:
        self._logger.info(
            "Setting simulated position level",
            extra={"request_id": request_id, "event": level},
        )
        _require_text("request_id", request_id)
        _require_text("position_id", position_id)
        _require_positive_decimal("price", price)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            operation = f"SET_{level}"
            fault = self._before_operation(operation, request_id)
            position = self._require_open_position(position_id)
            if level == "STOP_LOSS":
                updated = replace(position, stop_loss=price)
            else:
                updated = replace(position, take_profit=price)
            self._positions[position_id] = updated
            confirmation = self._confirmation(
                request_id=request_id,
                operation=operation,
                position_id=position_id,
                execution_price=price,
            )
            self._remember(confirmation)
            self._after_operation(fault, request_id)
            return confirmation

    def _remove_level(
        self, *, request_id: str, position_id: str, level: str
    ) -> BrokerConfirmation:
        _require_text("request_id", request_id)
        _require_text("position_id", position_id)
        with self._lock:
            cached = self._confirmations.get(request_id)
            if cached is not None:
                return cached
            operation = f"REMOVE_{level}"
            fault = self._before_operation(operation, request_id)
            position = self._require_open_position(position_id)
            updated = replace(
                position,
                stop_loss=None if level == "STOP_LOSS" else position.stop_loss,
                take_profit=None if level == "TAKE_PROFIT" else position.take_profit,
            )
            self._positions[position_id] = updated
            confirmation = self._confirmation(
                request_id=request_id,
                operation=operation,
                position_id=position_id,
            )
            self._remember(confirmation)
            self._after_operation(fault, request_id)
            return confirmation

    def _open_position(
        self,
        *,
        request_id: str,
        side: Side,
        size: Decimal,
        initial_entry: Optional[Decimal],
        reason: str,
    ) -> BrokerConfirmation:
        execution_price = (
            self._ask + self._slippage
            if side is Side.LONG
            else self._bid - self._slippage
        )
        if execution_price <= 0:
            raise BrokerRejectedError("slippage produced an invalid execution price")
        self._position_sequence += 1
        position_id = f"position-{self._position_sequence}"
        now = _utc_now()
        position = Position(
            position_id=position_id,
            broker_position_id=f"sim-{position_id}",
            side=side,
            size=size,
            initial_entry=initial_entry or execution_price,
            current_entry=execution_price,
            status=PositionStatus.OPEN,
            opened_at=now,
        )
        self._positions[position_id] = position
        confirmation = self._confirmation(
            request_id=request_id,
            operation="OPEN_POSITION",
            position_id=position_id,
            side=side,
            size=size,
            execution_price=execution_price,
            reason=reason,
        )
        self._remember(confirmation)
        return confirmation

    def _close_position(
        self, *, request_id: str, position_id: str, reason: str
    ) -> BrokerConfirmation:
        position = self._require_open_position(position_id)
        execution_price = (
            self._bid - self._slippage
            if position.side is Side.LONG
            else self._ask + self._slippage
        )
        if execution_price <= 0:
            raise BrokerRejectedError("slippage produced an invalid execution price")
        direction_pnl = (
            execution_price - position.current_entry
            if position.side is Side.LONG
            else position.current_entry - execution_price
        )
        realized_pnl = direction_pnl * position.size - self._close_commission
        self._positions[position_id] = replace(
            position,
            status=PositionStatus.CLOSED,
            closed_at=_utc_now(),
        )
        confirmation = self._confirmation(
            request_id=request_id,
            operation="CLOSE_POSITION",
            position_id=position_id,
            side=position.side,
            size=position.size,
            requested_size=position.size,
            filled_size=position.size,
            execution_price=execution_price,
            commission=self._close_commission,
            realized_pnl=realized_pnl,
            reason=reason,
        )
        self._remember(confirmation)
        return confirmation

    def _execute_crossed_triggers(
        self, previous_bid: Decimal, previous_ask: Decimal
    ) -> None:
        waiting = [
            trigger
            for trigger in self._triggers.values()
            if trigger.status is TriggerStatus.WAITING
        ]
        for trigger in waiting:
            previous = previous_ask if trigger.side is Side.LONG else previous_bid
            current = self._ask if trigger.side is Side.LONG else self._bid
            if not _crossed(previous, current, trigger.price):
                continue
            self._execute_trigger(trigger)

    def _execute_trigger(self, trigger: Trigger) -> None:
        now = _utc_now()
        self._triggers[trigger.trigger_id] = replace(
            trigger,
            status=TriggerStatus.EXECUTED,
            updated_at=now,
        )
        open_confirmation = self._open_position(
            request_id=self._next_event_request("TRIGGER_EXECUTED"),
            side=trigger.side,
            size=trigger.size,
            initial_entry=trigger.price,
            reason="TRIGGER_EXECUTED",
        )
        self._remember(
            self._confirmation(
                request_id=self._next_event_request("TRIGGER_STATUS"),
                operation="TRIGGER_EXECUTED",
                position_id=open_confirmation.position_id,
                trigger_id=trigger.trigger_id,
                side=trigger.side,
                size=trigger.size,
                execution_price=open_confirmation.execution_price,
            )
        )

    def _execute_position_levels(self) -> None:
        open_positions = [
            position
            for position in self._positions.values()
            if position.status is PositionStatus.OPEN
        ]
        for position in open_positions:
            reason: Optional[str] = None
            if position.side is Side.LONG:
                if position.stop_loss is not None and self._bid <= position.stop_loss:
                    reason = "STOP_LOSS"
                elif (
                    position.take_profit is not None
                    and self._bid >= position.take_profit
                ):
                    reason = "TAKE_PROFIT"
            else:
                if position.stop_loss is not None and self._ask >= position.stop_loss:
                    reason = "STOP_LOSS"
                elif (
                    position.take_profit is not None
                    and self._ask <= position.take_profit
                ):
                    reason = "TAKE_PROFIT"
            if reason is not None:
                self._close_position(
                    request_id=self._next_event_request(reason),
                    position_id=position.position_id,
                    reason=reason,
                )

    def _before_operation(self, operation: str, request_id: str) -> Optional[_Fault]:
        queue = self._faults.get(operation)
        fault = queue.pop(0) if queue else None
        if fault is None:
            return None
        if fault.kind == "REJECT":
            raise BrokerRejectedError(fault.message)
        if fault.kind == "TIMEOUT_BEFORE":
            raise BrokerTimeoutError(request_id, may_have_executed=False)
        return fault

    @staticmethod
    def _after_operation(fault: Optional[_Fault], request_id: str) -> None:
        if fault is not None and fault.kind == "TIMEOUT_AFTER":
            raise BrokerTimeoutError(request_id, may_have_executed=True)

    def _queue_fault(self, operation: str, fault: _Fault) -> None:
        _require_text("operation", operation)
        with self._lock:
            self._faults.setdefault(operation, []).append(fault)

    def _require_open_position(self, position_id: str) -> Position:
        position = self._positions.get(position_id)
        if position is None:
            raise BrokerRejectedError(f"unknown position: {position_id}")
        if position.status is not PositionStatus.OPEN:
            raise BrokerRejectedError(f"position is not open: {position_id}")
        return position

    def _require_trigger(self, trigger_id: str) -> Trigger:
        trigger = self._triggers.get(trigger_id)
        if trigger is None:
            raise BrokerRejectedError(f"unknown trigger: {trigger_id}")
        return trigger

    def _confirmation(
        self,
        *,
        request_id: str,
        operation: str,
        position_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        side: Optional[Side] = None,
        size: Optional[Decimal] = None,
        requested_size: Optional[Decimal] = None,
        filled_size: Optional[Decimal] = None,
        execution_price: Optional[Decimal] = None,
        commission: Decimal = Decimal("0"),
        realized_pnl: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> BrokerConfirmation:
        return BrokerConfirmation(
            request_id=request_id,
            operation=operation,
            status="CONFIRMED",
            created_at=_utc_now(),
            position_id=position_id,
            trigger_id=trigger_id,
            side=side,
            size=size,
            requested_size=requested_size,
            filled_size=filled_size,
            execution_price=execution_price,
            commission=commission,
            realized_pnl=realized_pnl,
            reason=reason,
        )

    def _remember(self, confirmation: BrokerConfirmation) -> None:
        self._confirmations[confirmation.request_id] = confirmation
        self._events.append(confirmation)

    def _next_event_request(self, event: str) -> str:
        self._event_sequence += 1
        return f"sim-event-{self._event_sequence}-{event.lower()}"


def _crossed(previous: Decimal, current: Decimal, target: Decimal) -> bool:
    return min(previous, current) <= target <= max(previous, current)


def _require_side(side: Side) -> None:
    if not isinstance(side, Side):
        raise DomainValidationError("side must be LONG or SHORT")


def _require_positive_decimal(name: str, value: object) -> None:
    _require_non_negative_decimal(name, value)
    if value == 0:
        raise DomainValidationError(f"{name} must be positive")


def _require_non_negative_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if value < 0:
        raise DomainValidationError(f"{name} must be non-negative")


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be a non-empty string")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
