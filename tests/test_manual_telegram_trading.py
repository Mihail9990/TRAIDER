"""Integration tests for authorized Scenario 9 Telegram trading commands."""

from dataclasses import replace
from decimal import Decimal

from ai_impulse_trader.config import AppConfig, ConfigManager
from ai_impulse_trader.enums import Side
from ai_impulse_trader.manual_trading import ManualTradingService
from ai_impulse_trader.telegram_commands import TelegramCommandService

from test_cycle_manager import runtime


class MemoryNotifications:
    def __init__(self) -> None:
        self.messages = []

    def send_message(self, text: str) -> None:
        self.messages.append(text)


def commands_in_manual_mode(tmp_path):
    manager, broker, state, costs = runtime(tmp_path)
    cycle = manager.start_scenario_one(
        cycle_id="cycle-1", config=AppConfig(), coverage=costs
    )
    state.save_cycle(replace(cycle, current_scenario=8), reason="TEST_SCENARIO_8")
    manager.enter_scenario_nine(cycle_id="cycle-1")
    config = ConfigManager(tmp_path / "commands.json")
    config.load(create_if_missing=True)
    notifications = MemoryNotifications()
    manual = ManualTradingService(
        orders=manager.orders,
        state=state,
        notifications=notifications,
    )
    commands = TelegramCommandService(
        config_manager=config,
        authorized_user_ids={123},
        manual_trading=manual,
    )
    return commands, broker, state, notifications


def test_each_long_and_short_level_can_be_set_and_removed_separately(tmp_path) -> None:
    commands, broker, _, _ = commands_in_manual_mode(tmp_path)

    assert commands.handle(user_id=123, text="/set_long_sl 4000").accepted
    assert commands.handle(user_id=123, text="/set_long_tp 4050").accepted
    assert commands.handle(user_id=123, text="/set_short_sl 4055").accepted
    assert commands.handle(user_id=123, text="/set_short_tp 3990").accepted
    long, short = broker.positions()
    assert (long.stop_loss, long.take_profit) == (Decimal("4000"), Decimal("4050"))
    assert (short.stop_loss, short.take_profit) == (Decimal("4055"), Decimal("3990"))

    assert commands.handle(user_id=123, text="/remove_long_sl").accepted
    assert commands.handle(user_id=123, text="/remove_short_tp").accepted
    long, short = broker.positions()
    assert long.stop_loss is None
    assert short.take_profit is None


def test_manual_trigger_creation_and_cancellation_use_explicit_ids(tmp_path) -> None:
    commands, broker, state, _ = commands_in_manual_mode(tmp_path)
    source_id = broker.positions()[0].position_id

    created = commands.handle(
        user_id=123,
        text=f"/set_short_trigger 4005 1 {source_id}",
    )
    trigger_id = broker.triggers()[0].trigger_id
    cancelled = commands.handle(user_id=123, text=f"/cancel_trigger {trigger_id}")

    assert created.accepted and cancelled.accepted
    assert broker.triggers() == ()
    assert state.load_cycle("cycle-1").manual_triggers == ()


def test_close_side_accepts_optional_broker_position_id(tmp_path) -> None:
    commands, broker, state, _ = commands_in_manual_mode(tmp_path)
    long = next(p for p in broker.positions() if p.side is Side.LONG)

    result = commands.handle(user_id=123, text=f"/close_long {long.position_id}")

    assert result.accepted
    assert all(p.side is not Side.LONG for p in broker.positions())
    assert state.load_cycle("cycle-1").long_position is None


def test_manual_commands_are_rejected_outside_scenario_nine(tmp_path) -> None:
    manager, _, state, costs = runtime(tmp_path)
    manager.start_scenario_one(cycle_id="cycle-1", config=AppConfig(), coverage=costs)
    config = ConfigManager(tmp_path / "commands.json")
    config.load(create_if_missing=True)
    commands = TelegramCommandService(
        config_manager=config,
        authorized_user_ids={123},
        manual_trading=ManualTradingService(orders=manager.orders, state=state),
    )

    result = commands.handle(user_id=123, text="/set_long_sl 4000")

    assert not result.accepted
    assert "Scenario 9" in result.message


def test_unauthorized_user_cannot_close_position(tmp_path) -> None:
    commands, broker, _, _ = commands_in_manual_mode(tmp_path)
    before = broker.positions()

    result = commands.handle(user_id=999, text="/close_long")

    assert not result.accepted
    assert broker.positions() == before


def test_partial_close_sends_immediate_position_details(tmp_path) -> None:
    commands, broker, _, notifications = commands_in_manual_mode(tmp_path)
    original_close = broker.close_position

    def partial_close(**kwargs):
        confirmation = original_close(**kwargs)
        return replace(
            confirmation,
            status="CONFIRMED",
            requested_size=Decimal("1"),
            filled_size=Decimal("0.4"),
        )

    broker.close_position = partial_close  # type: ignore[method-assign]
    result = commands.handle(user_id=123, text="/close_short")

    assert result.accepted
    assert notifications.messages
    assert "Частичное исполнение SHORT" in notifications.messages[0]
    assert "requested=1" in notifications.messages[0]
    assert "filled=0.4" in notifications.messages[0]
