"""Integration tests for restart reconciliation and timeout lookup."""

from dataclasses import replace
from decimal import Decimal

import pytest

from ai_impulse_trader.config import AppConfig
from ai_impulse_trader.enums import Side
from ai_impulse_trader.recovery_manager import RecoveryLookupError, RecoveryManager

from test_cycle_manager import runtime


def test_idle_state_is_safe_only_without_broker_activity(tmp_path) -> None:
    _, broker, state, _ = runtime(tmp_path)
    recovery = RecoveryManager(broker=broker, state=state)

    assert recovery.reconcile().status == "IDLE_CONFIRMED"
    broker.open_position(request_id="orphan", side=Side.LONG, size=Decimal("1"))
    unsafe = recovery.reconcile()

    assert not unsafe.safe_to_continue
    assert unsafe.differences == ("BROKER_ACTIVITY_WITHOUT_ACTIVE_CYCLE",)
    assert state.load_application_state().recovery_required


def test_running_cycle_exact_match_can_continue_after_restart(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )

    report = RecoveryManager(broker=broker, state=state).reconcile()

    assert report.safe_to_continue
    assert report.status == "ACTIVE_CYCLE_CONFIRMED"
    assert report.cycle == cycle
    assert not state.load_application_state().recovery_required


def test_position_level_mismatch_blocks_automatic_resume(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    broker.set_take_profit(
        request_id="external-change",
        position_id=cycle.long_position.position_id,
        price=Decimal("9999"),
    )

    report = RecoveryManager(broker=broker, state=state).reconcile()

    assert not report.safe_to_continue
    assert any(
        item.startswith("POSITION_STATE_MISMATCH") for item in report.differences
    )


def test_manual_mode_with_no_levels_is_recoverable(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    state.save_cycle(replace(cycle, current_scenario=8), reason="TEST_SCENARIO_8")
    manual = manager.enter_scenario_nine(cycle_id="cycle-1")

    report = RecoveryManager(broker=broker, state=state).reconcile()

    assert report.safe_to_continue
    assert report.cycle == manual


def test_timeout_confirmation_lookup_is_explicit(tmp_path) -> None:
    _, broker, state, _ = runtime(tmp_path)
    known = broker.open_position(
        request_id="known-request",
        side=Side.LONG,
        size=Decimal("1"),
    )
    recovery = RecoveryManager(broker=broker, state=state)

    assert recovery.recover_confirmation("known-request") == known
    with pytest.raises(RecoveryLookupError):
        recovery.recover_confirmation("unknown-request")
