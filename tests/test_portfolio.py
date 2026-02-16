from __future__ import annotations

from datetime import datetime, timezone

from src.models import Fill, MarketSnapshot
from src.portfolio.state import PortfolioState


def test_apply_fill_and_mark() -> None:
    p = PortfolioState()
    p.apply_fill(
        Fill(
            order_id="o1",
            client_order_id="c1",
            ticker="kxbtc15m-foo",
            side="yes",
            action="buy",
            price_cents=45,
            contracts=10,
            fee_cents=5,
            ts=datetime.now(timezone.utc),
        )
    )
    snap = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=50,
        yes_ask=52,
        no_bid=48,
        no_ask=50,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
    )
    p.mark(snap)

    assert p.positions["kxbtc15m-foo"].yes_contracts == 10
    assert p.unrealized_pnl_usd > 0


def test_net_side_resolution() -> None:
    p = PortfolioState()
    p.apply_fill(
        Fill(
            order_id="o2",
            client_order_id="c2",
            ticker="kxbtc15m-foo",
            side="no",
            action="buy",
            price_cents=55,
            contracts=7,
            fee_cents=0,
            ts=datetime.now(timezone.utc),
        )
    )
    assert p.net_side("kxbtc15m-foo") == "no"
