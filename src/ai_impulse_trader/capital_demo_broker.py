"""Capital.com DEMO REST adapter with confirmation-first trade semantics.

The adapter deliberately implements only behavior established by the public API
contract.  In particular, it never retries a trading write and does not invent
partial-close or individual SL/TP removal payloads.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .broker_gateway import BrokerConfirmation
from .enums import PositionStatus, Side, TriggerStatus
from .exceptions import (
    BrokerAmbiguousOutcomeError,
    BrokerError,
    BrokerNotSupportedError,
    BrokerRejectedError,
)
from .models import Position, Trigger

DEMO_REST_BASE = "https://demo-api-capital.backend-capital.com"


@dataclass(frozen=True)
class CapitalHttpResponse:
    """Minimal transport result retained for deterministic unit tests."""

    status: int
    headers: Mapping[str, str]
    body: Mapping[str, Any]


class CapitalHttpTransport(Protocol):
    """Injectable HTTP boundary; production uses the Python standard library."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[Mapping[str, Any]],
        timeout: float,
    ) -> CapitalHttpResponse:
        """Send one JSON request without applying retries."""


class UrllibCapitalTransport:
    """Pydroid-compatible JSON transport with no third-party dependency."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[Mapping[str, Any]],
        timeout: float,
    ) -> CapitalHttpResponse:
        encoded = None
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=encoded, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return _decode_response(
                    response.status,
                    dict(response.headers.items()),
                    response.read(),
                )
        except HTTPError as exc:
            return _decode_response(exc.code, dict(exc.headers.items()), exc.read())
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise BrokerError("Capital.com transport failed") from exc


class CapitalDemoBroker:
    """BrokerGateway implementation pinned to the Capital.com DEMO host."""

    def __init__(
        self,
        *,
        api_key: str,
        identifier: str,
        password: str,
        epic: str,
        transport: Optional[CapitalHttpTransport] = None,
        timeout: float = 10.0,
        confirmation_attempts: int = 5,
        confirmation_delay: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
        partial_fill_notifier: Optional[Callable[[BrokerConfirmation], None]] = None,
    ) -> None:
        self._api_key = _require_secret("api_key", api_key)
        self._identifier = _require_secret("identifier", identifier)
        self._password = _require_secret("password", password)
        self._epic = _require_text("epic", epic)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if isinstance(confirmation_attempts, bool) or confirmation_attempts < 1:
            raise ValueError("confirmation_attempts must be a positive integer")
        if confirmation_delay < 0:
            raise ValueError("confirmation_delay cannot be negative")
        self._transport = transport or UrllibCapitalTransport()
        self._timeout = timeout
        self._confirmation_attempts = confirmation_attempts
        self._confirmation_delay = confirmation_delay
        self._sleep = sleeper
        self._partial_fill_notifier = partial_fill_notifier
        self._cst: Optional[str] = None
        self._security_token: Optional[str] = None
        self._deal_references: Dict[str, str] = {}
        self._requests: Dict[str, Tuple[str, Optional[Side], Optional[Decimal]]] = {}

    def authenticate(self) -> None:
        """Create a DEMO session and retain both required response tokens."""
        response = self._raw_request(
            "POST",
            "/api/v1/session",
            body={
                "identifier": self._identifier,
                "password": self._password,
                "encryptedPassword": False,
            },
            authenticated=False,
        )
        _raise_for_response(response, "session creation")
        cst = _header(response.headers, "CST")
        token = _header(response.headers, "X-SECURITY-TOKEN")
        if not cst or not token:
            raise BrokerError("Capital.com session response omitted security tokens")
        self._cst = cst
        self._security_token = token

    def logout(self) -> None:
        """End the current REST session if one exists."""
        if self._cst is not None:
            response = self._request("DELETE", "/api/v1/session", safe_read=False)
            _raise_for_response(response, "session logout")
        self._cst = None
        self._security_token = None

    def preflight_hedging(self) -> None:
        """Require the active account to support simultaneous LONG and SHORT."""
        response = self._request("GET", "/api/v1/accounts/preferences")
        _raise_for_response(response, "account preferences")
        if response.body.get("hedgingMode") is not True:
            raise BrokerRejectedError("Capital.com account requires hedgingMode=true")

    def quote(self) -> Tuple[Decimal, Decimal]:
        response = self._request("GET", f"/api/v1/markets/{self._epic}")
        _raise_for_response(response, "market details")
        snapshot = _mapping(response.body.get("snapshot"), "snapshot")
        return _decimal(snapshot.get("bid"), "snapshot.bid"), _decimal(
            snapshot.get("offer"), "snapshot.offer"
        )

    def open_position(
        self, *, request_id: str, side: Side, size: Decimal
    ) -> BrokerConfirmation:
        _require_request(request_id, side, size)
        return self._trade(
            request_id,
            "OPEN_POSITION",
            "POST",
            "/api/v1/positions",
            {"epic": self._epic, "direction": _direction(side), "size": str(size)},
            side=side,
            requested_size=size,
        )

    def set_stop_loss(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        return self._update_level(request_id, position_id, price, "stopLevel")

    def set_take_profit(
        self, *, request_id: str, position_id: str, price: Decimal
    ) -> BrokerConfirmation:
        return self._update_level(request_id, position_id, price, "profitLevel")

    def remove_stop_loss(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        raise BrokerNotSupportedError(
            "Capital.com public contract does not define individual SL removal"
        )

    def remove_take_profit(
        self, *, request_id: str, position_id: str
    ) -> BrokerConfirmation:
        raise BrokerNotSupportedError(
            "Capital.com public contract does not define individual TP removal"
        )

    def close_position(
        self, *, request_id: str, position_id: str, reason: str = "MANUAL"
    ) -> BrokerConfirmation:
        _require_text("reason", reason)
        return self._trade(
            request_id,
            "CLOSE_POSITION",
            "DELETE",
            f"/api/v1/positions/{_require_text('position_id', position_id)}",
            None,
        )

    def create_trigger(
        self,
        *,
        request_id: str,
        side: Side,
        price: Decimal,
        size: Decimal,
        source_position_id: str,
    ) -> BrokerConfirmation:
        _require_request(request_id, side, size)
        _require_decimal("price", price)
        _require_text("source_position_id", source_position_id)
        bid, ask = self.quote()
        order_type = _working_order_type(side, price, bid, ask)
        return self._trade(
            request_id,
            "CREATE_TRIGGER",
            "POST",
            "/api/v1/workingorders",
            {
                "epic": self._epic,
                "direction": _direction(side),
                "size": str(size),
                "level": str(price),
                "type": order_type,
            },
            side=side,
            requested_size=size,
        )

    def cancel_trigger(
        self, *, request_id: str, trigger_id: str
    ) -> BrokerConfirmation:
        return self._trade(
            request_id,
            "CANCEL_TRIGGER",
            "DELETE",
            f"/api/v1/workingorders/{_require_text('trigger_id', trigger_id)}",
            None,
        )

    def positions(self, *, include_closed: bool = False) -> Tuple[Position, ...]:
        response = self._request("GET", "/api/v1/positions")
        _raise_for_response(response, "positions")
        result = []
        for item in _list(response.body.get("positions"), "positions"):
            position = _mapping(item.get("position"), "position")
            deal_id = _require_text("dealId", position.get("dealId"))
            entry = _decimal(position.get("level"), "position.level")
            result.append(
                Position(
                    position_id=deal_id,
                    broker_position_id=deal_id,
                    side=_side(position.get("direction")),
                    size=_decimal(position.get("size"), "position.size"),
                    initial_entry=entry,
                    current_entry=entry,
                    status=PositionStatus.OPEN,
                    stop_loss=_optional_decimal(position.get("stopLevel")),
                    take_profit=_optional_decimal(position.get("profitLevel")),
                    opened_at=_date(position.get("createdDateUTC") or position.get("createdDate")),
                )
            )
        return tuple(result)

    def triggers(self, *, include_inactive: bool = False) -> Tuple[Trigger, ...]:
        response = self._request("GET", "/api/v1/workingorders")
        _raise_for_response(response, "working orders")
        result = []
        for item in _list(response.body.get("workingOrders"), "workingOrders"):
            order = _mapping(item.get("workingOrderData"), "workingOrderData")
            deal_id = _require_text("dealId", order.get("dealId"))
            created = _date(order.get("createdDateUTC") or order.get("createdDate"))
            result.append(
                Trigger(
                    trigger_id=deal_id,
                    broker_order_id=deal_id,
                    side=_side(order.get("direction")),
                    price=_decimal(order.get("orderLevel"), "orderLevel"),
                    size=_decimal(order.get("orderSize"), "orderSize"),
                    status=TriggerStatus.WAITING,
                    source_position_id="CAPITAL_MANUAL_OR_REENTRY",
                    created_at=created,
                    updated_at=created,
                )
            )
        return tuple(result)

    def confirmation_for(self, request_id: str) -> Optional[BrokerConfirmation]:
        _require_text("request_id", request_id)
        deal_reference = self._deal_references.get(request_id)
        if deal_reference is None:
            return None
        operation, side, requested_size = self._requests[request_id]
        response = self._request(
            "GET", f"/api/v1/confirms/{deal_reference}", safe_read=True
        )
        if response.status == 404:
            return None
        _raise_for_response(response, "deal confirmation")
        return _normalize_confirmation(
            request_id,
            operation,
            response.body,
            side=side,
            requested_size=requested_size,
        )

    def _update_level(
        self, request_id: str, position_id: str, price: Decimal, field: str
    ) -> BrokerConfirmation:
        _require_decimal("price", price)
        return self._trade(
            request_id,
            "SET_STOP_LOSS" if field == "stopLevel" else "SET_TAKE_PROFIT",
            "PUT",
            f"/api/v1/positions/{_require_text('position_id', position_id)}",
            {field: str(price)},
        )

    def _trade(
        self,
        request_id: str,
        operation: str,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]],
        *,
        side: Optional[Side] = None,
        requested_size: Optional[Decimal] = None,
    ) -> BrokerConfirmation:
        _require_text("request_id", request_id)
        existing = self.confirmation_for(request_id)
        if existing is not None:
            return existing
        try:
            response = self._request(method, path, body=body, safe_read=False)
        except BrokerError as exc:
            raise BrokerAmbiguousOutcomeError(request_id) from exc
        if response.status == 429 or response.status >= 500:
            raise BrokerAmbiguousOutcomeError(request_id)
        _raise_for_response(response, operation)
        deal_reference = response.body.get("dealReference")
        if not isinstance(deal_reference, str) or not deal_reference.strip():
            raise BrokerAmbiguousOutcomeError(request_id)
        self._deal_references[request_id] = deal_reference
        self._requests[request_id] = (operation, side, requested_size)
        for attempt in range(self._confirmation_attempts):
            confirmation = self.confirmation_for(request_id)
            if confirmation is not None:
                if confirmation.status == "REJECTED":
                    raise BrokerRejectedError(confirmation.reason or operation)
                if (
                    confirmation.status == "PARTIALLY_FILLED"
                    and self._partial_fill_notifier is not None
                ):
                    self._partial_fill_notifier(confirmation)
                return confirmation
            if attempt + 1 < self._confirmation_attempts:
                self._sleep(self._confirmation_delay)
        raise BrokerAmbiguousOutcomeError(request_id)

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        *,
        safe_read: bool = True,
    ) -> CapitalHttpResponse:
        if self._cst is None or self._security_token is None:
            self.authenticate()
        response = self._raw_request(method, path, body=body, authenticated=True)
        if response.status == 401 and safe_read:
            self.authenticate()
            response = self._raw_request(method, path, body=body, authenticated=True)
        return response

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]],
        authenticated: bool,
    ) -> CapitalHttpResponse:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CAP-API-KEY": self._api_key,
        }
        if authenticated:
            if self._cst is None or self._security_token is None:
                raise BrokerError("Capital.com session is not authenticated")
            headers["CST"] = self._cst
            headers["X-SECURITY-TOKEN"] = self._security_token
        return self._transport.request(
            method,
            DEMO_REST_BASE + path,
            headers,
            body,
            self._timeout,
        )


def _decode_response(
    status: int, headers: Mapping[str, str], raw: bytes
) -> CapitalHttpResponse:
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BrokerError("Capital.com returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise BrokerError("Capital.com JSON response must be an object")
    return CapitalHttpResponse(status, headers, decoded)


def _normalize_confirmation(
    request_id: str,
    operation: str,
    payload: Mapping[str, Any],
    *,
    side: Optional[Side],
    requested_size: Optional[Decimal],
) -> BrokerConfirmation:
    deal_status = str(payload.get("dealStatus", "UNKNOWN")).upper()
    broker_status = str(payload.get("status", "UNKNOWN")).upper()
    rejected = deal_status not in {"ACCEPTED", "SUCCESS"}
    filled_size = _optional_decimal(payload.get("size"))
    status = "REJECTED" if rejected else "CONFIRMED"
    if (
        not rejected
        and requested_size is not None
        and filled_size is not None
        and filled_size < requested_size
    ):
        status = "PARTIALLY_FILLED"
    affected = payload.get("affectedDeals")
    affected_id = None
    if isinstance(affected, list) and affected:
        first = affected[0]
        if isinstance(first, Mapping):
            raw_id = first.get("dealId")
            if isinstance(raw_id, str):
                affected_id = raw_id
    deal_id = payload.get("dealId") or affected_id
    is_trigger = "TRIGGER" in operation
    reason = payload.get("reason")
    if rejected and not reason:
        reason = broker_status
    return BrokerConfirmation(
        request_id=request_id,
        operation=operation,
        status=status,
        created_at=_date(payload.get("date")),
        position_id=None if is_trigger else _optional_text(deal_id),
        trigger_id=_optional_text(deal_id) if is_trigger else None,
        side=side or _optional_side(payload.get("direction")),
        size=filled_size,
        requested_size=requested_size,
        filled_size=filled_size,
        execution_price=_optional_decimal(payload.get("level")),
        reason=_optional_text(reason),
    )


def _working_order_type(side: Side, price: Decimal, bid: Decimal, ask: Decimal) -> str:
    if side is Side.LONG:
        if price > ask:
            return "STOP"
        if price < ask:
            return "LIMIT"
    elif side is Side.SHORT:
        if price < bid:
            return "STOP"
        if price > bid:
            return "LIMIT"
    raise BrokerRejectedError("trigger price equals the executable market price")


def _raise_for_response(response: CapitalHttpResponse, operation: str) -> None:
    if 200 <= response.status < 300:
        return
    reason = response.body.get("errorCode") or response.body.get("message")
    detail = f"Capital.com {operation} failed with HTTP {response.status}"
    if reason:
        detail += f": {reason}"
    raise BrokerRejectedError(detail)


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _direction(side: Side) -> str:
    if side is Side.LONG:
        return "BUY"
    if side is Side.SHORT:
        return "SELL"
    raise ValueError("side must be LONG or SHORT")


def _side(value: object) -> Side:
    if value == "BUY":
        return Side.LONG
    if value == "SELL":
        return Side.SHORT
    raise BrokerError(f"unknown Capital.com direction: {value!r}")


def _optional_side(value: object) -> Optional[Side]:
    return None if value is None else _side(value)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BrokerError(f"invalid Capital.com decimal field: {name}") from exc
    if not result.is_finite() or result <= 0:
        raise BrokerError(f"Capital.com field must be positive: {name}")
    return result


def _optional_decimal(value: object) -> Optional[Decimal]:
    return None if value is None else _decimal(value, "optional decimal")


def _date(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.now(timezone.utc)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BrokerError("invalid Capital.com timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BrokerError(f"Capital.com response field {name} must be an object")
    return value


def _list(value: object, name: str) -> list:
    if not isinstance(value, list):
        raise BrokerError(f"Capital.com response field {name} must be a list")
    return value


def _require_secret(name: str, value: object) -> str:
    return _require_text(name, value)


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    return _require_text("text", value)


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")


def _require_request(request_id: str, side: Side, size: Decimal) -> None:
    _require_text("request_id", request_id)
    _direction(side)
    _require_decimal("size", size)
