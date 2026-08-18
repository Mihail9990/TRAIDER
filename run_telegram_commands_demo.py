"""Pydroid-compatible Telegram command service smoke test."""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.config import ConfigManager
from ai_impulse_trader.telegram_commands import TelegramCommandService


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manager = ConfigManager(Path(directory) / "config.json")
        manager.load(create_if_missing=True)
        commands = TelegramCommandService(
            config_manager=manager,
            authorized_user_ids={12345},
        )
        changed = commands.handle(
            user_id=12345,
            text="/set_entry_range 5.50",
        )
        stopped = commands.handle(user_id=12345, text="/stop_after_cycle")
        resumed = commands.handle(user_id=12345, text="/start_cycle")
        denied = commands.handle(user_id=99999, text="/set_entry_range 100")
        restored = manager.load()

        print("AI Impulse Trader TelegramCommandService")
        print("ENTRY_RANGE:", restored.entry_candle_range_threshold)
        print("RANGE_ACCEPTED:", changed.accepted)
        print("STOP_ACCEPTED:", stopped.accepted)
        print("START_REQUESTED:", resumed.request_cycle_start)
        print("UNAUTHORIZED_REJECTED:", not denied.accepted)
        print("TELEGRAM_COMMANDS: OK")


if __name__ == "__main__":
    main()
