"""Order domain model and state transition rules."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from trad_auto.core.exceptions import DomainValidationError


@dataclass
class Order:
    """Represents a trading order with strict state machine lifecycle."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Decimal
    quantity: Decimal
    client_order_id: str
    action_purpose: OrderActionPurpose = OrderActionPurpose.NEW_ENTRY
    time_in_force: TimeInForce = TimeInForce.GTC
    order_id: UUID = field(default_factory=uuid4)
    intent_id: UUID | None = None
    parent_order_id: UUID | None = None
    filled_quantity: Decimal = ZERO_DECIMAL
    status: OrderStatus = OrderStatus.NEW
    rejection_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.symbol:
            raise DomainValidationError("Order symbol cannot be empty")
        if not self.client_order_id:
            raise DomainValidationError("Order client_order_id cannot be empty")
        if self.quantity <= ZERO_DECIMAL:
            raise DomainValidationError("Order quantity must be strictly positive")
        if self.order_type != OrderType.MARKET and self.price <= ZERO_DECIMAL:
            raise DomainValidationError("Limit and stop order price must be strictly positive")
        if self.price < ZERO_DECIMAL:
            raise DomainValidationError("Order price cannot be negative")
        if self.filled_quantity < ZERO_DECIMAL:
            raise DomainValidationError("Order filled_quantity cannot be negative")
        if self.filled_quantity > self.quantity:
            raise DomainValidationError("Order filled_quantity cannot exceed total quantity")
        if self.created_at.tzinfo is None or self.created_at.tzinfo != UTC:
            raise DomainValidationError("Order created_at must be timezone-aware (UTC)")
        if self.updated_at.tzinfo is None or self.updated_at.tzinfo != UTC:
            raise DomainValidationError("Order updated_at must be timezone-aware (UTC)")

    @property
    def remaining_quantity(self) -> Decimal:
        """Remaining unfilled order quantity."""
        return self.quantity - self.filled_quantity

    @property
    def is_active(self) -> bool:
        """True if order can still receive fills or cancellations."""
        return self.status in (
            OrderStatus.NEW,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        )

    @property
    def is_terminal(self) -> bool:
        """True if order has reached a final immutable state."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def notional_value(self) -> Decimal:
        """Total gross order notional value."""
        return self.quantity * self.price

    def mark_submitted(self, timestamp: datetime) -> None:
        """Transitions order from NEW to SUBMITTED."""
        if self.status != OrderStatus.NEW:
            raise DomainValidationError(
                f"Cannot submit order with current status {self.status} (expected NEW)"
            )
        self.status = OrderStatus.SUBMITTED
        self.updated_at = timestamp

    def apply_fill(
        self,
        fill_qty: Decimal,
        timestamp: datetime,
    ) -> None:
        """Applies an execution fill, updating filled quantity and lifecycle status."""
        if not self.is_active:
            raise DomainValidationError(
                f"Cannot apply fill to inactive order with status {self.status}"
            )
        if fill_qty <= ZERO_DECIMAL:
            raise DomainValidationError("Fill quantity must be strictly positive")
        if self.filled_quantity + fill_qty > self.quantity:
            raise DomainValidationError(
                f"Fill quantity ({fill_qty}) exceeds remaining order quantity "
                f"({self.remaining_quantity})"
            )

        self.filled_quantity += fill_qty
        self.updated_at = timestamp

        if self.filled_quantity == self.quantity:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED

    def mark_canceled(self, timestamp: datetime) -> None:
        """Cancels an active order."""
        if not self.is_active:
            raise DomainValidationError(f"Cannot cancel inactive order with status {self.status}")
        self.status = OrderStatus.CANCELED
        self.updated_at = timestamp

    def mark_rejected(self, reason: str, timestamp: datetime) -> None:
        """Marks an order as rejected by exchange or adapter."""
        if self.status not in (OrderStatus.NEW, OrderStatus.SUBMITTED):
            raise DomainValidationError(f"Cannot reject order with status {self.status}")
        self.status = OrderStatus.REJECTED
        self.rejection_reason = reason
        self.updated_at = timestamp

    def mark_expired(self, timestamp: datetime) -> None:
        """Marks an order as expired (TTL or time in force elapsed)."""
        if not self.is_active:
            raise DomainValidationError(f"Cannot expire inactive order with status {self.status}")
        self.status = OrderStatus.EXPIRED
        self.updated_at = timestamp
