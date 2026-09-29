"""Strict Decimal monetary and quantity quantization utilities."""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from trad_auto.core.constants import DEFAULT_CURRENCY_QUANTUM, ZERO_DECIMAL
from trad_auto.core.exceptions import PrecisionError


def quantize_money(
    amount: Decimal,
    quantum: Decimal = DEFAULT_CURRENCY_QUANTUM,
) -> Decimal:
    """Quantizes monetary amount to a currency-specific quantum without float precision loss."""
    if not isinstance(amount, Decimal) or not isinstance(quantum, Decimal):
        raise PrecisionError("amount and quantum must be decimal.Decimal instances")
    if quantum <= ZERO_DECIMAL:
        raise PrecisionError("quantum must be strictly positive")
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)


def quantize_to_tick(
    value: Decimal,
    tick_size: Decimal,
    rounding_mode: str = ROUND_HALF_UP,
) -> Decimal:
    """Quantizes price to an exact multiple of tick_size with explicit rounding direction."""
    if not isinstance(value, Decimal) or not isinstance(tick_size, Decimal):
        raise PrecisionError("value and tick_size must be decimal.Decimal instances")
    if tick_size <= ZERO_DECIMAL:
        raise PrecisionError("tick_size must be strictly positive")
    return (value / tick_size).quantize(Decimal("1"), rounding=rounding_mode) * tick_size


def quantize_quantity_down(
    quantity: Decimal,
    quantity_step: Decimal,
    min_quantity: Decimal,
) -> Decimal:
    """Quantizes order quantity strictly DOWN to quantity_step to avoid exposure expansion.

    If quantity is below min_quantity, returns Decimal('0').
    """
    if not isinstance(quantity, Decimal) or not isinstance(quantity_step, Decimal):
        raise PrecisionError("quantity and quantity_step must be decimal.Decimal instances")
    if not isinstance(min_quantity, Decimal):
        raise PrecisionError("min_quantity must be a decimal.Decimal instance")
    if quantity_step <= ZERO_DECIMAL:
        raise PrecisionError("quantity_step must be strictly positive")
    if min_quantity <= ZERO_DECIMAL:
        raise PrecisionError("min_quantity must be strictly positive")

    if quantity < min_quantity:
        return ZERO_DECIMAL

    units = (quantity // quantity_step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return units * quantity_step
