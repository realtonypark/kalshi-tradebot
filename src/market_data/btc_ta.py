from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import fmean
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TAFeatures:
    spot: float
    ema_fast: float
    ema_slow: float
    macd_hist: float
    rsi: float
    momentum_5m: float
    volatility_1m: float
    ema_fast_15m: float = 0.0
    ema_slow_15m: float = 0.0
    macd_hist_15m: float = 0.0
    rsi_15m: float = 50.0
    momentum_15m: float = 0.0
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class BtcTechnicalFeed:
    def __init__(self, refresh_sec: int = 5) -> None:
        self.refresh_sec = max(1, refresh_sec)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.8, connect=1.5))
        self._cached: TAFeatures | None = None
        self._last_fetch: datetime = datetime.min.replace(tzinfo=timezone.utc)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_features(self) -> TAFeatures | None:
        now = datetime.now(timezone.utc)
        if self._cached is not None and (now - self._last_fetch) < timedelta(seconds=self.refresh_sec):
            return self._cached

        for fetcher in (self._from_binance, self._from_coinbase):
            try:
                closes_1m, closes_15m = await fetcher()
                if len(closes_1m) < 40:
                    continue
                features = _compute_features(closes_1m, closes_15m)
                self._cached = features
                self._last_fetch = now
                return features
            except Exception as exc:
                LOGGER.debug("ta source failed source=%s err=%s", fetcher.__name__, exc)

        return self._cached

    async def _from_binance(self) -> tuple[list[float], list[float]]:
        res_1m = await self._client.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1m", "limit": 180},
        )
        res_1m.raise_for_status()
        rows_1m = res_1m.json()
        closes_1m = [float(r[4]) for r in rows_1m]

        res_15m = await self._client.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 120},
        )
        res_15m.raise_for_status()
        rows_15m = res_15m.json()
        closes_15m = [float(r[4]) for r in rows_15m]
        return closes_1m, closes_15m

    async def _from_coinbase(self) -> tuple[list[float], list[float]]:
        res_1m = await self._client.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params={"granularity": 60, "limit": 180},
        )
        res_1m.raise_for_status()
        rows_1m = res_1m.json()
        if not isinstance(rows_1m, list):
            raise ValueError("coinbase candles invalid")
        rows_1m.sort(key=lambda r: r[0])
        closes_1m = [float(r[4]) for r in rows_1m]

        res_15m = await self._client.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/candles",
            params={"granularity": 900, "limit": 120},
        )
        res_15m.raise_for_status()
        rows_15m = res_15m.json()
        if not isinstance(rows_15m, list):
            raise ValueError("coinbase 15m candles invalid")
        rows_15m.sort(key=lambda r: r[0])
        closes_15m = [float(r[4]) for r in rows_15m]
        return closes_1m, closes_15m


def _compute_features(closes_1m: list[float], closes_15m: list[float]) -> TAFeatures:
    now = datetime.now(timezone.utc)
    spot = closes_1m[-1]
    ema_fast = _ema(closes_1m, 12)
    ema_slow = _ema(closes_1m, 26)

    macd_series = []
    for i in range(26, len(closes_1m) + 1):
        window = closes_1m[:i]
        macd_series.append(_ema(window, 12) - _ema(window, 26))
    signal = _ema(macd_series, 9) if macd_series else 0.0
    macd_hist = (macd_series[-1] - signal) if macd_series else 0.0

    rsi = _rsi(closes_1m, 14)
    momentum_5m = closes_1m[-1] - closes_1m[-6] if len(closes_1m) >= 6 else 0.0

    returns = []
    for i in range(1, min(len(closes_1m), 31)):
        prev = closes_1m[-i - 1]
        cur = closes_1m[-i]
        if prev > 0:
            returns.append((cur - prev) / prev)
    volatility = _stdev(returns) if returns else 0.0

    if len(closes_15m) < 30:
        closes_15m = _downsample_to_15m(closes_1m)
    ema_fast_15m = _ema(closes_15m, 8) if closes_15m else spot
    ema_slow_15m = _ema(closes_15m, 21) if closes_15m else spot
    macd_hist_15m = _macd_hist(closes_15m, 8, 21, 9) if closes_15m else 0.0
    rsi_15m = _rsi(closes_15m, 14) if closes_15m else 50.0
    momentum_15m = closes_15m[-1] - closes_15m[-5] if len(closes_15m) >= 5 else 0.0

    return TAFeatures(
        spot=spot,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        macd_hist=macd_hist,
        rsi=rsi,
        momentum_5m=momentum_5m,
        volatility_1m=volatility,
        ema_fast_15m=ema_fast_15m,
        ema_slow_15m=ema_slow_15m,
        macd_hist_15m=macd_hist_15m,
        rsi_15m=rsi_15m,
        momentum_15m=momentum_15m,
        ts=now,
    )


def _macd_hist(closes: list[float], fast: int, slow: int, sig: int) -> float:
    if len(closes) < slow + sig:
        return 0.0
    macd_series = []
    for i in range(slow, len(closes) + 1):
        window = closes[:i]
        macd_series.append(_ema(window, fast) - _ema(window, slow))
    signal = _ema(macd_series, sig) if macd_series else 0.0
    return (macd_series[-1] - signal) if macd_series else 0.0


def _downsample_to_15m(closes_1m: list[float]) -> list[float]:
    if len(closes_1m) < 15:
        return closes_1m[:]
    out: list[float] = []
    for i in range(14, len(closes_1m), 15):
        out.append(closes_1m[i])
    return out


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    ema = values[0]
    for val in values[1:]:
        ema = (val * k) + (ema * (1.0 - k))
    return ema


def _rsi(closes: list[float], period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = fmean(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return sqrt(var)
