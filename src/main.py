from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import BotConfig, load_config
from src.control.kill_switch import KillSwitch
from src.execution.router import ExecutionRouter
from src.kalshi.client import KalshiClient
from src.kalshi.ws import KalshiWsFeed
from src.logging_setup import setup_logging
from src.market_data.btc_spot import BtcSpotFeed
from src.market_data.btc_ta import BtcTechnicalFeed
from src.market_locator import MarketLocator
from src.models import Fill, HealthState, MarketSnapshot
from src.portfolio.state import PortfolioState
from src.reporting.dashboard import Dashboard
from src.risk.engine import PortfolioRiskState, RiskEngine
from src.storage.files import FileStore
from src.strategy.hybrid import HybridStrategy

LOGGER = logging.getLogger(__name__)


async def run_bot(cfg: BotConfig) -> None:
    setup_logging(cfg.log_dir)
    _validate_config(cfg)

    store = FileStore(cfg.data_dir)
    kill = KillSwitch(cfg.kill_switch_file)
    kill.install_signal_handlers()

    health = HealthState(ws_healthy=False)
    client = KalshiClient(cfg, health)
    strategy = HybridStrategy(cfg)
    risk_engine = RiskEngine(cfg)
    portfolio = PortfolioState()
    locator = MarketLocator(cfg)
    spot_feed = BtcSpotFeed()
    ta_feed = BtcTechnicalFeed(refresh_sec=cfg.ta_refresh_sec)
    dashboard = Dashboard(cfg)
    router = ExecutionRouter(cfg, client)
    seen_fills: set[str] = store.load_seen_fills()

    startup_balance = await _startup_auth_check(client)
    router.set_available_balance_cents(_extract_available_balance_cents(startup_balance))

    ws = KalshiWsFeed(cfg, health)
    ws.subscribe(cfg.market_seed_ticker)
    ws_task = asyncio.create_task(ws.run_forever(), name="kalshi-ws")
    LOGGER.info("bot started env=%s paper_mode=%s seed=%s", cfg.kalshi_env, cfg.paper_mode, cfg.market_seed_ticker)

    last_dashboard = datetime.min.replace(tzinfo=timezone.utc)
    last_positions_sync = datetime.min.replace(tzinfo=timezone.utc)
    last_balance_sync = datetime.min.replace(tzinfo=timezone.utc)
    last_snapshots: dict[str, MarketSnapshot] = {}
    last_skip_log = datetime.min.replace(tzinfo=timezone.utc)

    try:
        while not kill.triggered:
            ticker = await locator.pick_active_ticker(client)
            ws.subscribe(ticker)

            now = datetime.now(timezone.utc)
            if (now - last_positions_sync).total_seconds() > 20:
                await _sync_positions(client, portfolio)
                last_positions_sync = now
            if (now - last_balance_sync).total_seconds() > 10:
                await _sync_balance(client, router)
                last_balance_sync = now

            snap = await _snapshot_from_ws_or_rest(client, ws, ticker, last_snapshots.get(ticker))
            last_snapshots[ticker] = snap
            health.last_market_data_ts = snap.ts

            ta_features = await ta_feed.get_features()
            spot_price = ta_features.spot if ta_features is not None else await spot_feed.get_price()
            signal = strategy.evaluate(snap, spot_price=spot_price, ta_features=ta_features)
            portfolio.mark(snap)

            risk_state = PortfolioRiskState(
                gross_exposure_usd=portfolio.gross_exposure_usd(),
                market_exposure_usd=portfolio.market_exposure_usd(ticker),
                realized_pnl_usd=portfolio.realized_pnl_usd,
                unrealized_pnl_usd=portfolio.unrealized_pnl_usd,
                open_orders=len(router.open_orders),
                trading_started_at=portfolio.trading_started_at,
            )
            risk = risk_engine.evaluate(snap, signal, health, risk_state)
            if risk.halt:
                LOGGER.critical("risk halt triggered reasons=%s", ",".join(risk.reasons))
                kill.trip()
                break
            if not risk.approved and (now - last_skip_log).total_seconds() >= 5:
                LOGGER.info(
                    "no-trade ticker=%s risk=%s signal=%s/%s signal_reasons=%s spread=%s depth=%s/%s beat=%s spot=%s rsi1=%s macd1=%s rsi15=%s macd15=%s",
                    ticker,
                    ",".join(risk.reasons) if risk.reasons else "unknown",
                    signal.mode,
                    signal.side,
                    ",".join(signal.reason_codes) if signal.reason_codes else "none",
                    max(0, snap.yes_ask - snap.yes_bid),
                    snap.bid_size,
                    snap.ask_size,
                    f"{snap.price_to_beat:.2f}" if snap.price_to_beat is not None else "na",
                    f"{spot_price:.2f}" if spot_price is not None else "na",
                    f"{ta_features.rsi:.1f}" if ta_features is not None else "na",
                    f"{ta_features.macd_hist:.4f}" if ta_features is not None else "na",
                    f"{ta_features.rsi_15m:.1f}" if ta_features is not None else "na",
                    f"{ta_features.macd_hist_15m:.4f}" if ta_features is not None else "na",
                )
                last_skip_log = now

            intents = router.build_intents(snap, signal, risk, portfolio.net_side(ticker))
            result = await router.execute(intents)
            await router.cancel_stale(max_age_sec=12)

            for intent, response in zip(result.sent_intents, result.responses):
                store.append_order(
                    {
                        "ticker": intent.ticker,
                        "client_order_id": intent.client_order_id,
                        "side": intent.side,
                        "action": intent.action,
                        "price_cents": intent.price_cents,
                        "contracts": intent.contracts,
                        "post_only": intent.post_only,
                        "response": response,
                    }
                )

            await _reconcile_fills(client, ticker, seen_fills, portfolio, store)
            store.write_positions(portfolio.snapshot())

            if (now - last_dashboard).total_seconds() >= cfg.dashboard_interval_sec:
                print(dashboard.render(ticker, snap, signal, portfolio, health, router), flush=True)
                store.append_pnl(
                    portfolio.realized_pnl_usd,
                    portfolio.unrealized_pnl_usd,
                    portfolio.realized_pnl_usd + portfolio.unrealized_pnl_usd,
                    portfolio.gross_exposure_usd(),
                )
                store.write_health(dashboard.health_payload(ticker, snap, signal, portfolio, health, router))
                last_dashboard = now

            await asyncio.sleep(1.0)
    finally:
        LOGGER.info("shutdown initiated")
        try:
            await router.cancel_all()
        except Exception as exc:
            LOGGER.warning("cancel all failed: %s", exc)
        await ws.stop()
        ws_task.cancel()
        await ta_feed.aclose()
        await spot_feed.aclose()
        await client.aclose()


