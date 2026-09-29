"""Volatility-based position sizer using ATR risk distance and strict step quantization."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.money import quantize_quantity_down


class VolatilityPositionSizer:
    """Calculates risk-adjusted position sizes based on stop-loss distance.

    Guarantees:
    1. Cash risk on stop-out never exceeds the target risk budget.
    2. Gross notional value never exceeds available capital.
    3. Quantity is strictly rounded DOWN to the instrument's quantity_step.
    4. If the sized quantity is below instrument.min_quantity, returns 0.
    """

    @classmethod
    def calculate_quantity(
        cls,
        proposal: TradeProposal,
        instrument: Instrument,
        available_capital: Decimal,
        risk_pct_per_trade: Decimal = Decimal("0.01"),  # 1% default risk
        max_risk_amount: Decimal | None = None,
    ) -> Decimal:
        """Computes safe, quantized position quantity for a TradeProposal."""
        if available_capital <= ZERO_DECIMAL:
            return ZERO_DECIMAL

        if risk_pct_per_trade <= ZERO_DECIMAL or risk_pct_per_trade > Decimal("1.0"):
            raise DomainValidationError("risk_pct_per_trade must be in range (0.0, 1.0]")

        risk_per_unit = proposal.risk_amount_per_unit
        if risk_per_unit <= ZERO_DECIMAL:
            raise DomainValidationError("Proposal risk_amount_per_unit must be strictly positive")

        # 1. Determine cash risk budget
        cash_risk_budget = available_capital * risk_pct_per_trade
        if max_risk_amount is not None and max_risk_amount > ZERO_DECIMAL:
            cash_risk_budget = min(cash_risk_budget, max_risk_amount)

        # 2. Compute raw quantity: Budget / Risk per unit
        raw_quantity = cash_risk_budget / risk_per_unit

        # 3. Notional ceiling check: Quantity * Entry <= Available Capital
        max_notional_quantity = available_capital / proposal.entry_price
        if raw_quantity > max_notional_quantity:
            raw_quantity = max_notional_quantity

        # 4. Strictly quantize DOWN to step size
        quantized = quantize_quantity_down(
            raw_quantity,
            instrument.quantity_step,
            instrument.min_quantity,
        )

        return quantized
