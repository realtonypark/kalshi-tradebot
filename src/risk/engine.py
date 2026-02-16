from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import BotConfig
from src.models import HealthState, MarketSnapshot, RiskDecision, SignalDecision


@dataclass(slots=True)
class PortfolioRiskState:
    gross_exposure_usd: float
    market_exposure_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    open_orders: int
    trading_started_at: datetime


@dataclass(slots=True)
class EconomicsSnapshot:
    entry_price_cents: int
    total_cost_cents: int
    max_profit_cents: int
    fair_prob: float
    adjusted_prob: float
    expected_value_cents: float
    kelly_fraction: float


class RiskEngine:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        snap: MarketSnapshot,
        signal: SignalDecision,
        health: HealthState,
        portfolio: PortfolioRiskState,
    ) -> RiskDecision:
        reasons: list[str] = []
        halt = False
        economics: EconomicsSnapshot | None = None

        drawdown = -(portfolio.realized_pnl_usd + portfolio.unrealized_pnl_usd)
        if drawdown >= self.cfg.daily_loss_limit_usd:
            reasons.append("daily_drawdown_limit")
            halt = True

        if portfolio.gross_exposure_usd > self.cfg.gross_exposure_limit_usd:
            reasons.append("gross_exposure_limit")

        if portfolio.market_exposure_usd > self.cfg.market_exposure_limit_usd:
            reasons.append("market_exposure_limit")

        spread = max(0, snap.yes_ask - snap.yes_bid)
        if spread > self.cfg.max_spread_cents:
            reasons.append("spread_too_wide")

        if max(snap.bid_size, snap.ask_size) > 0 and min(snap.bid_size, snap.ask_size) < self.cfg.min_top_book_depth:
            reasons.append("book_depth_too_thin")

        age_sec = (datetime.now(timezone.utc) - health.last_market_data_ts).total_seconds()
        if age_sec > self.cfg.stale_feed_sec:
            reasons.append("stale_feed")

        if not health.ws_healthy:
            reasons.append("ws_unhealthy")

        if health.rest_latency_ms > self.cfg.max_rest_latency_ms:
            reasons.append("rest_latency_too_high")

        if health.consecutive_api_errors >= self.cfg.max_consecutive_api_errors:
            reasons.append("api_error_streak")
            halt = True

        if snap.close_time is not None:
            time_to_close = (snap.close_time - datetime.now(timezone.utc)).total_seconds()
            if time_to_close < self.cfg.market_close_buffer_sec:
                reasons.append("too_close_to_settlement")
            if self.cfg.entry_at_session_start_only:
                since_open = self.cfg.session_duration_sec - time_to_close
                if since_open < 0:
                    reasons.append("session_not_open")
                elif since_open > self.cfg.session_start_entry_window_sec:
                    reasons.append("outside_session_start_window")
        elif self.cfg.entry_at_session_start_only:
            reasons.append("missing_close_time")

        if signal.mode == "hold":
            reasons.append("signal_hold")
        else:
            economics = self._compute_economics(snap, signal)
            economics_reason = self._economics_check(economics)
            if economics_reason:
                reasons.append(economics_reason)

        max_contracts = self._size_contracts(snap, signal, portfolio, economics)
        if max_contracts <= 0:
            reasons.append("bet_cap_reached")

        approved = not reasons or reasons == ["signal_hold"]
        if "signal_hold" in reasons:
            approved = False

        if halt:
            approved = False

        return RiskDecision(approved=approved, max_order_contracts=max_contracts, halt=halt, reasons=reasons)

    def _compute_economics(self, snap: MarketSnapshot, signal: SignalDecision) -> EconomicsSnapshot:
        entry_price_cents = snap.yes_ask if signal.side == "yes" else snap.no_ask
        total_cost_cents = entry_price_cents + self.cfg.assumed_fee_per_contract_cents
        max_profit_cents = max(0, 100 - total_cost_cents)

        fair_yes = _clamp(signal.fair_yes_price, 1.0, 99.0)
        fair_prob = fair_yes / 100.0 if signal.side == "yes" else (100.0 - fair_yes) / 100.0
        confidence = _clamp(signal.confidence, 0.0, 1.0)
        adjusted_prob = 0.5 + ((fair_prob - 0.5) * confidence)
        expected_value_cents = (adjusted_prob * 100.0) - total_cost_cents

        kelly_fraction = _kelly_fraction(adjusted_prob, total_cost_cents, max_profit_cents)
        return EconomicsSnapshot(
            entry_price_cents=entry_price_cents,
            total_cost_cents=total_cost_cents,
            max_profit_cents=max_profit_cents,
            fair_prob=fair_prob,
            adjusted_prob=adjusted_prob,
            expected_value_cents=expected_value_cents,
            kelly_fraction=kelly_fraction,
        )

    def _economics_check(self, economics: EconomicsSnapshot) -> str | None:
        if economics.max_profit_cents < self.cfg.min_win_profit_cents:
            return "poor_payout_ratio"
        if economics.expected_value_cents < self.cfg.min_expected_value_cents:
            return "negative_expected_value"
        if economics.kelly_fraction <= 0:
            return "negative_expected_value"
        return None

    def _size_contracts(
        self,
        snap: MarketSnapshot,
        signal: SignalDecision,
        portfolio: PortfolioRiskState,
        economics: EconomicsSnapshot | None,
    ) -> int:
        if signal.side not in {"yes", "no"} or economics is None:
            return 0

        depth_limited = int(max(1, min(snap.bid_size, snap.ask_size) * 0.25))
        confidence_factor = _clamp(signal.confidence, 0.1, 1.0)
        spread = max(1.0, float(abs(snap.yes_ask - snap.yes_bid)))
        spread_penalty = _clamp(1.0 - ((spread - 1.0) / max(1.0, float(self.cfg.max_spread_cents))), 0.3, 1.0)

        base_risk_usd = self.cfg.bankroll_usd * max(0.0, self.cfg.base_trade_risk_pct)
        kelly_risk_usd = self.cfg.bankroll_usd * _clamp(
            economics.kelly_fraction * max(0.0, self.cfg.kelly_fraction),
            0.0,
            max(0.0, self.cfg.max_trade_risk_pct),
        )
        edge_scale = _clamp(
            economics.expected_value_cents / max(0.1, self.cfg.size_target_ev_cents),
            0.5,
            2.5,
        )
        risk_budget_usd = max(base_risk_usd, kelly_risk_usd)
        risk_budget_usd *= confidence_factor * spread_penalty * edge_scale
        risk_budget_usd = min(risk_budget_usd, self.cfg.bankroll_usd * max(0.0, self.cfg.max_trade_risk_pct))

        elapsed_hours = (datetime.now(timezone.utc) - portfolio.trading_started_at).total_seconds() / 3600
        warmup_cap = self.cfg.max_initial_order_contracts
        if elapsed_hours > 24:
            warmup_cap = int(self.cfg.max_initial_order_contracts * 1.6)

        per_contract_usd = max(0.01, economics.total_cost_cents / 100.0)
        contracts = int(risk_budget_usd / per_contract_usd)
        if contracts <= 0:
            contracts = 1

        cap_usd = self.cfg.bankroll_usd * self.cfg.max_bet_pct
        remaining_cap_usd = max(0.0, cap_usd - portfolio.market_exposure_usd)
        cap_contracts = int(remaining_cap_usd / per_contract_usd)
        if cap_contracts <= 0:
            return 0

        return max(1, min(contracts, depth_limited, warmup_cap, cap_contracts))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _kelly_fraction(win_prob: float, loss_cents: int, win_profit_cents: int) -> float:
    if loss_cents <= 0 or win_profit_cents <= 0:
        return 0.0
    b = win_profit_cents / loss_cents
    q = 1.0 - win_prob
    raw = ((b * win_prob) - q) / b
    return _clamp(raw, 0.0, 1.0)
