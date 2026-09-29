"""TradingSessionManager controlling lifecycle state transitions and permissions."""

import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import SessionState, TradingMode
from trad_auto.core.events import KillSwitchActivatedEvent, TradingSessionStateChangedEvent
from trad_auto.core.exceptions import InvalidSessionTransitionError
from trad_auto.core.models.session import FinancialLimits, TradingSession

logger = logging.getLogger(__name__)


class TradingSessionManager:
    """Deterministic state machine governing trading authorizations and safety locks."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._current_session: TradingSession | None = None
        self._system_state: SessionState = SessionState.IDLE
        self._event_bus = event_bus
        self._session_counter = 0

    @property
    def current_session(self) -> TradingSession | None:
        return self._current_session

    @property
    def state(self) -> SessionState:
        if self._system_state == SessionState.EMERGENCY_STOP:
            return SessionState.EMERGENCY_STOP
        if self._current_session is None:
            return SessionState.IDLE
        return self._current_session.status

    def get_state(self) -> SessionState:
        """Returns the current dynamic SessionState."""
        return self.state

    def _emit_state_change(
        self,
        session_id: UUID,
        old_state: SessionState,
        new_state: SessionState,
        reason: str,
    ) -> None:
        if self._event_bus:
            self._event_bus.publish(
                TradingSessionStateChangedEvent(
                    session_id=session_id,
                    old_state=old_state,
                    new_state=new_state,
                    reason=reason,
                )
            )

    def activate_session(
        self,
        mode: TradingMode,
        limits: FinancialLimits,
        owner_command_id: UUID,
        current_time: datetime,
        duration_hours: int = 8,
    ) -> TradingSession:
        """Activates a new TRADING session after owner confirmation."""
        if self.state == SessionState.EMERGENCY_STOP:
            raise InvalidSessionTransitionError(
                "Cannot activate session while EMERGENCY_STOP is active"
            )
        if self.state == SessionState.TRADING:
            raise InvalidSessionTransitionError("A trading session is already active")
        if self.state == SessionState.RISK_LOCKED:
            raise InvalidSessionTransitionError("Cannot activate session while RISK_LOCKED")

        self._session_counter += 1
        expires_at = current_time + timedelta(hours=duration_hours)

        old_state = self.state
        new_session = TradingSession(
            session_id=uuid4(),
            session_number=self._session_counter,
            mode=mode,
            limits=limits,
            deployed_capital=ZERO_DECIMAL,
            authorized_at=current_time,
            expires_at=expires_at,
            status=SessionState.TRADING,
            owner_command_id=owner_command_id,
        )

        self._current_session = new_session
        self._system_state = SessionState.TRADING
        self._emit_state_change(
            new_session.session_id, old_state, SessionState.TRADING, "Owner confirmed start"
        )
        return new_session

    def pause_session(self, reason: str = "Owner manual pause") -> TradingSession:
        """Pauses active session; blocks new entries while preserving protective exits."""
        if self.state != SessionState.TRADING or self._current_session is None:
            raise InvalidSessionTransitionError(f"Cannot pause session from state: {self.state}")

        old_state = self.state
        self._current_session = self._current_session.transition_to(SessionState.PAUSED)
        self._system_state = SessionState.PAUSED
        self._emit_state_change(
            self._current_session.session_id, old_state, SessionState.PAUSED, reason
        )
        return self._current_session

    def resume_session(self, reason: str = "Owner manual resume") -> TradingSession:
        """Resumes active trading from PAUSED state."""
        if self.state != SessionState.PAUSED or self._current_session is None:
            raise InvalidSessionTransitionError(f"Cannot resume session from state: {self.state}")

        old_state = self.state
        self._current_session = self._current_session.transition_to(SessionState.TRADING)
        self._system_state = SessionState.TRADING
        self._emit_state_change(
            self._current_session.session_id, old_state, SessionState.TRADING, reason
        )
        return self._current_session

    def stop_session(
        self,
        open_positions_count: int = 0,
        reason: str = "Owner manual stop",
    ) -> SessionState:
        """Executes deterministic StopTrading transition based on active position count.

        If open positions exist: TRADING -> PAUSED (protective stops continue).
        If zero open positions: TRADING / PAUSED -> IDLE.
        """
        if self.state not in (SessionState.TRADING, SessionState.PAUSED):
            raise InvalidSessionTransitionError(f"Cannot stop trading from state: {self.state}")

        old_state = self.state
        session_id = self._current_session.session_id if self._current_session else uuid4()

        if open_positions_count > 0:
            target_state = SessionState.PAUSED
            if self._current_session:
                self._current_session = self._current_session.transition_to(target_state)
            self._system_state = target_state
            self._emit_state_change(
                session_id,
                old_state,
                target_state,
                f"{reason}: {open_positions_count} open positions remain managed in PAUSED",
            )
            return target_state
        else:
            target_state = SessionState.IDLE
            self._current_session = None
            self._system_state = target_state
            self._emit_state_change(
                session_id, old_state, target_state, f"{reason}: All positions flat"
            )
            return target_state

    def lock_risk(self, reason: str = "Risk limit breached") -> TradingSession:
        """Locks session due to risk breach; preserves protective exits."""
        if self._current_session is None or self.state not in (
            SessionState.TRADING,
            SessionState.PAUSED,
        ):
            raise InvalidSessionTransitionError(f"Cannot lock risk from state: {self.state}")

        old_state = self.state
        self._current_session = self._current_session.transition_to(SessionState.RISK_LOCKED)
        self._system_state = SessionState.RISK_LOCKED
        self._emit_state_change(
            self._current_session.session_id, old_state, SessionState.RISK_LOCKED, reason
        )
        return self._current_session

    def reset_risk_lock(self, policy_verified: bool = True) -> SessionState:
        """Resets RISK_LOCKED to IDLE via explicit configured policy."""
        if self.state != SessionState.RISK_LOCKED:
            raise InvalidSessionTransitionError(f"Cannot reset risk lock from state: {self.state}")
        if not policy_verified:
            raise InvalidSessionTransitionError("Risk reset policy validation failed")

        old_state = self.state
        session_id = self._current_session.session_id if self._current_session else uuid4()
        self._current_session = None
        self._system_state = SessionState.IDLE
        self._emit_state_change(
            session_id, old_state, SessionState.IDLE, "Explicit risk lock reset"
        )
        return SessionState.IDLE

    def activate_kill_switch(
        self,
        reason: str = "Emergency kill switch triggered",
        triggered_by: str = "Owner",
    ) -> None:
        """Immediate emergency action. No confirmation required. Enters EMERGENCY_STOP."""
        old_state = self.state
        session_id = self._current_session.session_id if self._current_session else uuid4()

        self._system_state = SessionState.EMERGENCY_STOP
        if self._current_session:
            self._current_session = self._current_session.transition_to(SessionState.EMERGENCY_STOP)

        if self._event_bus:
            self._event_bus.publish(
                KillSwitchActivatedEvent(reason=reason, triggered_by=triggered_by)
            )
            self._emit_state_change(session_id, old_state, SessionState.EMERGENCY_STOP, reason)

    def reset_kill_switch(self) -> SessionState:
        """Resets EMERGENCY_STOP to IDLE after explicit owner confirmation."""
        if self.state != SessionState.EMERGENCY_STOP:
            raise InvalidSessionTransitionError("Kill switch is not active")

        old_state = self.state
        session_id = self._current_session.session_id if self._current_session else uuid4()
        self._current_session = None
        self._system_state = SessionState.IDLE
        self._emit_state_change(
            session_id, old_state, SessionState.IDLE, "Confirmed kill switch reset"
        )
        return SessionState.IDLE

    def is_trading_allowed(self, current_time: datetime) -> bool:
        """Returns True if the system is actively authorized to take new trade entries."""
        if self.state != SessionState.TRADING:
            return False
        if self._current_session is None:
            return False
        if self._current_session.is_expired(current_time):
            return False
        return True
