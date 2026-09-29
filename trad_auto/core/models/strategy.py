"""Strategy domain models and trade proposal data contracts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide
from trad_auto.core.exceptions import DomainValidationError

MINIMUM_RISK_REWARD_RATIO = Decimal("2.0")


@dataclass(frozen=True)
class TradeProposal:
    """Immutable trade proposal emitted by a strategy.

    Enforces pro-trader mathematical invariants:
    1. Mandatory protective stop-loss and take-profit targets.
    2. Exact geometric price alignment (Stop < Entry < Target for Long,
       Target < Entry < Stop for Short).
    3. Mandatory minimum Risk-to-Reward Ratio (default >= 1:2.0).
    """

    strategy_id: str
    symbol: str
    timeframe: str
    direction: OrderSide
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    timestamp: datetime  # Timezone-aware UTC
    reason: str
    proposal_id: UUID = field(default_factory=uuid4)
    min_rrr: Decimal = field(default=MINIMUM_RISK_REWARD_RATIO)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.tzinfo != UTC:
            raise DomainValidationError("TradeProposal timestamp must be timezone-aware (UTC)")
        if not self.strategy_id or not self.symbol or not self.timeframe or not self.reason:
            raise DomainValidationError(
                "Strategy id, symbol, timeframe, and reason must be non-empty"
            )

        if self.entry_price <= ZERO_DECIMAL:
            raise DomainValidationError("TradeProposal entry_price must be strictly positive")
        if self.stop_loss <= ZERO_DECIMAL:
            raise DomainValidationError("TradeProposal stop_loss must be strictly positive")
        if self.take_profit <= ZERO_DECIMAL:
            raise DomainValidationError("TradeProposal take_profit must be strictly positive")

        if self.direction == OrderSide.BUY:
            # Long Proposal: Stop < Entry < Target
            if self.stop_loss >= self.entry_price:
                raise DomainValidationError(
                    f"Invalid Long proposal: stop_loss ({self.stop_loss}) must be "
                    f"strictly lower than entry_price ({self.entry_price})"
                )
            if self.take_profit <= self.entry_price:
                raise DomainValidationError(
                    f"Invalid Long proposal: take_profit ({self.take_profit}) must be "
                    f"strictly higher than entry_price ({self.entry_price})"
                )
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        elif self.direction == OrderSide.SELL:
            # Short Proposal: Target < Entry < Stop
            if self.stop_loss <= self.entry_price:
                raise DomainValidationError(
                    f"Invalid Short proposal: stop_loss ({self.stop_loss}) must be "
                    f"strictly higher than entry_price ({self.entry_price})"
                )
            if self.take_profit >= self.entry_price:
                raise DomainValidationError(
                    f"Invalid Short proposal: take_profit ({self.take_profit}) must be "
                    f"strictly lower than entry_price ({self.entry_price})"
                )
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit
        else:
            raise DomainValidationError(f"Unsupported proposal direction: {self.direction}")

        if risk <= ZERO_DECIMAL:
            raise DomainValidationError("Calculated trade risk must be strictly positive")

        rrr = reward / risk
        # Allow tiny decimal tolerance for rounding (e.g. 1.999 vs 2.0)
        if rrr < (self.min_rrr - Decimal("0.001")):
            raise DomainValidationError(
                f"Trade proposal violates minimum Risk-to-Reward ratio: "
                f"calculated {rrr:.2f} is below minimum {self.min_rrr:.2f}"
            )

    @property
    def risk_amount_per_unit(self) -> Decimal:
        """Absolute dollar distance from entry to stop loss per unit."""
        if self.direction == OrderSide.BUY:
            return self.entry_price - self.stop_loss
        return self.stop_loss - self.entry_price

    @property
    def reward_amount_per_unit(self) -> Decimal:
        """Absolute dollar distance from entry to take profit per unit."""
        if self.direction == OrderSide.BUY:
            return self.take_profit - self.entry_price
        return self.entry_price - self.take_profit

    @property
    def risk_reward_ratio(self) -> Decimal:
        """Calculated mathematical Risk-to-Reward ratio."""
        return self.reward_amount_per_unit / self.risk_amount_per_unit
