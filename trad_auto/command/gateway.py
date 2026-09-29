"""CommandGateway coordinating authentication, deduplication,
validation, confirmation, and execution.
"""

import logging
from uuid import uuid4

from config.settings import Settings
from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.command.validator import CommandValidator
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import ChannelType, CommandStatus, CommandType, TradingMode
from trad_auto.core.events import CommandExecutedEvent, CommandReceivedEvent
from trad_auto.core.exceptions import CommandAmbiguousError, ConfirmationExpiredError, TradAutoError
from trad_auto.core.models.command import (
    ActivateKillSwitchCommand,
    BaseCommand,
    CommandResult,
    ConfirmCommand,
    GetPositionsCommand,
    GetRiskStatusCommand,
    GetStatusCommand,
    GetTodayPnLCommand,
    InboundMessage,
    PauseTradingCommand,
    ResetKillSwitchCommand,
    ResumeTradingCommand,
    StartTradingCommand,
    StopTradingCommand,
)
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.time import Clock
from trad_auto.portfolio.ledger import PositionLedger

logger = logging.getLogger(__name__)


class CommandGateway:
    """Channel-agnostic gateway routing inbound messages to deterministic handlers."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        session_manager: TradingSessionManager,
        confirmation_manager: ConfirmationManager,
        deduplicator: MessageDeduplicator,
        event_bus: EventBus | None = None,
        position_ledger: PositionLedger | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._session_manager = session_manager
        self._confirmation_manager = confirmation_manager
        self._deduplicator = deduplicator
        self._validator = CommandValidator(
            max_session_capital=settings.max_authorized_capital_per_session
        )
        self._event_bus = event_bus
        self._position_ledger = position_ledger

    @property
    def position_ledger(self) -> PositionLedger | None:
        """Returns the connected position ledger."""
        return self._position_ledger

    @position_ledger.setter
    def position_ledger(self, ledger: PositionLedger | None) -> None:
        """Connects or updates the active position ledger."""
        self._position_ledger = ledger

    def handle_inbound_message(self, message: InboundMessage) -> CommandResult:
        """Processes an inbound message through security, deduplication, parsing, and execution."""
        now = self._clock.now()

        # 1. Owner Authentication Check
        if not self._is_authorized_owner(message):
            logger.warning(
                "Unauthorized sender attempted command: %s on channel %s",
                message.sender_id,
                message.channel,
            )
            return CommandResult(
                command_id=uuid4(),
                command_type=CommandType.GET_STATUS,
                status=CommandStatus.REJECTED,
                message="Unauthorized sender. Command dropped.",
            )

        # 2. Atomic Persistent Deduplication Check
        claim = self._deduplicator.try_claim(
            external_message_id=message.external_message_id,
            channel=message.channel.value,
            sender_id=message.sender_id,
            received_at=now,
        )

        if not claim.claimed:
            cached_msg = (
                claim.cached_response or "Duplicate message ignored (previously processed)."
            )
            return CommandResult(
                command_id=uuid4(),
                command_type=CommandType.GET_STATUS,
                status=CommandStatus.DUPLICATE,
                message=cached_msg,
            )

        # 3. Parse Natural Text to Typed Command Schema (Fail-Closed)
        raw_text = message.raw_text or ""
        try:
            command = self._validator.parse_text_command(
                raw_text=raw_text,
                sender_id=message.sender_id,
                channel=message.channel,
                external_message_id=message.external_message_id,
                received_at=now,
            )
        except CommandAmbiguousError as e:
            err_result = CommandResult(
                command_id=uuid4(),
                command_type=CommandType.GET_STATUS,
                status=CommandStatus.COMMAND_AMBIGUOUS,
                message=str(e),
            )
            self._deduplicator.mark_completed(message.external_message_id, err_result.message, now)
            return err_result

        # 4. Dispatch to Deterministic Command Handler
        if self._event_bus:
            self._event_bus.publish(
                CommandReceivedEvent(
                    command_id=command.command_id,
                    channel=command.channel,
                    command_type=self._get_command_type(command),
                    sender_id=command.sender_id,
                )
            )

        result = self._execute_command(command)

        # 5. Persist Deduplication Outcome & Publish Execution Event
        self._deduplicator.mark_completed(message.external_message_id, result.message, now)

        if self._event_bus:
            self._event_bus.publish(
                CommandExecutedEvent(
                    command_id=result.command_id,
                    command_type=result.command_type,
                    status=result.status,
                    message=result.message,
                )
            )

        return result

    def _is_authorized_owner(self, message: InboundMessage) -> bool:
        """Enforces owner identity validation across channels."""
        if message.channel == ChannelType.CLI:
            return self._settings.owner_cli_enabled
        if message.channel == ChannelType.WHATSAPP:
            if not self._settings.owner_whatsapp_number:
                return False
            return message.sender_id.strip() == self._settings.owner_whatsapp_number.strip()
        return False

    @staticmethod
    def _get_command_type(command: BaseCommand) -> CommandType:
        type_mapping: dict[type[BaseCommand], CommandType] = {
            StartTradingCommand: CommandType.START_TRADING,
            ConfirmCommand: CommandType.CONFIRM,
            StopTradingCommand: CommandType.STOP_TRADING,
            PauseTradingCommand: CommandType.PAUSE_TRADING,
            ResumeTradingCommand: CommandType.RESUME_TRADING,
            ActivateKillSwitchCommand: CommandType.ACTIVATE_KILL_SWITCH,
            ResetKillSwitchCommand: CommandType.RESET_KILL_SWITCH,
            GetStatusCommand: CommandType.GET_STATUS,
            GetTodayPnLCommand: CommandType.GET_TODAY_PNL,
            GetPositionsCommand: CommandType.GET_POSITIONS,
            GetRiskStatusCommand: CommandType.GET_RISK_STATUS,
        }
        return type_mapping.get(type(command), CommandType.GET_STATUS)

    def _execute_command(self, command: BaseCommand) -> CommandResult:
        """Routes parsed command to exact operational logic."""
        now = self._clock.now()

        try:
            # Query commands
            if isinstance(command, GetStatusCommand):
                session = self._session_manager.current_session
                mode_str = session.mode.value if session else self._settings.trading_mode
                status_msg = (
                    f"System State: {self._session_manager.state.value} | "
                    f"Session Mode: {mode_str} | "
                    f"Active Sessions: {1 if session else 0}"
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.GET_STATUS,
                    status=CommandStatus.EXECUTED,
                    message=status_msg,
                )

            if isinstance(command, GetTodayPnLCommand):
                if self._position_ledger is None:
                    return CommandResult(
                        command_id=command.command_id,
                        command_type=CommandType.GET_TODAY_PNL,
                        status=CommandStatus.EXECUTED,
                        message="P&L unavailable — portfolio/ledger module not implemented yet.",
                    )
                realized = self._position_ledger.get_today_realized_pnl(now.date())
                unrealized = self._position_ledger.get_current_unrealized_pnl()
                total_pnl = realized + unrealized
                pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
                sign = "+" if total_pnl > 0 else ""
                r_sign = "+" if realized > 0 else ""
                u_sign = "+" if unrealized > 0 else ""
                open_count = self._position_ledger.get_open_positions_count()
                pnl_msg = (
                    f"Today's P&L Summary {pnl_emoji}\n"
                    f"• Net P&L: {sign}${total_pnl:.2f}\n"
                    f"• Realized: {r_sign}${realized:.2f}\n"
                    f"• Unrealized: {u_sign}${unrealized:.2f}\n"
                    f"• Open Positions: {open_count}"
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.GET_TODAY_PNL,
                    status=CommandStatus.EXECUTED,
                    message=pnl_msg,
                )

            if isinstance(command, GetPositionsCommand):
                if self._position_ledger is None:
                    return CommandResult(
                        command_id=command.command_id,
                        command_type=CommandType.GET_POSITIONS,
                        status=CommandStatus.EXECUTED,
                        message="No open positions (portfolio module not implemented yet).",
                    )
                open_positions = self._position_ledger.get_open_positions()
                if not open_positions:
                    return CommandResult(
                        command_id=command.command_id,
                        command_type=CommandType.GET_POSITIONS,
                        status=CommandStatus.EXECUTED,
                        message="No open positions (System is flat).",
                    )
                lines = [f"Open Positions ({len(open_positions)}):"]
                for p in open_positions:
                    p_emoji = "🟢" if p.unrealized_pnl >= 0 else "🔴"
                    p_sign = "+" if p.unrealized_pnl > 0 else ""
                    lines.append(
                        f"• {p.symbol} {p.side.value}: {p.quantity} @ ${p.average_entry_price:.2f} "
                        f"(Mark: ${p.mark_price:.2f}) | "
                        f"PnL: {p_emoji} {p_sign}${p.unrealized_pnl:.2f}"
                    )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.GET_POSITIONS,
                    status=CommandStatus.EXECUTED,
                    message="\n".join(lines),
                )

            if isinstance(command, GetRiskStatusCommand):
                risk_msg = (
                    f"Risk Status: Normal | System State: {self._session_manager.state.value}"
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.GET_RISK_STATUS,
                    status=CommandStatus.EXECUTED,
                    message=risk_msg,
                )

            # Emergency Kill Switch (Immediate - No confirmation required)
            if isinstance(command, ActivateKillSwitchCommand):
                self._session_manager.activate_kill_switch(
                    reason=command.reason,
                    triggered_by=command.sender_id,
                )
                kill_msg = (
                    "🚨 KILL SWITCH ACTIVATED. System in EMERGENCY_STOP. All new entries blocked."
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.ACTIVATE_KILL_SWITCH,
                    status=CommandStatus.EXECUTED,
                    message=kill_msg,
                )

            # Reset Kill Switch (Requires explicit confirmation)
            if isinstance(command, ResetKillSwitchCommand):
                pending = self._confirmation_manager.create_confirmation(
                    command_id=command.command_id,
                    owner_identity=command.sender_id,
                    command_type=CommandType.RESET_KILL_SWITCH,
                    command_payload={},
                    current_time=now,
                    timeout_seconds=self._settings.confirmation_timeout_seconds,
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.RESET_KILL_SWITCH,
                    status=CommandStatus.PENDING_CONFIRMATION,
                    message=(
                        f"⚠️ Reset Kill Switch requested. "
                        f"Reply 'CONFIRM RESET {pending.confirmation_code}' within "
                        f"{self._settings.confirmation_timeout_seconds}s to unlock system."
                    ),
                    requires_confirmation=True,
                    pending_confirmation_id=pending.pending_confirmation_id,
                    confirmation_code=pending.confirmation_code,
                )

            # Start Trading (Requires explicit confirmation)
            if isinstance(command, StartTradingCommand):
                pending = self._confirmation_manager.create_confirmation(
                    command_id=command.command_id,
                    owner_identity=command.sender_id,
                    command_type=CommandType.START_TRADING,
                    command_payload={
                        "mode": command.mode.value,
                        "authorized_capital": str(command.authorized_capital),
                        "duration_hours": command.duration_hours
                        or self._settings.default_session_duration_hours,
                    },
                    current_time=now,
                    timeout_seconds=self._settings.confirmation_timeout_seconds,
                )
                prompt_msg = (
                    f"Trading request received: Authorized budget ₹{command.authorized_capital} "
                    f"({command.mode.value}). Reply 'CONFIRM START {pending.confirmation_code}' "
                    f"within {self._settings.confirmation_timeout_seconds}s to activate session."
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.START_TRADING,
                    status=CommandStatus.PENDING_CONFIRMATION,
                    message=prompt_msg,
                    requires_confirmation=True,
                    pending_confirmation_id=pending.pending_confirmation_id,
                    confirmation_code=pending.confirmation_code,
                )

            # Confirm Command
            if isinstance(command, ConfirmCommand):
                consumed = self._confirmation_manager.validate_and_consume(
                    owner_identity=command.sender_id,
                    code=command.confirmation_code,
                    current_time=now,
                )

                if consumed.command_type == CommandType.START_TRADING:
                    payload = consumed.command_payload
                    from decimal import Decimal

                    limits = FinancialLimits(
                        authorized_capital=Decimal(payload["authorized_capital"])
                    )
                    mode = TradingMode(payload["mode"])
                    duration = int(
                        payload.get("duration_hours", self._settings.default_session_duration_hours)
                    )

                    self._session_manager.activate_session(
                        mode=mode,
                        limits=limits,
                        owner_command_id=consumed.command_id,
                        current_time=now,
                        duration_hours=duration,
                    )
                    success_msg = (
                        f"✅ Trading session activated with ₹{limits.authorized_capital} "
                        f"budget ({mode.value}). System is monitoring."
                    )
                    return CommandResult(
                        command_id=command.command_id,
                        command_type=CommandType.CONFIRM,
                        status=CommandStatus.EXECUTED,
                        message=success_msg,
                    )

                if consumed.command_type == CommandType.RESET_KILL_SWITCH:
                    self._session_manager.reset_kill_switch()
                    return CommandResult(
                        command_id=command.command_id,
                        command_type=CommandType.CONFIRM,
                        status=CommandStatus.EXECUTED,
                        message="✅ Kill switch reset successfully. System returned to IDLE.",
                    )

                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.CONFIRM,
                    status=CommandStatus.REJECTED,
                    message=f"Unsupported confirmation command type: {consumed.command_type}",
                )

            # Stop Trading Command
            if isinstance(command, StopTradingCommand):
                # Phase 1: Open positions are 0 since portfolio module is deferred
                new_state = self._session_manager.stop_session(
                    open_positions_count=0,
                    reason=command.reason,
                )
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.STOP_TRADING,
                    status=CommandStatus.EXECUTED,
                    message=f"Session stopped. System state is now {new_state.value}.",
                )

            # Pause Trading Command
            if isinstance(command, PauseTradingCommand):
                self._session_manager.pause_session()
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.PAUSE_TRADING,
                    status=CommandStatus.EXECUTED,
                    message="Trading session PAUSED. New entries blocked; protective stops active.",
                )

            # Resume Trading Command
            if isinstance(command, ResumeTradingCommand):
                self._session_manager.resume_session()
                return CommandResult(
                    command_id=command.command_id,
                    command_type=CommandType.RESUME_TRADING,
                    status=CommandStatus.EXECUTED,
                    message="Trading session RESUMED. System actively monitoring entries.",
                )

        except ConfirmationExpiredError as e:
            return CommandResult(
                command_id=command.command_id,
                command_type=self._get_command_type(command),
                status=CommandStatus.REJECTED,
                message=str(e),
            )
        except TradAutoError as e:
            return CommandResult(
                command_id=command.command_id,
                command_type=self._get_command_type(command),
                status=CommandStatus.FAILED,
                message=str(e),
            )
        except Exception as e:
            logger.error("Unexpected error handling command: %s", e, exc_info=True)
            return CommandResult(
                command_id=command.command_id,
                command_type=self._get_command_type(command),
                status=CommandStatus.FAILED,
                message=f"Internal system error: {e}",
            )

        return CommandResult(
            command_id=command.command_id,
            command_type=self._get_command_type(command),
            status=CommandStatus.REJECTED,
            message="Unrecognized command flow.",
        )