def _validate_config(cfg: BotConfig) -> None:
    if cfg.kalshi_env not in {"prod", "demo"}:
        raise ValueError("KALSHI_ENV must be 'prod' or 'demo'")
    if not cfg.api_key_id:
        raise ValueError("Missing KALSHI_API_KEY_ID")
    if not cfg.private_key_path and not cfg.private_key_pem:
        raise ValueError("Missing private key: set KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY_PEM")


async def _sync_positions(client: KalshiClient, portfolio: PortfolioState) -> None:
    try:
        rows = await client.get_positions()
        portfolio.update_from_exchange_positions(rows)
    except Exception as exc:
        LOGGER.warning("positions sync failed err=%s", exc)


async def _startup_auth_check(client: KalshiClient) -> dict[str, Any]:
    try:
        return await client.get_balance()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise RuntimeError(
                "Kalshi auth failed (401). Check API key ID/private key pairing and request signing config."
            ) from exc
        raise


async def _sync_balance(client: KalshiClient, router: ExecutionRouter) -> None:
    try:
        payload = await client.get_balance()
        router.set_available_balance_cents(_extract_available_balance_cents(payload))
    except Exception as exc:
        LOGGER.warning("balance sync failed err=%s", exc)


def _extract_available_balance_cents(payload: dict[str, Any]) -> int | None:
    candidates = [
        "available_balance",
        "available_cash",
        "cash_balance",
        "balance",
    ]
    for key in candidates:
        value = payload.get(key)
        cents = _to_cents(value)
        if cents is not None:
            return cents

    nested = payload.get("balance")
    if isinstance(nested, dict):
        for key in candidates:
            cents = _to_cents(nested.get(key))
            if cents is not None:
                return cents
    return None


