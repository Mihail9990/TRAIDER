# Running AI Impulse Trader in Pydroid 3

The runtime code supports Python 3.9 or newer and currently uses only the
Python standard library. This avoids native wheels and compiler-dependent
runtime packages that are difficult to install on Android.

## Open the project

1. Copy or clone the repository to a directory accessible from Pydroid 3.
2. Open the Pydroid 3 **Terminal**, not the Pip `INSTALL` screen.
3. Change to the repository directory.

The Pip `INSTALL` screen accepts package names such as `pytest`; it does not
accept shell commands. Do not enter `python`, `python -m ...`, or
`python run_formula_demo.py` in the **Library name** field. If that screen shows
`No matching distribution found for python` or `no such option: -m`, return to
Pydroid 3 and open its Terminal from the application menu.

In Terminal you should see a command prompt where complete commands can be
entered. First verify it with:

```sh
python --version
```

## Install the local package

```sh
python -m pip install -e .
```

No third-party runtime dependencies are currently required. The empty
`requirements.txt` is retained so future Android-compatible dependencies can be
installed consistently.

## Run the smoke test

```sh
python run_formula_demo.py
```

Expected values include:

```text
LONG_SL: 4010.00
SHORT_SL: 4011.70
BASE_COVERAGE: 0.82
LONG_TP: 4012.52
SHORT_TP: 4009.18
```

Run the configuration persistence smoke test:

```sh
python run_config_demo.py
```

It must finish with `ATOMIC_RELOAD: OK` and show that the entry range and
stop-after-cycle flag survive a reload.

Run the structured logging smoke test:

```sh
python run_logging_demo.py
```

It must finish with `SECRET_REDACTION: OK` and `STRUCTURED_JSON: OK`.

Run the domain model serialization smoke test:

```sh
python run_models_demo.py
```

It must finish with `DECIMAL_ROUND_TRIP: OK` and `MODEL_VALIDATION: OK`.

Run the SQLite persistence smoke test:

```sh
python run_state_demo.py
```

It must finish with `SQLITE_TRANSACTION: OK` and `IDEMPOTENCY: OK`.

Run the in-memory broker smoke test:

```sh
python run_broker_simulator_demo.py
```

It must finish with `BROKER_IDEMPOTENCY: OK` and `BROKER_SIMULATION: OK`.

Run the minute-candle Entry Filter smoke test:

```sh
python run_entry_filter_demo.py
```

It must finish with `STRICT_THRESHOLD: OK` and `ENTRY_FILTER: OK`.

Run the synchronous Event Bus smoke test:

```sh
python run_event_bus_demo.py
```

It must finish with `PRIORITY_DELIVERY: OK` and `EVENT_IDEMPOTENCY: OK`.

Run the transport-independent Telegram command smoke test:

```sh
python run_telegram_commands_demo.py
```

It must finish with `UNAUTHORIZED_REJECTED: True` and `TELEGRAM_COMMANDS: OK`.

Run the broker-neutral Order Manager smoke test:

```sh
python run_order_manager_demo.py
```

It must finish with `BROKER_NEUTRAL_SEQUENCE: OK` and `ORDER_IDEMPOTENCY: OK`.

Run the complete deterministic strategy simulation:

```sh
python run_full_simulation.py
```

It must finish with `FINAL_STATE: MANUAL_MODE` and `SCENARIO_1_TO_9: OK`.

Scenario 9 authorized Telegram commands:

```text
/set_long_sl <price> [position_id]
/set_long_tp <price> [position_id]
/set_short_sl <price> [position_id]
/set_short_tp <price> [position_id]
/remove_long_sl [position_id]
/remove_long_tp [position_id]
/remove_short_sl [position_id]
/remove_short_tp [position_id]
/set_long_trigger <price> <size> <source_position_id>
/set_short_trigger <price> <size> <source_position_id>
/cancel_trigger <trigger_id>
/close_long [position_id]
/close_short [position_id]
```

`position_id` may be omitted only when exactly one open position exists for that
side. No extra confirmation command is required; success is reported only after
the broker confirms the action. Partial fills produce an immediate warning with
requested size, filled size, execution price, and position ID.

## Runtime and simulation separation

Production business logic lives directly under `ai_impulse_trader`. The
deterministic broker is isolated under `ai_impulse_trader.simulation` and is
imported only by tests and local smoke scripts. A plain
`import ai_impulse_trader` does not load or expose `BrokerSimulator`.

The Capital.com DEMO adapter implements the real `BrokerGateway` without
importing the simulation namespace. The read-only entry point below validates
credentials, account mode, and a quote before any trading runtime is assembled.

## Capital.com DEMO read-only preflight

The real adapter is pinned to Capital.com's DEMO REST host. It uses only the
Python standard library, never stores credentials in `config.json`, never
retries a trading write, and waits for `GET /confirms/{dealReference}` before a
trade is considered confirmed.

Create the private credentials directory and copy the versioned template:

```sh
mkdir -p "$HOME/.traider"
cp secrets.example.json "$HOME/.traider/secrets.json"
chmod 600 "$HOME/.traider/secrets.json"
```

Edit only `$HOME/.traider/secrets.json` in Pydroid and replace every
placeholder. The file stores Capital.com and Telegram credentials outside the
replaceable project checkout. Never commit it or include it in screenshots.
Then run:

```sh
python run_capital_demo_preflight.py
```

Set `TRAIDER_SECRETS_FILE` only when a different local path is needed. The
default is `$HOME/.traider/secrets.json`.

This preflight performs only authentication, `hedgingMode` validation, and a
market-details quote read. It deliberately sends no position or working-order
writes. Expected markers are:

```text
CAPITAL_DEMO_AUTH: OK
HEDGING_MODE: OK
QUOTE: <bid> / <ask>
NO_TRADE_WRITES_SENT: OK
```

Confirmed public-contract limitations remain fail-closed:

- individual removal of only SL or only TP raises `BrokerNotSupportedError`;
- partial position close is not implemented because the documented DELETE
  contract has no partial-size body;
- a transport failure during POST/PUT/DELETE becomes
  `BrokerAmbiguousOutcomeError` and must enter reconciliation rather than
  replaying the write;
- bid, ask, or midpoint selection for the Entry Filter remains an explicit
  strategy decision rather than a hidden broker-adapter assumption.

Wire immediate Telegram partial-fill alerts without coupling the adapter to
Telegram itself:

```python
notifications = NotificationManager(telegram_api)
broker = CapitalDemoBroker(
    api_key=api_key,
    identifier=identifier,
    password=api_password,
    epic=epic,
    partial_fill_notifier=notifications.send_partial_fill,
)
```

## Run unit tests (optional)

Install development-only dependencies:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

If installing from the Pip graphical screen instead, enter only `pytest` in the
**Library name** field. Running the tests still must be done in Terminal with
`python -m pytest -q`.

## Troubleshooting Pydroid 3 commands

### `No matching distribution found for python`

The command was entered in the Pip package installer, which tried to download a
package named `python`. Python is already included in Pydroid 3. Open Terminal
and run `python --version` there.

### `no such option: -m`

The Pip screen interpreted `-m` as a pip option. Open Terminal and run the full
command there, for example `python -m pytest -q`.

## Android runtime rules

- Keep Pydroid 3 running while the bot is active.
- Disable Android battery optimization for Pydroid 3 before long-running demo
  or real-time sessions.
- Keep API tokens and Telegram tokens outside source-controlled files.
- Test with the simulator and Capital.com demo environment before real trading.
- The FormulaEngine uses `Decimal`; do not replace prices with `float`.
