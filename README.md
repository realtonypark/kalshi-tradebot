# Kalshi BTC/ETH 15m Hybrid Bot

Live-first trading bot for Kalshi BTC/ETH 15-minute up/down markets with strict risk controls, auto-roll, flat-file persistence, and a 5-second terminal dashboard.

## Safety Notes

- This bot can lose money. It is not guaranteed to be profitable.
- Start with `PAPER_MODE=true` until you validate behavior.
- Never commit `.env.local` or private keys.

## Setup

1. Copy environment template:
   - `cp .env.example .env.local`
2. Set real credentials in `.env.local`:
   - `KALSHI_API_KEY_ID`
   - `KALSHI_PRIVATE_KEY_PATH` or `KALSHI_PRIVATE_KEY_PEM`
   - `MARKET_SEED_TICKERS` for multi-market trading (example: BTC + ETH)
3. Install dependencies:
   - `python3 -m pip install -e ".[dev]"`

## Run

- `./run_bot.sh`
- or `python3 -m src.main --env-file .env.local`
- flatten positions and exit: `python3 -m src.main --env-file .env.local --flatten-now`

## launchd Example

Create `~/Library/LaunchAgents/com.local.kalshi-btc15m.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.local.kalshi-btc15m</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/zsh</string>
      <string>-lc</string>
      <string>cd /Users/realtonypark/Developer/printer && ./run_bot.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/realtonypark/Developer/printer/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/realtonypark/Developer/printer/logs/launchd.err.log</string>
  </dict>
</plist>
```

Load it:

- `launchctl unload ~/Library/LaunchAgents/com.local.kalshi-btc15m.plist 2>/dev/null || true`
- `launchctl load ~/Library/LaunchAgents/com.local.kalshi-btc15m.plist`

## Notes About Kalshi API Shapes

Some payload fields differ by endpoint version. The client normalizes common alternatives for market quotes, positions, and order fields, and fails closed when risk checks are not satisfied.

## 15m Directional Signal

- The bot now prioritizes `price_to_beat` + live BTC/ETH spot to decide `YES` (up) vs `NO` (down).
- It can run multiple 15m markets in parallel from `MARKET_SEED_TICKERS` (e.g., BTC + ETH).
- It only places directional taker entries when confidence and edge clear thresholds.
- It uses multi-timeframe confirmation (`1m + 5m + 15m`) and can veto choppy regimes.
- Directional fair value is probability-calibrated before EV/sizing checks.
- The directional entry can require 15m chart alignment (higher-timeframe regime filter).
- It can trade only at session start (first part of each new 15-minute market).
- It blocks trades with weak payout/EV after estimated fees.
- Position size is EV/Kelly-aware and still bounded by strict hard caps.
- You can tune in `.env.local`:
  - `DIRECTIONAL_ONLY`
  - `MOMENTUM_LOOKBACK`
  - `MOMENTUM_MIN_CENTS`
  - `TAKER_CONFIDENCE_THRESHOLD`
  - `TAKER_MIN_EDGE_CENTS`
  - `ENTRY_COOLDOWN_SEC`
  - `TA_REQUIRE_15M_ALIGNMENT`
  - `TA_15M_MIN_STRENGTH`
  - `TA_REQUIRE_5M_ALIGNMENT`
  - `TA_5M_MIN_STRENGTH`
  - `SKIP_CHOPPY_REGIME`
  - `REGIME_MIN_TREND_STRENGTH_BPS`
  - `REGIME_CHOP_VOL_1M`
  - `PROBABILITY_CALIBRATION_SLOPE`
  - `PROBABILITY_CALIBRATION_INTERCEPT`
  - `PROBABILITY_SHRINK`
  - `ENTRY_AT_SESSION_START_ONLY`
  - `SESSION_START_ENTRY_WINDOW_SEC`
  - `MIN_WIN_PROFIT_CENTS`
  - `MIN_EXPECTED_VALUE_CENTS`
  - `ASSUMED_SLIPPAGE_CENTS`
  - `EV_SAFETY_CENTS`
  - `BASE_TRADE_RISK_PCT`
  - `MAX_TRADE_RISK_PCT`
  - `KELLY_FRACTION`
  - `SIZE_TARGET_EV_CENTS`

## Troubleshooting

- `401 Unauthorized` on `/portfolio/*`:
  - Verify `KALSHI_API_KEY_ID` matches the exact private key in `KALSHI_PRIVATE_KEY_PATH`.
  - If using inline key, verify `KALSHI_PRIVATE_KEY_PEM` is complete and contains valid `\\n` line breaks.
  - Ensure key file is readable PEM and not truncated.
  - Restart the bot; startup now performs a private auth check and fails fast on bad auth.
- `CERTIFICATE_VERIFY_FAILED` on websocket:
  - Keep `KALSHI_WS_VERIFY_TLS=true` for production.
  - If your local Python cert store is broken, set `KALSHI_WS_CA_BUNDLE_PATH` to a valid CA bundle path.
  - Temporary local debug only: `KALSHI_WS_VERIFY_TLS=false` (not recommended for real trading).
