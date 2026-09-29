"""Domain event contracts for Trad-Auto."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import SYSTEM_TIMEZONE, ZERO_DECIMAL
from trad_auto.core.enums import (
    ChannelType,
    CommandStatus,
    CommandType,
    DataFeedStatus,
    OrderSide,
    PositionSide,
    SessionState,
)
from trad_auto.core.models.intent import TradeIntent
from trad_auto.core.models.market_data import (
    Bar,
    DataQualityIssue,
    FundingRate,
    Quote,
    Trade,
)
from trad_auto.core.models.news import MacroEconomicEvent, NewsArticle
from trad_auto.core.models.order import Order
from trad_auto.core.models.strategy import TradeProposal


@dataclass(frozen=True)
class BaseEvent:
    """Base event contract for all domain events."""

    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(SYSTEM_TIMEZONE))


@dataclass(frozen=True)
class CommandReceivedEvent(BaseEvent):
    """Emitted when a validated command is ingested."""

    command_id: UUID = field(default_factory=uuid4)
    channel: ChannelType = ChannelType.CLI
    command_type: CommandType = CommandType.GET_STATUS
    sender_id: str = ""


@dataclass(frozen=True)
class CommandExecutedEvent(BaseEvent):
    """Emitted when command processing completes."""

    command_id: UUID = field(default_factory=uuid4)
    command_type: CommandType = CommandType.GET_STATUS
    status: CommandStatus = CommandStatus.EXECUTED
    message: str = ""


@dataclass(frozen=True)
class ConfirmationCreatedEvent(BaseEvent):
    """Emitted when a high-impact action generates a pending confirmation."""

    pending_confirmation_id: UUID = field(default_factory=uuid4)
    command_id: UUID = field(default_factory=uuid4)
    confirmation_code: str = ""
    expires_at: datetime = field(default_factory=lambda: datetime.now(SYSTEM_TIMEZONE))


@dataclass(frozen=True)
class ConfirmationConsumedEvent(BaseEvent):
    """Emitted when a confirmation token is successfully consumed."""

    pending_confirmation_id: UUID = field(default_factory=uuid4)
    consumed_at: datetime = field(default_factory=lambda: datetime.now(SYSTEM_TIMEZONE))


@dataclass(frozen=True)
class TradingSessionStateChangedEvent(BaseEvent):
    """Emitted whenever a TradingSession transitions between states."""

    session_id: UUID = field(default_factory=uuid4)
    old_state: SessionState = SessionState.IDLE
    new_state: SessionState = SessionState.ARMED
    reason: str = ""


@dataclass(frozen=True)
class KillSwitchActivatedEvent(BaseEvent):
    """Emitted when emergency kill switch is engaged."""

    reason: str = ""
    triggered_by: str = ""


@dataclass(frozen=True)
class QuoteUpdatedEvent(BaseEvent):
    """Emitted when top-of-book market quote updates."""

    quote: Quote | None = None


@dataclass(frozen=True)
class TradeReceivedEvent(BaseEvent):
    """Emitted when an executed market trade (tick) is received."""

    trade: Trade | None = None


@dataclass(frozen=True)
class FundingRateEvent(BaseEvent):
    """Emitted when perpetual funding rate updates."""

    funding_rate: FundingRate | None = None


@dataclass(frozen=True)
class BarUpdatedEvent(BaseEvent):
    """Emitted on real-time intrabar price updates."""

    bar: Bar | None = None


@dataclass(frozen=True)
class BarCompletedEvent(BaseEvent):
    """Emitted when a candle bar closes (zero look-ahead bias strategy trigger)."""

    bar: Bar | None = None


@dataclass(frozen=True)
class DataFeedStatusChangedEvent(BaseEvent):
    """Emitted when a market data feed status changes."""

    symbol: str = ""
    old_status: DataFeedStatus = DataFeedStatus.DISCONNECTED
    new_status: DataFeedStatus = DataFeedStatus.CONNECTED
    reason: str = ""


@dataclass(frozen=True)
class DataAnomalyDetectedEvent(BaseEvent):
    """Emitted when data quality validator detects an anomaly."""

    issue: DataQualityIssue | None = None


@dataclass(frozen=True)
class TradeProposalEvent(BaseEvent):
    """Emitted when a strategy generates a valid trade proposal."""

    proposal: TradeProposal | None = None


@dataclass(frozen=True)
class StrategyStatusChangedEvent(BaseEvent):
    """Emitted when a strategy is enabled, disabled, or reset."""

    strategy_id: str = ""
    is_enabled: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TradeIntentCreatedEvent(BaseEvent):
    """Emitted when a proposal is approved by risk gatekeeper and converted to TradeIntent."""

    intent: TradeIntent | None = None


@dataclass(frozen=True)
class TradeProposalRejectedEvent(BaseEvent):
    """Emitted when a TradeProposal is rejected by the risk gatekeeper."""

    proposal_id: UUID = field(default_factory=uuid4)
    strategy_id: str = ""
    symbol: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RiskLimitBreachedEvent(BaseEvent):
    """Emitted when an operational financial limit (loss limit, exposure) is breached."""

    limit_type: str = ""
    current_value: Decimal = ZERO_DECIMAL
    limit_value: Decimal = ZERO_DECIMAL
    action_taken: str = ""


@dataclass(frozen=True)
class DailyProfitTargetReachedEvent(BaseEvent):
    """Emitted when daily profit target is reached, triggering gain protection."""

    realized_profit: Decimal = ZERO_DECIMAL
    target: Decimal = ZERO_DECIMAL


@dataclass(frozen=True)
class PositionOpenedEvent(BaseEvent):
    """Emitted when a new trading position is opened."""

    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    quantity: Decimal = ZERO_DECIMAL
    entry_price: Decimal = ZERO_DECIMAL
    lot_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class PositionUpdatedEvent(BaseEvent):
    """Emitted when an existing position is marked to market or lot added."""

    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    quantity: Decimal = ZERO_DECIMAL
    average_entry_price: Decimal = ZERO_DECIMAL
    mark_price: Decimal = ZERO_DECIMAL
    unrealized_pnl: Decimal = ZERO_DECIMAL


@dataclass(frozen=True)
class PositionClosedEvent(BaseEvent):
    """Emitted when a position is completely closed."""

    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    closed_quantity: Decimal = ZERO_DECIMAL
    exit_price: Decimal = ZERO_DECIMAL
    realized_pnl: Decimal = ZERO_DECIMAL
    total_fees: Decimal = ZERO_DECIMAL


@dataclass(frozen=True)
class BalanceUpdatedEvent(BaseEvent):
    """Emitted when an asset balance changes."""

    asset: str = ""
    free: Decimal = ZERO_DECIMAL
    locked: Decimal = ZERO_DECIMAL
    total: Decimal = ZERO_DECIMAL
    reason: str = ""


@dataclass(frozen=True)
class FundingPaymentEvent(BaseEvent):
    """Emitted when a perpetual contract funding rate payment is processed."""

    symbol: str = ""
    payment_amount: Decimal = ZERO_DECIMAL
    funding_rate: Decimal = ZERO_DECIMAL


@dataclass(frozen=True)
class OrderSubmittedEvent(BaseEvent):
    """Emitted when an order is submitted to the execution adapter."""

    order: Order | None = None


@dataclass(frozen=True)
class OrderFilledEvent(BaseEvent):
    """Emitted when an order execution fill occurs."""

    order_id: UUID = field(default_factory=uuid4)
    client_order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    fill_price: Decimal = ZERO_DECIMAL
    fill_quantity: Decimal = ZERO_DECIMAL
    fee: Decimal = ZERO_DECIMAL
    fee_asset: str = "USDT"
    is_maker: bool = True
    intent_id: UUID | None = None
    order: Order | None = None


@dataclass(frozen=True)
class OrderCanceledEvent(BaseEvent):
    """Emitted when an active order is canceled."""

    order_id: UUID = field(default_factory=uuid4)
    client_order_id: str = ""
    symbol: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OrderRejectedEvent(BaseEvent):
    """Emitted when an order submission is rejected."""

    order_id: UUID = field(default_factory=uuid4)
    client_order_id: str = ""
    symbol: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OrderExpiredEvent(BaseEvent):
    """Emitted when an order expires."""

    order_id: UUID = field(default_factory=uuid4)
    client_order_id: str = ""
    symbol: str = ""


@dataclass(frozen=True)
class NewsArticleReceivedEvent(BaseEvent):
    """Emitted when a new raw news wire report or headline is received."""

    article: NewsArticle | None = None


@dataclass(frozen=True)
class MacroEventScheduledEvent(BaseEvent):
    """Emitted when a high-impact macroeconomic calendar release is registered."""

    event: MacroEconomicEvent | None = None


@dataclass(frozen=True)
class NewsShieldBlackoutEngagedEvent(BaseEvent):
    """Emitted when a news volatility blackout window activates, blocking new entries."""

    title: str = ""
    scheduled_at: datetime | None = None
    blackout_end: datetime | None = None
    affected_symbols: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class NewsShieldBlackoutDisengagedEvent(BaseEvent):
    """Emitted when a news volatility blackout window expires and normal entries resume."""

    title: str = ""
    resumed_at: datetime | None = None
    reason: str = ""


@dataclass(frozen=True)
class QuantModelRetrainedEvent(BaseEvent):
    """Emitted when the auto-retrainer evaluates and hot-swaps the ML model."""

    model_name: str = ""
    sample_count: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    net_return_pct: float = 0.0
    is_hot_swapped: bool = False
