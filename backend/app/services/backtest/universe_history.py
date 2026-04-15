"""S&P500 historical constituents — survivorship bias correction.

What this solves
----------------
A backtest built on "companies currently in the database" suffers from
two forms of bias:

1. SURVIVORSHIP BIAS: companies that were delisted, went bankrupt, or
   were acquired are excluded. Their negative returns never enter the
   backtest. The model looks better than it was.

2. LOOK-AHEAD BIAS: companies added to the S&P500 after date T are
   included in the T backtest, but they weren't available to trade on T.
   They were added precisely because they performed well — selection bias.

This module corrects bias #2 completely and partially corrects #1
(for companies that are in the DB but were removed from the index).

Data source
-----------
GitHub: github.com/fja05680/sp500
Maintained CSV of every S&P500 addition and removal since 1957.
Free, no authentication.

URL: https://raw.githubusercontent.com/fja05680/sp500/master/
     S%26P%20500%20Historical%20Components%20%26%20Changes(08-01-2023).csv

Format
------
Each row is a change event. The 'tickers' column contains the complete
list of all S&P500 constituents at that point in time.
"""

from __future__ import annotations

import csv
import io
import logging
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SP500_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes(08-01-2023).csv"
)
_USER_AGENT = "apex-terminal research@apex-terminal.io"
_CACHE_DIR  = Path(__file__).parent.parent.parent.parent / ".cache"
_SP500_CACHE = _CACHE_DIR / "sp500_historical_constituents.csv"


# ─────────────────────────────────────────────────────────────────────────────
# Download + parse
# ─────────────────────────────────────────────────────────────────────────────

