"""Exception hierarchy for Trad-Auto domain operations."""


class TradAutoError(Exception):
    """Base exception for all domain errors within Trad-Auto."""


class ConfigurationError(TradAutoError):
    """Raised when application configuration or safety flags are invalid."""


class PrecisionError(TradAutoError):
    """Raised on invalid decimal, tick size, or quantity quantization."""


class TimeError(TradAutoError):
    """Raised on invalid datetime or clock operations."""


class BackwardClockError(TimeError):
    """Raised when SimulatedClock is instructed to travel backward in time."""


class DomainValidationError(TradAutoError):
    """Raised when a domain model invariant is violated."""


class CommandError(TradAutoError):
    """Base exception for command processing errors."""


class CommandAmbiguousError(CommandError):
    """Raised when an inbound command cannot be unambiguously parsed (fail-closed)."""


class UnauthorizedSenderError(CommandError):
    """Raised when an unauthorized sender attempts to issue privileged commands."""


class DuplicateMessageError(CommandError):
    """Raised when an identical external message is processed concurrently."""


class ConfirmationExpiredError(CommandError):
    """Raised when a confirmation code is expired or already consumed."""


class SessionError(TradAutoError):
    """Base exception for trading session lifecycle errors."""


class InvalidSessionTransitionError(SessionError):
    """Raised when an illegal state transition is attempted on a TradingSession."""


class SessionExpiredError(SessionError):
    """Raised when an action is attempted on an expired TradingSession."""


class SessionCapitalExceededError(SessionError):
    """Raised when requested capital exceeds the session authorized capital ceiling."""


class RiskError(TradAutoError):
    """Base exception for risk gatekeeper violations."""


class RiskBreachError(RiskError):
    """Raised when a proposed trade breaches risk rules."""


class KillSwitchActiveError(RiskError):
    """Raised when trading action is blocked because the Kill Switch is engaged."""


class ExecutionError(TradAutoError):
    """Base exception for order execution and broker adapter errors."""


class ExchangeApiError(ExecutionError):
    """Raised when an exchange REST API returns an error response."""


class RateLimitExceededError(ExecutionError):
    """Raised when exchange rate limit (HTTP 429 / 418) is encountered."""
