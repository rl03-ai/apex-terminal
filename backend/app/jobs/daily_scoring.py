from __future__ import annotations

import logging
import os
from datetime import date

from app.core.database import SessionLocal
from app.services.scoring.engine import refresh_all_scores
from app.services.scoring.percentile import run_normalisation
from app.services.scoring.evolution import run_evolution_for_all

logger = logging.getLogger(__name__)


def run(as_of: date | None = None, workers: int | None = None) -> dict:
    """
    Daily scoring pipeline:
      1. Score all active assets (parallel if SCORE_WORKERS > 1)
      2. Percentile normalisation within today's universe
      3. Score evolution + regime change detection

    Parameters
    ----------
    as_of   : override scoring date (default: latest price date per asset)
    workers : parallel threads (None = reads SCORE_WORKERS env var, default 1)
    """
    # Clear weight cache so any .env changes take effect
    try:
        from app.services.scoring.engine import _get_weights
        _get_weights.cache_clear()
        logger.debug("Weight cache cleared")
    except Exception:
        pass

    if workers is None:
        workers = int(os.getenv("SCORE_WORKERS", "1"))

    db = SessionLocal()
    try:
        # 1. Parallel scoring with error isolation
        rows = refresh_all_scores(db, as_of=as_of, workers=workers)
        db.flush()

        if not rows:
            logger.warning("No score rows returned — universe may be empty")
            db.commit()
            return {'scores_refreshed': 0, 'normalisation': {}, 'evolution': {}}

        scoring_date = as_of or rows[0].date

        # 2. Percentile normalisation
        norm_result = run_normalisation(db, as_of=scoring_date)
        db.flush()
        logger.info("Percentile normalisation: %s", norm_result)

        # 3. Evolution + regime change detection
        evo_result = run_evolution_for_all(db, as_of=scoring_date)
        logger.info("Evolution tracking: %s", evo_result)

        db.commit()
        return {
            'scores_refreshed': len(rows),
            'workers': workers,
            'normalisation': norm_result,
            'evolution': evo_result,
        }
    finally:
        db.close()
