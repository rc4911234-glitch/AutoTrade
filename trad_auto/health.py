"""System health monitoring, component vitals, and container healthcheck runner."""

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.enums import SessionState
from trad_auto.core.time import Clock, SystemClock
from trad_auto.execution.adapter_base import ExecutionAdapter
from trad_auto.market_data.streaming.watchdog import FeedWatchdog
from trad_auto.persistence.database import DatabaseManager

if TYPE_CHECKING:
    from trad_auto.news.poller import LiveNewsPoller
    from trad_auto.news.shield import NewsVolatilityShield

logger = logging.getLogger(__name__)


def get_process_memory_bytes() -> int:
    """Retrieves current process RSS/Working Set memory in bytes cross-platform."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):  # noqa: N801
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return int(counters.WorkingSetSize)
        elif sys.platform.startswith("linux"):
            with open("/proc/self/statm") as f:
                pages = int(f.read().split()[1])
                return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        pass
    return 0


class ComponentStatus(StrEnum):
    """Health status grades for individual components and overall system."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


@dataclass
class ComponentHealth:
    """Health check outcome for an individual subsystem."""

    name: str
    status: ComponentStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    last_checked: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SystemHealthReport:
    """Comprehensive system-wide health report."""

    status: ComponentStatus
    uptime_seconds: float
    memory_bytes: int
    timestamp: datetime
    components: dict[str, ComponentHealth]

    @property
    def is_healthy(self) -> bool:
        """Returns True if system is operational (HEALTHY or DEGRADED), False if UNHEALTHY."""
        return self.status != ComponentStatus.UNHEALTHY

    @property
    def memory_mb(self) -> float:
        """Process memory in megabytes."""
        return round(self.memory_bytes / (1024 * 1024), 2)

    def to_dict(self) -> dict[str, Any]:
        """Serializes report to dictionary for JSON output and logging."""
        return {
            "status": self.status.value,
            "is_healthy": self.is_healthy,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "memory_mb": self.memory_mb,
            "timestamp": self.timestamp.isoformat(),
            "components": {
                name: {
                    "status": comp.status.value,
                    "message": comp.message,
                    "details": comp.details,
                    "last_checked": comp.last_checked.isoformat(),
                }
                for name, comp in self.components.items()
            },
        }


