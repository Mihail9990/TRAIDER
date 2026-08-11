"""Unit tests for the Decimal-based FormulaEngine."""

from decimal import Decimal

import pytest

from ai_impulse_trader.exceptions import FormulaValidationError
from ai_impulse_trader.formula_engine import FormulaEngine, StopLevels, TakeProfitLevels


@pytest.fixture
def engine() -> FormulaEngine:
    return FormulaEngine()


def test_scenario_one_example(engine: FormulaEngine) -> None:
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

    assert stops == StopLevels(long=Decimal("4010.00"), short=Decimal("4011.70"))
    assert coverage == Decimal("0.82")
    assert take_profits == TakeProfitLevels(
        long=Decimal("4012.52"),
        short=Decimal("4009.18"),
    )


def test_reentry_updates_both_saved_take_profits(engine: FormulaEngine) -> None:
    cost = engine.calculate_reentry_cost(
        entry_stop_distance=Decimal("1.00"),
        reentry_close_commission=Decimal("0.09"),
        actual_reentry_slippage=Decimal("0.02"),
    )
    take_profits = engine.calculate_next_take_profits(
        saved_long_take_profit=Decimal("4012.52"),
        saved_short_take_profit=Decimal("4009.18"),
        reentry_cost=cost,
    )

    assert cost == Decimal("1.11")
    assert take_profits == TakeProfitLevels(
        long=Decimal("4013.63"),
        short=Decimal("4008.07"),
    )


def test_next_take_profit_uses_previous_saved_value(engine: FormulaEngine) -> None:
    take_profits = engine.calculate_next_take_profits(
        saved_long_take_profit=Decimal("4013.63"),
        saved_short_take_profit=Decimal("4008.07"),
        reentry_cost=Decimal("1.11"),
    )

    assert take_profits == TakeProfitLevels(
        long=Decimal("4014.74"),
        short=Decimal("4006.96"),
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (Decimal("0"), "stop_distance must be positive"),
        (Decimal("-1"), "stop_distance must be positive"),
        (Decimal("NaN"), "stop_distance must be finite"),
    ],
)
def test_initial_stops_reject_invalid_distance(
    engine: FormulaEngine, value: Decimal, message: str
) -> None:
    with pytest.raises(FormulaValidationError, match=message):
        engine.calculate_initial_stops(
            long_entry=Decimal("4011"),
            short_entry=Decimal("4010.7"),
            stop_distance=value,
        )


def test_formulas_reject_float_input(engine: FormulaEngine) -> None:
    with pytest.raises(FormulaValidationError, match="spread must be a Decimal"):
        engine.calculate_base_coverage(
            spread=0.3,  # type: ignore[arg-type]
            initial_long_close_commission=Decimal("0.10"),
            initial_short_close_commission=Decimal("0.10"),
            actual_initial_slippage=Decimal("0.02"),
            target_profit=Decimal("0.30"),
        )


def test_short_take_profit_must_remain_positive(engine: FormulaEngine) -> None:
    with pytest.raises(
        FormulaValidationError, match="short take-profit must be positive"
    ):
        engine.calculate_next_take_profits(
            saved_long_take_profit=Decimal("10"),
            saved_short_take_profit=Decimal("1"),
            reentry_cost=Decimal("1"),
        )
