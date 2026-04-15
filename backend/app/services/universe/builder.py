"""Universe builder.

Fetches ticker lists from free public sources (Wikipedia tables, NASDAQ FTP).
Falls back to a hardcoded seed list if network is unavailable.

Usage:
    from app.services.universe.builder import build_universe
    tickers = build_universe(include_sp500=True, include_russell1000=True, include_nasdaq100=True)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded seed – always available, used as fallback and supplement
# ---------------------------------------------------------------------------
_SEED: list[str] = [
    # Mega cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "ORCL",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "AXP", "V", "MA",
    # Healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "AMGN", "GILD",
    # Industrials
    "CAT", "RTX", "HON", "UPS", "GE", "BA", "LMT", "NOC", "DE", "MMM",
    # Consumer
    "WMT", "COST", "PG", "KO", "PEP", "MCD", "SBUX", "NKE", "TGT", "HD",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL",
    # Semiconductors
    "AMD", "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ON", "TXN",
    # Software / cloud
    "CRM", "NOW", "SNOW", "DDOG", "NET", "CRWD", "PANW", "ZS", "FTNT", "SPLK",
    # Biotech / pharma
    "MRNA", "REGN", "VRTX", "BIIB", "ILMN", "SGEN", "ALNY", "INCY", "EXAS", "RARE",
    # Small / mid growth
    "SOFI", "RKLB", "EOSE", "IONQ", "ACHR", "JOBY", "LUNR", "RDDT", "HOOD", "UPST",
    "CAVA", "CELH", "KRYS", "RXRX", "ARKG", "BILL", "AEHR", "SMCI", "WOLF", "LAZR",
]


# ---------------------------------------------------------------------------
# Wikipedia scrapers (no API key required)
# ---------------------------------------------------------------------------

def _fetch_sp500() -> list[str]:
    """Scrape S&P 500 constituents from Wikipedia."""
    try:
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url, header=0)
        df = tables[0]
        col = next((c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()), df.columns[0])
        tickers = df[col].dropna().astype(str).str.replace(".", "-", regex=False).tolist()
        logger.info("S&P 500: fetched %d tickers", len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("S&P 500 fetch failed: %s", exc)
        return []


def _fetch_nasdaq100() -> list[str]:
    """Scrape NASDAQ-100 constituents from Wikipedia."""
    try:
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        tables = pd.read_html(url, header=0)
        # Find table with a Ticker/Symbol column
        for df in tables:
            cols_lower = [c.lower() for c in df.columns]
            for col_name, col_lower in zip(df.columns, cols_lower):
                if "ticker" in col_lower or "symbol" in col_lower:
                    tickers = df[col_name].dropna().astype(str).str.strip().tolist()
                    if len(tickers) > 50:
                        logger.info("NASDAQ-100: fetched %d tickers", len(tickers))
                        return tickers
        logger.warning("NASDAQ-100: could not find ticker column in any table")
        return []
    except Exception as exc:
        logger.warning("NASDAQ-100 fetch failed: %s", exc)
        return []


def _fetch_russell1000() -> list[str]:
    """Scrape Russell 1000 from Wikipedia (may be partial)."""
    try:
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/Russell_1000_Index"
        tables = pd.read_html(url, header=0)
        for df in tables:
            cols_lower = [c.lower() for c in df.columns]
            for col_name, col_lower in zip(df.columns, cols_lower):
                if "ticker" in col_lower or "symbol" in col_lower:
                    tickers = df[col_name].dropna().astype(str).str.strip().tolist()
                    if len(tickers) > 50:
                        logger.info("Russell 1000 (Wikipedia): fetched %d tickers", len(tickers))
                        return tickers
        return []
    except Exception as exc:
        logger.warning("Russell 1000 fetch failed: %s", exc)
        return []


def _fetch_nasdaq_listed() -> list[str]:
    """
    Fetch full NASDAQ-listed tickers from NASDAQ FTP.
    Returns ~3500 active US-listed tickers.
    """
    try:
        import io
        import urllib.request
        url = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        with urllib.request.urlopen(url, timeout=15) as r:
            text = r.read().decode("utf-8", errors="ignore")
        import pandas as pd
        df = pd.read_csv(io.StringIO(text), sep="|")
        if "Symbol" not in df.columns:
            return []
        # Remove test symbols, ETFs, and the trailing header row
        df = df[df["Symbol"].notna()]
        df = df[~df["Symbol"].astype(str).str.contains(r"\$|FILE|Symbol", regex=True)]
        if "ETF" in df.columns:
            df = df[df["ETF"].astype(str).str.strip().str.upper() != "Y"]
        tickers = df["Symbol"].astype(str).str.strip().tolist()
        logger.info("NASDAQ listed: fetched %d tickers", len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("NASDAQ FTP fetch failed: %s", exc)
        return []


def _fetch_other_listed() -> list[str]:
    """Fetch NYSE + other US listed tickers from NASDAQ FTP."""
    try:
        import io
        import urllib.request
        url = "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
        with urllib.request.urlopen(url, timeout=15) as r:
            text = r.read().decode("utf-8", errors="ignore")
        import pandas as pd
        df = pd.read_csv(io.StringIO(text), sep="|")
        col = "ACT Symbol" if "ACT Symbol" in df.columns else df.columns[0]
        df = df[df[col].notna()]
        df = df[~df[col].astype(str).str.contains(r"\$|FILE|Symbol", regex=True)]
        if "ETF" in df.columns:
            df = df[df["ETF"].astype(str).str.strip().str.upper() != "Y"]
        tickers = df[col].astype(str).str.strip().tolist()
        logger.info("Other listed: fetched %d tickers", len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("Other listed FTP fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_universe(
    *,
    include_sp500: bool = True,
    include_nasdaq100: bool = True,
    include_russell1000: bool = True,
    include_nasdaq_ftp: bool = False,   # ~3500 tickers – only enable if you want full market
    include_other_ftp: bool = False,    # NYSE + AMEX listed
    extra_tickers: Sequence[str] = (),
    min_length: int = 100,
) -> list[str]:
    """
    Build a deduplicated, sorted list of US equity tickers.

    Parameters
    ----------
    include_sp500          : scrape S&P 500 from Wikipedia
    include_nasdaq100      : scrape NASDAQ-100 from Wikipedia
    include_russell1000    : scrape Russell 1000 from Wikipedia (partial)
    include_nasdaq_ftp     : fetch full NASDAQ listed file (~3500)
    include_other_ftp      : fetch NYSE/AMEX listed file (~3000)
    extra_tickers          : additional tickers to always include
    min_length             : if result < min_length, seed list is always added
    """
    collected: set[str] = set()

    if include_sp500:
        collected.update(_fetch_sp500())
    if include_nasdaq100:
        collected.update(_fetch_nasdaq100())
    if include_russell1000:
        collected.update(_fetch_russell1000())
    if include_nasdaq_ftp:
        collected.update(_fetch_nasdaq_listed())
    if include_other_ftp:
        collected.update(_fetch_other_listed())

    collected.update(t.upper().strip() for t in extra_tickers)

    # Always include seed if below threshold
    if len(collected) < min_length:
        logger.warning("Universe too small (%d); appending seed list.", len(collected))
        collected.update(_SEED)

    # Sanitise: remove empty strings, whitespace, multi-word entries
    cleaned = sorted(
        t for t in collected
        if t and len(t) <= 6 and " " not in t and t.isalpha()
    )
    logger.info("Final universe: %d tickers", len(cleaned))
    return cleaned


@lru_cache(maxsize=1)
def cached_universe() -> list[str]:
    """Cached universe for use within a single process lifetime."""
    return build_universe()
