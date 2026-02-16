from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.models import MarketSnapshot
from src.strategy.hybrid import HybridStrategy


def test_missing_depth_uses_midpoint_not_floor() -> None:
    strat = HybridStrategy(BotConfig())
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=44,
        yes_ask=45,
        no_bid=55,
        no_ask=56,
        bid_size=0,
        ask_size=0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(snap)

    assert decision.fair_yes_price > 40
    assert decision.fair_yes_price < 60
