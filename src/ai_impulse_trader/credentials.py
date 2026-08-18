"""Load local runtime credentials without placing secrets in source control."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


DEFAULT_CREDENTIALS_PATH = Path.home() / ".traider" / "secrets.json"


class CredentialsError(ValueError):
    """Raised when the local credentials file is missing or invalid."""


@dataclass(frozen=True)
class RuntimeCredentials:
    capital_api_key: str
    capital_identifier: str
    capital_api_password: str
    capital_epic: str
    telegram_bot_token: str
    telegram_chat_id: int
    telegram_allowed_user_id: int


def load_credentials(path: Optional[Path] = None) -> RuntimeCredentials:
    """Load credentials from JSON, with an optional path supplied by the environment."""
    selected = path or Path(
        os.environ.get("TRAIDER_SECRETS_FILE", str(DEFAULT_CREDENTIALS_PATH))
    )
    try:
        payload = json.loads(selected.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CredentialsError(f"Credentials file does not exist: {selected}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialsError(f"Cannot read credentials file: {selected}") from exc
    if not isinstance(payload, dict):
        raise CredentialsError("Credentials file must contain one JSON object")
    return RuntimeCredentials(
        capital_api_key=_secret(payload, "capital_api_key"),
        capital_identifier=_secret(payload, "capital_identifier"),
        capital_api_password=_secret(payload, "capital_api_password"),
        capital_epic=_secret(payload, "capital_epic"),
        telegram_bot_token=_secret(payload, "telegram_bot_token"),
        telegram_chat_id=_positive_int(payload, "telegram_chat_id"),
        telegram_allowed_user_id=_positive_int(payload, "telegram_allowed_user_id"),
    )


def _secret(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CredentialsError(f"Credential {name!r} must be a non-empty string")
    if value.startswith("REPLACE_WITH_"):
        raise CredentialsError(f"Credential {name!r} still contains a placeholder")
    return value.strip()


def _positive_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CredentialsError(f"Credential {name!r} must be a positive integer")
    return value
