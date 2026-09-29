"""TradingSession and FinancialLimits domain models."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import SessionState, TradingMode
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class FinancialLimits:
    """Four distinct financial limits ensuring capital and risk isolation."""

    authorized_capital: Decimal
    max_risk_amount: Decimal | None = None
    max_exposure: Decimal | None = None
    max_allowed_loss: Decimal | None = None
    daily_profit_target: Decimal | None = None

    def __post_init__(self) -> None:
        if self.authorized_capital <= ZERO_DECIMAL:
            raise DomainValidationError("authorized_capital must be strictly positive")
        if self.max_risk_amount is not None and self.max_risk_amount <= ZERO_DECIMAL:
            raise DomainValidationError("max_risk_amount must be strictly positive")
        if self.max_exposure is not None and self.max_exposure <= ZERO_DECIMAL:
            raise DomainValidationError("max_exposure must be strictly positive")
        if self.max_allowed_loss is not None and self.max_allowed_loss <= ZERO_DECIMAL:
            raise DomainValidationError("max_allowed_loss must be strictly positive")
        if self.daily_profit_target is not None and self.daily_profit_target <= ZERO_DECIMAL:
            raise DomainValidationError("daily_profit_target must be strictly positive")


@dataclass(frozen=True)
class TradingSession:
    """Immutable snapshot of an authorized trading session."""

    session_id: UUID
    session_number: int
    mode: TradingMode
    limits: FinancialLimits
    deployed_capital: Decimal
    authorized_at: datetime
    expires_at: datetime
    status: SessionState
    owner_command_id: UUID

    def __post_init__(self) -> None:
        if self.authorized_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise DomainValidationError("Session timestamps must be timezone-aware (UTC)")
        if self.expires_at <= self.authorized_at:
            raise DomainValidationError("Session expires_at must be strictly after authorized_at")
        if self.deployed_capital < ZERO_DECIMAL:
            raise DomainValidationError("deployed_capital cannot be negative")

    def transition_to(self, new_state: SessionState) -> "TradingSession":
        """Returns a new validated immutable snapshot with the updated SessionState."""
        return replace(self, status=new_state)

    def is_expired(self, current_time: datetime) -> bool:
        """Checks whether the session has passed its expiration time."""
        return current_time >= self.expires_at