def _download_sp500_history() -> str | None:
    """Download the historical constituents CSV from GitHub."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if _SP500_CACHE.exists():
        age_days = (date.today() - date.fromtimestamp(_SP500_CACHE.stat().st_mtime)).days
        if age_days < 30:   # refresh monthly
            return _SP500_CACHE.read_text(encoding="utf-8", errors="ignore")

    logger.info("Downloading S&P500 historical constituents from GitHub…")
    try:
        req = urllib.request.Request(_SP500_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8", errors="ignore")
        _SP500_CACHE.write_text(content, encoding="utf-8")
        logger.info("S&P500 history cached → %s", _SP500_CACHE.name)
        return content
    except Exception as exc:
        logger.warning("S&P500 history download failed: %s", exc)
        return None


def _parse_constituents(content: str) -> list[tuple[date, set[str]]]:
    """
    Parse the CSV into a list of (change_date, constituent_set) tuples,
    sorted oldest first.
    """
    rows: list[tuple[date, set[str]]] = []
    reader = csv.DictReader(io.StringIO(content))

    for row in reader:
        # Date column: 'date' or 'Date'
        date_str = row.get("date") or row.get("Date") or ""
        tickers_str = row.get("tickers") or row.get("Tickers") or ""

        if not date_str.strip() or not tickers_str.strip():
            continue

        try:
            # Parse various date formats
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(date_str.strip(), fmt).date()
                    break
                except ValueError:
                    continue
            else:
                continue
        except Exception:
            continue

        # Parse ticker list (comma-separated, may have quotes)
        tickers = {
            t.strip().upper()
            for t in tickers_str.replace('"', "").replace("'", "").split(",")
            if t.strip()
        }

        if tickers:
            rows.append((d, tickers))

    # Sort oldest first
    rows.sort(key=lambda x: x[0])
    logger.info(
        "Parsed S&P500 history: %d change events, %s → %s",
        len(rows),
        rows[0][0] if rows else "?",
        rows[-1][0] if rows else "?",
    )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Universe history class
# ─────────────────────────────────────────────────────────────────────────────

class SP500UniverseHistory:
    """
    Provides the correct S&P500 universe for any historical date.

    Usage:
        history = SP500UniverseHistory.load()
        tickers_on_date = history.constituents_at(date(2021, 6, 15))
    """

    def __init__(self, events: list[tuple[date, set[str]]]) -> None:
        self._events = events   # sorted oldest first

    @classmethod
    def load(cls) -> "SP500UniverseHistory":
        """Download (or use cache) and parse constituent history."""
        content = _download_sp500_history()
        if content is None:
            logger.warning("S&P500 history unavailable — survivorship correction disabled")
            return cls([])
        events = _parse_constituents(content)
        return cls(events)

    def available(self) -> bool:
        """True if constituent data was successfully loaded."""
        return len(self._events) > 0

    def date_range(self) -> tuple[date | None, date | None]:
        if not self._events:
            return None, None
        return self._events[0][0], self._events[-1][0]

    def constituents_at(self, as_of: date) -> set[str]:
        """
        Return the set of S&P500 tickers that were members on `as_of`.
        Uses the most recent change event at or before `as_of`.
        Returns empty set if date is before the first event.
        """
        if not self._events:
            return set()

        # Binary search for the rightmost event <= as_of
        lo, hi = 0, len(self._events) - 1
        result_idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._events[mid][0] <= as_of:
                result_idx = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if result_idx == -1:
            return set()   # as_of is before any data

        return set(self._events[result_idx][1])

    def filter_asset_ids(
        self,
        asset_ids: list[str],
        ticker_map: dict[str, str],   # asset_id → ticker
        as_of: date,
    ) -> list[str]:
        """
        Filter a list of asset_ids to only those that were S&P500
        constituents on `as_of`.

        Parameters
        ----------
        asset_ids  : list of asset IDs from DB
        ticker_map : mapping from asset_id to ticker symbol
        as_of      : rebalance date

        Returns filtered list. If history is unavailable, returns all.
        """
        if not self.available():
            return asset_ids

        constituents = self.constituents_at(as_of)
        if not constituents:
            return asset_ids   # no data for this date

        filtered = [
            aid for aid in asset_ids
            if ticker_map.get(aid, "").upper() in constituents
        ]

        removed = len(asset_ids) - len(filtered)
        if removed > 0:
            logger.debug(
                "Survivorship filter %s: %d → %d (removed %d look-ahead tickers)",
                as_of, len(asset_ids), len(filtered), removed,
            )

        return filtered

    def survivorship_stats(
        self,
        asset_ids_in_db: list[str],
        ticker_map: dict[str, str],
        rebalance_dates: list[date],
    ) -> dict:
        """
        Compute statistics about survivorship bias correction across
        all rebalance dates.
        """
        if not self.available() or not rebalance_dates:
            return {"available": False, "reason": "No constituent history loaded"}

        first_date, last_date = self.date_range()
        dates_in_range = [
            d for d in rebalance_dates
            if first_date and d >= first_date
        ]

        all_tickers = {ticker_map.get(aid, "").upper() for aid in asset_ids_in_db}
        exclusion_counts: list[int] = []

        for d in dates_in_range:
            constituents = self.constituents_at(d)
            if not constituents:
                continue
            excluded = all_tickers - constituents
            exclusion_counts.append(len(excluded))

        avg_excluded = (
            sum(exclusion_counts) / len(exclusion_counts)
            if exclusion_counts else 0
        )

        # Tickers currently in DB but never in any S&P500 snapshot
        all_historical = set()
        for _, tickers in self._events:
            all_historical |= tickers

        db_never_in_sp500 = {
            t for t in all_tickers if t and t not in all_historical
        }

        return {
            "available":             True,
            "history_start":         str(first_date) if first_date else None,
            "history_end":           str(last_date)  if last_date  else None,
            "rebalance_dates_total": len(rebalance_dates),
            "dates_with_correction": len(dates_in_range),
            "avg_tickers_excluded_per_date": round(avg_excluded, 1),
            "db_tickers_never_in_sp500":     len(db_never_in_sp500),
            "db_tickers_never_in_sp500_list": sorted(db_never_in_sp500)[:20],
            "note": (
                "avg_tickers_excluded = average number of DB tickers excluded "
                "per rebalance date because they were not S&P500 members at that date. "
                "Higher values indicate more look-ahead bias was being injected."
            ),
        }
