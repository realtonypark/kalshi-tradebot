from __future__ import annotations

import asyncio
import json
import logging
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ModuleNotFoundError:  # pragma: no cover
    websockets = None
    WebSocketClientProtocol = Any  # type: ignore[assignment]

from src.config import BotConfig
from src.kalshi.auth import KalshiAuthSigner
from src.models import HealthState

LOGGER = logging.getLogger(__name__)


class KalshiWsFeed:
    def __init__(self, cfg: BotConfig, health: HealthState) -> None:
        self.cfg = cfg
        self.health = health
        self._conn: WebSocketClientProtocol | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._stop = asyncio.Event()
        self._tickers: set[str] = set()

    def subscribe(self, ticker: str) -> None:
        if ticker in self._tickers:
            return
        self._tickers.add(ticker)
        if self._conn is not None:
            asyncio.create_task(self._send_subscribe())

    def clear_subscriptions(self) -> None:
        self._tickers.clear()

    async def get_message(self, timeout: float = 0.0) -> dict[str, Any] | None:
        if timeout <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def run_forever(self) -> None:
        if websockets is None:
            LOGGER.warning("websockets dependency not installed; feed disabled")
            self.health.ws_healthy = False
            while not self._stop.is_set():
                await asyncio.sleep(1.0)
            return
        while not self._stop.is_set():
            try:
                await self._connect_and_stream()
            except Exception as exc:
                self.health.ws_healthy = False
                LOGGER.warning("websocket reconnecting err=%s", exc)
                await asyncio.sleep(1.5)

    async def stop(self) -> None:
        self._stop.set()
        if self._conn is not None:
            await self._conn.close()

    async def _connect_and_stream(self) -> None:
        ssl_context = _build_ssl_context(self.cfg)
        ws_headers = _build_ws_auth_headers(self.cfg)
        try:
            ws_ctx = websockets.connect(
                self.cfg.ws_url,
                ping_interval=10,
                ping_timeout=10,
                ssl=ssl_context,
                additional_headers=ws_headers,
            )
        except TypeError:
            ws_ctx = websockets.connect(
                self.cfg.ws_url,
                ping_interval=10,
                ping_timeout=10,
                ssl=ssl_context,
                extra_headers=ws_headers,
            )

        async with ws_ctx as conn:
            self._conn = conn
            self.health.ws_healthy = True
            await self._send_subscribe()
            async for raw in conn:
                payload = _parse_json(raw)
                if payload is None:
                    continue
                self.health.last_market_data_ts = datetime.now(timezone.utc)
                self.health.ws_healthy = True
                if self._queue.full():
                    _ = self._queue.get_nowait()
                self._queue.put_nowait(payload)

    async def _send_subscribe(self) -> None:
        if self._conn is None:
            return
        if not self._tickers:
            return
        msg = {
            "id": "k15m-feed",
            "cmd": "subscribe",
            "params": {
                # Keep to public channels to avoid WS auth requirements.
                "channels": ["ticker"],
                "market_tickers": sorted(self._tickers),
            },
        }
        await self._conn.send(json.dumps(msg, separators=(",", ":")))


def _parse_json(raw: str | bytes) -> dict[str, Any] | None:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
        return None
    except json.JSONDecodeError:
        return None


def _build_ssl_context(cfg: BotConfig) -> ssl.SSLContext:
    if not cfg.ws_verify_tls:
        return ssl._create_unverified_context()

    if cfg.ws_ca_bundle_path:
        ca_path = Path(cfg.ws_ca_bundle_path).expanduser()
        if ca_path.exists():
            return ssl.create_default_context(cafile=str(ca_path))
        LOGGER.warning("KALSHI_WS_CA_BUNDLE_PATH not found path=%s; using default certs", ca_path)

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ModuleNotFoundError:
        return ssl.create_default_context()


def _build_ws_auth_headers(cfg: BotConfig) -> dict[str, str]:
    signer = KalshiAuthSigner(cfg.api_key_id, cfg.private_key_path, cfg.private_key_pem)
    path = urlparse(cfg.ws_url).path or "/trade-api/ws/v2"
    return signer.sign_request("GET", path)
