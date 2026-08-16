"""Restart reconciliation between durable state and the broker source of truth."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Tuple

from .broker_gateway import BrokerConfirmation, BrokerGateway
from .enums import CycleState
from .exceptions import DomainValidationError
from .models import ApplicationState, Cycle, Position, Trigger
from .state_manager import StateManager


@dataclass(frozen=True)
class RecoveryReport:
    """Auditable result of one startup reconciliation."""

    safe_to_continue: bool
    status: str
    cycle: Optional[Cycle]
    differences: Tuple[str, ...] = ()


class RecoveryManager:
    """Never infer trades: continue only when broker and SQLite agree exactly."""

    def __init__(self, *, broker: BrokerGateway, state: StateManager) -> None:
        if not isinstance(broker, BrokerGateway):
            raise DomainValidationError("broker must implement BrokerGateway")
        if not isinstance(state, StateManager):
            raise DomainValidationError("state must be a StateManager")
        self._broker = broker
        self._state = state

    def reconcile(self) -> RecoveryReport:
        """Compare active durable state with current normalized broker state."""
        application = self._state.load_application_state() or ApplicationState()
        broker_positions = self._broker.positions()
        broker_triggers = self._broker.triggers()
        if application.active_cycle_id is None:
            if broker_positions or broker_triggers:
                return self._unsafe(
                    application,
                    None,
                    ("BROKER_ACTIVITY_WITHOUT_ACTIVE_CYCLE",),
                )
            if application.recovery_required:
                self._state.save_application_state(
                    replace(application, recovery_required=False)
                )
            return RecoveryReport(True, "IDLE_CONFIRMED", None)

        cycle = self._state.load_cycle(application.active_cycle_id)
        if cycle is None:
            return self._unsafe(application, None, ("ACTIVE_CYCLE_STATE_MISSING",))
        differences = _compare_cycle(cycle, broker_positions, broker_triggers)
        if differences:
            return self._unsafe(application, cycle, differences)
        if cycle.state is CycleState.RECOVERY:
            return self._unsafe(application, cycle, ("CYCLE_REQUIRES_RECOVERY",))
        if cycle.state in {CycleState.FINISHED, CycleState.ARCHIVED}:
            self._state.save_application_state(
                replace(
                    application,
                    active_cycle_id=None,
                    recovery_required=False,
                )
            )
            return RecoveryReport(True, "FINISHED_CYCLE_CLEARED", cycle)

        self._state.save_application_state(
            replace(application, recovery_required=False)
        )
        return RecoveryReport(True, "ACTIVE_CYCLE_CONFIRMED", cycle)

    def recover_confirmation(self, request_id: str) -> BrokerConfirmation:
        """Resolve an uncertain timeout by its stable idempotency request ID."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise DomainValidationError("request_id must be a non-empty string")
        confirmation = self._broker.confirmation_for(request_id)
        if confirmation is None:
            raise RecoveryLookupError(
                f"broker has no confirmation for request: {request_id}"
            )
        return confirmation

    def _unsafe(
        self,
        application: ApplicationState,
        cycle: Optional[Cycle],
        differences: Tuple[str, ...],
    ) -> RecoveryReport:
        if not application.recovery_required:
            self._state.save_application_state(
                replace(application, recovery_required=True)
            )
        return RecoveryReport(False, "MANUAL_RECOVERY_REQUIRED", cycle, differences)


class RecoveryLookupError(RuntimeError):
    """Raised when an uncertain broker request cannot be resolved safely."""


def _compare_cycle(
    cycle: Cycle,
    broker_positions: Tuple[Position, ...],
    broker_triggers: Tuple[Trigger, ...],
) -> Tuple[str, ...]:
    differences = []
    expected_positions = {
        position.position_id: position
        for position in (cycle.long_position, cycle.short_position)
        if position is not None
    }
    actual_positions = {position.position_id: position for position in broker_positions}
    if set(expected_positions) != set(actual_positions):
        differences.append("POSITION_IDS_MISMATCH")
    for position_id in set(expected_positions) & set(actual_positions):
        expected = expected_positions[position_id]
        actual = actual_positions[position_id]
        if (
            expected.side is not actual.side
            or expected.size != actual.size
            or expected.current_entry != actual.current_entry
            or expected.stop_loss != actual.stop_loss
            or expected.take_profit != actual.take_profit
        ):
            differences.append("POSITION_STATE_MISMATCH:" + position_id)
    expected_triggers = {trigger.trigger_id for trigger in cycle.manual_triggers}
    if cycle.active_trigger is not None:
        expected_triggers.add(cycle.active_trigger.trigger_id)
    actual_triggers = {trigger.trigger_id for trigger in broker_triggers}
    if expected_triggers != actual_triggers:
        differences.append("TRIGGER_IDS_MISMATCH")
    return tuple(differences)
