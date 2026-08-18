"""Integration tests for transactional SQLite state persistence."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ai_impulse_trader.enums import CycleState
from ai_impulse_trader.exceptions import StateCorruptionError, StateStorageError
from ai_impulse_trader.models import ApplicationState, Cycle
from ai_impulse_trader.state_manager import StateManager

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _cycle(scenario: int = 1) -> Cycle:
    return Cycle(
        cycle_id="cycle-1",
        symbol="GOLD",
        position_size=Decimal("1"),
        state=CycleState.RUNNING,
        current_scenario=scenario,
        initial_long_entry=Decimal("4011"),
        initial_short_entry=Decimal("4010.7"),
        base_coverage=Decimal("0.82"),
        saved_long_tp=Decimal("4012.52"),
        saved_short_tp=Decimal("4009.18"),
        created_at=NOW,
        last_confirmed_step=f"SCENARIO_{scenario}",
    )


@pytest.fixture
def manager(tmp_path: Path) -> StateManager:
    instance = StateManager(tmp_path / "data" / "trader.sqlite3")
    instance.initialize()
    return instance


def test_initialize_is_idempotent_and_creates_schema(tmp_path: Path) -> None:
    path = tmp_path / "trader.sqlite3"
    manager = StateManager(path)

    manager.initialize()
    manager.initialize()

    assert path.exists()
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "1"


def test_operations_require_initialization(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "trader.sqlite3")

    with pytest.raises(StateStorageError, match="initialized first"):
        manager.load_cycle("cycle-1")


def test_application_state_round_trip(manager: StateManager) -> None:
    state = ApplicationState(
        stop_after_current_cycle=True,
        active_cycle_id="cycle-1",
        recovery_required=True,
        updated_at=NOW,
    )

    assert manager.load_application_state() is None
    manager.save_application_state(state)

    assert manager.load_application_state() == state


def test_cycle_save_load_list_and_snapshots(manager: StateManager) -> None:
    original = _cycle(1)
    updated = replace(original, current_scenario=2, last_confirmed_step="SCENARIO_2")

    assert manager.save_cycle(original, reason="CREATED") is True
    assert manager.save_cycle(updated, reason="SCENARIO_CHANGED") is True

    assert manager.load_cycle("cycle-1") == updated
    assert manager.load_cycle("missing") is None
    assert manager.list_cycles() == (updated,)
    snapshots = manager.cycle_snapshots("cycle-1")
    assert [snapshot["reason"] for snapshot in snapshots] == [
        "CREATED",
        "SCENARIO_CHANGED",
    ]
    assert snapshots[0]["cycle"]["current_scenario"] == 1
    assert snapshots[1]["cycle"]["current_scenario"] == 2
    with pytest.raises(TypeError):
        snapshots[0]["reason"] = "CHANGED"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshots[0]["cycle"]["symbol"] = "OTHER"  # type: ignore[index]


def test_duplicate_event_does_not_apply_cycle_update(manager: StateManager) -> None:
    original = _cycle(1)
    updated = replace(original, current_scenario=2, last_confirmed_step="SCENARIO_2")

    assert (
        manager.save_cycle(
            original,
            reason="BROKER_EVENT",
            event_id="event-1",
            event_type="STOP_LOSS_CONFIRMED",
            event_payload={"price": Decimal("4011.70")},
        )
        is True
    )
    assert (
        manager.save_cycle(
            updated,
            reason="DUPLICATE_EVENT",
            event_id="event-1",
            event_type="STOP_LOSS_CONFIRMED",
        )
        is False
    )

    assert manager.load_cycle("cycle-1") == original
    assert len(manager.cycle_snapshots("cycle-1")) == 1


def test_record_event_is_idempotent(manager: StateManager) -> None:
    assert (
        manager.record_event(
            event_id="event-1",
            event_type="TRIGGER_EXECUTED",
            cycle_id="cycle-1",
            payload={"side": "SHORT"},
        )
        is True
    )
    assert (
        manager.record_event(
            event_id="event-1",
            event_type="TRIGGER_EXECUTED",
            cycle_id="cycle-1",
            payload={"side": "SHORT"},
        )
        is False
    )
    assert manager.event_exists("event-1") is True
    assert manager.event_exists("missing") is False


def test_cycle_and_snapshot_are_rolled_back_together(
    manager: StateManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _cycle(1)
    updated = replace(original, current_scenario=2, last_confirmed_step="SCENARIO_2")
    manager.save_cycle(original, reason="CREATED")

    def fail_snapshot(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("simulated snapshot failure")

    monkeypatch.setattr(manager, "_insert_snapshot", fail_snapshot)

    with pytest.raises(StateStorageError, match="unable to save Cycle"):
        manager.save_cycle(updated, reason="SCENARIO_CHANGED")

    assert manager.load_cycle("cycle-1") == original
    assert len(manager.cycle_snapshots("cycle-1")) == 1


def test_corrupted_cycle_payload_is_rejected(manager: StateManager) -> None:
    manager.save_cycle(_cycle(), reason="CREATED")
    with sqlite3.connect(manager._path) as connection:  # type: ignore[attr-defined]
        connection.execute(
            "UPDATE cycles SET payload = ? WHERE cycle_id = ?",
            (json.dumps({"broken": True}), "cycle-1"),
        )

    with pytest.raises(StateCorruptionError, match="invalid persisted Cycle"):
        manager.load_cycle("cycle-1")


def test_invalid_arguments_are_rejected(manager: StateManager) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        manager.save_cycle(_cycle(), reason="EVENT", event_id="event-only")
    with pytest.raises(ValueError, match="reason"):
        manager.save_cycle(_cycle(), reason="")
    with pytest.raises(TypeError, match="mapping"):
        manager.record_event(
            event_id="event-2",
            event_type="TEST",
            payload=[],  # type: ignore[arg-type]
        )


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "trader.sqlite3"
    manager = StateManager(path)
    manager.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE metadata SET value = '99' WHERE key = 'schema_version'"
        )

    with pytest.raises(StateStorageError, match="unsupported"):
        StateManager(path).initialize()