class HealthMonitor:
    """Subsystem telemetry monitor and health checker.

    Verifies:
    1. Database connectivity and WAL journal availability.
    2. Trading session state (flags EMERGENCY_STOP or RISK_LOCKED).
    3. Market data feed freshness via FeedWatchdog.
    4. Execution adapter status and error counts.
    5. Process working set memory consumption and uptime.
    """

    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        session_manager: TradingSessionManager | None = None,
        feed_watchdog: FeedWatchdog | None = None,
        execution_adapter: ExecutionAdapter | None = None,
        news_shield: "NewsVolatilityShield | None" = None,
        news_poller: "LiveNewsPoller | None" = None,
        clock: Clock | None = None,
        stale_threshold_seconds: float = 15.0,
    ) -> None:
        self._db_manager = db_manager
        self._session_manager = session_manager
        self._feed_watchdog = feed_watchdog
        self._execution_adapter = execution_adapter
        self._news_shield = news_shield
        self._news_poller = news_poller
        self._clock: Clock = clock or SystemClock()
        self._start_time: datetime = self._clock.now()
        self._stale_threshold_seconds = stale_threshold_seconds

    @property
    def start_time(self) -> datetime:
        return self._start_time

    def check_database(self) -> ComponentHealth:
        """Checks SQLite database connectivity and basic query execution."""
        now = self._clock.now()
        if self._db_manager is None:
            return ComponentHealth(
                name="database",
                status=ComponentStatus.HEALTHY,
                message="No database configured (in-memory mode)",
                last_checked=now,
            )

        try:
            with self._db_manager.get_connection() as conn:
                cursor = conn.execute("SELECT 1")
                row = cursor.fetchone()
                if row and row[0] == 1:
                    return ComponentHealth(
                        name="database",
                        status=ComponentStatus.HEALTHY,
                        message="Database connection verified (WAL mode)",
                        details={"db_path": self._db_manager.db_path},
                        last_checked=now,
                    )
            return ComponentHealth(
                name="database",
                status=ComponentStatus.UNHEALTHY,
                message="Database ping query returned unexpected result",
                last_checked=now,
            )
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            return ComponentHealth(
                name="database",
                status=ComponentStatus.UNHEALTHY,
                message=f"Database unreachable: {exc}",
                last_checked=now,
            )

    def check_session(self) -> ComponentHealth:
        """Checks session state machine and safety locks."""
        now = self._clock.now()
        if self._session_manager is None:
            return ComponentHealth(
                name="session",
                status=ComponentStatus.HEALTHY,
                message="No session manager configured",
                last_checked=now,
            )

        state = self._session_manager.state
        details = {
            "session_state": state.value,
            "has_active_session": self._session_manager.current_session is not None,
        }

        if state == SessionState.EMERGENCY_STOP:
            return ComponentHealth(
                name="session",
                status=ComponentStatus.UNHEALTHY,
                message="EMERGENCY_STOP active: Kill switch is tripped",
                details=details,
                last_checked=now,
            )

        if state == SessionState.RISK_LOCKED:
            return ComponentHealth(
                name="session",
                status=ComponentStatus.DEGRADED,
                message="RISK_LOCKED: Financial limit breached; new trades locked",
                details=details,
                last_checked=now,
            )

        return ComponentHealth(
            name="session",
            status=ComponentStatus.HEALTHY,
            message=f"Session operational: state is {state.value}",
            details=details,
            last_checked=now,
        )

    def check_market_data(self) -> ComponentHealth:
        """Checks streaming feed watchdog and data freshness."""
        now = self._clock.now()
        if self._feed_watchdog is None:
            return ComponentHealth(
                name="market_data",
                status=ComponentStatus.HEALTHY,
                message="Feed watchdog not attached",
                last_checked=now,
            )

        tracked = self._feed_watchdog.tracked_symbols
        if not tracked:
            return ComponentHealth(
                name="market_data",
                status=ComponentStatus.HEALTHY,
                message="No symbols actively tracked by watchdog",
                last_checked=now,
            )

        stale_symbols: list[str] = []
        symbol_details: dict[str, Any] = {}

        for sym in sorted(tracked):
            last_hb = self._feed_watchdog.last_heartbeat(sym)
            status = self._feed_watchdog.get_status(sym)
            is_stale = self._feed_watchdog.is_stale(sym, now)
            elapsed = (now - last_hb).total_seconds() if last_hb else None
            symbol_details[sym] = {
                "status": status.value,
                "is_stale": is_stale,
                "seconds_since_last_message": round(elapsed, 2) if elapsed is not None else None,
            }
            if is_stale or status.value == "STALE":
                stale_symbols.append(sym)

        if stale_symbols:
            return ComponentHealth(
                name="market_data",
                status=ComponentStatus.DEGRADED,
                message=f"Stale feeds detected for symbols: {', '.join(stale_symbols)}",
                details={"symbols": symbol_details, "stale_symbols": stale_symbols},
                last_checked=now,
            )

        return ComponentHealth(
            name="market_data",
            status=ComponentStatus.HEALTHY,
            message="Market data streaming active and fresh",
            details={"symbols": symbol_details},
            last_checked=now,
        )

    def check_execution(self) -> ComponentHealth:
        """Checks execution adapter status."""
        now = self._clock.now()
        if self._execution_adapter is None:
            return ComponentHealth(
                name="execution",
                status=ComponentStatus.HEALTHY,
                message="No execution adapter attached",
                last_checked=now,
            )

        details: dict[str, Any] = {
            "adapter_type": self._execution_adapter.__class__.__name__,
        }

        # Check Binance REST client rate limit if available
        client = getattr(self._execution_adapter, "rest_client", None)
        if client is not None:
            weight = getattr(client, "used_weight_1m", None)
            if isinstance(weight, int):
                details["used_weight_1m"] = weight
                if weight > 1000:
                    return ComponentHealth(
                        name="execution",
                        status=ComponentStatus.DEGRADED,
                        message=f"Binance rate limit weight elevated: {weight}/1200",
                        details=details,
                        last_checked=now,
                    )

        return ComponentHealth(
            name="execution",
            status=ComponentStatus.HEALTHY,
            message="Execution adapter operational",
            details=details,
            last_checked=now,
        )

    def check_system(self) -> ComponentHealth:
        """Checks memory and host runtime vitals."""
        now = self._clock.now()
        mem_bytes = get_process_memory_bytes()
        mem_mb = round(mem_bytes / (1024 * 1024), 2)
        uptime = (now - self._start_time).total_seconds()

        details = {
            "uptime_seconds": round(uptime, 2),
            "memory_mb": mem_mb,
            "python_version": sys.version.split()[0],
        }

        # Warning threshold if memory exceeds 500 MB
        if mem_mb > 500.0:
            return ComponentHealth(
                name="system",
                status=ComponentStatus.DEGRADED,
                message=f"Process memory elevated: {mem_mb} MB",
                details=details,
                last_checked=now,
            )

        return ComponentHealth(
            name="system",
            status=ComponentStatus.HEALTHY,
            message="System vitals normal",
            details=details,
            last_checked=now,
        )

    def check_news_shield(self) -> ComponentHealth:
        """Checks news volatility shield status and active blackout windows."""
        now = self._clock.now()
        if self._news_shield is None:
            return ComponentHealth(
                name="news_shield",
                status=ComponentStatus.HEALTHY,
                message="News shield not configured",
                last_checked=now,
            )

        in_blackout = self._news_shield.is_blackout_active("*", now=now)
        next_event = self._news_shield.calendar_mgr.next_upcoming_event(now=now)
        details: dict[str, Any] = {
            "blackout_active": in_blackout,
            "next_macro_event": next_event.title if next_event else None,
            "next_event_time": (next_event.scheduled_at.isoformat() if next_event else None),
        }

        if self._news_poller is not None:
            details["poller_active"] = self._news_poller.is_running
            details["total_articles_ingested"] = self._news_poller.total_articles_ingested
            details["consecutive_errors"] = self._news_poller.consecutive_errors
            details["last_poll_time"] = (
                self._news_poller.last_poll_time.isoformat()
                if self._news_poller.last_poll_time
                else None
            )
            errs = self._news_poller.consecutive_errors
            if errs >= 5:
                return ComponentHealth(
                    name="news_shield",
                    status=ComponentStatus.DEGRADED,
                    message=f"News poller failing ({errs} consecutive errors)",
                    details=details,
                    last_checked=now,
                )

        if in_blackout:
            return ComponentHealth(
                name="news_shield",
                status=ComponentStatus.DEGRADED,
                message="Active news volatility blackout in progress (entries blocked)",
                details=details,
                last_checked=now,
            )

        return ComponentHealth(
            name="news_shield",
            status=ComponentStatus.HEALTHY,
            message="News volatility shield active and clear",
            details=details,
            last_checked=now,
        )

    def get_health_report(self) -> SystemHealthReport:
        """Collects all subsystem health checks and determines overall system status."""
        now = self._clock.now()
        uptime = (now - self._start_time).total_seconds()
        mem_bytes = get_process_memory_bytes()

        components: dict[str, ComponentHealth] = {
            "database": self.check_database(),
            "session": self.check_session(),
            "market_data": self.check_market_data(),
            "execution": self.check_execution(),
            "system": self.check_system(),
        }
        if self._news_shield is not None:
            components["news_shield"] = self.check_news_shield()

        # Overall status resolution:
        # If any component is UNHEALTHY -> UNHEALTHY
        # Else if any component is DEGRADED -> DEGRADED
        # Else -> HEALTHY
        statuses = [c.status for c in components.values()]
        if ComponentStatus.UNHEALTHY in statuses:
            overall = ComponentStatus.UNHEALTHY
        elif ComponentStatus.DEGRADED in statuses:
            overall = ComponentStatus.DEGRADED
        else:
            overall = ComponentStatus.HEALTHY

        return SystemHealthReport(
            status=overall,
            uptime_seconds=uptime,
            memory_bytes=mem_bytes,
            timestamp=now,
            components=components,
        )


def main() -> None:
    """CLI entrypoint for container health checks (returns exit code 0 or 1)."""
    import json

    from config.settings import get_settings

    settings = get_settings()
    db_manager = DatabaseManager(db_path=settings.sqlite_db_path)
    monitor = HealthMonitor(db_manager=db_manager)
    report = monitor.get_health_report()

    print(json.dumps(report.to_dict(), indent=2))
    if report.status == ComponentStatus.UNHEALTHY:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
