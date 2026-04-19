"""Equity trend regime detector.

Adapts the TrendChangeDetector (originally crypto intraday) for equity investing:
  - Uses daily + weekly bars (not 4h/1h)
  - Outputs structural regime classification suitable for long-term investing
  - Used to enrich market_score and provide entry timing signals

Output regimes:
  STRONG_UPTREND   — momentum strong, all timeframes bullish, low risk of reversal
  UPTREND          — bullish bias confirmed
  TOPPING          — uptrend losing momentum, possible reversal
  RANGING          — no clear direction, choppy
  DOWNTREND        — bearish bias confirmed
  BASING           — downtrend losing momentum, possible bottoming
  UNKNOWN          — insufficient data

Entry timing signals:
  STRONG_BUY       — all factors aligned bullish, fresh breakout
  BUY              — uptrend with pullback (good entry)
  HOLD             — uptrend but extended (wait for pullback)
  CAUTION          — topping or losing momentum
  AVOID            — downtrend or basing without confirmation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TrendRegime:
    regime: str               # STRONG_UPTREND, UPTREND, TOPPING, RANGING, DOWNTREND, BASING, UNKNOWN
    entry_signal: str         # STRONG_BUY, BUY, HOLD, CAUTION, AVOID
    score_daily: float        # -5 to +5, raw composite score from detector
    score_weekly: float       # -5 to +5, weekly score
    confidence: float         # 0-1, signal strength
    reasons: list[str]        # human-readable explanation
    market_score_boost: float # -10 to +15, modifier to add to base market_score


def _classify_regime(score_d: float, score_w: float) -> str:
    """Map composite scores to a structural regime label."""
    if score_d > 1.5 and score_w > 1.0:
        return "STRONG_UPTREND"
    if score_d > 0.5 and score_w > 0.0:
        return "UPTREND"
    if score_d < -1.5 and score_w < -1.0:
        return "DOWNTREND"
    if score_d < -0.5 and score_w < 0.0:
        return "DOWNTREND"
    # Divergence cases
    if score_w > 0.5 and score_d < 0:
        return "TOPPING"
    if score_w < -0.5 and score_d > 0:
        return "BASING"
    return "RANGING"


def _classify_entry(regime: str, score_d: float, score_w: float, distance_high_pct: float) -> str:
    """Map regime + technicals to actionable entry signal.

    distance_high_pct = how far below 90-day high (0 = at high, 0.10 = 10% below)
    """
    if regime == "STRONG_UPTREND":
        if distance_high_pct > 0.05 and score_d > 1.0:
            return "BUY"  # pullback in strong uptrend
        if distance_high_pct < 0.02:
            return "HOLD"  # too close to highs
        return "STRONG_BUY"
    if regime == "UPTREND":
        if distance_high_pct > 0.07:
            return "BUY"
        return "HOLD"
    if regime == "TOPPING":
        return "CAUTION"
    if regime == "BASING":
        return "CAUTION"  # not yet confirmed
    if regime in ("DOWNTREND",):
        return "AVOID"
    return "HOLD"  # RANGING / UNKNOWN


def _market_score_boost(regime: str, confidence: float) -> float:
    """How much to modify market_score (additive, applied after base computation)."""
    base = {
        "STRONG_UPTREND": +12.0,
        "UPTREND":         +6.0,
        "TOPPING":         -3.0,
        "RANGING":          0.0,
        "BASING":          -2.0,
        "DOWNTREND":       -8.0,
        "UNKNOWN":          0.0,
    }.get(regime, 0.0)
    return base * confidence


def _last_score(df_out: pd.DataFrame) -> float:
    """Extract last finite score from detector output."""
    if df_out is None or df_out.empty or "score" not in df_out.columns:
        return 0.0
    s = df_out["score"].dropna()
    if s.empty:
        return 0.0
    val = float(s.iloc[-1])
    if not (val == val):  # NaN check
        return 0.0
    return max(-5.0, min(5.0, val))


def _distance_from_high(df_daily: pd.DataFrame, lookback: int = 90) -> float:
    """How far is current price below the lookback-period high (as fraction)."""
    if df_daily is None or df_daily.empty:
        return 0.0
    recent = df_daily.tail(lookback)
    if recent.empty:
        return 0.0
    high = recent["high"].max() if "high" in recent.columns else recent["close"].max()
    last = recent["close"].iloc[-1]
    if high <= 0:
        return 0.0
    return float(max(0.0, (high - last) / high))


def analyse_equity_trend(
    df_daily: pd.DataFrame,
    df_weekly: Optional[pd.DataFrame] = None,
    *,
    min_bars_daily: int = 60,
) -> TrendRegime:
    """
    Run trend analysis on equity OHLCV data.

    df_daily: DataFrame with columns [open, high, low, close, volume], indexed by date.
    df_weekly: optional weekly resampled DataFrame.
    """
    if df_daily is None or len(df_daily) < min_bars_daily:
        return TrendRegime(
            regime="UNKNOWN",
            entry_signal="HOLD",
            score_daily=0.0,
            score_weekly=0.0,
            confidence=0.0,
            reasons=[f"Insufficient daily data ({len(df_daily) if df_daily is not None else 0} bars)."],
            market_score_boost=0.0,
        )

    # Resample weekly if not provided
    if df_weekly is None or df_weekly.empty:
        try:
            df_weekly = df_daily.resample("W").agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna()
        except Exception as e:
            logger.debug("Weekly resample failed: %s", e)
            df_weekly = pd.DataFrame()

    try:
        from app.services.technical.trend_change import (
            TrendChangeDetector,
            TrendChangeConfig,
        )

        # Tune for daily equity data — slower thresholds
        cfg = TrendChangeConfig()

        detector = TrendChangeDetector(cfg)
        out_d = detector.detect(df_daily)
        score_d = _last_score(out_d)

        # Weekly run with same config (still works at higher TF)
        score_w = 0.0
        if not df_weekly.empty and len(df_weekly) >= 30:
            detector_w = TrendChangeDetector(cfg)
            out_w = detector_w.detect(df_weekly)
            score_w = _last_score(out_w)
        else:
            # Without weekly data, use daily as proxy with reduced weight
            score_w = score_d * 0.6

    except Exception as e:
        logger.warning("TrendChange analysis failed: %s", e)
        return TrendRegime(
            regime="UNKNOWN",
            entry_signal="HOLD",
            score_daily=0.0,
            score_weekly=0.0,
            confidence=0.0,
            reasons=[f"Detector error: {str(e)[:80]}"],
            market_score_boost=0.0,
        )

    regime = _classify_regime(score_d, score_w)
    distance_high = _distance_from_high(df_daily)
    entry_signal = _classify_entry(regime, score_d, score_w, distance_high)

    # Confidence = how aligned daily + weekly are
    if score_d * score_w > 0:  # same sign
        confidence = min(1.0, (abs(score_d) + abs(score_w)) / 4.0)
    else:
        confidence = max(0.0, (abs(score_d) - abs(score_w)) / 4.0) if abs(score_d) > abs(score_w) else 0.2

    boost = _market_score_boost(regime, confidence)

    reasons = [
        f"Daily score: {score_d:+.2f} | Weekly score: {score_w:+.2f}",
        f"Distance from 90d high: {distance_high*100:.1f}%",
    ]
    if regime == "STRONG_UPTREND":
        reasons.append("All factors aligned bullish — strong momentum.")
    elif regime == "UPTREND":
        reasons.append("Bullish bias on multiple timeframes.")
    elif regime == "TOPPING":
        reasons.append("Weekly bullish but daily losing momentum — possible top.")
    elif regime == "BASING":
        reasons.append("Daily improving while weekly still bearish — possible bottom.")
    elif regime == "DOWNTREND":
        reasons.append("Bearish bias confirmed — avoid until reversal signal.")
    elif regime == "RANGING":
        reasons.append("No clear direction — wait for breakout.")

    return TrendRegime(
        regime=regime,
        entry_signal=entry_signal,
        score_daily=score_d,
        score_weekly=score_w,
        confidence=round(confidence, 2),
        reasons=reasons,
        market_score_boost=round(boost, 1),
    )


def analyse_from_db(asset_id: str, db) -> TrendRegime:
    """Convenience: load prices from DB and run analysis."""
    from app.models.asset import AssetPriceDaily
    rows = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == asset_id)
        .order_by(AssetPriceDaily.date.asc())
        .all()
    )
    if not rows or len(rows) < 60:
        return TrendRegime(
            regime="UNKNOWN", entry_signal="HOLD",
            score_daily=0.0, score_weekly=0.0,
            confidence=0.0, market_score_boost=0.0,
            reasons=[f"Insufficient price history ({len(rows)} days)."],
        )

    df = pd.DataFrame([{
        "date":   r.date,
        "open":   r.open,
        "high":   r.high,
        "low":    r.low,
        "close":  r.close,
        "volume": r.volume or 0,
    } for r in rows])
    df = df.set_index(pd.to_datetime(df["date"])).drop(columns=["date"])

    return analyse_equity_trend(df)
