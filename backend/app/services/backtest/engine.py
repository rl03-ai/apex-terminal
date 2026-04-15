"""Walk-forward quintile backtest engine.

Methodology
-----------
For each available scoring date T in the DB:
  1. Load the sub-scores (growth, quality, market) for all assets scored on T.
     Narrative is intentionally excluded — yfinance event data has
     look-ahead contamination and unstable historical coverage.
  2. Build a composite structural score:
         structural = w_g * growth + w_q * quality + w_m * market
     Default weights come from config (or can be overridden per run).
  3. Rank assets into N quintiles (default 5) by structural score.
  4. Measure forward price return for each quintile at horizons
     [63, 126, 252] trading days (~3M, 6M, 12M).
  5. Store per-quintile average returns for each (date, horizon).

After all dates are processed:
  - Compute mean quintile returns across all rebalance dates.
  - Compute Information Coefficient (Spearman rank correlation between
    structural score and forward return) for each horizon.
  - Run a grid search over weight combinations to find the set that
    maximises the Q1–Q5 spread at the 6M horizon.

What this tells you
-------------------
  - If Q1 consistently outperforms Q5, the model has real signal.
  - The IC tells you how much signal per unit of noise.
  - The weight optimisation tells you what the data actually prefers
    vs. what was hard-coded.

Reliability caveat
------------------
  yfinance price history is clean and reliable.
  The backtest is only as deep as the price history in the DB
  (default 370 days → ~1 year of walk-forward, 1 rebalance date).
  With more history (set YFINANCE_HISTORY_DAYS=1260 for 5 years),
  the results become much more statistically significant.

Usage
-----
    from app.services.backtest.engine import run_backtest
    result = run_backtest(db)
    print(result.weight_suggestion)
    print(result.ic_by_horizon)
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, stdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetPriceDaily, AssetScoreDaily

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QuintileReturn:
    """Average return for one quintile at one horizon on one rebalance date."""
    rebalance_date: date
    horizon_days: int
    quintile: int          # 1 = top, 5 = bottom
    asset_count: int
    avg_return_pct: float
    median_return_pct: float


@dataclass
class BacktestResult:
    rebalance_dates: list[date]
    horizons: list[int]
    quintile_means: dict[str, float]          # "Q1_63d" → mean return %
    quintile_stdev: dict[str, float]          # "Q1_63d" → stdev
    q1_q5_spread: dict[int, float]            # horizon_days → Q1-Q5 spread %
    ic_by_horizon: dict[int, float]           # horizon_days → Spearman IC (raw returns)
    ic_significance: dict[int, dict]             # horizon_days → {t_stat, p_value, n, significant}
    ic_neutralized_by_horizon: dict[int, float]  # IC against factor-neutral returns
    ic_neutralized_significance: dict[int, dict] # same for neutralized IC
    factor_exposures: dict[str, dict]         # {factor: {mean, stdev, n}}
    survivorship_stats: dict                  # stats about survivorship correction
    weight_suggestion: dict[str, float]       # {"growth": 0.4, "quality": 0.3, "market": 0.3}
    weight_search_results: list[dict]         # top 10 weight combos by 6M spread
    universe_size: int
    observations: int                         # total (date, asset) pairs used
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spearman_ic(scores: list[float], returns: list[float]) -> float:
    """Spearman rank correlation between scores and forward returns."""
    n = len(scores)
    if n < 4:
        return 0.0

    def rank(lst: list[float]) -> list[float]:
        sorted_idx = sorted(range(n), key=lambda i: lst[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and lst[sorted_idx[j + 1]] == lst[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j) / 2.0
            for k in range(i, j + 1):
                ranks[sorted_idx[k]] = avg_rank
            i = j + 1
        return ranks

    r_scores = rank(scores)
    r_returns = rank(returns)
    mean_rs = mean(r_scores)
    mean_rr = mean(r_returns)
    cov = sum((r_scores[i] - mean_rs) * (r_returns[i] - mean_rr) for i in range(n))
    std_s = (sum((x - mean_rs) ** 2 for x in r_scores) ** 0.5)
    std_r = (sum((x - mean_rr) ** 2 for x in r_returns) ** 0.5)
    if std_s == 0 or std_r == 0:
        return 0.0
    return round(cov / (std_s * std_r), 4)


def _ic_significance(ic: float, n: int) -> dict:
    """
    Compute t-statistic and two-tailed p-value for a Spearman IC.

    Formula: t = IC * sqrt(n - 2) / sqrt(1 - IC^2)
    Degrees of freedom: n - 2

    Interpretation:
      |t| > 1.96 → p < 0.05 (significant at 5%)
      |t| > 2.58 → p < 0.01 (significant at 1%)

    Uses a pure-Python approximation of the Student t CDF via the
    regularised incomplete beta function — no scipy dependency.
    """
    if n < 4 or ic == 0.0:
        return {'t_stat': 0.0, 'p_value': 1.0, 'n': n, 'significant_5pct': False, 'significant_1pct': False}

    ic_clamped = max(-0.9999, min(0.9999, ic))
    t = ic_clamped * ((n - 2) ** 0.5) / ((1 - ic_clamped ** 2) ** 0.5)
    df = n - 2

    # Two-tailed p-value via incomplete beta function approximation
    # P(|T| > |t|) = I(df/(df+t^2), df/2, 1/2)
    # Using a simple numerical approximation
    try:
        x = df / (df + t * t)
        # Regularised incomplete beta via continued fraction (Lentz algorithm, simplified)
        def _reg_incomplete_beta(x: float, a: float, b: float) -> float:
            """Regularised incomplete beta I_x(a,b) via continued fraction."""
            import math
            if x == 0.0: return 0.0
            if x == 1.0: return 1.0
            # Use symmetry relation if needed
            if x > (a + 1) / (a + b + 2):
                return 1.0 - _reg_incomplete_beta(1 - x, b, a)
            lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
            front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
            # Lentz continued fraction
            f, C, D = 1e-30, 1e-30, 0.0
            for m in range(200):
                for i in (0, 1):
                    if m == 0 and i == 0:
                        d = 1.0
                    elif i == 0:
                        d = m * (b - m) * x / ((a + 2*m - 1) * (a + 2*m))
                    else:
                        d = -(a + m) * (a + b + m) * x / ((a + 2*m) * (a + 2*m + 1))
                    D = 1.0 + d * D
                    if abs(D) < 1e-30: D = 1e-30
                    C = 1.0 + d / C
                    if abs(C) < 1e-30: C = 1e-30
                    D = 1.0 / D
                    delta = C * D
                    f *= delta
                    if abs(delta - 1.0) < 1e-10:
                        break
            return front * f

        p_one_tail = _reg_incomplete_beta(x, df / 2.0, 0.5)
        p_value = min(1.0, 2.0 * p_one_tail)
    except Exception:
        # Fallback: normal approximation for large df
        import math
        z = abs(t)
        p_value = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))

    return {
        't_stat': round(t, 4),
        'p_value': round(p_value, 4),
        'n': n,
        'significant_5pct': p_value < 0.05,
        'significant_1pct': p_value < 0.01,
    }


def _forward_return(
    price_map: dict[date, float],
    start_date: date,
    horizon_days: int,
) -> float | None:
    """
    Return the forward return % from start_date to start_date + horizon_days.
    Looks forward up to horizon_days + 10 to handle weekends/holidays.
    """
    start_price = price_map.get(start_date)
    if start_price is None or start_price == 0:
        return None

    target = start_date + timedelta(days=horizon_days)
    for offset in range(0, 15):
        end_price = price_map.get(target + timedelta(days=offset))
        if end_price is not None:
            return ((end_price - start_price) / start_price) * 100.0
    return None


def _structural_score(
    growth: float,
    quality: float,
    valuation: float,
    market: float,
    weights: tuple[float, float, float, float],
) -> float:
    wg, wq, wv, wm = weights
    total = wg + wq + wv + wm
    if total == 0:
        return 0.0
    return (wg * growth + wq * quality + wv * valuation + wm * market) / total


def _assign_quintiles(scores: list[tuple[str, float]], n: int = 5) -> dict[str, int]:
    """Assign quintile ranks 1 (top) to n (bottom) to a list of (asset_id, score)."""
    if not scores:
        return {}
    sorted_scores = sorted(scores, key=lambda x: x[1], reverse=True)
    size = len(sorted_scores)
    result: dict[str, int] = {}
    for i, (asset_id, _) in enumerate(sorted_scores):
        quintile = min(n, int(i / size * n) + 1)
        result[asset_id] = quintile
    return result


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

def run_backtest(
    db: Session,
    *,
    horizons: list[int] | None = None,
    weights: tuple[float, float, float, float] = (0.30, 0.25, 0.15, 0.30),
    n_quintiles: int = 5,
    min_universe_size: int = 20,
    run_weight_search: bool = True,
    factor_neutralize: bool = True,
    survivorship_correction: bool = True,
) -> BacktestResult:
    """
    Run a walk-forward quintile backtest on all scored dates in the DB.

    Parameters
    ----------
    factor_neutralize       : compute IC against Fama-French factor-neutral returns
    survivorship_correction : filter universe per date to historical S&P500 constituents
    """
    if horizons is None:
        horizons = [63, 126, 252]

    warnings: list[str] = []

    # ------------------------------------------------------------------
    # 1. Load all scoring dates
    # ------------------------------------------------------------------
    scoring_dates = sorted(set(
        row[0] for row in db.execute(select(AssetScoreDaily.date).distinct()).all()
    ))
    if not scoring_dates:
        return BacktestResult(
            rebalance_dates=[], horizons=horizons,
            quintile_means={}, quintile_stdev={}, q1_q5_spread={},
            ic_by_horizon={}, ic_significance={},
            ic_neutralized_by_horizon={}, ic_neutralized_significance={},
            factor_exposures={}, survivorship_stats={},
            weight_suggestion={}, weight_search_results=[],
            universe_size=0, observations=0,
            warnings=["No scoring dates found in DB. Run daily_scoring first."],
        )

    logger.info("Backtest: %d scoring dates found", len(scoring_dates))

    # ------------------------------------------------------------------
    # 2. Load all price histories keyed by asset_id → {date: close}
    # ------------------------------------------------------------------
    logger.info("Loading price histories...")
    all_prices = db.execute(
        select(AssetPriceDaily.asset_id, AssetPriceDaily.date, AssetPriceDaily.close)
    ).all()

    price_map: dict[str, dict[date, float]] = {}
    for asset_id, d, close in all_prices:
        if asset_id not in price_map:
            price_map[asset_id] = {}
        price_map[asset_id][d] = float(close)

    logger.info("Loaded prices for %d assets", len(price_map))

    # Ticker map (needed for survivorship filter)
    ticker_rows = db.execute(select(Asset.id, Asset.ticker)).all()
    ticker_map: dict[str, str] = {row[0]: row[1] for row in ticker_rows}

    # ------------------------------------------------------------------
    # 2b. Load Fama-French factors (optional)
    # ------------------------------------------------------------------
    ff_factors: dict = {}
    beta_cache: dict = {}
    factor_exposures_summary: dict = {}

    if factor_neutralize:
        logger.info("Loading Fama-French factors...")
        try:
            from app.services.backtest.factors import (
                load_ff_factors, compute_all_betas, summarize_factor_exposures,
            )
            ff_factors = load_ff_factors()
            if ff_factors:
                beta_cache = compute_all_betas(price_map, ff_factors, scoring_dates)
                factor_exposures_summary = summarize_factor_exposures(beta_cache)
                logger.info("Factor betas ready: %d (asset,date) pairs", len(beta_cache))
            else:
                warnings.append("Fama-French download failed. IC computed against raw returns only.")
                factor_neutralize = False
        except Exception as exc:
            warnings.append(f"Factor setup failed: {exc}. Using raw returns.")
            factor_neutralize = False

    # ------------------------------------------------------------------
    # 2c. Load S&P500 historical constituents (optional)
    # ------------------------------------------------------------------
    surv_history = None
    surv_stats: dict = {"available": False}

    if survivorship_correction:
        logger.info("Loading S&P500 historical constituents...")
        try:
            from app.services.backtest.universe_history import SP500UniverseHistory
            surv_history = SP500UniverseHistory.load()
            if surv_history.available():
                first, last = surv_history.date_range()
                logger.info("Survivorship correction enabled: %s to %s", first, last)
            else:
                warnings.append("S&P500 constituent history unavailable. Survivorship correction disabled.")
                survivorship_correction = False
        except Exception as exc:
            warnings.append(f"Survivorship setup failed: {exc}.")
            survivorship_correction = False

    # ------------------------------------------------------------------
    # 3. Walk-forward loop
    # ------------------------------------------------------------------
    # quintile_returns[horizon][quintile] = [return_pct, ...]
    quintile_returns: dict[int, dict[int, list[float]]] = {
        h: {q: [] for q in range(1, n_quintiles + 1)} for h in horizons
    }
    # ic_data[horizon] = [(score, fwd_return), ...]
    ic_data: dict[int, list[tuple[float, float]]] = {h: [] for h in horizons}
    # ic_neutralized_data[horizon] = [(score, neutralized_return), ...]
    ic_neutralized_data: dict[int, list[tuple[float, float]]] = {h: [] for h in horizons}

    usable_dates: list[date] = []
    total_observations = 0

    for rebalance_date in scoring_dates:
        # Load scores for this date
        score_rows = db.execute(
            select(
                AssetScoreDaily.asset_id,
                AssetScoreDaily.growth_score,
                AssetScoreDaily.quality_score,
                AssetScoreDaily.valuation_score,
                AssetScoreDaily.market_score,
            ).where(AssetScoreDaily.date == rebalance_date)
        ).all()

        if len(score_rows) < min_universe_size:
            continue

        # Survivorship correction: filter to S&P500 constituents at this date
        if survivorship_correction and surv_history is not None:
            all_ids = [r[0] for r in score_rows]
            allowed = set(surv_history.filter_asset_ids(all_ids, ticker_map, rebalance_date))
            score_rows = [r for r in score_rows if r[0] in allowed]

        # Build structural scores
        asset_scores: list[tuple[str, float]] = []
        for asset_id, growth, quality, valuation, market in score_rows:
            if growth is None or quality is None or market is None:
                continue
            s = _structural_score(float(growth), float(quality), float(valuation or 50), float(market), weights)
            asset_scores.append((asset_id, s))

        if len(asset_scores) < min_universe_size:
            continue

        quintile_map = _assign_quintiles(asset_scores, n=n_quintiles)
        score_lookup = {asset_id: s for asset_id, s in asset_scores}

        has_data = False
        for horizon in horizons:
            for asset_id, quintile in quintile_map.items():
                prices = price_map.get(asset_id, {})
                fwd_ret = _forward_return(prices, rebalance_date, horizon)
                if fwd_ret is None:
                    continue
                score = score_lookup[asset_id]
                quintile_returns[horizon][quintile].append(fwd_ret)
                ic_data[horizon].append((score, fwd_ret))
                has_data = True
                total_observations += 1

                # Factor-neutralized IC collection
                if factor_neutralize and ff_factors:
                    try:
                        from app.services.backtest.factors import neutralize_forward_return
                        betas = beta_cache.get((asset_id, rebalance_date))
                        if betas:
                            neutral_ret = neutralize_forward_return(
                                fwd_ret, betas, ff_factors, rebalance_date, horizon
                            )
                            ic_neutralized_data[horizon].append((score, neutral_ret))
                    except Exception:
                        pass

        if has_data:
            usable_dates.append(rebalance_date)

    if not usable_dates:
        warnings.append(
            "No usable rebalance dates: either no forward price data exists yet "
            "(need >63 days of history after first scoring date), "
            "or the universe is too small. "
            "Increase YFINANCE_HISTORY_DAYS and re-run ingestion."
        )

    # ------------------------------------------------------------------
    # 4. Aggregate quintile means + IC
    # ------------------------------------------------------------------
    quintile_means: dict[str, float] = {}
    quintile_stdev_map: dict[str, float] = {}

    for h in horizons:
        for q in range(1, n_quintiles + 1):
            key = f"Q{q}_{h}d"
            vals = quintile_returns[h][q]
            quintile_means[key] = round(mean(vals), 3) if vals else 0.0
            quintile_stdev_map[key] = round(stdev(vals), 3) if len(vals) > 1 else 0.0

    q1_q5_spread: dict[int, float] = {}
    for h in horizons:
        q1_mean = quintile_means.get(f"Q1_{h}d", 0.0)
        q5_mean = quintile_means.get(f"Q5_{h}d", 0.0)
        q1_q5_spread[h] = round(q1_mean - q5_mean, 3)

    ic_by_horizon: dict[int, float] = {}
    ic_significance: dict[int, dict] = {}
    for h in horizons:
        pairs = ic_data[h]
        if len(pairs) >= 4:
            scores_list = [p[0] for p in pairs]
            rets_list = [p[1] for p in pairs]
            ic = _spearman_ic(scores_list, rets_list)
            ic_by_horizon[h] = ic
            ic_significance[h] = _ic_significance(ic, len(pairs))
        else:
            ic_by_horizon[h] = 0.0
            ic_significance[h] = _ic_significance(0.0, len(pairs))

    ic_neutralized_by_horizon: dict[int, float] = {}
    ic_neutralized_significance: dict[int, dict] = {}
    for h in horizons:
        pairs = ic_neutralized_data[h]
        if len(pairs) >= 4:
            ic_n = _spearman_ic([p[0] for p in pairs], [p[1] for p in pairs])
            ic_neutralized_by_horizon[h] = ic_n
            ic_neutralized_significance[h] = _ic_significance(ic_n, len(pairs))
        else:
            ic_neutralized_by_horizon[h] = 0.0
            ic_neutralized_significance[h] = _ic_significance(0.0, len(pairs))

    # ------------------------------------------------------------------
    # 4b. Survivorship stats
    # ------------------------------------------------------------------
    if survivorship_correction and surv_history is not None:
        all_asset_ids = list(price_map.keys())
        surv_stats = surv_history.survivorship_stats(
            all_asset_ids, ticker_map, usable_dates
        )

    # ------------------------------------------------------------------
    # 5. Weight grid search (optimise 6M Q1–Q5 spread)
    # ------------------------------------------------------------------
    weight_suggestion = {"growth": weights[0], "quality": weights[1], "market": weights[2]}
    weight_search_results: list[dict] = []

    if run_weight_search and usable_dates and total_observations > 50:
        logger.info("Running weight grid search...")
        target_horizon = 126  # 6M

        # Grid: weights in steps of 0.1, must sum to 1.0
        candidates = [
            (wg, wq, wv, wm)
            for wg in [i / 10 for i in range(1, 7)]
            for wq in [i / 10 for i in range(1, 7)]
            for wv in [i / 10 for i in range(1, 5)]
            for wm in [i / 10 for i in range(1, 7)]
            if abs(wg + wq + wv + wm - 1.0) < 0.001
        ]

        best_spread = float("-inf")
        combo_results: list[dict] = []

        for candidate_weights in candidates:
            q_rets: dict[int, list[float]] = {q: [] for q in range(1, n_quintiles + 1)}

            for rebalance_date in usable_dates:
                score_rows = db.execute(
                    select(
                        AssetScoreDaily.asset_id,
                        AssetScoreDaily.growth_score,
                        AssetScoreDaily.quality_score,
                        AssetScoreDaily.market_score,
                    ).where(AssetScoreDaily.date == rebalance_date)
                ).all()

                asset_scores_cand: list[tuple[str, float]] = []
                for asset_id, growth, quality, valuation, market in score_rows:
                    if growth is None or quality is None or market is None:
                        continue
                    s = _structural_score(
                        float(growth), float(quality), float(market), candidate_weights
                    )
                    asset_scores_cand.append((asset_id, s))

                qmap = _assign_quintiles(asset_scores_cand, n=n_quintiles)

                for asset_id, quintile in qmap.items():
                    prices = price_map.get(asset_id, {})
                    fwd_ret = _forward_return(prices, rebalance_date, target_horizon)
                    if fwd_ret is not None:
                        q_rets[quintile].append(fwd_ret)

            q1_m = mean(q_rets[1]) if q_rets[1] else 0.0
            q5_m = mean(q_rets[5]) if q_rets[5] else 0.0
            spread = q1_m - q5_m

            combo_results.append({
                "weights": {
                    "growth": candidate_weights[0],
                    "quality": candidate_weights[1],
                    "valuation": candidate_weights[2],
                    "market": candidate_weights[3],
                },
                "q1_mean_6m": round(q1_m, 3),
                "q5_mean_6m": round(q5_m, 3),
                "spread_6m": round(spread, 3),
            })

            if spread > best_spread:
                best_spread = spread
                weight_suggestion = {
                    "growth": candidate_weights[0],
                    "quality": candidate_weights[1],
                    "valuation": candidate_weights[2],
                    "market": candidate_weights[3],
                }

        weight_search_results = sorted(combo_results, key=lambda x: x["spread_6m"], reverse=True)[:10]
        logger.info("Best weights: %s (6M Q1-Q5 spread=%.2f%%)", weight_suggestion, best_spread)

    return BacktestResult(
        rebalance_dates=usable_dates,
        horizons=horizons,
        quintile_means=quintile_means,
        quintile_stdev=quintile_stdev_map,
        q1_q5_spread=q1_q5_spread,
        ic_by_horizon=ic_by_horizon,
        ic_significance=ic_significance,
        ic_neutralized_by_horizon=ic_neutralized_by_horizon,
        ic_neutralized_significance=ic_neutralized_significance,
        factor_exposures=factor_exposures_summary,
        survivorship_stats=surv_stats,
        weight_suggestion=weight_suggestion,
        weight_search_results=weight_search_results,
        universe_size=len(price_map),
        observations=total_observations,
        warnings=warnings,
    )
