"""Repository for persisting and querying Order lifecycle states."""

import sqlite3
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from trad_auto.core.models.order import Order
from trad_auto.persistence.database import DatabaseManager


class OrderRepository:
    """Provides atomic persistence and querying for Order domain models."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def save_order(self, order: Order) -> None:
        """Inserts or updates an Order record."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, client_order_id, symbol, side, order_type,
                    price, quantity, filled_quantity, status, action_purpose,
                    time_in_force, intent_id, parent_order_id, rejection_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    filled_quantity=excluded.filled_quantity,
                    status=excluded.status,
                    rejection_reason=excluded.rejection_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    str(order.order_id),
                    order.client_order_id,
                    order.symbol,
                    order.side.value,
                    order.order_type.value,
                    str(order.price),
                    str(order.quantity),
                    str(order.filled_quantity),
                    order.status.value,
                    order.action_purpose.value,
                    order.time_in_force.value,
                    str(order.intent_id) if order.intent_id else None,
                    str(order.parent_order_id) if order.parent_order_id else None,
                    order.rejection_reason,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )

    def get_order(self, order_id: UUID) -> Order | None:
        """Retrieves an order by its unique UUID."""
        with self._db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?",
                (str(order_id),),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_order(row)

    def get_active_orders(self, symbol: str | None = None) -> list[Order]:
        """Loads orders in non-terminal states (NEW, SUBMITTED, PARTIALLY_FILLED)."""
        active_statuses = (
            OrderStatus.NEW.value,
            OrderStatus.SUBMITTED.value,
            OrderStatus.PARTIALLY_FILLED.value,
        )
        with self._db.get_connection() as conn:
            if symbol is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM orders
                    WHERE symbol = ? AND status IN (?, ?, ?)
                    ORDER BY created_at ASC
                    """,
                    (symbol, *active_statuses),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM orders
                    WHERE status IN (?, ?, ?)
                    ORDER BY created_at ASC
                    """,
                    active_statuses,
                ).fetchall()

            return [self._row_to_order(r) for r in rows]

    def list_orders(self, symbol: str | None = None, limit: int = 100) -> list[Order]:
        """Lists recent orders up to limit."""
        with self._db.get_connection() as conn:
            if symbol is not None:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        """Converts a SQLite row into an Order domain entity."""
        return Order(
            order_id=UUID(row["order_id"]),
            client_order_id=row["client_order_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            order_type=OrderType(row["order_type"]),
            price=Decimal(row["price"]),
            quantity=Decimal(row["quantity"]),
            filled_quantity=Decimal(row["filled_quantity"]),
            status=OrderStatus(row["status"]),
            action_purpose=OrderActionPurpose(row["action_purpose"]),
            time_in_force=TimeInForce(row["time_in_force"]),
            intent_id=UUID(row["intent_id"]) if row["intent_id"] else None,
            parent_order_id=UUID(row["parent_order_id"]) if row["parent_order_id"] else None,
            rejection_reason=row["rejection_reason"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
