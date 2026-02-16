from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import BotConfig
from src.models import HealthState, MarketSnapshot, SignalDecision
from src.risk.engine import PortfolioRiskState, RiskEngine


def _snap(**overrides: object) -> MarketSnapshot:
    base = MarketSnapshot(
        ticker="kxbtc15m-foo",
        status="open",
        yes_bid=48,
        yes_ask=52,
        no_bid=48,
        no_ask=52,
        bid_size=120,
        ask_size=130,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _risk_state(**overrides: object) -> PortfolioRiskState:
    base = PortfolioRiskState(
        gross_exposure_usd=50.0,
        market_exposure_usd=20.0,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
        open_orders=0,
        trading_started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_halts_on_daily_drawdown() -> None:
    cfg = BotConfig(bankroll_usd=1000, max_daily_loss_pct=0.025)
    engine = RiskEngine(cfg)
    signal = SignalDecision("maker", "yes", 0.9, 50.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=120, consecutive_api_errors=0)

    decision = engine.evaluate(
        _snap(),
        signal,
        health,
        _risk_state(realized_pnl_usd=-30.0, unrealized_pnl_usd=0.0),
    )

    assert decision.halt is True
    assert decision.approved is False
    assert "daily_drawdown_limit" in decision.reasons


def test_blocks_thin_book() -> None:
    cfg = BotConfig(min_top_book_depth=30)
    engine = RiskEngine(cfg)
    signal = SignalDecision("maker", "yes", 0.6, 51.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=50, consecutive_api_errors=0)

    decision = engine.evaluate(
        _snap(bid_size=10, ask_size=12),
        signal,
        health,
        _risk_state(),
    )

    assert decision.approved is False
    assert "book_depth_too_thin" in decision.reasons


def test_mandatory_override_approves_soft_block() -> None:
    cfg = BotConfig(mandatory_session_entry=True, mandatory_entry_contracts=1)
    engine = RiskEngine(cfg)
    signal = SignalDecision("taker", "yes", 0.6, 60.0, ["mandatory_session_entry"])
    health = HealthState(ws_healthy=True, rest_latency_ms=50, consecutive_api_errors=0)

    decision = engine.evaluate(
        _snap(yes_bid=0, yes_ask=100, no_bid=0, no_ask=100),
        signal,
        health,
        _risk_state(),
    )
    forced = engine.mandatory_override(decision)

    assert decision.approved is False
    assert forced.approved is True
    assert forced.max_order_contracts == 1
    assert "mandatory_session_entry_override" in forced.reasons


def test_mandatory_override_keeps_hard_halt() -> None:
    cfg = BotConfig(mandatory_session_entry=True, mandatory_entry_contracts=1)
    engine = RiskEngine(cfg)
    signal = SignalDecision("taker", "yes", 0.6, 60.0, ["mandatory_session_entry"])
    health = HealthState(ws_healthy=True, rest_latency_ms=50, consecutive_api_errors=0)

    decision = engine.evaluate(
        _snap(),
        signal,
        health,
        _risk_state(realized_pnl_usd=-100.0, unrealized_pnl_usd=0.0),
    )
    forced = engine.mandatory_override(decision)

    assert decision.halt is True
    assert forced.approved is False
    assert forced.halt is True
