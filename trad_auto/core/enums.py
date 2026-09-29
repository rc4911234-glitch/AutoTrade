"""Frozen domain enums for Trad-Auto."""

from enum import StrEnum


class TradingMode(StrEnum):
    """Operating mode for the trading platform."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"  # Default mode
    LIVE = "LIVE"  # Requires explicit double confirmation


class SessionState(StrEnum):
    """Lifecycle states of a TradingSession."""

    IDLE = "IDLE"  # Startup state; zero new trades
    ARMED = "ARMED"  # Request received; awaiting correlated confirmation code
    TRADING = "TRADING"  # Authorized active trading session
    PAUSED = "PAUSED"  # New entries blocked; protective stops active
    RISK_LOCKED = "RISK_LOCKED"  # Risk breach; protective stops active
    EMERGENCY_STOP = "EMERGENCY_STOP"  # Kill switch engaged; new entries blocked


class OrderSide(StrEnum):
    """Direction of an order or trade."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Supported order execution types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    LIMIT_MAKER = "LIMIT_MAKER"  # Post-only limit order to guarantee maker fees in crypto


class OrderActionPurpose(StrEnum):
    """Distinguishes new speculative entries from risk-reducing/exit orders."""

    NEW_ENTRY = "NEW_ENTRY"
    RISK_REDUCING = "RISK_REDUCING"
    EXIT = "EXIT"


class OrderStatus(StrEnum):
    """Lifecycle states of an order."""

    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(StrEnum):
    """Time in force policy for order execution."""

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"


class PositionSide(StrEnum):
    """Position direction."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(StrEnum):
    """Status of an open or closed position."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


class RiskDecisionType(StrEnum):
    """Outcome of risk gatekeeper evaluation."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CommandType(StrEnum):
    """Structured commands supported by the command gateway."""

    START_TRADING = "START_TRADING"
    CONFIRM = "CONFIRM"
    STOP_TRADING = "STOP_TRADING"
    PAUSE_TRADING = "PAUSE_TRADING"
    RESUME_TRADING = "RESUME_TRADING"
    CLOSE_ALL_POSITIONS = "CLOSE_ALL_POSITIONS"
    ACTIVATE_KILL_SWITCH = "ACTIVATE_KILL_SWITCH"
    RESET_KILL_SWITCH = "RESET_KILL_SWITCH"
    GET_STATUS = "GET_STATUS"
    GET_TODAY_PNL = "GET_TODAY_PNL"
    GET_PORTFOLIO = "GET_PORTFOLIO"
    GET_POSITIONS = "GET_POSITIONS"
    GET_RISK_STATUS = "GET_RISK_STATUS"


class CommandStatus(StrEnum):
    """Outcome status of command execution."""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    COMMAND_AMBIGUOUS = "COMMAND_AMBIGUOUS"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


class ChannelType(StrEnum):
    """Supported inbound communication channels."""

    CLI = "CLI"
    WHATSAPP = "WHATSAPP"


class MessageProcessingState(StrEnum):
    """States for SQLite atomic message deduplication."""

    NEW = "NEW"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BarTimeframe(StrEnum):
    """Standard candle bar timeframes."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class DataFeedStatus(StrEnum):
    """Operational health status of a market data feed."""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"


class DataAnomalyType(StrEnum):
    """Types of data anomalies detected by quality filters."""

    STALE_DATA = "STALE_DATA"
    PRICE_SPIKE = "PRICE_SPIKE"
    INVERTED_QUOTE = "INVERTED_QUOTE"
    TIMESTAMP_DRIFT = "TIMESTAMP_DRIFT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    GAP_DETECTED = "GAP_DETECTED"


class NewsImpactLevel(StrEnum):
    """Expected volatility and market impact tier of an event or news item."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class NewsSentimentType(StrEnum):
    """Directional bias of news sentiment."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class NewsSourceType(StrEnum):
    """Provenance of news or macro calendar updates."""

    CALENDAR = "CALENDAR"
    CRYPTO_PANIC = "CRYPTO_PANIC"
    RSS_FEED = "RSS_FEED"
    CUSTOM = "CUSTOM"
