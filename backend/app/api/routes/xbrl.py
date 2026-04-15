"""XBRL diagnostics API routes.

Endpoints:
  GET  /xbrl/{ticker}          — fetch and display raw XBRL fundamentals
  GET  /xbrl/{ticker}/compare  — side-by-side XBRL vs yfinance comparison
  GET  /xbrl/{ticker}/timeline — point-in-time filing timeline
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{ticker}", summary="Raw XBRL fundamentals for a ticker")
def get_xbrl_fundamentals(ticker: str) -> dict[str, Any]:
    """
    Fetch XBRL fundamentals directly from SEC EDGAR.
    Shows point-in-time filed dates for each quarter.
    """
    ticker = ticker.upper()
    from app.services.ingestion.xbrl import fetch_xbrl_fundamentals, _get_cik

    cik = _get_cik(ticker)
    if cik is None:
        raise HTTPException(
            status_code=404,
            detail=f"No SEC EDGAR CIK found for {ticker}. "
                   "This ticker may not be a US-listed SEC filer.",
        )

    rows = fetch_xbrl_fundamentals(ticker)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"XBRL data found for CIK {cik} but no quarterly fundamentals parsed. "
                   "Company may predate XBRL mandate (~2009) or use non-standard concepts.",
        )

    return {
        "ticker":   ticker,
        "cik":      cik,
        "quarters": len(rows),
        "source":   "sec_edgar_xbrl_point_in_time",
        "data":     [
            {
                "period":            r["fiscal_period"],
                "filed_date":        str(r["reported_at"])[:10] if r.get("reported_at") else None,
                "revenue":           r.get("revenue"),
                "gross_profit":      r.get("gross_profit"),
                "operating_income":  r.get("operating_income"),
                "net_income":        r.get("net_income"),
                "eps":               r.get("eps"),
                "free_cash_flow":    r.get("free_cash_flow"),
                "cash":              r.get("cash_and_equivalents"),
                "total_debt":        r.get("total_debt"),
                "shares_outstanding":r.get("shares_outstanding"),
                "gross_margin":      round(r["gross_margin"], 4) if r.get("gross_margin") else None,
                "operating_margin":  round(r["operating_margin"], 4) if r.get("operating_margin") else None,
            }
            for r in rows
        ],
    }


@router.get("/{ticker}/compare", summary="XBRL vs yfinance side-by-side comparison")
def compare_xbrl_vs_yfinance(ticker: str) -> dict[str, Any]:
    """
    Fetch the same fundamentals from both XBRL and yfinance and compare.
    Highlights discrepancies — these are caused by yfinance using restated values.
    """
    ticker = ticker.upper()

    # XBRL
    from app.services.ingestion.xbrl import fetch_xbrl_fundamentals
    xbrl_rows = fetch_xbrl_fundamentals(ticker)

    # yfinance
    from app.services.ingestion.providers import YFinanceMarketDataProvider
    try:
        yf_rows = YFinanceMarketDataProvider().fetch_quarterly_fundamentals(ticker)
    except Exception as exc:
        yf_rows = []
        logger.warning("yfinance fetch failed for %s: %s", ticker, exc)

    # Build lookup by fiscal_period
    xbrl_by_period = {r["fiscal_period"]: r for r in xbrl_rows}
    yf_by_period   = {r["fiscal_period"]: r for r in yf_rows}

    common_periods = sorted(
        set(xbrl_by_period) & set(yf_by_period),
        reverse=True,
    )[:8]

    comparisons: list[dict] = []
    for period in common_periods:
        xr = xbrl_by_period[period]
        yr = yf_by_period[period]

        def _diff(field: str) -> dict:
            xv = xr.get(field)
            yv = yr.get(field)
            if xv is None or yv is None:
                return {"xbrl": xv, "yfinance": yv, "delta_pct": None}
            delta = ((yv - xv) / abs(xv) * 100) if xv != 0 else None
            return {
                "xbrl":      round(xv, 2) if isinstance(xv, float) else xv,
                "yfinance":  round(yv, 2) if isinstance(yv, float) else yv,
                "delta_pct": round(delta, 2) if delta is not None else None,
            }

        comparisons.append({
            "period":          period,
            "xbrl_filed":      str(xr.get("reported_at", ""))[:10],
            "yf_reported":     str(yr.get("reported_at", ""))[:10],
            "revenue":         _diff("revenue"),
            "net_income":      _diff("net_income"),
            "operating_income":_diff("operating_income"),
            "free_cash_flow":  _diff("free_cash_flow"),
            "eps":             _diff("eps"),
        })

    restatements = sum(
        1 for c in comparisons
        if any(
            abs(c[f]["delta_pct"] or 0) > 2
            for f in ("revenue", "net_income")
            if c[f]["delta_pct"] is not None
        )
    )

    return {
        "ticker":           ticker,
        "periods_compared": len(comparisons),
        "periods_with_restatement_delta_gt_2pct": restatements,
        "note": (
            "delta_pct > 0 means yfinance has HIGHER value than what was originally filed. "
            "This is caused by restatements. For backtesting, use XBRL filed values."
        ),
        "comparison": comparisons,
    }


@router.get("/{ticker}/timeline", summary="Filing timeline — when the market received each number")
def get_filing_timeline(ticker: str) -> dict[str, Any]:
    """
    Shows exactly when each quarterly result was filed with the SEC.
    This is the point-in-time date — when the information became public.
    """
    ticker = ticker.upper()
    from app.services.ingestion.xbrl import fetch_xbrl_fundamentals

    rows = fetch_xbrl_fundamentals(ticker)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No XBRL data for {ticker}.")

    timeline = []
    for r in rows:
        filed = str(r.get("reported_at", ""))[:10]
        rev = r.get("revenue")
        ni  = r.get("net_income")
        timeline.append({
            "period":          r["fiscal_period"],
            "filed_with_sec":  filed,
            "revenue_filed":   f"${rev/1e9:.2f}B" if rev and rev > 1e9
                               else f"${rev/1e6:.1f}M" if rev and rev > 1e6
                               else str(rev),
            "net_income_filed":f"${ni/1e9:.2f}B" if ni and ni > 1e9
                               else f"${ni/1e6:.1f}M" if ni and abs(ni) > 1e6
                               else str(ni) if ni else "N/A",
            "eps_filed":       r.get("eps"),
            "operating_margin":f"{r['operating_margin']*100:.1f}%" if r.get("operating_margin") else "N/A",
        })

    return {
        "ticker":   ticker,
        "quarters": len(timeline),
        "note":     "filed_with_sec is the point-in-time date. "
                    "Numbers reflect what the market knew on that date, "
                    "before any subsequent restatements.",
        "timeline": timeline,
    }


@router.get("/cache/stats", summary="XBRL disk cache statistics")
def get_cache_stats() -> dict:
    """Return stats about the XBRL disk cache."""
    from app.services.ingestion.xbrl import _XBRL_CACHE_DIR, _XBRL_CACHE_TTL_DAYS
    import os
    from datetime import datetime, timezone

    if not _XBRL_CACHE_DIR.exists():
        return {"files": 0, "total_mb": 0.0, "ttl_days": _XBRL_CACHE_TTL_DAYS, "stale": 0}

    files = list(_XBRL_CACHE_DIR.glob("*.json"))
    now = datetime.now(tz=timezone.utc).timestamp()
    stale = sum(
        1 for f in files
        if (now - f.stat().st_mtime) / 86400 > _XBRL_CACHE_TTL_DAYS
    )
    total_bytes = sum(f.stat().st_size for f in files)
    return {
        "files": len(files),
        "total_mb": round(total_bytes / 1_000_000, 2),
        "ttl_days": _XBRL_CACHE_TTL_DAYS,
        "stale": stale,
        "fresh": len(files) - stale,
        "cache_dir": str(_XBRL_CACHE_DIR),
    }


@router.delete("/cache/{cik}", summary="Invalidate XBRL disk cache for a CIK")
def invalidate_cache(cik: str) -> dict:
    """Delete the cached XBRL facts for a specific CIK (forces re-fetch)."""
    from app.services.ingestion.xbrl import _XBRL_CACHE_DIR, _facts_from_disk
    cik_padded = cik.zfill(10)
    cache_file = _XBRL_CACHE_DIR / f"{cik_padded}.json"
    if cache_file.exists():
        cache_file.unlink()
        _facts_from_disk.cache_clear()
        return {"status": "deleted", "cik": cik_padded}
    return {"status": "not_found", "cik": cik_padded}


@router.delete("/cache", summary="Clear entire XBRL disk cache")
def clear_cache() -> dict:
    """Delete all cached XBRL files. Next ingest will re-fetch from SEC."""
    from app.services.ingestion.xbrl import _XBRL_CACHE_DIR, _facts_from_disk
    if not _XBRL_CACHE_DIR.exists():
        return {"deleted": 0}
    files = list(_XBRL_CACHE_DIR.glob("*.json"))
    for f in files:
        try:
            f.unlink()
        except Exception:
            pass
    _facts_from_disk.cache_clear()
    return {"deleted": len(files)}
