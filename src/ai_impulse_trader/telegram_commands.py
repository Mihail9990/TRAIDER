"""Transport-independent Telegram command validation and configuration actions."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Set

from .config import ConfigManager
from .exceptions import BrokerError, ConfigurationError, DomainValidationError
from .enums import Side
from .manual_trading import ManualTradingError, ManualTradingService


@dataclass(frozen=True)
class TelegramCommandResult:
    """Safe response returned to a future Telegram network adapter."""

    accepted: bool
    command: Optional[str]
    message: str
    request_cycle_start: bool = False


class TelegramCommandService:
    """Authorize and execute supported operator commands without network code."""

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        authorized_user_ids: Iterable[object],
        manual_trading: Optional[ManualTradingService] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not isinstance(config_manager, ConfigManager):
            raise DomainValidationError("config_manager must be a ConfigManager")
        self._authorized_user_ids = _normalize_user_ids(authorized_user_ids)
        if not self._authorized_user_ids:
            raise DomainValidationError("at least one authorized user ID is required")
        self._config_manager = config_manager
        self._manual_trading = manual_trading
        self._logger = logger or logging.getLogger(__name__)

    def handle(
        self, *, user_id: object, text: str, command_id: Optional[str] = None
    ) -> TelegramCommandResult:
        """Validate one message and perform only its explicitly supported action."""
        normalized_user_id = _normalize_user_id(user_id)
        if normalized_user_id not in self._authorized_user_ids:
            self._logger.warning(
                "Rejected unauthorized Telegram command",
                extra={"telegram_user_id": normalized_user_id},
            )
            return TelegramCommandResult(False, None, "UNAUTHORIZED")
        if not isinstance(text, str) or not text.strip():
            return TelegramCommandResult(False, None, "EMPTY_COMMAND")
        try:
            parts = shlex.split(text.strip())
        except ValueError:
            return TelegramCommandResult(False, None, "INVALID_COMMAND_SYNTAX")
        if not parts:
            return TelegramCommandResult(False, None, "EMPTY_COMMAND")

        command = parts[0].split("@", 1)[0].lower()
        if command == "/stop_after_cycle":
            return self._stop_after_cycle(parts)
        if command == "/start_cycle":
            return self._start_cycle(parts)
        if command == "/set_entry_range":
            return self._set_entry_range(parts)
        manual = self._manual_command(command, parts, command_id=command_id)
        if manual is not None:
            return manual
        return TelegramCommandResult(False, command, "UNKNOWN_COMMAND")

    def _manual_command(
        self, command: str, parts: list, *, command_id: Optional[str]
    ) -> Optional[TelegramCommandResult]:
        level_commands = {
            "/set_long_sl": ("set", Side.LONG, "SL"),
            "/set_long_tp": ("set", Side.LONG, "TP"),
            "/set_short_sl": ("set", Side.SHORT, "SL"),
            "/set_short_tp": ("set", Side.SHORT, "TP"),
            "/remove_long_sl": ("remove", Side.LONG, "SL"),
            "/remove_long_tp": ("remove", Side.LONG, "TP"),
            "/remove_short_sl": ("remove", Side.SHORT, "SL"),
            "/remove_short_tp": ("remove", Side.SHORT, "TP"),
        }
        known = set(level_commands) | {
            "/set_long_trigger",
            "/set_short_trigger",
            "/cancel_trigger",
            "/close_long",
            "/close_short",
        }
        if command not in known:
            return None
        if self._manual_trading is None:
            return TelegramCommandResult(
                False, command, "MANUAL_TRADING_NOT_CONFIGURED"
            )
        try:
            if command in level_commands:
                action, side, level = level_commands[command]
                if action == "set" and len(parts) in {2, 3}:
                    result = self._manual_trading.set_level(
                        side=side,
                        level=level,
                        price=_decimal(parts[1]),
                        position_id=parts[2] if len(parts) == 3 else None,
                    )
                elif action == "remove" and len(parts) in {1, 2}:
                    result = self._manual_trading.remove_level(
                        side=side,
                        level=level,
                        position_id=parts[1] if len(parts) == 2 else None,
                    )
                else:
                    return TelegramCommandResult(
                        False, command, "INVALID_MANUAL_COMMAND_USAGE"
                    )
            elif (
                command in {"/set_long_trigger", "/set_short_trigger"}
                and len(parts) == 4
            ):
                result = self._manual_trading.create_trigger(
                    side=Side.LONG if command == "/set_long_trigger" else Side.SHORT,
                    price=_decimal(parts[1]),
                    size=_decimal(parts[2]),
                    source_position_id=parts[3],
                )
            elif command == "/cancel_trigger" and len(parts) == 2:
                result = self._manual_trading.cancel_trigger(parts[1])
            elif command in {"/close_long", "/close_short"} and len(parts) in {1, 2}:
                result = self._manual_trading.close_position(
                    side=Side.LONG if command == "/close_long" else Side.SHORT,
                    position_id=parts[1] if len(parts) == 2 else None,
                    action_id=command_id,
                )
            else:
                return TelegramCommandResult(
                    False, command, "INVALID_MANUAL_COMMAND_USAGE"
                )
        except (
            BrokerError,
            DomainValidationError,
            ManualTradingError,
            InvalidOperation,
        ) as exc:
            return TelegramCommandResult(
                False, command, "MANUAL_COMMAND_FAILED: " + str(exc)
            )
        return TelegramCommandResult(True, command, result.message)

    def _stop_after_cycle(self, parts: list) -> TelegramCommandResult:
        if len(parts) != 1:
            return TelegramCommandResult(
                False, "/stop_after_cycle", "USAGE: /stop_after_cycle"
            )
        self._config_manager.set_stop_after_current_cycle(True)
        return TelegramCommandResult(
            True,
            "/stop_after_cycle",
            "Текущий Cycle продолжится; следующий Cycle не будет запущен.",
        )

    def _start_cycle(self, parts: list) -> TelegramCommandResult:
        if len(parts) != 1:
            return TelegramCommandResult(False, "/start_cycle", "USAGE: /start_cycle")
        self._config_manager.set_stop_after_current_cycle(False)
        return TelegramCommandResult(
            True,
            "/start_cycle",
            "Остановка снята; запрос старта передан входному фильтру.",
            request_cycle_start=True,
        )

    def _set_entry_range(self, parts: list) -> TelegramCommandResult:
        if len(parts) != 2:
            return TelegramCommandResult(
                False,
                "/set_entry_range",
                "USAGE: /set_entry_range <positive_value>",
            )
        try:
            updated = self._config_manager.set_entry_range(parts[1])
        except ConfigurationError as exc:
            return TelegramCommandResult(
                False,
                "/set_entry_range",
                f"INVALID_ENTRY_RANGE: {exc}",
            )
        return TelegramCommandResult(
            True,
            "/set_entry_range",
            "Новый диапазон входа сохранён: "
            + str(updated.entry_candle_range_threshold),
        )


def _normalize_user_ids(values: Iterable[object]) -> Set[str]:
    if isinstance(values, (str, bytes)):
        raise DomainValidationError("authorized_user_ids must be an iterable of IDs")
    try:
        return {_normalize_user_id(value) for value in values}
    except TypeError as exc:
        raise DomainValidationError("authorized_user_ids must be iterable") from exc


def _normalize_user_id(value: object) -> str:
    if isinstance(value, bool):
        raise DomainValidationError("Telegram user ID cannot be boolean")
    if isinstance(value, int):
        if value <= 0:
            raise DomainValidationError("Telegram user ID must be positive")
        return str(value)
    if isinstance(value, str) and value.strip() and value.strip().isdigit():
        normalized = value.strip().lstrip("0") or "0"
        if normalized == "0":
            raise DomainValidationError("Telegram user ID must be positive")
        return normalized
    raise DomainValidationError("Telegram user ID must be a positive integer")


def _decimal(value: str) -> Decimal:
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise DomainValidationError("value must be a finite Decimal")
    return parsed
