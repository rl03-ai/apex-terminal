"""Early Signal Scanner.

Identifies assets at the START of a move, not tickers already extended.

Criteria (weighted):
  C1 (35%) Fundamentals + still cheap:
     - Insider buying > 60 in last 30d
     - Earnings surprise > +5% in last 90d
     - Distance from 52w high > 15%
     - total_score > 55

  C2 (25%) Breakout from compression:
     - Price broke above MA50 in last 10 days (was below for 60+)
     - Volume > 1.5x 20d average
     - Bollinger width compressed before breakout

  C3 (25%) Regime flip BASING -> UPTREND:
     - TrendChange regime changed in last 15 days
     - score_daily > 0 (was negative before)
     - From DOWNTREND/BASING -> UPTREND/STRONG_UPTREND

  C4 (15%) Score momentum:
     - score_slope_5d > 0
     - score_slope_20d > 0
     - state upgraded from dormant/broken

Gates:
  - total_score >= 45 (baseline quality)
  - at least 2 criteria pass
  - early_signal_score >= 70

Post-detection:
  - Track first_detected_price
  - Exit when price moves > 10% from first_detected_price
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta, datetime
from typing import Sequence

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CriteriaResult:
    passed: bool
    score: float                  # 0-100, score of this criterion
    details: list[str] = field(default_factory=list)


@dataclass
class EarlySignalScore:
    total: float                  # 0-100 composite score
    criteria_passed: list[str]    # names of passed criteria
    details: list[str]            # human-readable reasons
    c1_fundamentals: float
    c2_breakout: float
    c3_regime_flip: float
    c4_momentum: float
    qualifies: bool               # passes all gates
    total_score_value: float      # underlying structural score


WEIGHTS = {
    'c1_fundamentals': 0.35,
    'c2_breakout': 0.25,
    'c3_regime_flip': 0.25,
    'c4_momentum': 0.15,
}

MIN_TOTAL_SCORE = 45.0
MIN_SIGNAL_SCORE = 70.0
MIN_CRITERIA_COUNT = 2


# ─────────────────────────────────────────────────────────────────────────────
# Criteria evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_c1_fundamentals(
    score, events: list, technical
) -> CriteriaResult:
    """Insider + earnings + distance from 52w high + total_score."""
    details: list[str] = []
    points = 0
    max_points = 4

    cutoff_30d = datetime.utcnow() - timedelta(days=30)
    cutoff_90d = datetime.utcnow() - timedelta(days=90)

    # Insider buying in 30d
    insider_score = 0.0
    recent_insider = [e for e in events
                      if e.event_type == 'insider_buy'
                      and e.event_date >= cutoff_30d]
    if recent_insider:
        max_imp = max((e.importance_score or 0) for e in recent_insider)
        if max_imp >= 60:
            points += 1
            insider_score = max_imp
            details.append(f"Insider buying (importance {max_imp:.0f}) last 30d")

    # Earnings beat in 90d
    recent_earnings = [e for e in events
                       if e.event_type == 'earnings_result'
                       and e.event_date >= cutoff_90d
                       and (e.sentiment_score or 0) > 0.3]
    if recent_earnings:
        points += 1
        best = max(recent_earnings, key=lambda e: e.sentiment_score or 0)
        details.append(f"Earnings beat: {best.title}")

    # Distance from 52w high > 15%
    if technical and technical.distance_to_52w_high is not None:
        dist = abs(technical.distance_to_52w_high)
        if dist > 15:
            points += 1
            details.append(f"Still {dist:.0f}% from 52w high — room to run")

    # total_score > 55
    if score and score.total_score > 55:
        points += 1
        details.append(f"Quality score {score.total_score:.0f}")

    passed = points >= 3
    crit_score = (points / max_points) * 100
    return CriteriaResult(passed=passed, score=crit_score, details=details)


def _evaluate_c2_breakout(
    prices: Sequence, technical
) -> CriteriaResult:
    """Recent MA50 breakout with volume + prior compression."""
    details: list[str] = []
    if not prices or len(prices) < 60:
        return CriteriaResult(passed=False, score=0, details=['Insufficient price history'])

    ordered = sorted(prices, key=lambda p: p.date)
    closes = [p.close for p in ordered]
    volumes = [p.volume or 0 for p in ordered]

    # Compute MA50
    if len(closes) < 50:
        return CriteriaResult(passed=False, score=0, details=['No MA50 data'])

    def ma(series, n): return sum(series[-n:]) / n
    ma50_now = ma(closes, 50)

    # Was price below MA50 for most of last 60d, then recently crossed?
    recent_60d = closes[-60:] if len(closes) >= 60 else closes
    was_below = 0
    for i in range(max(0, len(recent_60d) - 60), len(recent_60d) - 10):
        idx = len(closes) - (len(recent_60d) - i)
        if idx < 50:
            continue
        past_ma = sum(closes[idx-50:idx]) / 50
        if closes[idx] < past_ma * 0.98:
            was_below += 1

    recently_crossed = False
    for i in range(len(closes) - 10, len(closes)):
        if i < 50:
            continue
        past_ma = sum(closes[i-50:i]) / 50
        if closes[i] > past_ma:
            recently_crossed = True
            break

    if was_below >= 30 and recently_crossed:
        details.append(f"Broke above MA50 after {was_below}d below")
    else:
        return CriteriaResult(passed=False, score=0, details=['No recent MA50 breakout'])

    # Volume confirmation
    vol_20d_avg = sum(volumes[-30:-10]) / 20 if len(volumes) >= 30 else 0
    vol_recent = sum(volumes[-10:]) / 10
    vol_ratio = vol_recent / vol_20d_avg if vol_20d_avg > 0 else 0
    volume_confirmed = vol_ratio > 1.3
    if volume_confirmed:
        details.append(f"Volume {vol_ratio:.1f}x avg confirms breakout")

    # Bollinger compression before breakout
    compression = False
    if len(closes) >= 80:
        # Stdev of last 10d before breakout
        pre_breakout = closes[-40:-10]
        if len(pre_breakout) >= 20:
            mean = sum(pre_breakout) / len(pre_breakout)
            var = sum((x - mean) ** 2 for x in pre_breakout) / len(pre_breakout)
            stdev = var ** 0.5
            width = (stdev / mean) if mean > 0 else 0
            # Typical range: >0.05 = normal, <0.03 = compressed
            compression = width < 0.035
            if compression:
                details.append(f"Compressed range before breakout (BB width {width*100:.1f}%)")

    # Score: 50 base for breakout + 30 volume + 20 compression
    points = 50 + (30 if volume_confirmed else 0) + (20 if compression else 0)
    passed = volume_confirmed or compression  # at least one confirmation
    return CriteriaResult(passed=passed, score=points, details=details)


def _evaluate_c3_regime_flip(score) -> CriteriaResult:
    """BASING/DOWNTREND → UPTREND in last 15 days."""
    details: list[str] = []
    if not score or not score.score_regime:
        return CriteriaResult(passed=False, score=0, details=['No regime data'])

    # Check current regime is bullish
    current_regime = score.score_regime.upper()
    is_bullish_now = 'UPTREND' in current_regime

    if not is_bullish_now:
        return CriteriaResult(passed=False, score=0, details=[f'Current regime {current_regime}'])

    # Check trajectory is rising
    trajectory = (score.score_trajectory or '').lower()
    is_rising = 'ris' in trajectory or 'improv' in trajectory

    # Check slopes positive
    slope_5d = score.score_slope_5d or 0
    slope_20d = score.score_slope_20d or 0

    passed = is_bullish_now and slope_5d > 0
    points = 0
    if is_bullish_now:
        points += 50
        details.append(f"Regime flipped to {current_regime}")
    if slope_5d > 0.5:
        points += 30
        details.append(f"5d slope +{slope_5d:.1f}")
    if slope_20d > 0:
        points += 20
        details.append(f"20d slope +{slope_20d:.1f}")
    if is_rising:
        points += 10
        details.append("Score trajectory rising")

    return CriteriaResult(passed=passed, score=min(points, 100), details=details)


def _evaluate_c4_momentum(score) -> CriteriaResult:
    """Score slopes + state upgrade."""
    details: list[str] = []
    if not score:
        return CriteriaResult(passed=False, score=0, details=['No score data'])

    slope_5d = score.score_slope_5d or 0
    slope_20d = score.score_slope_20d or 0
    state = (score.state or '').lower()

    good_states = {'active_setup', 'confirming', 'emerging'}
    state_ok = state in good_states

    points = 0
    if slope_5d > 0:
        points += 35
        details.append(f"5d slope +{slope_5d:.1f}")
    if slope_20d > 0:
        points += 35
        details.append(f"20d slope +{slope_20d:.1f}")
    if state_ok:
        points += 30
        details.append(f"State: {state}")

    passed = slope_5d > 0 and slope_20d > 0 and state_ok
    return CriteriaResult(passed=passed, score=points, details=details)


# ─────────────────────────────────────────────────────────────────────────────
# Main evaluator
# ─────────────────────────────────────────────────────────────────────────────

def compute_early_signal(
    score,
    events: list,
    prices: Sequence,
    technical,
) -> EarlySignalScore:
    """Evaluate all 4 criteria for a single asset."""
    total_score = score.total_score if score else 0

    # Gate 1: baseline quality
    if total_score < MIN_TOTAL_SCORE:
        return EarlySignalScore(
            total=0,
            criteria_passed=[],
            details=[f"Below minimum score ({total_score:.0f} < {MIN_TOTAL_SCORE})"],
            c1_fundamentals=0, c2_breakout=0, c3_regime_flip=0, c4_momentum=0,
            qualifies=False,
            total_score_value=total_score,
        )

    c1 = _evaluate_c1_fundamentals(score, events, technical)
    c2 = _evaluate_c2_breakout(prices, technical)
    c3 = _evaluate_c3_regime_flip(score)
    c4 = _evaluate_c4_momentum(score)

    total = (
        c1.score * WEIGHTS['c1_fundamentals']
        + c2.score * WEIGHTS['c2_breakout']
        + c3.score * WEIGHTS['c3_regime_flip']
        + c4.score * WEIGHTS['c4_momentum']
    )

    passed: list[str] = []
    if c1.passed: passed.append('fundamentals')
    if c2.passed: passed.append('breakout')
    if c3.passed: passed.append('regime_flip')
    if c4.passed: passed.append('momentum')

    details: list[str] = []
    if c1.passed: details.extend([f"[Fundamentals] {d}" for d in c1.details])
    if c2.passed: details.extend([f"[Breakout] {d}" for d in c2.details])
    if c3.passed: details.extend([f"[Regime flip] {d}" for d in c3.details])
    if c4.passed: details.extend([f"[Momentum] {d}" for d in c4.details])

    qualifies = (
        len(passed) >= MIN_CRITERIA_COUNT
        and total >= MIN_SIGNAL_SCORE
    )

    return EarlySignalScore(
        total=round(total, 1),
        criteria_passed=passed,
        details=details,
        c1_fundamentals=round(c1.score, 1),
        c2_breakout=round(c2.score, 1),
        c3_regime_flip=round(c3.score, 1),
        c4_momentum=round(c4.score, 1),
        qualifies=qualifies,
        total_score_value=total_score,
    )


def refresh_early_signals(db) -> dict:
    """Run the early signal scanner for all assets with scores."""
    from app.models.asset import (
        Asset, AssetScoreDaily, AssetEvent, AssetPriceDaily,
        AssetTechnicalSnapshot,
    )
    from app.models.early_signal import EarlySignal
    from sqlalchemy import desc
    from datetime import date as _date

    today = _date.today()
    exit_threshold_pct = 10.0

    # Get latest score per asset
    latest_scores = (
        db.query(AssetScoreDaily)
        .order_by(desc(AssetScoreDaily.date))
        .all()
    )
    latest_per_asset: dict[str, AssetScoreDaily] = {}
    for s in latest_scores:
        if s.asset_id not in latest_per_asset:
            latest_per_asset[s.asset_id] = s

    new_signals = 0
    exited_signals = 0
    active_signals = 0

    for asset_id, score in latest_per_asset.items():
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if not asset:
            continue

        prices = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == asset_id)
            .order_by(AssetPriceDaily.date.asc())
            .all()
        )
        if not prices:
            continue
        latest_price = prices[-1].close

        events = (
            db.query(AssetEvent)
            .filter(AssetEvent.asset_id == asset_id)
            .order_by(desc(AssetEvent.event_date))
            .limit(50)
            .all()
        )
        technical = (
            db.query(AssetTechnicalSnapshot)
            .filter(AssetTechnicalSnapshot.asset_id == asset_id)
            .order_by(desc(AssetTechnicalSnapshot.date))
            .first()
        )

        # Check if already tracking
        existing = (
            db.query(EarlySignal)
            .filter(EarlySignal.asset_id == asset_id, EarlySignal.is_active == True)
            .first()
        )

        if existing:
            # Check if should exit (price moved > 10%)
            pct_move = (latest_price - existing.first_detected_price) / existing.first_detected_price * 100
            existing.current_price = latest_price
            existing.pct_move_since = round(pct_move, 2)
            if abs(pct_move) > exit_threshold_pct:
                existing.is_active = False
                existing.exit_reason = (
                    f"Price moved {pct_move:+.1f}% since detection — movement started"
                )
                exited_signals += 1
                continue

            # Re-evaluate criteria
            result = compute_early_signal(score, events, prices, technical)
            if not result.qualifies:
                existing.is_active = False
                existing.exit_reason = "Criteria no longer met"
                exited_signals += 1
            else:
                existing.last_signal_date = today
                existing.signal_score = result.total
                existing.criteria_passed = ','.join(result.criteria_passed)
                existing.total_score = score.total_score
                active_signals += 1
        else:
            # New evaluation
            result = compute_early_signal(score, events, prices, technical)
            if result.qualifies:
                signal = EarlySignal(
                    asset_id=asset_id,
                    first_detected_date=today,
                    first_detected_price=latest_price,
                    last_signal_date=today,
                    current_price=latest_price,
                    pct_move_since=0.0,
                    signal_score=result.total,
                    criteria_passed=','.join(result.criteria_passed),
                    total_score=score.total_score,
                    is_active=True,
                )
                db.add(signal)
                new_signals += 1
                active_signals += 1

    db.commit()
    return {
        'new': new_signals,
        'exited': exited_signals,
        'total_active': active_signals,
    }
