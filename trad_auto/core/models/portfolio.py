"""Portfolio, balance, and position lot domain models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide, PositionStatus
from trad_auto.core.exceptions import DomainValidationError


@dataclass
class Balance:
    """Multi-currency asset balance tracking free vs. locked margin."""

    asset: str
    free: Decimal = ZERO_DECIMAL
    locked: Decimal = ZERO_DECIMAL

    def __post_init__(self) -> None:
        if not self.asset:
            raise DomainValidationError("Balance asset cannot be empty")
        if self.free < ZERO_DECIMAL:
            raise DomainValidationError("Balance free amount cannot be negative")
        if self.locked < ZERO_DECIMAL:
            raise DomainValidationError("Balance locked amount cannot be negative")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked

    def lock(self, amount: Decimal) -> None:
        """Locks free balance into locked margin."""
        if amount <= ZERO_DECIMAL:
            raise DomainValidationError("Lock amount must be strictly positive")
        if self.free < amount:
            raise DomainValidationError(
                f"Insufficient free balance to lock: free={self.free}, requested={amount}"
            )
        self.free -= amount
        self.locked += amount

    def unlock(self, amount: Decimal) -> None:
        """Releases locked margin back to free balance."""
        if amount <= ZERO_DECIMAL:
            raise DomainValidationError("Unlock amount must be strictly positive")
        if self.locked < amount:
            raise DomainValidationError(
                f"Insufficient locked balance to unlock: locked={self.locked}, requested={amount}"
            )
        self.locked -= amount
        self.free += amount

    def credit_free(self, amount: Decimal) -> None:
        """Credits funds directly to free balance (e.g. deposit, realized profit)."""
        if amount <= ZERO_DECIMAL:
            raise DomainValidationError("Credit amount must be strictly positive")
        self.free += amount

    def debit_free(self, amount: Decimal) -> None:
        """Debits funds directly from free balance (e.g. withdrawal, fee, loss)."""
        if amount <= ZERO_DECIMAL:
            raise DomainValidationError("Debit amount must be strictly positive")
        if self.free < amount:
            raise DomainValidationError(
                f"Insufficient free balance to debit: free={self.free}, requested={amount}"
            )
        self.free -= amount

    def debit_locked(self, amount: Decimal) -> None:
        """Debits funds from locked margin (e.g. position execution consumption)."""
        if amount <= ZERO_DECIMAL:
            raise DomainValidationError("Debit locked amount must be strictly positive")
        if self.locked < amount:
            raise DomainValidationError(
                f"Insufficient locked balance to debit: locked={self.locked}, requested={amount}"
            )
        self.locked -= amount


@dataclass(frozen=True)
class PositionLot:
    """An immutable record of a single execution fill lot for FIFO accounting."""

    symbol: str
    side: PositionSide
    entry_price: Decimal
    initial_quantity: Decimal
    remaining_quantity: Decimal
    timestamp: datetime
    lot_id: UUID = field(default_factory=uuid4)
    fee_paid: Decimal = ZERO_DECIMAL

    def __post_init__(self) -> None:
        if not self.symbol:
            raise DomainValidationError("PositionLot symbol cannot be empty")
        if self.entry_price <= ZERO_DECIMAL:
            raise DomainValidationError("PositionLot entry_price must be strictly positive")
        if self.initial_quantity <= ZERO_DECIMAL:
            raise DomainValidationError("PositionLot initial_quantity must be strictly positive")
        if self.remaining_quantity < ZERO_DECIMAL:
            raise DomainValidationError("PositionLot remaining_quantity cannot be negative")
        if self.remaining_quantity > self.initial_quantity:
            raise DomainValidationError(
                "PositionLot remaining_quantity cannot exceed initial_quantity"
            )
        if self.fee_paid < ZERO_DECIMAL:
            raise DomainValidationError("PositionLot fee_paid cannot be negative")
        if self.timestamp.tzinfo is None or self.timestamp.tzinfo != UTC:
            raise DomainValidationError("PositionLot timestamp must be timezone-aware (UTC)")

    def with_reduced_quantity(self, closed_quantity: Decimal) -> "PositionLot":
        """Creates a new PositionLot instance with reduced remaining quantity."""
        if closed_quantity <= ZERO_DECIMAL:
            raise DomainValidationError("closed_quantity must be strictly positive")
        if closed_quantity > self.remaining_quantity:
            raise DomainValidationError(
                f"closed_quantity ({closed_quantity}) cannot exceed "
                f"remaining_quantity ({self.remaining_quantity})"
            )
        return PositionLot(
            lot_id=self.lot_id,
            symbol=self.symbol,
            side=self.side,
            entry_price=self.entry_price,
            initial_quantity=self.initial_quantity,
            remaining_quantity=self.remaining_quantity - closed_quantity,
            timestamp=self.timestamp,
            fee_paid=self.fee_paid,
        )


@dataclass
class Position:
    """Aggregated live trading position for a specific symbol."""

    symbol: str
    side: PositionSide
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    lots: list[PositionLot] = field(default_factory=list)
    unrealized_pnl: Decimal = ZERO_DECIMAL
    realized_pnl: Decimal = ZERO_DECIMAL
    status: PositionStatus = PositionStatus.OPEN
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.symbol:
            raise DomainValidationError("Position symbol cannot be empty")
        if self.quantity < ZERO_DECIMAL:
            raise DomainValidationError("Position quantity cannot be negative")
        if self.average_entry_price < ZERO_DECIMAL:
            raise DomainValidationError("Position average_entry_price cannot be negative")
        if self.mark_price < ZERO_DECIMAL:
            raise DomainValidationError("Position mark_price cannot be negative")
        if self.updated_at.tzinfo is None or self.updated_at.tzinfo != UTC:
            raise DomainValidationError("Position updated_at must be timezone-aware (UTC)")

    @property
    def is_open(self) -> bool:
        return self.quantity > ZERO_DECIMAL and self.status == PositionStatus.OPEN

    @property
    def notional_value(self) -> Decimal:
        return self.quantity * self.mark_price

    def update_mark_price(self, new_mark_price: Decimal, timestamp: datetime) -> None:
        """Revalues open position to market (Mark-to-Market) and updates unrealized PnL."""
        if new_mark_price <= ZERO_DECIMAL:
            raise DomainValidationError("new_mark_price must be strictly positive")
        if timestamp.tzinfo is None or timestamp.tzinfo != UTC:
            raise DomainValidationError("timestamp must be timezone-aware (UTC)")
        self.mark_price = new_mark_price
        self.updated_at = timestamp

        if self.side == PositionSide.LONG:
            self.unrealized_pnl = (self.mark_price - self.average_entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.average_entry_price - self.mark_price) * self.quantity


@dataclass(frozen=True)
class LotFillResult:
    """Result of matching a closing trade against FIFO lots."""

    symbol: str
    closed_quantity: Decimal
    realized_pnl: Decimal
    exit_fee_paid: Decimal
    closed_lots: list[PositionLot]
    remaining_position_quantity: Decimal
