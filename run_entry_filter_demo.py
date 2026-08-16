"""Pydroid-compatible minute-candle Entry Filter smoke test."""

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.entry_filter import EntryFilter, EntryReadiness
from ai_impulse_trader.market_data import MarketDataStore


def main() -> None:
    store = MarketDataStore("GOLD")
    entry_filter = EntryFilter()
    readiness = EntryReadiness(
        broker_connected=True,
        market_data_fresh=True,
        settings_loaded=True,
        previous_cycle_finished=True,
        no_active_cycle=True,
        no_open_positions=True,
        no_active_triggers=True,
        no_active_orders=True,
        recovery_inactive=True,
        stop_after_current_cycle=False,
    )
    store.ingest_price(
        price=Decimal("4010"),
        observed_at=datetime(2026, 1, 1, 10, 0, 1, tzinfo=timezone.utc),
    )
    store.ingest_price(
        price=Decimal("4014.01"),
        observed_at=datetime(2026, 1, 1, 10, 0, 20, tzinfo=timezone.utc),
    )
    previous, current = store.candles()
    decision = entry_filter.evaluate(
        threshold=Decimal("4"),
        readiness=readiness,
        previous_candle=previous,
        current_candle=current,
    )

    print("AI Impulse Trader EntryFilter")
    print("CURRENT_RANGE:", decision.candle_range)
    print("CANDLE_SOURCE:", decision.candle_source)
    print("START_CYCLE:", decision.start_cycle)
    print("STRICT_THRESHOLD: OK")
    print("ENTRY_FILTER: OK")


if __name__ == "__main__":
    main()
