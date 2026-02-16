from __future__ import annotations

from src.config import BotConfig
from src.market_locator import MarketLocator


class _Client:
    def __init__(self) -> None:
        self._market_calls = 0

    async def get_market(self, ticker: str):
        self._market_calls += 1
        if ticker == "SEED404":
            import httpx

            req = httpx.Request("GET", f"https://x/markets/{ticker}")
            res = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("not found", request=req, response=res)
        return {"ticker": ticker, "status": "open", "yes_bid": 0, "yes_ask": 100}

    async def list_markets(self, **params):
        _ = params
        return [
            {
                "ticker": "MKT-WIDE",
                "status": "open",
                "yes_bid": 0,
                "yes_ask": 100,
                "yes_bid_size": 0,
                "yes_ask_size": 0,
                "close_time": "2099-01-01T00:10:00Z",
            },
            {
                "ticker": "MKT-TIGHT",
                "status": "open",
                "yes_bid": 48,
                "yes_ask": 50,
                "yes_bid_size": 100,
                "yes_ask_size": 120,
                "close_time": "2099-01-01T00:11:00Z",
            },
        ]


async def test_locator_prefers_live_quote_market() -> None:
    cfg = BotConfig(market_seed_ticker="SEED404", auto_roll=True)
    loc = MarketLocator(cfg)
    ticker = await loc.pick_active_ticker(_Client())
    assert ticker == "MKT-TIGHT"


class _SeriesClient:
    def __init__(self) -> None:
        self.last_series: str | None = None

    async def get_market(self, ticker: str):
        import httpx

        req = httpx.Request("GET", f"https://x/markets/{ticker}")
        res = httpx.Response(404, request=req)
        raise httpx.HTTPStatusError("not found", request=req, response=res)

    async def list_markets(self, **params):
        series = params.get("series_ticker")
        if isinstance(series, str):
            self.last_series = series
        return []


async def test_locator_uses_series_from_seed_prefix() -> None:
    client = _SeriesClient()
    cfg = BotConfig(market_seed_ticker="KXBTC15M-OLD", auto_roll=True)
    loc = MarketLocator(cfg, seed_ticker="KXETH15M-OLD")
    _ = await loc.pick_active_ticker(client)
    assert client.last_series == "KXETH15M"
