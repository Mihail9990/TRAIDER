"""Standard-library Telegram Bot API transport for Pydroid and desktop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .exceptions import DomainValidationError
from .telegram_commands import TelegramCommandService


@dataclass(frozen=True)
class TelegramUpdate:
    """Normalized text message received from Telegram."""

    update_id: int
    user_id: int
    chat_id: int
    text: str


class TelegramTransportError(OSError):
    """Raised when Telegram cannot confirm a Bot API request."""


class TelegramBotApi:
    """Minimal HTTPS Bot API client with no third-party dependencies."""

    def __init__(
        self,
        *,
        token: str,
        default_chat_id: int,
        timeout_seconds: int = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        _require_secret(token)
        _require_positive_int("default_chat_id", default_chat_id)
        _require_positive_int("timeout_seconds", timeout_seconds)
        self._base_url = "https://api.telegram.org/bot" + token
        self._default_chat_id = default_chat_id
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def send_message(self, text: str) -> None:
        """Send one plain-text message to the configured operator chat."""
        if not isinstance(text, str) or not text:
            raise DomainValidationError("Telegram message must be non-empty")
        self._request(
            "sendMessage",
            {"chat_id": self._default_chat_id, "text": text},
        )

    def get_updates(
        self, *, offset: Optional[int] = None, poll_timeout: int = 20
    ) -> Tuple[TelegramUpdate, ...]:
        """Long-poll new text updates and normalize only usable messages."""
        if offset is not None and (
            isinstance(offset, bool) or not isinstance(offset, int)
        ):
            raise DomainValidationError("offset must be an integer or None")
        if (
            isinstance(poll_timeout, bool)
            or not isinstance(poll_timeout, int)
            or poll_timeout < 0
        ):
            raise DomainValidationError("poll_timeout must be a non-negative integer")
        payload: Dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload)
        updates = []
        for raw in result:
            message = raw.get("message") if isinstance(raw, dict) else None
            sender = message.get("from") if isinstance(message, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            text = message.get("text") if isinstance(message, dict) else None
            if (
                not isinstance(sender, dict)
                or not isinstance(chat, dict)
                or not isinstance(text, str)
            ):
                continue
            update_id, user_id, chat_id = (
                raw.get("update_id"),
                sender.get("id"),
                chat.get("id"),
            )
            if all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (update_id, user_id, chat_id)
            ):
                updates.append(TelegramUpdate(update_id, user_id, chat_id, text))
        return tuple(updates)

    def _request(self, method: str, payload: Dict[str, Any]) -> Any:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self._base_url + "/" + method,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self._timeout_seconds)
            raw = response.read()
            decoded = json.loads(raw.decode("utf-8"))
        except (
            HTTPError,
            URLError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise TelegramTransportError("Telegram Bot API request failed") from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            description = (
                decoded.get("description", "unknown error")
                if isinstance(decoded, dict)
                else "invalid response"
            )
            raise TelegramTransportError(
                "Telegram rejected request: " + str(description)
            )
        return decoded.get("result")


class TelegramPollingAdapter:
    """Pass authorized updates to TelegramCommandService exactly once by offset."""

    def __init__(
        self, *, api: TelegramBotApi, commands: TelegramCommandService
    ) -> None:
        if not isinstance(api, TelegramBotApi):
            raise DomainValidationError("api must be TelegramBotApi")
        if not isinstance(commands, TelegramCommandService):
            raise DomainValidationError("commands must be TelegramCommandService")
        self._api = api
        self._commands = commands
        self._next_offset: Optional[int] = None

    def poll_once(self, *, poll_timeout: int = 20) -> int:
        """Process one update batch, answer each message, and advance offset."""
        updates = self._api.get_updates(
            offset=self._next_offset, poll_timeout=poll_timeout
        )
        for update in updates:
            result = self._commands.handle(
                user_id=update.user_id,
                text=update.text,
                command_id="telegram-" + str(update.update_id),
            )
            self._api.send_message(result.message)
            self._next_offset = update.update_id + 1
        return len(updates)


def _require_secret(token: object) -> None:
    if not isinstance(token, str) or ":" not in token or len(token) < 10:
        raise DomainValidationError("Telegram token format is invalid")


def _require_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{name} must be a positive integer")
