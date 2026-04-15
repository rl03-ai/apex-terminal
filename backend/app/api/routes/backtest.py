"""Backtest API routes.

Endpoints:
  POST /backtest/run          — run walk-forward backtest, return full report
  GET  /backtest/latest       — return last cached report (if any)
  POST /backtest/apply-weights — write recommended weights to .env and restart config cache
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple in-process cache for the last report
_last_report: dict | None = None


class BacktestRunRequest(BaseModel):
    horizons: list[int] = [63, 126, 252]
    run_weight_search: bool = True
    min_universe_size: int = 20
    factor_neutralize: bool = True
    survivorship_correction: bool = True


class ApplyWeightsRequest(BaseModel):
    growth: float
    quality: float
    valuation: float = 0.15
    market: float
    narrative_as_filter: bool = True


@router.post("/run", summary="Run walk-forward backtest")
def run_backtest_endpoint(
    body: BacktestRunRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Run a walk-forward quintile backtest on all scored data in the DB.

    Returns a full structured report including:
    - Quintile mean returns at each horizon
    - Information Coefficients (Spearman)
    - Optimal weight suggestion
    - .env config lines to apply recommended weights
    """
    global _last_report

    from app.services.backtest.engine import run_backtest
    from app.services.backtest.report import generate_report

    result = run_backtest(
        db,
        horizons=body.horizons,
        run_weight_search=body.run_weight_search,
        min_universe_size=body.min_universe_size,
        factor_neutralize=body.factor_neutralize,
        survivorship_correction=body.survivorship_correction,
    )
    report = generate_report(result)
    _last_report = report

    logger.info("Backtest complete:\n%s", report["text_summary"])
    return report


@router.get("/latest", summary="Return last backtest report")
def get_latest_report() -> dict:
    if _last_report is None:
        raise HTTPException(status_code=404, detail="No backtest has been run yet. POST /backtest/run first.")
    return _last_report


@router.post("/apply-weights", summary="Apply recommended weights to runtime config")
def apply_weights(body: ApplyWeightsRequest) -> dict:
    """
    Write the recommended weights to the .env file and clear the settings cache
    so the new weights take effect immediately without restarting the server.
    """
    total = body.growth + body.quality + body.valuation + body.market
    if total <= 0:
        raise HTTPException(status_code=400, detail="Weights must sum to a positive number.")

    # Normalise
    wg = round(body.growth    / total, 4)
    wq = round(body.quality   / total, 4)
    wv = round(body.valuation / total, 4)
    wm = round(1.0 - wg - wq - wv, 4)  # ensure exact sum

    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path("backend/.env")

    new_lines: list[str] = []
    keys_written: set[str] = set()
    target_keys = {
        "SCORE_WEIGHT_GROWTH":    str(wg),
        "SCORE_WEIGHT_QUALITY":   str(wq),
        "SCORE_WEIGHT_VALUATION": str(wv),
        "SCORE_WEIGHT_MARKET":    str(wm),
        "SCORE_NARRATIVE_AS_FILTER": str(body.narrative_as_filter).lower(),
    }

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            key = line.split("=")[0].strip()
            if key in target_keys:
                new_lines.append(f"{key}={target_keys[key]}")
                keys_written.add(key)
            else:
                new_lines.append(line)

    # Append any keys not already present
    for key, val in target_keys.items():
        if key not in keys_written:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")

    # Clear settings cache so next request picks up new values
    try:
        from app.core.config import get_settings
        get_settings.cache_clear()
        # Also clear the scoring weight cache so new weights take effect immediately
        from app.services.scoring.engine import _get_weights
        _get_weights.cache_clear()
    except Exception:
        pass

    logger.info("Weights applied: growth=%.3f quality=%.3f valuation=%.3f market=%.3f", wg, wq, wv, wm)
    return {
        "applied": {
            "SCORE_WEIGHT_GROWTH":    wg,
            "SCORE_WEIGHT_QUALITY":   wq,
            "SCORE_WEIGHT_VALUATION": wv,
            "SCORE_WEIGHT_MARKET":    wm,
            "SCORE_NARRATIVE_AS_FILTER": body.narrative_as_filter,
        },
        "env_path": str(env_path.absolute()),
        "status": "Weights written to .env. Re-run daily_scoring to see effect.",
    }
