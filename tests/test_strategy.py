from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.models import MarketSnapshot
from src.strategy.hybrid import HybridStrategy


def test_taker_requires_confidence_and_edge() -> None:
    cfg = BotConfig(taker_confidence_threshold=0.95, taker_min_edge_cents=8)
    strat = HybridStrategy(cfg)
    snap = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=49,
        yes_ask=51,
        no_bid=49,
        no_ask=51,
        bid_size=100,
        ask_size=99,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(snap)

    assert decision.mode in {"maker", "hold"}


def test_strategy_emits_valid_side() -> None:
    cfg = BotConfig()
    strat = HybridStrategy(cfg)
    snap = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=40,
        yes_ask=60,
        no_bid=40,
        no_ask=60,
        bid_size=300,
        ask_size=30,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(snap)

    assert decision.side in {"yes", "no", "flat"}
    assert 0.0 <= decision.confidence <= 1.0
