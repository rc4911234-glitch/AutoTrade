"""Unit tests for TradeIntent and RiskCheckResult models."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trad_auto.core.enums import OrderSide, OrderType, RiskDecisionType
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.intent import RiskCheckResult, TradeIntent


def test_trade_intent_valid_and_properties() -> None:
    """Verifies valid TradeIntent construction, notional value, and cash risk amount."""
    now = datetime.now(UTC)
    pid = uuid4()
    intent = TradeIntent(
        proposal_id=pid,
        strategy_id="strat_1",
        symbol="BTCUSDT",
        direction=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        entry_price=Decimal("50000.00"),
        quantity=Decimal("0.5"),
        stop_loss=Decimal("49000.00"),  # $1,000 risk per unit
        take_profit=Decimal("52000.00"),
        timeframe="1h",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    assert intent.proposal_id == pid
    assert intent.notional_value == Decimal("25000.00")  # 50000 * 0.5
    assert intent.risk_amount == Decimal("500.00")  # 1000 * 0.5


def test_trade_intent_expiration_and_invariants() -> None:
    """Verifies that expires_at <= created_at and non-positive prices/quantities raise."""
    now = datetime.now(UTC)
    pid = uuid4()

    # Expiration earlier than creation
    with pytest.raises(DomainValidationError, match="must be strictly later"):
        TradeIntent(
            proposal_id=pid,
            strategy_id="s",
            symbol="BTCUSDT",
            direction=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            entry_price=Decimal("100"),
            quantity=Decimal("1"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("120"),
            timeframe="1h",
            created_at=now,
            expires_at=now - timedelta(seconds=1),
        )

    # Non-positive quantity
    with pytest.raises(DomainValidationError, match="quantity must be strictly positive"):
        TradeIntent(
            proposal_id=pid,
            strategy_id="s",
            symbol="BTCUSDT",
            direction=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            entry_price=Decimal("100"),
            quantity=Decimal("0"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("120"),
            timeframe="1h",
            created_at=now,
            expires_at=now + timedelta(minutes=1),
        )


def test_risk_check_result_lifecycle() -> None:
    """Verifies RiskCheckResult decision properties."""
    pid = uuid4()
    app = RiskCheckResult(
        decision=RiskDecisionType.APPROVED,
        proposal_id=pid,
        reason="Checks passed",
        calculated_quantity=Decimal("1.5"),
        allocated_risk=Decimal("15.0"),
    )
    assert app.is_approved
    assert app.calculated_quantity == Decimal("1.5")

    rej = RiskCheckResult(
        decision=RiskDecisionType.REJECTED,
        proposal_id=pid,
        reason="Risk limit breached",
    )
    assert not rej.is_approved
    assert rej.reason == "Risk limit breached"
