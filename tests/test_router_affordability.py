from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.execution.router import ExecutionRouter
from src.models import MarketSnapshot, RiskDecision, SignalDecision


class _DummyClient:
    async def place_order(self, order):
        return {"order": {"order_id": "oid"}}

    async def cancel_order(self, order_id):
        return {"order_id": order_id}


def _snap() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=10,
        yes_ask=12,
        no_bid=88,
        no_ask=90,
        bid_size=10,
        ask_size=10,
        ts=datetime.now(timezone.utc),
    )


def test_build_intents_skips_when_unaffordable() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=True), _DummyClient())
    router.set_available_balance_cents(20)
    intents = router.build_intents(
        _snap(),
        SignalDecision(mode="taker", side="no", confidence=0.9, fair_yes_price=30),
        RiskDecision(approved=True, max_order_contracts=1, halt=False),
        net_side="flat",
    )
    assert intents == []
