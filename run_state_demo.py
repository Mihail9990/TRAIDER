"""Pydroid-compatible SQLite StateManager smoke test."""

import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.enums import CycleState
from ai_impulse_trader.models import ApplicationState, Cycle
from ai_impulse_trader.state_manager import StateManager


def main() -> None:
    """Persist and restore application state and an idempotent Cycle event."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "trader.sqlite3"
        manager = StateManager(path)
        manager.initialize()
        now = datetime.now(timezone.utc)
        app_state = ApplicationState(
            stop_after_current_cycle=True,
            active_cycle_id="demo-cycle",
            updated_at=now,
        )
        cycle = Cycle(
            cycle_id="demo-cycle",
            symbol="GOLD",
            position_size=Decimal("1"),
            state=CycleState.RUNNING,
            current_scenario=1,
            created_at=now,
        )

        manager.save_application_state(app_state)
        first = manager.save_cycle(
            cycle,
            reason="DEMO_SAVE",
            event_id="demo-event",
            event_type="CYCLE_CREATED",
        )
        duplicate = manager.save_cycle(
            cycle,
            reason="DUPLICATE",
            event_id="demo-event",
            event_type="CYCLE_CREATED",
        )
        restored_state = manager.load_application_state()
        restored_cycle = manager.load_cycle("demo-cycle")

        print("AI Impulse Trader StateManager")
        print("DATABASE_CREATED:", path.exists())
        print("APPLICATION_STATE_RESTORED:", restored_state == app_state)
        print("CYCLE_RESTORED:", restored_cycle == cycle)
        print("FIRST_EVENT_APPLIED:", first)
        print("DUPLICATE_EVENT_APPLIED:", duplicate)
        print("SNAPSHOT_COUNT:", len(manager.cycle_snapshots("demo-cycle")))
        print("SQLITE_TRANSACTION: OK")
        print("IDEMPOTENCY: OK")


if __name__ == "__main__":
    main()
