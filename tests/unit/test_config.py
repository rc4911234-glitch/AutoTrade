"""Tests for application settings and safety guardrails."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

import trad_auto
from config.settings import Settings, get_settings


def test_package_metadata() -> None:
    """Trad-Auto root package version should be accessible."""
    assert trad_auto.__version__ == "0.1.0"


def test_default_settings_are_safest(default_settings: Settings) -> None:
    """System must default to BACKTEST and protect capital."""
    assert default_settings.trading_mode == "BACKTEST"
    assert default_settings.enable_live_trading is False
    assert default_settings.confirm_real_money_trading == ""
    assert default_settings.store_raw_chat_text is False
    assert default_settings.max_authorized_capital_per_session == Decimal("10000.00")
    assert default_settings.owner_cli_enabled is True


def test_get_settings_cached() -> None:
    """get_settings should return a valid cached Settings instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    assert s1.trading_mode == "BACKTEST"


def test_live_mode_rejected_without_explicit_double_locks() -> None:
    """Attempting LIVE mode without explicit safety locks must raise ValidationError."""
    error_msg = "LIVE trading mode cannot start without explicit safety locks"

    with pytest.raises(ValidationError, match=error_msg):
        Settings(trading_mode="LIVE")

    with pytest.raises(ValidationError, match=error_msg):
        Settings(
            trading_mode="LIVE",
            enable_live_trading=True,
            confirm_real_money_trading="WRONG_PHRASE",
        )


def test_live_mode_allowed_with_exact_double_locks() -> None:
    """LIVE mode passes validation only when both locks are strictly satisfied."""
    settings = Settings(
        trading_mode="LIVE",
        enable_live_trading=True,
        confirm_real_money_trading="I_UNDERSTAND_AND_ACCEPT_CAPITAL_RISK",
    )
    assert settings.trading_mode == "LIVE"
    assert settings.enable_live_trading is True


def test_capital_ceiling_must_be_strictly_positive() -> None:
    """Session capital ceiling cannot be zero or negative."""
    with pytest.raises(ValidationError):
        Settings(max_authorized_capital_per_session=Decimal("0"))

    with pytest.raises(ValidationError):
        Settings(max_authorized_capital_per_session=Decimal("-500.00"))
