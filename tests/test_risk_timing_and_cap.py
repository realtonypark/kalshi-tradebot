from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import BotConfig
from src.models import HealthState, MarketSnapshot, SignalDecision
from src.risk.engine import PortfolioRiskState, RiskEngine


def _state(market_exposure_usd: float = 0.0) -> PortfolioRiskState:
    return PortfolioRiskState(
        gross_exposure_usd=0.0,
        market_exposure_usd=market_exposure_usd,
        realized_pnl_usd=0.0,
        unrealized_pnl_usd=0.0,
        open_orders=0,
        trading_started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )


def test_outside_session_start_window_blocks_entry() -> None:
    cfg = BotConfig(
        entry_at_session_start_only=True,
        session_duration_sec=900,
        session_start_entry_window_sec=120,
        market_close_buffer_sec=60,
        min_top_book_depth=0,
    )
    engine = RiskEngine(cfg)
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=45,
        yes_ask=46,
        no_bid=54,
        no_ask=55,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=11),
    )
    signal = SignalDecision("taker", "yes", 0.9, 60.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)

    decision = engine.evaluate(snap, signal, health, _state())
    assert decision.approved is False
    assert "outside_session_start_window" in decision.reasons


def test_bet_cap_reached_blocks_new_contracts() -> None:
    cfg = BotConfig(bankroll_usd=1000, max_bet_pct=0.33, max_market_exposure_pct=0.33)
    engine = RiskEngine(cfg)
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=90,
        yes_ask=92,
        no_bid=8,
        no_ask=10,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    signal = SignalDecision("taker", "yes", 0.9, 70.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)

    decision = engine.evaluate(snap, signal, health, _state(market_exposure_usd=330.0))
    assert decision.approved is False
    assert decision.max_order_contracts == 0
    assert "bet_cap_reached" in decision.reasons


def test_poor_payout_ratio_blocks_expensive_entry() -> None:
    cfg = BotConfig(
        entry_at_session_start_only=False,
        min_top_book_depth=0,
        assumed_fee_per_contract_cents=1,
        min_win_profit_cents=2,
    )
    engine = RiskEngine(cfg)
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=98,
        yes_ask=99,
        no_bid=1,
        no_ask=2,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    signal = SignalDecision("taker", "yes", 0.9, 99.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)

    decision = engine.evaluate(snap, signal, health, _state())
    assert decision.approved is False
    assert "poor_payout_ratio" in decision.reasons


def test_negative_expected_value_blocks_entry() -> None:
    cfg = BotConfig(
        entry_at_session_start_only=False,
        min_top_book_depth=0,
        assumed_fee_per_contract_cents=1,
        min_expected_value_cents=0.25,
    )
    engine = RiskEngine(cfg)
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=69,
        yes_ask=70,
        no_bid=30,
        no_ask=31,
        bid_size=100,
        ask_size=100,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    signal = SignalDecision("taker", "yes", 0.8, 60.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)

    decision = engine.evaluate(snap, signal, health, _state())
    assert decision.approved is False
    assert "negative_expected_value" in decision.reasons


def test_higher_expected_value_allows_larger_size() -> None:
    cfg = BotConfig(
        entry_at_session_start_only=False,
        min_top_book_depth=0,
        bankroll_usd=5000,
        max_initial_order_contracts=500,
        max_bet_pct=1.0,
        max_market_exposure_pct=1.0,
        assumed_fee_per_contract_cents=1,
        min_expected_value_cents=0.1,
        base_trade_risk_pct=0.001,
        max_trade_risk_pct=0.05,
        kelly_fraction=0.0,
        size_target_ev_cents=2.0,
    )
    engine = RiskEngine(cfg)
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=47,
        yes_ask=48,
        no_bid=51,
        no_ask=52,
        bid_size=2000,
        ask_size=2000,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)

    low_edge = SignalDecision("taker", "yes", 0.55, 52.0)
    high_edge = SignalDecision("taker", "yes", 0.90, 72.0)

    low = engine.evaluate(snap, low_edge, health, _state())
    high = engine.evaluate(snap, high_edge, health, _state())

    assert low.approved is True
    assert high.approved is True
    assert high.max_order_contracts > low.max_order_contracts


def test_slippage_buffer_blocks_marginal_ev_trade() -> None:
    snap = MarketSnapshot(
        ticker="KXBTC15M-TEST",
        status="open",
        yes_bid=59,
        yes_ask=60,
        no_bid=40,
        no_ask=41,
        bid_size=200,
        ask_size=200,
        ts=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    signal = SignalDecision("taker", "yes", 0.7, 67.0)
    health = HealthState(ws_healthy=True, rest_latency_ms=10, consecutive_api_errors=0)
    base_cfg = dict(
        entry_at_session_start_only=False,
        min_top_book_depth=0,
        assumed_fee_per_contract_cents=1,
        min_expected_value_cents=0.5,
        ev_safety_cents=0.0,
    )

    no_slip = RiskEngine(BotConfig(**base_cfg, assumed_slippage_cents=0))
    with_slip = RiskEngine(BotConfig(**base_cfg, assumed_slippage_cents=2))

    d0 = no_slip.evaluate(snap, signal, health, _state())
    d1 = with_slip.evaluate(snap, signal, health, _state())

    assert d0.approved is True
    assert d1.approved is False
    assert "negative_expected_value" in d1.reasons
