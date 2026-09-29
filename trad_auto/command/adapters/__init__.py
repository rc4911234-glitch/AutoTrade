"""Channel adapters package."""

from trad_auto.command.adapters.base import BaseChannelAdapter
from trad_auto.command.adapters.cli import CLICommandAdapter

__all__ = ["BaseChannelAdapter", "CLICommandAdapter"]
