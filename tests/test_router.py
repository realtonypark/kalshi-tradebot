from __future__ import annotations

from datetime import datetime, timezone

from src.config import BotConfig
from src.execution.router import ExecutionRouter
from src.models import MarketSnapshot, RiskDecision, SignalDecision


class _DummyClient:
    async def place_order(self, order: dict[str, object]) -> dict[str, object]:
        return {"order_id": "oid", "echo": order}

    async def cancel_order(self, order_id: str) -> dict[str, object]:
        return {"order_id": order_id, "status": "canceled"}


def test_build_intents_respects_netting_policy() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=True), _DummyClient())
    snap = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=48,
        yes_ask=52,
        no_bid=48,
        no_ask=52,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
    )
    signal = SignalDecision("maker", "no", 0.5, 49.0)
    risk = RiskDecision(approved=True, max_order_contracts=10, halt=False)

    intents = router.build_intents(snap, signal, risk, net_side="yes")

    assert intents == []


def test_build_intents_skips_when_already_in_position_same_side() -> None:
    router = ExecutionRouter(BotConfig(paper_mode=True), _DummyClient())
    snap = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=48,
        yes_ask=52,
        no_bid=48,
        no_ask=52,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
    )
    signal = SignalDecision("taker", "yes", 0.9, 60.0)
    risk = RiskDecision(approved=True, max_order_contracts=3, halt=False)

    intents = router.build_intents(snap, signal, risk, net_side="yes")

    assert intents == []
