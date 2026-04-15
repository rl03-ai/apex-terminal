"""Percentile normaliser.

After raw scores are computed for the full universe, this module converts
them to universe-relative percentiles and re-derives states from those
percentiles instead of fixed absolute thresholds.

Why this matters
----------------
A raw score of 72 means nothing in isolation. If 600 of 1000 tickers
score above 72, it is below-average. If only 50 score above 72, it is
elite. Percentiles make scores comparable across market regimes.

State thresholds (percentile-based)
------------------------------------
  active_setup  → top 10%   (≥ p90)
  confirming    → top 25%   (≥ p75)
  emerging      → top 50%   (≥ p50)
  dormant       → top 75%   (≥ p25)
  broken        → bottom 25% (< p25)

Usage
-----
    from app.services.scoring.percentile import PercentileNormaliser

    # After scoring all assets for today:
    raw_scores = {asset_id: 72.4, ...}
    norm = PercentileNormaliser(raw_scores)
    pct = norm.percentile(asset_id)           # 0–100
    state = norm.state(asset_id)              # 'active_setup' etc.
    norm.apply_to_db(db, as_of=date.today())  # write pct fields to DB
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models.asset import AssetScoreDaily

logger = logging.getLogger(__name__)

# State percentile thresholds (inclusive lower bound)
_STATE_THRESHOLDS: list[tuple[float, str]] = [
    (90.0, "active_setup"),
    (75.0, "confirming"),
    (50.0, "emerging"),
    (25.0, "dormant"),
    (0.0,  "broken"),
]


def _compute_percentiles(scores: dict[str, float]) -> dict[str, float]:
    """
    Convert raw scores to percentile ranks within the provided universe.

    Returns a dict mapping asset_id → percentile (0–100).
    Ties are resolved by averaging ranks (standard competition ranking).
    """
    if not scores:
        return {}

    sorted_ids = sorted(scores, key=lambda k: scores[k])
    n = len(sorted_ids)
    percentiles: dict[str, float] = {}

    i = 0
    while i < n:
        j = i
        while j < n - 1 and scores[sorted_ids[j + 1]] == scores[sorted_ids[i]]:
            j += 1
        # all items from i to j (inclusive) have the same score
        # average rank: ((i+1) + (j+1)) / 2 = (i + j + 2) / 2
        avg_rank = (i + j + 2) / 2
        pct = ((avg_rank - 1) / (n - 1)) * 100.0 if n > 1 else 50.0
        for k in range(i, j + 1):
            percentiles[sorted_ids[k]] = round(pct, 2)
        i = j + 1

    return percentiles


def _state_from_percentile(pct: float) -> str:
    for threshold, state in _STATE_THRESHOLDS:
        if pct >= threshold:
            return state
    return "broken"


class PercentileNormaliser:
    """
    Compute and cache percentile ranks for a scored universe.

    Parameters
    ----------
    raw_scores : dict mapping asset_id → raw total_score (0–100)
    """

    def __init__(self, raw_scores: dict[str, float]) -> None:
        self._raw = raw_scores
        self._percentiles = _compute_percentiles(raw_scores)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def percentile(self, asset_id: str) -> float:
        """Return percentile rank (0–100) for an asset. 50 if unknown."""
        return self._percentiles.get(asset_id, 50.0)

    def state(self, asset_id: str) -> str:
        """Return percentile-based state label."""
        return _state_from_percentile(self.percentile(asset_id))

    def universe_size(self) -> int:
        return len(self._raw)

    def summary(self) -> dict[str, int]:
        """Count of assets per state bucket."""
        counts: dict[str, int] = {s: 0 for _, s in _STATE_THRESHOLDS}
        for asset_id in self._percentiles:
            counts[self.state(asset_id)] += 1
        return counts

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def apply_to_db(self, db: Session, *, as_of: date) -> int:
        """
        Write percentile and percentile-derived state back to AssetScoreDaily rows.

        Adds two fields to each row:
          - score_percentile   (float 0–100)
          - state              (overwritten with percentile-based state)

        Returns the number of rows updated.
        """
        rows = (
            db.query(AssetScoreDaily)
            .filter(AssetScoreDaily.date == as_of)
            .all()
        )

        updated = 0
        for row in rows:
            pct = self._percentiles.get(row.asset_id)
            if pct is None:
                continue
            row.score_percentile = pct
            row.state = _state_from_percentile(pct)
            updated += 1

        logger.info(
            "Percentile normalisation applied — %d rows, as_of=%s | distribution=%s",
            updated, as_of, self.summary(),
        )
        return updated


# ---------------------------------------------------------------------------
# Convenience: build normaliser directly from DB for a given date
# ---------------------------------------------------------------------------

def build_normaliser_from_db(db: Session, *, as_of: date) -> PercentileNormaliser:
    """Load all scores for a date from DB and return a ready normaliser."""
    rows = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.date == as_of)
        .all()
    )
    raw_scores = {row.asset_id: float(row.total_score) for row in rows}
    return PercentileNormaliser(raw_scores)


def run_normalisation(db: Session, *, as_of: date) -> dict:
    """Full normalisation pass: build normaliser, write to DB, return summary."""
    normaliser = build_normaliser_from_db(db, as_of=as_of)
    if normaliser.universe_size() == 0:
        logger.warning("No scores found for %s — skipping normalisation.", as_of)
        return {"updated": 0, "as_of": str(as_of), "universe_size": 0}

    updated = normaliser.apply_to_db(db, as_of=as_of)
    db.flush()
    return {
        "updated": updated,
        "as_of": str(as_of),
        "universe_size": normaliser.universe_size(),
        "distribution": normaliser.summary(),
    }
