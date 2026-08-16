"""End-to-end tests for application boot, entry gating, and Cycle start."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.config import ConfigManager
from ai_impulse_trader.cycle_manager import CycleManager, InitialCoverageInputs
from ai_impulse_trader.entry_filter import EntryFilter
from ai_impulse_trader.formula_engine import FormulaEngine
from ai_impulse_trader.market_data import MarketDataStore
from ai_impulse_trader.order_manager import OrderManager
from ai_impulse_trader.recovery_manager import RecoveryManager
from ai_impulse_trader.runtime_controller import RuntimeController, RuntimeStateError
from ai_impulse_trader.state_manager import StateManager


def runtime(tmp_path, *, stop=False):
    config_manager = ConfigManager(tmp_path / "config.json")
    config = config_manager.load(create_if_missing=True)
    if stop:
        config_manager.set_stop_after_current_cycle(True)
    state = StateManager(tmp_path / "state.sqlite3")
    state.initialize()
    broker = BrokerSimulator(
        bid=Decimal("4010.70"), ask=Decimal("4011.00"), slippage=Decimal("0.02")
    )
    cycles = CycleManager(
        orders=OrderManager(broker=broker), formulas=FormulaEngine(), state=state
    )
    controller = RuntimeController(
        config_manager=config_manager,
        state_manager=state,
        recovery_manager=RecoveryManager(broker=broker, state=state),
        cycle_manager=cycles,
        entry_filter=EntryFilter(),
        market_data=MarketDataStore(config.symbol),
    )
    return controller, state, broker


def test_boot_candle_signal_and_scenario_one_are_coordinated(tmp_path) -> None:
    controller, state, broker = runtime(tmp_path)
    boot = controller.bootstrap()
    timestamp = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    assert boot.ready_for_entry
    assert not controller.ingest_price(
        price=Decimal("4010"), observed_at=timestamp
    ).start_cycle
    decision = controller.ingest_price(price=Decimal("4014.01"), observed_at=timestamp)
    assert decision.start_cycle

    cycle = controller.start_authorized_cycle(
        cycle_id="cycle-1",
        coverage=InitialCoverageInputs(
            Decimal("0.10"), Decimal("0.10"), Decimal("0.02")
        ),
    )
    assert cycle.current_scenario == 1
    assert len(broker.positions()) == 2
    assert state.load_application_state().active_cycle_id == "cycle-1"


def test_start_requires_entry_filter_authorization(tmp_path) -> None:
    controller, _, _ = runtime(tmp_path)
    controller.bootstrap()
    with pytest.raises(RuntimeStateError, match="not authorized"):
        controller.start_authorized_cycle(
            cycle_id="cycle-1",
            coverage=InitialCoverageInputs(
                Decimal("0.10"), Decimal("0.10"), Decimal("0.02")
            ),
        )


def test_stop_flag_is_synchronized_and_blocks_entry(tmp_path) -> None:
    controller, state, _ = runtime(tmp_path, stop=True)
    boot = controller.bootstrap()
    timestamp = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    controller.ingest_price(price=Decimal("4010"), observed_at=timestamp)
    decision = controller.ingest_price(price=Decimal("4020"), observed_at=timestamp)

    assert not boot.ready_for_entry
    assert decision.reason == "STOP_AFTER_CURRENT_CYCLE"
    assert state.load_application_state().stop_after_current_cycle
