"""Fama-French factor neutralization.

What this solves
----------------
Without factor neutralization, a high IC in the backtest might simply
mean the model was long in small-cap growth stocks during a bull market.
That's not alpha — it's beta dressed up.

Factor neutralization strips out the returns explained by known systematic
factors (market, size, value, profitability, investment, momentum) and
measures how much of the residual return the model actually explains.

If IC_raw = 0.08 and IC_neutralized = 0.02, the model has weak real alpha.
If IC_raw = 0.08 and IC_neutralized = 0.07, the model has genuine alpha.

Fama-French data
----------------
Source: Kenneth French's data library (Dartmouth)
URL: mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
Free, no authentication. Updated regularly.

Factors used
------------
  Mkt-RF  : excess market return (beta)
  SMB     : small minus big (size)
  HML     : high minus low (value)
  RMW     : robust minus weak (profitability)
  CMA     : conservative minus aggressive (investment)
  Mom     : momentum (past-12-month-minus-1-month return)

OLS regression per ticker (252-day rolling window):
  r_ticker = α + β_MKT*MKT + β_SMB*SMB + β_HML*HML +
             β_RMW*RMW + β_CMA*CMA + β_Mom*Mom + ε

Factor-neutralized forward return:
  r_neutral = r_raw - Σ(β_k * cumulative_factor_return_k_over_horizon)

The residual ε represents day-by-day returns unexplained by factors.
The neutralized forward return represents the forward alpha.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data sources
# ─────────────────────────────────────────────────────────────────────────────

_FF5_URL  = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
_MOM_URL  = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
_USER_AGENT = "apex-terminal research@apex-terminal.io"

# Cache location: backend/.cache/
_CACHE_DIR = Path(__file__).parent.parent.parent.parent / ".cache"
_FF5_CACHE = _CACHE_DIR / "ff5_daily.csv"
_MOM_CACHE = _CACHE_DIR / "ff_mom_daily.csv"

FACTOR_NAMES = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]


# ─────────────────────────────────────────────────────────────────────────────
# Download helpers
# ─────────────────────────────────────────────────────────────────────────────

def _download_zip_csv(url: str, cache_path: Path) -> str | None:
    """Download a ZIP from French's site, extract the first CSV, cache and return content."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Use cache if < 7 days old
    if cache_path.exists():
        age_days = (date.today() - date.fromtimestamp(cache_path.stat().st_mtime)).days
        if age_days < 7:
            return cache_path.read_text(encoding="utf-8", errors="ignore")
        logger.info("Cache stale (%d days), refreshing %s", age_days, cache_path.name)

    logger.info("Downloading FF factors from %s …", url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_name = next(n for n in zf.namelist() if n.endswith(".CSV") or n.endswith(".csv"))
            content = zf.read(csv_name).decode("utf-8", errors="ignore")
        cache_path.write_text(content, encoding="utf-8")
        logger.info("Downloaded and cached → %s", cache_path.name)
        return content
    except Exception as exc:
        logger.warning("FF download failed: %s", exc)
        return None


def _parse_ff_csv(content: str, value_col: str | None = None) -> dict[date, dict[str, float]]:
    """
    Parse a Fama-French daily CSV into {date: {factor: value}} dict.

    The files have a preamble (text lines), a daily section, and an annual
    section at the end. We parse only the daily section.

    Values in the CSV are percentages — we divide by 100 to get decimals.
    """
    lines = content.splitlines()

    # Find the first data line: starts with a date (8 digits)
    data_start = None
    for i, line in enumerate(lines):
        stripped = line.strip().replace(",", "").replace("-", "")
        if stripped.isdigit() and len(stripped) >= 8:
            data_start = i
            break

    if data_start is None:
        logger.warning("Could not find data section in FF CSV")
        return {}

    # Find the header row just before the data start
    header_row = None
    for i in range(data_start - 1, max(data_start - 5, -1), -1):
        if lines[i].strip() and not lines[i].strip().startswith("#"):
            header_row = i
            break

    # Parse header
    if header_row is not None:
        raw_header = [h.strip() for h in lines[header_row].split(",")]
        # Remove empty first element if present
        if raw_header and not raw_header[0]:
            raw_header = raw_header[1:]
        # Columns are: Date, Factor1, Factor2, ...
        cols = raw_header
    else:
        cols = ["Date"] + FACTOR_NAMES[:5]  # fallback

    result: dict[date, dict[str, float]] = {}

    for line in lines[data_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if not parts or len(parts) < 2:
            continue
        date_str = parts[0].strip()
        if not date_str.isdigit() or len(date_str) < 8:
            break  # hit annual section or end of data

        try:
            d = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except ValueError:
            continue

        row_data: dict[str, float] = {}
        for col_idx, col_name in enumerate(cols[1:], start=1):
            if col_idx < len(parts):
                try:
                    row_data[col_name.strip()] = float(parts[col_idx]) / 100.0
                except ValueError:
                    pass

        if row_data:
            result[d] = row_data

    logger.debug("Parsed %d daily FF observations", len(result))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public: load factors
# ─────────────────────────────────────────────────────────────────────────────

def load_ff_factors() -> dict[date, dict[str, float]]:
    """
    Load merged Fama-French 5-factor + momentum daily data.
    Returns {date: {factor_name: decimal_return}} dict.
    Downloads and caches automatically.
    """
    # 5 factors
    ff5_content = _download_zip_csv(_FF5_URL, _FF5_CACHE)
    ff5_data = _parse_ff_csv(ff5_content) if ff5_content else {}

    # Momentum factor
    mom_content = _download_zip_csv(_MOM_URL, _MOM_CACHE)
    mom_data = _parse_ff_csv(mom_content) if mom_content else {}

    # Merge momentum into 5-factor data
    merged: dict[date, dict[str, float]] = {}
    all_dates = set(ff5_data) | set(mom_data)
    for d in all_dates:
        row = dict(ff5_data.get(d, {}))
        mom_row = mom_data.get(d, {})
        # Mom column may be named "Mom" or "WML"
        mom_val = mom_row.get("Mom") or mom_row.get("WML")
        if mom_val is not None:
            row["Mom"] = mom_val
        if row:
            merged[d] = row

    logger.info("FF factors loaded: %d daily observations, factors: %s",
                len(merged),
                sorted({k for row in list(merged.values())[:1] for k in row}))
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# OLS regression (pure Python + numpy)
# ─────────────────────────────────────────────────────────────────────────────

def _ols(y: list[float], X: list[list[float]]) -> list[float]:
    """
    OLS regression: β = (X'X)^-1 X'y
    X should include a leading column of 1s for the intercept.
    Returns coefficient vector [intercept, β1, β2, ...].
    Falls back to zeros if matrix is singular.
    """
    try:
        import numpy as np
        X_np = np.array(X, dtype=float)
        y_np = np.array(y, dtype=float)
        # Normal equations
        betas, _, _, _ = np.linalg.lstsq(X_np, y_np, rcond=None)
        return betas.tolist()
    except Exception as exc:
        logger.debug("OLS failed: %s", exc)
        return [0.0] * (len(X[0]) if X else 1)


# ─────────────────────────────────────────────────────────────────────────────
# Factor beta estimation
# ─────────────────────────────────────────────────────────────────────────────

def compute_factor_betas(
    price_map: dict[date, float],
    ff_factors: dict[date, dict[str, float]],
    as_of: date,
    window: int = 252,
) -> dict[str, float] | None:
    """
    Estimate factor betas for a single asset using a rolling window.

    Parameters
    ----------
    price_map  : {date: close_price}
    ff_factors : {date: {factor: value}}
    as_of      : end of the estimation window
    window     : trading days of history to use (default 252 = 1 year)

    Returns dict {factor_name: beta} + "alpha" key, or None if insufficient data.
    """
    # Collect dates in window
    all_dates = sorted(d for d in price_map if d <= as_of)
    if len(all_dates) < max(30, window // 4):
        return None

    window_dates = all_dates[-window:] if len(all_dates) >= window else all_dates

    # Build daily return series
    y: list[float] = []
    X: list[list[float]] = []

    available_factors = sorted(
        {k for d in window_dates for k in ff_factors.get(d, {})}
        & set(FACTOR_NAMES)
    )
    if not available_factors:
        return None

    for i in range(1, len(window_dates)):
        prev_date = window_dates[i - 1]
        curr_date = window_dates[i]
        prev_price = price_map.get(prev_date)
        curr_price = price_map.get(curr_date)
        if prev_price is None or curr_price is None or prev_price == 0:
            continue

        ret = (curr_price - prev_price) / prev_price
        ff_row = ff_factors.get(curr_date)
        if ff_row is None:
            continue

        factor_vals = [ff_row.get(f, 0.0) for f in available_factors]
        if any(v is None for v in factor_vals):
            continue

        y.append(ret)
        X.append([1.0] + factor_vals)   # intercept + factors

    if len(y) < 30:
        return None

    coefficients = _ols(y, X)
    result = {"alpha": coefficients[0]}
    for i, factor_name in enumerate(available_factors):
        result[factor_name] = coefficients[i + 1] if i + 1 < len(coefficients) else 0.0

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Forward return neutralization
# ─────────────────────────────────────────────────────────────────────────────

def neutralize_forward_return(
    raw_return: float,
    betas: dict[str, float],
    ff_factors: dict[date, dict[str, float]],
    start_date: date,
    horizon_days: int,
) -> float:
    """
    Compute factor-neutralized forward return.

    neutralized = raw_return - Σ(β_k * cumulative_factor_return_k)

    The cumulative factor return is the sum of daily factor returns
    over the forward horizon window.
    """
    end_date = start_date + timedelta(days=horizon_days + 15)  # buffer for weekends
    factor_names = [k for k in betas if k != "alpha"]

    cumulative_factors: dict[str, float] = {f: 0.0 for f in factor_names}
    days_counted = 0
    current = start_date

    while current <= end_date and days_counted < horizon_days:
        ff_row = ff_factors.get(current)
        if ff_row:
            for f in factor_names:
                cumulative_factors[f] += ff_row.get(f, 0.0)
            days_counted += 1
        current += timedelta(days=1)

    # Factor-explained return
    factor_return = sum(
        betas.get(f, 0.0) * cumulative_factors[f]
        for f in factor_names
    )

    return raw_return - factor_return


# ─────────────────────────────────────────────────────────────────────────────
# Batch beta computation for the backtest
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_betas(
    price_map: dict[str, dict[date, float]],
    ff_factors: dict[date, dict[str, float]],
    rebalance_dates: list[date],
    window: int = 252,
) -> dict[tuple[str, date], dict[str, float]]:
    """
    Pre-compute factor betas for all (asset_id, rebalance_date) pairs.
    Returns cache dict: (asset_id, rebalance_date) → betas dict.
    """
    cache: dict[tuple[str, date], dict[str, float]] = {}
    total = len(price_map) * len(rebalance_dates)
    computed = 0

    for asset_id, prices in price_map.items():
        for rb_date in rebalance_dates:
            betas = compute_factor_betas(prices, ff_factors, rb_date, window=window)
            if betas:
                cache[(asset_id, rb_date)] = betas
            computed += 1

    hit_rate = len(cache) / max(1, total) * 100
    logger.info(
        "Factor betas computed: %d/%d pairs (%.1f%% coverage)",
        len(cache), total, hit_rate,
    )
    return cache


# ─────────────────────────────────────────────────────────────────────────────
# Universe factor exposure summary
# ─────────────────────────────────────────────────────────────────────────────

def summarize_factor_exposures(
    beta_cache: dict[tuple[str, date], dict[str, float]],
) -> dict[str, dict[str, float]]:
    """
    Compute mean and stdev of each factor beta across the universe.
    Useful for understanding what the universe is systematically exposed to.
    """
    from statistics import mean, stdev

    factor_values: dict[str, list[float]] = {}
    for betas in beta_cache.values():
        for factor, beta in betas.items():
            if factor == "alpha":
                continue
            factor_values.setdefault(factor, []).append(beta)

    summary: dict[str, dict[str, float]] = {}
    for factor, values in factor_values.items():
        summary[factor] = {
            "mean":   round(mean(values), 4),
            "stdev":  round(stdev(values), 4) if len(values) > 1 else 0.0,
            "n":      len(values),
        }
    return summary
