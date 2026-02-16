from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.config import BotConfig
from src.kalshi.client import KalshiClient
from src.models import MarketSnapshot, OrderIntent, RiskDecision, SignalDecision

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenOrderState:
    order_id: str
    client_order_id: str
    ticker: str
    side: str
    action: str
    price_cents: int
    contracts: int
    created_at: datetime


@dataclass(slots=True)
class ExecutionStats:
    submitted: int = 0
    rejected: int = 0
    canceled: int = 0
    fills: int = 0
    last_error: str = ""


@dataclass(slots=True)
class RouterResult:
    sent_intents: list[OrderIntent] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)


class ExecutionRouter:
    def __init__(self, cfg: BotConfig, client: KalshiClient) -> None:
        self.cfg = cfg
        self.client = client
        self.open_orders: dict[str, OpenOrderState] = {}
        self.last_entry_at: dict[str, datetime] = {}
        self.last_attempt_at: dict[str, datetime] = {}
        self.insufficient_balance_until = datetime.min.replace(tzinfo=timezone.utc)
        self.available_balance_cents: int | None = None
        self.stats = ExecutionStats()

    def set_available_balance_cents(self, cents: int | None) -> None:
        self.available_balance_cents = cents

    def build_intents(
        self,
        snap: MarketSnapshot,
        signal: SignalDecision,
        risk: RiskDecision,
        net_side: str,
    ) -> list[OrderIntent]:
        if not risk.approved:
            return []

        now = datetime.now(timezone.utc)
        if now < self.insufficient_balance_until:
            return []
        last_entry = self.last_entry_at.get(snap.ticker)
        if last_entry is not None and (now - last_entry).total_seconds() < self.cfg.entry_cooldown_sec:
            return []
        last_attempt = self.last_attempt_at.get(snap.ticker)
        if last_attempt is not None and (now - last_attempt).total_seconds() < 5:
            return []

        desired_side = signal.side
        if desired_side not in {"yes", "no"}:
            return []

        # Keep at most one directional position per market.
        if net_side in {"yes", "no"}:
            return []

        contracts = max(1, risk.max_order_contracts)
        intents: list[OrderIntent] = []

        if signal.mode == "maker":
            if self.cfg.directional_only:
                return []
            if desired_side == "yes":
                price = max(1, min(99, int(signal.fair_yes_price) - self.cfg.maker_edge_cents))
                intents.append(
                    self._intent(
                        snap.ticker,
                        "yes",
                        "buy",
                        price,
                        contracts,
                        post_only=True,
                        tif="good_till_canceled",
                    )
                )
            else:
                price = max(1, min(99, int(100 - signal.fair_yes_price) - self.cfg.maker_edge_cents))
                intents.append(
                    self._intent(
                        snap.ticker,
                        "no",
                        "buy",
                        price,
                        contracts,
                        post_only=True,
                        tif="good_till_canceled",
                    )
                )
        elif signal.mode == "taker":
            if desired_side == "yes":
                intents.append(
                    self._intent(
                        snap.ticker,
                        "yes",
                        "buy",
                        snap.yes_ask,
                        contracts,
                        post_only=False,
                        tif="immediate_or_cancel",
                    )
                )
            else:
                intents.append(
                    self._intent(
                        snap.ticker,
                        "no",
                        "buy",
                        snap.no_ask,
                        contracts,
                        post_only=False,
                        tif="immediate_or_cancel",
                    )
                )

        if self.available_balance_cents is not None:
            affordable: list[OrderIntent] = []
            for intent in intents:
                est_cost = self._estimated_order_cost_cents(intent)
                if est_cost <= self.available_balance_cents:
                    affordable.append(intent)
                else:
                    LOGGER.info(
                        "skip order insufficient local balance est_cost_cents=%s available_cents=%s",
                        est_cost,
                        self.available_balance_cents,
                    )
            intents = affordable

        return intents

    async def execute(self, intents: list[OrderIntent]) -> RouterResult:
        result = RouterResult()
        for intent in intents:
            result.sent_intents.append(intent)
            self.last_attempt_at[intent.ticker] = datetime.now(timezone.utc)
            if self.cfg.paper_mode:
                fake = {
                    "status": "paper",
                    "ticker": intent.ticker,
                    "client_order_id": intent.client_order_id,
                    "price": intent.price_cents,
                    "count": intent.contracts,
                    "side": intent.side,
                    "action": intent.action,
                }
                result.responses.append(fake)
                self.stats.submitted += 1
                self.last_entry_at[intent.ticker] = datetime.now(timezone.utc)
                continue

            payload = {
                "ticker": intent.ticker,
                "client_order_id": intent.client_order_id,
                "side": intent.side,
                "action": intent.action,
                "yes_price": intent.price_cents if intent.side == "yes" else None,
                "no_price": intent.price_cents if intent.side == "no" else None,
                "count": intent.contracts,
                "type": "limit",
                "time_in_force": intent.tif,
                "post_only": intent.post_only,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            try:
                response = await self.client.place_order(payload)
                result.responses.append(response)
                self.stats.submitted += 1
                self.last_entry_at[intent.ticker] = datetime.now(timezone.utc)
                order_id = str(response.get("order", {}).get("order_id", response.get("order_id", "")))
                if order_id and intent.tif == "good_till_canceled":
                    self.open_orders[order_id] = OpenOrderState(
                        order_id=order_id,
                        client_order_id=intent.client_order_id,
                        ticker=intent.ticker,
                        side=intent.side,
                        action=intent.action,
                        price_cents=intent.price_cents,
                        contracts=intent.contracts,
                        created_at=datetime.now(timezone.utc),
                    )
            except httpx.HTTPStatusError as exc:
                self.stats.rejected += 1
                self.stats.last_error = str(exc)
                detail = ""
                if exc.response is not None:
                    try:
                        detail = exc.response.text.lower()
                    except Exception:
                        detail = ""
                if "insufficient_balance" in detail or "insufficient balance" in detail:
                    self.insufficient_balance_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                        seconds=60
                    )
                    LOGGER.warning(
                        "order blocked by insufficient balance; pausing new entries until=%s",
                        self.insufficient_balance_until.isoformat(),
                    )
                LOGGER.error("order submit failed cid=%s err=%s", intent.client_order_id, exc)
            except Exception as exc:
                self.stats.rejected += 1
                self.stats.last_error = str(exc)
                LOGGER.error("order submit failed cid=%s err=%s", intent.client_order_id, exc)
        return result

    async def cancel_stale(self, max_age_sec: int = 12) -> int:
        now = datetime.now(timezone.utc)
        canceled = 0
        for order_id, order in list(self.open_orders.items()):
            age = (now - order.created_at).total_seconds()
            if age <= max_age_sec:
                continue
            if self.cfg.paper_mode:
                del self.open_orders[order_id]
                canceled += 1
                continue
            try:
                await self.client.cancel_order(order_id)
                del self.open_orders[order_id]
                canceled += 1
                self.stats.canceled += 1
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    # Treat as already gone on exchange; remove locally to stop retry loop.
                    del self.open_orders[order_id]
                    canceled += 1
                    continue
                LOGGER.warning("failed cancel order_id=%s err=%s", order_id, exc)
            except Exception as exc:
                LOGGER.warning("failed cancel order_id=%s err=%s", order_id, exc)
        return canceled

    async def cancel_all(self) -> int:
        canceled = 0
        for order_id in list(self.open_orders):
            if self.cfg.paper_mode:
                del self.open_orders[order_id]
                canceled += 1
                continue
            try:
                await self.client.cancel_order(order_id)
                del self.open_orders[order_id]
                canceled += 1
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    del self.open_orders[order_id]
                    canceled += 1
                    continue
                LOGGER.warning("cancel-all failed order_id=%s err=%s", order_id, exc)
            except Exception as exc:
                LOGGER.warning("cancel-all failed order_id=%s err=%s", order_id, exc)
        return canceled

    @staticmethod
    def _intent(
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        contracts: int,
        post_only: bool,
        tif: str,
    ) -> OrderIntent:
        return OrderIntent(
            ticker=ticker,
            side=side,
            action=action,
            price_cents=max(1, min(99, price_cents)),
            contracts=max(1, contracts),
            tif=tif,
            post_only=post_only,
            client_order_id=f"btc15m-{uuid.uuid4().hex[:20]}",
        )

    @staticmethod
    def _estimated_order_cost_cents(intent: OrderIntent) -> int:
        # Conservative estimate for buy orders on binary contracts.
        notional = intent.price_cents * intent.contracts
        fee_buffer = max(1, int(notional * 0.02))
        return notional + fee_buffer
