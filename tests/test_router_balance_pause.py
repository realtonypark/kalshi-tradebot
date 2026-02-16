from __future__ import annotations

from datetime import datetime, timezone

import httpx

from src.config import BotConfig
from src.execution.router import ExecutionRouter
from src.models import MarketSnapshot, RiskDecision, SignalDecision


class _InsufficientClient:
    async def place_order(self, order):
        req = httpx.Request("POST", "https://x/orders")
        body = {"error": {"code": "insufficient_balance", "message": "insufficient balance"}}
        res = httpx.Response(400, request=req, json=body)
        raise httpx.HTTPStatusError("bad", request=req, response=res)

    async def cancel_order(self, order_id):
        return {"order_id": order_id}


def _snap() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=45,
        yes_ask=46,
        no_bid=54,
        no_ask=55,
        bid_size=10,
        ask_size=10,
        ts=datetime.now(timezone.utc),
    )


async def test_insufficient_balance_sets_pause() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=False), _InsufficientClient())
    intents = router.build_intents(
        _snap(),
        SignalDecision(mode="taker", side="yes", confidence=0.9, fair_yes_price=60),
        RiskDecision(approved=True, max_order_contracts=1, halt=False),
        net_side="flat",
    )
    assert intents

    await router.execute(intents)
    assert router.insufficient_balance_until > datetime.now(timezone.utc)
