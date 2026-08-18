"""Syntax compatibility checks for the minimum supported Python version."""

import ast
import subprocess
import sys
from pathlib import Path


def test_runtime_source_parses_as_python_39() -> None:
    runtime_files = sorted(Path("src").rglob("*.py")) + sorted(
        Path(".").glob("run_*.py")
    )

    for path in runtime_files:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=(3, 9),
        )


def test_formula_demo_runs_from_checkout_without_installation() -> None:
    result = subprocess.run(
        [sys.executable, "run_formula_demo.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "BASE_COVERAGE: 0.82" in result.stdout
    assert "LONG_TP: 4012.52" in result.stdout
    assert "SHORT_TP: 4009.18" in result.stdout


def test_config_demo_runs_from_checkout_without_installation() -> None:
    result = subprocess.run(
        [sys.executable, "run_config_demo.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "UPDATED_ENTRY_RANGE: 5.50" in result.stdout
    assert "RESTORED_ENTRY_RANGE: 5.50" in result.stdout
    assert "STOP_AFTER_CURRENT_CYCLE: True" in result.stdout
    assert "ATOMIC_RELOAD: OK" in result.stdout
