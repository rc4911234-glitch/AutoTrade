"""Central Risk Gatekeeper enforcing all four financial limits and trade intent conversion."""

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderType, RiskDecisionType
from trad_auto.core.events import (
    DailyProfitTargetReachedEvent,
    RiskLimitBreachedEvent,
    TradeIntentCreatedEvent,
    TradeProposalEvent,
    TradeProposalRejectedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.intent import RiskCheckResult, TradeIntent
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.risk.ledger_bridge import PortfolioRiskBridge
from trad_auto.risk.sizer import VolatilityPositionSizer

if TYPE_CHECKING:
    from trad_auto.market_data.streaming.watchdog import FeedWatchdog
    from trad_auto.news.shield import NewsVolatilityShield


class RiskGatekeeper:
    """Central risk firewall validating proposals and creating sized TradeIntents.

    Enforces:
    1. Active session must be in SessionState.TRADING.
    2. Daily maximum loss threshold (triggers auto transition to RISK_LOCKED).
    3. Daily profit target locking (pauses trading to protect accumulated gains).
    4. News & Macro volatility shield (macro event blackouts & sentiment alignment).
    5. Sizing via ATR stop distance with down-rounding to step size.
    6. Maximum portfolio exposure ceilings.
    """

    def __init__(
        self,
        event_bus: EventBus,
        session_manager: TradingSessionManager,
        portfolio_bridge: PortfolioRiskBridge,
        instruments: dict[str, Instrument] | None = None,
        default_risk_pct: Decimal = Decimal("0.01"),  # 1% cash risk per trade
        order_type: OrderType = OrderType.LIMIT,
        intent_validity_seconds: int = 300,  # 5 minutes validity
        news_shield: "NewsVolatilityShield | None" = None,
        feed_watchdog: "FeedWatchdog | None" = None,
    ) -> None:
        self._event_bus = event_bus
        self._session_manager = session_manager
        self._portfolio_bridge = portfolio_bridge
        self._instruments = dict(instruments) if instruments is not None else {}
        self.default_risk_pct = default_risk_pct
        self.order_type = order_type
        self.intent_validity_seconds = intent_validity_seconds
        self._news_shield = news_shield
        self._feed_watchdog = feed_watchdog

        # Subscribe to strategy proposals
        self._event_bus.subscribe(TradeProposalEvent, self._handle_trade_proposal_event)

    def register_instrument(self, instrument: Instrument) -> None:
        self._instruments[instrument.symbol] = instrument

    @property
    def news_shield(self) -> "NewsVolatilityShield | None":
        return self._news_shield

    def set_news_shield(self, news_shield: "NewsVolatilityShield | None") -> None:
        self._news_shield = news_shield

    @property
    def feed_watchdog(self) -> "FeedWatchdog | None":
        return self._feed_watchdog

    def set_feed_watchdog(self, feed_watchdog: "FeedWatchdog | None") -> None:
        self._feed_watchdog = feed_watchdog

    def evaluate_proposal(self, proposal: TradeProposal) -> RiskCheckResult:
        """Evaluates TradeProposal against all financial limits and creates TradeIntent."""
        # 1. Session State & Expiration Check
        now = proposal.timestamp
        if not self._session_manager.is_trading_allowed(now):
            state = self._session_manager.get_state()
            reason = f"Trading is not authorized (session state: {state})"
            return self._reject_proposal(proposal, reason)

        # 1b. Feed Freshness Check (Capital Protection First)
        if self._feed_watchdog is not None and self._feed_watchdog.is_stale(
            proposal.symbol, now=now
        ):
            return self._reject_proposal(
                proposal,
                f"Market data feed for {proposal.symbol} is STALE "
                f"(> {self._feed_watchdog.timeout_seconds}s silence). "
                "Speculative entry blocked fail-closed to prevent trading on blind/delayed quotes.",
            )

        session = self._session_manager.current_session
        if session is None:
            return self._reject_proposal(proposal, "Active trading session is missing")

        # 2. Daily Maximum Loss Check (Drawdown Breaker)
        net_today_pnl = (
            self._portfolio_bridge.get_today_realized_pnl()
            + self._portfolio_bridge.get_current_unrealized_pnl()
        )
        if session.limits.max_allowed_loss is not None:
            max_loss = session.limits.max_allowed_loss
            if net_today_pnl <= -max_loss:
                # Trigger Risk Lock!
                lock_msg = (
                    f"Daily net loss ({net_today_pnl}) breached max_allowed_loss (-{max_loss})"
                )
                self._session_manager.lock_risk(reason=lock_msg)
                self._event_bus.publish(
                    RiskLimitBreachedEvent(
                        limit_type="DAILY_LOSS",
                        current_value=net_today_pnl,
                        limit_value=-max_loss,
                        action_taken="TRANSITION_TO_RISK_LOCKED",
                    )
                )
                return self._reject_proposal(
                    proposal,
                    f"Daily loss limit breached: {net_today_pnl} <= -{max_loss}. Session locked.",
                )

        # 3. Daily Profit Target Check (Gain Lock)
        if session.limits.daily_profit_target is not None:
            profit_target = session.limits.daily_profit_target
            realized = self._portfolio_bridge.get_today_realized_pnl()
            if realized >= profit_target:
                self._session_manager.pause_session(
                    reason=f"Daily profit target reached ({realized} >= {profit_target})"
                )
                self._event_bus.publish(
                    DailyProfitTargetReachedEvent(
                        realized_profit=realized,
                        target=profit_target,
                    )
                )
                return self._reject_proposal(
                    proposal,
                    f"Daily profit target reached ({realized} >= {profit_target}). Trading paused.",
                )

        # 4. News & Macro Volatility Shield Check
        if self._news_shield is not None:
            allowed, news_reason = self._news_shield.check_trade_proposal(proposal, now=now)
            if not allowed:
                return self._reject_proposal(proposal, news_reason)

        # 5. Instrument Tradability Check
        instrument = self._instruments.get(proposal.symbol)
        if instrument is None or not instrument.is_tradable:
            return self._reject_proposal(
                proposal, f"Instrument '{proposal.symbol}' is unconfigured or not tradable"
            )

        # 6. Available Capital Check
        available_capital = (
            session.limits.authorized_capital - self._portfolio_bridge.get_total_deployed_capital()
        )
        if available_capital <= ZERO_DECIMAL:
            return self._reject_proposal(
                proposal, "No remaining authorized capital available in this session"
            )

        # 7. Volatility Position Sizing
        quantity = VolatilityPositionSizer.calculate_quantity(
            proposal=proposal,
            instrument=instrument,
            available_capital=available_capital,
            risk_pct_per_trade=self.default_risk_pct,
            max_risk_amount=session.limits.max_risk_amount,
        )

        if quantity < instrument.min_quantity or quantity <= ZERO_DECIMAL:
            return self._reject_proposal(
                proposal,
                f"Calculated position quantity ({quantity}) is below "
                f"instrument minimum quantity ({instrument.min_quantity})",
            )

        # 8. Maximum Gross Exposure Check
        trade_notional = quantity * proposal.entry_price
        current_exposure = self._portfolio_bridge.get_current_exposure()
        new_exposure = current_exposure + trade_notional

        if session.limits.max_exposure is not None:
            max_exp = session.limits.max_exposure
            if new_exposure > max_exp:
                self._event_bus.publish(
                    RiskLimitBreachedEvent(
                        limit_type="MAX_EXPOSURE",
                        current_value=new_exposure,
                        limit_value=max_exp,
                        action_taken="PROPOSAL_REJECTED",
                    )
                )
                return self._reject_proposal(
                    proposal,
                    f"Trade notional ({trade_notional}) pushes total exposure "
                    f"({new_exposure}) above limit ({max_exp})",
                )

        # 9. All Checks Passed -> Create TradeIntent
        created_at = proposal.timestamp
        expires_at = created_at + timedelta(seconds=self.intent_validity_seconds)
        intent = TradeIntent(
            proposal_id=proposal.proposal_id,
            strategy_id=proposal.strategy_id,
            symbol=proposal.symbol,
            direction=proposal.direction,
            order_type=self.order_type,
            entry_price=proposal.entry_price,
            quantity=quantity,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            timeframe=proposal.timeframe,
            created_at=created_at,
            expires_at=expires_at,
        )

        self._event_bus.publish(TradeIntentCreatedEvent(intent=intent))

        return RiskCheckResult(
            decision=RiskDecisionType.APPROVED,
            proposal_id=proposal.proposal_id,
            reason="Proposal approved by risk gatekeeper",
            calculated_quantity=quantity,
            allocated_risk=intent.risk_amount,
        )

    def _reject_proposal(self, proposal: TradeProposal, reason: str) -> RiskCheckResult:
        self._event_bus.publish(
            TradeProposalRejectedEvent(
                proposal_id=proposal.proposal_id,
                strategy_id=proposal.strategy_id,
                symbol=proposal.symbol,
                reason=reason,
            )
        )
        return RiskCheckResult(
            decision=RiskDecisionType.REJECTED,
            proposal_id=proposal.proposal_id,
            reason=reason,
        )

    def _handle_trade_proposal_event(self, event: TradeProposalEvent) -> None:
        if event.proposal:
            self.evaluate_proposal(event.proposal)
