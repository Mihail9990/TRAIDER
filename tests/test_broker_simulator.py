"""Behavioral tests for the deterministic in-memory BrokerSimulator."""

from decimal import Decimal

import pytest

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.enums import PositionStatus, Side, TriggerStatus
from ai_impulse_trader.exceptions import (
    BrokerRejectedError,
    BrokerTimeoutError,
    DomainValidationError,
)


@pytest.fixture
def broker() -> BrokerSimulator:
    return BrokerSimulator(
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        slippage=Decimal("0.02"),
        close_commission=Decimal("0.09"),
    )


def test_market_open_uses_side_quote_and_adverse_slippage(
    broker: BrokerSimulator,
) -> None:
    long = broker.open_position(
        request_id="open-long", side=Side.LONG, size=Decimal("1")
    )
    short = broker.open_position(
        request_id="open-short", side=Side.SHORT, size=Decimal("1")
    )

    assert long.execution_price == Decimal("4011.02")
    assert short.execution_price == Decimal("4010.68")
    assert [position.side for position in broker.positions()] == [
        Side.LONG,
        Side.SHORT,
    ]


def test_request_id_is_idempotent(broker: BrokerSimulator) -> None:
    first = broker.open_position(
        request_id="same-request", side=Side.LONG, size=Decimal("1")
    )
    second = broker.open_position(
        request_id="same-request", side=Side.SHORT, size=Decimal("5")
    )

    assert second is first
    assert len(broker.positions()) == 1
    assert len(broker.confirmations()) == 1


def test_long_stop_loss_closes_position_with_commission(
    broker: BrokerSimulator,
) -> None:
    opened = broker.open_position(
        request_id="open-long", side=Side.LONG, size=Decimal("1")
    )
    broker.set_stop_loss(
        request_id="long-sl",
        position_id=opened.position_id or "",
        price=Decimal("4010.00"),
    )

    events = broker.set_market_price(bid=Decimal("4009.90"), ask=Decimal("4010.20"))
    closed = next(event for event in events if event.reason == "STOP_LOSS")

    assert closed.execution_price == Decimal("4009.88")
    assert closed.commission == Decimal("0.09")
    assert closed.realized_pnl == Decimal("-1.23")
    assert broker.positions() == ()
    assert broker.positions(include_closed=True)[0].status is PositionStatus.CLOSED


def test_short_take_profit_closes_position(broker: BrokerSimulator) -> None:
    opened = broker.open_position(
        request_id="open-short", side=Side.SHORT, size=Decimal("2")
    )
    broker.set_take_profit(
        request_id="short-tp",
        position_id=opened.position_id or "",
        price=Decimal("4009.18"),
    )

    events = broker.set_market_price(bid=Decimal("4008.80"), ask=Decimal("4009.10"))
    closed = next(event for event in events if event.reason == "TAKE_PROFIT")

    assert closed.execution_price == Decimal("4009.12")
    assert closed.realized_pnl == Decimal("3.03")


def test_trigger_executes_on_price_crossing_and_opens_position(
    broker: BrokerSimulator,
) -> None:
    created = broker.create_trigger(
        request_id="create-trigger",
        side=Side.SHORT,
        price=Decimal("4010.00"),
        size=Decimal("1"),
        source_position_id="stopped-short",
    )

    events = broker.set_market_price(bid=Decimal("4009.90"), ask=Decimal("4010.20"))

    assert created.trigger_id is not None
    assert broker.triggers() == ()
    trigger = broker.triggers(include_inactive=True)[0]
    assert trigger.status is TriggerStatus.EXECUTED
    assert any(event.operation == "TRIGGER_EXECUTED" for event in events)
    assert broker.positions()[0].side is Side.SHORT
    assert broker.positions()[0].initial_entry == Decimal("4010.00")


def test_cancelled_trigger_does_not_execute(broker: BrokerSimulator) -> None:
    created = broker.create_trigger(
        request_id="create-trigger",
        side=Side.LONG,
        price=Decimal("4012"),
        size=Decimal("1"),
        source_position_id="stopped-long",
    )
    broker.cancel_trigger(
        request_id="cancel-trigger", trigger_id=created.trigger_id or ""
    )

    broker.set_market_price(bid=Decimal("4012"), ask=Decimal("4012.30"))

    assert broker.positions() == ()
    assert broker.triggers(include_inactive=True)[0].status is TriggerStatus.CANCELLED


def test_rejection_has_no_side_effect(broker: BrokerSimulator) -> None:
    broker.reject_next("OPEN_POSITION", "market closed")

    with pytest.raises(BrokerRejectedError, match="market closed"):
        broker.open_position(request_id="rejected", side=Side.LONG, size=Decimal("1"))

    assert broker.positions() == ()
    assert broker.confirmation_for("rejected") is None


def test_timeout_before_execution_is_known_not_to_execute(
    broker: BrokerSimulator,
) -> None:
    broker.timeout_next("OPEN_POSITION")

    with pytest.raises(BrokerTimeoutError) as error:
        broker.open_position(
            request_id="timeout-before", side=Side.LONG, size=Decimal("1")
        )

    assert error.value.may_have_executed is False
    assert broker.positions() == ()


def test_timeout_after_execution_is_recoverable_by_request_id(
    broker: BrokerSimulator,
) -> None:
    broker.timeout_next("OPEN_POSITION", after_execution=True)

    with pytest.raises(BrokerTimeoutError) as error:
        broker.open_position(
            request_id="timeout-after", side=Side.LONG, size=Decimal("1")
        )

    assert error.value.may_have_executed is True
    recovered = broker.confirmation_for("timeout-after")
    retried = broker.open_position(
        request_id="timeout-after", side=Side.LONG, size=Decimal("1")
    )
    assert recovered is retried
    assert len(broker.positions()) == 1


def test_invalid_quote_and_values_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        BrokerSimulator(bid=Decimal("10"), ask=Decimal("9"))
    with pytest.raises(DomainValidationError):
        BrokerSimulator(bid=10.0, ask=Decimal("11"))  # type: ignore[arg-type]


def test_remove_levels_preserves_open_position(broker: BrokerSimulator) -> None:
    opened = broker.open_position(request_id="open", side=Side.LONG, size=Decimal("1"))
    position_id = opened.position_id or ""
    broker.set_stop_loss(
        request_id="sl", position_id=position_id, price=Decimal("4000")
    )
    broker.set_take_profit(
        request_id="tp", position_id=position_id, price=Decimal("4020")
    )

    broker.remove_stop_loss(request_id="remove-sl", position_id=position_id)
    broker.remove_take_profit(request_id="remove-tp", position_id=position_id)

    position = broker.positions()[0]
    assert position.status is PositionStatus.OPEN
    assert position.stop_loss is None
    assert position.take_profit is None


def test_forced_trigger_execution_is_available_only_for_simulation(broker) -> None:
    created = broker.create_trigger(
        request_id="trigger",
        side=Side.SHORT,
        price=Decimal("4010"),
        size=Decimal("1"),
        source_position_id="stopped",
    )
    events = broker.execute_trigger(created.trigger_id or "")

    assert any(event.operation == "TRIGGER_EXECUTED" for event in events)
    assert broker.positions()[0].side is Side.SHORT
    assert broker.triggers() == ()
