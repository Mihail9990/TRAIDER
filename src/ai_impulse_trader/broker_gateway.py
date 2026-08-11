"""Broker-neutral contract used by trading orchestration code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol, Tuple, runtime_checkable

from .enums import Side
from .models import Position, Trigger


@dataclass(frozen=True)
class BrokerConfirmation:
    """Normalized immutable result returned by any broker adapter."""

    request_id: str
    operation: str
    status: str
    created_at: datetime
    position_id: Optional[str] = None
    trigger_id: Optional[str] = None
    side: Optional[Side] = None
    size: Optional[Decimal] = None
    requested_size: Optional[Decimal] = None
    filled_size: Optional[Decimal] = None
    execution_price: Optional[Decimal] = None
    commission: Decimal = Decimal("0")
    realized_pnl: Optional[Decimal] = None
    reason: Optional[str] = None


@runtime_checkable
class BrokerGateway(Protocol):
    """Minimum broker operations required by Order and Cycle managers."""

    def quote(self) -> Tuple[Decimal, Decimal]:
        """Return current `(bid, ask)`."""

    def open_position(
        self, *, request_id: str, side: Side, size: Decimal
    ) -> BrokerConfirmation:
        """Open one market position idempotently."""

    def set_stop_loss(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        """Set or replace Stop Loss."""

    def set_take_profit(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        """Set or replace Take Profit."""

    def remove_stop_loss(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        """Remove Stop Loss without closing the position."""

    def remove_take_profit(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        """Remove Take Profit without closing the position."""

    def close_position(
        self, *, request_id: str, position_id: str, reason: str = "MANUAL"
    ) -> BrokerConfirmation:
        """Close one position idempotently."""

    def create_trigger(
        self,
        *,
        request_id: str,
        side: Side,
        price: Decimal,
        size: Decimal,
        source_position_id: str,
    ) -> BrokerConfirmation:
        """Create a Reentry trigger."""

    def cancel_trigger(self, *, request_id: str, trigger_id: str) -> BrokerConfirmation:
        """Cancel a waiting trigger."""

    def positions(self, *, include_closed: bool = False) -> Tuple[Position, ...]:
        """Return normalized broker positions."""

    def triggers(self, *, include_inactive: bool = False) -> Tuple[Trigger, ...]:
        """Return normalized broker triggers."""

    def confirmation_for(self, request_id: str) -> Optional[BrokerConfirmation]:
        """Recover the result of a request after an uncertain timeout."""
