"""Universal constants and precision bounds for Trad-Auto."""

from datetime import UTC
from decimal import Decimal

# Internal Time Authority
SYSTEM_TIMEZONE = UTC

# Financial Decimal Precisions
ZERO_DECIMAL = Decimal("0")
DEFAULT_CURRENCY_QUANTUM = Decimal("0.01")  # Standard 2 decimal places for fiat/USDT
CRYPTO_HIGH_PRECISION_QUANTUM = Decimal("0.00000001")  # 8 decimal places for BTC/sats

# Risk Bounds
MAX_DAILY_LOSS_LIMIT_PCT = Decimal("0.10")  # Max 10% daily loss ceiling
MIN_RISK_REWARD_RATIO = Decimal("1.5")  # Minimum R:R for pro-trader entries
DEFAULT_MAX_SESSION_CAPITAL = Decimal("10000.00")

# Confirmation Defaults
DEFAULT_CONFIRMATION_TIMEOUT_SECONDS = 120
CONFIRMATION_CODE_LENGTH = 4
