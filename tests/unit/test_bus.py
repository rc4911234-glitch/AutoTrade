"""Tests for in-process typed EventBus."""

from uuid import uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import CommandStatus, CommandType
from trad_auto.core.events import BaseEvent, CommandExecutedEvent, KillSwitchActivatedEvent


def test_event_bus_typed_subscription_and_dispatch() -> None:
    """Handlers receive only their subscribed event type."""
    bus = EventBus()
    received_commands: list[CommandExecutedEvent] = []
    received_kills: list[KillSwitchActivatedEvent] = []

    def on_command(e: CommandExecutedEvent) -> None:
        received_commands.append(e)

    def on_kill(e: KillSwitchActivatedEvent) -> None:
        received_kills.append(e)

    bus.subscribe(CommandExecutedEvent, on_command)
    bus.subscribe(KillSwitchActivatedEvent, on_kill)

    cmd_event = CommandExecutedEvent(
        command_id=uuid4(),
        command_type=CommandType.GET_STATUS,
        status=CommandStatus.EXECUTED,
        message="System normal",
    )
    bus.publish(cmd_event)

    assert len(received_commands) == 1
    assert received_commands[0] == cmd_event
    assert len(received_kills) == 0

    kill_event = KillSwitchActivatedEvent(reason="Panic", triggered_by="Owner")
    bus.publish(kill_event)

    assert len(received_commands) == 1
    assert len(received_kills) == 1
    assert received_kills[0] == kill_event


def test_event_bus_unsubscribe() -> None:
    """Unsubscribing stops handler execution."""
    bus = EventBus()
    events: list[BaseEvent] = []

    def handler(e: BaseEvent) -> None:
        events.append(e)

    bus.subscribe(BaseEvent, handler)
    bus.publish(BaseEvent())
    assert len(events) == 1

    assert bus.unsubscribe(BaseEvent, handler) is True
    bus.publish(BaseEvent())
    assert len(events) == 1


def test_event_bus_handler_isolation() -> None:
    """An exception in one handler does not prevent other handlers from executing."""
    bus = EventBus()
    results: list[str] = []

    def failing_handler(e: BaseEvent) -> None:
        raise RuntimeError("Handler failure")

    def successful_handler(e: BaseEvent) -> None:
        results.append("success")

    bus.subscribe(BaseEvent, failing_handler)
    bus.subscribe(BaseEvent, successful_handler)

    bus.publish(BaseEvent())
    assert results == ["success"]
