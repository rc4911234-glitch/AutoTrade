"""Tests for frozen domain enums."""

from trad_auto.core.enums import (
    ChannelType,
    CommandStatus,
    CommandType,
    MessageProcessingState,
    OrderActionPurpose,
    OrderSide,
    OrderType,
    PositionSide,
    PositionStatus,
    RiskDecisionType,
    SessionState,
    TradingMode,
)


def test_enums_string_compatibility() -> None:
    """All domain enums must inherit from str for clean serialization."""
    assert isinstance(TradingMode.BACKTEST, str)
    assert TradingMode.BACKTEST == "BACKTEST"
    assert TradingMode.PAPER == "PAPER"
    assert TradingMode.LIVE == "LIVE"

    assert SessionState.IDLE == "IDLE"
    assert SessionState.ARMED == "ARMED"
    assert SessionState.TRADING == "TRADING"
    assert SessionState.PAUSED == "PAUSED"
    assert SessionState.RISK_LOCKED == "RISK_LOCKED"
    assert SessionState.EMERGENCY_STOP == "EMERGENCY_STOP"

    assert OrderActionPurpose.NEW_ENTRY == "NEW_ENTRY"
    assert OrderActionPurpose.RISK_REDUCING == "RISK_REDUCING"
    assert OrderActionPurpose.EXIT == "EXIT"

    assert OrderSide.BUY == "BUY"
    assert OrderSide.SELL == "SELL"
    assert OrderType.MARKET == "MARKET"
    assert OrderType.LIMIT_MAKER == "LIMIT_MAKER"

    assert PositionSide.LONG == "LONG"
    assert PositionStatus.OPEN == "OPEN"
    assert RiskDecisionType.APPROVED == "APPROVED"
    assert CommandType.START_TRADING == "START_TRADING"
    assert CommandStatus.PENDING_CONFIRMATION == "PENDING_CONFIRMATION"
    assert ChannelType.CLI == "CLI"
    assert MessageProcessingState.PROCESSING == "PROCESSING"
