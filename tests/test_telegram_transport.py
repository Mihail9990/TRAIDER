"""Tests for the dependency-free Telegram Bot API transport."""

import json
from pathlib import Path

import pytest

from ai_impulse_trader.config import ConfigManager
from ai_impulse_trader.telegram_commands import TelegramCommandService
from ai_impulse_trader.telegram_transport import (
    TelegramBotApi,
    TelegramPollingAdapter,
    TelegramTransportError,
)


class Response:
    def __init__(self, payload) -> None:
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return Response(self.responses.pop(0))


def test_send_message_uses_https_json_without_exposing_token_in_payload() -> None:
    opener = FakeOpener([{"ok": True, "result": {"message_id": 1}}])
    api = TelegramBotApi(
        token="123456:secret-token",
        default_chat_id=777,
        opener=opener,
    )

    api.send_message("Scenario 9")

    request, timeout = opener.requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url.startswith("https://api.telegram.org/bot")
    assert payload == {"chat_id": 777, "text": "Scenario 9"}
    assert "secret-token" not in request.data.decode("utf-8")
    assert timeout == 30


def test_get_updates_normalizes_text_and_ignores_non_text_messages() -> None:
    opener = FakeOpener(
        [
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 10,
                        "message": {
                            "from": {"id": 123},
                            "chat": {"id": 777},
                            "text": "/start_cycle",
                        },
                    },
                    {"update_id": 11, "message": {"photo": []}},
                ],
            }
        ]
    )
    api = TelegramBotApi(
        token="123456:secret-token", default_chat_id=777, opener=opener
    )

    updates = api.get_updates(offset=10, poll_timeout=0)

    assert len(updates) == 1
    assert updates[0].user_id == 123
    assert updates[0].text == "/start_cycle"
    payload = json.loads(opener.requests[0][0].data.decode("utf-8"))
    assert payload["offset"] == 10


def test_polling_adapter_executes_command_and_advances_offset(tmp_path: Path) -> None:
    opener = FakeOpener(
        [
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 40,
                        "message": {
                            "from": {"id": 123},
                            "chat": {"id": 777},
                            "text": "/set_entry_range 6.5",
                        },
                    }
                ],
            },
            {"ok": True, "result": {"message_id": 1}},
            {"ok": True, "result": []},
        ]
    )
    api = TelegramBotApi(
        token="123456:secret-token", default_chat_id=777, opener=opener
    )
    config = ConfigManager(tmp_path / "config.json")
    config.load(create_if_missing=True)
    commands = TelegramCommandService(config_manager=config, authorized_user_ids={123})
    polling = TelegramPollingAdapter(api=api, commands=commands)

    assert polling.poll_once(poll_timeout=0) == 1
    assert polling.poll_once(poll_timeout=0) == 0
    assert str(config.load().entry_candle_range_threshold) == "6.5"
    second_poll = json.loads(opener.requests[2][0].data.decode("utf-8"))
    assert second_poll["offset"] == 41


def test_rejected_api_response_raises_without_false_success() -> None:
    api = TelegramBotApi(
        token="123456:secret-token",
        default_chat_id=777,
        opener=FakeOpener([{"ok": False, "description": "Forbidden"}]),
    )
    with pytest.raises(TelegramTransportError, match="Forbidden"):
        api.send_message("test")
