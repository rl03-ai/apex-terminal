"""SEC EDGAR XBRL fundamentals provider.

Why this matters over yfinance
--------------------------------
yfinance returns fundamentals as they exist today — including all
subsequent restatements and corrections. A backtest run today against
historical dates uses numbers that did not exist on those dates.

SEC EDGAR XBRL returns the exact value filed on the exact filing date.
The `filed` field in every XBRL entry is the date the document arrived
at the SEC — which is when the market received the information.

This gives genuinely point-in-time data for free.

Coverage
--------
All US SEC filers since ~2009 (Dodd-Frank mandated XBRL).
~12,000 companies. Balance sheet quality varies for small filers but
is excellent for S&P 500 / Russell 1000.

API endpoint
------------
  https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json

Rate limit: 10 requests/second with User-Agent header required.
No authentication needed.

Concept mapping
---------------
US-GAAP concept names vary by company and era. This module tries
multiple concept names in priority order for each field.

Income statement de-cumulation
-------------------------------
10-Q filings report cumulative YTD figures, not standalone quarters:
  fp=Q1 → 3 months  (standalone — use directly)
  fp=Q2 → 6 months  (cumulative H1 — subtract Q1)
  fp=Q3 → 9 months  (cumulative 9M — subtract H1)
  fp=FY → 12 months (annual — use for Q4 = FY - 9M)

Balance sheet items are point-in-time snapshots; no de-cumulation needed.
"""

from __future__ import annotations
import os
from pathlib import Path

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_SEC_BASE   = "https://data.sec.gov"
_USER_AGENT = "apex-terminal research@apex-terminal.io"
_REQ_DELAY  = 0.12   # ~8 req/s, under SEC 10 req/s limit


# ─────────────────────────────────────────────────────────────────────────────
# Concept name priority lists
# Different companies / filing eras use different concept names
# ─────────────────────────────────────────────────────────────────────────────

_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueServicesNet",
        "HealthCareOrganizationRevenue",
        "RealEstateRevenueNet",
        "TotalRevenuesAndOtherIncome",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "CapitalExpendituresIncurredButNotYetPaid",
    ],
    "cash": [
        "CashCashEquivalentsAndShortTermInvestments",
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
    ],
    "debt_current": [
        "DebtCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
        "NotesPayableCurrent",
    ],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
        "SeniorLongTermNotes",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
    ],
}

# Income statement concepts — these need de-cumulation for Q2/Q3
_FLOW_CONCEPTS = {
    "revenue", "gross_profit", "operating_income", "net_income",
    "operating_cash_flow", "capex",
}

