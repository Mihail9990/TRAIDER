"""Unit tests for structured, rotating, secret-safe logging."""

import json
from pathlib import Path

import pytest

from ai_impulse_trader.logging_manager import LoggerManager, SecretRedactor


def _read_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_structured_log_contains_context(tmp_path: Path) -> None:
    path = tmp_path / "trader.log"
    manager = LoggerManager(path=path, namespace="test.context")
    logger = manager.get_logger(
        "cycle",
        {"cycle_id": "cycle-7", "scenario": 3, "event": "REENTRY_CONFIRMED"},
    )

    logger.info("Position confirmed")
    manager.close()
    records = _read_records(path)

    assert records[-1]["level"] == "INFO"
    assert records[-1]["logger"] == "test.context.cycle"
    assert records[-1]["message"] == "Position confirmed"
    assert records[-1]["cycle_id"] == "cycle-7"
    assert records[-1]["scenario"] == "3"
    assert records[-1]["event"] == "REENTRY_CONFIRMED"
    assert records[-1]["timestamp"].endswith("+00:00")


def test_explicit_and_generic_secrets_are_redacted(tmp_path: Path) -> None:
    path = tmp_path / "trader.log"
    manager = LoggerManager(
        path=path,
        secrets=["telegram-secret-123", "capital-secret-456"],
        namespace="test.secrets",
    )
    logger = manager.get_logger("api")

    logger.error(
        "token=telegram-secret-123 password=capital-secret-456 "
        "Authorization: Bearer another-secret"
    )
    manager.close()
    content = path.read_text(encoding="utf-8")

    assert "telegram-secret-123" not in content
    assert "capital-secret-456" not in content
    assert "another-secret" not in content
    assert "[REDACTED]" in content


def test_exception_is_json_encoded_and_redacted(tmp_path: Path) -> None:
    path = tmp_path / "trader.log"
    manager = LoggerManager(
        path=path,
        secrets=["hidden-value"],
        namespace="test.exception",
    )
    logger = manager.get_logger("worker")

    try:
        raise RuntimeError("failure hidden-value")
    except RuntimeError:
        logger.exception("Worker failed")
    manager.close()
    record = _read_records(path)[-1]

    assert "RuntimeError" in record["exception"]
    assert "hidden-value" not in record["exception"]
    assert "[REDACTED]" in record["exception"]


def test_rotation_limits_active_file_and_keeps_backups(tmp_path: Path) -> None:
    path = tmp_path / "trader.log"
    manager = LoggerManager(
        path=path,
        max_bytes=240,
        backup_count=2,
        namespace="test.rotation",
    )
    logger = manager.get_logger("writer")

    for index in range(40):
        logger.info("rotation record %s %s", index, "x" * 40)

    files = manager.log_files()
    manager.close()

    assert path in files
    assert path.with_name("trader.log.1") in files
    assert len(files) <= 3


def test_configure_and_close_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "trader.log"
    manager = LoggerManager(path=path, namespace="test.idempotent")

    first = manager.configure()
    second = manager.configure()
    assert first is second
    assert len(first.handlers) == 1

    manager.close()
    manager.close()
    assert len(first.handlers) == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"level": "NOT_A_LEVEL"},
        {"max_bytes": 0},
        {"backup_count": -1},
        {"namespace": ""},
    ],
)
def test_invalid_logger_configuration_is_rejected(tmp_path: Path, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        LoggerManager(path=tmp_path / "trader.log", **kwargs)


def test_short_values_are_not_treated_as_explicit_secrets() -> None:
    redactor = SecretRedactor(["1", "abc", "long-secret"])

    assert redactor.redact("value=1 abc long-secret") == "value=1 abc [REDACTED]"
