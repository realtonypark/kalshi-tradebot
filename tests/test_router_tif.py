from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.execution.router import ExecutionRouter
from src.models import MarketSnapshot, RiskDecision, SignalDecision


class _DummyClient:
    async def place_order(self, order):
        return {"order_id": "x", "order": {"order_id": "x"}}

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


def test_taker_uses_ioc() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=True), _DummyClient())
    intents = router.build_intents(
        _snap(),
        SignalDecision(mode="taker", side="yes", confidence=0.9, fair_yes_price=60),
        RiskDecision(approved=True, max_order_contracts=1, halt=False),
        net_side="flat",
    )
    assert intents
    assert intents[0].tif == "immediate_or_cancel"


def test_maker_uses_gtc() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=True, directional_only=False), _DummyClient())
    intents = router.build_intents(
        _snap(),
        SignalDecision(mode="maker", side="yes", confidence=0.9, fair_yes_price=60),
        RiskDecision(approved=True, max_order_contracts=1, halt=False),
        net_side="flat",
    )
    assert intents
    assert intents[0].tif == "good_till_canceled"
