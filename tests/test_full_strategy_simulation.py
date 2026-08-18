"""Full deterministic Scenario 1-9 strategy simulation."""

from decimal import Decimal

from ai_impulse_trader.config import AppConfig
from ai_impulse_trader.cycle_manager import ReentryInputs
from ai_impulse_trader.enums import CycleState, Side
from ai_impulse_trader.recovery_manager import RecoveryManager

from test_cycle_manager import runtime


def test_complete_automatic_path_scenario_one_through_manual_nine(tmp_path) -> None:
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )

    stopped_side = Side.SHORT
    for scenario in range(2, 9):
        position = next(p for p in broker.positions() if p.side is stopped_side)
        broker.close_position(
            request_id=f"simulation-stop-{scenario}",
            position_id=position.position_id,
            reason="STOP_LOSS",
        )
        waiting = manager.register_confirmed_stop(
            cycle_id="cycle-1", stopped_side=stopped_side
        )
        assert waiting.active_trigger is not None
        broker.execute_trigger(waiting.active_trigger.trigger_id)
        cycle = manager.register_confirmed_reentry(
            cycle_id="cycle-1",
            reentry_side=stopped_side,
            stop_distance=Decimal("1"),
            costs=ReentryInputs(
                Decimal("0.09"), Decimal("0.02"), f"reentry-{scenario - 1}"
            ),
        )
        assert cycle.current_scenario == scenario
        assert all(
            p.stop_loss is not None and p.take_profit is not None
            for p in broker.positions()
        )
        stopped_side = Side.LONG if stopped_side is Side.SHORT else Side.SHORT

    assert cycle.current_scenario == 8
    assert cycle.total_reentry_cost == Decimal("7.77")
    assert len(cycle.reentry_cost_history) == 7

    final_stopped = next(p for p in broker.positions() if p.side is stopped_side)
    broker.close_position(
        request_id="simulation-stop-9",
        position_id=final_stopped.position_id,
        reason="STOP_LOSS",
    )
    manual = manager.register_confirmed_stop(
        cycle_id="cycle-1", stopped_side=stopped_side
    )

    assert manual.state is CycleState.MANUAL_MODE
    assert manual.current_scenario == 9
    assert len(broker.positions()) == 1
    assert all(
        p.stop_loss is None and p.take_profit is None for p in broker.positions()
    )
    assert broker.triggers() == ()
    assert state.load_cycle("cycle-1") == manual
    assert RecoveryManager(broker=broker, state=state).reconcile().safe_to_continue
