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
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "GOOG",
    "TSLA",
    "AVGO",
    "BRKB",
    "JPM",
    "LLY",
    "UNH",
    "XOM",
    "V",
    "MA",
    "PG",
    "JNJ",
    "HD",
    "MRK",
    "ABBV",
    "COST",
    "CVX",
    "WMT",
    "BAC",
    "NFLX",
    "CRM",
    "AMD",
    "TMO",
    "KO",
    "PEP",
    "LIN",
    "ACN",
    "MCD",
    "ABT",
    "CSCO",
    "DHR",
    "ADBE",
    "WFC",
    "TXN",
    "NOW",
    "NEE",
    "PM",
    "IBM",
    "RTX",
    "GE",
    "INTU",
    "CAT",
    "SPGI",
    "QCOM",
    "T",
    "VZ",
    "LOW",
    "AMGN",
    "ISRG",
    "CMCSA",
    "GS",
    "MS",
    "SYK",
    "HON",
    "BKNG",
    "UBER",
    "DE",
    "AMAT",
    "VRTX",
    "BSX",
    "PLD",
    "BLK",
    "AXP",
    "TJX",
    "GILD",
    "MDT",
    "ELV",
    "ADI",
    "C",
    "MMC",
    "REGN",
    "PGR",
    "MU",
    "ADP",
    "SCHW",
    "ETN",
    "LRCX",
    "ZTS",
    "BMY",
    "SHW",
    "DUK",
    "CB",
    "SO",
    "CI",
    "CL",
    "INTC",
    "KLAC",
    "CME",
    "HCA",
    "MCO",
    "NOC",
    "TGT",
    "PH",
    "USB",
    "EMR",
    "EOG",
    "MO",
    "GD",
    "APH",
    "WM",
    "COF",
    "APD",
    "NSC",
    "ITW",
    "CEG",
    "MMM",
    "FCX",
    "PSX",
    "HLT",
    "SLB",
    "ICE",
    "ECL",
    "FDX",
    "ORLY",
    "MSI",
    "NKE",
    "PCAR",
    "OXY",
    "MCK",
    "MAR",
    "ROST",
    "CTAS",
    "TT",
    "FTNT",
    "AIG",
    "MNST",
    "ROP",
    "BK",
    "COP",
    "A",
    "PAYX",
    "WELL",
    "MSCI",
    "VRSK",
    "AZO",
    "CMI",
    "GEHC",
    "IDXX",
    "TDG",
    "YUM",
    "ODFL",
    "DXCM",
    "PCG",
    "AFL",
    "CPRT",
    "SPG",
    "CARR",
    "PWR",
    "GWW",
    "FAST",
    "ACGL",
    "URI",
    "ALL",
    "EW",
    "KMB",
    "FICO",
    "RSG",
    "DOW",
    "MTD",
    "F",
    "LMT",
    "SRE",
    "NEM",
    "KHC",
    "GLW",
    "DLTR",
    "CSGP",
    "BDX",
    "AME",
    "AWK",
    "CTVA",
    "STZ",
    "ROK",
    "PPG",
    "IQV",
    "WTW",
    "VICI",
    "PRU",
    "OTIS",
    "LHX",
    "IR",
    "FANG",
    "EXC",
    "MRNA",
    "BIIB",
    "HPQ",
    "SBUX",
    "LEN",
    "GIS",
    "DG",
    "PEG",
    "CCI",
    "TROW",
    "KEYS",
    "EFX",
    "CTSH",
    "VMC",
    "WAB",
    "TSN",
    "NUE",
    "EBAY",
    "BR",
    "HAL",
    "BAX",
    "AVB",
    "CF",
    "TFC",
    "WEC",
    "MTB",
    "AEP",
    "D",
    "INVH",
    "ULTA",
    "XYL",
    "ETR",
    "ED",
    "ANSS",
    "CDW",
    "LDOS",
    "ES",
    "DVN",
    "PPL",
    "HPE",
    "IFF",
    "BALL",
    "EQT",
    "EXPD",
    "AEE",
    "LYB",
    "ZBRA",
    "DOV",
    "ALGN",
    "TER",
    "OMC",
    "NTAP",
    "IRM",
    "CAG",
    "STT",
    "MAA",
    "SWKS",
    "HOLX",
    "FFIV",
    "AKAM",
    "MKC",
    "CINF",
    "FITB",
    "CBOE",
    "HSY",
    "TRMB",
    "POOL",
    "TDY",
    "J",
    "EVRG",
    "HIG",
    "STE",
    "CHRW",
    "LW",
    "UAL",
    "LVS",
    "NDAQ",
    "WYNN",
    "ALLE",
    "JKHY",
    "MHK",
    "FMC",
    "PNW",
    "RL",
    "AIZ",
    "BBWI",
    "BWA",
    "HAS",
    "NWL",
    "IVZ",
    "XRAY",
    "LEG",
    "PKG",
    "CMS",
    "GNRC",
    "SOFI",
    "RKLB",
    "HOOD",
    "RDDT",
    "IONQ",
    "ACHR",
    "JOBY",
    "RIVN",
    "WOLF",
    "SMCI",
    "PLTR",
    "SNOW",
    "DDOG",
    "NET",
    "CRWD",
    "PANW",
    "ZS",
    "OKTA",
    "BILL",
    "HUBS",
    "SHOP",
    "SQ",
    "PYPL",
    "COIN",
    "CELH",
    "CAVA",
    "WING",
    "SHAK",
    "CMG",
    "TXRH",
    "DPZ",
    "RXRX",
    "BEAM",
    "EDIT",
    "CRSP",
    "NTLA",
    "FATE",
    "UPST",
    "AFRM",
    "OPEN",
    "RDFN",
    "Z",
    "RBLX",
    "TTWO",
    "EA",
    "U",
    "PATH",
    "APPN",
    "ALRM",
    "FOUR",
    "HIMS",
    "NTRA",
    "VEEV"
]


