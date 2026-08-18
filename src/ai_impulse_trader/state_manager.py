"""Transactional SQLite persistence for trading state on Pydroid and desktop."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, List, Mapping, Optional, Tuple

from .exceptions import StateCorruptionError, StateStorageError
from .models import ApplicationState, Cycle

_SCHEMA_VERSION = 1


class StateManager:
    """Persist validated application and Cycle snapshots in SQLite transactions."""

    def __init__(
        self,
        path: Path,
        logger: Optional[logging.Logger] = None,
        timeout: float = 10.0,
    ) -> None:
        """Create a manager without opening a long-lived database connection."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._timeout = timeout
        self._lock = threading.RLock()
        self._initialized = False
        self._logger.debug("StateManager initialized", extra={"path": str(path)})

    def initialize(self) -> None:
        """Create or validate the SQLite schema idempotently."""
        self._logger.info(
            "Initializing state database", extra={"path": str(self._path)}
        )
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self._connection() as connection:
                    connection.executescript("""
                        CREATE TABLE IF NOT EXISTS metadata (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS application_state (
                            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                            payload TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS cycles (
                            cycle_id TEXT PRIMARY KEY,
                            state TEXT NOT NULL,
                            scenario INTEGER NOT NULL,
                            payload TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS cycle_snapshots (
                            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            cycle_id TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            payload TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
                        );

                        CREATE INDEX IF NOT EXISTS idx_cycle_snapshots_cycle
                            ON cycle_snapshots(cycle_id, snapshot_id);

                        CREATE TABLE IF NOT EXISTS events (
                            event_id TEXT PRIMARY KEY,
                            cycle_id TEXT,
                            event_type TEXT NOT NULL,
                            payload TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_events_cycle
                            ON events(cycle_id, created_at);
                        """)
                    row = connection.execute(
                        "SELECT value FROM metadata WHERE key = 'schema_version'"
                    ).fetchone()
                    if row is None:
                        connection.execute(
                            "INSERT INTO metadata(key, value) VALUES (?, ?)",
                            ("schema_version", str(_SCHEMA_VERSION)),
                        )
                    elif int(row["value"]) != _SCHEMA_VERSION:
                        raise StateStorageError("unsupported state schema version")
            except (sqlite3.Error, OSError, ValueError) as exc:
                if isinstance(exc, StateStorageError):
                    raise
                raise StateStorageError(
                    f"unable to initialize state database: {self._path}"
                ) from exc
            self._initialized = True

    def save_application_state(self, state: ApplicationState) -> None:
        """Atomically insert or replace the singleton application state."""
        self._logger.info("Saving application state")
        if not isinstance(state, ApplicationState):
            raise TypeError("state must be an ApplicationState")
        payload = _encode_json(state.to_dict())
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO application_state(singleton_id, payload, updated_at)
                        VALUES (1, ?, ?)
                        ON CONFLICT(singleton_id) DO UPDATE SET
                            payload = excluded.payload,
                            updated_at = excluded.updated_at
                        """,
                        (payload, state.updated_at.isoformat()),
                    )
            except sqlite3.Error as exc:
                raise StateStorageError("unable to save application state") from exc

    def load_application_state(self) -> Optional[ApplicationState]:
        """Load validated global state, returning None before its first save."""
        self._logger.info("Loading application state")
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT payload FROM application_state WHERE singleton_id = 1"
                    ).fetchone()
            except sqlite3.Error as exc:
                raise StateStorageError("unable to load application state") from exc
        if row is None:
            return None
        return _decode_model(row["payload"], ApplicationState, "application state")

    def save_cycle(
        self,
        cycle: Cycle,
        *,
        reason: str,
        event_id: Optional[str] = None,
        event_type: Optional[str] = None,
        event_payload: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        """Atomically save a Cycle snapshot and optional idempotency event."""
        self._logger.info(
            "Saving Cycle snapshot",
            extra={"cycle_id": getattr(cycle, "cycle_id", None), "event": reason},
        )
        if not isinstance(cycle, Cycle):
            raise TypeError("cycle must be a Cycle")
        _require_text("reason", reason)
        if (event_id is None) != (event_type is None):
            raise ValueError("event_id and event_type must be supplied together")
        if event_id is not None:
            _require_text("event_id", event_id)
            _require_text("event_type", event_type)

        cycle_payload = _encode_json(cycle.to_dict())
        now = _utc_text()
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    if event_id is not None:
                        inserted = self._insert_event(
                            connection,
                            event_id=event_id,
                            cycle_id=cycle.cycle_id,
                            event_type=event_type or "",
                            payload=event_payload or {},
                            created_at=now,
                        )
                        if not inserted:
                            return False
                    connection.execute(
                        """
                        INSERT INTO cycles(cycle_id, state, scenario, payload, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(cycle_id) DO UPDATE SET
                            state = excluded.state,
                            scenario = excluded.scenario,
                            payload = excluded.payload,
                            updated_at = excluded.updated_at
                        """,
                        (
                            cycle.cycle_id,
                            cycle.state.value,
                            cycle.current_scenario,
                            cycle_payload,
                            now,
                        ),
                    )
                    self._insert_snapshot(
                        connection,
                        cycle_id=cycle.cycle_id,
                        reason=reason,
                        payload=cycle_payload,
                        created_at=now,
                    )
            except sqlite3.Error as exc:
                raise StateStorageError("unable to save Cycle") from exc
        return True

    def load_cycle(self, cycle_id: str) -> Optional[Cycle]:
        """Load and validate the latest state of one Cycle."""
        self._logger.info("Loading Cycle", extra={"cycle_id": cycle_id})
        _require_text("cycle_id", cycle_id)
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT payload FROM cycles WHERE cycle_id = ?", (cycle_id,)
                    ).fetchone()
            except sqlite3.Error as exc:
                raise StateStorageError("unable to load Cycle") from exc
        if row is None:
            return None
        return _decode_model(row["payload"], Cycle, "Cycle")

    def list_cycles(self) -> Tuple[Cycle, ...]:
        """Return all latest Cycle states ordered by creation time and ID."""
        self._logger.info("Listing Cycles")
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        "SELECT payload FROM cycles ORDER BY updated_at, cycle_id"
                    ).fetchall()
            except sqlite3.Error as exc:
                raise StateStorageError("unable to list Cycles") from exc
        return tuple(_decode_model(row["payload"], Cycle, "Cycle") for row in rows)

    def cycle_snapshots(self, cycle_id: str) -> Tuple[Mapping[str, Any], ...]:
        """Return immutable snapshot metadata and decoded payloads for a Cycle."""
        self._logger.info("Listing Cycle snapshots", extra={"cycle_id": cycle_id})
        _require_text("cycle_id", cycle_id)
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        """
                        SELECT snapshot_id, reason, payload, created_at
                        FROM cycle_snapshots
                        WHERE cycle_id = ?
                        ORDER BY snapshot_id
                        """,
                        (cycle_id,),
                    ).fetchall()
            except sqlite3.Error as exc:
                raise StateStorageError("unable to list Cycle snapshots") from exc
        snapshots: List[Mapping[str, Any]] = []
        for row in rows:
            payload = _decode_json(row["payload"], "Cycle snapshot")
            snapshots.append(
                MappingProxyType(
                    {
                        "snapshot_id": row["snapshot_id"],
                        "reason": row["reason"],
                        "created_at": row["created_at"],
                        "cycle": _freeze_json(payload),
                    }
                )
            )
        return tuple(snapshots)

    def record_event(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        cycle_id: Optional[str] = None,
    ) -> bool:
        """Persist one event exactly once, returning False for a duplicate ID."""
        self._logger.info(
            "Recording event", extra={"cycle_id": cycle_id, "event": event_type}
        )
        _require_text("event_id", event_id)
        _require_text("event_type", event_type)
        if cycle_id is not None:
            _require_text("cycle_id", cycle_id)
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    return self._insert_event(
                        connection,
                        event_id=event_id,
                        cycle_id=cycle_id,
                        event_type=event_type,
                        payload=payload,
                        created_at=_utc_text(),
                    )
            except sqlite3.Error as exc:
                raise StateStorageError("unable to record event") from exc

    def event_exists(self, event_id: str) -> bool:
        """Return whether an event ID has already been persisted."""
        self._logger.info("Checking event", extra={"event": event_id})
        _require_text("event_id", event_id)
        with self._lock:
            self._require_initialized()
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
                    ).fetchone()
            except sqlite3.Error as exc:
                raise StateStorageError("unable to check event") from exc
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=self._timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise StateStorageError("state database must be initialized first")

    @staticmethod
    def _insert_snapshot(
        connection: sqlite3.Connection,
        *,
        cycle_id: str,
        reason: str,
        payload: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO cycle_snapshots(cycle_id, reason, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (cycle_id, reason, payload, created_at),
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        cycle_id: Optional[str],
        event_type: str,
        payload: Mapping[str, Any],
        created_at: str,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO events(
                event_id, cycle_id, event_type, payload, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                cycle_id,
                event_type,
                _encode_json(payload),
                created_at,
            ),
        )
        return cursor.rowcount == 1


def _decode_model(payload: str, model_type: Any, description: str) -> Any:
    values = _decode_json(payload, description)
    try:
        return model_type.from_dict(values)
    except (TypeError, ValueError) as exc:
        raise StateCorruptionError(f"invalid persisted {description}") from exc


def _decode_json(payload: str, description: str) -> Mapping[str, Any]:
    try:
        values = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StateCorruptionError(f"invalid JSON for {description}") from exc
    if not isinstance(values, dict):
        raise StateCorruptionError(f"persisted {description} must be an object")
    return values


def _encode_json(values: Mapping[str, Any]) -> str:
    if not isinstance(values, Mapping):
        raise TypeError("JSON payload must be a mapping")
    try:
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise StateStorageError("state payload is not JSON serializable") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _require_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat()
