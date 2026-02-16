from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import BotConfig
from src.main import _asset_from_seed, _force_session_entry_signal, _should_force_session_entry
from src.market_data.btc_ta import TAFeatures
from src.models import MarketSnapshot, SignalDecision


def _snap(**overrides: object) -> MarketSnapshot:
    base = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=48,
        yes_ask=49,
        no_bid=51,
        no_ask=52,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=14, seconds=40),
        price_to_beat=98000.0,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_should_force_session_entry_within_window() -> None:
    cfg = BotConfig(
        mandatory_session_entry=True,
        entry_at_session_start_only=True,
        session_duration_sec=900,
        session_start_entry_window_sec=120,
    )
    snap = _snap(close_time=datetime.now(timezone.utc) + timedelta(seconds=860))
    assert _should_force_session_entry(cfg, snap, net_side="flat") is True


def test_force_session_entry_signal_uses_fallback_side() -> None:
    signal = SignalDecision("hold", "flat", 0.1, 50.0, ["low_directional_score"])
    ta = TAFeatures(
        spot=98100.0,
        ema_fast=98120.0,
        ema_slow=98050.0,
        macd_hist=1.5,
        rsi=55.0,
        momentum_5m=15.0,
        volatility_1m=0.0004,
        ema_fast_5m=98100.0,
        ema_slow_5m=98030.0,
        macd_hist_5m=1.2,
        rsi_5m=54.0,
        momentum_5m_tf=9.0,
        ema_fast_15m=98150.0,
        ema_slow_15m=97990.0,
        macd_hist_15m=1.6,
        rsi_15m=56.0,
        momentum_15m=22.0,
        ts=datetime.now(timezone.utc),
    )
    forced = _force_session_entry_signal(signal, _snap(), spot_price=98100.0, ta_features=ta)

    assert forced.mode == "taker"
    assert forced.side == "yes"
    assert forced.confidence >= 0.55
    assert "mandatory_session_entry" in forced.reason_codes
    assert "forced_side_from_fallback" in forced.reason_codes


def test_asset_from_seed_supports_sol_and_xrp() -> None:
    assert _asset_from_seed("KXSOL15M-26FEB161315") == "SOL"
    assert _asset_from_seed("KXXRP15M-26FEB161315") == "XRP"
