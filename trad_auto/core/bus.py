"""In-process typed synchronous event dispatcher."""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

from trad_auto.core.events import BaseEvent

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=BaseEvent)
EventHandler = Callable[[Any], None]


class EventBus:
    """In-process typed event bus with isolated handler execution."""

    def __init__(self) -> None:
        self._handlers: dict[type[BaseEvent], list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Registers a typed callback handler for a specific event class."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], None]) -> bool:
        """Removes a previously registered handler. Returns True if removed."""
        if event_type in self._handlers and handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
            return True
        return False

    def publish(self, event: BaseEvent) -> None:
        """Synchronously dispatches the event to all registered handlers for its type."""
        event_cls = type(event)
        handlers = self._handlers.get(event_cls, [])

        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    "Error executing event handler %s for event %s: %s",
                    handler,
                    event_cls.__name__,
                    e,
                    exc_info=True,
                )

    def clear(self) -> None:
        """Removes all registered event handlers."""
        self._handlers.clear()
