from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.kalshi.client import KalshiClient
from src.models import HealthState, MarketSnapshot
from src.strategy.hybrid import HybridStrategy


def test_parse_market_price_to_beat_from_field() -> None:
    market = {
        "ticker": "KXBTC15M-TEST",
        "status": "open",
        "yes_bid": 44,
        "yes_ask": 45,
        "no_bid": 55,
        "no_ask": 56,
        "price_to_beat": 98000.5,
    }
    snap = KalshiClient.parse_market_snapshot(market)
    assert snap.price_to_beat == 98000.5


def test_strategy_uses_price_to_beat_and_spot() -> None:
    strat = HybridStrategy(BotConfig(taker_confidence_threshold=0.2, taker_min_edge_cents=1, fee_buffer_cents=0))
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=44,
        yes_ask=45,
        no_bid=55,
        no_ask=56,
        bid_size=100,
        ask_size=110,
        ts=datetime.now(timezone.utc),
        price_to_beat=98000.0,
    )

    # warm history then produce signal with spot above beat.
    _ = strat.evaluate(snap, spot_price=98000.5)
    decision = strat.evaluate(snap, spot_price=98025.0)

    assert decision.side in {"yes", "flat"}
    assert decision.mode in {"taker", "hold"}
    assert decision.fair_yes_price >= 50.0
