"""Tests for complete transport-neutral Scenario 9 reports."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ai_impulse_trader.config import AppConfig
from ai_impulse_trader.broker_gateway import BrokerConfirmation
from ai_impulse_trader.notification_manager import NotificationManager
from ai_impulse_trader.exceptions import DomainValidationError
from ai_impulse_trader.enums import Side

from test_cycle_manager import runtime


class MemoryTransport:
    def __init__(self) -> None:
        self.messages = []

    def send_message(self, text: str) -> None:
        self.messages.append(text)


def test_scenario_nine_report_contains_required_state(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    state.save_cycle(replace(cycle, current_scenario=8), reason="TEST_SCENARIO_8")
    manual = manager.enter_scenario_nine(cycle_id="cycle-1")
    transport = MemoryTransport()

    report = NotificationManager(transport).send_scenario_nine(
        cycle=manual,
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        financial_result=Decimal("-2.50"),
    )

    assert report.total_commissions == Decimal("0.20")
    assert report.total_slippage == Decimal("0.02")
    assert report.text == transport.messages[0]
    for required in (
        "SCENARIO 9",
        "Cycle: cycle-1",
        "BASE_COVERAGE: 0.82",
        "SAVED_LONG_TP:",
        "SAVED_SHORT_TP:",
        "TOTAL_REENTRY_COST: 0",
        "Total commissions: 0.20",
        "Total slippage: 0.02",
        "Financial result: -2.50",
        "Triggers: cancelled",
        "Stop Loss / Take Profit: removed",
    ):
        assert required in report.text


def test_report_refuses_non_manual_cycle(tmp_path) -> None:
    manager, _, _, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    with pytest.raises(DomainValidationError, match="MANUAL_MODE"):
        NotificationManager(MemoryTransport()).send_scenario_nine(
            cycle=cycle,
            bid=Decimal("1"),
            ask=Decimal("2"),
            financial_result=Decimal("0"),
        )


def test_partial_fill_warning_contains_open_position_data() -> None:
    transport = MemoryTransport()
    confirmation = BrokerConfirmation(
        request_id="cycle:open-long",
        operation="OPEN_POSITION",
        status="PARTIALLY_FILLED",
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        position_id="broker-position-17",
        side=Side.LONG,
        requested_size=Decimal("1"),
        filled_size=Decimal("0.4"),
        size=Decimal("0.4"),
        execution_price=Decimal("2401.25"),
    )

    report = NotificationManager(transport).send_partial_fill(confirmation)

    assert report.position_id == "broker-position-17"
    assert report.requested_size == Decimal("1")
    assert report.filled_size == Decimal("0.4")
    assert report.text == transport.messages[0]
    assert "ЧАСТИЧНОЕ ИСПОЛНЕНИЕ" in report.text
    assert "Broker position ID: broker-position-17" in report.text
