"""Tests for UTC minute candle construction and validation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ai_impulse_trader.exceptions import DomainValidationError
from ai_impulse_trader.market_data import MarketDataStore, MinuteCandle

UTC = timezone.utc


def test_builds_forming_candle_and_rolls_previous_closed() -> None:
    store = MarketDataStore("GOLD")
    store.ingest_price(
        price=Decimal("4010"), observed_at=datetime(2026, 1, 1, 10, 0, 4, tzinfo=UTC)
    )
    store.ingest_price(
        price=Decimal("4015"), observed_at=datetime(2026, 1, 1, 10, 0, 40, tzinfo=UTC)
    )
    store.ingest_price(
        price=Decimal("4009"), observed_at=datetime(2026, 1, 1, 10, 0, 59, tzinfo=UTC)
    )

    current = store.candles()[1]
    assert current is not None
    assert current.range == Decimal("6")
    assert current.is_closed is False

    store.ingest_price(
        price=Decimal("4012"), observed_at=datetime(2026, 1, 1, 10, 1, tzinfo=UTC)
    )
    previous, current = store.candles()
    assert previous is not None and previous.is_closed
    assert previous.open == Decimal("4010")
    assert previous.high == Decimal("4015")
    assert previous.low == Decimal("4009")
    assert previous.close == Decimal("4009")
    assert current is not None and current.opened_at.minute == 1


def test_accepts_broker_closed_candle_and_reports_freshness() -> None:
    store = MarketDataStore("GOLD")
    candle = MinuteCandle(
        symbol="GOLD",
        opened_at=datetime(2026, 1, 1, 9, 59, tzinfo=UTC),
        open=Decimal("10"),
        high=Decimal("15"),
        low=Decimal("9"),
        close=Decimal("12"),
        is_closed=True,
    )
    store.ingest_closed_candle(candle)
    store.ingest_price(
        price=Decimal("12"), observed_at=datetime(2026, 1, 1, 10, 0, 10, tzinfo=UTC)
    )

    assert store.candles()[0] is candle
    assert store.is_fresh(
        datetime(2026, 1, 1, 10, 1, 20, tzinfo=UTC),
        maximum_age=timedelta(seconds=30),
    )
    assert not store.is_fresh(
        datetime(2026, 1, 1, 10, 1, 31, tzinfo=UTC),
        maximum_age=timedelta(seconds=30),
    )


def test_rejects_invalid_candles_and_out_of_order_prices() -> None:
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        MinuteCandle(
            "GOLD",
            datetime(2026, 1, 1, 10, 0),
            Decimal("10"),
            Decimal("10"),
            Decimal("10"),
            Decimal("10"),
            False,
        )

    store = MarketDataStore("GOLD")
    store.ingest_price(
        price=Decimal("10"), observed_at=datetime(2026, 1, 1, 10, 1, tzinfo=UTC)
    )
    with pytest.raises(DomainValidationError, match="out-of-order"):
        store.ingest_price(
            price=Decimal("10"),
            observed_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
