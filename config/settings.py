"""Trad-Auto settings loader and environment validation."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration with strict security and safety guardrails."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Operating Mode
    trading_mode: Literal["BACKTEST", "PAPER", "LIVE"] = Field(
        default="BACKTEST",
        description="Operating mode. Defaults to safest mode: BACKTEST.",
    )

    # Live Mode Safeguards
    enable_live_trading: bool = Field(
        default=False,
        description="Must be explicitly set to True for LIVE mode.",
    )
    confirm_real_money_trading: str = Field(
        default="",
        description="Must match 'I_UNDERSTAND_AND_ACCEPT_CAPITAL_RISK' for LIVE mode.",
    )

    # Owner Authentication & Twilio / WhatsApp
    owner_cli_enabled: bool = Field(
        default=True,
        description="Enables local CLI control for the authorized owner.",
    )
    owner_whatsapp_number: str = Field(
        default="",
        description="Authorized owner E.164 phone number (e.g. +919876543210).",
    )
    whatsapp_app_secret: str = Field(
        default="",
        description="Secret for validating WhatsApp webhook HMAC SHA-256 signatures.",
    )
    twilio_account_sid: str = Field(
        default="",
        description="Twilio Account SID.",
    )
    twilio_auth_token: str = Field(
        default="",
        description="Twilio Auth Token for webhook signature validation.",
    )
    twilio_whatsapp_number: str = Field(
        default="",
        description="Twilio WhatsApp sender number (e.g. whatsapp:+14155238886).",
    )

    # Webhook Server (for inbound WhatsApp messages via Twilio)
    webhook_host: str = Field(
        default="0.0.0.0",
        description="Bind address for the inbound webhook server.",
    )
    webhook_port: int = Field(
        default=5000,
        ge=1024,
        le=65535,
        description="Port for the inbound webhook server.",
    )
    enable_web_dashboard: bool = Field(
        default=True,
        description="Enable local HTTP web dashboard server on webhook_port.",
    )

    # Session Defaults
    confirmation_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Time-to-live in seconds for high-impact confirmation codes.",
    )
    default_session_duration_hours: int = Field(
        default=8,
        ge=1,
        le=24,
        description="Default trading session expiry duration.",
    )
    max_authorized_capital_per_session: Decimal = Field(
        default=Decimal("10000.00"),
        gt=Decimal("0"),
        description="Hard safety ceiling for authorized capital in any single session.",
    )

    # Dynamic Trailing Stop & Breakeven (Freqtrade-inspired)
    enable_trailing_stop: bool = Field(
        default=True,
        description="Enable dynamic trailing stop and breakeven ratchet on open positions.",
    )
    trailing_breakeven_offset_pct: Decimal = Field(
        default=Decimal("0.0075"),
        description="Profit threshold (e.g. 0.0075 = +0.75%) to move stop-loss to breakeven.",
    )
    trailing_breakeven_fee_buffer: Decimal = Field(
        default=Decimal("0.0008"),
        description="Fee buffer (e.g. 0.0008 = +0.08%) to cover exchange fees on breakeven.",
    )
    trailing_stop_offset_pct: Decimal = Field(
        default=Decimal("0.012"),
        description="Profit threshold (e.g. 0.012 = +1.20%) to activate dynamic trailing stop.",
    )
    trailing_stop_delta_pct: Decimal = Field(
        default=Decimal("0.005"),
        description="Trailing stop distance (e.g. 0.005 = 0.50%) behind peak price.",
    )
    trailing_min_ratchet_pct: Decimal = Field(
        default=Decimal("0.0005"),
        description="Min percentage improvement (e.g. 0.05%) to ratchet stop order.",
    )

    # Execution Protections & Adverse Selection Guards
    entry_timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Auto-cancellation timeout in seconds for unfilled limit entries.",
    )
    enable_orphan_watchdog: bool = Field(
        default=True,
        description="Enforces atomic position protection; flattens positions if unshielded.",
    )
    feed_watchdog_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description="Heartbeat staleness timeout in seconds for exchange market data streams.",
    )

    # Privacy & Auditing
    store_raw_chat_text: bool = Field(
        default=False,
        description="If False, discards raw message text after parsing to protect privacy.",
    )

    # Persistence & File Storage
    sqlite_db_path: str = Field(
        default="trad_auto.db",
        description="Path to SQLite database file for deduplication and audit storage.",
    )
    sqlite_busy_timeout_ms: int = Field(
        default=5000,
        ge=1000,
        description="SQLite busy timeout in milliseconds for concurrent transactions.",
    )
    data_dir: str = Field(
        default="data",
        description="Directory for persistent data storage.",
    )
    log_dir: str = Field(
        default="logs",
        description="Directory for application log files.",
    )

    # Binance Exchange Configuration
    binance_api_key: str = Field(
        default="",
        description="Binance API key.",
    )
    binance_api_secret: str = Field(
        default="",
        description="Binance API secret.",
    )
    binance_testnet: bool = Field(
        default=True,
        description="Whether to use Binance Testnet (defaults to True for capital safety).",
    )
    binance_futures_base_url: str = Field(
        default="https://testnet.binancefuture.com",
        description="Binance Futures REST API base URL.",
    )
    binance_ws_base_url: str = Field(
        default="wss://stream.binancefuture.com",
        description="Binance Futures WebSocket stream base URL.",
    )

    # News & Macro Event Volatility Shield
    enable_news_shield: bool = Field(
        default=True,
        description="Enable news and macro event volatility shield to protect capital.",
    )
    news_blackout_pre_minutes: int = Field(
        default=15,
        ge=0,
        le=120,
        description="Minutes before scheduled event release to block new trade entries.",
    )
    news_blackout_post_minutes: int = Field(
        default=15,
        ge=0,
        le=120,
        description="Minutes after scheduled event release to block new trade entries.",
    )
    cryptopanic_api_token: str = Field(
        default="",
        description="Optional CryptoPanic API auth token for breaking news aggregation.",
    )
    news_poll_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Polling frequency in seconds for real-time news headlines.",
    )
    news_rss_feed_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
            "https://decrypt.co/feed",
            "https://www.theblock.co/rss.xml",
            "https://bitcoinmagazine.com/feed",
            "https://news.google.com/rss/search?q=crypto+bitcoin+binance+when:1d&hl=en-US&gl=US&ceid=US:en",
        ],
        description="Public crypto RSS feed URLs for zero-key, 100% free live news ingestion.",
    )

    @field_validator("max_authorized_capital_per_session")
    @classmethod
    def validate_positive_capital(cls, value: Decimal) -> Decimal:
        if value <= Decimal("0"):
            raise ValueError("max_authorized_capital_per_session must be strictly positive")
        return value

    @model_validator(mode="after")
    def validate_live_mode_safeguards(self) -> "Settings":
        """Strict fail-closed check: LIVE mode requires explicit double confirmation."""
        if self.trading_mode == "LIVE":
            expected_phrase = "I_UNDERSTAND_AND_ACCEPT_CAPITAL_RISK"
            if not self.enable_live_trading or self.confirm_real_money_trading != expected_phrase:
                raise ValueError(
                    "LIVE trading mode cannot start without explicit safety locks: "
                    "ENABLE_LIVE_TRADING=true and "
                    f"CONFIRM_REAL_MONEY_TRADING='{expected_phrase}'"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns cached settings instance."""
    return Settings()
