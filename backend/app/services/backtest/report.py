"""Backtest report generator.

Converts a BacktestResult into:
  1. A structured dict (for the API response)
  2. A human-readable text report (for logging / CLI use)
  3. Concrete weight recommendations with confidence assessment

IC interpretation guide
-----------------------
  IC > 0.05   → weak but real signal
  IC > 0.10   → moderate signal (useful in practice)
  IC > 0.15   → strong signal (rare in equity markets)
  IC < 0.03   → noise (factor should be reviewed or dropped)

Q1–Q5 spread interpretation (annualised)
-----------------------------------------
  > 10%  → strong separation, model has clear alpha
  5–10%  → moderate separation, usable
  2–5%   → weak, monitor
  < 2%   → no meaningful separation

Usage
-----
    from app.services.backtest.report import generate_report
    report = generate_report(result)
    print(report["text_summary"])
    # → apply recommended weights:
    # SCORE_WEIGHT_GROWTH=0.4
    # SCORE_WEIGHT_QUALITY=0.3
    # SCORE_WEIGHT_MARKET=0.3
"""

from __future__ import annotations

import math
from datetime import date

from app.services.backtest.engine import BacktestResult


# ---------------------------------------------------------------------------
# IC interpretation
# ---------------------------------------------------------------------------

def _interpret_ic(ic: float) -> str:
    abs_ic = abs(ic)
    direction = "positive" if ic >= 0 else "negative (inverse signal)"
    if abs_ic >= 0.15:
        strength = "STRONG"
    elif abs_ic >= 0.10:
        strength = "MODERATE"
    elif abs_ic >= 0.05:
        strength = "WEAK but real"
    else:
        strength = "NOISE — not statistically meaningful"
    return f"{strength} ({direction}, IC={ic:.4f})"


def _interpret_spread(spread: float, horizon_days: int) -> str:
    trading_days_per_year = 252
    annualised = spread * (trading_days_per_year / horizon_days)
    if annualised >= 10:
        level = "STRONG alpha"
    elif annualised >= 5:
        level = "MODERATE alpha"
    elif annualised >= 2:
        level = "WEAK alpha"
    else:
        level = "NO meaningful alpha"
    return f"{level} (raw spread={spread:.2f}%, annualised≈{annualised:.1f}%)"


# ---------------------------------------------------------------------------
# Confidence score for weight recommendation
# ---------------------------------------------------------------------------

def _confidence(result: BacktestResult) -> str:
    """
    Estimate confidence in the weight recommendation.

    Factors:
    - Number of rebalance dates (more = better)
    - Number of observations (more = better)
    - IC at 6M (higher = more confident)
    - Q1-Q5 spread at 6M (higher = more confident)
    """
    n_dates = len(result.rebalance_dates)
    ic_6m = abs(result.ic_by_horizon.get(126, 0.0))
    spread_6m = result.q1_q5_spread.get(126, 0.0)
    obs = result.observations

    score = 0
    if n_dates >= 20:
        score += 3
    elif n_dates >= 10:
        score += 2
    elif n_dates >= 3:
        score += 1

    if ic_6m >= 0.10:
        score += 3
    elif ic_6m >= 0.05:
        score += 2
    elif ic_6m >= 0.02:
        score += 1

    if spread_6m >= 5:
        score += 2
    elif spread_6m >= 2:
        score += 1

    if obs >= 5000:
        score += 1

    if score >= 7:
        return "HIGH — apply weights with confidence"
    elif score >= 4:
        return "MODERATE — apply weights, but monitor performance"
    elif score >= 2:
        return "LOW — insufficient history; use as directional guide only"
    else:
        return "VERY LOW — not enough data; keep current weights"


# ---------------------------------------------------------------------------
# Main report function
# ---------------------------------------------------------------------------

