"""Base interface for order execution adapters."""

from abc import ABC, abstractmethod
from uuid import UUID

from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.order import Order


class ExecutionAdapter(ABC):
    """Abstract interface declaring order routing and execution capabilities."""

    @abstractmethod
    def submit_order(self, order: Order) -> None:
        """Submits an order to the execution venue."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: UUID) -> bool:
        """Cancels a specific active order."""
        pass

    @abstractmethod
    def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancels all active orders, optionally scoped to a symbol."""
        pass

    @abstractmethod
    def get_order(self, order_id: UUID) -> Order | None:
        """Retrieves an order by its ID."""
        pass

    @abstractmethod
    def get_active_orders(self, symbol: str | None = None) -> list[Order]:
        """Returns all currently active orders."""
        pass

    @abstractmethod
    def register_instrument(self, instrument: Instrument) -> None:
        """Enrolls instrument trading rules (tick size, lot size) into adapter."""
        pass
