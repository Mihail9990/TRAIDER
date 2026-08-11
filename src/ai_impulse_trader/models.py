"""Validated, serializable trading domain models with exact Decimal values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple

from .enums import CycleState, PositionStatus, Side, TriggerStatus
from .exceptions import DomainValidationError


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Position:
    """One broker-confirmed or pending LONG/SHORT position."""

    position_id: str
    side: Side
    size: Decimal
    initial_entry: Decimal
    current_entry: Decimal
    status: PositionStatus
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    broker_position_id: Optional[str] = None
    opened_at: datetime = field(default_factory=utc_now)
    closed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_identifier("position_id", self.position_id)
        _require_enum("side", self.side, Side)
        _require_positive_decimal("size", self.size)
        _require_positive_decimal("initial_entry", self.initial_entry)
        _require_positive_decimal("current_entry", self.current_entry)
        _require_enum("status", self.status, PositionStatus)
        _require_optional_positive_decimal("stop_loss", self.stop_loss)
        _require_optional_positive_decimal("take_profit", self.take_profit)
        _require_optional_identifier("broker_position_id", self.broker_position_id)
        _require_aware_datetime("opened_at", self.opened_at)
        if self.closed_at is not None:
            _require_aware_datetime("closed_at", self.closed_at)
            if self.closed_at < self.opened_at:
                raise DomainValidationError("closed_at cannot precede opened_at")
        if self.status is PositionStatus.CLOSED and self.closed_at is None:
            raise DomainValidationError("closed position requires closed_at")
        if self.status is not PositionStatus.CLOSED and self.closed_at is not None:
            raise DomainValidationError("only a closed position may have closed_at")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible representation preserving Decimal precision."""
        return {
            "position_id": self.position_id,
            "side": self.side.value,
            "size": str(self.size),
            "initial_entry": str(self.initial_entry),
            "current_entry": str(self.current_entry),
            "status": self.status.value,
            "stop_loss": _decimal_to_text(self.stop_loss),
            "take_profit": _decimal_to_text(self.take_profit),
            "broker_position_id": self.broker_position_id,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": _datetime_to_text(self.closed_at),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Position":
        """Restore and validate a Position from persisted data."""
        return cls(
            position_id=_required(values, "position_id"),
            side=_parse_enum(Side, _required(values, "side"), "side"),
            size=_parse_decimal("size", _required(values, "size")),
            initial_entry=_parse_decimal(
                "initial_entry", _required(values, "initial_entry")
            ),
            current_entry=_parse_decimal(
                "current_entry", _required(values, "current_entry")
            ),
            status=_parse_enum(PositionStatus, _required(values, "status"), "status"),
            stop_loss=_parse_optional_decimal("stop_loss", values.get("stop_loss")),
            take_profit=_parse_optional_decimal(
                "take_profit", values.get("take_profit")
            ),
            broker_position_id=values.get("broker_position_id"),
            opened_at=_parse_datetime("opened_at", _required(values, "opened_at")),
            closed_at=_parse_optional_datetime("closed_at", values.get("closed_at")),
        )


@dataclass(frozen=True)
class Trigger:
    """A pending, executed, cancelled, or rejected Reentry trigger."""

    trigger_id: str
    side: Side
    price: Decimal
    size: Decimal
    status: TriggerStatus
    source_position_id: str
    broker_order_id: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_identifier("trigger_id", self.trigger_id)
        _require_enum("side", self.side, Side)
        _require_positive_decimal("price", self.price)
        _require_positive_decimal("size", self.size)
        _require_enum("status", self.status, TriggerStatus)
        _require_identifier("source_position_id", self.source_position_id)
        _require_optional_identifier("broker_order_id", self.broker_order_id)
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise DomainValidationError("updated_at cannot precede created_at")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible trigger representation."""
        return {
            "trigger_id": self.trigger_id,
            "side": self.side.value,
            "price": str(self.price),
            "size": str(self.size),
            "status": self.status.value,
            "source_position_id": self.source_position_id,
            "broker_order_id": self.broker_order_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Trigger":
        """Restore and validate a Trigger from persisted data."""
        return cls(
            trigger_id=_required(values, "trigger_id"),
            side=_parse_enum(Side, _required(values, "side"), "side"),
            price=_parse_decimal("price", _required(values, "price")),
            size=_parse_decimal("size", _required(values, "size")),
            status=_parse_enum(TriggerStatus, _required(values, "status"), "status"),
            source_position_id=_required(values, "source_position_id"),
            broker_order_id=values.get("broker_order_id"),
            created_at=_parse_datetime("created_at", _required(values, "created_at")),
            updated_at=_parse_datetime("updated_at", _required(values, "updated_at")),
        )


@dataclass(frozen=True)
class ReentryCostRecord:
    """One immutable cost increment produced by a confirmed Reentry."""

    index: int
    side: Side
    entry_stop_distance: Decimal
    close_commission: Decimal
    actual_slippage: Decimal
    total: Decimal
    confirmation_id: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise DomainValidationError("index must be an integer")
        if self.index < 1 or self.index > 7:
            raise DomainValidationError("Reentry index must be between 1 and 7")
        _require_enum("side", self.side, Side)
        _require_positive_decimal("entry_stop_distance", self.entry_stop_distance)
        _require_non_negative_decimal("close_commission", self.close_commission)
        _require_non_negative_decimal("actual_slippage", self.actual_slippage)
        _require_positive_decimal("total", self.total)
        expected = (
            self.entry_stop_distance + self.close_commission + self.actual_slippage
        )
        if self.total != expected:
            raise DomainValidationError("Reentry total does not match its components")
        _require_identifier("confirmation_id", self.confirmation_id)
        _require_aware_datetime("created_at", self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible immutable cost record."""
        return {
            "index": self.index,
            "side": self.side.value,
            "entry_stop_distance": str(self.entry_stop_distance),
            "close_commission": str(self.close_commission),
            "actual_slippage": str(self.actual_slippage),
            "total": str(self.total),
            "confirmation_id": self.confirmation_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "ReentryCostRecord":
        """Restore and validate a Reentry cost record."""
        return cls(
            index=_required(values, "index"),
            side=_parse_enum(Side, _required(values, "side"), "side"),
            entry_stop_distance=_parse_decimal(
                "entry_stop_distance", _required(values, "entry_stop_distance")
            ),
            close_commission=_parse_decimal(
                "close_commission", _required(values, "close_commission")
            ),
            actual_slippage=_parse_decimal(
                "actual_slippage", _required(values, "actual_slippage")
            ),
            total=_parse_decimal("total", _required(values, "total")),
            confirmation_id=_required(values, "confirmation_id"),
            created_at=_parse_datetime("created_at", _required(values, "created_at")),
        )


@dataclass(frozen=True)
class Cycle:
    """Complete persisted state of one automated or manual trading Cycle."""

    cycle_id: str
    symbol: str
    position_size: Decimal
    state: CycleState = CycleState.CREATED
    current_scenario: int = 1
    initial_long_entry: Optional[Decimal] = None
    initial_short_entry: Optional[Decimal] = None
    base_coverage: Optional[Decimal] = None
    initial_long_close_commission: Decimal = Decimal("0")
    initial_short_close_commission: Decimal = Decimal("0")
    actual_initial_slippage: Decimal = Decimal("0")
    saved_long_tp: Optional[Decimal] = None
    saved_short_tp: Optional[Decimal] = None
    total_reentry_cost: Decimal = Decimal("0")
    reentry_cost_history: Tuple[ReentryCostRecord, ...] = ()
    long_position: Optional[Position] = None
    short_position: Optional[Position] = None
    active_trigger: Optional[Trigger] = None
    manual_triggers: Tuple[Trigger, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    last_confirmed_step: Optional[str] = None

    def __post_init__(self) -> None:
        _require_identifier("cycle_id", self.cycle_id)
        _require_identifier("symbol", self.symbol)
        _require_positive_decimal("position_size", self.position_size)
        _require_enum("state", self.state, CycleState)
        if (
            isinstance(self.current_scenario, bool)
            or not isinstance(self.current_scenario, int)
            or not 1 <= self.current_scenario <= 9
        ):
            raise DomainValidationError("current_scenario must be between 1 and 9")
        for name in (
            "initial_long_entry",
            "initial_short_entry",
            "base_coverage",
            "saved_long_tp",
            "saved_short_tp",
        ):
            _require_optional_positive_decimal(name, getattr(self, name))
        _require_non_negative_decimal("total_reentry_cost", self.total_reentry_cost)
        for name in (
            "initial_long_close_commission",
            "initial_short_close_commission",
            "actual_initial_slippage",
        ):
            _require_non_negative_decimal(name, getattr(self, name))
        if not isinstance(self.reentry_cost_history, tuple):
            raise DomainValidationError("reentry_cost_history must be a tuple")
        expected_indices = tuple(range(1, len(self.reentry_cost_history) + 1))
        actual_indices = tuple(record.index for record in self.reentry_cost_history)
        if actual_indices != expected_indices:
            raise DomainValidationError("Reentry cost indexes must be sequential")
        history_total = sum(
            (record.total for record in self.reentry_cost_history), Decimal("0")
        )
        if history_total != self.total_reentry_cost:
            raise DomainValidationError(
                "total_reentry_cost must equal the Reentry history sum"
            )
        if self.long_position is not None and self.long_position.side is not Side.LONG:
            raise DomainValidationError("long_position must have LONG side")
        if (
            self.short_position is not None
            and self.short_position.side is not Side.SHORT
        ):
            raise DomainValidationError("short_position must have SHORT side")
        if not isinstance(self.manual_triggers, tuple):
            raise DomainValidationError("manual_triggers must be a tuple")
        _require_aware_datetime("created_at", self.created_at)
        if self.completed_at is not None:
            _require_aware_datetime("completed_at", self.completed_at)
            if self.completed_at < self.created_at:
                raise DomainValidationError("completed_at cannot precede created_at")
        if self.state in {CycleState.FINISHED, CycleState.ARCHIVED}:
            if self.completed_at is None:
                raise DomainValidationError("finished Cycle requires completed_at")
        elif self.completed_at is not None:
            raise DomainValidationError("unfinished Cycle cannot have completed_at")
        _require_optional_identifier("last_confirmed_step", self.last_confirmed_step)

    def to_dict(self) -> Dict[str, Any]:
        """Return a complete JSON-compatible Cycle snapshot."""
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "position_size": str(self.position_size),
            "state": self.state.value,
            "current_scenario": self.current_scenario,
            "initial_long_entry": _decimal_to_text(self.initial_long_entry),
            "initial_short_entry": _decimal_to_text(self.initial_short_entry),
            "base_coverage": _decimal_to_text(self.base_coverage),
            "initial_long_close_commission": str(self.initial_long_close_commission),
            "initial_short_close_commission": str(self.initial_short_close_commission),
            "actual_initial_slippage": str(self.actual_initial_slippage),
            "saved_long_tp": _decimal_to_text(self.saved_long_tp),
            "saved_short_tp": _decimal_to_text(self.saved_short_tp),
            "total_reentry_cost": str(self.total_reentry_cost),
            "reentry_cost_history": [
                record.to_dict() for record in self.reentry_cost_history
            ],
            "long_position": (
                self.long_position.to_dict() if self.long_position else None
            ),
            "short_position": (
                self.short_position.to_dict() if self.short_position else None
            ),
            "active_trigger": (
                self.active_trigger.to_dict() if self.active_trigger else None
            ),
            "manual_triggers": [trigger.to_dict() for trigger in self.manual_triggers],
            "created_at": self.created_at.isoformat(),
            "completed_at": _datetime_to_text(self.completed_at),
            "last_confirmed_step": self.last_confirmed_step,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "Cycle":
        """Restore and validate a complete Cycle snapshot."""
        if not isinstance(values, Mapping):
            raise DomainValidationError("model data must be an object")
        history_raw = values.get("reentry_cost_history", [])
        if not isinstance(history_raw, list):
            raise DomainValidationError("reentry_cost_history must be a list")
        manual_triggers_raw = values.get("manual_triggers", [])
        if not isinstance(manual_triggers_raw, list):
            raise DomainValidationError("manual_triggers must be a list")
        return cls(
            cycle_id=_required(values, "cycle_id"),
            symbol=_required(values, "symbol"),
            position_size=_parse_decimal(
                "position_size", _required(values, "position_size")
            ),
            state=_parse_enum(CycleState, _required(values, "state"), "state"),
            current_scenario=_required(values, "current_scenario"),
            initial_long_entry=_parse_optional_decimal(
                "initial_long_entry", values.get("initial_long_entry")
            ),
            initial_short_entry=_parse_optional_decimal(
                "initial_short_entry", values.get("initial_short_entry")
            ),
            base_coverage=_parse_optional_decimal(
                "base_coverage", values.get("base_coverage")
            ),
            initial_long_close_commission=_parse_decimal(
                "initial_long_close_commission",
                values.get("initial_long_close_commission", "0"),
            ),
            initial_short_close_commission=_parse_decimal(
                "initial_short_close_commission",
                values.get("initial_short_close_commission", "0"),
            ),
            actual_initial_slippage=_parse_decimal(
                "actual_initial_slippage",
                values.get("actual_initial_slippage", "0"),
            ),
            saved_long_tp=_parse_optional_decimal(
                "saved_long_tp", values.get("saved_long_tp")
            ),
            saved_short_tp=_parse_optional_decimal(
                "saved_short_tp", values.get("saved_short_tp")
            ),
            total_reentry_cost=_parse_decimal(
                "total_reentry_cost", _required(values, "total_reentry_cost")
            ),
            reentry_cost_history=tuple(
                ReentryCostRecord.from_dict(record) for record in history_raw
            ),
            long_position=_parse_optional_model(
                Position, "long_position", values.get("long_position")
            ),
            short_position=_parse_optional_model(
                Position, "short_position", values.get("short_position")
            ),
            active_trigger=_parse_optional_model(
                Trigger, "active_trigger", values.get("active_trigger")
            ),
            manual_triggers=tuple(
                Trigger.from_dict(trigger) for trigger in manual_triggers_raw
            ),
            created_at=_parse_datetime("created_at", _required(values, "created_at")),
            completed_at=_parse_optional_datetime(
                "completed_at", values.get("completed_at")
            ),
            last_confirmed_step=values.get("last_confirmed_step"),
        )


@dataclass(frozen=True)
class ApplicationState:
    """Global state that survives and controls creation of future Cycles."""

    stop_after_current_cycle: bool = False
    active_cycle_id: Optional[str] = None
    recovery_required: bool = False
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.stop_after_current_cycle, bool):
            raise DomainValidationError("stop_after_current_cycle must be boolean")
        _require_optional_identifier("active_cycle_id", self.active_cycle_id)
        if not isinstance(self.recovery_required, bool):
            raise DomainValidationError("recovery_required must be boolean")
        _require_aware_datetime("updated_at", self.updated_at)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible global application state."""
        return {
            "stop_after_current_cycle": self.stop_after_current_cycle,
            "active_cycle_id": self.active_cycle_id,
            "recovery_required": self.recovery_required,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "ApplicationState":
        """Restore and validate global state from persisted data."""
        return cls(
            stop_after_current_cycle=_required(values, "stop_after_current_cycle"),
            active_cycle_id=values.get("active_cycle_id"),
            recovery_required=_required(values, "recovery_required"),
            updated_at=_parse_datetime("updated_at", _required(values, "updated_at")),
        )


def _required(values: Mapping[str, Any], name: str) -> Any:
    if not isinstance(values, Mapping):
        raise DomainValidationError("model data must be an object")
    if name not in values:
        raise DomainValidationError(f"missing required field: {name}")
    return values[name]


def _parse_decimal(name: str, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise DomainValidationError(f"{name} must be a decimal number")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DomainValidationError(f"{name} must be a decimal number") from exc
    if not parsed.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    return parsed


def _parse_optional_decimal(name: str, value: Any) -> Optional[Decimal]:
    return None if value is None else _parse_decimal(name, value)


def _parse_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, str):
        raise DomainValidationError(f"{name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DomainValidationError(f"{name} must be an ISO datetime string") from exc
    _require_aware_datetime(name, parsed)
    return parsed


def _parse_optional_datetime(name: str, value: Any) -> Optional[datetime]:
    return None if value is None else _parse_datetime(name, value)


def _parse_enum(enum_type: Any, value: Any, name: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} has an invalid value") from exc


def _parse_optional_model(model_type: Any, name: str, value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{name} must be an object or null")
    return model_type.from_dict(value)


def _require_identifier(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} must be a non-empty string")


def _require_optional_identifier(name: str, value: Any) -> None:
    if value is not None:
        _require_identifier(name, value)


def _require_enum(name: str, value: Any, enum_type: Any) -> None:
    if not isinstance(value, enum_type):
        raise DomainValidationError(f"{name} must be a {enum_type.__name__}")


def _require_positive_decimal(name: str, value: Any) -> None:
    _require_non_negative_decimal(name, value)
    if value == 0:
        raise DomainValidationError(f"{name} must be positive")


def _require_non_negative_decimal(name: str, value: Any) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise DomainValidationError(f"{name} must be finite")
    if value < 0:
        raise DomainValidationError(f"{name} must be non-negative")


def _require_optional_positive_decimal(name: str, value: Any) -> None:
    if value is not None:
        _require_positive_decimal(name, value)


def _require_aware_datetime(name: str, value: Any) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DomainValidationError(f"{name} must be timezone-aware")


def _decimal_to_text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value)


def _datetime_to_text(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.isoformat()