def generate_report(result: BacktestResult) -> dict:
    """
    Generate a full backtest report.

    Returns a dict with:
      - structured    : machine-readable data
      - text_summary  : human-readable text block
      - weight_config : copy-paste .env lines for recommended weights
    """

    # ------------------------------------------------------------------
    # Structured data
    # ------------------------------------------------------------------
    quintile_table: list[dict] = []
    for h in result.horizons:
        row: dict = {"horizon_days": h, "horizon_label": f"{h // 21}M"}
        for q in range(1, 6):
            key = f"Q{q}_{h}d"
            row[f"Q{q}_mean_pct"] = result.quintile_means.get(key, 0.0)
            row[f"Q{q}_stdev_pct"] = result.quintile_stdev.get(key, 0.0)
        row["Q1_Q5_spread_pct"] = result.q1_q5_spread.get(h, 0.0)
        row["IC"] = result.ic_by_horizon.get(h, 0.0)
        sig = result.ic_significance.get(h, {})
        row["IC_t_stat"] = sig.get("t_stat", 0.0)
        row["IC_p_value"] = sig.get("p_value", 1.0)
        row["IC_significant_5pct"] = sig.get("significant_5pct", False)
        row["IC_n"] = sig.get("n", 0)
        quintile_table.append(row)

    confidence = _confidence(result)
    w = result.weight_suggestion

    structured = {
        "rebalance_dates_used": len(result.rebalance_dates),
        "first_date": str(result.rebalance_dates[0]) if result.rebalance_dates else None,
        "last_date": str(result.rebalance_dates[-1]) if result.rebalance_dates else None,
        "universe_size": result.universe_size,
        "total_observations": result.observations,
        "quintile_table": quintile_table,
        "weight_suggestion": w,
        "weight_confidence": confidence,
        "top_weight_combos": result.weight_search_results[:5],
        "ic_neutralized_by_horizon": result.ic_neutralized_by_horizon,
        "ic_significance": result.ic_significance,
        "ic_neutralized_significance": getattr(result, "ic_neutralized_significance", {}),
        "factor_exposures": result.factor_exposures,
        "survivorship_stats": result.survivorship_stats,
        "warnings": result.warnings,
    }

    # ------------------------------------------------------------------
    # .env config lines
    # ------------------------------------------------------------------
    weight_config = (
        f"# Backtest-optimised weights (structural score only)\n"
        f"SCORE_WEIGHT_GROWTH={w.get('growth', 0.30)}\n"
        f"SCORE_WEIGHT_QUALITY={w.get('quality', 0.25)}\n"
        f"SCORE_WEIGHT_VALUATION={w.get('valuation', 0.15)}\n"
        f"SCORE_WEIGHT_MARKET={w.get('market', 0.30)}\n"
        f"SCORE_WEIGHT_NARRATIVE=0.10\n"
        f"# Narrative is used as a filter, not in the primary score\n"
        f"SCORE_NARRATIVE_AS_FILTER=true\n"
    )

    # ------------------------------------------------------------------
    # Text summary
    # ------------------------------------------------------------------
    lines: list[str] = [
        "=" * 70,
        "  APEX BACKTEST REPORT",
        "=" * 70,
        f"  Universe size    : {result.universe_size} assets",
        f"  Rebalance dates  : {len(result.rebalance_dates)}"
        + (f" ({result.rebalance_dates[0]} → {result.rebalance_dates[-1]})" if result.rebalance_dates else ""),
        f"  Total observations: {result.observations}",
        "",
        "  QUINTILE RETURNS (Q1 = top structural score)",
        "  " + "-" * 66,
    ]

    for h in result.horizons:
        horizon_label = f"~{h // 21}M"
        lines.append(f"  Horizon {horizon_label} ({h}d):")
        for q in range(1, 6):
            key = f"Q{q}_{h}d"
            m = result.quintile_means.get(key, 0.0)
            s = result.quintile_stdev.get(key, 0.0)
            bar = ("▲" if m >= 0 else "▼") * min(20, max(1, int(abs(m) / 2)))
            lines.append(f"    Q{q}: {m:+7.2f}%  ±{s:.2f}%  {bar}")
        spread = result.q1_q5_spread.get(h, 0.0)
        lines.append(f"    Q1-Q5 spread: {spread:+.2f}%  → {_interpret_spread(spread, h)}")
        ic = result.ic_by_horizon.get(h, 0.0)
        sig = result.ic_significance.get(h, {})
        t = sig.get("t_stat", 0.0)
        p = sig.get("p_value", 1.0)
        n_obs = sig.get("n", 0)
        sig_label = (
            "★★ significant at 1%" if sig.get("significant_1pct") else
            "★  significant at 5%" if sig.get("significant_5pct") else
            "   NOT significant"
        )
        lines.append(f"    IC (Spearman): {_interpret_ic(ic)}")
        lines.append(f"    Significance : t={t:+.3f}  p={p:.4f}  n={n_obs}  {sig_label}")
        lines.append("")

    lines += [
        "  WEIGHT OPTIMISATION (maximises 6M Q1-Q5 spread)",
        "  " + "-" * 66,
        f"  Recommended : growth={w.get('growth'):.2f}  quality={w.get('quality'):.2f}  market={w.get('market'):.2f}",
        f"  Confidence  : {confidence}",
        "",
    ]

    if result.weight_search_results:
        lines.append("  Top 5 weight combos (6M):")
        for i, combo in enumerate(result.weight_search_results[:5], 1):
            cw = combo["weights"]
            lines.append(
                f"    #{i}  g={cw.get('growth',0):.1f} q={cw.get('quality',0):.1f} v={cw.get('valuation',0):.1f} m={cw.get('market',0):.1f}"
                f"  spread={combo['spread_6m']:+.2f}%"
                f"  Q1={combo['q1_mean_6m']:+.2f}%  Q5={combo['q5_mean_6m']:+.2f}%"
            )
        lines.append("")

    # ── Factor neutralization ────────────────────────────────────────────
    lines += [
        "  FACTOR NEUTRALIZATION (IC vs factor-neutral returns)",
        "  " + "-" * 66,
    ]
    if result.ic_neutralized_by_horizon:
        for h in result.horizons:
            ic_raw   = result.ic_by_horizon.get(h, 0.0)
            ic_neut  = result.ic_neutralized_by_horizon.get(h, 0.0)
            label    = f"~{h // 21}M ({h}d)"
            delta    = ic_neut - ic_raw
            verdict  = (
                "GENUINE ALPHA — survives factor adjustment"
                if abs(ic_neut) >= 0.05
                else "WEAK — may be mostly factor exposure"
                if abs(ic_neut) >= 0.02
                else "NOISE — not distinguishable from factor beta"
            )
            sig_raw  = result.ic_significance.get(h, {})
            sig_neut = getattr(result, "ic_neutralized_significance", {}).get(h, {})
            p_raw  = sig_raw.get("p_value", 1.0)
            p_neut = sig_neut.get("p_value", 1.0)
            lines.append(
                f"    {label:10s}  IC_raw={ic_raw:+.4f}(p={p_raw:.3f})  "
                f"IC_neutral={ic_neut:+.4f}(p={p_neut:.3f})  delta={delta:+.4f}  → {verdict}"
            )
    else:
        lines.append("    Factor neutralization not run (network unavailable or disabled).")
    lines.append("")

    # ── Factor exposures ─────────────────────────────────────────────────
    if result.factor_exposures:
        lines += [
            "  AVERAGE UNIVERSE FACTOR EXPOSURES",
            "  " + "-" * 66,
        ]
        for factor, stats in sorted(result.factor_exposures.items()):
            lines.append(
                f"    {factor:10s}  β_mean={stats['mean']:+.3f}  "
                f"β_stdev={stats['stdev']:.3f}  (n={stats['n']})"
            )
        lines.append("")

    # ── Survivorship ─────────────────────────────────────────────────────
    lines += [
        "  SURVIVORSHIP BIAS CORRECTION",
        "  " + "-" * 66,
    ]
    ss = result.survivorship_stats
    if ss.get("available"):
        lines += [
            f"    History: {ss.get('history_start')} → {ss.get('history_end')}",
            f"    Dates corrected: {ss.get('dates_with_correction')} / {ss.get('rebalance_dates_total')}",
            f"    Avg tickers excluded per date: {ss.get('avg_tickers_excluded_per_date')} "
            f"(these were added AFTER the rebalance date — look-ahead bias removed)",
        ]
        if ss.get("db_tickers_never_in_sp500"):
            lines.append(
                f"    {ss['db_tickers_never_in_sp500']} DB tickers never in S&P500 "
                "(non-index securities — included in non-survivorship runs)"
            )
    else:
        lines.append("    Survivorship correction not applied (network unavailable or disabled).")
    lines.append("")

    lines += [
        "  NARRATIVE SCORE — VERDICT",
        "  " + "-" * 66,
        "  Narrative is NOT included in this backtest.",
        "  Reason: yfinance event dates have look-ahead bias risk.",
        "  Recommendation: use narrative as an entry filter:",
        "    → A stock must be in top 25% by structural score",
        "    → AND have a recent catalyst (analyst upgrade, earnings",
        "       beat, contract) to appear in 'active_setup'.",
        "",
    ]

    if result.warnings:
        lines.append("  WARNINGS")
        lines.append("  " + "-" * 66)
        for w_msg in result.warnings:
            lines.append(f"  ⚠ {w_msg}")
        lines.append("")

    lines += [
        "  .ENV CONFIGURATION",
        "  " + "-" * 66,
    ]
    for line in weight_config.splitlines():
        lines.append(f"  {line}")

    lines.append("=" * 70)

    return {
        "structured": structured,
        "text_summary": "\n".join(lines),
        "weight_config": weight_config,
    }
