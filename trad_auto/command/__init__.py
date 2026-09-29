"""Command processing, deduplication, and session orchestration package."""

from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.command.gateway import CommandGateway
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.command.validator import CommandValidator

__all__ = [
    "MessageDeduplicator",
    "ConfirmationManager",
    "TradingSessionManager",
    "CommandValidator",
    "CommandGateway",
]
