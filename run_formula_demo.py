"""Small Pydroid 3-compatible smoke test for the FormulaEngine."""

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.formula_engine import FormulaEngine


def main() -> None:
    """Calculate and print the documented Scenario 1 example."""
    engine = FormulaEngine()
    stops = engine.calculate_initial_stops(
        long_entry=Decimal("4011.00"),
        short_entry=Decimal("4010.70"),
        stop_distance=Decimal("1.00"),
    )
    coverage = engine.calculate_base_coverage(
        spread=Decimal("0.30"),
        initial_long_close_commission=Decimal("0.10"),
        initial_short_close_commission=Decimal("0.10"),
        actual_initial_slippage=Decimal("0.02"),
        target_profit=Decimal("0.30"),
    )
    take_profits = engine.calculate_initial_take_profits(
        long_stop=stops.long,
        short_stop=stops.short,
        base_coverage=coverage,
    )

    print("AI Impulse Trader FormulaEngine")
    print("LONG_SL:", stops.long)
    print("SHORT_SL:", stops.short)
    print("BASE_COVERAGE:", coverage)
    print("LONG_TP:", take_profits.long)
    print("SHORT_TP:", take_profits.short)


if __name__ == "__main__":
    main()
