"""Volatility-based position sizer using ATR risk distance, Kelly Criterion, and step quantization."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.money import quantize_quantity_down


class VolatilityPositionSizer:
    """Calculates risk-adjusted position sizes based on stop-loss distance and Kelly Criterion.

    Guarantees:
    1. Kelly Criterion Dynamic Sizing: When proposal confidence (ML win probability) is supplied,
       calculates Half-Kelly risk budget to optimize long-term geometric compounding.
    2. Negative Expectancy Veto: If Kelly formula determines edge <= 0, size returns 0.
    3. Cash risk on stop-out never exceeds the target or maximum risk budget.
    4. Gross notional value never exceeds available capital.
    5. Quantity is strictly rounded DOWN to the instrument's quantity_step.
    6. If the sized quantity is below instrument.min_quantity, returns 0.
    """

    @classmethod
    def calculate_kelly_risk_pct(
        cls,
        win_probability: Decimal,
        risk_reward_ratio: Decimal,
        base_risk_pct: Decimal = Decimal("0.01"),
        fractional_multiplier: Decimal = Decimal("0.5"),  # Half-Kelly for risk mitigation
        max_risk_cap: Decimal = Decimal("0.025"),  # Hard 2.5% max risk cap
        min_risk_floor: Decimal = Decimal("0.005"),  # 0.5% minimum risk floor
    ) -> Decimal:
        """Calculates optimal risk-budget percentage using Fractional Kelly Criterion.

        Formula: f* = (p * (b + 1) - 1) / b
        where:
          p = win probability (e.g. 0.65 from ML model)
          b = payoff ratio (risk_reward_ratio, e.g. 2.0)
        """
        p = win_probability
        b = risk_reward_ratio
        if b <= ZERO_DECIMAL or p <= ZERO_DECIMAL:
            return ZERO_DECIMAL

        # Kelly fraction: (p * (b + 1) - 1) / b
        numerator = p * (b + Decimal("1.0")) - Decimal("1.0")
        if numerator <= ZERO_DECIMAL:
            # Negative expectancy: mathematical trade veto
            return ZERO_DECIMAL

        full_kelly = numerator / b
        fractional_kelly = full_kelly * fractional_multiplier

        # Scale base risk by fractional Kelly
        dynamic_risk = base_risk_pct * (Decimal("1.0") + fractional_kelly)

        # Enforce institutional boundaries [min_risk_floor, max_risk_cap]
        clamped_risk = max(min_risk_floor, min(dynamic_risk, max_risk_cap))
        return clamped_risk

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

        # 1. Determine cash risk budget (Kelly-adjusted if confidence is provided)
        effective_risk_pct = risk_pct_per_trade
        if proposal.confidence is not None and proposal.confidence > ZERO_DECIMAL:
            effective_risk_pct = cls.calculate_kelly_risk_pct(
                win_probability=proposal.confidence,
                risk_reward_ratio=proposal.risk_reward_ratio,
                base_risk_pct=risk_pct_per_trade,
            )
            if effective_risk_pct <= ZERO_DECIMAL:
                # Mathematical veto by Kelly Criterion
                return ZERO_DECIMAL

        cash_risk_budget = available_capital * effective_risk_pct
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
