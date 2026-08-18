import json
from pathlib import Path

import pytest

from ai_impulse_trader.credentials import CredentialsError, load_credentials


def _payload() -> dict:
    return {
        "capital_api_key": "api-key",
        "capital_identifier": "login@example.com",
        "capital_api_password": "password",
        "capital_epic": "GOLD-EPIC",
        "telegram_bot_token": "123:token",
        "telegram_chat_id": 123,
        "telegram_allowed_user_id": 456,
    }


def test_load_credentials_reads_all_runtime_secrets(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    credentials = load_credentials(path)

    assert credentials.capital_api_key == "api-key"
    assert credentials.telegram_chat_id == 123
    assert credentials.telegram_allowed_user_id == 456


def test_load_credentials_rejects_placeholders(tmp_path: Path) -> None:
    payload = _payload()
    payload["capital_api_key"] = "REPLACE_WITH_DEMO_API_KEY"
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CredentialsError, match="placeholder"):
        load_credentials(path)


def test_load_credentials_uses_environment_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setenv("TRAIDER_SECRETS_FILE", str(path))

    assert load_credentials().capital_epic == "GOLD-EPIC"
