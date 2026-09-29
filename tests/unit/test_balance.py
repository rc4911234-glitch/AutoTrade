"""Unit tests for multi-currency Balance model."""

from decimal import Decimal

import pytest

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.portfolio import Balance


def test_balance_creation_and_total_property() -> None:
    bal = Balance(asset="USDT", free=Decimal("1000.00"), locked=Decimal("250.00"))
    assert bal.asset == "USDT"
    assert bal.free == Decimal("1000.00")
    assert bal.locked == Decimal("250.00")
    assert bal.total == Decimal("1250.00")


def test_balance_invalid_initialization() -> None:
    with pytest.raises(DomainValidationError, match="asset cannot be empty"):
        Balance(asset="")

    with pytest.raises(DomainValidationError, match="free amount cannot be negative"):
        Balance(asset="BTC", free=Decimal("-0.01"))

    with pytest.raises(DomainValidationError, match="locked amount cannot be negative"):
        Balance(asset="ETH", free=Decimal("10.0"), locked=Decimal("-1.0"))


def test_balance_lock_and_unlock_lifecycle() -> None:
    bal = Balance(asset="USDT", free=Decimal("500.00"), locked=ZERO_DECIMAL)

    # Lock $200
    bal.lock(Decimal("200.00"))
    assert bal.free == Decimal("300.00")
    assert bal.locked == Decimal("200.00")
    assert bal.total == Decimal("500.00")

    # Unlock $50
    bal.unlock(Decimal("50.00"))
    assert bal.free == Decimal("350.00")
    assert bal.locked == Decimal("150.00")
    assert bal.total == Decimal("500.00")


def test_balance_lock_exceeding_free_fails() -> None:
    bal = Balance(asset="USDT", free=Decimal("100.00"), locked=ZERO_DECIMAL)
    with pytest.raises(DomainValidationError, match="Insufficient free balance"):
        bal.lock(Decimal("100.01"))


def test_balance_unlock_exceeding_locked_fails() -> None:
    bal = Balance(asset="USDT", free=Decimal("100.00"), locked=Decimal("50.00"))
    with pytest.raises(DomainValidationError, match="Insufficient locked balance"):
        bal.unlock(Decimal("50.01"))


def test_balance_credit_and_debit() -> None:
    bal = Balance(asset="USDT", free=Decimal("100.00"), locked=Decimal("50.00"))

    # Credit free
    bal.credit_free(Decimal("25.50"))
    assert bal.free == Decimal("125.50")
    assert bal.total == Decimal("175.50")

    # Debit free
    bal.debit_free(Decimal("25.50"))
    assert bal.free == Decimal("100.00")

    # Debit locked
    bal.debit_locked(Decimal("50.00"))
    assert bal.locked == ZERO_DECIMAL
    assert bal.total == Decimal("100.00")


def test_balance_debit_exceeding_balance_fails() -> None:
    bal = Balance(asset="USDT", free=Decimal("50.00"), locked=Decimal("10.00"))
    with pytest.raises(DomainValidationError, match="Insufficient free balance to debit"):
        bal.debit_free(Decimal("50.01"))

    with pytest.raises(DomainValidationError, match="Insufficient locked balance to debit"):
        bal.debit_locked(Decimal("10.01"))


def test_balance_zero_or_negative_operations_fail() -> None:
    bal = Balance(asset="USDT", free=Decimal("100.00"), locked=Decimal("100.00"))
    with pytest.raises(DomainValidationError, match="must be strictly positive"):
        bal.lock(ZERO_DECIMAL)
    with pytest.raises(DomainValidationError, match="must be strictly positive"):
        bal.unlock(Decimal("-5.0"))
    with pytest.raises(DomainValidationError, match="must be strictly positive"):
        bal.credit_free(ZERO_DECIMAL)
    with pytest.raises(DomainValidationError, match="must be strictly positive"):
        bal.debit_free(ZERO_DECIMAL)
    with pytest.raises(DomainValidationError, match="must be strictly positive"):
        bal.debit_locked(ZERO_DECIMAL)
