"""Pydroid-compatible domain model round-trip smoke test."""

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.enums import CycleState, PositionStatus, Side
from ai_impulse_trader.models import Cycle, Position, ReentryCostRecord


def main() -> None:
    """Serialize and restore a Scenario 2 Cycle with exact Decimal values."""
    now = datetime.now(timezone.utc)
    cost = ReentryCostRecord(
        index=1,
        side=Side.SHORT,
        entry_stop_distance=Decimal("1.00"),
        close_commission=Decimal("0.09"),
        actual_slippage=Decimal("0.02"),
        total=Decimal("1.11"),
        confirmation_id="demo-confirmation",
        created_at=now,
    )
    short = Position(
        position_id="demo-short",
        side=Side.SHORT,
        size=Decimal("1"),
        initial_entry=Decimal("4010.70"),
        current_entry=Decimal("4010.70"),
        stop_loss=Decimal("4011.70"),
        take_profit=Decimal("4008.07"),
        status=PositionStatus.OPEN,
        opened_at=now,
    )
    cycle = Cycle(
        cycle_id="demo-cycle",
        symbol="GOLD",
        position_size=Decimal("1"),
        state=CycleState.RUNNING,
        current_scenario=2,
        initial_long_entry=Decimal("4011.00"),
        initial_short_entry=Decimal("4010.70"),
        base_coverage=Decimal("0.82"),
        saved_long_tp=Decimal("4013.63"),
        saved_short_tp=Decimal("4008.07"),
        total_reentry_cost=Decimal("1.11"),
        reentry_cost_history=(cost,),
        short_position=short,
        created_at=now,
    )

    encoded = json.dumps(cycle.to_dict(), ensure_ascii=False)
    restored = Cycle.from_dict(json.loads(encoded))
    if restored != cycle:
        raise RuntimeError("domain model round-trip failed")

    print("AI Impulse Trader Domain Models")
    print("CYCLE_ID:", restored.cycle_id)
    print("SCENARIO:", restored.current_scenario)
    print("SAVED_LONG_TP:", restored.saved_long_tp)
    print("SAVED_SHORT_TP:", restored.saved_short_tp)
    print("TOTAL_REENTRY_COST:", restored.total_reentry_cost)
    print("DECIMAL_ROUND_TRIP: OK")
    print("MODEL_VALIDATION: OK")


if __name__ == "__main__":
    main()
