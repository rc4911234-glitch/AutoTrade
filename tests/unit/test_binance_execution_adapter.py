"""Unit tests for BinanceFuturesExecutionAdapter."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from trad_auto.core.events import (
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
)
from trad_auto.core.exceptions import ConfigurationError
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.order import Order
from trad_auto.core.time import SimulatedClock
from trad_auto.execution.binance.adapter import BinanceFuturesExecutionAdapter
from trad_auto.execution.binance.client import (
    BinanceFuturesRestClient,
    MockHttpTransport,
)
from trad_auto.execution.binance.signer import BinanceRequestSigner


@pytest.fixture
def test_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        exchange="BINANCE",
        asset_class="CRYPTO_FUTURES",
        currency="USDT",
        tick_size=Decimal("0.10"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        price_precision=2,
    )


@pytest.fixture
def adapter_setup(
    test_instrument: Instrument,
) -> tuple[BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock]:
    signer = BinanceRequestSigner(api_key="key", api_secret="secret")
    transport = MockHttpTransport()
    rest_client = BinanceFuturesRestClient(signer=signer, transport=transport)
    event_bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))

    adapter = BinanceFuturesExecutionAdapter(
        rest_client=rest_client,
        event_bus=event_bus,
        clock=clock,
        instruments={"BTCUSDT": test_instrument},
        is_live_mode=False,
    )
    return adapter, transport, event_bus, clock


def test_safety_double_locks_validation() -> None:
    signer = BinanceRequestSigner(api_key="key", api_secret="secret")
    client = BinanceFuturesRestClient(signer=signer)

    # is_live_mode=True without enable_live_trading=True must raise ConfigurationError
    with pytest.raises(ConfigurationError, match="enable_live_trading must be True"):
        BinanceFuturesExecutionAdapter(
            rest_client=client,
            is_live_mode=True,
            enable_live_trading=False,
        )

    # is_live_mode=True WITH enable_live_trading=True succeeds
    adapter = BinanceFuturesExecutionAdapter(
        rest_client=client,
        is_live_mode=True,
        enable_live_trading=True,
    )
    assert adapter.is_live_mode
    assert adapter.enable_live_trading


def test_submit_market_order_immediate_fill(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, clock = adapter_setup

    submitted_events: list[OrderSubmittedEvent] = []
    filled_events: list[OrderFilledEvent] = []
    event_bus.subscribe(OrderSubmittedEvent, lambda e: submitted_events.append(e))
    event_bus.subscribe(OrderFilledEvent, lambda e: filled_events.append(e))

    transport.queue_response(
        200,
        {
            "orderId": 8888,
            "status": "FILLED",
            "executedQty": "0.500",
            "avgPrice": "50000.00",
        },
    )

    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=Decimal("50000.00"),
        quantity=Decimal("0.500"),
        client_order_id="cid_mkt_1",
    )

    adapter.submit_order(order)

    assert order.status.value == OrderStatus.FILLED.value
    assert order.filled_quantity == Decimal("0.500")
    assert len(submitted_events) == 1
    assert len(filled_events) == 1
    assert filled_events[0].fill_price == Decimal("50000.00")
    assert filled_events[0].fill_quantity == Decimal("0.500")

    # Check payload sent to transport
    req = transport.sent_requests[0]
    assert req["params"]["type"] == "MARKET"
    assert req["params"]["side"] == "BUY"
    assert req["params"]["quantity"] == "0.500"
    assert req["params"]["reduceOnly"] == "false"


def test_submit_limit_order_post_only_and_quantization(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    transport.queue_response(
        200,
        {
            "orderId": 9999,
            "status": "NEW",
        },
    )

    # LIMIT_MAKER (Post-Only) with unquantized values:
    # price 50000.18 -> 50000.20, qty 0.1239 -> 0.123
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("50000.18"),
        quantity=Decimal("0.1239"),
        client_order_id="cid_maker_1",
    )

    adapter.submit_order(order)

    assert order.status.value == OrderStatus.SUBMITTED.value
    req = transport.sent_requests[0]
    assert req["params"]["type"] == "LIMIT"
    assert req["params"]["timeInForce"] == "GTX"  # Binance Post-Only
    assert req["params"]["price"] == "50000.20"
    assert req["params"]["quantity"] == "0.123"


def test_bracket_orders_mandatory_reduce_only(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, _, _ = adapter_setup

    transport.queue_response(200, {"orderId": 1001, "status": "NEW"})
    transport.queue_response(200, {"orderId": 1002, "status": "NEW"})

    # 1. Protective Stop-Loss Order (RISK_REDUCING)
    sl_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        price=Decimal("49000.00"),
        quantity=Decimal("0.100"),
        client_order_id="cid_sl_1",
        action_purpose=OrderActionPurpose.RISK_REDUCING,
    )
    adapter.submit_order(sl_order)

    sl_req = transport.sent_requests[0]
    assert sl_req["params"]["type"] == "STOP_MARKET"
    assert sl_req["params"]["stopPrice"] == "49000.00"
    assert sl_req["params"]["reduceOnly"] == "true"  # Mandatory safety rule

    # 2. Take-Profit Order (EXIT)
    tp_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("52000.00"),
        quantity=Decimal("0.100"),
        client_order_id="cid_tp_1",
        action_purpose=OrderActionPurpose.EXIT,
    )
    adapter.submit_order(tp_order)

    tp_req = transport.sent_requests[1]
    assert tp_req["params"]["type"] == "LIMIT"
    assert tp_req["params"]["timeInForce"] == "GTX"
    assert tp_req["params"]["reduceOnly"] == "true"  # Mandatory safety rule


def test_order_rejected_below_min_quantity(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, _, event_bus, _ = adapter_setup

    rejected_events: list[OrderRejectedEvent] = []
    event_bus.subscribe(OrderRejectedEvent, lambda e: rejected_events.append(e))

    # BTCUSDT min_quantity is 0.001. Quantity 0.0005 will quantize to 0.000
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=Decimal("50000.00"),
        quantity=Decimal("0.0005"),
        client_order_id="cid_tiny_1",
    )

    adapter.submit_order(order)

    assert order.status.value == OrderStatus.REJECTED.value
    assert len(rejected_events) == 1
    assert "below instrument minimum" in rejected_events[0].reason


def test_order_rejected_on_exchange_error(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    rejected_events: list[OrderRejectedEvent] = []
    event_bus.subscribe(OrderRejectedEvent, lambda e: rejected_events.append(e))

    transport.queue_response(400, {"code": -2010, "msg": "Account has insufficient balance"})

    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000.00"),
        quantity=Decimal("1.000"),
        client_order_id="cid_err_1",
    )

    adapter.submit_order(order)

    assert order.status.value == OrderStatus.REJECTED.value
    assert len(rejected_events) == 1
    assert "insufficient balance" in rejected_events[0].reason


def test_cancel_order_lifecycle(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    canceled_events: list[OrderCanceledEvent] = []
    event_bus.subscribe(OrderCanceledEvent, lambda e: canceled_events.append(e))

    transport.queue_response(200, {"orderId": 1111, "status": "NEW"})
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000.00"),
        quantity=Decimal("1.000"),
        client_order_id="cid_cancel_1",
    )
    adapter.submit_order(order)
    assert order.is_active

    # Cancel order
    transport.queue_response(200, {"orderId": 1111, "status": "CANCELED"})
    success = adapter.cancel_order(order.order_id)

    assert success
    assert order.status.value == OrderStatus.CANCELED.value
    assert len(canceled_events) == 1
    assert canceled_events[0].order_id == order.order_id

    # Trying to cancel again returns False
    assert not adapter.cancel_order(order.order_id)
    # Non-existent order returns False
    assert not adapter.cancel_order(uuid4())


def test_cancel_all_orders(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    # Submit 2 active orders
    transport.queue_response(200, {"orderId": 1, "status": "NEW"})
    transport.queue_response(200, {"orderId": 2, "status": "NEW"})

    o1 = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000.00"),
        quantity=Decimal("1.000"),
        client_order_id="c1",
    )
    o2 = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        price=Decimal("51000.00"),
        quantity=Decimal("1.000"),
        client_order_id="c2",
    )
    adapter.submit_order(o1)
    adapter.submit_order(o2)

    assert len(adapter.get_active_orders("BTCUSDT")) == 2

    # Batch cancel all
    transport.queue_response(200, {"code": 200, "msg": "success"})
    count = adapter.cancel_all_orders("BTCUSDT")

    assert count == 2
    assert len(adapter.get_active_orders("BTCUSDT")) == 0
    assert o1.status.value == OrderStatus.CANCELED.value
    assert o2.status.value == OrderStatus.CANCELED.value


def test_sync_order_status_external_fill_and_cancel(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    filled_events: list[OrderFilledEvent] = []
    canceled_events: list[OrderCanceledEvent] = []
    event_bus.subscribe(OrderFilledEvent, lambda e: filled_events.append(e))
    event_bus.subscribe(OrderCanceledEvent, lambda e: canceled_events.append(e))

    transport.queue_response(200, {"orderId": 500, "status": "NEW"})
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000.00"),
        quantity=Decimal("1.000"),
        client_order_id="sync_order_1",
    )
    adapter.submit_order(order)

    # Sync order status when exchange says it was FILLED
    transport.queue_response(
        200,
        {
            "orderId": 500,
            "status": "FILLED",
            "executedQty": "1.000",
            "avgPrice": "49990.00",
        },
    )
    adapter.sync_order_status(order.order_id)

    assert order.status.value == OrderStatus.FILLED.value
    assert len(filled_events) == 1
    assert filled_events[0].fill_price == Decimal("49990.00")

    # Second order sync when exchange says CANCELED
    transport.queue_response(200, {"orderId": 501, "status": "NEW"})
    order2 = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        price=Decimal("51000.00"),
        quantity=Decimal("0.500"),
        client_order_id="sync_order_2",
    )
    adapter.submit_order(order2)

    transport.queue_response(200, {"orderId": 501, "status": "CANCELED"})
    adapter.sync_order_status(order2.order_id)

    assert order2.status.value == OrderStatus.CANCELED.value
    assert len(canceled_events) == 1


def test_binance_execution_adapter_more_branches(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, _ = adapter_setup

    rejected_events: list[OrderRejectedEvent] = []
    event_bus.subscribe(OrderRejectedEvent, lambda e: rejected_events.append(e))

    # 1. LIMIT with IOC time_in_force
    transport.queue_response(200, {"orderId": 7001, "status": "NEW"})
    ioc_limit_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50100.00"),
        quantity=Decimal("1.000"),
        client_order_id="ioc_lim_1",
        time_in_force=TimeInForce.IOC,
    )
    adapter.submit_order(ioc_limit_order)

    req = transport.sent_requests[-1]
    assert req["params"]["type"] == "LIMIT"
    assert req["params"]["timeInForce"] == "IOC"
    assert req["params"]["price"] == "50100.00"

    # 2. LIMIT order with FOK time_in_force
    transport.queue_response(200, {"orderId": 7002, "status": "NEW"})
    fok_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("49500.00"),
        quantity=Decimal("1.000"),
        client_order_id="fok_1",
        time_in_force=TimeInForce.FOK,
    )
    adapter.submit_order(fok_order)
    assert transport.sent_requests[-1]["params"]["timeInForce"] == "FOK"

    # 3. Exchange returns status="REJECTED"
    transport.queue_response(
        200, {"orderId": 7003, "status": "REJECTED", "msg": "Post only reject"}
    )
    rejected_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("49000.00"),
        quantity=Decimal("1.000"),
        client_order_id="rej_1",
    )
    adapter.submit_order(rejected_order)
    assert rejected_order.status.value == OrderStatus.REJECTED.value
    assert len(rejected_events) == 1
    assert "Post only reject" in rejected_events[0].reason

    # 4. get_order retrieval
    assert adapter.get_order(ioc_limit_order.order_id) == ioc_limit_order

    # 5. cancel_all_orders without symbol argument
    transport.queue_response(200, {"code": 200, "msg": "success"})
    count = adapter.cancel_all_orders()
    assert count >= 1

    # 6. Fallback path: submit order for symbol without configured instrument
    transport.queue_response(200, {"orderId": 7004, "status": "NEW"})
    eth_order = Order(
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=Decimal("3000.00"),
        quantity=Decimal("2.5"),
        client_order_id="eth_1",
    )
    adapter.submit_order(eth_order)
    assert eth_order.status.value == OrderStatus.SUBMITTED.value
    assert transport.sent_requests[-1]["params"]["symbol"] == "ETHUSDT"

    # 7. sync_order_status on already inactive order returns immediately
    assert adapter.sync_order_status(rejected_order.order_id) == rejected_order


def test_binance_adapter_register_and_sync_instrument_filters(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, _, _ = adapter_setup

    canned_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            }
        ]
    }
    transport.queue_response(200, canned_info)

    inst = adapter.sync_instrument_filters("BTCUSDT")
    assert inst is not None
    assert inst.symbol == "BTCUSDT"
    assert inst.tick_size == Decimal("0.10")
    assert inst.quantity_step == Decimal("0.001")
    assert adapter._instruments["BTCUSDT"] == inst


def test_binance_adapter_sync_account_balance(
    adapter_setup: tuple[
        BinanceFuturesExecutionAdapter, MockHttpTransport, EventBus, SimulatedClock
    ],
) -> None:
    adapter, transport, event_bus, clock = adapter_setup
    from trad_auto.portfolio.ledger import PositionLedger

    ledger = PositionLedger(event_bus=event_bus, clock=clock)

    canned_account = {
        "assets": [
            {"asset": "USDT", "walletBalance": "15420.50"},
            {"asset": "BNB", "walletBalance": "5.00"},
        ]
    }
    transport.queue_response(200, canned_account)

    bal = adapter.sync_account_balance(ledger)
    assert bal == Decimal("15420.50")
    assert ledger.get_balance("USDT").total == Decimal("15420.50")
