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
        bid_size=120,
        ask_size=140,
        ts=datetime.now(timezone.utc),
        price_to_beat=98000.0,
    )


def test_ta_bullish_biases_yes() -> None:
    cfg = BotConfig(
        taker_confidence_threshold=0.2,
        taker_min_edge_cents=1,
        fee_buffer_cents=0,
        directional_score_threshold=0.4,
        momentum_min_cents=0.05,
        min_signal_confirmations=1,
    )
    strat = HybridStrategy(cfg)
    ta = TAFeatures(
        spot=98025.0,
        ema_fast=98020.0,
        ema_slow=97990.0,
        macd_hist=5.0,
        rsi=62.0,
        momentum_5m=22.0,
        volatility_1m=0.0004,
        ema_fast_15m=98050.0,
        ema_slow_15m=97920.0,
        macd_hist_15m=6.0,
        rsi_15m=63.0,
        momentum_15m=55.0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(_snap(), spot_price=98025.0, ta_features=ta)
    assert decision.side == "yes"


def test_ta_bearish_biases_no() -> None:
    cfg = BotConfig(
        taker_confidence_threshold=0.2,
        taker_min_edge_cents=1,
        fee_buffer_cents=0,
        directional_score_threshold=0.4,
        momentum_min_cents=0.05,
        min_signal_confirmations=1,
    )
    strat = HybridStrategy(cfg)
    ta = TAFeatures(
        spot=97970.0,
        ema_fast=97960.0,
        ema_slow=98010.0,
        macd_hist=-4.0,
        rsi=41.0,
        momentum_5m=-20.0,
        volatility_1m=0.0004,
        ema_fast_15m=97920.0,
        ema_slow_15m=98050.0,
        macd_hist_15m=-5.0,
        rsi_15m=39.0,
        momentum_15m=-48.0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(_snap(), spot_price=97970.0, ta_features=ta)
    assert decision.side == "no"


def test_conflicting_15m_regime_blocks_entry() -> None:
    cfg = BotConfig(
        taker_confidence_threshold=0.2,
        taker_min_edge_cents=1,
        fee_buffer_cents=0,
        directional_score_threshold=0.2,
        min_signal_confirmations=1,
        ta_require_15m_alignment=True,
        ta_15m_min_strength=0.6,
    )
    strat = HybridStrategy(cfg)
    ta = TAFeatures(
        spot=98105.0,
        ema_fast=98100.0,
        ema_slow=98000.0,
        macd_hist=7.0,
        rsi=66.0,
        momentum_5m=40.0,
        volatility_1m=0.0004,
        ema_fast_15m=97920.0,
        ema_slow_15m=98040.0,
        macd_hist_15m=-4.0,
        rsi_15m=41.0,
        momentum_15m=-40.0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(_snap(), spot_price=98105.0, ta_features=ta)
    assert decision.mode == "hold"
    assert ("15m_direction_conflict" in decision.reason_codes) or ("low_directional_score" in decision.reason_codes)


def test_choppy_regime_blocks_entry() -> None:
    cfg = BotConfig(
        taker_confidence_threshold=0.2,
        taker_min_edge_cents=1,
        fee_buffer_cents=0,
        directional_score_threshold=0.15,
        min_signal_confirmations=1,
        ta_require_5m_alignment=False,
        ta_require_15m_alignment=False,
        skip_choppy_regime=True,
        regime_min_trend_strength_bps=6.0,
        regime_chop_vol_1m=0.001,
    )
    strat = HybridStrategy(cfg)
    ta = TAFeatures(
        spot=98090.0,
        ema_fast=98085.0,
        ema_slow=98060.0,
        macd_hist=4.0,
        rsi=58.0,
        momentum_5m=18.0,
        volatility_1m=0.0016,
        ema_fast_5m=98020.0,
        ema_slow_5m=98019.8,
        macd_hist_5m=0.2,
        rsi_5m=51.0,
        momentum_5m_tf=1.0,
        ema_fast_15m=98050.0,
        ema_slow_15m=98010.0,
        macd_hist_15m=2.0,
        rsi_15m=55.0,
        momentum_15m=15.0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(_snap(), spot_price=98090.0, ta_features=ta)
    assert decision.mode == "hold"
    assert "choppy_regime" in decision.reason_codes


def test_macd_rsi_crosses_are_used() -> None:
    cfg = BotConfig(
        taker_confidence_threshold=0.2,
        taker_min_edge_cents=1,
        fee_buffer_cents=0,
        directional_score_threshold=0.2,
        min_signal_confirmations=1,
        ta_require_5m_alignment=False,
        ta_require_15m_alignment=False,
    )
    strat = HybridStrategy(cfg)
    ta = TAFeatures(
        spot=98110.0,
        ema_fast=98095.0,
        ema_slow=98080.0,
        macd_hist=0.8,
        macd_hist_prev=-0.6,
        rsi=52.0,
        rsi_prev=47.0,
        momentum_5m=12.0,
        volatility_1m=0.0005,
        ema_fast_5m=98100.0,
        ema_slow_5m=98040.0,
        macd_hist_5m=0.7,
        macd_hist_prev_5m=-0.4,
        rsi_5m=53.0,
        rsi_prev_5m=48.0,
        momentum_5m_tf=8.0,
        ema_fast_15m=98120.0,
        ema_slow_15m=97990.0,
        macd_hist_15m=1.1,
        macd_hist_prev_15m=-0.5,
        rsi_15m=54.0,
        rsi_prev_15m=49.0,
        momentum_15m=22.0,
        ts=datetime.now(timezone.utc),
    )

    decision = strat.evaluate(_snap(), spot_price=98110.0, ta_features=ta)
    assert decision.side == "yes"
    assert "macd_golden_cross_1m" in decision.reason_codes
    assert "macd_golden_cross_5m" in decision.reason_codes
    assert "macd_golden_cross_15m" in decision.reason_codes
    assert "rsi_golden_cross_1m" in decision.reason_codes