def _to_cents(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        if abs(value) > 100000:
            return value
        return int(value * 100)
    if isinstance(value, float):
        if abs(value) > 100000:
            return int(value)
        return int(round(value * 100))
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            val = float(cleaned)
            return int(round(val * 100))
        except ValueError:
            return None
    return None


async def _snapshot_from_ws_or_rest(
    client: KalshiClient,
    ws: KalshiWsFeed,
    ticker: str,
    prior: MarketSnapshot | None,
) -> MarketSnapshot:
    for _ in range(30):
        msg = await ws.get_message(timeout=0.0)
        if msg is None:
            break
        ws_snap = _extract_ws_snapshot(msg, ticker, prior)
        if ws_snap is not None:
            return ws_snap
    market = await client.get_market(ticker)
    return KalshiClient.parse_market_snapshot(market, ticker=ticker)


def _extract_ws_snapshot(
    message: dict[str, Any],
    expected_ticker: str,
    prior: MarketSnapshot | None,
) -> MarketSnapshot | None:
    payload = message.get("msg", message)
    if not isinstance(payload, dict):
        return None

    ticker = str(payload.get("market_ticker", payload.get("ticker", "")))
    if ticker != expected_ticker:
        return None

    yes_bid = _from_payload(payload, ("yes_bid", "best_yes_bid"))
    yes_ask = _from_payload(payload, ("yes_ask", "best_yes_ask"))
    no_bid = _from_payload(payload, ("no_bid", "best_no_bid"))
    no_ask = _from_payload(payload, ("no_ask", "best_no_ask"))
    bid_size = _from_payload(payload, ("bid_size", "yes_bid_size", "best_yes_bid_size"), default=0)
    ask_size = _from_payload(payload, ("ask_size", "yes_ask_size", "best_yes_ask_size"), default=0)

    if prior is not None:
        yes_bid = yes_bid if yes_bid is not None else prior.yes_bid
        yes_ask = yes_ask if yes_ask is not None else prior.yes_ask
        no_bid = no_bid if no_bid is not None else prior.no_bid
        no_ask = no_ask if no_ask is not None else prior.no_ask
        bid_size = bid_size if bid_size is not None else prior.bid_size
        ask_size = ask_size if ask_size is not None else prior.ask_size

    if None in {yes_bid, yes_ask, no_bid, no_ask}:
        return None

    status = str(payload.get("status", prior.status if prior else "open"))
    return MarketSnapshot(
        ticker=ticker,
        status=status,
        yes_bid=int(yes_bid),
        yes_ask=int(yes_ask),
        no_bid=int(no_bid),
        no_ask=int(no_ask),
        bid_size=int(bid_size or 0),
        ask_size=int(ask_size or 0),
        ts=datetime.now(timezone.utc),
        close_time=prior.close_time if prior else None,
        price_to_beat=prior.price_to_beat if prior else None,
    )


def _from_payload(payload: dict[str, Any], keys: tuple[str, ...], default: int | None = None) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


async def _reconcile_fills(
    client: KalshiClient,
    ticker: str,
    seen_fills: set[str],
    portfolio: PortfolioState,
    store: FileStore,
) -> None:
    try:
        orders = await client.list_orders(status="executed", ticker=ticker)
    except Exception as exc:
        LOGGER.warning("fill reconciliation skipped err=%s", exc)
        return

    for order in orders:
        order_id = str(order.get("order_id", ""))
        fill_count = int(order.get("fill_count", order.get("count", 0)) or 0)
        if not order_id or fill_count <= 0:
            continue

        fill_key = f"{order_id}:{fill_count}"
        if fill_key in seen_fills:
            continue
        seen_fills.add(fill_key)

        side = str(order.get("side", "yes"))
        action = str(order.get("action", "buy"))
        price = _extract_fill_price_cents(order, side)
        fee = _extract_fill_fee_cents(order)
        fill = Fill(
            order_id=order_id,
            client_order_id=str(order.get("client_order_id", "")),
            ticker=ticker,
            side="yes" if side == "yes" else "no",
            action="buy" if action == "buy" else "sell",
            price_cents=price,
            contracts=fill_count,
            fee_cents=fee,
            ts=datetime.now(timezone.utc),
        )
        portfolio.apply_fill(fill)
        payload = asdict(fill)
        payload["ts"] = fill.ts.isoformat()
        store.append_fill(payload)


def _extract_fill_price_cents(order: dict[str, Any], side: str) -> int:
    avg_fill = order.get("avg_fill_price")
    if avg_fill is not None:
        try:
            return int(avg_fill)
        except (TypeError, ValueError):
            pass
    price_key = "yes_price" if side == "yes" else "no_price"
    fallback_key = "no_price" if side == "yes" else "yes_price"
    for key in (price_key, fallback_key):
        value = order.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _extract_fill_fee_cents(order: dict[str, Any]) -> int:
    for key in ("fee", "taker_fees", "maker_fees"):
        value = order.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kalshi BTC 15m hybrid bot")
    parser.add_argument("--env-file", default=".env.local", help="Path to .env file")
    parser.add_argument("--config-yaml", default=None, help="Optional yaml config override")
    parser.add_argument("--flatten-now", action="store_true", help="Flatten current positions and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(env_file=args.env_file, yaml_file=args.config_yaml)
    if args.flatten_now:
        asyncio.run(flatten_now(cfg))
        return
    asyncio.run(run_bot(cfg))


async def flatten_now(cfg: BotConfig) -> None:
    setup_logging(cfg.log_dir)
    _validate_config(cfg)
    health = HealthState(ws_healthy=False)
    client = KalshiClient(cfg, health)
    try:
        positions = await client.get_positions()
        if not positions:
            LOGGER.info("no positions found; nothing to flatten")
            return

        for row in positions:
            ticker = str(row.get("ticker", ""))
            if not ticker:
                continue
            yes_qty = int(row.get("yes_count", row.get("yes_contracts", 0)) or 0)
            no_qty = int(row.get("no_count", row.get("no_contracts", 0)) or 0)
            if yes_qty <= 0 and no_qty <= 0:
                continue

            market = await client.get_market(ticker)
            snap = KalshiClient.parse_market_snapshot(market, ticker=ticker)
            if yes_qty > 0:
                payload = {
                    "ticker": ticker,
                    "client_order_id": f"flatten-yes-{int(datetime.now(timezone.utc).timestamp())}",
                    "side": "yes",
                    "action": "sell",
                    "yes_price": max(1, snap.yes_bid),
                    "count": yes_qty,
                    "type": "limit",
                    "time_in_force": "ioc",
                }
                await client.place_order(payload)
                LOGGER.info("flatten sent ticker=%s side=yes qty=%s", ticker, yes_qty)
            if no_qty > 0:
                payload = {
                    "ticker": ticker,
                    "client_order_id": f"flatten-no-{int(datetime.now(timezone.utc).timestamp())}",
                    "side": "no",
                    "action": "sell",
                    "no_price": max(1, snap.no_bid),
                    "count": no_qty,
                    "type": "limit",
                    "time_in_force": "ioc",
                }
                await client.place_order(payload)
                LOGGER.info("flatten sent ticker=%s side=no qty=%s", ticker, no_qty)
    finally:
        await client.aclose()


if __name__ == "__main__":
    main()
