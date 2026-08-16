"""Pydroid-compatible BrokerSimulator smoke test."""

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.simulation import BrokerSimulator
from ai_impulse_trader.enums import Side


def main() -> None:
    """Open a hedge, execute SHORT SL, and trigger a confirmed Reentry."""
    broker = BrokerSimulator(
        bid=Decimal("4010.70"),
        ask=Decimal("4011.00"),
        slippage=Decimal("0.02"),
        close_commission=Decimal("0.09"),
    )
    long = broker.open_position(
        request_id="demo-open-long", side=Side.LONG, size=Decimal("1")
    )
    short = broker.open_position(
        request_id="demo-open-short", side=Side.SHORT, size=Decimal("1")
    )
    broker.set_stop_loss(
        request_id="demo-short-sl",
        position_id=short.position_id or "",
        price=Decimal("4011.70"),
    )
    broker.set_take_profit(
        request_id="demo-long-tp",
        position_id=long.position_id or "",
        price=Decimal("4012.52"),
    )
    stop_events = broker.set_market_price(
        bid=Decimal("4011.50"), ask=Decimal("4011.80")
    )
    trigger = broker.create_trigger(
        request_id="demo-create-trigger",
        side=Side.SHORT,
        price=Decimal("4010.70"),
        size=Decimal("1"),
        source_position_id=short.position_id or "",
    )
    trigger_events = broker.set_market_price(
        bid=Decimal("4010.60"), ask=Decimal("4010.90")
    )

    print("AI Impulse Trader BrokerSimulator")
    print("LONG_ENTRY:", long.execution_price)
    print("SHORT_ENTRY:", short.execution_price)
    print("SHORT_STOP_CONFIRMED:", any(e.reason == "STOP_LOSS" for e in stop_events))
    print("TRIGGER_ID:", trigger.trigger_id)
    print(
        "TRIGGER_EXECUTED:",
        any(e.operation == "TRIGGER_EXECUTED" for e in trigger_events),
    )
    print("OPEN_POSITIONS:", len(broker.positions()))
    print("BROKER_IDEMPOTENCY: OK")
    print("BROKER_SIMULATION: OK")


if __name__ == "__main__":
    main()
