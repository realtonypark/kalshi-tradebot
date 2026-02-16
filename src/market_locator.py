from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from src.config import BotConfig
from src.kalshi.client import KalshiClient

LOGGER = logging.getLogger(__name__)


class MarketLocator:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.seed_ticker = cfg.market_seed_ticker
        self.auto_roll = cfg.auto_roll
        self.current_ticker = cfg.market_seed_ticker
        self._last_discovery_at = datetime.min.replace(tzinfo=timezone.utc)
        self._discovery_interval_sec = 10

    async def pick_active_ticker(self, client: KalshiClient) -> str:
        if not self.auto_roll:
            return self.current_ticker

        now = datetime.now(timezone.utc)
        try:
            market = await client.get_market(self.current_ticker)
            status = str(market.get("status", "")).lower()
            yes_bid = _as_int(market.get("yes_bid", market.get("best_yes_bid", 0)), 0)
            yes_ask = _as_int(market.get("yes_ask", market.get("best_yes_ask", 100)), 100)
            spread = max(0, yes_ask - yes_bid)
            if yes_bid > 0 and yes_ask < 100 and spread <= max(4, self.cfg.max_spread_cents * 2):
                return self.current_ticker
            untradable_stub = yes_bid <= 0 and yes_ask >= 100
            if status and status not in {"closed", "settled", "determined", "expired", "finalized"} and not untradable_stub:
                return self.current_ticker
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                LOGGER.info("seed ticker not found ticker=%s; attempting auto-roll", self.current_ticker)
            else:
                LOGGER.warning("failed to fetch current ticker=%s err=%s", self.current_ticker, exc)
        except Exception as exc:
            LOGGER.warning("failed to fetch current ticker=%s err=%s", self.current_ticker, exc)

        if (now - self._last_discovery_at).total_seconds() < self._discovery_interval_sec:
            return self.current_ticker

        discovered = await self._discover_next(client)
        self._last_discovery_at = now
        if discovered:
            LOGGER.info("rolling market from=%s to=%s", self.current_ticker, discovered)
            self.current_ticker = discovered
        return self.current_ticker

    async def _discover_next(self, client: KalshiClient) -> str | None:
        candidates: list[dict] = []
        # If seed is event-like, try to resolve markets under that event first.
        for event_ticker in {self.seed_ticker, self.seed_ticker.lower()}:
            try:
                rows = await client.list_markets(event_ticker=event_ticker, status="open", limit=50)
            except Exception:
                rows = []
            candidates.extend(rows)

        if not candidates:
            candidates = await client.list_markets(series_ticker="KXBTC15M", status="open", limit=50)

        unique: dict[str, dict] = {}
        for market in candidates:
            ticker = str(market.get("ticker", ""))
            if ticker:
                unique[ticker] = market

        ranked_live = []
        ranked_fallback = []
        now = datetime.now(timezone.utc)
        for market in unique.values():
            ticker = str(market.get("ticker", ""))
            if not ticker:
                continue
            details = market
            # Pull details for quote/depth if not present in list payload.
            if details.get("yes_bid") is None and details.get("best_yes_bid") is None:
                try:
                    details = await client.get_market(ticker)
                except Exception:
                    details = market

            close_dt = _close_time(details, now)
            if close_dt <= now:
                continue

            yes_bid = _as_int(details.get("yes_bid", details.get("best_yes_bid", 0)), 0)
            yes_ask = _as_int(details.get("yes_ask", details.get("best_yes_ask", 100)), 100)
            bid_size = _as_int(details.get("yes_bid_size", details.get("best_yes_bid_size", 0)), 0)
            ask_size = _as_int(details.get("yes_ask_size", details.get("best_yes_ask_size", 0)), 0)
            spread = max(0, yes_ask - yes_bid)
            min_depth = min(bid_size, ask_size)
            has_live_quote = yes_bid > 0 and yes_ask < 100

            if has_live_quote:
                ranked_live.append((spread, -min_depth, close_dt, ticker))
            ranked_fallback.append((spread, -min_depth, close_dt, ticker))

        if ranked_live:
            ranked_live.sort(key=lambda item: item[:3])
            return ranked_live[0][3]

        if not ranked_fallback:
            return None

        ranked_fallback.sort(key=lambda item: item[:3])
        return ranked_fallback[0][3]


def _close_time(market: dict, default: datetime) -> datetime:
    close_at = market.get("close_time") or market.get("expiration_time")
    if isinstance(close_at, str):
        try:
            return datetime.fromisoformat(close_at.replace("Z", "+00:00"))
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
