from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import BotConfig
from src.kalshi.auth import KalshiAuthSigner
from src.models import HealthState, MarketSnapshot

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiResult:
    payload: dict[str, Any]
    latency_ms: float


class KalshiClient:
    def __init__(self, cfg: BotConfig, health: HealthState) -> None:
        self.cfg = cfg
        self.health = health
        self.signer = KalshiAuthSigner(cfg.api_key_id, cfg.private_key_path, cfg.private_key_pem)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> ApiResult:
        body = json.dumps(payload, separators=(",", ":")) if payload is not None else ""
        url = f"{self.cfg.api_base_url.rstrip('/')}{path}"
        signed_path = self._signed_path(path)
        headers = self.signer.sign_request(method, signed_path)
        started = time.monotonic()

        for attempt in range(1, max_retries + 1):
            try:
                res = await self._client.request(method, url, params=params, content=body or None, headers=headers)
                latency_ms = (time.monotonic() - started) * 1000
                self.health.rest_latency_ms = latency_ms
                if res.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server_error:{res.status_code}", request=res.request, response=res
                    )
                if res.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "rate_limited", request=res.request, response=res
                    )
                res.raise_for_status()
                self.health.consecutive_api_errors = 0
                return ApiResult(payload=res.json(), latency_ms=latency_ms)
            except httpx.HTTPStatusError as exc:
                self.health.consecutive_api_errors += 1
                status = exc.response.status_code if exc.response is not None else None
                detail = ""
                if exc.response is not None:
                    try:
                        detail = exc.response.text[:400]
                    except Exception:
                        detail = ""
                if status is not None and status < 500 and status != 429:
                    if status == 404 and method.upper() == "GET" and path.startswith("/markets/"):
                        LOGGER.info("API request not found method=%s path=%s", method, path)
                    else:
                        LOGGER.error("API request failed method=%s path=%s err=%s detail=%s", method, path, exc, detail)
                    raise
                if attempt == max_retries:
                    LOGGER.error("API request failed method=%s path=%s err=%s detail=%s", method, path, exc, detail)
                    raise
                await asyncio.sleep((2 ** (attempt - 1)) * 0.15 + random.uniform(0.0, 0.08))
            except Exception as exc:
                self.health.consecutive_api_errors += 1
                if attempt == max_retries:
                    LOGGER.error("API request failed method=%s path=%s err=%s", method, path, exc)
                    raise
                await asyncio.sleep((2 ** (attempt - 1)) * 0.15 + random.uniform(0.0, 0.08))

        raise RuntimeError("unreachable")

    def _signed_path(self, path: str) -> str:
        base_path = urlparse(self.cfg.api_base_url).path.rstrip("/")
        if not base_path:
            return path
        return f"{base_path}{path}"

    async def get_market(self, ticker: str) -> dict[str, Any]:
        out = await self._request("GET", f"/markets/{ticker}")
        payload = out.payload
        if "market" in payload:
            return payload["market"]
        return payload

    async def list_markets(self, **params: Any) -> list[dict[str, Any]]:
        out = await self._request("GET", "/markets", params=params)
        payload = out.payload
        if "markets" in payload and isinstance(payload["markets"], list):
            return payload["markets"]
        return []

    async def get_positions(self) -> list[dict[str, Any]]:
        out = await self._request("GET", "/portfolio/positions")
        payload = out.payload
        return payload.get("positions", []) if isinstance(payload, dict) else []

    async def get_balance(self) -> dict[str, Any]:
        out = await self._request("GET", "/portfolio/balance")
        return out.payload

    async def list_orders(self, status: str = "open", ticker: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": status}
        if ticker:
            params["ticker"] = ticker
        out = await self._request("GET", "/portfolio/orders", params=params)
        payload = out.payload
        return payload.get("orders", []) if isinstance(payload, dict) else []

    async def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        out = await self._request("POST", "/portfolio/orders", payload=order)
        return out.payload

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        out = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return out.payload

    @staticmethod
    def parse_market_snapshot(market: dict[str, Any], ticker: str | None = None) -> MarketSnapshot:
        now = datetime.now(timezone.utc)
        yes_bid = int(market.get("yes_bid", market.get("best_yes_bid", 0)) or 0)
        yes_ask = int(market.get("yes_ask", market.get("best_yes_ask", 100)) or 100)
        no_bid = int(market.get("no_bid", market.get("best_no_bid", 0)) or 0)
        no_ask = int(market.get("no_ask", market.get("best_no_ask", 100)) or 100)
        bid_size = int(
            market.get("bid_size", market.get("yes_bid_size", market.get("best_yes_bid_size", 0))) or 0
        )
        ask_size = int(
            market.get("ask_size", market.get("yes_ask_size", market.get("best_yes_ask_size", 0))) or 0
        )
        close_time = None
        raw_close = market.get("close_time") or market.get("expiration_time")
        if isinstance(raw_close, str):
            close_time = _parse_time(raw_close)
        price_to_beat = _extract_price_to_beat(market)
        return MarketSnapshot(
            ticker=ticker or str(market.get("ticker", "")),
            status=str(market.get("status", "unknown")),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            ts=now,
            close_time=close_time,
            price_to_beat=price_to_beat,
        )


def _parse_time(value: str) -> datetime | None:
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return parsed
        except (TypeError, ValueError):
            return None


def _extract_price_to_beat(market: dict[str, Any]) -> float | None:
    for key in (
        "price_to_beat",
        "strike_price",
        "settlement_threshold",
        "target_price",
        "floor_price",
        "ceiling_price",
        "reference_price",
    ):
        value = market.get(key)
        parsed = _as_float(value)
        if parsed is not None:
            return parsed

    meta = market.get("metadata")
    if isinstance(meta, dict):
        for key in (
            "price_to_beat",
            "strike_price",
            "target_price",
            "floor_price",
            "ceiling_price",
            "reference_price",
        ):
            parsed = _as_float(meta.get(key))
            if parsed is not None:
                return parsed

    text_fields = [
        str(market.get("subtitle", "")),
        str(market.get("sub_title", "")),
        str(market.get("yes_sub_title", "")),
        str(market.get("no_sub_title", "")),
        str(market.get("yes_subtitle", "")),
        str(market.get("no_subtitle", "")),
        str(market.get("title", "")),
        str(market.get("question", "")),
        str(market.get("rules_primary", "")),
        str(market.get("rules_secondary", "")),
    ]
    pattern = re.compile(r"(?:price\\s*to\\s*beat|above|below)\\D*([0-9][0-9,]*(?:\\.[0-9]+)?)", re.IGNORECASE)
    for text in text_fields:
        match = pattern.search(text)
        if match:
            parsed = _as_float(match.group(1))
            if parsed is not None:
                return parsed
    # Last resort: scan serialized payload for price-like numbers near "beat/strike/above/below".
    blob = json.dumps(market, separators=(",", ":"), ensure_ascii=False)
    for rx in (
        re.compile(r"(?:beat|strike|above|below)[^0-9]{0,40}([0-9]{4,6}(?:\\.[0-9]+)?)", re.IGNORECASE),
        re.compile(r"\\$\\s*([0-9]{4,6}(?:\\.[0-9]+)?)"),
    ):
        m = rx.search(blob)
        if m:
            parsed = _as_float(m.group(1))
            if parsed is not None:
                return parsed
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 1000:
            return float(value)
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
