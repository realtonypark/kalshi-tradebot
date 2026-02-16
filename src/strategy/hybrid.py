from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import copysign
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

        score, reasons, htf_score = self._directional_score(snap, features, spot_price, ta_features)
        if abs(score) < self.cfg.directional_score_threshold:
            self._direction_history.append("flat")
            return SignalDecision("hold", "flat", min(1.0, abs(score)), fair, ["low_directional_score"] + reasons)

        if self.cfg.use_technical_analysis and ta_features is not None and self.cfg.ta_require_15m_alignment:
            if abs(htf_score) < self.cfg.ta_15m_min_strength:
                self._direction_history.append("flat")
                return SignalDecision("hold", "flat", min(1.0, abs(score)), fair, ["weak_15m_regime", *reasons])

        direction = "yes" if score > 0 else "no"
        if self.cfg.use_technical_analysis and ta_features is not None and self.cfg.ta_require_15m_alignment:
            htf_direction = "yes" if htf_score > 0 else "no"
            if direction != htf_direction:
                self._direction_history.append("flat")
                return SignalDecision(
                    "hold",
                    "flat",
                    min(1.0, abs(score)),
                    fair,
                    ["15m_direction_conflict", *reasons],
                )

        confidence = min(1.0, abs(score) / 1.6)
        fair = max(1.0, min(99.0, 50.0 + (score * 11.0)))
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
                reason_codes=["directional_force_entry", *reasons],
            )

        if confidence >= self.cfg.taker_confidence_threshold and fair_edge >= taker_threshold:
            return SignalDecision(
                mode="taker",
                side=direction,
                confidence=confidence,
                fair_yes_price=fair,
                reason_codes=["directional_score", *reasons, "edge_after_fees"],
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
    ) -> tuple[float, list[str], float]:
        score = 0.0
        htf_score = 0.0
        reasons: list[str] = []

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

            # 15m chart regime: stronger weighting for higher timeframe confirmation.
            trend_15 = 1.0 if ta.ema_fast_15m >= ta.ema_slow_15m else -1.0
            macd_15 = 1.0 if ta.macd_hist_15m >= 0 else -1.0
            rsi_15 = _clamp((ta.rsi_15m - 50.0) / 16.0, -1.6, 1.6)
            mom_15 = _clamp(ta.momentum_15m / 45.0, -1.6, 1.6)
            htf_score = (trend_15 * 0.70) + (macd_15 * 0.55) + (rsi_15 * 0.45) + (mom_15 * 0.55)
            score += htf_score
            reasons.extend(["ema_trend_15m", "macd_15m", "rsi_15m", "momentum_15m"])

            # Penalize lower-timeframe direction if it conflicts with strong 15m trend.
            if abs(htf_score) >= 1.0 and (score * htf_score) < 0:
                score *= 0.45
                reasons.append("htf_conflict_penalty")

        return score, reasons, htf_score

    def _confirmed(self, direction: str) -> bool:
        self._direction_history.append(direction)
        needed = max(1, self.cfg.min_signal_confirmations)
        if len(self._direction_history) < needed:
            return False
        recent = list(self._direction_history)[-needed:]
        return all(item == direction for item in recent)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
