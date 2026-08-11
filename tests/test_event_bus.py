"""Tests for deterministic EventBus delivery and recovery behavior."""

from datetime import datetime, timezone

import pytest

from ai_impulse_trader.event_bus import Event, EventBus
from ai_impulse_trader.exceptions import DomainValidationError


def event(event_id: str = "event-1", event_type: str = "START_CYCLE") -> Event:
    return Event(
        event_id=event_id,
        event_type=event_type,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        payload={"symbol": "GOLD"},
        cycle_id="cycle-1",
    )


def test_delivers_in_priority_then_registration_order() -> None:
    bus = EventBus()
    calls = []
    bus.subscribe(
        subscriber_id="normal-1",
        event_type="START_CYCLE",
        handler=lambda item: calls.append("normal-1"),
    )
    bus.subscribe(
        subscriber_id="high",
        event_type="START_CYCLE",
        handler=lambda item: calls.append("high"),
        priority=10,
    )
    bus.subscribe(
        subscriber_id="normal-2",
        event_type="START_CYCLE",
        handler=lambda item: calls.append("normal-2"),
    )

    report = bus.publish(event())

    assert calls == ["high", "normal-1", "normal-2"]
    assert report.delivered == ("high", "normal-1", "normal-2")
    assert report.success


def test_duplicate_publish_is_idempotent_per_subscriber() -> None:
    bus = EventBus()
    calls = []
    bus.subscribe(
        subscriber_id="cycle-manager",
        event_type="START_CYCLE",
        handler=lambda item: calls.append(item.event_id),
    )

    first = bus.publish(event())
    duplicate = bus.publish(event())

    assert first.delivered == ("cycle-manager",)
    assert duplicate.skipped == ("cycle-manager",)
    assert calls == ["event-1"]


def test_failure_does_not_block_other_subscribers_and_only_failure_retries() -> None:
    bus = EventBus()
    calls = []
    attempts = {"count": 0}

    def unstable(item: Event) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")
        calls.append("unstable")

    bus.subscribe(
        subscriber_id="unstable", event_type="BROKER_CONFIRMED", handler=unstable
    )
    bus.subscribe(
        subscriber_id="state-manager",
        event_type="BROKER_CONFIRMED",
        handler=lambda item: calls.append("state-manager"),
    )
    broker_event = event(event_type="BROKER_CONFIRMED")

    failed = bus.publish(broker_event)
    recovered = bus.publish(broker_event)

    assert not failed.success
    assert failed.failures[0].subscriber_id == "unstable"
    assert recovered.delivered == ("unstable",)
    assert recovered.skipped == ("state-manager",)
    assert calls == ["state-manager", "unstable"]


def test_wildcard_once_and_unsubscribe() -> None:
    bus = EventBus()
    calls = []
    bus.subscribe(
        subscriber_id="audit-once",
        event_type="*",
        handler=lambda item: calls.append(item.event_type),
        once=True,
    )

    bus.publish(event())
    bus.publish(event("event-2", "STOP_LOSS"))

    assert calls == ["START_CYCLE"]
    assert bus.subscriber_count() == 0
    assert not bus.unsubscribe("audit-once")


def test_event_payload_is_immutable_copy() -> None:
    payload = {"symbol": "GOLD"}
    item = Event.create(event_id="immutable", event_type="QUOTE", payload=payload)
    payload["symbol"] = "SILVER"

    assert item.payload["symbol"] == "GOLD"
    with pytest.raises(TypeError):
        item.payload["symbol"] = "OIL"  # type: ignore[index]


def test_validates_subscriptions_and_events() -> None:
    bus = EventBus()
    with pytest.raises(DomainValidationError):
        bus.subscribe(subscriber_id="", event_type="QUOTE", handler=lambda item: None)
    bus.subscribe(subscriber_id="unique", event_type="QUOTE", handler=lambda item: None)
    with pytest.raises(DomainValidationError, match="already registered"):
        bus.subscribe(
            subscriber_id="unique", event_type="QUOTE", handler=lambda item: None
        )
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        Event("id", "QUOTE", datetime(2026, 1, 1), {})
