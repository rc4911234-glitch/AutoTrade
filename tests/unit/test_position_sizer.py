"""Unit and property-based tests for VolatilityPositionSizer."""

from datetime import UTC, datetime
from decimal import Decimal

import hypothesis.strategies as st
import pytest
from hypothesis import given

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.risk.sizer import VolatilityPositionSizer


def make_instrument(
    symbol: str = "BTCUSDT",
    tick_size: Decimal = Decimal("0.1"),
    lot_size: Decimal = Decimal("0.001"),
    quantity_step: Decimal = Decimal("0.001"),
    min_quantity: Decimal = Decimal("0.001"),
) -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=tick_size,
        lot_size=lot_size,
        quantity_step=quantity_step,
        min_quantity=min_quantity,
    )


def make_proposal(
    entry: str,
    stop: str,
    target: str,
    direction: OrderSide = OrderSide.BUY,
    symbol: str = "BTCUSDT",
) -> TradeProposal:
    return TradeProposal(
        strategy_id="test_sizer",
        symbol=symbol,
        timeframe="1h",
        direction=direction,
        entry_price=Decimal(entry),
        stop_loss=Decimal(stop),
        take_profit=Decimal(target),
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        reason="test sizing",
    )


def test_position_sizer_exact_risk_scaling() -> None:
    """Verifies that smaller stop distance results in larger size while maintaining cash risk."""
    inst = make_instrument(quantity_step=Decimal("0.001"), min_quantity=Decimal("0.001"))
    capital = Decimal("1000.00")  # $1,000 capital
    # 1% risk budget = $10.00 cash risk

    # Trade A: Wide Stop: Entry 100, Stop 90 (Risk per unit = $10)
    # Expected Qty: 10 / 10 = 1.0 unit. Loss on stop: 1.0 * 10 = $10.00
    prop_a = make_proposal("100", "90", "120")
    qty_a = VolatilityPositionSizer.calculate_quantity(
        prop_a, inst, capital, risk_pct_per_trade=Decimal("0.01")
    )
    assert qty_a == Decimal("1.000")
    assert qty_a * prop_a.risk_amount_per_unit == Decimal("10.00")

    # Trade B: Tight Stop: Entry 100, Stop 95 (Risk per unit = $5)
    # Expected Qty: 10 / 5 = 2.0 units. Loss on stop: 2.0 * 5 = $10.00
    prop_b = make_proposal("100", "95", "110")
    qty_b = VolatilityPositionSizer.calculate_quantity(
        prop_b, inst, capital, risk_pct_per_trade=Decimal("0.01")
    )
    assert qty_b == Decimal("2.000")
    assert qty_b * prop_b.risk_amount_per_unit == Decimal("10.00")


def test_position_sizer_quantization_down_rounding() -> None:
    """Verifies that position quantities are strictly rounded DOWN to instrument step size."""
    # Step size is 0.1
    inst = make_instrument(quantity_step=Decimal("0.1"), min_quantity=Decimal("0.1"))
    capital = Decimal("1000.00")

    # Entry 100, Stop 87 (Risk per unit = 13). Budget = 10. Raw Qty = 10 / 13 = 0.7692...
    # Must round down to 0.7 (NOT 0.8)
    prop = make_proposal("100", "87", "130")
    qty = VolatilityPositionSizer.calculate_quantity(
        prop, inst, capital, risk_pct_per_trade=Decimal("0.01")
    )
    assert qty == Decimal("0.7")
    # Loss on stop: 0.7 * 13 = 9.1 <= 10 budget
    assert qty * prop.risk_amount_per_unit <= Decimal("10.00")


def test_position_sizer_insufficient_capital_for_min_quantity() -> None:
    """Verifies that if sized quantity is below min_quantity, returns 0."""
    # min_quantity is 5.0
    inst = make_instrument(quantity_step=Decimal("1.0"), min_quantity=Decimal("5.0"))
    capital = Decimal("100.00")  # 1% risk = $1.00 budget

    # Entry 100, Stop 90 (Risk per unit = 10). Raw qty = 1 / 10 = 0.1 < min_quantity 5.0
    prop = make_proposal("100", "90", "120")
    qty = VolatilityPositionSizer.calculate_quantity(
        prop, inst, capital, risk_pct_per_trade=Decimal("0.01")
    )
    assert qty == ZERO_DECIMAL


def test_position_sizer_notional_capital_ceiling() -> None:
    """Verifies that total order notional never exceeds available capital."""
    inst = make_instrument(quantity_step=Decimal("0.01"), min_quantity=Decimal("0.01"))
    # Very tight stop on small capital: Capital = 100, Entry = 10, Stop = 9.99 (Risk = 0.01)
    # Risk budget = 10. Raw qty by risk = 10 / 0.01 = 1,000 units.
    # But 1,000 units * 10 = $10,000 > $100 available capital!
    # Sizer must cap at 100 / 10 = 10.0 units.
    capital = Decimal("100.00")
    prop = make_proposal("10.00", "9.99", "10.03")
    qty = VolatilityPositionSizer.calculate_quantity(
        prop, inst, capital, risk_pct_per_trade=Decimal("0.10")
    )
    assert qty == Decimal("10.00")
    assert qty * prop.entry_price <= capital


def test_position_sizer_invalid_parameters() -> None:
    """Verifies error handling for invalid risk percentages."""
    inst = make_instrument()
    prop = make_proposal("100", "90", "120")

    with pytest.raises(DomainValidationError, match="risk_pct_per_trade"):
        VolatilityPositionSizer.calculate_quantity(
            prop, inst, Decimal("1000"), risk_pct_per_trade=Decimal("0")
        )

    with pytest.raises(DomainValidationError, match="risk_pct_per_trade"):
        VolatilityPositionSizer.calculate_quantity(
            prop, inst, Decimal("1000"), risk_pct_per_trade=Decimal("1.5")
        )


@given(
    capital=st.decimals(min_value=Decimal("10"), max_value=Decimal("10000"), places=2),
    entry=st.decimals(min_value=Decimal("10"), max_value=Decimal("1000"), places=2),
    risk_pct=st.decimals(min_value=Decimal("0.005"), max_value=Decimal("0.05"), places=3),
)
def test_hypothesis_position_sizer_invariants(
    capital: Decimal, entry: Decimal, risk_pct: Decimal
) -> None:
    """Hypothesis invariant: Sized notional value never exceeds available capital."""
    inst = make_instrument(quantity_step=Decimal("0.001"), min_quantity=Decimal("0.001"))
    stop = entry * Decimal("0.95")  # 5% stop
    target = entry * Decimal("1.10")  # 10% target (1:2 R:R)

    prop = TradeProposal(
        strategy_id="hypo",
        symbol="BTCUSDT",
        timeframe="1h",
        direction=OrderSide.BUY,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        reason="hypo test",
    )

    qty = VolatilityPositionSizer.calculate_quantity(
        prop, inst, capital, risk_pct_per_trade=risk_pct
    )
    notional = qty * entry
    # Notional must never exceed capital
    assert notional <= capital
    # Cash risk must never exceed budget (capital * risk_pct)
    cash_risk = qty * prop.risk_amount_per_unit
    assert cash_risk <= (capital * risk_pct) + Decimal("0.01")
