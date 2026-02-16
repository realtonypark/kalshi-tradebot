from __future__ import annotations

from src.main import _extract_fill_fee_cents, _extract_fill_price_cents


def test_extract_no_side_price_prefers_no_price() -> None:
    order = {"yes_price": 5, "no_price": 95}
    assert _extract_fill_price_cents(order, "no") == 95


def test_extract_fee_prefers_explicit_fee_then_taker() -> None:
    assert _extract_fill_fee_cents({"fee": 3, "taker_fees": 1}) == 3
    assert _extract_fill_fee_cents({"taker_fees": 1}) == 1
