"""Authorized Scenario 9 trading actions invoked by Telegram commands."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from .enums import CycleState, Side
from .exceptions import DomainValidationError
from .models import Cycle
from .notification_manager import NotificationTransport
from .order_manager import OrderManager
from .state_manager import StateManager


@dataclass(frozen=True)
class ManualActionResult:
    """Confirmed manual action returned to TelegramCommandService."""

    message: str
    partial_execution: bool = False


class ManualTradingService:
    """Apply individual levels, Triggers, cancellations, and closes in Scenario 9."""

    def __init__(
        self,
        *,
        orders: OrderManager,
        state: StateManager,
        notifications: Optional[NotificationTransport] = None,
    ) -> None:
        self._orders = orders
        self._state = state
        self._notifications = notifications

    def set_level(
        self,
        *,
        side: Side,
        level: str,
        price: Decimal,
        position_id: Optional[str] = None,
    ) -> ManualActionResult:
        cycle, position = self._context(side, position_id)
        if level not in {"SL", "TP"}:
            raise DomainValidationError("level must be SL or TP")
        _positive_decimal("price", price)
        request_id = (
            f"{cycle.cycle_id}:manual:set-{side.value.lower()}-{level.lower()}:{price}"
        )
        operation = (
            self._orders.broker.set_stop_loss
            if level == "SL"
            else self._orders.broker.set_take_profit
        )
        confirmation = operation(
            request_id=request_id, position_id=position.position_id, price=price
        )
        _confirmed(confirmation.status)
        self._persist(cycle, "MANUAL_LEVEL_SET")
        return ManualActionResult(f"{side.value} {level} подтверждён: {price}")

    def remove_level(
        self, *, side: Side, level: str, position_id: Optional[str] = None
    ) -> ManualActionResult:
        cycle, position = self._context(side, position_id)
        if level not in {"SL", "TP"}:
            raise DomainValidationError("level must be SL or TP")
        operation = (
            self._orders.broker.remove_stop_loss
            if level == "SL"
            else self._orders.broker.remove_take_profit
        )
        confirmation = operation(
            request_id=f"{cycle.cycle_id}:manual:remove-{side.value.lower()}-{level.lower()}",
            position_id=position.position_id,
        )
        _confirmed(confirmation.status)
        self._persist(cycle, "MANUAL_LEVEL_REMOVED")
        return ManualActionResult(f"{side.value} {level} удалён")

    def create_trigger(
        self, *, side: Side, price: Decimal, size: Decimal, source_position_id: str
    ) -> ManualActionResult:
        cycle = self._manual_cycle()
        _positive_decimal("price", price)
        _positive_decimal("size", size)
        confirmation = self._orders.broker.create_trigger(
            request_id=f"{cycle.cycle_id}:manual:create-{side.value.lower()}-trigger:{price}:{size}:{source_position_id}",
            side=side,
            price=price,
            size=size,
            source_position_id=source_position_id,
        )
        _confirmed(confirmation.status)
        self._persist(cycle, "MANUAL_TRIGGER_CREATED")
        return ManualActionResult(
            f"{side.value} Trigger подтверждён: {confirmation.trigger_id} @ {price}"
        )

    def cancel_trigger(self, trigger_id: str) -> ManualActionResult:
        cycle = self._manual_cycle()
        confirmation = self._orders.broker.cancel_trigger(
            request_id=f"{cycle.cycle_id}:manual:cancel-trigger:{trigger_id}",
            trigger_id=trigger_id,
        )
        _confirmed(confirmation.status)
        self._persist(cycle, "MANUAL_TRIGGER_CANCELLED")
        return ManualActionResult(f"Trigger отменён: {trigger_id}")

    def close_position(
        self,
        *,
        side: Side,
        position_id: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> ManualActionResult:
        cycle, position = self._context(side, position_id)
        confirmation = self._orders.broker.close_position(
            request_id=(
                f"{cycle.cycle_id}:manual:close-{position.position_id}:"
                + (action_id or "direct")
            ),
            position_id=position.position_id,
            reason="TELEGRAM_MANUAL",
        )
        _confirmed(confirmation.status)
        partial = (
            confirmation.requested_size is not None
            and confirmation.filled_size is not None
            and confirmation.filled_size < confirmation.requested_size
        )
        self._persist(cycle, "MANUAL_POSITION_CLOSE")
        if partial:
            message = (
                f"⚠️ Частичное исполнение {side.value}: position_id={position.position_id}, "
                f"requested={confirmation.requested_size}, filled={confirmation.filled_size}, "
                f"price={confirmation.execution_price}"
            )
            if self._notifications is not None:
                self._notifications.send_message(message)
            return ManualActionResult(message, partial_execution=True)
        return ManualActionResult(
            f"{side.value} позиция закрыта: {position.position_id}, price={confirmation.execution_price}"
        )

    def _context(self, side: Side, position_id: Optional[str]) -> tuple:
        cycle = self._manual_cycle()
        positions = self._orders.broker.positions()
        candidates = [position for position in positions if position.side is side]
        if position_id is not None:
            candidates = [p for p in candidates if p.position_id == position_id]
        if len(candidates) != 1:
            raise ManualTradingError(
                f"Не удалось однозначно определить {side.value} position_id"
            )
        return cycle, candidates[0]

    def _manual_cycle(self) -> Cycle:
        application = self._state.load_application_state()
        cycle = (
            self._state.load_cycle(application.active_cycle_id)
            if application is not None and application.active_cycle_id is not None
            else None
        )
        if cycle is None or cycle.state is not CycleState.MANUAL_MODE:
            raise ManualTradingError(
                "Ручные торговые команды разрешены только в Scenario 9"
            )
        return cycle

    def _persist(self, cycle: Cycle, reason: str) -> None:
        positions = self._orders.broker.positions()
        triggers = self._orders.broker.triggers()
        updated = replace(
            cycle,
            long_position=next((p for p in positions if p.side is Side.LONG), None),
            short_position=next((p for p in positions if p.side is Side.SHORT), None),
            manual_triggers=tuple(triggers),
            last_confirmed_step=reason,
        )
        self._state.save_cycle(updated, reason=reason)


class ManualTradingError(RuntimeError):
    """Raised when a manual command cannot be applied safely."""


def _positive_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise DomainValidationError(f"{name} must be a positive Decimal")


def _confirmed(status: str) -> None:
    if status != "CONFIRMED":
        raise ManualTradingError("Брокер не подтвердил ручную операцию")
