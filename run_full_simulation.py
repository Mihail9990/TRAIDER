"""Pydroid-compatible full Scenario 1-9 deterministic simulation."""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.config import AppConfig
from ai_impulse_trader.cycle_manager import (
    CycleManager,
    InitialCoverageInputs,
    ReentryInputs,
)
from ai_impulse_trader.enums import Side
from ai_impulse_trader.formula_engine import FormulaEngine
from ai_impulse_trader.order_manager import OrderManager
from ai_impulse_trader.recovery_manager import RecoveryManager
from ai_impulse_trader.state_manager import StateManager


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        broker = BrokerSimulator(
            bid=Decimal("4010.70"),
            ask=Decimal("4011.00"),
            slippage=Decimal("0.02"),
            close_commission=Decimal("0.09"),
        )
        state = StateManager(Path(directory) / "simulation.sqlite3")
        state.initialize()
        cycles = CycleManager(
            orders=OrderManager(broker=broker),
            formulas=FormulaEngine(),
            state=state,
        )
        cycle = cycles.start_scenario_one(
            cycle_id="simulation-cycle",
            config=AppConfig(),
            coverage=InitialCoverageInputs(
                Decimal("0.10"), Decimal("0.10"), Decimal("0.02")
            ),
        )
        stopped_side = Side.SHORT
        for scenario in range(2, 9):
            position = next(p for p in broker.positions() if p.side is stopped_side)
            broker.close_position(
                request_id=f"simulation-stop-{scenario}",
                position_id=position.position_id,
                reason="STOP_LOSS",
            )
            waiting = cycles.register_confirmed_stop(
                cycle_id=cycle.cycle_id, stopped_side=stopped_side
            )
            broker.execute_trigger(waiting.active_trigger.trigger_id)
            cycle = cycles.register_confirmed_reentry(
                cycle_id=cycle.cycle_id,
                reentry_side=stopped_side,
                stop_distance=Decimal("1"),
                costs=ReentryInputs(
                    Decimal("0.09"),
                    Decimal("0.02"),
                    f"reentry-{scenario - 1}",
                ),
            )
            stopped_side = Side.LONG if stopped_side is Side.SHORT else Side.SHORT
        final_position = next(p for p in broker.positions() if p.side is stopped_side)
        broker.close_position(
            request_id="simulation-stop-9",
            position_id=final_position.position_id,
            reason="STOP_LOSS",
        )
        manual = cycles.register_confirmed_stop(
            cycle_id=cycle.cycle_id, stopped_side=stopped_side
        )
        recovery = RecoveryManager(broker=broker, state=state).reconcile()

        print("AI Impulse Trader Full Simulation")
        print("FINAL_SCENARIO:", manual.current_scenario)
        print("FINAL_STATE:", manual.state.value)
        print("REENTRY_COUNT:", len(manual.reentry_cost_history))
        print("TOTAL_REENTRY_COST:", manual.total_reentry_cost)
        print("OPEN_POSITIONS:", len(broker.positions()))
        print("RECOVERY_SAFE:", recovery.safe_to_continue)
        print("SCENARIO_1_TO_9: OK")


if __name__ == "__main__":
    main()
