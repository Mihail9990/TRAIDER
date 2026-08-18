"""Transport-neutral operator reports for safety-critical Cycle transitions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from .enums import CycleState
from .exceptions import DomainValidationError
from .models import Cycle, Position
from .broker_gateway import BrokerConfirmation


@runtime_checkable
class NotificationTransport(Protocol):
    """Minimal transport implemented later by the Telegram HTTP adapter."""

    def send_message(self, text: str) -> None:
        """Send one operator-visible message or raise on failure."""


@dataclass(frozen=True)
class ScenarioNineReport:
    """Structured report plus its ready-to-send Telegram text."""

    cycle_id: str
    total_commissions: Decimal
    total_slippage: Decimal
    financial_result: Decimal
    text: str


@dataclass(frozen=True)
class PartialFillReport:
    """Operator warning containing the broker-confirmed partial position data."""

    request_id: str
    requested_size: Decimal
    filled_size: Decimal
    execution_price: Decimal
    position_id: str
    text: str


class NotificationManager:
    """Build and send deterministic reports without owning trading actions."""

    def __init__(self, transport: NotificationTransport) -> None:
        if not isinstance(transport, NotificationTransport):
            raise DomainValidationError(
                "transport must implement NotificationTransport"
            )
        self._transport = transport

    def send_scenario_nine(
        self,
        *,
        cycle: Cycle,
        bid: Decimal,
        ask: Decimal,
        financial_result: Decimal,
    ) -> ScenarioNineReport:
        """Send the full manual-takeover report after confirmed Scenario 9."""
        if not isinstance(cycle, Cycle) or cycle.state is not CycleState.MANUAL_MODE:
            raise DomainValidationError("Scenario 9 report requires MANUAL_MODE Cycle")
        for name, value in {
            "bid": bid,
            "ask": ask,
            "financial_result": financial_result,
        }.items():
            if not isinstance(value, Decimal) or not value.is_finite():
                raise DomainValidationError(f"{name} must be a finite Decimal")
        commissions = (
            cycle.initial_long_close_commission
            + cycle.initial_short_close_commission
            + sum(
                (item.close_commission for item in cycle.reentry_cost_history),
                Decimal("0"),
            )
        )
        slippage = cycle.actual_initial_slippage + sum(
            (item.actual_slippage for item in cycle.reentry_cost_history), Decimal("0")
        )
        history = (
            ", ".join(
                f"#{item.index} {item.side.value}={item.total}"
                for item in cycle.reentry_cost_history
            )
            or "нет"
        )
        text = "\n".join(
            (
                "⚠️ AI Impulse Trader: SCENARIO 9",
                f"Cycle: {cycle.cycle_id}",
                f"Scenario: {cycle.current_scenario}",
                f"Symbol: {cycle.symbol}",
                f"Position size: {cycle.position_size}",
                f"Initial LONG entry: {cycle.initial_long_entry}",
                f"Initial SHORT entry: {cycle.initial_short_entry}",
                f"Current Bid / Ask: {bid} / {ask}",
                f"BASE_COVERAGE: {cycle.base_coverage}",
                f"SAVED_LONG_TP: {cycle.saved_long_tp}",
                f"SAVED_SHORT_TP: {cycle.saved_short_tp}",
                f"TOTAL_REENTRY_COST: {cycle.total_reentry_cost}",
                f"REENTRY_COST_HISTORY: {history}",
                f"Total commissions: {commissions}",
                f"Total slippage: {slippage}",
                f"Financial result: {financial_result}",
                "LONG: " + _position_text(cycle.long_position),
                "SHORT: " + _position_text(cycle.short_position),
                "Triggers: cancelled",
                "Stop Loss / Take Profit: removed",
                "Automation: stopped; awaiting authorized Telegram commands.",
            )
        )
        self._transport.send_message(text)
        return ScenarioNineReport(
            cycle.cycle_id, commissions, slippage, financial_result, text
        )

    def send_partial_fill(
        self, confirmation: BrokerConfirmation
    ) -> PartialFillReport:
        """Immediately notify the operator about a partially opened position."""
        if not isinstance(confirmation, BrokerConfirmation):
            raise DomainValidationError("confirmation must be BrokerConfirmation")
        if confirmation.status != "PARTIALLY_FILLED":
            raise DomainValidationError("partial-fill report requires partial status")
        required = {
            "requested_size": confirmation.requested_size,
            "filled_size": confirmation.filled_size,
            "execution_price": confirmation.execution_price,
        }
        for name, value in required.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise DomainValidationError(f"partial fill requires positive {name}")
        if not confirmation.position_id:
            raise DomainValidationError("partial fill requires broker position ID")
        text = "\n".join(
            (
                "⚠️ Capital.com: ЧАСТИЧНОЕ ИСПОЛНЕНИЕ",
                f"Операция: {confirmation.operation}",
                f"Request ID: {confirmation.request_id}",
                f"Сторона: {confirmation.side.value if confirmation.side else 'UNKNOWN'}",
                f"Запрошенный объём: {confirmation.requested_size}",
                f"Исполненный объём: {confirmation.filled_size}",
                f"Цена исполнения: {confirmation.execution_price}",
                f"Broker position ID: {confirmation.position_id}",
                "Автоматическая последовательность остановлена для проверки.",
            )
        )
        self._transport.send_message(text)
        return PartialFillReport(
            request_id=confirmation.request_id,
            requested_size=confirmation.requested_size,
            filled_size=confirmation.filled_size,
            execution_price=confirmation.execution_price,
            position_id=confirmation.position_id,
            text=text,
        )


def _position_text(position: Optional[Position]) -> str:
    if position is None:
        return "closed / absent"
    return (
        f"{position.status.value}, entry={position.current_entry}, size={position.size}"
    )
