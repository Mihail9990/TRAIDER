"""Unit tests for typed and atomically persisted configuration."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ai_impulse_trader.config import AppConfig, ConfigManager
from ai_impulse_trader.exceptions import ConfigurationError, ConfigurationStorageError


def test_create_defaults_and_reload_exact_decimals(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)

    created = manager.load(create_if_missing=True)
    loaded = ConfigManager(path).load()

    assert created == AppConfig()
    assert loaded == created
    assert loaded.entry_candle_range_threshold == Decimal("4")
    assert json.loads(path.read_text(encoding="utf-8"))["target_profit"] == "0.30"


def test_missing_config_is_rejected_by_default(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationStorageError, match="does not exist"):
        ConfigManager(tmp_path / "missing.json").load()


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"unknown": 1}',
        '{"position_size": "0"}',
        '{"entry_candle_range_threshold": "NaN"}',
        '{"max_scenario": 8}',
        '{"entry_candle_timeframe": "5m"}',
        '{"symbol": 10}',
        '{"log_path": 10}',
        '{"log_max_bytes": "large"}',
        '{"log_backup_count": -1}',
        '{"database_path": null}',
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(payload, encoding="utf-8")

    error = (ConfigurationStorageError, ConfigurationError)
    with pytest.raises(error):
        ConfigManager(path).load()


def test_set_entry_range_persists_for_restart(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.load(create_if_missing=True)

    updated = manager.set_entry_range("5.50")
    restored = ConfigManager(path).load()

    assert updated.entry_candle_range_threshold == Decimal("5.50")
    assert restored.entry_candle_range_threshold == Decimal("5.50")


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", True, "text"])
def test_set_entry_range_rejects_invalid_values(tmp_path: Path, value: object) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    original = manager.load(create_if_missing=True)

    with pytest.raises(ConfigurationError):
        manager.set_entry_range(value)

    assert ConfigManager(path).load() == original


def test_stop_after_current_cycle_persists(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.load(create_if_missing=True)

    manager.set_stop_after_current_cycle(True)
    assert ConfigManager(path).load().stop_after_current_cycle is True

    manager.set_stop_after_current_cycle(False)
    assert ConfigManager(path).load().stop_after_current_cycle is False


def test_runtime_update_requires_initial_load(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")

    with pytest.raises(ConfigurationError, match="must be loaded first"):
        manager.set_entry_range("5")


def test_cycle_snapshot_is_immutable_and_detached(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.load(create_if_missing=True)
    snapshot = manager.cycle_snapshot()

    with pytest.raises(TypeError):
        snapshot["symbol"] = "OTHER"  # type: ignore[index]

    manager.set_entry_range("6")
    assert snapshot["entry_candle_range_threshold"] == "4"


def test_failed_atomic_replace_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    original = manager.load(create_if_missing=True)
    original_text = path.read_text(encoding="utf-8")

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("ai_impulse_trader.config.os.replace", fail_replace)

    with pytest.raises(ConfigurationStorageError, match="unable to save"):
        manager.set_entry_range("8")

    assert path.read_text(encoding="utf-8") == original_text
    assert manager.cycle_snapshot() == original.to_mapping()
