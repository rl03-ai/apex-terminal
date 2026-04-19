"""Apex scoring engine v2.

Changes from v1:
  1. Valuation score    — new component (EV/Sales, P/FCF, PEG).
                          Higher = cheaper relative to growth. Weight: 15%.
  2. Market score       — split into 3 genuinely independent signals:
                          technical structure, intermediate momentum,
                          recent acceleration. Removes triple-counting of
                          momentum.
  3. Guidance proxy     — replaced binary heuristic with operating margin
                          slope over last 3 quarters (real operating leverage
                          signal).
  4. News normalisation — news sentiment adjusted by market-cap tier coverage
                          ratio (small caps stop being penalised vs mega caps).
  5. Weights            — growth 30%, quality 25%, valuation 15%, market 15%,
                          narrative 10%, risk penalty -15%.
"""

from __future__ import annotations
from functools import lru_cache

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.models.asset import (
    Asset,
    AssetEvent,
    AssetFundamentalsQuarterly,
    AssetPriceDaily,
    AssetScoreDaily,
    AssetTechnicalSnapshot,
)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    growth: float
    quality: float
    valuation: float          # NEW
    narrative: float
    market: float
    risk: float
    total: float
    consistency: float
    score_momentum: float
    conviction: float
    state: str
    explanation: dict[str, list[str]]


# ─────────────────────────────────────────────────────────────────────────────
# Primitives
# ─────────────────────────────────────────────────────────────────────────────

def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, round(value, 2)))


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / abs(previous)) * 100.0


def _score_linear(value: float | None, low: float, high: float) -> float:
    if value is None:
        return 50.0
    if high == low:
        return 50.0
    return clamp_score(((value - low) / (high - low)) * 100)


