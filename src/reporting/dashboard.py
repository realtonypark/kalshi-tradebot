from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import BotConfig
from src.execution.router import ExecutionRouter
from src.models import HealthState, MarketSnapshot, SignalDecision
from src.portfolio.state import PortfolioState


class Dashboard:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg

    def render(
        self,
        ticker: str,
        snap: MarketSnapshot | None,
        signal: SignalDecision | None,
        portfolio: PortfolioState,
        health: HealthState,
        router: ExecutionRouter,
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        net = portfolio.realized_pnl_usd + portfolio.unrealized_pnl_usd
        win_rate = 0.0
        if portfolio.total_fills:
            win_rate = portfolio.total_wins / portfolio.total_fills

        parts: list[str] = []
        parts.append("=" * 100)
        parts.append(f"Prediction Market Trader | {now} | market={ticker}")
        parts.append(
            f"PnL realized={portfolio.realized_pnl_usd:+.2f} unrealized={portfolio.unrealized_pnl_usd:+.2f} net={net:+.2f}"
        )
        parts.append(
            f"Exposure gross=${portfolio.gross_exposure_usd():.2f} open_orders={len(router.open_orders)} fills={portfolio.total_fills}"
        )
        parts.append(
            f"WinRate={win_rate:.2%} rejects={router.stats.rejected} cancels={router.stats.canceled} ws_healthy={health.ws_healthy}"
        )

        if snap is not None:
            spread = snap.yes_ask - snap.yes_bid
            parts.append(
                f"Book yes_bid={snap.yes_bid} yes_ask={snap.yes_ask} no_bid={snap.no_bid} no_ask={snap.no_ask} spread={spread}"
            )
            if snap.price_to_beat is not None:
                parts.append(f"PriceToBeat={snap.price_to_beat:.2f}")
        if signal is not None:
            parts.append(
                f"Signal mode={signal.mode} side={signal.side} conf={signal.confidence:.2f} fair_yes={signal.fair_yes_price:.2f}"
            )
            if snap is not None and signal.side in {"yes", "no"}:
                entry_price = snap.yes_ask if signal.side == "yes" else snap.no_ask
                spread = max(0, snap.yes_ask - snap.yes_bid)
                slippage = self.cfg.assumed_slippage_cents + max(0, (spread - 1) // 2)
                total_cost = entry_price + self.cfg.assumed_fee_per_contract_cents + slippage
                max_win = 100 - total_cost
                breakeven = total_cost / 100.0
                fair_yes = max(1.0, min(99.0, signal.fair_yes_price))
                p_raw = fair_yes / 100.0 if signal.side == "yes" else (100.0 - fair_yes) / 100.0
                conf = max(0.0, min(1.0, signal.confidence))
                p_adj = 0.5 + ((p_raw - 0.5) * max(0.55, conf))
                ev = (p_adj * 100.0) - total_cost
                kelly = _kelly_fraction(p_adj, total_cost, max_win)
                parts.append(
                    f"Economics est_cost={total_cost}c(fee={self.cfg.assumed_fee_per_contract_cents}c,slip={slippage}c) max_win={max_win}c breakeven={breakeven:.1%} model_p={p_adj:.1%} model_ev={ev:+.2f}c"
                )
                parts.append(f"Sizing kelly_raw={kelly:.2%} kelly_used={(kelly * self.cfg.kelly_fraction):.2%}")
            if signal.reason_codes:
                parts.append(f"Signal reasons={','.join(signal.reason_codes)}")

        parts.append(
            f"Health rest_latency_ms={health.rest_latency_ms:.1f} api_error_streak={health.consecutive_api_errors}"
        )
        parts.append("=" * 100)
        return "\n".join(parts)

    @staticmethod
    def health_payload(
        ticker: str,
        snap: MarketSnapshot | None,
        signal: SignalDecision | None,
        portfolio: PortfolioState,
        health: HealthState,
        router: ExecutionRouter,
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "ws_healthy": health.ws_healthy,
            "rest_latency_ms": round(health.rest_latency_ms, 3),
            "api_error_streak": health.consecutive_api_errors,
            "open_orders": len(router.open_orders),
            "realized_pnl_usd": round(portfolio.realized_pnl_usd, 6),
            "unrealized_pnl_usd": round(portfolio.unrealized_pnl_usd, 6),
            "signal_mode": signal.mode if signal else "none",
            "signal_side": signal.side if signal else "none",
            "last_snapshot_ts": snap.ts.isoformat() if snap else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _kelly_fraction(win_prob: float, loss_cents: int, win_profit_cents: int) -> float:
    if loss_cents <= 0 or win_profit_cents <= 0:
        return 0.0
    b = win_profit_cents / loss_cents
    q = 1.0 - win_prob
    raw = ((b * win_prob) - q) / b
    return max(0.0, min(1.0, raw))
