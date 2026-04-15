"""Score evolution tracker.

Analyses the time series of daily scores for each asset to detect:

  1. Trajectory — the direction and velocity of score change:
       breakout    : score rising fast, recently crossed a threshold
       topping     : score high but decelerating or reversing
       breakdown   : score falling fast, lost key level
       bottoming   : score low but decelerating decline / tentative recovery
       recovery    : score was low, now rising consistently
       plateau     : score stable (low slope, low volatility)

  2. Regime — derived from trajectory + absolute percentile level:
       STRONG_UPTREND    : breakout + top 25%
       UPTREND           : recovery or plateau + top 50%
       TOPPING           : topping signal in top 25%
       RANGING           : plateau in middle 50%
       DOWNTREND         : breakdown or plateau in bottom 50%
       BASING            : bottoming in bottom 25%

  3. Regime change — fired when regime is different from yesterday.
     Stored as an AssetEvent with event_type='regime_change'.

Technical methodology:
  - Linear regression slope over 5d, 10d, 20d windows
  - Slope normalised to score units per day
  - Acceleration = slope_5d - slope_20d (positive = accelerating up)
  - Volatility = standard deviation of score over 20d
  - Trajectory inferred from (slope, acceleration, current percentile, volatility)

Stored fields (added to AssetScoreDaily):
  score_slope_5d   : slope of linear regression over last 5 daily scores
  score_slope_20d  : slope over last 20 daily scores
  score_trajectory : string label (see above)
  score_regime     : regime label (see above)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import mean, stdev
from typing import Sequence

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScorePoint:
    date: date
    total_score: float
    score_percentile: float | None = None


@dataclass
class EvolutionResult:
    slope_5d: float
    slope_20d: float
    acceleration: float          # slope_5d - slope_20d
    volatility_20d: float        # stdev of scores over 20d
    trajectory: str
    regime: str
    regime_changed: bool         # True if regime differs from previous stored
    score_delta_5d: float        # simple arithmetic difference score[0] - score[-5]
    score_delta_20d: float


# ─────────────────────────────────────────────────────────────────────────────
# Linear regression slope
# ─────────────────────────────────────────────────────────────────────────────

def _linear_slope(values: list[float]) -> float:
    """
    Return the slope (units/step) of a simple OLS regression y = a + bx.
    x = [0, 1, ..., n-1], y = values.
    Returns 0.0 if fewer than 2 points.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = mean(values)
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Trajectory classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify_trajectory(
    slope_5d: float,
    slope_20d: float,
    acceleration: float,
    volatility: float,
    current_pct: float,
    current_score: float,
) -> str:
    """
    Classify the score trajectory into one of 6 states.

    Thresholds are in score-units-per-day.
    A typical "significant" move is ±0.5 score/day.
    """
    fast_rise  = slope_5d > 0.5
    fast_fall  = slope_5d < -0.5
    rising     = slope_5d > 0.15
    falling    = slope_5d < -0.15
    flat       = abs(slope_5d) <= 0.15
    accel_up   = acceleration > 0.3
    accel_down = acceleration < -0.3

    high_zone = current_pct >= 65
    low_zone  = current_pct <= 35

    if fast_rise or (rising and accel_up and not high_zone):
        return 'breakout'
    if high_zone and (fast_fall or (falling and accel_down)):
        return 'topping'
    if fast_fall or (falling and accel_down and not low_zone):
        return 'breakdown'
    if low_zone and (rising or (flat and accel_up)):
        return 'bottoming'
    if (rising or accel_up) and not high_zone:
        return 'recovery'
    return 'plateau'


def _derive_regime(trajectory: str, percentile: float) -> str:
    top_q  = percentile >= 75
    mid    = 25 <= percentile < 75
    bot_q  = percentile < 25

    if trajectory == 'breakout' and top_q:
        return 'STRONG_UPTREND'
    if trajectory in ('breakout', 'recovery') and not bot_q:
        return 'UPTREND'
    if trajectory == 'topping' and top_q:
        return 'TOPPING'
    if trajectory == 'plateau' and mid:
        return 'RANGING'
    if trajectory in ('breakdown', 'plateau') and not top_q:
        return 'DOWNTREND'
    if trajectory == 'bottoming' and bot_q:
        return 'BASING'
    # Fallback
    if top_q:
        return 'UPTREND'
    if bot_q:
        return 'DOWNTREND'
    return 'RANGING'


