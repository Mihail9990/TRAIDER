"""Contract tests for the Capital.com DEMO REST adapter."""

from decimal import Decimal

import pytest

from ai_impulse_trader.capital_demo_broker import (
    DEMO_REST_BASE,
    CapitalDemoBroker,
    CapitalHttpResponse,
)
from ai_impulse_trader.enums import Side
from ai_impulse_trader.exceptions import (
    BrokerAmbiguousOutcomeError,
    BrokerNotSupportedError,
    BrokerRejectedError,
)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status=200, body=None, headers=None):
    return CapitalHttpResponse(status, headers or {}, body or {})


def session():
    return response(
        headers={"CST": "cst-secret", "X-SECURITY-TOKEN": "security-secret"}
    )


def broker(transport, **kwargs):
    return CapitalDemoBroker(
        api_key="api-secret",
        identifier="login",
        password="password",
        epic="GOLD-EPIC",
        transport=transport,
        confirmation_delay=0,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_authentication_and_quote_use_demo_host_and_tokens():
    transport = ScriptedTransport(
        [session(), response(body={"snapshot": {"bid": 2400.1, "offer": 2400.4}})]
    )

    assert broker(transport).quote() == (Decimal("2400.1"), Decimal("2400.4"))
    auth, quote = transport.calls
    assert auth[0:2] == ("POST", DEMO_REST_BASE + "/api/v1/session")
    assert auth[3]["encryptedPassword"] is False
    assert quote[2]["CST"] == "cst-secret"
    assert quote[2]["X-SECURITY-TOKEN"] == "security-secret"
    assert quote[2]["X-CAP-API-KEY"] == "api-secret"


def test_open_position_waits_for_confirmation_and_normalizes_fill():
    transport = ScriptedTransport(
        [
            session(),
            response(body={"dealReference": "o_ref"}),
            response(
                body={
                    "date": "2026-08-10T12:00:00Z",
                    "dealStatus": "ACCEPTED",
                    "status": "OPEN",
                    "dealId": "position-7",
                    "direction": "BUY",
                    "size": 1,
                    "level": 2401.25,
                }
            ),
        ]
    )

    confirmation = broker(transport).open_position(
        request_id="cycle:open-long", side=Side.LONG, size=Decimal("1")
    )

    assert confirmation.status == "CONFIRMED"
    assert confirmation.position_id == "position-7"
    assert confirmation.side is Side.LONG
    assert confirmation.requested_size == Decimal("1")
    assert confirmation.filled_size == Decimal("1")
    assert confirmation.execution_price == Decimal("2401.25")
    assert transport.calls[1][3] == {
        "epic": "GOLD-EPIC",
        "direction": "BUY",
        "size": "1",
    }
    assert transport.calls[2][1].endswith("/api/v1/confirms/o_ref")


def test_partial_fill_is_explicit_instead_of_claiming_full_confirmation():
    transport = ScriptedTransport(
        [
            session(),
            response(body={"dealReference": "partial-ref"}),
            response(
                body={
                    "dealStatus": "ACCEPTED",
                    "status": "OPEN",
                    "dealId": "position-partial",
                    "direction": "SELL",
                    "size": "0.4",
                    "level": "2400",
                }
            ),
        ]
    )

    notifications = []
    confirmation = broker(
        transport, partial_fill_notifier=notifications.append
    ).open_position(
        request_id="partial", side=Side.SHORT, size=Decimal("1")
    )

    assert confirmation.status == "PARTIALLY_FILLED"
    assert confirmation.requested_size == Decimal("1")
    assert confirmation.filled_size == Decimal("0.4")
    assert notifications == [confirmation]


@pytest.mark.parametrize(
    "side,price,bid,ask,expected",
    [
        (Side.LONG, "101", "99", "100", "STOP"),
        (Side.LONG, "98", "99", "100", "LIMIT"),
        (Side.SHORT, "98", "99", "100", "STOP"),
        (Side.SHORT, "101", "99", "100", "LIMIT"),
    ],
)
def test_trigger_selects_explicit_limit_or_stop(side, price, bid, ask, expected):
    transport = ScriptedTransport(
        [
            session(),
            response(body={"snapshot": {"bid": bid, "offer": ask}}),
            response(body={"dealReference": "trigger-ref"}),
            response(
                body={
                    "dealStatus": "ACCEPTED",
                    "status": "CREATED",
                    "dealId": "working-1",
                    "direction": "BUY" if side is Side.LONG else "SELL",
                    "size": "1",
                    "level": price,
                }
            ),
        ]
    )

    result = broker(transport).create_trigger(
        request_id="trigger",
        side=side,
        price=Decimal(price),
        size=Decimal("1"),
        source_position_id="position-1",
    )

    assert result.trigger_id == "working-1"
    assert transport.calls[2][3]["type"] == expected


def test_individual_level_removal_and_partial_close_are_not_invented():
    adapter = broker(ScriptedTransport([]))

    with pytest.raises(BrokerNotSupportedError, match="SL removal"):
        adapter.remove_stop_loss(request_id="remove-sl", position_id="p1")
    with pytest.raises(BrokerNotSupportedError, match="TP removal"):
        adapter.remove_take_profit(request_id="remove-tp", position_id="p1")


def test_write_transport_failure_is_ambiguous_and_is_not_retried():
    transport = ScriptedTransport([session(), BrokerRejectedError("network down")])

    with pytest.raises(BrokerAmbiguousOutcomeError):
        broker(transport).open_position(
            request_id="unknown-write", side=Side.LONG, size=Decimal("1")
        )

    assert len(transport.calls) == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_uncertain_write_http_status_is_ambiguous_and_not_retried(status):
    transport = ScriptedTransport([session(), response(status=status)])

    with pytest.raises(BrokerAmbiguousOutcomeError):
        broker(transport).close_position(request_id="close", position_id="p1")

    assert len(transport.calls) == 2


def test_safe_read_reauthenticates_once_after_401():
    transport = ScriptedTransport(
        [
            session(),
            response(status=401, body={"errorCode": "error.security.client-token-invalid"}),
            session(),
            response(body={"snapshot": {"bid": "1", "offer": "2"}}),
        ]
    )

    assert broker(transport).quote() == (Decimal("1"), Decimal("2"))
    assert [call[0] for call in transport.calls] == ["POST", "GET", "POST", "GET"]


def test_position_and_working_order_snapshots_are_normalized():
    transport = ScriptedTransport(
        [
            session(),
            response(
                body={
                    "positions": [
                        {
                            "position": {
                                "dealId": "p1",
                                "direction": "BUY",
                                "size": "0.5",
                                "level": "2450.2",
                                "stopLevel": "2440",
                                "profitLevel": "2470",
                                "createdDateUTC": "2026-08-10T12:00:00Z",
                            }
                        }
                    ]
                }
            ),
            response(
                body={
                    "workingOrders": [
                        {
                            "workingOrderData": {
                                "dealId": "w1",
                                "direction": "SELL",
                                "orderSize": "0.5",
                                "orderLevel": "2400",
                                "createdDateUTC": "2026-08-10T12:01:00Z",
                            }
                        }
                    ]
                }
            ),
        ]
    )
    adapter = broker(transport)

    positions = adapter.positions()
    triggers = adapter.triggers()

    assert positions[0].position_id == "p1"
    assert positions[0].stop_loss == Decimal("2440")
    assert positions[0].take_profit == Decimal("2470")
    assert triggers[0].trigger_id == "w1"
    assert triggers[0].side is Side.SHORT


def test_hedging_preflight_rejects_netting_account():
    transport = ScriptedTransport([session(), response(body={"hedgingMode": False})])

    with pytest.raises(BrokerRejectedError, match="hedgingMode=true"):
        broker(transport).preflight_hedging()
