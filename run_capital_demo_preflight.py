"""Read-only Capital.com DEMO connectivity and hedging preflight for Pydroid."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_impulse_trader import CapitalDemoBroker, CredentialsError, load_credentials  # noqa: E402


def main() -> None:
    try:
        credentials = load_credentials()
    except CredentialsError as error:
        raise SystemExit(str(error)) from error
    broker = CapitalDemoBroker(
        api_key=credentials.capital_api_key,
        identifier=credentials.capital_identifier,
        password=credentials.capital_api_password,
        epic=credentials.capital_epic,
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
