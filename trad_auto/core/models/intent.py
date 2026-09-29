"""Trade intent and risk evaluation outcome data contracts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide, OrderType, RiskDecisionType
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class TradeIntent:
    """Approved, sized, and risk-checked order execution intent.

    Produced exclusively by the RiskGatekeeper after successfully passing all
    session capital ceilings, exposure boundaries, and volatility sizing rules.
    """

    proposal_id: UUID
    strategy_id: str
    symbol: str
    direction: OrderSide
    order_type: OrderType
    entry_price: Decimal
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    timeframe: str
    created_at: datetime  # Timezone-aware UTC
    expires_at: datetime  # Timezone-aware UTC
    intent_id: UUID = field(default_factory=uuid4)
    max_slippage_pct: Decimal = Decimal("0.005")  # 0.5% default maximum slippage

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.tzinfo != UTC:
            raise DomainValidationError("TradeIntent created_at must be timezone-aware (UTC)")
        if self.expires_at.tzinfo is None or self.expires_at.tzinfo != UTC:
            raise DomainValidationError("TradeIntent expires_at must be timezone-aware (UTC)")
        if self.expires_at <= self.created_at:
            raise DomainValidationError(
                "TradeIntent expires_at must be strictly later than created_at"
            )

        if not self.strategy_id or not self.symbol or not self.timeframe:
            raise DomainValidationError(
                "TradeIntent strategy_id, symbol, and timeframe must be non-empty"
            )

        if self.entry_price <= ZERO_DECIMAL:
            raise DomainValidationError("TradeIntent entry_price must be strictly positive")
        if self.quantity <= ZERO_DECIMAL:
            raise DomainValidationError("TradeIntent quantity must be strictly positive")
        if self.stop_loss <= ZERO_DECIMAL:
            raise DomainValidationError("TradeIntent stop_loss must be strictly positive")
        if self.take_profit <= ZERO_DECIMAL:
            raise DomainValidationError("TradeIntent take_profit must be strictly positive")
        if self.max_slippage_pct < ZERO_DECIMAL:
            raise DomainValidationError("TradeIntent max_slippage_pct cannot be negative")

    @property
    def notional_value(self) -> Decimal:
        """Gross cash value of the proposed order (Price * Quantity)."""
        return self.entry_price * self.quantity

    @property
    def risk_amount(self) -> Decimal:
        """Total dollar loss if stopped out (Per-unit risk * Quantity)."""
        unit_risk = abs(self.entry_price - self.stop_loss)
        return unit_risk * self.quantity


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of risk gatekeeper evaluation for a TradeProposal."""

    decision: RiskDecisionType
    proposal_id: UUID
    reason: str
    calculated_quantity: Decimal | None = None
    allocated_risk: Decimal | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None or self.checked_at.tzinfo != UTC:
            raise DomainValidationError("RiskCheckResult checked_at must be timezone-aware (UTC)")
        if not self.reason:
            raise DomainValidationError("RiskCheckResult reason must be non-empty")

    @property
    def is_approved(self) -> bool:
        return self.decision == RiskDecisionType.APPROVED
