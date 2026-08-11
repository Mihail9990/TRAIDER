"""Unit tests for immutable, persistable domain models."""

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_impulse_trader.enums import CycleState, PositionStatus, Side, TriggerStatus
from ai_impulse_trader.exceptions import DomainValidationError
from ai_impulse_trader.models import (
    ApplicationState,
    Cycle,
    Position,
    ReentryCostRecord,
    Trigger,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _position(side: Side) -> Position:
    return Position(
        position_id=f"position-{side.value.lower()}",
        broker_position_id=f"broker-{side.value.lower()}",
        side=side,
        size=Decimal("1"),
        initial_entry=Decimal("4011") if side is Side.LONG else Decimal("4010.7"),
        current_entry=Decimal("4011") if side is Side.LONG else Decimal("4010.7"),
        stop_loss=Decimal("4010") if side is Side.LONG else Decimal("4011.7"),
        take_profit=Decimal("4012.52") if side is Side.LONG else Decimal("4009.18"),
        status=PositionStatus.OPEN,
        opened_at=NOW,
    )


def _cost(index: int, side: Side = Side.SHORT) -> ReentryCostRecord:
    return ReentryCostRecord(
        index=index,
        side=side,
        entry_stop_distance=Decimal("1.00"),
        close_commission=Decimal("0.09"),
        actual_slippage=Decimal("0.02"),
        total=Decimal("1.11"),
        confirmation_id=f"confirmation-{index}",
        created_at=NOW + timedelta(minutes=index),
    )


def test_position_round_trip_preserves_decimal_and_enum() -> None:
    position = _position(Side.LONG)

    restored = Position.from_dict(json.loads(json.dumps(position.to_dict())))

    assert restored == position
    assert isinstance(restored.size, Decimal)
    assert restored.side is Side.LONG


def test_closed_position_requires_consistent_timestamp() -> None:
    with pytest.raises(DomainValidationError, match="requires closed_at"):
        Position(
            position_id="closed",
            side=Side.LONG,
            size=Decimal("1"),
            initial_entry=Decimal("10"),
            current_entry=Decimal("10"),
            status=PositionStatus.CLOSED,
            opened_at=NOW,
        )


def test_trigger_round_trip() -> None:
    trigger = Trigger(
        trigger_id="trigger-1",
        broker_order_id="broker-order-1",
        side=Side.SHORT,
        price=Decimal("4010.70"),
        size=Decimal("1"),
        status=TriggerStatus.WAITING,
        source_position_id="position-short",
        created_at=NOW,
        updated_at=NOW,
    )

    assert Trigger.from_dict(trigger.to_dict()) == trigger


def test_reentry_cost_requires_exact_component_sum() -> None:
    with pytest.raises(DomainValidationError, match="does not match"):
        ReentryCostRecord(
            index=1,
            side=Side.SHORT,
            entry_stop_distance=Decimal("1"),
            close_commission=Decimal("0.09"),
            actual_slippage=Decimal("0.02"),
            total=Decimal("1.10"),
            confirmation_id="confirmation-1",
            created_at=NOW,
        )


def test_cycle_round_trip_with_positions_trigger_and_history() -> None:
    history = (_cost(1), _cost(2, Side.LONG))
    trigger = Trigger(
        trigger_id="trigger-long",
        side=Side.LONG,
        price=Decimal("4011"),
        size=Decimal("1"),
        status=TriggerStatus.WAITING,
        source_position_id="position-long",
        created_at=NOW,
        updated_at=NOW,
    )
    cycle = Cycle(
        cycle_id="cycle-1",
        symbol="GOLD",
        position_size=Decimal("1"),
        state=CycleState.RUNNING,
        current_scenario=3,
        initial_long_entry=Decimal("4011"),
        initial_short_entry=Decimal("4010.70"),
        base_coverage=Decimal("0.82"),
        saved_long_tp=Decimal("4014.74"),
        saved_short_tp=Decimal("4006.96"),
        total_reentry_cost=Decimal("2.22"),
        reentry_cost_history=history,
        long_position=_position(Side.LONG),
        short_position=_position(Side.SHORT),
        active_trigger=trigger,
        created_at=NOW,
        last_confirmed_step="TP_UPDATED",
    )

    restored = Cycle.from_dict(json.loads(json.dumps(cycle.to_dict())))

    assert restored == cycle
    assert restored.reentry_cost_history[1].side is Side.LONG
    assert restored.total_reentry_cost == Decimal("2.22")


def test_cycle_rejects_non_sequential_reentry_history() -> None:
    with pytest.raises(DomainValidationError, match="indexes must be sequential"):
        Cycle(
            cycle_id="cycle-1",
            symbol="GOLD",
            position_size=Decimal("1"),
            reentry_cost_history=(_cost(2),),
            total_reentry_cost=Decimal("1.11"),
            created_at=NOW,
        )


def test_cycle_rejects_history_total_mismatch() -> None:
    with pytest.raises(DomainValidationError, match="history sum"):
        Cycle(
            cycle_id="cycle-1",
            symbol="GOLD",
            position_size=Decimal("1"),
            reentry_cost_history=(_cost(1),),
            total_reentry_cost=Decimal("2.22"),
            created_at=NOW,
        )


def test_cycle_rejects_position_on_wrong_side() -> None:
    with pytest.raises(DomainValidationError, match="long_position"):
        Cycle(
            cycle_id="cycle-1",
            symbol="GOLD",
            position_size=Decimal("1"),
            long_position=_position(Side.SHORT),
            created_at=NOW,
        )


def test_finished_cycle_requires_completion_time() -> None:
    with pytest.raises(DomainValidationError, match="requires completed_at"):
        Cycle(
            cycle_id="cycle-1",
            symbol="GOLD",
            position_size=Decimal("1"),
            state=CycleState.FINISHED,
            created_at=NOW,
        )


def test_application_state_round_trip_and_immutability() -> None:
    state = ApplicationState(
        stop_after_current_cycle=True,
        active_cycle_id="cycle-1",
        recovery_required=False,
        updated_at=NOW,
    )

    restored = ApplicationState.from_dict(state.to_dict())

    assert restored == state
    with pytest.raises(FrozenInstanceError):
        state.stop_after_current_cycle = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), 1.0],
)
def test_position_rejects_invalid_size(value: object) -> None:
    with pytest.raises(DomainValidationError):
        Position(
            position_id="position-1",
            side=Side.LONG,
            size=value,  # type: ignore[arg-type]
            initial_entry=Decimal("10"),
            current_entry=Decimal("10"),
            status=PositionStatus.OPEN,
            opened_at=NOW,
        )


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        ApplicationState(updated_at=datetime(2026, 8, 9, 12, 0))


def test_cycle_rejects_non_object_persisted_data() -> None:
    with pytest.raises(DomainValidationError, match="must be an object"):
        Cycle.from_dict([])  # type: ignore[arg-type]
