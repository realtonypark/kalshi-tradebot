from __future__ import annotations

from src.config import BotConfig


def test_seed_tickers_parses_and_dedupes() -> None:
    cfg = BotConfig(
        market_seed_ticker="KXBTC15M-26FEB151715",
        market_seed_tickers="kxbtc15m-26feb151715, KXETH15M-26FEB152300, kxbtc15m-26feb151715",
    )
    assert cfg.seed_tickers == ["KXBTC15M-26FEB151715", "KXETH15M-26FEB152300"]


def test_seed_tickers_falls_back_to_single_seed() -> None:
    cfg = BotConfig(market_seed_ticker="KXETH15M-26FEB152300", market_seed_tickers="")
    assert cfg.seed_tickers == ["KXETH15M-26FEB152300"]
