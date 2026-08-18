"""Scenario 1 integration tests."""

from decimal import Decimal
from dataclasses import replace

import pytest

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.config import AppConfig
from ai_impulse_trader.cycle_manager import (
    CycleManager,
    CycleStartError,
    InitialCoverageInputs,
    ReentryInputs,
)
from ai_impulse_trader.enums import Side
from ai_impulse_trader.enums import CycleState
from ai_impulse_trader.formula_engine import FormulaEngine
from ai_impulse_trader.order_manager import OrderManager
from ai_impulse_trader.state_manager import StateManager


def runtime(tmp_path):
    broker = BrokerSimulator(
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        slippage=Decimal("0.02"),
        close_commission=Decimal("0.10"),
    )
    state = StateManager(tmp_path / "state.sqlite3")
    state.initialize()
    manager = CycleManager(
        orders=OrderManager(broker=broker), formulas=FormulaEngine(), state=state
    )
    costs = InitialCoverageInputs(Decimal("0.10"), Decimal("0.10"), Decimal("0.02"))
    return manager, broker, state, costs


def test_scenario_one_reaches_running_only_after_all_confirmations(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )

    assert cycle.state is CycleState.RUNNING
    assert cycle.base_coverage == Decimal("0.82")
    assert cycle.saved_long_tp == Decimal("4012.50")
    assert cycle.saved_short_tp == Decimal("4009.20")
    assert all(
        position.stop_loss and position.take_profit for position in broker.positions()
    )
    assert state.load_cycle("cycle-1") == cycle
    assert state.load_application_state().active_cycle_id == "cycle-1"


def test_clean_start_and_stop_flag_are_enforced(tmp_path) -> None:
    manager, _, _, costs = runtime(tmp_path)
    with pytest.raises(CycleStartError, match="STOP_AFTER"):
        manager.start_scenario_one(
            cycle_id="blocked",
            config=AppConfig(stop_after_current_cycle=True),
            coverage=costs,
        )
    manager.start_scenario_one(cycle_id="cycle-1", config=AppConfig(), coverage=costs)
    with pytest.raises(CycleStartError):
        manager.start_scenario_one(
            cycle_id="cycle-2", config=AppConfig(), coverage=costs
        )


def test_partial_broker_sequence_is_persisted_as_recovery(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    broker.reject_next("SET_TAKE_PROFIT", "temporary")
    with pytest.raises(Exception):
        manager.start_scenario_one(
            cycle_id="cycle-1", config=AppConfig(), coverage=costs
        )

    recovered = state.load_cycle("cycle-1")
    assert recovered is not None and recovered.state is CycleState.RECOVERY
    assert state.load_application_state().recovery_required


def test_short_stop_trigger_and_scenario_two_reentry_update_both_tp(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    broker.set_market_price(bid=Decimal("4011.50"), ask=Decimal("4011.80"))
    waiting = manager.register_confirmed_stop(
        cycle_id="cycle-1", stopped_side=Side.SHORT
    )
    assert waiting.active_trigger is not None
    assert waiting.active_trigger.price == cycle.initial_short_entry

    broker.set_market_price(bid=Decimal("4010.60"), ask=Decimal("4010.90"))
    scenario_two = manager.register_confirmed_reentry(
        cycle_id="cycle-1",
        reentry_side=Side.SHORT,
        stop_distance=Decimal("1"),
        costs=ReentryInputs(Decimal("0.09"), Decimal("0.02"), "reentry-1"),
    )

    assert scenario_two.current_scenario == 2
    assert scenario_two.total_reentry_cost == Decimal("1.11")
    assert scenario_two.saved_long_tp == Decimal("4013.61")
    assert scenario_two.saved_short_tp == Decimal("4008.09")
    assert scenario_two.long_position.take_profit == Decimal("4013.61")
    assert scenario_two.short_position.take_profit == Decimal("4008.09")
    assert scenario_two.short_position.stop_loss == Decimal("4011.58")
    assert state.load_cycle("cycle-1") == scenario_two


def test_scenario_three_long_reentry_uses_last_saved_tp(tmp_path) -> None:
    manager, broker, _, costs = runtime(tmp_path)
    manager.start_scenario_one(cycle_id="cycle-1", config=AppConfig(), coverage=costs)
    broker.set_market_price(bid=Decimal("4011.50"), ask=Decimal("4011.80"))
    manager.register_confirmed_stop(cycle_id="cycle-1", stopped_side=Side.SHORT)
    broker.set_market_price(bid=Decimal("4010.60"), ask=Decimal("4010.90"))
    manager.register_confirmed_reentry(
        cycle_id="cycle-1",
        reentry_side=Side.SHORT,
        stop_distance=Decimal("1"),
        costs=ReentryInputs(Decimal("0.09"), Decimal("0.02"), "reentry-1"),
    )

    broker.set_market_price(bid=Decimal("4010.00"), ask=Decimal("4010.30"))
    manager.register_confirmed_stop(cycle_id="cycle-1", stopped_side=Side.LONG)
    broker.set_market_price(bid=Decimal("4010.90"), ask=Decimal("4011.20"))
    scenario_three = manager.register_confirmed_reentry(
        cycle_id="cycle-1",
        reentry_side=Side.LONG,
        stop_distance=Decimal("1"),
        costs=ReentryInputs(Decimal("0.09"), Decimal("0.02"), "reentry-2"),
    )

    assert scenario_three.current_scenario == 3
    assert scenario_three.total_reentry_cost == Decimal("2.22")
    assert scenario_three.saved_long_tp == Decimal("4014.72")
    assert scenario_three.saved_short_tp == Decimal("4006.98")
    assert len(scenario_three.reentry_cost_history) == 2


def test_take_profit_cancels_waiting_trigger_and_finishes_cycle(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    manager.start_scenario_one(cycle_id="cycle-1", config=AppConfig(), coverage=costs)
    broker.set_market_price(bid=Decimal("4011.50"), ask=Decimal("4011.80"))
    manager.register_confirmed_stop(cycle_id="cycle-1", stopped_side=Side.SHORT)
    broker.set_market_price(bid=Decimal("4012.60"), ask=Decimal("4012.90"))

    finished = manager.finish_after_take_profit(cycle_id="cycle-1")

    assert finished.state is CycleState.FINISHED
    assert finished.completed_at is not None
    assert broker.positions() == ()
    assert broker.triggers() == ()
    assert state.load_application_state().active_cycle_id is None


def test_scenario_nine_removes_all_automation_but_keeps_positions(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    state.save_cycle(replace(cycle, current_scenario=8), reason="TEST_SCENARIO_8")

    manual = manager.enter_scenario_nine(cycle_id="cycle-1")

    assert manual.state is CycleState.MANUAL_MODE
    assert manual.current_scenario == 9
    assert len(broker.positions()) == 2
    assert all(
        p.stop_loss is None and p.take_profit is None for p in broker.positions()
    )
    assert broker.triggers() == ()
    assert state.load_cycle("cycle-1") == manual


def test_scenario_nine_rejects_early_takeover(tmp_path) -> None:
    manager, _, _, costs = runtime(tmp_path)
    manager.start_scenario_one(cycle_id="cycle-1", config=AppConfig(), coverage=costs)
    with pytest.raises(CycleStartError, match="only follow Scenario 8"):
        manager.enter_scenario_nine(cycle_id="cycle-1")
