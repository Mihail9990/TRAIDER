"""Read-only Capital.com DEMO connectivity and hedging preflight for Pydroid."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_impulse_trader import CapitalDemoBroker  # noqa: E402


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("Missing environment variable: " + name)
    return value


def main() -> None:
    broker = CapitalDemoBroker(
        api_key=required("CAPITAL_API_KEY"),
        identifier=required("CAPITAL_IDENTIFIER"),
        password=required("CAPITAL_API_PASSWORD"),
        epic=required("CAPITAL_EPIC"),
    )
    broker.authenticate()
    broker.preflight_hedging()
    bid, ask = broker.quote()
    broker.logout()
    print("CAPITAL_DEMO_AUTH: OK")
    print("HEDGING_MODE: OK")
    print(f"QUOTE: {bid} / {ask}")
    print("NO_TRADE_WRITES_SENT: OK")


if __name__ == "__main__":
    main()