def _linear_slope(values: list[float]) -> float:
    """OLS slope (units/step). Returns 0.0 if < 2 points."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = mean(values)
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return round(num / den, 4) if den else 0.0


def _latest_quarters(
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> tuple[
    AssetFundamentalsQuarterly | None,
    AssetFundamentalsQuarterly | None,
    AssetFundamentalsQuarterly | None,
]:
    ordered = sorted(
        fundamentals, key=lambda x: (x.fiscal_year, x.fiscal_quarter), reverse=True
    )
    latest   = ordered[0] if ordered else None
    prev_q   = ordered[1] if len(ordered) > 1 else None
    year_ago = None
    if latest:
        for item in ordered[1:]:
            if (
                item.fiscal_quarter == latest.fiscal_quarter
                and item.fiscal_year == latest.fiscal_year - 1
            ):
                year_ago = item
                break
    return latest, prev_q, year_ago


# ─────────────────────────────────────────────────────────────────────────────
# Weights
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_weights() -> dict[str, float]:
    """
    Load scoring weights from env vars; normalise positive weights to sum to 1.
    Defaults: growth 30%, quality 25%, valuation 15%, market 15%, narrative 10%.
    Risk is a separate penalty (default 15%), not normalised with positives.
    """
    import os
    try:
        w_growth    = float(os.getenv("SCORE_WEIGHT_GROWTH",    "0.30"))
        w_quality   = float(os.getenv("SCORE_WEIGHT_QUALITY",   "0.25"))
        w_valuation = float(os.getenv("SCORE_WEIGHT_VALUATION", "0.15"))
        w_market    = float(os.getenv("SCORE_WEIGHT_MARKET",    "0.15"))
        w_narrative = float(os.getenv("SCORE_WEIGHT_NARRATIVE", "0.10"))
        w_risk      = float(os.getenv("SCORE_WEIGHT_RISK",      "0.15"))
        if os.getenv("SCORE_NARRATIVE_AS_FILTER", "false").lower() == "true":
            w_narrative = 0.0
        pos_sum = w_growth + w_quality + w_valuation + w_market + w_narrative
        if pos_sum <= 0:
            pos_sum = 1.0
        return {
            "growth":    w_growth    / pos_sum,
            "quality":   w_quality   / pos_sum,
            "valuation": w_valuation / pos_sum,
            "market":    w_market    / pos_sum,
            "narrative": w_narrative / pos_sum,
            "risk":      w_risk,
        }
    except Exception:
        return {
            "growth": 0.30, "quality": 0.25, "valuation": 0.15,
            "market": 0.15, "narrative": 0.10, "risk": 0.15,
        }


def compute_total_score(
    *,
    growth: float,
    quality: float,
    valuation: float,
    narrative: float,
    market: float,
    risk: float,
) -> float:
    w = _get_weights()
    total = (
        w["growth"]    * growth
        + w["quality"]   * quality
        + w["valuation"] * valuation
        + w["market"]    * market
        + w["narrative"] * narrative
        - w["risk"]      * risk
    )
    return clamp_score(total)


def derive_state(total_score: float) -> str:
    """
    Absolute-threshold state. After scoring full universe, percentile
    normalisation (scoring.percentile) overwrites this with p-based states.
    """
    if total_score >= 80: return "active_setup"
    if total_score >= 68: return "confirming"
    if total_score >= 55: return "emerging"
    if total_score >= 40: return "dormant"
    return "broken"


def build_explanation(payload: dict[str, list[str]]) -> dict[str, Any]:
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 1. GROWTH SCORE  (fix: guidance proxy → margin slope)
# ─────────────────────────────────────────────────────────────────────────────

def _margin_slope_score(
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> tuple[float, str]:
    """
    Compute operating leverage score from margin trend over last 3 quarters.

    Uses OLS slope of operating_margin values (oldest→newest).
    Maps slope to 0–100:
      slope ≥ +0.03/quarter (accelerating)  → 85–95
      slope  0..+0.03 (stable/slight rise)  → 60–75
      slope -0.02..0  (slight decline)      → 40–55
      slope ≤ -0.02 (deteriorating)         → 15–35

    This replaces the binary guidance_proxy from v1.
    """
    ordered = sorted(
        fundamentals, key=lambda x: (x.fiscal_year, x.fiscal_quarter)
    )
    margins = [
        q.operating_margin for q in ordered[-4:]
        if q.operating_margin is not None
    ]
    if len(margins) < 2:
        return 50.0, "Insufficient margin history for slope calculation."

    slope = _linear_slope(margins)   # units: margin change per quarter

    if slope >= 0.03:
        s = clamp_score(75 + (slope - 0.03) / 0.02 * 15)
        label = f"Operating margin accelerating ({slope*100:+.1f} pp/quarter)."
    elif slope >= 0.0:
        s = _score_linear(slope, 0.0, 0.03) * 0.25 + 60
        label = f"Operating margin stable/rising ({slope*100:+.1f} pp/quarter)."
    elif slope >= -0.02:
        s = _score_linear(slope, -0.02, 0.0) * 0.20 + 40
        label = f"Operating margin declining slightly ({slope*100:+.1f} pp/quarter)."
    else:
        s = clamp_score(15 + (slope + 0.04) / 0.02 * 15)
        label = f"Operating margin deteriorating ({slope*100:+.1f} pp/quarter)."

    return clamp_score(s), label


def compute_growth_score(
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> tuple[float, list[str]]:
    latest, prev_q, year_ago = _latest_quarters(fundamentals)
    reasons: list[str] = []
    if not latest:
        return 45.0, ["Missing recent fundamentals; growth score defaulted to neutral."]

    rev_yoy = _pct_change(latest.revenue, year_ago.revenue if year_ago else None)
    rev_qoq = _pct_change(latest.revenue, prev_q.revenue if prev_q else None)
    op_yoy  = _pct_change(
        latest.operating_income,
        year_ago.operating_income if year_ago else None,
    )

    revenue_score      = _score_linear(rev_yoy, 0, 35)
    acceleration_score = _score_linear(rev_qoq, -10, 15)
    op_score           = _score_linear(op_yoy, -20, 60)

    # FIX: margin slope replaces binary guidance_proxy
    margin_slope_score, margin_slope_reason = _margin_slope_score(fundamentals)

    total = clamp_score(
        0.30 * revenue_score
        + 0.15 * acceleration_score
        + 0.25 * op_score
        + 0.30 * margin_slope_score   # raised from 0.15 (margin) + 0.25 (proxy) = 0.40 → kept at 0.30
    )

    if rev_yoy is not None:
        reasons.append(f"Revenue growth YoY at {rev_yoy:.1f}%.")
    if rev_qoq is not None:
        reasons.append(f"Revenue change QoQ at {rev_qoq:.1f}%.")
    if op_yoy is not None:
        reasons.append(f"Operating income growth YoY at {op_yoy:.1f}%.")
    reasons.append(margin_slope_reason)

    if rev_yoy is not None and rev_yoy < 10:
        total = clamp_score(total - 10)
        reasons.append("Revenue growth below 10% applies a structural penalty.")

    return total, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 2. QUALITY SCORE  (unchanged — was already sound)
# ─────────────────────────────────────────────────────────────────────────────

def compute_quality_score(
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> tuple[float, list[str]]:
    latest, _, year_ago = _latest_quarters(fundamentals)
    reasons: list[str] = []
    if not latest:
        return 45.0, ["Missing recent fundamentals; quality score defaulted to neutral."]

    fcf_score     = 85.0 if (latest.free_cash_flow or 0) > 0 else 35.0
    leverage_ratio = _safe_ratio(latest.total_debt, latest.revenue)
    leverage_score = (
        80.0 if leverage_ratio is not None and leverage_ratio < 0.5
        else _score_linear((leverage_ratio or 1.5) * -1, -2.0, -0.2)
    )
    runway_ratio = _safe_ratio(
        latest.cash_and_equivalents,
        abs(latest.free_cash_flow)
        if latest.free_cash_flow and latest.free_cash_flow < 0
        else latest.revenue,
    )
    runway_score = (
        80.0 if runway_ratio is not None and runway_ratio > 1
        else _score_linear(runway_ratio, 0.1, 1.0)
    )
    margin_stability = None
    if latest.gross_margin is not None and year_ago and year_ago.gross_margin is not None:
        margin_stability = abs(latest.gross_margin - year_ago.gross_margin) * 100
    margin_score = (
        85.0 if margin_stability is not None and margin_stability <= 3
        else _score_linear(10 - (margin_stability or 10), 0, 10)
    )
    dilution = None
    if (
        latest.shares_outstanding is not None
        and year_ago
        and year_ago.shares_outstanding not in (None, 0)
    ):
        dilution = _pct_change(latest.shares_outstanding, year_ago.shares_outstanding)
    dilution_score = (
        85.0 if dilution is not None and dilution <= 3
        else _score_linear(15 - (dilution or 15), 0, 15)
    )

    total = clamp_score(
        0.25 * fcf_score
        + 0.20 * leverage_score
        + 0.15 * runway_score
        + 0.20 * margin_score
        + 0.20 * dilution_score
    )
    if (latest.free_cash_flow or 0) < 0 and (latest.total_debt or 0) > (
        latest.cash_and_equivalents or 0
    ):
        total = clamp_score(total - 15)
        reasons.append("Negative FCF with debt above cash applied a quality penalty.")

    reasons += [
        f'FCF is {"positive" if (latest.free_cash_flow or 0) > 0 else "negative"}.',
        f"Debt/Revenue proxy at {(leverage_ratio or 0):.2f}.",
        f"Cash runway proxy at {(runway_ratio or 0):.2f}.",
    ]
    if dilution is not None:
        reasons.append(f"Share count change YoY at {dilution:.1f}%.")
    return total, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 3. VALUATION SCORE  (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def compute_valuation_score(
    asset: Asset,
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> tuple[float, list[str]]:
    """
    Valuation score — higher = cheaper relative to earnings/growth.

    Metrics (all annualised from trailing quarterly data):
      EV/Sales  :  EV = market_cap + total_debt - cash
                   Good: <3 for slow growth, <10 for high growth
      P/FCF     :  market_cap / annual_FCF  (only when FCF > 0)
                   Good: <20
      PEG ratio :  (market_cap / annual_NI) / rev_growth_yoy
                   Good: <1 = undervalued, 1-2 = fair, >3 = expensive

    If market_cap is missing → neutral 50.

    Sub-weights:  EV/Sales 40%,  P/FCF 35%,  PEG 25%
    PEG excluded when NI < 0 or growth unknown.
    """
    reasons: list[str] = []
    market_cap = asset.market_cap
    if not market_cap:
        return 50.0, ["Market cap unavailable; valuation score set to neutral."]

    latest, _, year_ago = _latest_quarters(fundamentals)
    if not latest:
        return 50.0, ["No fundamentals; valuation score set to neutral."]

    # Annualise trailing quarters (use 4-quarter sum if available, else *4)
    ordered_q = sorted(
        fundamentals, key=lambda x: (x.fiscal_year, x.fiscal_quarter), reverse=True
    )[:4]
    annual_revenue = sum(q.revenue for q in ordered_q if q.revenue) or None
    annual_fcf     = sum(q.free_cash_flow for q in ordered_q if q.free_cash_flow is not None) or None
    annual_ni      = sum(q.net_income for q in ordered_q if q.net_income is not None) or None

    if annual_revenue is None and latest.revenue:
        annual_revenue = latest.revenue * 4
    if annual_fcf is None and latest.free_cash_flow is not None:
        annual_fcf = latest.free_cash_flow * 4
    if annual_ni is None and latest.net_income is not None:
        annual_ni = latest.net_income * 4

    scores: list[tuple[float, float]] = []   # (score, weight)

    # ── EV/Sales ──────────────────────────────────────────────────────────────
    ev_sales = None
    ev = market_cap
    if latest.total_debt:
        ev += latest.total_debt
    if latest.cash_and_equivalents:
        ev -= latest.cash_and_equivalents
    ev = max(ev, 0)
    if annual_revenue and annual_revenue > 0:
        ev_sales = ev / annual_revenue
        # Score: EV/Sales 0-2 → 90-100, 2-5 → 70-89, 5-10 → 45-69, 10-20 → 20-44, >20 → 0-19
        if ev_sales <= 2:
            ev_score = _score_linear(2 - ev_sales, 0, 2) * 0.1 + 90
        elif ev_sales <= 5:
            ev_score = _score_linear(5 - ev_sales, 0, 3) * 0.20 + 70
        elif ev_sales <= 10:
            ev_score = _score_linear(10 - ev_sales, 0, 5) * 0.24 + 45
        elif ev_sales <= 20:
            ev_score = _score_linear(20 - ev_sales, 0, 10) * 0.24 + 20
        else:
            ev_score = max(0.0, 20 - (ev_sales - 20) * 0.5)
        scores.append((clamp_score(ev_score), 0.40))
        reasons.append(f"EV/Sales at {ev_sales:.2f}x.")

    # ── P/FCF ─────────────────────────────────────────────────────────────────
    if annual_fcf and annual_fcf > 0:
        pfcf = market_cap / annual_fcf
        # P/FCF ≤10 → 95, 10-20 → 80, 20-35 → 60, 35-60 → 35, >60 → 10
        if pfcf <= 10:
            pfcf_score = 95.0
        elif pfcf <= 20:
            pfcf_score = _score_linear(20 - pfcf, 0, 10) * 0.15 + 80
        elif pfcf <= 35:
            pfcf_score = _score_linear(35 - pfcf, 0, 15) * 0.20 + 60
        elif pfcf <= 60:
            pfcf_score = _score_linear(60 - pfcf, 0, 25) * 0.25 + 35
        else:
            pfcf_score = max(5.0, 35 - (pfcf - 60) * 0.3)
        scores.append((clamp_score(pfcf_score), 0.35))
        reasons.append(f"P/FCF at {pfcf:.1f}x.")
    elif annual_fcf is not None and annual_fcf <= 0:
        # Negative FCF → penalise but don't kill
        scores.append((30.0, 0.35))
        reasons.append("Negative trailing FCF; P/FCF score penalised.")

    # ── PEG ───────────────────────────────────────────────────────────────────
    rev_growth = _pct_change(
        latest.revenue, year_ago.revenue if year_ago else None
    )
    if annual_ni and annual_ni > 0 and rev_growth and rev_growth > 0:
        pe = market_cap / annual_ni
        peg = pe / rev_growth   # revenue growth as proxy for earnings growth
        # PEG ≤0.5 → 95, 0.5-1 → 80, 1-2 → 60, 2-3 → 40, >3 → 15
        if peg <= 0.5:
            peg_score = 95.0
        elif peg <= 1.0:
            peg_score = _score_linear(1.0 - peg, 0, 0.5) * 0.15 + 80
        elif peg <= 2.0:
            peg_score = _score_linear(2.0 - peg, 0, 1.0) * 0.20 + 60
        elif peg <= 3.0:
            peg_score = _score_linear(3.0 - peg, 0, 1.0) * 0.20 + 40
        else:
            peg_score = max(5.0, 40 - (peg - 3.0) * 8)
        scores.append((clamp_score(peg_score), 0.25))
        reasons.append(f"PEG at {peg:.2f} (P/E {pe:.1f}, rev growth {rev_growth:.1f}%).")

    if not scores:
        return 50.0, ["Insufficient data for valuation scoring; set to neutral."]

    # Renormalise weights for available metrics
    total_weight = sum(w for _, w in scores)
    total = sum(s * w for s, w in scores) / total_weight
    return clamp_score(total), reasons


# ─────────────────────────────────────────────────────────────────────────────
# 4. NARRATIVE SCORE  (fix: news coverage normalisation by market cap tier)
# ─────────────────────────────────────────────────────────────────────────────

# Expected news coverage per market cap tier (for normalisation)
_NEWS_COVERAGE_EXPECTED: list[tuple[float, int]] = [
    (200_000_000_000, 15),
    (10_000_000_000,  10),
    (2_000_000_000,    6),
    (300_000_000,      3),
    (0,                1),
]


def _expected_news_coverage(market_cap: float | None) -> int:
    if market_cap is None:
        return 6
    for threshold, expected in _NEWS_COVERAGE_EXPECTED:
        if market_cap >= threshold:
            return expected
    return 1


def compute_narrative_score(
    asset: Asset,
    events: Sequence[AssetEvent],
) -> tuple[float, list[str]]:
    """
    Narrative score v2 — driven entirely by real catalyst data.

    Removed: IMPORTANT_THEMES keyword fallback (hardcoded sector→score mapping).
    Reason: an "AI" company with consecutive EPS misses and insider selling
    should NOT get a high narrative score just because its industry
    contains the word "ai". The fallback was injecting noise.

    Signal hierarchy (all from AssetEvent table):
      1. CatalystScore from aggregator (earnings + insider + news) — 60%
         This is the primary signal. It has real data, real weights, real
         recency decay. It replaces theme_score + most of the old sub-components.
      2. Upcoming catalysts (events in next 45 days) — 25%
         Earnings dates, product launches, analyst days — forward-looking.
      3. Analyst upgrade/downgrade net signal — 15%
         Recent (45-day) analyst actions as a sentiment cross-check.

    Neutral fallback: 50.0 when no event data is available.
    This is intentionally conservative — absence of data is not bullish.
    """
    reasons: list[str] = []
    now = datetime.now(tz=timezone.utc)
    # Handle both aware and naive event_date datetimes
    def _to_aware(dt):
        if dt is None: return now
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    recent_events = [e for e in events if _to_aware(e.event_date) >= now - timedelta(days=45)]
    upcoming = [e for e in events if now <= _to_aware(e.event_date) <= now + timedelta(days=45)]

    # ── 1. CatalystScore (60%) ────────────────────────────────────────────
    catalyst_composite = 50.0
    try:
        from app.services.catalyst.aggregator import compute_full_catalyst
        event_dicts = [
            {
                "event_type": e.event_type,
                "event_date": e.event_date,
                "title": e.title,
                "summary": e.summary,
                "sentiment_score": e.sentiment_score,
                "importance_score": e.importance_score,
                "source": e.source,
            }
            for e in events
        ]
        cat = compute_full_catalyst(
                asset.ticker, event_dicts,
                sector=getattr(asset, 'sector', None),
                industry=getattr(asset, 'industry', None),
                market_cap=getattr(asset, 'market_cap', None),
            )
        catalyst_composite = cat.score
        if cat.catalyst_type != "none":
            e_s = f'{cat.earnings_score:.0f}' if cat.earnings_score is not None else '—'
            i_s = f'{cat.insider_score:.0f}' if cat.insider_score is not None else '—'
            n_s = f'{cat.news_score:.0f}' if cat.news_score is not None else '—'
            reasons.append(
                f"CatalystScore={cat.score:.1f} [earnings={e_s} insider={i_s} news={n_s}]."
            )
    except Exception:
        # Fallback: derive from raw events directly if aggregator unavailable
        sentiment_vals = [
            e.sentiment_score for e in recent_events
            if e.sentiment_score is not None
        ]
        if sentiment_vals:
            avg_s = sum(sentiment_vals) / len(sentiment_vals)
            catalyst_composite = clamp_score((avg_s + 1) * 50)
            reasons.append(f"Raw event sentiment avg={avg_s:+.2f} (aggregator unavailable).")

    # ── 2. Upcoming catalyst proximity (25%) ─────────────────────────────
    if upcoming:
        upcoming_score = clamp_score(
            sum((e.importance_score or 40) for e in upcoming) / len(upcoming)
        )
        reasons.append(f"{len(upcoming)} catalyst(s) in next 45 days.")
    else:
        upcoming_score = 40.0   # neutral-to-slightly-negative: no upcoming catalyst

    # ── 3. Analyst net signal (15%) ───────────────────────────────────────
    upgrades   = sum(1 for e in recent_events if e.event_type == "analyst_upgrade")
    downgrades = sum(1 for e in recent_events if e.event_type == "analyst_downgrade")
    net_analyst = upgrades - downgrades
    if net_analyst > 0:
        analyst_score = min(85.0, 65.0 + net_analyst * 5)
        reasons.append(f"Net analyst upgrades: +{net_analyst}.")
    elif net_analyst < 0:
        analyst_score = max(25.0, 50.0 + net_analyst * 8)
        reasons.append(f"Net analyst downgrades: {net_analyst}.")
    else:
        analyst_score = 50.0

    total = clamp_score(
        0.60 * catalyst_composite
        + 0.25 * upcoming_score
        + 0.15 * analyst_score
    )

    return total, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 5. MARKET SCORE  (fix: separate into 3 independent signals)
# ─────────────────────────────────────────────────────────────────────────────

def compute_market_score(
    prices: Sequence[AssetPriceDaily],
    technical: AssetTechnicalSnapshot | None,
) -> tuple[float, list[str]]:
    """
    Three genuinely independent signals (v1 had 5 but 3 measured momentum):

    A. Technical structure (35%)
       - MA50 / MA200 alignment  (trend direction)
       - Distance to 52-week high  (positioning within range)

    B. Intermediate momentum (35%)
       - 3-month and 6-month price performance
       (raw trend signal — how strong was recent price action)

    C. Recent acceleration (30%)
       - Change in 1-month return vs 3-month return
       (is momentum building or fading?)
       This is the most predictive short-term signal per AQR research.
    """
    reasons: list[str] = []
    if not prices:
        return 45.0, ["No price history; market score defaulted to neutral."]

    ordered = sorted(prices, key=lambda x: x.date)
    latest_close = ordered[-1].close

    def close_ago(n: int) -> float | None:
        return ordered[-(n + 1)].close if len(ordered) > n else None

    # ── A. Technical structure ────────────────────────────────────────────────
    ma_score = 50.0
    if technical and technical.ma50 and technical.ma200:
        above50  = latest_close > technical.ma50
        above200 = latest_close > technical.ma200
        golden   = technical.ma50 > technical.ma200
        if above50 and above200 and golden:
            ma_score = 85.0
        elif above200:
            ma_score = 65.0
        elif above50:
            ma_score = 50.0
        else:
            ma_score = 30.0
        reasons.append(
            f'{"Above" if above50 else "Below"} MA50, '
            f'{"above" if above200 else "below"} MA200'
            f'{", golden cross" if golden else ""}.'
        )

    dist_52w = abs(technical.distance_to_52w_high or 20) if technical else 20
    # 0% from high → 100,  5% → ~80,  20% → ~40,  50% → ~0
    proximity_score = clamp_score(100 - dist_52w * 1.8)

    technical_structure = clamp_score(0.60 * ma_score + 0.40 * proximity_score)

    # ── B. Intermediate momentum ──────────────────────────────────────────────
    perf_3m = _pct_change(latest_close, close_ago(63))
    perf_6m = _pct_change(latest_close, close_ago(126))
    rs3_score = _score_linear(perf_3m, -20, 35)
    rs6_score = _score_linear(perf_6m, -25, 60)
    intermediate_momentum = clamp_score(0.55 * rs3_score + 0.45 * rs6_score)

    if perf_3m is not None:
        reasons.append(f"3M performance: {perf_3m:+.1f}%.")
    if perf_6m is not None:
        reasons.append(f"6M performance: {perf_6m:+.1f}%.")

    # ── C. Recent acceleration ────────────────────────────────────────────────
    # 1M return vs 3M return — positive = accelerating, negative = decelerating
    perf_1m = _pct_change(latest_close, close_ago(21))
    acceleration_score = 50.0
    if perf_1m is not None and perf_3m is not None:
        # acceleration = 1M annualised - 3M annualised
        ann_1m = perf_1m * (252 / 21)
        ann_3m = perf_3m * (252 / 63)
        accel  = ann_1m - ann_3m
        # Maps: +30% acceleration → ~80,  0 → 50,  -30% → ~20
        acceleration_score = clamp_score(50 + accel * 0.50)
        direction = "accelerating" if accel > 5 else ("decelerating" if accel < -5 else "stable")
        reasons.append(f"Momentum {direction} (accel={accel:+.1f}% ann.).")

    total = clamp_score(
        0.35 * technical_structure
        + 0.35 * intermediate_momentum
        + 0.30 * acceleration_score
    )
    return total, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 6. RISK SCORE  (unchanged — drawdown/volatility/valuation penalty still valid)
# ─────────────────────────────────────────────────────────────────────────────

def compute_risk_score(
    asset: Asset,
    fundamentals: Sequence[AssetFundamentalsQuarterly],
    prices: Sequence[AssetPriceDaily],
    events: Sequence[AssetEvent],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    latest, _, year_ago = _latest_quarters(fundamentals)

    # Annualised volatility
    if len(prices) > 20:
        ordered = sorted(prices, key=lambda x: x.date)
        returns = [
            (ordered[i].close - ordered[i-1].close) / ordered[i-1].close
            for i in range(1, len(ordered))
            if ordered[i-1].close
        ]
        volatility = (pstdev(returns) * (252 ** 0.5) * 100) if len(returns) >= 2 else 35.0
    else:
        volatility = 35.0
    volatility_score = _score_linear(volatility, 15, 80)

    # Drawdown from peak
    latest_close = sorted(prices, key=lambda x: x.date)[-1].close if prices else None
    drawdown = None
    if prices and latest_close is not None:
        peak = max(p.close for p in prices)
        drawdown = ((peak - latest_close) / peak) * 100 if peak else 0
    drawdown_score = _score_linear(drawdown, 5, 60)

    # Narrative / speculative dependency
    narrative_dep = (
        75.0 if any(
            w in (asset.industry or "").lower() for w in ["space", "battery", "ev", "pre-revenue"]
        ) else 55.0
    )

    # Funding risk
    funding_risk = 80.0 if any(e.event_type == "funding_risk" for e in events[-20:]) else 45.0
    if latest and year_ago and latest.shares_outstanding and year_ago.shares_outstanding:
        share_change = _pct_change(latest.shares_outstanding, year_ago.shares_outstanding) or 0
        if share_change > 8:
            funding_risk = max(funding_risk, 75.0)
            reasons.append(f"Dilution: shares +{share_change:.1f}% YoY.")

    # Earnings consistency risk (more volatile earnings = higher risk)
    ordered_q = sorted(
        fundamentals, key=lambda x: (x.fiscal_year, x.fiscal_quarter), reverse=True
    )[:6]
    ni_values = [q.net_income for q in ordered_q if q.net_income is not None]
    earnings_consistency_risk = 50.0
    if len(ni_values) >= 3:
        ni_stdev = pstdev(ni_values)
        ni_mean  = abs(mean(ni_values)) or 1
        cv = ni_stdev / ni_mean   # coefficient of variation
        earnings_consistency_risk = clamp_score(cv * 40)   # CV 0→0, CV 1→40, CV 2→80
        if cv > 1.0:
            reasons.append(f"High earnings volatility (CV={cv:.2f}) increases risk score.")

    total = clamp_score(
        0.25 * volatility_score
        + 0.20 * drawdown_score
        + 0.15 * narrative_dep
        + 0.20 * funding_risk
        + 0.20 * earnings_consistency_risk
    )

    reasons += [
        f"Annualised volatility proxy: {volatility:.1f}%.",
    ]
    if drawdown is not None:
        reasons.append(f"Drawdown from trailing peak: {drawdown:.1f}%.")
    return total, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 7. Supporting scores (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def compute_consistency_score(
    fundamentals: Sequence[AssetFundamentalsQuarterly],
) -> float:
    ordered = sorted(
        fundamentals, key=lambda x: (x.fiscal_year, x.fiscal_quarter), reverse=True
    )[:6]
    if len(ordered) < 3:
        return 50.0
    revenues   = [x.revenue for x in ordered if x.revenue is not None]
    op_margins = [x.operating_margin for x in ordered if x.operating_margin is not None]
    rev_beats  = sum(1 for i in range(len(revenues) - 1) if revenues[i] >= revenues[i + 1])
    margin_stab = 100 - min(100, pstdev(op_margins) * 100) if len(op_margins) >= 2 else 50.0
    return clamp_score(
        (rev_beats / max(len(revenues) - 1, 1)) * 60 + 0.4 * margin_stab
    )


def compute_score_momentum(
    previous_scores: Sequence[AssetScoreDaily],
    total_score_today: float,
) -> float:
    if not previous_scores:
        return 0.0
    ordered = sorted(previous_scores, key=lambda s: s.date, reverse=True)
    score_7d  = ordered[6].total_score  if len(ordered) > 6  else ordered[-1].total_score
    score_30d = ordered[29].total_score if len(ordered) > 29 else ordered[-1].total_score
    return clamp_score(
        50
        + 0.5 * (total_score_today - score_7d)
        + 0.5 * (total_score_today - score_30d)
    )


def compute_conviction_score(
    growth: float, quality: float, valuation: float,
    narrative: float, market: float, total: float,
) -> float:
    # Conviction = total penalised by dispersion across all components
    dispersion = pstdev([growth, quality, valuation, narrative, market])
    return clamp_score(total - dispersion * 0.5)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Main assembly
# ─────────────────────────────────────────────────────────────────────────────

def calculate_asset_breakdown(
    *,
    asset: Asset,
    fundamentals: Sequence[AssetFundamentalsQuarterly],
    prices: Sequence[AssetPriceDaily],
    events: Sequence[AssetEvent],
    technical: AssetTechnicalSnapshot | None,
    previous_scores: Sequence[AssetScoreDaily],
) -> ScoreBreakdown:
    growth,    growth_r    = compute_growth_score(fundamentals)
    quality,   quality_r   = compute_quality_score(fundamentals)
    valuation, valuation_r = compute_valuation_score(asset, fundamentals)
    narrative, narrative_r = compute_narrative_score(asset, events)
    market,    market_r    = compute_market_score(prices, technical)
    risk,      risk_r      = compute_risk_score(asset, fundamentals, prices, events)

    total      = compute_total_score(
        growth=growth, quality=quality, valuation=valuation,
        narrative=narrative, market=market, risk=risk,
    )
    consistency = compute_consistency_score(fundamentals)
    momentum    = compute_score_momentum(previous_scores, total)
    conviction  = compute_conviction_score(growth, quality, valuation, narrative, market, total)
    state       = derive_state(total)
    explanation = build_explanation({
        "growth":    growth_r,
        "quality":   quality_r,
        "valuation": valuation_r,
        "narrative": narrative_r,
        "market":    market_r,
        "risk":      risk_r,
    })
    return ScoreBreakdown(
        growth=growth, quality=quality, valuation=valuation,
        narrative=narrative, market=market, risk=risk,
        total=total, consistency=consistency,
        score_momentum=momentum, conviction=conviction,
        state=state, explanation=explanation,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. DB refresh
# ─────────────────────────────────────────────────────────────────────────────

import logging as _log
_score_logger = _log.getLogger(__name__)


def refresh_asset_score(
    db: Session, asset: Asset, as_of: date | None = None
) -> AssetScoreDaily:
    """Score a single asset with error isolation. Never raises — logs and returns partial row."""
    try:
        if as_of is None:
            lp = (
                db.query(AssetPriceDaily)
                .filter(AssetPriceDaily.asset_id == asset.id)
                .order_by(AssetPriceDaily.date.desc())
                .first()
            )
            as_of = lp.date if lp else date.today()

        fundamentals    = db.query(AssetFundamentalsQuarterly).filter(AssetFundamentalsQuarterly.asset_id == asset.id).all()
        prices          = db.query(AssetPriceDaily).filter(AssetPriceDaily.asset_id == asset.id).order_by(AssetPriceDaily.date.asc()).limit(400).all()
        events          = db.query(AssetEvent).filter(AssetEvent.asset_id == asset.id).order_by(AssetEvent.event_date.asc()).all()
        technical       = db.query(AssetTechnicalSnapshot).filter(AssetTechnicalSnapshot.asset_id == asset.id).order_by(AssetTechnicalSnapshot.date.desc()).first()
        previous_scores = db.query(AssetScoreDaily).filter(AssetScoreDaily.asset_id == asset.id).order_by(AssetScoreDaily.date.desc()).limit(40).all()

        bd = calculate_asset_breakdown(
            asset=asset, fundamentals=fundamentals, prices=prices,
            events=events, technical=technical, previous_scores=previous_scores,
        )

        row = db.query(AssetScoreDaily).filter(
            AssetScoreDaily.asset_id == asset.id,
            AssetScoreDaily.date == as_of,
        ).first()
        if not row:
            row = AssetScoreDaily(
                asset_id=asset.id, date=as_of,
                growth_score=0, quality_score=0, narrative_score=0,
                market_score=0, risk_score=0, total_score=0,
            )
            db.add(row)

        row.growth_score     = bd.growth
        row.quality_score    = bd.quality
        row.narrative_score  = bd.narrative
        row.market_score     = bd.market
        row.risk_score       = bd.risk
        row.total_score      = bd.total
        row.consistency_score = bd.consistency
        row.score_momentum   = bd.score_momentum
        row.conviction_score = bd.conviction
        row.valuation_score  = bd.valuation
        row.state            = bd.state
        row.explanation      = bd.explanation
        db.flush()
        return row

    except Exception as exc:
        _score_logger.warning(
            "Score failed for %s (%s): %s — skipping",
            asset.ticker, asset.id, exc,
        )
        # Return or create a neutral placeholder row so the caller
        # has something to work with for percentile normalisation
        try:
            row = db.query(AssetScoreDaily).filter(
                AssetScoreDaily.asset_id == asset.id,
                AssetScoreDaily.date == (as_of or date.today()),
            ).first()
            if row:
                return row
        except Exception:
            pass
        placeholder = AssetScoreDaily(
            asset_id=asset.id,
            date=as_of or date.today(),
            growth_score=50, quality_score=50, narrative_score=50,
            market_score=50, risk_score=50, total_score=50,
            state='broken',
        )
        try:
            db.add(placeholder)
            db.flush()
        except Exception:
            pass
        return placeholder


def _score_one_asset_isolated(
    asset_id: str,
    asset_ticker: str,
    as_of: date | None,
) -> tuple[str, bool, str | None]:
    """
    Score a single asset in its own DB session.
    Designed to run inside a ThreadPoolExecutor thread.

    Returns (asset_id, success, error_message_or_None).
    """
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if asset is None:
            return asset_id, False, "Asset not found in thread session"
        refresh_asset_score(db, asset, as_of=as_of)
        db.commit()
        return asset_id, True, None
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        _score_logger.warning("Thread score failed %s: %s", asset_ticker, exc)
        return asset_id, False, str(exc)
    finally:
        try:
            db.close()
        except Exception:
            pass


def refresh_all_scores(
    db: Session,
    as_of: date | None = None,
    workers: int | None = None,
    log_every: int = 50,
) -> list[AssetScoreDaily]:
    """
    Score all active assets.

    When workers > 1 (default: reads SCORE_WORKERS env var, falls back to 1
    for safety), scoring runs in parallel threads — each asset in its own
    DB session to avoid SQLAlchemy thread-safety issues.

    The shared `db` session is only used for:
      - Loading the asset list (fast)
      - Returning the final AssetScoreDaily rows after parallel scoring

    Error isolation: a failure on one asset is logged and skipped;
    the remaining assets continue processing.

    Parameters
    ----------
    db        : shared session (read-only during parallel phase)
    as_of     : scoring date override
    workers   : parallel threads (None = read from SCORE_WORKERS env, default 1)
    log_every : log progress every N completions
    """
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if workers is None:
        workers = int(os.getenv("SCORE_WORKERS", "1"))

    assets = db.query(Asset).filter(Asset.is_active.is_(True)).all()
    total = len(assets)
    if total == 0:
        return []

    _score_logger.info(
        "Scoring %d assets (workers=%d, as_of=%s)", total, workers, as_of or "latest"
    )
    t0 = time.monotonic()

    # ── Sequential path (workers=1) — avoids thread overhead for small universes
    if workers <= 1:
        results: list[AssetScoreDaily] = []
        failed = 0
        for i, asset in enumerate(assets, 1):
            row = refresh_asset_score(db, asset, as_of=as_of)
            results.append(row)
            if row.total_score == 50 and row.state == 'broken':
                failed += 1
            if i % log_every == 0 or i == total:
                _score_logger.info(
                    "Scoring %d/%d (%.0f%%) failed=%d",
                    i, total, i/total*100, failed,
                )
        elapsed = time.monotonic() - t0
        _score_logger.info(
            "Scoring complete — %d ok, %d failed, %.1fs",
            total - failed, failed, elapsed,
        )
        return results

    # ── Parallel path (workers > 1) — each asset in own session
    asset_specs = [(a.id, a.ticker) for a in assets]
    succeeded: list[str] = []
    failed_ids: list[str] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apex-score") as pool:
        future_map = {
            pool.submit(_score_one_asset_isolated, aid, ticker, as_of): (aid, ticker)
            for aid, ticker in asset_specs
        }
        for future in as_completed(future_map):
            aid, ticker = future_map[future]
            completed += 1
            try:
                _, success, err = future.result()
                if success:
                    succeeded.append(aid)
                else:
                    failed_ids.append(aid)
                    _score_logger.warning("Score FAILED %s: %s", ticker, err)
            except Exception as exc:
                failed_ids.append(aid)
                _score_logger.warning("Score FUTURE ERR %s: %s", ticker, exc)

            if completed % log_every == 0 or completed == total:
                elapsed = time.monotonic() - t0
                rate = completed / elapsed if elapsed > 0 else 0
                _score_logger.info(
                    "Scoring %d/%d (%.0f%%) ok=%d fail=%d %.1ft/s",
                    completed, total, completed/total*100,
                    len(succeeded), len(failed_ids), rate,
                )

    elapsed = time.monotonic() - t0
    _score_logger.info(
        "Parallel scoring done — %d ok, %d failed, %.1fs (%.1f assets/s)",
        len(succeeded), len(failed_ids), elapsed,
        len(succeeded)/elapsed if elapsed > 0 else 0,
    )

    # Re-load all score rows from DB for return value.
    # Use the most recent scoring date in DB (not necessarily today),
    # since as_of per asset is determined by their latest price date.
    if as_of:
        query_date = as_of
    else:
        latest_date_row = (
            db.query(AssetScoreDaily.date)
            .order_by(AssetScoreDaily.date.desc())
            .first()
        )
        query_date = latest_date_row[0] if latest_date_row else date.today()

    all_rows = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.date == query_date)
        .all()
    )
    return all_rows
