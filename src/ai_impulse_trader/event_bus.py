"""Small synchronous event bus with deterministic and idempotent delivery."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from .exceptions import DomainValidationError

EventHandler = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    """Immutable event exchanged between runtime modules."""

    event_id: str
    event_type: str
    created_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    cycle_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_text("event_id", self.event_id)
        _require_text("event_type", self.event_type)
        if not isinstance(self.created_at, datetime):
            raise DomainValidationError("created_at must be a datetime")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise DomainValidationError("created_at must be timezone-aware")
        if not isinstance(self.payload, Mapping):
            raise DomainValidationError("payload must be a mapping")
        if self.cycle_id is not None:
            _require_text("cycle_id", self.cycle_id)
        if self.correlation_id is not None:
            _require_text("correlation_id", self.correlation_id)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        event_type: str,
        payload: Optional[Mapping[str, Any]] = None,
        cycle_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> "Event":
        """Create an event using an aware UTC timestamp."""
        return cls(
            event_id=event_id,
            event_type=event_type,
            created_at=datetime.now(timezone.utc),
            payload=payload or {},
            cycle_id=cycle_id,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True)
class DeliveryFailure:
    """One subscriber exception captured without hiding other deliveries."""

    subscriber_id: str
    exception_type: str
    message: str


@dataclass(frozen=True)
class PublishReport:
    """Auditable delivery outcome returned to the event producer."""

    event_id: str
    event_type: str
    delivered: Tuple[str, ...]
    skipped: Tuple[str, ...]
    failures: Tuple[DeliveryFailure, ...]

    @property
    def success(self) -> bool:
        """Return true when every pending subscriber completed successfully."""
        return not self.failures


@dataclass(frozen=True)
class _Subscription:
    subscriber_id: str
    event_type: str
    handler: EventHandler
    priority: int
    sequence: int
    once: bool


class EventBus:
    """Deliver events synchronously in priority order with per-subscriber retries."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._subscriptions: Dict[str, _Subscription] = {}
        self._delivered: Set[Tuple[str, str]] = set()
        self._sequence = 0
        self._lock = threading.RLock()

    def subscribe(
        self,
        *,
        subscriber_id: str,
        event_type: str,
        handler: EventHandler,
        priority: int = 0,
        once: bool = False,
    ) -> None:
        """Register one stable subscriber ID for an exact event type or `*`."""
        _require_text("subscriber_id", subscriber_id)
        _require_text("event_type", event_type)
        if not callable(handler):
            raise DomainValidationError("handler must be callable")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise DomainValidationError("priority must be an integer")
        if not isinstance(once, bool):
            raise DomainValidationError("once must be boolean")
        with self._lock:
            if subscriber_id in self._subscriptions:
                raise DomainValidationError(
                    f"subscriber_id is already registered: {subscriber_id}"
                )
            self._sequence += 1
            self._subscriptions[subscriber_id] = _Subscription(
                subscriber_id,
                event_type,
                handler,
                priority,
                self._sequence,
                once,
            )

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Remove a subscriber and return whether it existed."""
        _require_text("subscriber_id", subscriber_id)
        with self._lock:
            return self._subscriptions.pop(subscriber_id, None) is not None

    def publish(self, event: Event) -> PublishReport:
        """Deliver an event; retries invoke only subscribers not yet successful."""
        if not isinstance(event, Event):
            raise DomainValidationError("event must be an Event")
        with self._lock:
            subscriptions = sorted(
                (
                    item
                    for item in self._subscriptions.values()
                    if item.event_type in {event.event_type, "*"}
                ),
                key=lambda item: (-item.priority, item.sequence),
            )

        delivered: List[str] = []
        skipped: List[str] = []
        failures: List[DeliveryFailure] = []
        for subscription in subscriptions:
            delivery_key = (event.event_id, subscription.subscriber_id)
            with self._lock:
                if delivery_key in self._delivered:
                    skipped.append(subscription.subscriber_id)
                    continue
                current = self._subscriptions.get(subscription.subscriber_id)
                if current is not subscription:
                    skipped.append(subscription.subscriber_id)
                    continue
            try:
                subscription.handler(event)
            except Exception as exc:  # handler boundary: report and continue delivery
                self._logger.exception(
                    "Event subscriber failed",
                    extra={
                        "event": event.event_type,
                        "event_id": event.event_id,
                        "subscriber_id": subscription.subscriber_id,
                    },
                )
                failures.append(
                    DeliveryFailure(
                        subscription.subscriber_id,
                        type(exc).__name__,
                        str(exc),
                    )
                )
                continue
            with self._lock:
                self._delivered.add(delivery_key)
                if subscription.once:
                    self._subscriptions.pop(subscription.subscriber_id, None)
            delivered.append(subscription.subscriber_id)

        return PublishReport(
            event.event_id,
            event.event_type,
            tuple(delivered),
            tuple(skipped),
            tuple(failures),
        )

    def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """Return the total or event-specific number of subscribers."""
        if event_type is not None:
            _require_text("event_type", event_type)
        with self._lock:
            if event_type is None:
                return len(self._subscriptions)
            return sum(
                item.event_type in {event_type, "*"}
                for item in self._subscriptions.values()
            )

    def clear_delivery_history(self, event_id: Optional[str] = None) -> int:
        """Clear completed delivery keys, normally after durable event archival."""
        if event_id is not None:
            _require_text("event_id", event_id)
        with self._lock:
            selected = {
                key for key in self._delivered if event_id is None or key[0] == event_id
            }
            self._delivered.difference_update(selected)
            return len(selected)


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be a non-empty string")
