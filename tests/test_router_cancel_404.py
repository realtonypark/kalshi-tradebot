from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from src.config import BotConfig
from src.execution.router import ExecutionRouter, OpenOrderState
from src.models import MarketSnapshot, RiskDecision, SignalDecision


class _DummyClient:
    def __init__(self, cancel_404: bool = False) -> None:
        self.cancel_404 = cancel_404

    async def place_order(self, order: dict[str, object]) -> dict[str, object]:
        _ = order
        return {"order": {"order_id": "oid-1"}}

    async def cancel_order(self, order_id: str) -> dict[str, object]:
        if self.cancel_404:
            req = httpx.Request("DELETE", f"https://x/orders/{order_id}")
            res = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("not found", request=req, response=res)
        return {"order_id": order_id, "status": "canceled"}


def _snap() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=45,
        yes_ask=46,
        no_bid=54,
        no_ask=55,
        bid_size=5,
        ask_size=5,
        ts=datetime.now(timezone.utc),
    )


async def test_ioc_orders_not_tracked_as_open() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=False), _DummyClient())
    intents = router.build_intents(
        _snap(),
        SignalDecision(mode="taker", side="yes", confidence=0.9, fair_yes_price=60),
        RiskDecision(approved=True, max_order_contracts=1, halt=False),
        net_side="flat",
    )

    assert intents
    assert intents[0].tif == "immediate_or_cancel"
    await router.execute(intents)
    assert router.open_orders == {}


async def test_cancel_404_clears_local_open_order() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=False), _DummyClient(cancel_404=True))
    router.open_orders["oid-404"] = OpenOrderState(
        order_id="oid-404",
        client_order_id="cid",
        ticker="KXBTC15M-TEST",
        side="yes",
        action="buy",
        price_cents=50,
        contracts=1,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
    )

    canceled = await router.cancel_stale(max_age_sec=0)
    assert canceled == 1
    assert router.open_orders == {}
