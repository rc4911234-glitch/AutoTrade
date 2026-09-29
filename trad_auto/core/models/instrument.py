"""Instrument domain model."""

from dataclasses import dataclass
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class Instrument:
    """Tradable market instrument contract."""

    symbol: str
    exchange: str
    asset_class: str
    currency: str
    tick_size: Decimal
    lot_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    price_precision: int = 2
    is_tradable: bool = True

    def __post_init__(self) -> None:
        if not self.symbol or not self.exchange or not self.currency:
            raise DomainValidationError(
                "Instrument symbol, exchange, and currency must be non-empty"
            )
        if self.tick_size <= ZERO_DECIMAL:
            raise DomainValidationError("tick_size must be strictly positive")
        if self.lot_size <= ZERO_DECIMAL:
            raise DomainValidationError("lot_size must be strictly positive")
        if self.quantity_step <= ZERO_DECIMAL:
            raise DomainValidationError("quantity_step must be strictly positive")
        if self.min_quantity <= ZERO_DECIMAL:
            raise DomainValidationError("min_quantity must be strictly positive")
        if self.price_precision < 0:
            raise DomainValidationError("price_precision cannot be negative")
