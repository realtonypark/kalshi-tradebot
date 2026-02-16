from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import copysign, exp
from statistics import fmean

from src.config import BotConfig
from src.market_data.btc_ta import TAFeatures
from src.models import MarketSnapshot, SignalDecision


@dataclass(slots=True)
class FeatureState:
    weighted_mid: float
    imbalance: float
    momentum: float


class HybridStrategy:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self._mid_history: deque[float] = deque(maxlen=max(16, cfg.momentum_lookback * 3))
        self._direction_history: deque[str] = deque(maxlen=max(3, cfg.signal_confirmation_window))

    def evaluate(
        self,
        snap: MarketSnapshot,
        spot_price: float | None = None,
        ta_features: TAFeatures | None = None,
    ) -> SignalDecision:
        spread = max(0, snap.yes_ask - snap.yes_bid)
        if spread <= 0 or snap.yes_ask >= 100 or snap.no_ask >= 100:
            self._direction_history.append("flat")
            return SignalDecision("hold", "flat", 0.0, 50.0, ["illiquid_book"])

        if self.cfg.use_technical_analysis and ta_features is None:
            self._direction_history.append("flat")
            return SignalDecision("hold", "flat", 0.0, 50.0, ["ta_unavailable"])

        features = self._extract_features(snap)
        fair = self._fair_yes_price(features)
        self._mid_history.append(features.weighted_mid)

        score, reasons, score_5m, score_15m, is_choppy = self._directional_score(snap, features, spot_price, ta_features)
        fair_prob = self._calibrated_probability(score)
        strength = abs(fair_prob - 0.5) * 2.0
        fair = max(1.0, min(99.0, fair_prob * 100.0))

        if self.cfg.skip_choppy_regime and is_choppy:
            self._direction_history.append("flat")
            return SignalDecision("hold", "flat", strength, fair, ["choppy_regime", *reasons])

        if strength < self.cfg.directional_score_threshold:
            self._direction_history.append("flat")
            return SignalDecision("hold", "flat", strength, fair, ["low_directional_score"] + reasons)

        direction = "yes" if fair_prob >= 0.5 else "no"

        if self.cfg.use_technical_analysis and ta_features is not None and self.cfg.ta_require_5m_alignment:
            if abs(score_5m) < self.cfg.ta_5m_min_strength:
                self._direction_history.append("flat")
                return SignalDecision("hold", "flat", strength, fair, ["weak_5m_regime", *reasons])
            trend_5m_dir = "yes" if score_5m >= 0 else "no"
            if direction != trend_5m_dir:
                self._direction_history.append("flat")
                return SignalDecision("hold", "flat", strength, fair, ["5m_direction_conflict", *reasons])

        if self.cfg.use_technical_analysis and ta_features is not None and self.cfg.ta_require_15m_alignment:
            if abs(score_15m) < self.cfg.ta_15m_min_strength:
                self._direction_history.append("flat")
                return SignalDecision("hold", "flat", strength, fair, ["weak_15m_regime", *reasons])
            trend_15m_dir = "yes" if score_15m >= 0 else "no"
            if direction != trend_15m_dir:
                self._direction_history.append("flat")
                return SignalDecision(
                    "hold",
                    "flat",
                    strength,
                    fair,
                    ["15m_direction_conflict", *reasons],
                )

        agreement_bonus = 0.0
        if score * score_5m > 0:
            agreement_bonus += 0.05
        if score * score_15m > 0:
            agreement_bonus += 0.05
        confidence = min(1.0, strength + agreement_bonus)

        fair_edge = (fair - snap.yes_ask) if direction == "yes" else ((100.0 - fair) - snap.no_ask)
        taker_threshold = self.cfg.taker_min_edge_cents + self.cfg.fee_buffer_cents
        if not self._confirmed(direction):
            return SignalDecision("hold", "flat", confidence, fair, ["awaiting_evidence", *reasons])

        if self.cfg.force_directional_entries and confidence >= self.cfg.taker_confidence_threshold:
            return SignalDecision(
                mode="taker",
                side=direction,
                confidence=confidence,
                fair_yes_price=fair,
                reason_codes=["directional_force_entry", "probability_calibrated", *reasons],
            )

        if confidence >= self.cfg.taker_confidence_threshold and fair_edge >= taker_threshold:
            return SignalDecision(
                mode="taker",
                side=direction,
                confidence=confidence,
                fair_yes_price=fair,
                reason_codes=["directional_score", "probability_calibrated", *reasons, "edge_after_fees"],
            )

        # Fallback directional model: momentum + imbalance only.
        if abs(features.momentum) < self.cfg.momentum_min_cents:
            return SignalDecision("hold", "flat", 0.0, fair, ["weak_momentum"])

        direction = (
            "yes"
            if features.momentum > 0 and fair > 50
            else "no"
            if features.momentum < 0 and fair < 50
            else "flat"
        )
        if direction == "flat":
            return SignalDecision("hold", "flat", 0.0, fair, ["mixed_signal"])

        confidence = min(1.0, abs(features.momentum) * 2.2 + abs(features.imbalance) * 0.8)

        fair_edge = (fair - snap.yes_ask) if direction == "yes" else ((100.0 - fair) - snap.no_ask)
        taker_threshold = self.cfg.taker_min_edge_cents + self.cfg.fee_buffer_cents

        if confidence >= self.cfg.taker_confidence_threshold and fair_edge >= taker_threshold:
            return SignalDecision(
                mode="taker",
                side=direction,
                confidence=confidence,
                fair_yes_price=fair,
                reason_codes=["directional_momentum", "edge_after_fees"],
            )

        if not self.cfg.directional_only and spread >= self.cfg.maker_edge_cents * 2:
            return SignalDecision(
                mode="maker",
                side=direction,
                confidence=max(0.25, confidence),
                fair_yes_price=fair,
                reason_codes=["spread_capture"],
            )

        return SignalDecision("hold", "flat", confidence, fair, ["insufficient_edge", *reasons])

    def _extract_features(self, snap: MarketSnapshot) -> FeatureState:
        bid = max(1, snap.yes_bid)
        ask = min(99, snap.yes_ask)
        raw_depth_total = snap.bid_size + snap.ask_size
        if raw_depth_total <= 0:
            # Some endpoints omit top-book sizes; fallback to midpoint instead of collapsing to 0.
            weighted_mid = (bid + ask) / 2.0
            imbalance = 0.0
        else:
            weighted_mid = ((bid * snap.ask_size) + (ask * snap.bid_size)) / raw_depth_total
            imbalance = (snap.bid_size - snap.ask_size) / raw_depth_total

        momentum = 0.0
        if len(self._mid_history) >= 2:
            short_n = min(3, len(self._mid_history))
            long_n = min(max(4, self.cfg.momentum_lookback), len(self._mid_history))
            short_ma = fmean(list(self._mid_history)[-short_n:])
            long_ma = fmean(list(self._mid_history)[-long_n:])
            momentum = short_ma - long_ma

        return FeatureState(weighted_mid=weighted_mid, imbalance=imbalance, momentum=momentum)

    @staticmethod
    def _fair_yes_price(features: FeatureState) -> float:
        fair = features.weighted_mid + (features.imbalance * 4.0) + (features.momentum * 0.7)
        return max(1.0, min(99.0, fair))

    def _directional_score(
        self,
        snap: MarketSnapshot,
        micro: FeatureState,
        spot_price: float | None,
        ta: TAFeatures | None,
    ) -> tuple[float, list[str], float, float, bool]:
        score = 0.0
        score_5m = 0.0
        score_15m = 0.0
        reasons: list[str] = []
        is_choppy = False

        # Price-to-beat signal dominates for this contract.
        if spot_price is not None and snap.price_to_beat is not None and snap.price_to_beat > 0:
            gap = spot_price - snap.price_to_beat
            gap_score = _clamp(gap / 22.0, -2.0, 2.0)
            score += gap_score * 1.25
            reasons.append("price_to_beat_gap_up" if gap > 0 else "price_to_beat_gap_down")

        # Microstructure context.
        score += _clamp(micro.momentum / max(self.cfg.momentum_min_cents, 0.05), -1.5, 1.5) * 0.5
        score += _clamp(micro.imbalance, -1.0, 1.0) * 0.35

        # Technical-analysis context from BTC 1m candles.
        if self.cfg.use_technical_analysis and ta is not None:
            trend_sign = 1.0 if ta.ema_fast >= ta.ema_slow else -1.0
            macd_sign = 1.0 if ta.macd_hist >= 0 else -1.0
            rsi_norm = _clamp((ta.rsi - 50.0) / 18.0, -1.5, 1.5)
            mom_norm = _clamp(ta.momentum_5m / 18.0, -1.5, 1.5)
            vol_penalty = _clamp(ta.volatility_1m * 1800.0, 0.0, 0.9)

            score += trend_sign * 0.35
            score += macd_sign * 0.30
            score += rsi_norm * 0.20
            score += mom_norm * 0.25
            score -= copysign(vol_penalty, score) if score != 0 else 0.0
            reasons.extend(["ema_trend_1m", "macd_1m", "rsi_1m", "momentum_5m"])

            ema_fast_5m = ta.ema_fast_5m if ta.ema_fast_5m != 0 else ta.ema_fast
            ema_slow_5m = ta.ema_slow_5m if ta.ema_slow_5m != 0 else ta.ema_slow
            macd_5m_hist = ta.macd_hist_5m if ta.macd_hist_5m != 0 else ta.macd_hist
            rsi_5m = ta.rsi_5m if ta.rsi_5m != 50.0 else ta.rsi
            mom_5m_tf = ta.momentum_5m_tf if ta.momentum_5m_tf != 0 else ta.momentum_5m
            vol_5m = ta.volatility_5m if ta.volatility_5m > 0 else ta.volatility_1m

            trend_5 = 1.0 if ema_fast_5m >= ema_slow_5m else -1.0
            macd_5 = 1.0 if macd_5m_hist >= 0 else -1.0
            rsi_5 = _clamp((rsi_5m - 50.0) / 16.0, -1.6, 1.6)
            mom_5 = _clamp(mom_5m_tf / 28.0, -1.6, 1.6)
            vol_5_penalty = _clamp(vol_5m * 3500.0, 0.0, 0.8)
            score_5m = (trend_5 * 0.55) + (macd_5 * 0.40) + (rsi_5 * 0.35) + (mom_5 * 0.45) - vol_5_penalty
            score += score_5m
            reasons.extend(["ema_trend_5m", "macd_5m", "rsi_5m", "momentum_5m_tf"])

            # 15m chart regime: stronger weighting for higher timeframe confirmation.
            trend_15 = 1.0 if ta.ema_fast_15m >= ta.ema_slow_15m else -1.0
            macd_15 = 1.0 if ta.macd_hist_15m >= 0 else -1.0
            rsi_15 = _clamp((ta.rsi_15m - 50.0) / 16.0, -1.6, 1.6)
            mom_15 = _clamp(ta.momentum_15m / 45.0, -1.6, 1.6)
            score_15m = (trend_15 * 0.70) + (macd_15 * 0.55) + (rsi_15 * 0.45) + (mom_15 * 0.55)
            score += score_15m
            reasons.extend(["ema_trend_15m", "macd_15m", "rsi_15m", "momentum_15m"])

            # Penalize lower-timeframe direction if it conflicts with strong 15m trend.
            if abs(score_15m) >= 1.0 and (score * score_15m) < 0:
                score *= 0.45
                reasons.append("htf_conflict_penalty")

            trend_strength_bps = abs(ema_fast_5m - ema_slow_5m) / max(1.0, ta.spot) * 10000.0
            is_choppy = (
                trend_strength_bps < self.cfg.regime_min_trend_strength_bps
                and ta.volatility_1m >= self.cfg.regime_chop_vol_1m
            )
            if is_choppy:
                reasons.append("regime_choppy")

        return score, reasons, score_5m, score_15m, is_choppy

    def _calibrated_probability(self, score: float) -> float:
        slope = max(0.05, self.cfg.probability_calibration_slope)
        z = (score * slope) + self.cfg.probability_calibration_intercept
        raw = 1.0 / (1.0 + exp(-z))
        shrink = _clamp(self.cfg.probability_shrink, 0.0, 0.49)
        return 0.5 + ((raw - 0.5) * (1.0 - shrink))

    def _confirmed(self, direction: str) -> bool:
        self._direction_history.append(direction)
        needed = max(1, self.cfg.min_signal_confirmations)
        if len(self._direction_history) < needed:
            return False
        recent = list(self._direction_history)[-needed:]
        return all(item == direction for item in recent)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
