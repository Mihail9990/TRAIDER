"""Pydroid-compatible ConfigManager smoke test."""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.config import ConfigManager


def main() -> None:
    """Create, update, and restore a temporary simulator configuration."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        manager = ConfigManager(path)
        initial = manager.load(create_if_missing=True)
        updated = manager.set_entry_range("5.50")
        manager.set_stop_after_current_cycle(True)
        restored = ConfigManager(path).load()

        print("AI Impulse Trader ConfigManager")
        print("MODE:", initial.mode)
        print("INITIAL_ENTRY_RANGE:", initial.entry_candle_range_threshold)
        print("UPDATED_ENTRY_RANGE:", updated.entry_candle_range_threshold)
        print("RESTORED_ENTRY_RANGE:", restored.entry_candle_range_threshold)
        print("STOP_AFTER_CURRENT_CYCLE:", restored.stop_after_current_cycle)
        print("ATOMIC_RELOAD: OK")


if __name__ == "__main__":
    main()
