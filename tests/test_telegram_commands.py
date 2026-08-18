"""Tests for transport-independent authorized Telegram commands."""

import json
from decimal import Decimal

import pytest

from ai_impulse_trader.config import ConfigManager
from ai_impulse_trader.exceptions import DomainValidationError
from ai_impulse_trader.telegram_commands import TelegramCommandService


@pytest.fixture
def service(tmp_path):
    manager = ConfigManager(tmp_path / "config.json")
    manager.load(create_if_missing=True)
    return (
        TelegramCommandService(
            config_manager=manager,
            authorized_user_ids={12345},
        ),
        manager,
        tmp_path / "config.json",
    )


def test_stop_after_cycle_is_persisted_without_requesting_immediate_stop(
    service,
) -> None:
    commands, manager, path = service

    result = commands.handle(user_id=12345, text="/stop_after_cycle")

    assert result.accepted
    assert not result.request_cycle_start
    assert manager.load().stop_after_current_cycle is True
    assert (
        json.loads(path.read_text(encoding="utf-8"))["stop_after_current_cycle"] is True
    )


def test_start_cycle_clears_flag_and_only_requests_filtered_start(service) -> None:
    commands, manager, path = service
    manager.set_stop_after_current_cycle(True)

    result = commands.handle(user_id="0012345", text="/start_cycle")

    assert result.accepted
    assert result.request_cycle_start
    assert manager.load().stop_after_current_cycle is False
    assert (
        json.loads(path.read_text(encoding="utf-8"))["stop_after_current_cycle"]
        is False
    )


def test_set_entry_range_validates_and_atomically_persists(service) -> None:
    commands, manager, _ = service

    result = commands.handle(user_id=12345, text="/set_entry_range 5.75")

    assert result.accepted
    assert manager.load().entry_candle_range_threshold == Decimal("5.75")
    assert "5.75" in result.message


@pytest.mark.parametrize("value", ["0", "-1", "nan", "abc"])
def test_invalid_entry_range_does_not_change_configuration(service, value) -> None:
    commands, manager, _ = service

    result = commands.handle(user_id=12345, text="/set_entry_range " + value)

    assert not result.accepted
    assert result.message.startswith("INVALID_ENTRY_RANGE")
    assert manager.load().entry_candle_range_threshold == Decimal("4")


def test_unauthorized_user_cannot_change_any_setting(service) -> None:
    commands, manager, _ = service

    result = commands.handle(user_id=99999, text="/set_entry_range 100")

    assert not result.accepted
    assert result.message == "UNAUTHORIZED"
    assert manager.load().entry_candle_range_threshold == Decimal("4")


def test_bot_command_suffix_and_usage_errors(service) -> None:
    commands, _, _ = service

    result = commands.handle(
        user_id=12345,
        text="/stop_after_cycle@ai_impulse_trader_bot",
    )
    usage = commands.handle(user_id=12345, text="/start_cycle now")
    unknown = commands.handle(user_id=12345, text="/close_everything")

    assert result.accepted
    assert not usage.accepted and usage.message == "USAGE: /start_cycle"
    assert not unknown.accepted and unknown.message == "UNKNOWN_COMMAND"


def test_requires_at_least_one_valid_authorized_user(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load(create_if_missing=True)

    with pytest.raises(DomainValidationError):
        TelegramCommandService(config_manager=manager, authorized_user_ids=[])
    with pytest.raises(DomainValidationError):
        TelegramCommandService(config_manager=manager, authorized_user_ids=[True])
