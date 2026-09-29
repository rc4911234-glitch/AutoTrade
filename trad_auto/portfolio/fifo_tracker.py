"""First-In, First-Out (FIFO) position lot tracker."""

from collections import deque
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.portfolio import LotFillResult, PositionLot


class FifoPositionTracker:
    """Tracks active position inventory lots in First-In, First-Out (FIFO) sequence.

    When an exit or reduction order executes, lots are consumed in chronological order,
    yielding exact tax lot accounting, cost basis calculations, and realized P&L.
    """

    def __init__(self, symbol: str, side: PositionSide) -> None:
        if not symbol:
            raise DomainValidationError("Symbol cannot be empty for FifoPositionTracker")
        self._symbol = symbol
        self._side = side
        self._lots: deque[PositionLot] = deque()

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def side(self) -> PositionSide:
        return self._side

    @property
    def is_empty(self) -> bool:
        return len(self._lots) == 0

    @property
    def total_quantity(self) -> Decimal:
        """Returns the aggregate remaining quantity across all open lots."""
        return sum((lot.remaining_quantity for lot in self._lots), ZERO_DECIMAL)

    @property
    def open_lots(self) -> tuple[PositionLot, ...]:
        """Returns a snapshot tuple of currently open lots."""
        return tuple(self._lots)

    @property
    def average_entry_price(self) -> Decimal:
        """Computes volume-weighted average entry price (VWAP) across open lots."""
        total_qty = self.total_quantity
        if total_qty <= ZERO_DECIMAL:
            return ZERO_DECIMAL

        total_cost = sum(
            (lot.remaining_quantity * lot.entry_price for lot in self._lots),
            ZERO_DECIMAL,
        )
        return total_cost / total_qty

    def add_lot(self, lot: PositionLot) -> None:
        """Appends a newly filled entry lot to the FIFO inventory queue."""
        if lot.symbol != self._symbol:
            raise DomainValidationError(
                f"Cannot add lot with symbol {lot.symbol} to tracker for {self._symbol}"
            )
        if lot.side != self._side:
            raise DomainValidationError(
                f"Cannot add lot with side {lot.side} to tracker with side {self._side}"
            )
        if lot.remaining_quantity <= ZERO_DECIMAL:
            raise DomainValidationError("Cannot add lot with non-positive remaining quantity")

        self._lots.append(lot)

    def close_lots(
        self,
        quantity: Decimal,
        exit_price: Decimal,
        exit_fee: Decimal = ZERO_DECIMAL,
    ) -> LotFillResult:
        """Consumes open lots in FIFO order to fill a position reduction or exit.

        Args:
            quantity: The quantity to close (must be > 0 and <= total_quantity).
            exit_price: The executed fill price of the closing order.
            exit_fee: Trading fee paid on the closing fill.

        Returns:
            LotFillResult detailing closed lots and realized P&L.
        """
        if quantity <= ZERO_DECIMAL:
            raise DomainValidationError("Close quantity must be strictly positive")
        if exit_price <= ZERO_DECIMAL:
            raise DomainValidationError("Exit price must be strictly positive")
        if exit_fee < ZERO_DECIMAL:
            raise DomainValidationError("Exit fee cannot be negative")

        total_available = self.total_quantity
        if quantity > total_available:
            raise DomainValidationError(
                f"Requested close quantity ({quantity}) exceeds "
                f"open position ({total_available}) for {self._symbol}"
            )

        remaining_to_close = quantity
        gross_realized_pnl = ZERO_DECIMAL
        consumed_lots: list[PositionLot] = []

        while remaining_to_close > ZERO_DECIMAL and self._lots:
            current_lot = self._lots.popleft()

            if current_lot.remaining_quantity <= remaining_to_close:
                # Lot is fully consumed
                lot_closed_qty = current_lot.remaining_quantity
                remaining_to_close -= lot_closed_qty

                # Calculate lot PnL
                if self._side == PositionSide.LONG:
                    lot_pnl = lot_closed_qty * (exit_price - current_lot.entry_price)
                else:
                    lot_pnl = lot_closed_qty * (current_lot.entry_price - exit_price)

                gross_realized_pnl += lot_pnl
                consumed_lots.append(current_lot)
            else:
                # Lot is partially consumed
                lot_closed_qty = remaining_to_close
                updated_lot = current_lot.with_reduced_quantity(lot_closed_qty)

                # Push updated lot back to the front of the queue
                self._lots.appendleft(updated_lot)
                remaining_to_close = ZERO_DECIMAL

                # Calculate partial lot PnL
                if self._side == PositionSide.LONG:
                    lot_pnl = lot_closed_qty * (exit_price - current_lot.entry_price)
                else:
                    lot_pnl = lot_closed_qty * (current_lot.entry_price - exit_price)

                gross_realized_pnl += lot_pnl
                consumed_lots.append(
                    PositionLot(
                        lot_id=current_lot.lot_id,
                        symbol=current_lot.symbol,
                        side=current_lot.side,
                        entry_price=current_lot.entry_price,
                        initial_quantity=lot_closed_qty,
                        remaining_quantity=ZERO_DECIMAL,
                        timestamp=current_lot.timestamp,
                        fee_paid=current_lot.fee_paid,
                    )
                )

        net_realized_pnl = gross_realized_pnl - exit_fee
        new_total_qty = self.total_quantity

        return LotFillResult(
            symbol=self._symbol,
            closed_quantity=quantity,
            realized_pnl=net_realized_pnl,
            exit_fee_paid=exit_fee,
            closed_lots=consumed_lots,
            remaining_position_quantity=new_total_qty,
        )
