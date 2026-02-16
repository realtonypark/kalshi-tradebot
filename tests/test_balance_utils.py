from __future__ import annotations

from src.main import _extract_available_balance_cents


def test_extract_available_balance_cents_direct_float() -> None:
    payload = {"available_balance": 12.34}
    assert _extract_available_balance_cents(payload) == 1234


def test_extract_available_balance_cents_nested() -> None:
    payload = {"balance": {"available_cash": "45.67"}}
    assert _extract_available_balance_cents(payload) == 4567