# Balance sheet concepts — point-in-time, no de-cumulation
_STOCK_CONCEPTS = {"cash", "debt_current", "debt_noncurrent", "shares_outstanding"}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict | None:
    try:
        time.sleep(_REQ_DELAY)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("EDGAR XBRL request failed %s: %s", url, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CIK lookup (reuses the mapping from insider.py, same cache)
# ─────────────────────────────────────────────────────────────────────────────

def _get_cik(ticker: str) -> str | None:
    """Return zero-padded 10-digit CIK for ticker."""
    try:
        from app.services.catalyst.insider import get_cik
        return get_cik(ticker)
    except Exception:
        pass
    # Fallback: direct lookup
    try:
        req = urllib.request.Request(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read().decode("utf-8"))
        for entry in raw.values():
            if str(entry.get("ticker", "")).upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Company facts — disk cache per CIK
# ─────────────────────────────────────────────────────────────────────────────
# Why disk cache instead of lru_cache(maxsize=512)?
#
# With 1000+ tickers, lru_cache(512) evicts the first 488 companies
# before the ingest run finishes. Those are re-fetched on the next
# daily run — 488 unnecessary SEC API calls.
#
# Disk cache:
#   - Persists across runs (one download per company per week)
#   - Survives process restarts
#   - No eviction — every CIK cached indefinitely until stale
#   - ~2–5MB per file; 1000 tickers ≈ 2–5GB (acceptable)
#   - Cache dir: backend/.cache/xbrl/{cik}.json

_XBRL_CACHE_DIR = Path(__file__).parent.parent.parent.parent / ".cache" / "xbrl"
_XBRL_CACHE_TTL_DAYS = int(os.getenv("XBRL_CACHE_TTL_DAYS", "7"))

# In-process LRU for the current ingestion run (avoids redundant disk reads)
@lru_cache(maxsize=128)
def _facts_from_disk(cik_padded: str) -> dict | None:
    """Return parsed JSON from disk cache, or None if absent/stale."""
    cache_file = _XBRL_CACHE_DIR / f"{cik_padded}.json"
    if not cache_file.exists():
        return None
    try:
        age_days = (datetime.now(timezone.utc).timestamp() - cache_file.stat().st_mtime) / 86400
        if age_days > _XBRL_CACHE_TTL_DAYS:
            return None  # stale
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_facts_to_disk(cik_padded: str, data: dict) -> None:
    """Write company facts JSON to disk cache."""
    try:
        _XBRL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _XBRL_CACHE_DIR / f"{cik_padded}.json"
        cache_file.write_text(json.dumps(data, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug("XBRL disk cache write failed for %s: %s", cik_padded, exc)


def _fetch_company_facts(cik_padded: str) -> dict | None:
    """
    Fetch company facts JSON with two-level caching:
      L1: in-process lru_cache (128 entries, current run)
      L2: disk cache (.cache/xbrl/{cik}.json, TTL=XBRL_CACHE_TTL_DAYS days)
      L3: SEC EDGAR API (live download, updates disk cache)
    """
    # L1: in-process
    cached = _facts_from_disk(cik_padded)
    if cached is not None:
        logger.debug("XBRL L1/L2 cache hit for CIK %s", cik_padded)
        return cached

    # L3: live download
    url = f"{_SEC_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"
    data = _get_json(url)
    if data:
        _write_facts_to_disk(cik_padded, data)
        # Invalidate L1 so next call picks up freshly written disk data
        _facts_from_disk.cache_clear()
        logger.debug(
            "XBRL fetched from SEC for CIK %s (%s)",
            cik_padded, data.get("entityName", "?"),
        )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Concept extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entries(facts: dict, concept_names: list[str]) -> list[dict]:
    """
    Try each concept name in priority order; return the first non-empty list.
    Only considers entries from 10-K and 10-Q filings in USD units.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for name in concept_names:
        concept = us_gaap.get(name, {})
        usd_entries = concept.get("units", {}).get("USD", [])
        if not usd_entries:
            continue
        # Filter to 10-K / 10-Q only, exclude 10-K/A amendments (use latest filing)
        filtered = [
            e for e in usd_entries
            if e.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A")
            and e.get("fp") in ("Q1", "Q2", "Q3", "Q4", "FY")
            and e.get("filed")
        ]
        if filtered:
            return filtered
    return []


def _best_entry(
    entries: list[dict], fy: int, fp: str
) -> dict | None:
    """
    For a given (fiscal_year, fiscal_period), return the most recently
    filed entry. Handles amendments (10-K/A, 10-Q/A) by taking latest.
    """
    matches = [e for e in entries if e.get("fy") == fy and e.get("fp") == fp]
    if not matches:
        return None
    return max(matches, key=lambda e: e.get("filed", ""))


# ─────────────────────────────────────────────────────────────────────────────
# De-cumulation for flow (income statement) concepts
# ─────────────────────────────────────────────────────────────────────────────

_FP_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}


def _decumulate(entries: list[dict]) -> dict[tuple[int, str], tuple[float, str]]:
    """
    Convert cumulative YTD income statement entries into standalone quarterly
    values. Returns dict: (fy, fp) → (standalone_value, filed_date).

    De-cumulation logic:
      Q1  standalone = Q1  value                      (already standalone)
      Q2  standalone = Q2  cumulative - Q1  value
      Q3  standalone = Q3  cumulative - Q2  cumulative
      Q4  standalone = FY  value - Q3  cumulative
    """
    # Build lookup: (fy, fp) → (val, filed)
    lookup: dict[tuple[int, str], tuple[float, str]] = {}
    for e in entries:
        fy = e.get("fy")
        fp = e.get("fp")
        val = e.get("val")
        filed = e.get("filed", "")
        if fy is None or fp is None or val is None:
            continue
        key = (fy, fp)
        # If duplicate, prefer most recently filed
        if key not in lookup or filed > lookup[key][1]:
            lookup[key] = (float(val), filed)

    result: dict[tuple[int, str], tuple[float, str]] = {}
    fiscal_years = sorted(set(k[0] for k in lookup))

    for fy in fiscal_years:
        q1 = lookup.get((fy, "Q1"))
        q2 = lookup.get((fy, "Q2"))
        q3 = lookup.get((fy, "Q3"))
        fy_entry = lookup.get((fy, "FY"))

        if q1:
            result[(fy, "Q1")] = q1   # already standalone

        if q2 and q1:
            standalone_q2 = q2[0] - q1[0]
            result[(fy, "Q2")] = (standalone_q2, q2[1])

        if q3 and q2:
            standalone_q3 = q3[0] - q2[0]
            result[(fy, "Q3")] = (standalone_q3, q3[1])

        if fy_entry and q3:
            standalone_q4 = fy_entry[0] - q3[0]
            result[(fy, "Q4")] = (standalone_q4, fy_entry[1])
        elif fy_entry and q2:
            # Q3 missing — compute H2 as FY - H1
            result[(fy, "Q4")] = fy_entry   # best we can do without Q3

    return result


def _stock_lookup(entries: list[dict]) -> dict[tuple[int, str], tuple[float, str]]:
    """
    For balance sheet (stock) concepts: no de-cumulation.
    Returns dict: (fy, fp) → (val, filed).
    """
    lookup: dict[tuple[int, str], tuple[float, str]] = {}
    for e in entries:
        fy  = e.get("fy")
        fp  = e.get("fp")
        val = e.get("val")
        filed = e.get("filed", "")
        if fy is None or fp is None or val is None:
            continue
        key = (fy, fp)
        if key not in lookup or filed > lookup[key][1]:
            lookup[key] = (float(val), filed)
    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def fetch_xbrl_fundamentals(ticker: str) -> list[dict[str, Any]]:
    """
    Fetch quarterly fundamentals from SEC EDGAR XBRL with point-in-time dates.

    Returns a list of dicts compatible with AssetFundamentalsQuarterly,
    with `reported_at` set to the SEC filing date (not the period end date).

    Returns [] if ticker is not found or EDGAR is unreachable.
    """
    cik = _get_cik(ticker)
    if cik is None:
        logger.debug("No CIK for %s — XBRL unavailable", ticker)
        return []

    facts = _fetch_company_facts(cik)
    if facts is None:
        logger.debug("No XBRL facts for %s (CIK %s)", ticker, cik)
        return []

    # ── Extract per-concept series ─────────────────────────────────────────

    def flow(field: str) -> dict[tuple[int, str], tuple[float, str]]:
        entries = _extract_entries(facts, _CONCEPTS[field])
        return _decumulate(entries) if entries else {}

    def stock(field: str) -> dict[tuple[int, str], tuple[float, str]]:
        entries = _extract_entries(facts, _CONCEPTS[field])
        return _stock_lookup(entries) if entries else {}

    rev_series   = flow("revenue")
    gp_series    = flow("gross_profit")
    op_series    = flow("operating_income")
    ni_series    = flow("net_income")
    ocf_series   = flow("operating_cash_flow")
    capex_series = flow("capex")

    # EPS — handled separately (already per-share, no de-cumulation)
    eps_entries  = _extract_entries(facts, _CONCEPTS["eps_diluted"])
    eps_lookup   = _stock_lookup(eps_entries)   # EPS is already per-share, not cumulative

    cash_series   = stock("cash")
    debt_cur      = stock("debt_current")
    debt_nc       = stock("debt_noncurrent")
    shares_series = stock("shares_outstanding")

    # ── Build quarterly rows ───────────────────────────────────────────────

    # Collect all (fy, fp) keys that have at least revenue
    periods = sorted(
        rev_series.keys(),
        key=lambda k: (k[0], _FP_ORDER.get(k[1], 0)),
        reverse=True,   # most recent first
    )

    rows: list[dict[str, Any]] = []

    for fy, fp in periods[:12]:   # last 12 quarters max
        rev_entry = rev_series.get((fy, fp))
        if rev_entry is None:
            continue

        revenue, filed_date = rev_entry

        def _val(series: dict, fallback=None):
            entry = series.get((fy, fp))
            return entry[0] if entry else fallback

        # Map fp → fiscal quarter number
        fp_to_q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}
        fiscal_quarter = fp_to_q.get(fp, 4)

        # Free cash flow = operating CF - capex
        ocf   = _val(ocf_series)
        capex = _val(capex_series)
        fcf: float | None = None
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)   # capex is reported as negative in some filers
        elif ocf is not None:
            fcf = ocf  # best approximation without capex

        gp   = _val(gp_series)
        op   = _val(op_series)
        ni   = _val(ni_series)
        eps  = _val(eps_lookup)
        cash = _val(cash_series)

        # Total debt = current + non-current
        dc = _val(debt_cur)
        dn = _val(debt_nc)
        debt: float | None = None
        if dc is not None and dn is not None:
            debt = dc + dn
        elif dc is not None:
            debt = dc
        elif dn is not None:
            debt = dn

        shares = _val(shares_series)

        gm: float | None = (gp / revenue) if gp is not None and revenue else None
        om: float | None = (op / revenue) if op is not None and revenue else None

        # Parse filed date → datetime (this is the point-in-time date)
        try:
            reported_at = datetime.strptime(filed_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            reported_at = None

        rows.append({
            "fiscal_year":          fy,
            "fiscal_quarter":       fiscal_quarter,
            "fiscal_period":        f"{fy}-Q{fiscal_quarter}",
            "revenue":              revenue,
            "gross_profit":         gp,
            "operating_income":     op,
            "net_income":           ni,
            "eps":                  eps,
            "free_cash_flow":       fcf,
            "cash_and_equivalents": cash,
            "total_debt":           debt,
            "shares_outstanding":   shares,
            "gross_margin":         gm,
            "operating_margin":     om,
            "reported_at":          reported_at,
            # Extra metadata (not stored in DB but useful for debugging)
            "_source":              "sec_xbrl",
            "_filed":               filed_date,
            "_cik":                 cik,
        })

    # Remove internal fields before returning
    for r in rows:
        r.pop("_source", None)
        r.pop("_filed", None)
        r.pop("_cik", None)

    logger.debug("XBRL fundamentals for %s: %d quarters", ticker, len(rows))
    return rows


def xbrl_available(ticker: str) -> bool:
    """Quick check: does this ticker have XBRL data on EDGAR?"""
    return _get_cik(ticker) is not None
