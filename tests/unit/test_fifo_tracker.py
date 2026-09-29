"""Unit and property-based tests for FifoPositionTracker."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.portfolio import PositionLot
from trad_auto.portfolio.fifo_tracker import FifoPositionTracker


def make_lot(
    price: str,
    qty: str,
    side: PositionSide = PositionSide.LONG,
    fee: str = "0.0",
) -> PositionLot:
    return PositionLot(
        symbol="BTCUSDT",
        side=side,
        entry_price=Decimal(price),
        initial_quantity=Decimal(qty),
        remaining_quantity=Decimal(qty),
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        fee_paid=Decimal(fee),
    )


def test_fifo_tracker_initial_state() -> None:
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    assert tracker.symbol == "BTCUSDT"
    assert tracker.side == PositionSide.LONG
    assert tracker.is_empty
    assert tracker.total_quantity == ZERO_DECIMAL
    assert tracker.average_entry_price == ZERO_DECIMAL


def test_fifo_tracker_add_single_lot() -> None:
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    lot = make_lot("100.00", "1.5")
    tracker.add_lot(lot)

    assert not tracker.is_empty
    assert tracker.total_quantity == Decimal("1.5")
    assert tracker.average_entry_price == Decimal("100.00")
    assert len(tracker.open_lots) == 1


def test_fifo_tracker_vwap_scaling_in() -> None:
    """Verifies volume-weighted average price calculation across multiple lots."""
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    # Lot 1: 1.0 @ 100 = 100
    # Lot 2: 2.0 @ 110 = 220
    # Lot 3: 1.0 @ 120 = 120
    # Total Cost = 440, Total Qty = 4.0, VWAP = 110.00
    tracker.add_lot(make_lot("100.00", "1.0"))
    tracker.add_lot(make_lot("110.00", "2.0"))
    tracker.add_lot(make_lot("120.00", "1.0"))

    assert tracker.total_quantity == Decimal("4.0")
    assert tracker.average_entry_price == Decimal("110.00")


def test_fifo_tracker_partial_close_first_lot() -> None:
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    tracker.add_lot(make_lot("100.00", "1.0"))
    tracker.add_lot(make_lot("120.00", "1.0"))

    # Close 0.4 units at exit price 150 with $1 fee
    # Consumes 0.4 from Lot 1 (entry 100)
    # Gross PnL: 0.4 * (150 - 100) = 20. Net PnL: 20 - 1 = 19
    res = tracker.close_lots(
        quantity=Decimal("0.4"),
        exit_price=Decimal("150.00"),
        exit_fee=Decimal("1.00"),
    )

    assert res.closed_quantity == Decimal("0.4")
    assert res.realized_pnl == Decimal("19.00")
    assert tracker.total_quantity == Decimal("1.6")
    # Remaining: Lot 1 has 0.6 @ 100, Lot 2 has 1.0 @ 120
    # Total cost = 60 + 120 = 180. VWAP = 180 / 1.6 = 112.50
    assert tracker.average_entry_price == Decimal("112.50")


def test_fifo_tracker_multi_lot_fifo_consumption() -> None:
    """Verifies that oldest lots are strictly consumed before newer lots."""
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    tracker.add_lot(make_lot("100.00", "1.0"))  # Lot 1
    tracker.add_lot(make_lot("200.00", "1.0"))  # Lot 2

    # Close 1.5 units at exit price 250
    # Must completely consume Lot 1 (1.0 @ 100) -> PnL: 1.0 * (250 - 100) = 150
    # And partially consume Lot 2 (0.5 @ 200) -> PnL: 0.5 * (250 - 200) = 25
    # Total gross PnL = 175.00
    res = tracker.close_lots(quantity=Decimal("1.5"), exit_price=Decimal("250.00"))

    assert res.closed_quantity == Decimal("1.5")
    assert res.realized_pnl == Decimal("175.00")
    assert tracker.total_quantity == Decimal("0.5")
    # Remaining: 0.5 of Lot 2 @ 200
    assert tracker.average_entry_price == Decimal("200.00")


def test_fifo_tracker_short_position_pnl() -> None:
    """Verifies short position realized PnL: (entry - exit) * qty - fee."""
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.SHORT)
    tracker.add_lot(make_lot("100.00", "2.0", side=PositionSide.SHORT))

    # Exit short at lower price 80 (profit) with $2 fee
    # Gross PnL: 2.0 * (100 - 80) = 40. Net PnL: 40 - 2 = 38
    res = tracker.close_lots(
        quantity=Decimal("2.0"),
        exit_price=Decimal("80.00"),
        exit_fee=Decimal("2.00"),
    )

    assert res.closed_quantity == Decimal("2.0")
    assert res.realized_pnl == Decimal("38.00")
    assert tracker.is_empty
    assert tracker.total_quantity == ZERO_DECIMAL


def test_fifo_tracker_overclose_raises_error() -> None:
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    tracker.add_lot(make_lot("100.00", "1.0"))

    with pytest.raises(DomainValidationError, match="exceeds open position"):
        tracker.close_lots(quantity=Decimal("1.0001"), exit_price=Decimal("100.00"))


def test_fifo_tracker_mismatched_symbol_or_side() -> None:
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    eth_lot = PositionLot(
        symbol="ETHUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("2000.00"),
        initial_quantity=Decimal("1.0"),
        remaining_quantity=Decimal("1.0"),
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError, match="Cannot add lot with symbol"):
        tracker.add_lot(eth_lot)

    short_lot = make_lot("100.00", "1.0", side=PositionSide.SHORT)
    with pytest.raises(DomainValidationError, match="Cannot add lot with side"):
        tracker.add_lot(short_lot)


@given(
    lot_prices=st.lists(
        st.decimals(min_value=Decimal("10.0"), max_value=Decimal("1000.0"), places=2),
        min_size=1,
        max_size=5,
    ),
    lot_qtys=st.lists(
        st.decimals(min_value=Decimal("0.1"), max_value=Decimal("10.0"), places=2),
        min_size=1,
        max_size=5,
    ),
)
def test_hypothesis_fifo_conservation_invariant(
    lot_prices: list[Decimal], lot_qtys: list[Decimal]
) -> None:
    """Property test verifying conservation of total quantity across arbitrary entries and exits."""
    tracker = FifoPositionTracker(symbol="BTCUSDT", side=PositionSide.LONG)
    n = min(len(lot_prices), len(lot_qtys))
    total_added = ZERO_DECIMAL

    for i in range(n):
        qty = lot_qtys[i]
        price = lot_prices[i]
        tracker.add_lot(make_lot(str(price), str(qty)))
        total_added += qty

    assert tracker.total_quantity == total_added

    # Close half
    close_amount = (total_added / Decimal("2")).quantize(Decimal("0.001"))
    if close_amount > ZERO_DECIMAL and close_amount <= tracker.total_quantity:
        res = tracker.close_lots(quantity=close_amount, exit_price=Decimal("100.00"))
        # Conservation: closed + remaining must equal initial total
        assert res.closed_quantity + tracker.total_quantity == total_added
