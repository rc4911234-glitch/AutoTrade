"""Local CLI Command Adapter for interactive terminal testing and control."""

import sys
from uuid import uuid4

from trad_auto.command.adapters.base import BaseChannelAdapter
from trad_auto.command.gateway import CommandGateway
from trad_auto.core.enums import ChannelType
from trad_auto.core.models.command import CommandResult, InboundMessage
from trad_auto.core.time import Clock


class CLICommandAdapter(BaseChannelAdapter):
    """Local CLI adapter translating terminal inputs to Gateway commands."""

    def __init__(
        self,
        gateway: CommandGateway,
        clock: Clock,
        sender_id: str = "owner_cli",
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._sender_id = sender_id

    def process_message(self, message: InboundMessage) -> CommandResult:
        """Forwards an InboundMessage directly to the CommandGateway."""
        return self._gateway.handle_inbound_message(message)

    def execute_string(
        self,
        raw_text: str,
        external_message_id: str | None = None,
    ) -> CommandResult:
        """Translates a raw string command into an InboundMessage and dispatches it."""
        msg_id = external_message_id or f"cli_{uuid4().hex[:12]}"
        now = self._clock.now()

        message = InboundMessage(
            external_message_id=msg_id,
            channel=ChannelType.CLI,
            sender_id=self._sender_id,
            raw_text=raw_text,
            received_at=now,
        )
        return self.process_message(message)

    def send_response(self, result: CommandResult) -> None:
        """Prints command output to standard output."""
        print(f"\n[{result.status.value}] {result.message}\n")

    def run_loop(self) -> None:
        """Runs a blocking terminal REPL loop for interactive management."""
        print("==================================================")
        print("  Trad-Auto CLI Control Terminal (Phase 1)       ")
        print("  Commands: status | start paper <amt> | stop     ")
        print("            pause  | resume | kill | reset kill   ")
        print("            pnl today | positions | risk status   ")
        print("  Type 'exit' or 'quit' to close terminal.        ")
        print("==================================================")

        while True:
            try:
                line = input("trad-auto > ").strip()
                if not line:
                    continue
                if line.lower() in ("exit", "quit"):
                    print("Exiting Trad-Auto CLI.")
                    sys.exit(0)

                result = self.execute_string(line)
                self.send_response(result)
            except (KeyboardInterrupt, EOFError):
                print("\nExiting Trad-Auto CLI.")
                break
