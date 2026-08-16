"""Application coordination without broker- or transport-specific code."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from .config import AppConfig, ConfigManager
from .cycle_manager import CycleManager, InitialCoverageInputs
from .entry_filter import EntryDecision, EntryFilter, EntryReadiness
from .market_data import MarketDataStore
from .models import ApplicationState, Cycle
from .recovery_manager import RecoveryManager, RecoveryReport
from .state_manager import StateManager


@dataclass(frozen=True)
class RuntimeStatus:
    """Current boot result for logs, UI, and Telegram."""

    config: AppConfig
    recovery: RecoveryReport
    ready_for_entry: bool


class RuntimeController:
    """Coordinate boot, Recovery, market entry decisions, and Cycle creation."""

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        state_manager: StateManager,
        recovery_manager: RecoveryManager,
        cycle_manager: CycleManager,
        entry_filter: EntryFilter,
        market_data: MarketDataStore,
    ) -> None:
        self._config_manager = config_manager
        self._state_manager = state_manager
        self._recovery_manager = recovery_manager
        self._cycle_manager = cycle_manager
        self._entry_filter = entry_filter
        self._market_data = market_data
        self._config: Optional[AppConfig] = None
        self._last_decision: Optional[EntryDecision] = None

    def bootstrap(self, *, create_config_if_missing: bool = False) -> RuntimeStatus:
        """Load settings, synchronize the stop flag, and reconcile broker state."""
        config = self._config_manager.load(create_if_missing=create_config_if_missing)
        application = self._state_manager.load_application_state() or ApplicationState()
        if application.stop_after_current_cycle != config.stop_after_current_cycle:
            self._state_manager.save_application_state(
                replace(
                    application,
                    stop_after_current_cycle=config.stop_after_current_cycle,
                )
            )
        recovery = self._recovery_manager.reconcile()
        self._config = config
        return RuntimeStatus(
            config=config,
            recovery=recovery,
            ready_for_entry=(
                recovery.safe_to_continue
                and recovery.cycle is None
                and not config.stop_after_current_cycle
            ),
        )

    def ingest_price(
        self,
        *,
        price: Decimal,
        observed_at: datetime,
        now: Optional[datetime] = None,
    ) -> EntryDecision:
        """Update the forming candle and evaluate all new-Cycle gates."""
        config = self._required_config()
        self._market_data.ingest_price(price=price, observed_at=observed_at)
        previous, current = self._market_data.candles()
        application = self._state_manager.load_application_state() or ApplicationState()
        active = application.active_cycle_id is not None or any(
            cycle.completed_at is None for cycle in self._state_manager.list_cycles()
        )
        readiness = EntryReadiness(
            broker_connected=True,
            market_data_fresh=self._market_data.is_fresh(
                now or observed_at, maximum_age=timedelta(seconds=30)
            ),
            settings_loaded=True,
            previous_cycle_finished=not active,
            no_active_cycle=not active,
            no_open_positions=not self._cycle_manager.orders.broker.positions(),
            no_active_triggers=not self._cycle_manager.orders.broker.triggers(),
            no_active_orders=True,
            recovery_inactive=not application.recovery_required,
            stop_after_current_cycle=config.stop_after_current_cycle,
        )
        self._last_decision = self._entry_filter.evaluate(
            threshold=config.entry_candle_range_threshold,
            readiness=readiness,
            previous_candle=previous,
            current_candle=current,
        )
        return self._last_decision

    def start_authorized_cycle(
        self, *, cycle_id: str, coverage: InitialCoverageInputs
    ) -> Cycle:
        """Consume a positive candle decision and start Scenario 1 once."""
        if self._last_decision is None or not self._last_decision.start_cycle:
            raise RuntimeStateError("Entry Filter has not authorized START_CYCLE")
        cycle = self._cycle_manager.start_scenario_one(
            cycle_id=cycle_id,
            config=self._required_config(),
            coverage=coverage,
        )
        self._last_decision = None
        return cycle

    def refresh_runtime_config(self) -> AppConfig:
        """Reload Telegram-updated settings for future entry decisions."""
        self._config = self._config_manager.load()
        return self._config

    def reset_entry_after_completed_cycle(self) -> None:
        """Permit one new candle signal after confirmed Cycle completion."""
        self._entry_filter.reset_after_cycle()
        self._last_decision = None

    def _required_config(self) -> AppConfig:
        if self._config is None:
            raise RuntimeStateError("RuntimeController.bootstrap() must run first")
        return self._config


class RuntimeStateError(RuntimeError):
    """Raised when runtime operations are invoked out of lifecycle order."""
