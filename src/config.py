from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    from dotenv import load_dotenv as _dotenv_load
except ModuleNotFoundError:
    _dotenv_load = None


@dataclass(slots=True)
class BotConfig:
    kalshi_env: Literal["prod", "demo"] = "prod"
    api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    ws_verify_tls: bool = True
    ws_ca_bundle_path: str = ""
    api_key_id: str = ""
    private_key_path: str = ""
    private_key_pem: str = ""
    market_seed_ticker: str = "kxbtc15m-26feb151715"
    market_seed_tickers: str = ""
    auto_roll: bool = True
    paper_mode: bool = False
    bankroll_usd: float = 3000.0
    max_daily_loss_pct: float = 0.025
    max_gross_exposure_pct: float = 0.20
    max_market_exposure_pct: float = 0.06
    max_bet_pct: float = 0.33
    dashboard_interval_sec: int = 5
    kill_switch_file: str = "/Users/realtonypark/Developer/printer/.halt_trading"
    max_spread_cents: int = 8
    min_top_book_depth: int = 20
    max_rest_latency_ms: int = 800
    stale_feed_sec: int = 4
    market_close_buffer_sec: int = 45
    trade_start_before_close_sec: int = 600
    entry_at_session_start_only: bool = True
    session_duration_sec: int = 900
    session_start_entry_window_sec: int = 120
    mandatory_session_entry: bool = True
    mandatory_entry_contracts: int = 1
    max_consecutive_api_errors: int = 5
    max_initial_order_contracts: int = 25
    maker_edge_cents: int = 2
    taker_confidence_threshold: float = 0.78
    taker_min_edge_cents: int = 3
    fee_buffer_cents: int = 1
    directional_only: bool = True
    force_directional_entries: bool = True
    momentum_lookback: int = 8
    momentum_min_cents: float = 0.35
    entry_cooldown_sec: int = 20
    use_technical_analysis: bool = True
    ta_refresh_sec: int = 5
    ta_require_5m_alignment: bool = True
    ta_5m_min_strength: float = 0.45
    ta_require_15m_alignment: bool = True
    ta_15m_min_strength: float = 0.6
    skip_choppy_regime: bool = True
    regime_min_trend_strength_bps: float = 4.0
    regime_chop_vol_1m: float = 0.001
    probability_calibration_slope: float = 0.85
    probability_calibration_intercept: float = 0.0
    probability_shrink: float = 0.18
    directional_score_threshold: float = 0.55
    min_signal_confirmations: int = 3
    signal_confirmation_window: int = 6
    assumed_fee_per_contract_cents: int = 1
    assumed_slippage_cents: int = 1
    min_win_profit_cents: int = 2
    min_expected_value_cents: float = 0.25
    ev_safety_cents: float = 0.15
    base_trade_risk_pct: float = 0.0015
    max_trade_risk_pct: float = 0.01
    kelly_fraction: float = 0.20
    size_target_ev_cents: float = 2.5
    data_dir: str = "/Users/realtonypark/Developer/printer/data"
    log_dir: str = "/Users/realtonypark/Developer/printer/logs"

    @property
    def daily_loss_limit_usd(self) -> float:
        return self.bankroll_usd * self.max_daily_loss_pct

    @property
    def gross_exposure_limit_usd(self) -> float:
        return self.bankroll_usd * self.max_gross_exposure_pct

    @property
    def market_exposure_limit_usd(self) -> float:
        return self.bankroll_usd * min(self.max_market_exposure_pct, self.max_bet_pct)

    @property
    def seed_tickers(self) -> list[str]:
        return _parse_seed_tickers(self.market_seed_tickers, self.market_seed_ticker)


