"""Typed, atomically persisted configuration for Pydroid and desktop Python."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

from .exceptions import ConfigurationError, ConfigurationStorageError

_ALLOWED_MODES = frozenset({"SIMULATOR", "DEMO", "REAL"})
_CONFIG_FIELDS = frozenset(
    {
        "mode",
        "symbol",
        "position_size",
        "stop_distance",
        "target_profit",
        "max_scenario",
        "entry_candle_timeframe",
        "entry_candle_range_threshold",
        "stop_after_current_cycle",
        "log_level",
        "log_path",
        "log_max_bytes",
        "log_backup_count",
        "database_path",
        "telegram_enabled",
    }
)
_DECIMAL_FIELDS = (
    "position_size",
    "stop_distance",
    "target_profit",
    "entry_candle_range_threshold",
)


@dataclass(frozen=True)
class AppConfig:
    """Validated application settings safe to share as an immutable snapshot."""

    mode: str = "SIMULATOR"
    symbol: str = "GOLD"
    position_size: Decimal = Decimal("1")
    stop_distance: Decimal = Decimal("1")
    target_profit: Decimal = Decimal("0.30")
    max_scenario: int = 9
    entry_candle_timeframe: str = "1m"
    entry_candle_range_threshold: Decimal = Decimal("4")
    stop_after_current_cycle: bool = False
    log_level: str = "INFO"
    log_path: str = "logs/trader.log"
    log_max_bytes: int = 1_000_000
    log_backup_count: int = 3
    database_path: str = "data/trader.sqlite3"
    telegram_enabled: bool = False

    def __post_init__(self) -> None:
        """Validate values regardless of whether construction came from JSON."""
        if not isinstance(self.mode, str) or self.mode not in _ALLOWED_MODES:
            raise ConfigurationError("mode must be one of SIMULATOR, DEMO, or REAL")
        _require_non_empty_string("symbol", self.symbol)
        for field_name in _DECIMAL_FIELDS:
            _require_positive_decimal(field_name, getattr(self, field_name))
        if isinstance(self.max_scenario, bool) or self.max_scenario != 9:
            raise ConfigurationError("max_scenario must be exactly 9")
        if self.entry_candle_timeframe != "1m":
            raise ConfigurationError("entry_candle_timeframe must be '1m'")
        if not isinstance(self.stop_after_current_cycle, bool):
            raise ConfigurationError("stop_after_current_cycle must be boolean")
        if not isinstance(self.telegram_enabled, bool):
            raise ConfigurationError("telegram_enabled must be boolean")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("log_level is invalid")
        _require_non_empty_string("log_path", self.log_path)
        if (
            isinstance(self.log_max_bytes, bool)
            or not isinstance(self.log_max_bytes, int)
            or self.log_max_bytes <= 0
        ):
            raise ConfigurationError("log_max_bytes must be a positive integer")
        if (
            isinstance(self.log_backup_count, bool)
            or not isinstance(self.log_backup_count, int)
            or self.log_backup_count < 0
        ):
            raise ConfigurationError("log_backup_count must be a non-negative integer")
        _require_non_empty_string("database_path", self.database_path)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AppConfig":
        """Build and validate configuration loaded from a JSON-like mapping."""
        if not isinstance(values, Mapping):
            raise ConfigurationError("configuration root must be an object")
        unknown = set(values) - _CONFIG_FIELDS
        if unknown:
            raise ConfigurationError(
                "unknown configuration fields: " + ", ".join(sorted(unknown))
            )

        parsed: Dict[str, Any] = dict(values)
        for field_name in _DECIMAL_FIELDS:
            if field_name in parsed:
                parsed[field_name] = _parse_decimal(field_name, parsed[field_name])
        return cls(**parsed)

    def to_mapping(self) -> Dict[str, Any]:
        """Return a JSON-serializable copy with exact Decimal strings."""
        values = asdict(self)
        for field_name in _DECIMAL_FIELDS:
            values[field_name] = str(values[field_name])
        return values


class ConfigManager:
    """Load, validate, snapshot, and atomically persist application settings."""

    def __init__(
        self,
        path: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Create a manager for one JSON configuration path."""
        self._path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._current: Optional[AppConfig] = None
        self._logger.debug("ConfigManager initialized", extra={"path": str(path)})

    def load(self, *, create_if_missing: bool = False) -> AppConfig:
        """Load and validate settings, optionally creating safe simulator defaults."""
        self._logger.info("Loading configuration", extra={"path": str(self._path)})
        with self._lock:
            if not self._path.exists():
                if not create_if_missing:
                    raise ConfigurationStorageError(
                        f"configuration file does not exist: {self._path}"
                    )
                default = AppConfig()
                self._write_atomic(default)
                self._current = default
                return default

            try:
                raw = self._path.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ConfigurationStorageError(
                    f"unable to read configuration: {self._path}"
                ) from exc

            config = AppConfig.from_mapping(payload)
            self._current = config
            return config

    def save(self, config: AppConfig) -> AppConfig:
        """Validate and atomically save the complete configuration."""
        self._logger.info("Saving configuration", extra={"path": str(self._path)})
        if not isinstance(config, AppConfig):
            raise ConfigurationError("config must be an AppConfig")
        with self._lock:
            self._write_atomic(config)
            self._current = config
            return config

    def set_entry_range(self, value: Any) -> AppConfig:
        """Persist a new positive minute-candle range threshold."""
        self._logger.info("Updating entry candle range threshold")
        threshold = _parse_decimal("entry_candle_range_threshold", value)
        _require_positive_decimal("entry_candle_range_threshold", threshold)
        with self._lock:
            current = self._require_loaded()
            updated = replace(current, entry_candle_range_threshold=threshold)
            self._write_atomic(updated)
            self._current = updated
            return updated

    def set_stop_after_current_cycle(self, enabled: bool) -> AppConfig:
        """Persist whether a new Cycle must remain blocked after completion."""
        self._logger.info(
            "Updating stop-after-current-cycle flag", extra={"enabled": enabled}
        )
        if not isinstance(enabled, bool):
            raise ConfigurationError("enabled must be boolean")
        with self._lock:
            current = self._require_loaded()
            updated = replace(current, stop_after_current_cycle=enabled)
            self._write_atomic(updated)
            self._current = updated
            return updated

    def cycle_snapshot(self) -> Mapping[str, Any]:
        """Return an immutable copy of the settings for a new Cycle."""
        self._logger.info("Creating immutable Cycle configuration snapshot")
        with self._lock:
            current = self._require_loaded()
            return MappingProxyType(current.to_mapping())

    def _require_loaded(self) -> AppConfig:
        if self._current is None:
            raise ConfigurationError("configuration must be loaded first")
        return self._current

    def _write_atomic(self, config: AppConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                config.to_mapping(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        temporary_path: Optional[Path] = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=str(self._path.parent),
                text=True,
            )
            temporary_path = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary_path), str(self._path))
        except OSError as exc:
            raise ConfigurationStorageError(
                f"unable to save configuration: {self._path}"
            ) from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _parse_decimal(name: str, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a decimal number")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ConfigurationError(f"{name} must be finite")
    return parsed


def _require_positive_decimal(name: str, value: Any) -> None:
    if not isinstance(value, Decimal):
        raise ConfigurationError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ConfigurationError(f"{name} must be finite")
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")


def _require_non_empty_string(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string")
