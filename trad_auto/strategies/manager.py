"""Strategy manager coordinating strategy lifecycles and event bus routing."""

from collections.abc import Callable

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import SessionState
from trad_auto.core.events import (
    BarCompletedEvent,
    QuoteUpdatedEvent,
    StrategyStatusChangedEvent,
    TradeProposalEvent,
)
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.base import BaseStrategy


class StrategyManager:
    """Coordinates strategy evaluation, event subscriptions, and session authorization.

    Enforces the Cardinal Rule:
    If the system is not in SessionState.TRADING, all strategy proposals are suppressed.
    """

    def __init__(
        self,
        event_bus: EventBus,
        bar_store: BarStore,
        session_state_provider: Callable[[], SessionState] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._bar_store = bar_store
        self._session_state_provider = session_state_provider
        self._strategies: dict[str, BaseStrategy] = {}

        # Subscribe to market events
        self._event_bus.subscribe(BarCompletedEvent, self._handle_bar_completed_event)
        self._event_bus.subscribe(QuoteUpdatedEvent, self._handle_quote_updated_event)

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Registers an automated strategy instance."""
        if strategy.strategy_id in self._strategies:
            raise DomainValidationError(f"Strategy '{strategy.strategy_id}' is already registered")
        self._strategies[strategy.strategy_id] = strategy
        self._event_bus.publish(
            StrategyStatusChangedEvent(
                strategy_id=strategy.strategy_id,
                is_enabled=strategy.is_enabled,
                reason="Strategy registered",
            )
        )

    def unregister_strategy(self, strategy_id: str) -> None:
        """Unregisters a strategy instance."""
        strategy = self._strategies.pop(strategy_id, None)
        if strategy:
            self._event_bus.publish(
                StrategyStatusChangedEvent(
                    strategy_id=strategy_id,
                    is_enabled=False,
                    reason="Strategy unregistered",
                )
            )

    def get_strategy(self, strategy_id: str) -> BaseStrategy | None:
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> list[BaseStrategy]:
        return list(self._strategies.values())

    def enable_strategy(self, strategy_id: str) -> None:
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            raise DomainValidationError(f"Strategy '{strategy_id}' not found")
        strategy.enable()
        self._event_bus.publish(
            StrategyStatusChangedEvent(
                strategy_id=strategy_id,
                is_enabled=True,
                reason="Strategy manually enabled",
            )
        )

    def disable_strategy(self, strategy_id: str) -> None:
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            raise DomainValidationError(f"Strategy '{strategy_id}' not found")
        strategy.disable()
        self._event_bus.publish(
            StrategyStatusChangedEvent(
                strategy_id=strategy_id,
                is_enabled=False,
                reason="Strategy manually disabled",
            )
        )

    def is_trading_allowed(self) -> bool:
        """Returns True only if an active session is currently in TRADING state."""
        if self._session_state_provider is None:
            # If no provider is attached (e.g. standalone backtest), allow evaluation
            return True
        return self._session_state_provider() == SessionState.TRADING

    def evaluate_bar(self, bar: Bar) -> list[TradeProposal]:
        """Evaluates all matching registered strategies on a closed Bar.

        Proposals are strictly suppressed if not in TRADING state.
        """
        if not self.is_trading_allowed():
            return []

        proposals: list[TradeProposal] = []
        for strategy in self._strategies.values():
            if strategy.handles(bar.symbol, bar.timeframe):
                strategy_proposals = strategy.on_bar_completed(bar, self._bar_store)
                for p in strategy_proposals:
                    proposals.append(p)
                    self._event_bus.publish(TradeProposalEvent(proposal=p))

        return proposals

    def evaluate_quote(self, quote: Quote) -> list[TradeProposal]:
        """Evaluates matching registered strategies on an incoming Quote."""
        if not self.is_trading_allowed():
            return []

        proposals: list[TradeProposal] = []
        for strategy in self._strategies.values():
            if strategy.is_enabled and quote.symbol in strategy.symbols:
                strategy_proposals = strategy.on_quote(quote)
                for p in strategy_proposals:
                    proposals.append(p)
                    self._event_bus.publish(TradeProposalEvent(proposal=p))

        return proposals

    def _handle_bar_completed_event(self, event: BarCompletedEvent) -> None:
        if event.bar:
            self.evaluate_bar(event.bar)

    def _handle_quote_updated_event(self, event: QuoteUpdatedEvent) -> None:
        if event.quote:
            self.evaluate_quote(event.quote)