# ---------------------------------------------------------------------------
# Wikipedia scrapers (no API key required)
# ---------------------------------------------------------------------------


# ═══════════════════════════════════════════════════════════════════════
# GitHub-hosted JSON sources (no scraping, no rate limit)
# ═══════════════════════════════════════════════════════════════════════


def _fetch_universe_from_finnhub(exchange: str = "US") -> list[str]:
    """
    Fetch all tradable symbols from Finnhub.
    Free tier: covers NYSE + NASDAQ. Returns ~7000+ active US tickers.
    """
    import os, urllib.request, json as _json
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        logger.warning("FINNHUB_API_KEY not set — skipping Finnhub universe fetch")
        return []

    url = f"https://finnhub.io/api/v1/stock/symbol?exchange={exchange}&token={api_key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "apex-terminal/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = _json.loads(r.read().decode("utf-8"))

        # Filter: only common stocks, no OTC, no 5+ letter tickers (usually warrants/units)
        tickers = []
        for row in data:
            sym = row.get("symbol", "").strip().upper()
            type_ = row.get("type", "")
            if not sym or len(sym) > 5:
                continue
            if "." in sym or "-" in sym:
                # Keep -A, -B share classes (BRK-B)
                if sym.count("-") > 1 or "." in sym:
                    continue
            # Filter out warrants, rights, units (suffixes like W, R, U)
            if type_ and type_ not in ("Common Stock", "ETP", "ADR"):
                continue
            tickers.append(sym)

        logger.info("Finnhub universe (%s): %d tickers", exchange, len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("Finnhub universe fetch failed: %s", exc)
        return []



def _fetch_github_csv_or_json(url: str, description: str) -> list[str]:
    """Fetch a CSV or JSON from GitHub and extract tickers."""
    try:
        import urllib.request, csv, io, json as _json
        req = urllib.request.Request(url, headers={'User-Agent': 'apex-terminal/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            content = r.read().decode('utf-8', errors='ignore')

        tickers: list[str] = []
        if url.endswith('.csv'):
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                for field in ('Symbol', 'symbol', 'Ticker', 'ticker'):
                    if field in row and row[field]:
                        tickers.append(str(row[field]).strip().upper())
                        break
        else:
            data = _json.loads(content)
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        for field in ('Symbol', 'symbol', 'Ticker', 'ticker'):
                            if field in row and row[field]:
                                tickers.append(str(row[field]).strip().upper())
                                break
                    elif isinstance(row, str):
                        tickers.append(row.strip().upper())

        # Clean and dedupe
        tickers = list(dict.fromkeys(
            t.replace('.', '-') for t in tickers
            if t and len(t) <= 6 and t.replace('-', '').isalnum()
        ))
        logger.info("%s: fetched %d tickers", description, len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("%s fetch failed: %s", description, exc)
        return []


def _fetch_github_json(url: str, description: str) -> list[str]:
    """Fetch a JSON list of tickers from GitHub."""
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(url, headers={'User-Agent': 'apex-terminal/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = _json.loads(r.read().decode('utf-8'))

        # Handle different formats
        if isinstance(data, list):
            if data and isinstance(data[0], str):
                tickers = data
            elif data and isinstance(data[0], dict):
                # Try common field names
                for field in ('Symbol', 'symbol', 'ticker', 'Ticker'):
                    if field in data[0]:
                        tickers = [row.get(field, '').strip().upper() for row in data]
                        tickers = [t for t in tickers if t]
                        break
                else:
                    tickers = []
            else:
                tickers = []
        elif isinstance(data, dict):
            # Sometimes wrapped in a key
            for key in ('symbols', 'tickers', 'constituents', 'data'):
                if key in data and isinstance(data[key], list):
                    tickers = [str(t).strip().upper() for t in data[key]]
                    break
            else:
                tickers = []
        else:
            tickers = []

        # Clean and dedupe
        tickers = list(dict.fromkeys(
            t.replace('.', '-') for t in tickers
            if t and '.' not in t[:3] and len(t) <= 6
        ))
        logger.info("%s: fetched %d tickers from GitHub", description, len(tickers))
        return tickers
    except Exception as exc:
        logger.warning("%s fetch failed: %s", description, exc)
        return []


def _fetch_sp500_github() -> list[str]:
    """S&P500 constituents - try multiple maintained sources."""
    sources = [
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    ]
    for url in sources:
        result = _fetch_github_csv_or_json(url, "S&P 500 (GitHub)")
        if result:
            return result
    return []


def _fetch_nasdaq100_github() -> list[str]:
    """NASDAQ-100 constituents from GitHub. Fallback to hardcoded list."""
    # Static NASDAQ-100 list (updated Jan 2026 — manually maintained)
    # This is reliable; no external dependency
    nasdaq100 = [
        "AAPL","MSFT","GOOG","GOOGL","AMZN","NVDA","META","TSLA","AVGO","COST",
        "NFLX","ADBE","AMD","PEP","CSCO","TMUS","INTC","QCOM","LIN","TXN",
        "INTU","AMGN","HON","ISRG","AMAT","BKNG","VRTX","LRCX","ADP","MU",
        "PANW","GILD","MDLZ","REGN","ADI","SBUX","KLAC","SNPS","CDNS","MELI",
        "CRWD","ASML","PDD","CTAS","CMCSA","ORLY","PYPL","MAR","ABNB","CEG",
        "MRVL","FTNT","DASH","MNST","WDAY","ADSK","CHTR","CSX","NXPI","PCAR",
        "ROP","FANG","AEP","PAYX","ODFL","LULU","KDP","FAST","EXC","BKR",
        "VRSK","KHC","EA","XEL","CTSH","MCHP","CCEP","AZN","GEHC","ON",
        "CPRT","IDXX","TEAM","CSGP","BIIB","DDOG","ZS","DXCM","ANSS","ARM",
        "TTD","MDB","WBD","ILMN","ROST","SMCI","CDW","MRNA","DLTR","SIRI",
    ]
    logger.info("NASDAQ-100 (static): %d tickers", len(nasdaq100))
    return nasdaq100


def _fetch_russell1000_github() -> list[str]:
    """Russell 1000 - try GitHub JSON sources with multiple fallbacks."""
    sources = [
        "https://raw.githubusercontent.com/datasets/russell-1000/master/data/constituents.json",
        "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_full_tickers.json",
        "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_full_tickers.json",
    ]
    all_tickers = set()
    for url in sources:
        result = _fetch_github_json(url, "Russell/Stocks (GitHub)")
        all_tickers.update(result)
    return list(all_tickers)


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

    # Try Finnhub universe first (has API key, ~7000 US tickers)
    finnhub_tickers = _fetch_universe_from_finnhub("US")
    if finnhub_tickers:
        # Finnhub returns all US listings — this alone covers S&P500+NASDAQ100+R1000
        collected.update(finnhub_tickers)
        logger.info("Using Finnhub universe (%d tickers), skipping individual index fetches", len(finnhub_tickers))
    else:
        # Fallback: GitHub + Wikipedia scraping
        if include_sp500:
            sp500 = _fetch_sp500_github() or _fetch_sp500()
            collected.update(sp500)
        if include_nasdaq100:
            nq = _fetch_nasdaq100_github() or _fetch_nasdaq100()
            collected.update(nq)
        if include_russell1000:
            r1k = _fetch_russell1000_github() or _fetch_russell1000()
            collected.update(r1k)
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
