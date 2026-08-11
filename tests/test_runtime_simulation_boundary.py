"""Ensure production package imports never pull in simulation adapters."""

import subprocess
import sys
import os


def test_root_package_does_not_import_simulation_namespace() -> None:
    script = """
import sys
import ai_impulse_trader
assert 'ai_impulse_trader.simulation' not in sys.modules
assert 'ai_impulse_trader.simulation.broker' not in sys.modules
assert not hasattr(ai_impulse_trader, 'BrokerSimulator')
print('RUNTIME_WITHOUT_SIMULATOR: OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert "RUNTIME_WITHOUT_SIMULATOR: OK" in result.stdout


def test_simulator_remains_explicitly_available_for_tests() -> None:
    from ai_impulse_trader.simulation import BrokerSimulator

    assert BrokerSimulator.__module__ == "ai_impulse_trader.simulation.broker"