# ─────────────────────────────────────────────────────────────────────────────
# Main computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_evolution(
    history: Sequence[ScorePoint],
    previous_regime: str | None = None,
) -> EvolutionResult:
    """
    Compute score evolution metrics from a sequence of ScorePoints.

    Parameters
    ----------
    history         : ordered oldest-first, min 2 points needed
    previous_regime : yesterday's regime label (for change detection)
    """
    if not history:
        return EvolutionResult(
            slope_5d=0.0, slope_20d=0.0, acceleration=0.0,
            volatility_20d=0.0, trajectory='plateau', regime='RANGING',
            regime_changed=False, score_delta_5d=0.0, score_delta_20d=0.0,
        )

    scores = [p.total_score for p in history]
    current_pct = history[-1].score_percentile or 50.0

    scores_5d  = scores[-5:]  if len(scores) >= 5  else scores
    scores_20d = scores[-20:] if len(scores) >= 20 else scores

    slope_5d  = _linear_slope(scores_5d)
    slope_20d = _linear_slope(scores_20d)
    acceleration = round(slope_5d - slope_20d, 4)
    volatility = round(stdev(scores_20d), 3) if len(scores_20d) >= 2 else 0.0

    delta_5d  = round(scores[-1] - scores[-min(5, len(scores))],  2)
    delta_20d = round(scores[-1] - scores[-min(20, len(scores))], 2)

    trajectory = _classify_trajectory(
        slope_5d, slope_20d, acceleration, volatility,
        current_pct, scores[-1],
    )
    regime = _derive_regime(trajectory, current_pct)
    regime_changed = (previous_regime is not None and regime != previous_regime)

    return EvolutionResult(
        slope_5d=slope_5d,
        slope_20d=slope_20d,
        acceleration=acceleration,
        volatility_20d=volatility,
        trajectory=trajectory,
        regime=regime,
        regime_changed=regime_changed,
        score_delta_5d=delta_5d,
        score_delta_20d=delta_20d,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DB integration
# ─────────────────────────────────────────────────────────────────────────────

def compute_and_store_evolution(db, asset_id: str, as_of: date) -> EvolutionResult | None:
    """
    Load score history for an asset, compute evolution, write back to DB.
    Returns the EvolutionResult or None if insufficient data.
    """
    from app.models.asset import AssetEvent, AssetScoreDaily

    # Load last 30 scoring days
    rows = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == asset_id)
        .order_by(AssetScoreDaily.date.asc())
        .limit(30)
        .all()
    )

    if len(rows) < 2:
        return None

    history = [
        ScorePoint(
            date=r.date,
            total_score=float(r.total_score),
            score_percentile=float(r.score_percentile) if r.score_percentile is not None else None,
        )
        for r in rows
    ]

    # Get yesterday's regime for change detection
    previous_regime = None
    if len(rows) >= 2:
        prev_row = rows[-2]
        previous_regime = getattr(prev_row, 'score_regime', None)

    result = compute_evolution(history, previous_regime=previous_regime)

    # Write back to today's score row
    today_row = next((r for r in rows if r.date == as_of), None)
    if today_row is not None:
        today_row.score_slope_5d = result.slope_5d
        today_row.score_slope_20d = result.slope_20d
        today_row.score_trajectory = result.trajectory
        today_row.score_regime = result.regime

    # If regime changed, create an AssetEvent
    if result.regime_changed:
        event = AssetEvent(
            asset_id=asset_id,
            event_type='regime_change',
            event_date=datetime.combine(as_of, datetime.min.time()).replace(tzinfo=timezone.utc),
            title=f"Regime change → {result.regime}",
            summary=(
                f"Score regime changed from {previous_regime} to {result.regime}. "
                f"Trajectory: {result.trajectory}. "
                f"Slope 5d: {result.slope_5d:+.2f}, 20d: {result.slope_20d:+.2f}. "
                f"Δscore 20d: {result.score_delta_20d:+.1f}."
            ),
            sentiment_score=_regime_to_sentiment(result.regime),
            importance_score=_regime_change_importance(previous_regime, result.regime),
            source='evolution_engine',
            external_id=f"regime_{asset_id}_{as_of}",
        )
        # Upsert — avoid duplicates
        existing = (
            db.query(AssetEvent)
            .filter(
                AssetEvent.asset_id == asset_id,
                AssetEvent.event_type == 'regime_change',
                AssetEvent.external_id == event.external_id,
            )
            .first()
        )
        if not existing:
            db.add(event)
        logger.info(
            "Regime change %s: %s → %s (trajectory=%s)",
            asset_id, previous_regime, result.regime, result.trajectory,
        )

    return result


def _regime_to_sentiment(regime: str) -> float:
    return {
        'STRONG_UPTREND': 0.80,
        'UPTREND': 0.50,
        'TOPPING': -0.20,
        'RANGING': 0.0,
        'DOWNTREND': -0.50,
        'BASING': 0.10,
    }.get(regime, 0.0)


def _regime_change_importance(previous: str | None, current: str) -> float:
    """Higher importance for transitions between extreme states."""
    if previous is None:
        return 50.0
    strong = {'STRONG_UPTREND', 'DOWNTREND', 'BASING'}
    if previous in strong or current in strong:
        return 80.0
    if previous != current:
        return 65.0
    return 45.0


# ─────────────────────────────────────────────────────────────────────────────
# Batch run for all assets
# ─────────────────────────────────────────────────────────────────────────────

def run_evolution_for_all(db, as_of: date) -> dict:
    """Run evolution computation for all assets scored today. Called from daily_scoring."""
    from app.models.asset import Asset, AssetScoreDaily

    assets = db.query(Asset).filter(Asset.is_active.is_(True)).all()
    computed = 0
    regime_changes = 0
    skipped = 0

    for asset in assets:
        result = compute_and_store_evolution(db, asset.id, as_of)
        if result is None:
            skipped += 1
        else:
            computed += 1
            if result.regime_changed:
                regime_changes += 1

    logger.info(
        "Evolution run complete — %d computed, %d skipped, %d regime changes",
        computed, skipped, regime_changes,
    )
    return {
        'computed': computed,
        'skipped': skipped,
        'regime_changes': regime_changes,
        'as_of': str(as_of),
    }
