"""Decision Matrix.

Aggregates all signals into a single comparable view for entry decisions.

For each candidate ticker (from scanners + watchlist + early signals + insider
alerts), computes 4 sub-scores and a composite Setup Score (0-100).

Sub-scores:
  - Quality   (30%): structural total_score
  - Timing    (30%): early signals + insider alerts + distance from 52w high
  - Regime    (20%): TrendChange daily/weekly regime × confidence
  - R/R       (20%): distance to suggested stop vs. distance from highs

Output verdict:
  Setup >= 75: STRONG_SETUP   (✅)
  Setup >= 60: GOOD            (🟢)
  Setup >= 45: WAIT            (🟡)
  Setup <  45: AVOID           (❌)

The matrix excludes tickers already held in the portfolio (optional filter).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


WEIGHTS = {
    'quality':       0.25,
    'timing':        0.25,
    'regime':        0.20,
    'rr':            0.15,
    'institutional': 0.15,
}

REGIME_BASE_SCORE = {
    'STRONG_UPTREND': 90,
    'UPTREND':        70,
    'BASING':         55,
    'RANGING':        50,
    'TOPPING':        35,
    'DOWNTREND':      15,
    'UNKNOWN':        50,
}

INSIDER_TIMING_BOOST = {
    'CLUSTER_BUY':   30,
    'LARGE_BUY':     20,
    'EXECUTIVE_BUY': 15,
}


def _verdict(setup_score: float) -> str:
    if setup_score >= 75: return 'STRONG_SETUP'
    if setup_score >= 60: return 'GOOD'
    if setup_score >= 45: return 'WAIT'
    return 'AVOID'


def _score_quality(score) -> float:
    return float(score.total_score) if score else 50.0


def _score_timing(
    asset_id: str,
    early_signal_active: bool,
    insider_signal_type: str | None,
    technical,
) -> float:
    points = 0.0
    if early_signal_active:
        points += 40
    if insider_signal_type:
        points += INSIDER_TIMING_BOOST.get(insider_signal_type, 0)
    # Distance from 52w high
    if technical and technical.distance_to_52w_high is not None:
        dist = abs(technical.distance_to_52w_high)
        if dist >= 20:   points += 30
        elif dist >= 10: points += 20
        elif dist >= 5:  points += 10
    return min(points, 100.0)


def _score_regime(score, prices) -> tuple[float, str, float]:
    """Returns (score, regime_label, confidence)."""
    try:
        if not prices or len(prices) < 60:
            return 50.0, 'UNKNOWN', 0.0
        import pandas as pd
        from app.services.technical.equity_trend import analyse_equity_trend
        df = pd.DataFrame([{
            'date': p.date, 'open': p.open, 'high': p.high,
            'low': p.low, 'close': p.close, 'volume': p.volume or 0,
        } for p in prices])
        df = df.set_index(pd.to_datetime(df['date'])).drop(columns=['date'])
        trend = analyse_equity_trend(df)
        if trend.regime == 'UNKNOWN':
            return 50.0, 'UNKNOWN', 0.0
        base = REGIME_BASE_SCORE.get(trend.regime, 50)
        # Blend with confidence
        score_val = 50.0 + (base - 50.0) * trend.confidence
        return score_val, trend.regime, trend.confidence
    except Exception as e:
        logger.debug("regime calc failed: %s", e)
        return 50.0, 'UNKNOWN', 0.0




def _score_institutional(prices) -> float:
    """Institutional analysis score (VWAP/FVG/Delta/POC/Sweeps)."""
    try:
        from app.services.technical.institutional import analyse_institutional
        result = analyse_institutional(prices)
        return result.score
    except Exception as e:
        logger.debug("institutional score failed: %s", e)
        return 50.0

def _score_risk_reward(
    current_price: float,
    technical,
    prices,
) -> tuple[float, dict]:
    """R/R = upside potential vs. stop distance."""
    if not prices or current_price <= 0:
        return 50.0, {'upside_pct': 0, 'stop_distance_pct': 0}

    # Upside = distance from current price to 52w high (approximation of room to run)
    upside_pct = 0.0
    if technical and technical.distance_to_52w_high is not None:
        upside_pct = abs(technical.distance_to_52w_high)
    else:
        # Compute from prices
        ordered = sorted(prices, key=lambda p: p.date)[-252:]
        if ordered:
            high = max(p.high for p in ordered)
            upside_pct = ((high - current_price) / current_price * 100) if current_price > 0 else 0

    # Stop distance — use simple ATR or 12% percentage stop
    try:
        from app.services.portfolio.risk import compute_atr
        atr = compute_atr(prices)
        if atr:
            stop_distance_pct = (2.5 * atr / current_price * 100)
        else:
            stop_distance_pct = 12.0  # fallback
    except Exception:
        stop_distance_pct = 12.0

    # R/R ratio = upside / stop distance
    if stop_distance_pct < 0.1:
        rr_ratio = 0.0
    else:
        rr_ratio = upside_pct / stop_distance_pct

    # Map ratio to score: 3+ = excellent (90), 2 = good (75), 1 = neutral (55), <0.5 = bad (25)
    if rr_ratio >= 3:    score = 90
    elif rr_ratio >= 2:  score = 75
    elif rr_ratio >= 1.5: score = 65
    elif rr_ratio >= 1:  score = 55
    elif rr_ratio >= 0.5: score = 40
    else:                score = 25

    return float(score), {
        'upside_pct': round(upside_pct, 1),
        'stop_distance_pct': round(stop_distance_pct, 1),
        'rr_ratio': round(rr_ratio, 2),
    }


def compute_decision_matrix(
    db: Session,
    user_id: str | None = None,
    exclude_held: bool = True,
    only_watchlist: bool = False,
) -> list[dict]:
    """Build the decision matrix for all candidate assets."""
    from app.models.asset import (
        Asset, AssetScoreDaily, AssetEvent, AssetPriceDaily,
        AssetTechnicalSnapshot, ScannerResult,
    )
    from app.models.early_signal import EarlySignal
    from app.models.insider_alert import InsiderAlertCache
    from app.models.portfolio import Portfolio, Position
    from app.models.watchlist import Watchlist

    # Collect candidate asset IDs from all sources
    candidate_ids: set[str] = set()

    # 1) Scanner results (top opportunities)
    scanner_rows = db.query(ScannerResult).all()
    for s in scanner_rows:
        candidate_ids.add(s.asset_id)

    # 2) Early signals (active)
    early = db.query(EarlySignal).filter(EarlySignal.is_active == True).all()
    early_by_asset = {e.asset_id: e for e in early}
    candidate_ids.update(early_by_asset.keys())

    # 3) Insider alerts
    insider = db.query(InsiderAlertCache).all()
    insider_by_asset = {ia.asset_id: ia for ia in insider}
    candidate_ids.update(insider_by_asset.keys())

    # 4) Watchlist
    watchlist_ids: set[str] = set()
    if user_id:
        wl = db.query(Watchlist).filter(Watchlist.user_id == user_id).all()
        watchlist_ids = {w.asset_id for w in wl}
        candidate_ids.update(watchlist_ids)

    # If only_watchlist, restrict
    if only_watchlist:
        candidate_ids = watchlist_ids

    # Held positions (to exclude)
    held_ids: set[str] = set()
    if exclude_held and user_id:
        portfolios = db.query(Portfolio).filter(Portfolio.user_id == user_id).all()
        for pf in portfolios:
            positions = db.query(Position).filter(
                Position.portfolio_id == pf.id, Position.status == 'open'
            ).all()
            held_ids.update(p.asset_id for p in positions)

    # Get latest score per asset
    latest_scores = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id.in_(candidate_ids))
        .order_by(desc(AssetScoreDaily.date))
        .all()
    )
    score_by_asset: dict[str, AssetScoreDaily] = {}
    for s in latest_scores:
        if s.asset_id not in score_by_asset:
            score_by_asset[s.asset_id] = s

    # Pre-filter: skip held + assets without scores, then sort by quality + signal presence
    pre_candidates = []
    for asset_id in candidate_ids:
        if asset_id in held_ids:
            continue
        score = score_by_asset.get(asset_id)
        if not score:
            continue
        # Quick priority: has timing signal? base quality?
        has_signal = (asset_id in early_by_asset) or (asset_id in insider_by_asset)
        priority = (1 if has_signal else 0) * 1000 + score.total_score
        pre_candidates.append((priority, asset_id, score))

    # Take top 60 candidates only (keeps response under 5 seconds)
    pre_candidates.sort(reverse=True)
    pre_candidates = pre_candidates[:60]

    candidate_ids_ordered = [asset_id for _, asset_id, _ in pre_candidates]

    # ── BATCH QUERIES — single DB round-trip per table ───────────────────────
    assets_map = {
        a.id: a
        for a in db.query(Asset).filter(Asset.id.in_(candidate_ids_ordered)).all()
    }

    # Load all prices in one query, group by asset_id in memory
    all_prices_raw = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id.in_(candidate_ids_ordered))
        .order_by(AssetPriceDaily.asset_id, AssetPriceDaily.date.asc())
        .all()
    )
    prices_map: dict[str, list] = {}
    for p in all_prices_raw:
        prices_map.setdefault(p.asset_id, []).append(p)

    # Load latest technical snapshot per asset in one query
    from sqlalchemy import func as _func
    latest_tech_sub = (
        db.query(
            AssetTechnicalSnapshot.asset_id,
            _func.max(AssetTechnicalSnapshot.date).label('max_date')
        )
        .filter(AssetTechnicalSnapshot.asset_id.in_(candidate_ids_ordered))
        .group_by(AssetTechnicalSnapshot.asset_id)
        .subquery()
    )
    technical_map = {
        t.asset_id: t
        for t in db.query(AssetTechnicalSnapshot).join(
            latest_tech_sub,
            (AssetTechnicalSnapshot.asset_id == latest_tech_sub.c.asset_id) &
            (AssetTechnicalSnapshot.date == latest_tech_sub.c.max_date)
        ).all()
    }

    rows: list[dict] = []
    for _, asset_id, score in pre_candidates:
        asset = assets_map.get(asset_id)
        if not asset:
            continue

        prices = prices_map.get(asset_id, [])
        if not prices:
            continue
        current_price = prices[-1].close

        technical = technical_map.get(asset_id)

        # Compute the 4 sub-scores
        q_score = _score_quality(score)

        early_active = asset_id in early_by_asset
        insider_type = insider_by_asset[asset_id].signal_type if asset_id in insider_by_asset else None
        t_score = _score_timing(asset_id, early_active, insider_type, technical)

        r_score, regime_label, regime_conf = _score_regime(score, prices)

        rr_score, rr_details = _score_risk_reward(current_price, technical, prices)

        # Institutional analysis
        inst_score = _score_institutional(prices)

        # Composite
        setup = (
            q_score    * WEIGHTS['quality'] +
            t_score    * WEIGHTS['timing'] +
            r_score    * WEIGHTS['regime'] +
            rr_score   * WEIGHTS['rr'] +
            inst_score * WEIGHTS['institutional']
        )

        rows.append({
            'asset_id': asset_id,
            'ticker': asset.ticker,
            'name': asset.name,
            'sector': asset.sector,
            'current_price': round(current_price, 2),
            'is_watchlist': asset_id in watchlist_ids,
            'in_early_signals': early_active,
            'insider_signal': insider_type,
            'setup_score':    round(setup, 1),
            'verdict':        _verdict(setup),
            'sub_scores': {
                'quality':        round(q_score, 1),
                'timing':         round(t_score, 1),
                'regime':         round(r_score, 1),
                'risk_reward':    round(rr_score, 1),
                'institutional':  round(inst_score, 1),
            },
            'regime':         regime_label,
            'regime_confidence': round(regime_conf, 2),
            'rr_details':     rr_details,
            'total_score':    round(score.total_score, 1),
        })

    rows.sort(key=lambda x: -x['setup_score'])
    return rows
