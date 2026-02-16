from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


class BtcSpotFeed:
    def __init__(self, symbol: str = "BTC") -> None:
        self.symbol = symbol.upper()
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
        product = f"{self.symbol}-USD"
        res = await self._client.get(f"https://api.coinbase.com/v2/prices/{product}/spot")
        res.raise_for_status()
        payload = res.json()
        return float(payload["data"]["amount"])

    async def _from_binance(self) -> float:
        binance_symbol = f"{self.symbol}USDT"
        res = await self._client.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": binance_symbol},
        )
        res.raise_for_status()
        payload = res.json()
        return float(payload["price"])

    async def _from_kraken(self) -> float:
        pair = _kraken_pair(self.symbol)
        res = await self._client.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair})
        res.raise_for_status()
        payload: dict[str, Any] = res.json()
        result = payload.get("result", {})
        if not result:
            raise ValueError("kraken result empty")
        pair_data = next(iter(result.values()))
        return float(pair_data["c"][0])


def _kraken_pair(symbol: str) -> str:
    if symbol == "BTC":
        return "XBTUSD"
    if symbol == "ETH":
        return "ETHUSD"
    return f"{symbol}USD"