def _as_bool(value: str | bool, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def load_config(env_file: str = ".env.local", yaml_file: str | None = None) -> BotConfig:
    _load_env_file(env_file)
    merged: dict[str, Any] = {}
    if yaml_file:
        merged.update(_load_yaml_config(Path(yaml_file)))
    defaults = BotConfig()

    def env(key: str, default: Any) -> Any:
        if key in os.environ:
            return os.environ[key]
        return merged.get(key, default)

    cfg = BotConfig(
        kalshi_env=str(env("KALSHI_ENV", "prod")).lower(),
        api_base_url=str(env("KALSHI_API_BASE_URL", defaults.api_base_url)),
        ws_url=str(env("KALSHI_WS_URL", defaults.ws_url)),
        ws_verify_tls=_as_bool(env("KALSHI_WS_VERIFY_TLS", defaults.ws_verify_tls), defaults.ws_verify_tls),
        ws_ca_bundle_path=str(env("KALSHI_WS_CA_BUNDLE_PATH", defaults.ws_ca_bundle_path)),
        api_key_id=str(env("KALSHI_API_KEY_ID", "")),
        private_key_path=str(env("KALSHI_PRIVATE_KEY_PATH", "")),
        private_key_pem=str(env("KALSHI_PRIVATE_KEY_PEM", env("KALSHI_PRIVATE_KEY", defaults.private_key_pem))),
        market_seed_ticker=str(env("MARKET_SEED_TICKER", defaults.market_seed_ticker)).upper(),
        market_seed_tickers=str(env("MARKET_SEED_TICKERS", defaults.market_seed_tickers)),
        auto_roll=_as_bool(env("AUTO_ROLL", defaults.auto_roll), defaults.auto_roll),
        paper_mode=_as_bool(env("PAPER_MODE", defaults.paper_mode), defaults.paper_mode),
        bankroll_usd=float(env("BANKROLL_USD", defaults.bankroll_usd)),
        max_daily_loss_pct=float(env("MAX_DAILY_LOSS_PCT", defaults.max_daily_loss_pct)),
        max_gross_exposure_pct=float(env("MAX_GROSS_EXPOSURE_PCT", defaults.max_gross_exposure_pct)),
        max_market_exposure_pct=float(env("MAX_MARKET_EXPOSURE_PCT", defaults.max_market_exposure_pct)),
        max_bet_pct=float(env("MAX_BET_PCT", defaults.max_bet_pct)),
        dashboard_interval_sec=int(env("DASHBOARD_INTERVAL_SEC", defaults.dashboard_interval_sec)),
        kill_switch_file=str(env("KILL_SWITCH_FILE", defaults.kill_switch_file)),
        max_spread_cents=int(env("MAX_SPREAD_CENTS", defaults.max_spread_cents)),
        min_top_book_depth=int(env("MIN_TOP_BOOK_DEPTH", defaults.min_top_book_depth)),
        max_rest_latency_ms=int(env("MAX_REST_LATENCY_MS", defaults.max_rest_latency_ms)),
        stale_feed_sec=int(env("STALE_FEED_SEC", defaults.stale_feed_sec)),
        market_close_buffer_sec=int(env("MARKET_CLOSE_BUFFER_SEC", defaults.market_close_buffer_sec)),
        trade_start_before_close_sec=int(env("TRADE_START_BEFORE_CLOSE_SEC", defaults.trade_start_before_close_sec)),
        entry_at_session_start_only=_as_bool(
            env("ENTRY_AT_SESSION_START_ONLY", defaults.entry_at_session_start_only),
            defaults.entry_at_session_start_only,
        ),
        session_duration_sec=int(env("SESSION_DURATION_SEC", defaults.session_duration_sec)),
        session_start_entry_window_sec=int(
            env("SESSION_START_ENTRY_WINDOW_SEC", defaults.session_start_entry_window_sec)
        ),
        mandatory_session_entry=_as_bool(
            env("MANDATORY_SESSION_ENTRY", defaults.mandatory_session_entry),
            defaults.mandatory_session_entry,
        ),
        mandatory_entry_contracts=int(env("MANDATORY_ENTRY_CONTRACTS", defaults.mandatory_entry_contracts)),
        max_consecutive_api_errors=int(env("MAX_CONSECUTIVE_API_ERRORS", defaults.max_consecutive_api_errors)),
        max_initial_order_contracts=int(env("MAX_INITIAL_ORDER_CONTRACTS", defaults.max_initial_order_contracts)),
        maker_edge_cents=int(env("MAKER_EDGE_CENTS", defaults.maker_edge_cents)),
        taker_confidence_threshold=float(env("TAKER_CONFIDENCE_THRESHOLD", defaults.taker_confidence_threshold)),
        taker_min_edge_cents=int(env("TAKER_MIN_EDGE_CENTS", defaults.taker_min_edge_cents)),
        fee_buffer_cents=int(env("FEE_BUFFER_CENTS", defaults.fee_buffer_cents)),
        directional_only=_as_bool(env("DIRECTIONAL_ONLY", defaults.directional_only), defaults.directional_only),
        force_directional_entries=_as_bool(
            env("FORCE_DIRECTIONAL_ENTRIES", defaults.force_directional_entries), defaults.force_directional_entries
        ),
        momentum_lookback=int(env("MOMENTUM_LOOKBACK", defaults.momentum_lookback)),
        momentum_min_cents=float(env("MOMENTUM_MIN_CENTS", defaults.momentum_min_cents)),
        entry_cooldown_sec=int(env("ENTRY_COOLDOWN_SEC", defaults.entry_cooldown_sec)),
        use_technical_analysis=_as_bool(
            env("USE_TECHNICAL_ANALYSIS", defaults.use_technical_analysis), defaults.use_technical_analysis
        ),
        ta_refresh_sec=int(env("TA_REFRESH_SEC", defaults.ta_refresh_sec)),
        ta_require_5m_alignment=_as_bool(
            env("TA_REQUIRE_5M_ALIGNMENT", defaults.ta_require_5m_alignment), defaults.ta_require_5m_alignment
        ),
        ta_5m_min_strength=float(env("TA_5M_MIN_STRENGTH", defaults.ta_5m_min_strength)),
        ta_require_15m_alignment=_as_bool(
            env("TA_REQUIRE_15M_ALIGNMENT", defaults.ta_require_15m_alignment), defaults.ta_require_15m_alignment
        ),
        ta_15m_min_strength=float(env("TA_15M_MIN_STRENGTH", defaults.ta_15m_min_strength)),
        skip_choppy_regime=_as_bool(env("SKIP_CHOPPY_REGIME", defaults.skip_choppy_regime), defaults.skip_choppy_regime),
        regime_min_trend_strength_bps=float(
            env("REGIME_MIN_TREND_STRENGTH_BPS", defaults.regime_min_trend_strength_bps)
        ),
        regime_chop_vol_1m=float(env("REGIME_CHOP_VOL_1M", defaults.regime_chop_vol_1m)),
        probability_calibration_slope=float(
            env("PROBABILITY_CALIBRATION_SLOPE", defaults.probability_calibration_slope)
        ),
        probability_calibration_intercept=float(
            env("PROBABILITY_CALIBRATION_INTERCEPT", defaults.probability_calibration_intercept)
        ),
        probability_shrink=float(env("PROBABILITY_SHRINK", defaults.probability_shrink)),
        directional_score_threshold=float(env("DIRECTIONAL_SCORE_THRESHOLD", defaults.directional_score_threshold)),
        min_signal_confirmations=int(env("MIN_SIGNAL_CONFIRMATIONS", defaults.min_signal_confirmations)),
        signal_confirmation_window=int(env("SIGNAL_CONFIRMATION_WINDOW", defaults.signal_confirmation_window)),
        assumed_fee_per_contract_cents=int(
            env("ASSUMED_FEE_PER_CONTRACT_CENTS", defaults.assumed_fee_per_contract_cents)
        ),
        assumed_slippage_cents=int(env("ASSUMED_SLIPPAGE_CENTS", defaults.assumed_slippage_cents)),
        min_win_profit_cents=int(env("MIN_WIN_PROFIT_CENTS", defaults.min_win_profit_cents)),
        min_expected_value_cents=float(env("MIN_EXPECTED_VALUE_CENTS", defaults.min_expected_value_cents)),
        ev_safety_cents=float(env("EV_SAFETY_CENTS", defaults.ev_safety_cents)),
        base_trade_risk_pct=float(env("BASE_TRADE_RISK_PCT", defaults.base_trade_risk_pct)),
        max_trade_risk_pct=float(env("MAX_TRADE_RISK_PCT", defaults.max_trade_risk_pct)),
        kelly_fraction=float(env("KELLY_FRACTION", defaults.kelly_fraction)),
        size_target_ev_cents=float(env("SIZE_TARGET_EV_CENTS", defaults.size_target_ev_cents)),
        data_dir=str(env("DATA_DIR", defaults.data_dir)),
        log_dir=str(env("LOG_DIR", defaults.log_dir)),
    )
    return cfg


def _load_env_file(path: str) -> None:
    if _dotenv_load is not None:
        _dotenv_load(path)
        return
    _load_env_file_fallback(path)


def _load_env_file_fallback(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _parse_seed_tickers(raw_csv: str, fallback: str) -> list[str]:
    seed_raw = raw_csv.strip()
    tokens = [fallback]
    if seed_raw:
        tokens = [part.strip() for part in seed_raw.split(",") if part.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        ticker = token.upper()
        if ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out
