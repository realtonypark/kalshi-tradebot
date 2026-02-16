from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.models import Fill, MarketSnapshot, Position


class PortfolioState:
    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.realized_pnl_usd: float = 0.0
        self.unrealized_pnl_usd: float = 0.0
        self.last_mark_price: dict[str, float] = {}
        self.trading_started_at: datetime = datetime.now(timezone.utc)
        self.total_fills: int = 0
        self.total_wins: int = 0
        self.total_losses: int = 0

    def upsert_position(self, position: Position) -> None:
        self.positions[position.ticker] = position

    def update_from_exchange_positions(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            ticker = str(row.get("ticker", ""))
            if not ticker:
                continue
            yes_contracts = int(row.get("yes_count", row.get("yes_contracts", 0)) or 0)
            no_contracts = int(row.get("no_count", row.get("no_contracts", 0)) or 0)
            avg_yes_price = float(row.get("yes_avg_price", row.get("avg_yes_price", 0.0)) or 0.0)
            avg_no_price = float(row.get("no_avg_price", row.get("avg_no_price", 0.0)) or 0.0)
            self.positions[ticker] = Position(
                ticker=ticker,
                yes_contracts=yes_contracts,
                no_contracts=no_contracts,
                avg_yes_price=avg_yes_price,
                avg_no_price=avg_no_price,
            )

    def apply_fill(self, fill: Fill) -> None:
        pos = self.positions.setdefault(fill.ticker, Position(ticker=fill.ticker))
        fee_usd = fill.fee_cents / 100.0
        if fill.side == "yes":
            if fill.action == "buy":
                total_cost = (pos.avg_yes_price * pos.yes_contracts) + (fill.price_cents * fill.contracts)
                pos.yes_contracts += fill.contracts
                pos.avg_yes_price = total_cost / max(1, pos.yes_contracts)
                self.realized_pnl_usd -= fee_usd
            else:
                sold = min(pos.yes_contracts, fill.contracts)
                pnl_cents = (fill.price_cents - pos.avg_yes_price) * sold
                pos.yes_contracts -= sold
                self.realized_pnl_usd += pnl_cents / 100.0 - fee_usd
        else:
            if fill.action == "buy":
                total_cost = (pos.avg_no_price * pos.no_contracts) + (fill.price_cents * fill.contracts)
                pos.no_contracts += fill.contracts
                pos.avg_no_price = total_cost / max(1, pos.no_contracts)
                self.realized_pnl_usd -= fee_usd
            else:
                sold = min(pos.no_contracts, fill.contracts)
                pnl_cents = (fill.price_cents - pos.avg_no_price) * sold
                pos.no_contracts -= sold
                self.realized_pnl_usd += pnl_cents / 100.0 - fee_usd

        self.total_fills += 1
        if self.realized_pnl_usd >= 0:
            self.total_wins += 1
        else:
            self.total_losses += 1

    def mark(self, snap: MarketSnapshot) -> None:
        pos = self.positions.get(snap.ticker)
        if pos is None:
            self.unrealized_pnl_usd = self._unrealized_total()
            return

        yes_mark = (snap.yes_bid + snap.yes_ask) / 2.0
        no_mark = (snap.no_bid + snap.no_ask) / 2.0
        self.last_mark_price[snap.ticker] = yes_mark

        yes_unrealized = (yes_mark - pos.avg_yes_price) * pos.yes_contracts / 100.0
        no_unrealized = (no_mark - pos.avg_no_price) * pos.no_contracts / 100.0
        # Replace this ticker component while preserving others.
        other_unrealized = self._unrealized_total(exclude=snap.ticker)
        self.unrealized_pnl_usd = other_unrealized + yes_unrealized + no_unrealized

    def gross_exposure_usd(self) -> float:
        total = 0.0
        for pos in self.positions.values():
            total += (pos.yes_contracts * pos.avg_yes_price) / 100.0
            total += (pos.no_contracts * pos.avg_no_price) / 100.0
        return total

    def market_exposure_usd(self, ticker: str) -> float:
        pos = self.positions.get(ticker)
        if not pos:
            return 0.0
        return (pos.yes_contracts * pos.avg_yes_price + pos.no_contracts * pos.avg_no_price) / 100.0

    def net_side(self, ticker: str) -> str:
        pos = self.positions.get(ticker)
        if not pos:
            return "flat"
        if pos.yes_contracts > 0 and pos.no_contracts > 0:
            if pos.yes_contracts > pos.no_contracts:
                return "yes"
            if pos.no_contracts > pos.yes_contracts:
                return "no"
            return "flat"
        if pos.yes_contracts > 0:
            return "yes"
        if pos.no_contracts > 0:
            return "no"
        return "flat"

    def snapshot(self) -> dict[str, Any]:
        return {
            "realized_pnl_usd": round(self.realized_pnl_usd, 6),
            "unrealized_pnl_usd": round(self.unrealized_pnl_usd, 6),
            "net_pnl_usd": round(self.realized_pnl_usd + self.unrealized_pnl_usd, 6),
            "positions": {ticker: asdict(pos) for ticker, pos in self.positions.items()},
            "total_fills": self.total_fills,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "trading_started_at": self.trading_started_at.isoformat(),
        }

    def _unrealized_total(self, exclude: str | None = None) -> float:
        total = 0.0
        for ticker, pos in self.positions.items():
            if exclude and ticker == exclude:
                continue
            yes_mark = self.last_mark_price.get(ticker, pos.avg_yes_price)
            no_mark = 100.0 - yes_mark
            total += (yes_mark - pos.avg_yes_price) * pos.yes_contracts / 100.0
            total += (no_mark - pos.avg_no_price) * pos.no_contracts / 100.0
        return total
