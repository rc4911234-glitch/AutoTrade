"""Tests for domain models and validation invariants."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import CommandType, SessionState, TradingMode
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.confirmation import PendingConfirmation
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.core.models.session import FinancialLimits, TradingSession


def test_instrument_validation() -> None:
    """Instrument enforces positive ticks, lots, and steps."""
    inst = Instrument(
        symbol="BTC/USDT",
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.10"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )
    assert inst.symbol == "BTC/USDT"
    assert inst.is_tradable is True

    # Violations
    with pytest.raises(DomainValidationError):
        Instrument(
            "BTC",
            "BINANCE",
            "CRYPTO",
            "USDT",
            Decimal("0"),
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
        )
    with pytest.raises(DomainValidationError):
        Instrument(
            "BTC",
            "BINANCE",
            "CRYPTO",
            "USDT",
            Decimal("1"),
            Decimal("-1"),
            Decimal("1"),
            Decimal("1"),
        )


def test_bar_invariants() -> None:
    """Bar validates candle geometry strictly."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    bar = Bar(
        timestamp=now,
        symbol="BTC/USDT",
        timeframe="1m",
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("98"),
        close=Decimal("102"),
        volume=Decimal("10.5"),
    )
    assert bar.high == Decimal("105")

    # High < Low
    with pytest.raises(DomainValidationError, match="cannot be lower than Low"):
        Bar(
            now,
            "BTC",
            "1m",
            Decimal("100"),
            Decimal("95"),
            Decimal("98"),
            Decimal("96"),
            Decimal("1"),
        )

    # Open > High
    with pytest.raises(DomainValidationError, match="must be within"):
        Bar(
            now,
            "BTC",
            "1m",
            Decimal("110"),
            Decimal("105"),
            Decimal("98"),
            Decimal("102"),
            Decimal("1"),
        )

    # Close < Low
    with pytest.raises(DomainValidationError, match="must be within"):
        Bar(
            now,
            "BTC",
            "1m",
            Decimal("100"),
            Decimal("105"),
            Decimal("98"),
            Decimal("90"),
            Decimal("1"),
        )

    # Negative volume
    with pytest.raises(DomainValidationError, match="cannot be negative"):
        Bar(
            now,
            "BTC",
            "1m",
            Decimal("100"),
            Decimal("105"),
            Decimal("98"),
            Decimal("102"),
            Decimal("-1"),
        )

    # Naive timestamp
    with pytest.raises(DomainValidationError, match="must be timezone-aware"):
        Bar(
            datetime(2026, 1, 1),
            "BTC",
            "1m",
            Decimal("100"),
            Decimal("105"),
            Decimal("98"),
            Decimal("102"),
            Decimal("1"),
        )


def test_quote_invariants() -> None:
    """Quote validates prices, sizes, and inverted market detection."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    quote = Quote(
        timestamp=now,
        symbol="BTC/USDT",
        bid_price=Decimal("100.00"),
        ask_price=Decimal("100.05"),
        bid_size=Decimal("1.5"),
        ask_size=Decimal("2.0"),
    )
    assert quote.bid_price == Decimal("100.00")

    # Inverted market (bid > ask)
    with pytest.raises(DomainValidationError, match="Inverted market anomaly"):
        Quote(now, "BTC", Decimal("101.00"), Decimal("100.00"), Decimal("1"), Decimal("1"))

    # Non-positive bid or ask
    with pytest.raises(DomainValidationError):
        Quote(now, "BTC", Decimal("0"), Decimal("100.00"), Decimal("1"), Decimal("1"))
    with pytest.raises(DomainValidationError):
        Quote(now, "BTC", Decimal("100.00"), Decimal("-5"), Decimal("1"), Decimal("1"))


def test_financial_limits_and_session() -> None:
    """FinancialLimits and TradingSession validate constraints."""
    limits = FinancialLimits(
        authorized_capital=Decimal("500.00"),
        max_risk_amount=Decimal("10.00"),
        daily_profit_target=Decimal("25.00"),
    )
    assert limits.authorized_capital == Decimal("500.00")

    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    exp = now + timedelta(hours=8)

    session = TradingSession(
        session_id=uuid4(),
        session_number=1,
        mode=TradingMode.PAPER,
        limits=limits,
        deployed_capital=ZERO_DECIMAL,
        authorized_at=now,
        expires_at=exp,
        status=SessionState.TRADING,
        owner_command_id=uuid4(),
    )
    assert session.is_expired(now) is False
    assert session.is_expired(exp + timedelta(minutes=1)) is True

    # Immutable snapshot transition
    paused = session.transition_to(SessionState.PAUSED)
    assert paused.status == SessionState.PAUSED
    assert session.status == SessionState.TRADING  # Original unchanged


def test_pending_confirmation_lifecycle() -> None:
    """PendingConfirmation lifecycle and single-use consumption."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    conf = PendingConfirmation(
        pending_confirmation_id=uuid4(),
        command_id=uuid4(),
        owner_identity="owner_1",
        command_type=CommandType.START_TRADING,
        command_payload={"capital": "500"},
        confirmation_code="A7K2",
        created_at=now,
        expires_at=now + timedelta(seconds=120),
    )
    assert conf.is_consumed is False
    assert conf.is_expired(now + timedelta(seconds=60)) is False
    assert conf.is_expired(now + timedelta(seconds=130)) is True

    consumed = conf.consume(now + timedelta(seconds=30))
    assert consumed.is_consumed is True
    assert consumed.consumed_at == now + timedelta(seconds=30)
