"""Structured rotating logging designed for desktop Python and Pydroid 3."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

_CONTEXT_FIELDS = (
    "cycle_id",
    "scenario",
    "event",
    "request_id",
    "broker_order_id",
)
_GENERIC_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer)\s+([^\s,;]+)"),
    re.compile(r"(?i)(api[_-]?key|password|token|authorization)\s*[:=]\s*([^\s,;]+)"),
)


class SecretRedactor:
    """Redact configured secrets and common credential formats from text."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        """Create a redactor while ignoring empty or very short secret values."""
        unique = {str(secret) for secret in secrets if secret and len(str(secret)) >= 4}
        self._secrets = tuple(sorted(unique, key=len, reverse=True))

    def redact(self, value: Any) -> str:
        """Return text with explicit and common credential values hidden."""
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in _GENERIC_SECRET_PATTERNS:
            text = pattern.sub(r"\1=[REDACTED]", text)
        return text


class JsonLogFormatter(logging.Formatter):
    """Format one log record as a stable UTF-8 JSON object."""

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record, selected trading context, and exception details."""
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redactor.redact(record.getMessage()),
        }
        for field_name in _CONTEXT_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = self._redactor.redact(value)
        if record.exc_info:
            payload["exception"] = self._redactor.redact(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class LoggerManager:
    """Own application handlers and provide contextual child loggers."""

    def __init__(
        self,
        *,
        path: Path,
        level: str = "INFO",
        max_bytes: int = 1_000_000,
        backup_count: int = 3,
        secrets: Iterable[str] = (),
        namespace: str = "ai_impulse_trader",
    ) -> None:
        """Store validated logging settings without opening files yet."""
        normalized_level = level.upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid logging level")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if backup_count < 0:
            raise ValueError("backup_count must be non-negative")
        if not namespace.strip():
            raise ValueError("namespace must not be empty")

        self._path = Path(path)
        self._level = normalized_level
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._namespace = namespace
        self._redactor = SecretRedactor(secrets)
        self._handler: Optional[RotatingFileHandler] = None
        self._lock = threading.RLock()

    def configure(self) -> logging.Logger:
        """Create the log directory and install exactly one rotating handler."""
        with self._lock:
            logger = logging.getLogger(self._namespace)
            logger.setLevel(self._level)
            logger.propagate = False
            if self._handler is not None:
                return logger

            self._path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self._path,
                maxBytes=self._max_bytes,
                backupCount=self._backup_count,
                encoding="utf-8",
                delay=True,
            )
            handler.setLevel(self._level)
            handler.setFormatter(JsonLogFormatter(self._redactor))
            logger.addHandler(handler)
            self._handler = handler
            logger.info("Logging configured", extra={"event": "LOGGER_CONFIGURED"})
            return logger

    def get_logger(
        self,
        name: str,
        context: Optional[Mapping[str, Any]] = None,
    ) -> logging.LoggerAdapter:
        """Return a child logger carrying stable Cycle or request context."""
        if not name.strip():
            raise ValueError("logger name must not be empty")
        self.configure()
        child = logging.getLogger(f"{self._namespace}.{name}")
        return logging.LoggerAdapter(child, dict(context or {}))

    def close(self) -> None:
        """Flush, close, and detach the owned handler idempotently."""
        with self._lock:
            if self._handler is None:
                return
            logger = logging.getLogger(self._namespace)
            handler = self._handler
            logger.removeHandler(handler)
            handler.flush()
            handler.close()
            self._handler = None

    def log_files(self) -> Tuple[Path, ...]:
        """Return existing active and rotated files in display order."""
        self.configure()
        candidates = [self._path]
        candidates.extend(
            Path(f"{self._path}.{index}") for index in range(1, self._backup_count + 1)
        )
        return tuple(path for path in candidates if path.exists())
