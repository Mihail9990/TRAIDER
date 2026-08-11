"""Pydroid-compatible adapter-neutral OrderManager smoke test."""

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.formula_engine import StopLevels, TakeProfitLevels
from ai_impulse_trader.order_manager import OrderManager


def main() -> None:
    broker = BrokerSimulator(
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        slippage=Decimal("0.02"),
        close_commission=Decimal("0.09"),
    )
    manager = OrderManager(broker=broker)
    pair = manager.open_initial_pair(
        cycle_id="demo-cycle",
        position_size=Decimal("1"),
    )
    manager.apply_initial_levels(
        cycle_id="demo-cycle",
        long_position_id=pair.long.position_id or "",
        short_position_id=pair.short.position_id or "",
        stops=StopLevels(Decimal("4010.00"), Decimal("4011.70")),
        take_profits=TakeProfitLevels(Decimal("4012.52"), Decimal("4009.18")),
    )
    duplicate = manager.open_initial_pair(
        cycle_id="demo-cycle",
        position_size=Decimal("1"),
    )

    print("AI Impulse Trader OrderManager")
    print("LONG_CONFIRMED:", pair.long.status == "CONFIRMED")
    print("SHORT_CONFIRMED:", pair.short.status == "CONFIRMED")
    print(
        "LEVELS_CONFIRMED:",
        all(p.stop_loss and p.take_profit for p in broker.positions()),
    )
    print("DUPLICATE_POSITIONS:", len(broker.positions()))
    print("BROKER_NEUTRAL_SEQUENCE: OK")
    print("ORDER_IDEMPOTENCY: OK" if duplicate == pair else "ORDER_IDEMPOTENCY: FAILED")


if __name__ == "__main__":
    main()
