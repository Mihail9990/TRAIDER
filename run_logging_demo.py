"""Pydroid-compatible structured logging smoke test."""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ai_impulse_trader.logging_manager import LoggerManager


def main() -> None:
    """Write one contextual record and verify that its secret is redacted."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "trader.log"
        manager = LoggerManager(
            path=path,
            secrets=["demo-secret-token"],
            namespace="ai_impulse_demo",
        )
        logger = manager.get_logger(
            "cycle",
            {"cycle_id": "demo-cycle", "scenario": 1, "event": "DEMO"},
        )
        logger.info("Logger works; token=demo-secret-token")
        manager.close()

        record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        if "demo-secret-token" in json.dumps(record):
            raise RuntimeError("secret redaction failed")

        print("AI Impulse Trader LoggerManager")
        print("LEVEL:", record["level"])
        print("CYCLE_ID:", record["cycle_id"])
        print("SCENARIO:", record["scenario"])
        print("MESSAGE:", record["message"])
        print("SECRET_REDACTION: OK")
        print("STRUCTURED_JSON: OK")


if __name__ == "__main__":
    main()
