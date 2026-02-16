from __future__ import annotations

from src.market_data.btc_ta import _compute_features


def test_compute_features_includes_15m_fields() -> None:
    closes_1m = [68000 + i * 1.2 for i in range(180)]
    closes_5m = [68000 + i * 5.5 for i in range(120)]
    closes_15m = [68000 + i * 12.0 for i in range(120)]

    feat = _compute_features(closes_1m, closes_15m, closes_5m)

    assert feat.ema_fast_5m > 0
    assert feat.ema_slow_5m > 0
    assert 0 <= feat.rsi_5m <= 100
    assert feat.ema_fast_15m > 0
    assert feat.ema_slow_15m > 0
    assert 0 <= feat.rsi_15m <= 100
    assert feat.spot == closes_1m[-1]
