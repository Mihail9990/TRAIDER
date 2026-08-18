"""Tests for strict minute-candle Cycle entry decisions."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from ai_impulse_trader.entry_filter import EntryFilter, EntryReadiness
from ai_impulse_trader.market_data import MinuteCandle


def candle(high: str, low: str, *, closed: bool) -> MinuteCandle:
    return MinuteCandle(
        symbol="GOLD",
        opened_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        is_closed=closed,
    )


def ready() -> EntryReadiness:
    return EntryReadiness(
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


def test_previous_closed_candle_above_threshold_starts_cycle() -> None:
    decision = EntryFilter().evaluate(
        threshold=Decimal("4"),
        readiness=ready(),
        previous_candle=candle("15", "10", closed=True),
        current_candle=candle("12", "10", closed=False),
    )

    assert decision.start_cycle
    assert decision.candle_source == "PREVIOUS_CLOSED"
    assert decision.candle_range == Decimal("5")


def test_range_equal_to_threshold_does_not_start() -> None:
    decision = EntryFilter().evaluate(
        threshold=Decimal("4"),
        readiness=ready(),
        previous_candle=candle("14", "10", closed=True),
        current_candle=candle("14", "10", closed=False),
    )

    assert not decision.start_cycle
    assert decision.reason == "RANGE_NOT_EXCEEDED"


def test_current_forming_candle_can_start_without_waiting_for_close() -> None:
    decision = EntryFilter().evaluate(
        threshold=Decimal("4"),
        readiness=ready(),
        previous_candle=candle("13", "10", closed=True),
        current_candle=candle("14.01", "10", closed=False),
    )

    assert decision.start_cycle
    assert decision.candle_source == "CURRENT_FORMING"


def test_stop_after_cycle_blocks_signal_until_start_command_clears_flag() -> None:
    entry_filter = EntryFilter()
    stopped = replace(ready(), stop_after_current_cycle=True)
    signal = candle("15", "10", closed=True)

    blocked = entry_filter.evaluate(
        threshold=Decimal("4"),
        readiness=stopped,
        previous_candle=signal,
        current_candle=None,
    )
    allowed = entry_filter.evaluate(
        threshold=Decimal("4"),
        readiness=ready(),
        previous_candle=signal,
        current_candle=None,
    )

    assert blocked.reason == "STOP_AFTER_CURRENT_CYCLE"
    assert not blocked.start_cycle
    assert allowed.start_cycle


def test_system_readiness_is_rechecked_after_candle_signal() -> None:
    disconnected = replace(ready(), broker_connected=False)
    decision = EntryFilter().evaluate(
        threshold=Decimal("4"),
        readiness=disconnected,
        previous_candle=candle("15", "10", closed=True),
        current_candle=None,
    )

    assert not decision.start_cycle
    assert decision.reason == "SYSTEM_NOT_READY"


def test_emits_only_once_until_cycle_completion_reset() -> None:
    entry_filter = EntryFilter()
    arguments = {
        "threshold": Decimal("4"),
        "readiness": ready(),
        "previous_candle": candle("15", "10", closed=True),
        "current_candle": None,
    }

    assert entry_filter.evaluate(**arguments).start_cycle
    assert entry_filter.evaluate(**arguments).reason == "START_ALREADY_EMITTED"
    entry_filter.reset_after_cycle()
    assert entry_filter.evaluate(**arguments).start_cycle


def test_runtime_threshold_change_is_used_immediately_while_waiting() -> None:
    entry_filter = EntryFilter()
    signal = candle("15", "10", closed=True)
    first = entry_filter.evaluate(
        threshold=Decimal("6"),
        readiness=ready(),
        previous_candle=signal,
        current_candle=None,
    )
    second = entry_filter.evaluate(
        threshold=Decimal("4"),
        readiness=ready(),
        previous_candle=signal,
        current_candle=None,
    )

    assert first.reason == "RANGE_NOT_EXCEEDED"
    assert second.start_cycle
