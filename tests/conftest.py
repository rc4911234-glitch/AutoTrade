"""Shared pytest fixtures for Trad-Auto."""

import pytest

from config.settings import Settings


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate unit tests from user-specific local .env values."""
    monkeypatch.setenv("TRADING_MODE", "BACKTEST")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("CONFIRM_REAL_MONEY_TRADING", "")
    monkeypatch.setenv("MAX_AUTHORIZED_CAPITAL_PER_SESSION", "10000.00")
    from config.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def default_settings() -> Settings:
    """Fixture providing clean default settings."""
    return Settings(
        trading_mode="BACKTEST",
        enable_live_trading=False,
        confirm_real_money_trading="",
        owner_cli_enabled=True,
    )
