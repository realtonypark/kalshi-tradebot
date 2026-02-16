from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.market_data.btc_ta import TAFeatures
from src.models import MarketSnapshot
from src.strategy.hybrid import HybridStrategy


def _snap() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=48,
        yes_ask=49,
        no_bid=51,
        no_ask=52,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        price_to_beat=69000.0,
    )


def _ta() -> TAFeatures:
    return TAFeatures(
        spot=69040.0,
        ema_fast=69030.0,
        ema_slow=68990.0,
        macd_hist=4.0,
        rsi=61.0,
        momentum_5m=20.0,
        volatility_1m=0.0003,
        ts=datetime.now(timezone.utc),
    )


def test_requires_multiple_confirmations_before_taker() -> None:
    cfg = BotConfig(
        directional_score_threshold=0.2,
        taker_confidence_threshold=0.2,
        min_signal_confirmations=3,
        signal_confirmation_window=5,
        force_directional_entries=True,
    )
    strat = HybridStrategy(cfg)
    s = _snap()
    ta = _ta()

    d1 = strat.evaluate(s, spot_price=ta.spot, ta_features=ta)
    d2 = strat.evaluate(s, spot_price=ta.spot, ta_features=ta)
    d3 = strat.evaluate(s, spot_price=ta.spot, ta_features=ta)

    assert d1.mode == "hold"
    assert d2.mode == "hold"
    assert d3.mode in {"taker", "hold"}
