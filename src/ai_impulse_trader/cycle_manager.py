"""Scenario 1 orchestration built on broker-neutral runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .config import AppConfig
from .enums import CycleState, Side
from .exceptions import DomainValidationError
from .formula_engine import FormulaEngine
from .models import ApplicationState, Cycle, Position, ReentryCostRecord
from .order_manager import OrderManager
from .state_manager import StateManager


@dataclass(frozen=True)
class InitialCoverageInputs:
    """Confirmed/account-specific costs required by Scenario 1."""

    long_close_commission: Decimal
    short_close_commission: Decimal
    actual_initial_slippage: Decimal

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise DomainValidationError(f"{name} must be a non-negative Decimal")


@dataclass(frozen=True)
class ReentryInputs:
    """Broker-confirmed variable costs of one Reentry."""

    close_commission: Decimal
    actual_slippage: Decimal
    confirmation_id: str

    def __post_init__(self) -> None:
        for name in ("close_commission", "actual_slippage"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise DomainValidationError(f"{name} must be a non-negative Decimal")
        if not isinstance(self.confirmation_id, str) or not self.confirmation_id:
            raise DomainValidationError("confirmation_id must be non-empty")


class CycleStartError(RuntimeError):
    """Raised when safety preconditions prohibit a new Cycle."""


class CycleManager:
    """Open and persist a fully broker-confirmed Scenario 1."""

    def __init__(
        self, *, orders: OrderManager, formulas: FormulaEngine, state: StateManager
    ) -> None:
        if not isinstance(orders, OrderManager) or not isinstance(
            formulas, FormulaEngine
        ):
            raise DomainValidationError("invalid Cycle Manager dependency")
        if not isinstance(state, StateManager):
            raise DomainValidationError("state must be a StateManager")
        self._orders, self._formulas, self._state = orders, formulas, state

    @property
    def orders(self) -> OrderManager:
        """Expose broker-neutral order coordination to the runtime controller."""
        return self._orders

    def start_scenario_one(
        self, *, cycle_id: str, config: AppConfig, coverage: InitialCoverageInputs
    ) -> Cycle:
        """Open equal positions, calculate levels, confirm them, and persist RUNNING."""
        if not isinstance(config, AppConfig) or not isinstance(
            coverage, InitialCoverageInputs
        ):
            raise DomainValidationError("invalid Scenario 1 input")
        self._require_clean_start(cycle_id, config)
        cycle = Cycle(
            cycle_id,
            config.symbol,
            config.position_size,
            state=CycleState.OPENING,
            last_confirmed_step="CYCLE_CREATED",
        )
        self._state.save_cycle(cycle, reason="SCENARIO_1_CREATED")
        bid, ask = self._orders.broker.quote()
        try:
            pair = self._orders.open_initial_pair(
                cycle_id=cycle_id, position_size=config.position_size
            )
            long_entry = _price(pair.long.execution_price)
            short_entry = _price(pair.short.execution_price)
            stops = self._formulas.calculate_initial_stops(
                long_entry=long_entry,
                short_entry=short_entry,
                stop_distance=config.stop_distance,
            )
            base = self._formulas.calculate_base_coverage(
                spread=ask - bid,
                initial_long_close_commission=coverage.long_close_commission,
                initial_short_close_commission=coverage.short_close_commission,
                actual_initial_slippage=coverage.actual_initial_slippage,
                target_profit=config.target_profit,
            )
            targets = self._formulas.calculate_initial_take_profits(
                long_stop=stops.long, short_stop=stops.short, base_coverage=base
            )
            self._orders.apply_initial_levels(
                cycle_id=cycle_id,
                long_position_id=pair.long.position_id or "",
                short_position_id=pair.short.position_id or "",
                stops=stops,
                take_profits=targets,
            )
            cycle = replace(
                cycle,
                state=CycleState.RUNNING,
                initial_long_entry=long_entry,
                initial_short_entry=short_entry,
                base_coverage=base,
                initial_long_close_commission=coverage.long_close_commission,
                initial_short_close_commission=coverage.short_close_commission,
                actual_initial_slippage=coverage.actual_initial_slippage,
                saved_long_tp=targets.long,
                saved_short_tp=targets.short,
                long_position=self._position(pair.long.position_id, Side.LONG),
                short_position=self._position(pair.short.position_id, Side.SHORT),
                last_confirmed_step="INITIAL_LEVELS_CONFIRMED",
            )
            self._state.save_cycle(cycle, reason="SCENARIO_1_RUNNING")
            self._state.save_application_state(
                ApplicationState(active_cycle_id=cycle_id)
            )
            return cycle
        except Exception:
            positions = self._orders.broker.positions()
            failed = replace(
                cycle,
                state=CycleState.RECOVERY,
                long_position=next((p for p in positions if p.side is Side.LONG), None),
                short_position=next(
                    (p for p in positions if p.side is Side.SHORT), None
                ),
                last_confirmed_step="SCENARIO_1_PARTIAL",
            )
            self._state.save_cycle(failed, reason="SCENARIO_1_RECOVERY")
            self._state.save_application_state(
                ApplicationState(active_cycle_id=cycle_id, recovery_required=True)
            )
            raise

    def _require_clean_start(self, cycle_id: str, config: AppConfig) -> None:
        if not isinstance(cycle_id, str) or not cycle_id.strip():
            raise DomainValidationError("cycle_id must be a non-empty string")
        if config.stop_after_current_cycle:
            raise CycleStartError("STOP_AFTER_CURRENT_CYCLE blocks a new Cycle")
        active = [
            c
            for c in self._state.list_cycles()
            if c.state not in {CycleState.FINISHED, CycleState.ARCHIVED}
        ]
        if (
            self._state.load_cycle(cycle_id)
            or active
            or self._orders.broker.positions()
            or self._orders.broker.triggers()
        ):
            raise CycleStartError("positions, triggers, or unfinished Cycle exist")

    def register_confirmed_stop(self, *, cycle_id: str, stopped_side: Side) -> Cycle:
        """Persist the stopped side and create its Trigger at initial entry."""
        cycle = self._required_cycle(cycle_id)
        if cycle.current_scenario == 8:
            return self.enter_scenario_nine(cycle_id=cycle_id)
        position = (
            cycle.long_position if stopped_side is Side.LONG else cycle.short_position
        )
        initial_entry = (
            cycle.initial_long_entry
            if stopped_side is Side.LONG
            else cycle.initial_short_entry
        )
        if position is None or initial_entry is None:
            raise CycleStartError("stopped position or initial entry is missing")
        confirmation = self._orders.create_reentry_trigger(
            cycle_id=cycle_id,
            scenario=cycle.current_scenario + 1,
            side=stopped_side,
            price=initial_entry,
            size=cycle.position_size,
            source_position_id=position.position_id,
        )
        trigger = next(
            item
            for item in self._orders.broker.triggers()
            if item.trigger_id == confirmation.trigger_id
        )
        positions = self._orders.broker.positions()
        updated = replace(
            cycle,
            long_position=next((p for p in positions if p.side is Side.LONG), None),
            short_position=next((p for p in positions if p.side is Side.SHORT), None),
            active_trigger=trigger,
            last_confirmed_step=f"{stopped_side.value}_STOP_TRIGGER_CONFIRMED",
        )
        self._state.save_cycle(updated, reason="STOP_AND_TRIGGER_CONFIRMED")
        return updated

    def register_confirmed_reentry(
        self,
        *,
        cycle_id: str,
        reentry_side: Side,
        stop_distance: Decimal,
        costs: ReentryInputs,
    ) -> Cycle:
        """Add one REENTRY_COST and synchronously replace both open positions' TP."""
        cycle = self._required_cycle(cycle_id)
        if cycle.current_scenario >= 8:
            raise CycleStartError("automatic Reentry is limited to Scenario 2-8")
        if cycle.saved_long_tp is None or cycle.saved_short_tp is None:
            raise CycleStartError("saved Take Profit values are missing")
        positions = self._orders.broker.positions()
        reentry = _latest_position(positions, reentry_side)
        opposite = _latest_position(
            positions, Side.SHORT if reentry_side is Side.LONG else Side.LONG
        )
        reentry_cost = self._formulas.calculate_reentry_cost(
            entry_stop_distance=stop_distance,
            reentry_close_commission=costs.close_commission,
            actual_reentry_slippage=costs.actual_slippage,
        )
        targets = self._formulas.calculate_next_take_profits(
            saved_long_take_profit=cycle.saved_long_tp,
            saved_short_take_profit=cycle.saved_short_tp,
            reentry_cost=reentry_cost,
        )
        scenario = cycle.current_scenario + 1
        stop = (
            reentry.current_entry - stop_distance
            if reentry_side is Side.LONG
            else reentry.current_entry + stop_distance
        )
        self._orders.apply_reentry_levels(
            cycle_id=cycle_id,
            scenario=scenario,
            reentry_position_id=reentry.position_id,
            opposite_position_id=opposite.position_id,
            reentry_stop=stop,
            reentry_take_profit=(
                targets.long if reentry_side is Side.LONG else targets.short
            ),
            opposite_take_profit=(
                targets.short if reentry_side is Side.LONG else targets.long
            ),
        )
        positions = self._orders.broker.positions()
        record = ReentryCostRecord(
            index=len(cycle.reentry_cost_history) + 1,
            side=reentry_side,
            entry_stop_distance=stop_distance,
            close_commission=costs.close_commission,
            actual_slippage=costs.actual_slippage,
            total=reentry_cost,
            confirmation_id=costs.confirmation_id,
        )
        updated = replace(
            cycle,
            current_scenario=scenario,
            saved_long_tp=targets.long,
            saved_short_tp=targets.short,
            total_reentry_cost=cycle.total_reentry_cost + reentry_cost,
            reentry_cost_history=cycle.reentry_cost_history + (record,),
            long_position=_latest_position(positions, Side.LONG),
            short_position=_latest_position(positions, Side.SHORT),
            active_trigger=None,
            last_confirmed_step="REENTRY_LEVELS_CONFIRMED",
        )
        self._state.save_cycle(updated, reason=f"SCENARIO_{scenario}_RUNNING")
        return updated

    def finish_after_take_profit(self, *, cycle_id: str) -> Cycle:
        """Cancel the remaining Trigger and finish only after broker reconciliation."""
        cycle = self._required_cycle(cycle_id)
        if self._orders.broker.positions():
            raise CycleStartError("cannot finish while an open position remains")
        if cycle.active_trigger is not None:
            self._orders.cancel_reentry_trigger(
                cycle_id=cycle_id,
                scenario=cycle.current_scenario,
                trigger_id=cycle.active_trigger.trigger_id,
            )
        if self._orders.broker.triggers():
            raise CycleStartError("cannot finish while an active Trigger remains")
        finished = replace(
            cycle,
            state=CycleState.FINISHED,
            long_position=None,
            short_position=None,
            active_trigger=None,
            completed_at=datetime.now(timezone.utc),
            last_confirmed_step="TAKE_PROFIT_AND_TRIGGER_CANCEL_CONFIRMED",
        )
        self._state.save_cycle(finished, reason="CYCLE_FINISHED")
        application = self._state.load_application_state() or ApplicationState()
        self._state.save_application_state(
            ApplicationState(
                stop_after_current_cycle=application.stop_after_current_cycle,
                active_cycle_id=None,
                recovery_required=False,
            )
        )
        return finished

    def enter_scenario_nine(self, *, cycle_id: str) -> Cycle:
        """Stop automation, remove all levels, preserve positions, and persist manual mode."""
        cycle = self._required_cycle(cycle_id)
        if cycle.current_scenario != 8:
            raise CycleStartError("Scenario 9 can only follow Scenario 8")
        self._orders.enter_manual_mode(cycle_id=cycle_id, scenario=9)
        if self._orders.broker.triggers():
            raise CycleStartError("active Trigger remains after Scenario 9 takeover")
        positions = self._orders.broker.positions()
        if any(p.stop_loss is not None or p.take_profit is not None for p in positions):
            raise CycleStartError("automatic position level remains in Scenario 9")
        manual = replace(
            cycle,
            state=CycleState.MANUAL_MODE,
            current_scenario=9,
            long_position=next((p for p in positions if p.side is Side.LONG), None),
            short_position=next((p for p in positions if p.side is Side.SHORT), None),
            active_trigger=None,
            last_confirmed_step="SCENARIO_9_MANUAL_TAKEOVER_CONFIRMED",
        )
        self._state.save_cycle(manual, reason="SCENARIO_9_MANUAL_MODE")
        return manual

    def _required_cycle(self, cycle_id: str) -> Cycle:
        cycle = self._state.load_cycle(cycle_id)
        if cycle is None or cycle.state is not CycleState.RUNNING:
            raise CycleStartError("running Cycle does not exist")
        return cycle

    def _position(self, position_id: Optional[str], side: Side) -> Position:
        for position in self._orders.broker.positions():
            if position.position_id == position_id and position.side is side:
                return position
        raise CycleStartError("confirmed broker position is missing")


def _price(value: Optional[Decimal]) -> Decimal:
    if value is None or value <= 0:
        raise CycleStartError("broker confirmation has no valid execution price")
    return value


def _latest_position(positions: tuple, side: Side) -> Position:
    matches = [position for position in positions if position.side is side]
    if not matches:
        raise CycleStartError(f"open {side.value} position is missing")
    return matches[-1]
