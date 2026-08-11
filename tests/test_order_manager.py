"""Integration tests for broker-neutral OrderManager sequences."""

from decimal import Decimal

import pytest

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.enums import Side
from ai_impulse_trader.formula_engine import StopLevels, TakeProfitLevels
from ai_impulse_trader.order_manager import OrderManager, OrderSequenceError


@pytest.fixture
def broker() -> BrokerSimulator:
    return BrokerSimulator(
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        slippage=Decimal("0.02"),
        close_commission=Decimal("0.09"),
    )


def test_opens_equal_initial_long_and_short_with_stable_ids(broker) -> None:
    manager = OrderManager(broker=broker)

    pair = manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("2"))

    positions = broker.positions()
    assert [position.side for position in positions] == [Side.LONG, Side.SHORT]
    assert all(position.size == Decimal("2") for position in positions)
    assert pair.long.request_id == "cycle-1:scenario-1:open-long"
    assert pair.short.request_id == "cycle-1:scenario-1:open-short"


def test_retry_does_not_duplicate_initial_positions(broker) -> None:
    manager = OrderManager(broker=broker)

    first = manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("1"))
    second = manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("1"))

    assert first == second
    assert len(broker.positions()) == 2


def test_partial_open_reports_recovery_and_retry_completes_pair(broker) -> None:
    manager = OrderManager(broker=broker)
    broker.reject_next("OPEN_POSITION", "short rejected")
    # The first queued fault applies to LONG, so first make LONG idempotently known.
    with pytest.raises(OrderSequenceError) as first_error:
        manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("1"))
    assert not first_error.value.recovery_required

    broker.open_position(
        request_id="cycle-2:scenario-1:open-long",
        side=Side.LONG,
        size=Decimal("1"),
    )
    broker.reject_next("OPEN_POSITION", "short rejected")
    with pytest.raises(OrderSequenceError) as partial_error:
        manager.open_initial_pair(cycle_id="cycle-2", position_size=Decimal("1"))

    assert partial_error.value.failed_step == "OPEN_SHORT"
    assert partial_error.value.recovery_required
    pair = manager.open_initial_pair(cycle_id="cycle-2", position_size=Decimal("1"))
    assert pair.short.position_id is not None


def test_applies_all_four_initial_levels(broker) -> None:
    manager = OrderManager(broker=broker)
    pair = manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("1"))

    levels = manager.apply_initial_levels(
        cycle_id="cycle-1",
        long_position_id=pair.long.position_id or "",
        short_position_id=pair.short.position_id or "",
        stops=StopLevels(Decimal("4010.00"), Decimal("4011.70")),
        take_profits=TakeProfitLevels(Decimal("4012.52"), Decimal("4009.18")),
    )

    long, short = broker.positions()
    assert long.stop_loss == Decimal("4010.00")
    assert long.take_profit == Decimal("4012.52")
    assert short.stop_loss == Decimal("4011.70")
    assert short.take_profit == Decimal("4009.18")
    assert levels.short.take_profit.status == "CONFIRMED"


def test_partial_level_failure_reports_completed_requests_and_retries(broker) -> None:
    manager = OrderManager(broker=broker)
    pair = manager.open_initial_pair(cycle_id="cycle-1", position_size=Decimal("1"))
    broker.reject_next("SET_TAKE_PROFIT", "temporary")
    arguments = {
        "cycle_id": "cycle-1",
        "long_position_id": pair.long.position_id or "",
        "short_position_id": pair.short.position_id or "",
        "stops": StopLevels(Decimal("4010.00"), Decimal("4011.70")),
        "take_profits": TakeProfitLevels(Decimal("4012.52"), Decimal("4009.18")),
    }

    with pytest.raises(OrderSequenceError) as error:
        manager.apply_initial_levels(**arguments)

    assert error.value.failed_step == "LONG_TAKE_PROFIT"
    assert error.value.completed_request_ids == ("cycle-1:scenario-1:set-long-sl",)
    completed = manager.apply_initial_levels(**arguments)
    assert completed.long.take_profit.status == "CONFIRMED"
