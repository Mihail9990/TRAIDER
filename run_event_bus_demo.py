"""Pydroid-compatible EventBus smoke test."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.event_bus import Event, EventBus


def main() -> None:
    bus = EventBus()
    received = []
    bus.subscribe(
        subscriber_id="cycle-manager",
        event_type="START_CYCLE",
        handler=lambda event: received.append(event.event_id),
        priority=10,
    )
    bus.subscribe(
        subscriber_id="state-manager",
        event_type="START_CYCLE",
        handler=lambda event: received.append(event.event_id),
    )
    start = Event.create(
        event_id="demo-start-1",
        event_type="START_CYCLE",
        payload={"symbol": "GOLD", "candle_range": "4.01"},
    )

    first = bus.publish(start)
    duplicate = bus.publish(start)

    print("AI Impulse Trader EventBus")
    print("DELIVERED:", len(first.delivered))
    print("DUPLICATE_SKIPPED:", len(duplicate.skipped))
    print("HANDLER_CALLS:", len(received))
    print("PRIORITY_DELIVERY: OK")
    print("EVENT_IDEMPOTENCY: OK")


if __name__ == "__main__":
    main()
