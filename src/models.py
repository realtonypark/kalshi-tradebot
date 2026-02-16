from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

SignalMode = Literal["maker", "taker", "hold"]
SignalSide = Literal["yes", "no", "flat"]
OrderSide = Literal["yes", "no"]
OrderAction = Literal["buy", "sell"]


@dataclass(slots=True)
class MarketSnapshot:
    ticker: str
    status: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    bid_size: int
    ask_size: int
    ts: datetime
    close_time: datetime | None = None
    price_to_beat: float | None = None


@dataclass(slots=True)
class SignalDecision:
    mode: SignalMode
    side: SignalSide
    confidence: float
    fair_yes_price: float
    reason_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    max_order_contracts: int
    halt: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OrderIntent:
    ticker: str
    side: OrderSide
    action: OrderAction
    price_cents: int
    contracts: int
    tif: str
    post_only: bool
    client_order_id: str


@dataclass(slots=True)
class Position:
    ticker: str
    yes_contracts: int = 0
    no_contracts: int = 0
    avg_yes_price: float = 0.0
    avg_no_price: float = 0.0


@dataclass(slots=True)
class Fill:
    order_id: str
    client_order_id: str
    ticker: str
    side: OrderSide
    action: OrderAction
    price_cents: int
    contracts: int
    fee_cents: int
    ts: datetime


@dataclass(slots=True)
class HealthState:
    ws_healthy: bool = False
    rest_latency_ms: float = 0.0
    last_market_data_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    consecutive_api_errors: int = 0
