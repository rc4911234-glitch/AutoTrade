"""Unit and property-based tests for decimal money and quantity quantization."""

from decimal import ROUND_DOWN, ROUND_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import PrecisionError
from trad_auto.core.money import quantize_money, quantize_quantity_down, quantize_to_tick


def test_quantize_money_basic() -> None:
    """Standard monetary quantization rounds half-up to 2 decimals."""
    assert quantize_money(Decimal("100.004")) == Decimal("100.00")
    assert quantize_money(Decimal("100.005")) == Decimal("100.01")
    assert quantize_money(Decimal("100.009")) == Decimal("100.01")
    assert quantize_money(Decimal("50.1234"), Decimal("0.001")) == Decimal("50.123")


def test_quantize_money_invalid_inputs() -> None:
    """quantize_money raises PrecisionError on non-Decimal or non-positive quantum."""
    with pytest.raises(PrecisionError):
        quantize_money(100.0)  # type: ignore[arg-type]

    with pytest.raises(PrecisionError):
        quantize_money(Decimal("100"), Decimal("0"))

    with pytest.raises(PrecisionError):
        quantize_money(Decimal("100"), Decimal("-0.01"))


def test_quantize_to_tick_basic() -> None:
    """Price rounds exactly to tick_size multiples."""
    tick = Decimal("0.05")
    assert quantize_to_tick(Decimal("100.02"), tick) == Decimal("100.00")
    assert quantize_to_tick(Decimal("100.03"), tick) == Decimal("100.05")
    assert quantize_to_tick(Decimal("100.07"), tick) == Decimal("100.05")
    assert quantize_to_tick(Decimal("100.08"), tick) == Decimal("100.10")


def test_quantize_to_tick_explicit_rounding() -> None:
    """Explicit rounding directions (e.g. ROUND_DOWN, ROUND_UP) are respected."""
    tick = Decimal("0.10")
    assert quantize_to_tick(Decimal("100.09"), tick, rounding_mode=ROUND_DOWN) == Decimal("100.00")
    assert quantize_to_tick(Decimal("100.01"), tick, rounding_mode=ROUND_UP) == Decimal("100.10")


def test_quantize_to_tick_invalid() -> None:
    """quantize_to_tick raises on non-positive tick size."""
    with pytest.raises(PrecisionError):
        quantize_to_tick(Decimal("100"), Decimal("0"))


def test_quantize_quantity_down_basic() -> None:
    """Order sizing rounds DOWN to quantity_step increments."""
    step = Decimal("0.001")
    min_qty = Decimal("0.005")

    assert quantize_quantity_down(Decimal("0.0078"), step, min_qty) == Decimal("0.007")
    assert quantize_quantity_down(Decimal("0.0049"), step, min_qty) == ZERO_DECIMAL
    assert quantize_quantity_down(Decimal("0.0050"), step, min_qty) == Decimal("0.005")


def test_quantize_quantity_down_invalid() -> None:
    """quantize_quantity_down raises on non-positive steps or min_quantities."""
    with pytest.raises(PrecisionError):
        quantize_quantity_down(Decimal("1"), Decimal("0"), Decimal("1"))
    with pytest.raises(PrecisionError):
        quantize_quantity_down(Decimal("1"), Decimal("1"), Decimal("0"))


@given(
    val=st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=4),
    tick=st.sampled_from(
        [Decimal("0.01"), Decimal("0.05"), Decimal("0.10"), Decimal("0.25"), Decimal("0.50")]
    ),
)
def test_hypothesis_tick_quantization_invariant(val: Decimal, tick: Decimal) -> None:
    """Property Invariant: Quantized price modulo tick_size must always be zero."""
    quantized = quantize_to_tick(val, tick)
    remainder = (quantized / tick) % Decimal("1")
    assert remainder == ZERO_DECIMAL


@given(
    qty=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000"), places=4),
    step=st.sampled_from([Decimal("0.01"), Decimal("0.05"), Decimal("0.1"), Decimal("1.0")]),
    min_qty=st.sampled_from([Decimal("0.01"), Decimal("0.1"), Decimal("1.0")]),
)
def test_hypothesis_quantity_step_invariant(qty: Decimal, step: Decimal, min_qty: Decimal) -> None:
    """Property Invariant: Quantized quantity is never greater than input quantity
    and is an exact multiple of quantity_step.
    """
    quantized = quantize_quantity_down(qty, step, min_qty)
    assert quantized <= qty
    if quantized > ZERO_DECIMAL:
        remainder = (quantized / step) % Decimal("1")
        assert remainder == ZERO_DECIMAL
        assert quantized >= min_qty
