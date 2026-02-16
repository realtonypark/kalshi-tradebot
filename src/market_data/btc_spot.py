from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


class BtcSpotFeed:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.5))
        self._last_price: float | None = None
        self._last_fetch: datetime = datetime.min.replace(tzinfo=timezone.utc)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_price(self) -> float | None:
        now = datetime.now(timezone.utc)
        if self._last_price is not None and now - self._last_fetch < timedelta(seconds=1):
            return self._last_price

        for fetcher in (self._from_coinbase, self._from_binance, self._from_kraken):
            try:
                price = await fetcher()
                if price and price > 0:
                    self._last_price = price
                    self._last_fetch = now
                    return price
            except Exception as exc:
                LOGGER.debug("spot source failed source=%s err=%s", fetcher.__name__, exc)

        return self._last_price

    async def _from_coinbase(self) -> float:
        res = await self._client.get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
        res.raise_for_status()
        payload = res.json()
        return float(payload["data"]["amount"])

    async def _from_binance(self) -> float:
        res = await self._client.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
        res.raise_for_status()
        payload = res.json()
        return float(payload["price"])

    async def _from_kraken(self) -> float:
        res = await self._client.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XBTUSD"})
        res.raise_for_status()
        payload: dict[str, Any] = res.json()
        result = payload.get("result", {})
        if not result:
            raise ValueError("kraken result empty")
        pair_data = next(iter(result.values()))
        return float(pair_data["c"][0])
