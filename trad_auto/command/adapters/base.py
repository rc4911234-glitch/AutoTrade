"""Abstract base channel adapter."""

from abc import ABC, abstractmethod

from trad_auto.core.models.command import CommandResult, InboundMessage


class BaseChannelAdapter(ABC):
    """Abstract interface for all I/O channel adapters (CLI, WhatsApp)."""

    @abstractmethod
    def send_response(self, result: CommandResult) -> None:
        """Transmits the command outcome back to the originating channel."""
        pass

    @abstractmethod
    def process_message(self, message: InboundMessage) -> CommandResult:
        """Dispatches an inbound channel message to the CommandGateway."""
        pass
