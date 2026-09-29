"""Execution engine and order routing package for Trad-Auto."""

from trad_auto.execution.adapter_base import ExecutionAdapter
from trad_auto.execution.binance import BinanceFuturesExecutionAdapter
from trad_auto.execution.order_manager import OrderManager
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter

__all__ = [
    "ExecutionAdapter",
    "SimulatedExecutionAdapter",
    "BinanceFuturesExecutionAdapter",
    "OrderManager",
]
