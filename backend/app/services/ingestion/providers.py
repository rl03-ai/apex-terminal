"""Configurable market data providers.

This module now supports:
- DemoMarketDataProvider for deterministic local data.
- YFinanceMarketDataProvider for real market data.
- get_market_data_provider() factory to choose the active source from settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import httpx

from app.core.config import Settings


@dataclass
class AssetProfile:
    ticker: str
    name: str
    exchange: str = 'NASDAQ'
    sector: str | None = None
    industry: str | None = None
    country: str | None = 'US'
    currency: str = 'USD'
    market_cap: float | None = None


class MarketDataProvider(Protocol):
    def fetch_asset_profile(self, ticker: str) -> dict[str, Any]: ...
    def fetch_price_history(self, ticker: str, days: int = 260) -> list[dict[str, Any]]: ...
    def fetch_quarterly_fundamentals(self, ticker: str) -> list[dict[str, Any]]: ...
    def fetch_events(self, ticker: str) -> list[dict[str, Any]]: ...


class DemoMarketDataProvider:
    _catalog = {
        'SOFI': AssetProfile('SOFI', 'SoFi Technologies', sector='Financial Services', industry='Fintech Platform', market_cap=15_200_000_000),
        'RKLB': AssetProfile('RKLB', 'Rocket Lab USA', sector='Industrials', industry='Space Launch & Defense', market_cap=5_900_000_000),
        'NOW': AssetProfile('NOW', 'ServiceNow', sector='Technology', industry='Cloud AI Software', market_cap=160_000_000_000),
        'EOSE': AssetProfile('EOSE', 'Eos Energy Enterprises', sector='Industrials', industry='Battery Energy Storage', market_cap=850_000_000),
    }

    def fetch_asset_profile(self, ticker: str) -> dict[str, Any]:
        profile = self._catalog.get(ticker.upper()) or AssetProfile(ticker.upper(), f'{ticker.upper()} Inc.')
        return profile.__dict__

    def fetch_price_history(self, ticker: str, days: int = 260) -> list[dict[str, Any]]:
        base_prices = {'SOFI': 11.2, 'RKLB': 18.0, 'NOW': 760.0, 'EOSE': 4.8}
        growth_bias = {'SOFI': 0.0007, 'RKLB': 0.0012, 'NOW': 0.0008, 'EOSE': 0.0002}
        volume_base = {'SOFI': 45_000_000, 'RKLB': 14_000_000, 'NOW': 1_300_000, 'EOSE': 8_000_000}
        today = date.today()
        price = base_prices.get(ticker.upper(), 20.0)
        rows: list[dict[str, Any]] = []
        for idx in range(days, 0, -1):
            d = today - timedelta(days=idx)
            drift = growth_bias.get(ticker.upper(), 0.0005)
            cycle = ((idx % 17) - 8) / 500
            price = max(1.0, price * (1 + drift + cycle / 10))
            rows.append(
                {
                    'date': d,
                    'open': round(price * 0.99, 2),
                    'high': round(price * 1.02, 2),
                    'low': round(price * 0.98, 2),
                    'close': round(price, 2),
                    'adjusted_close': round(price, 2),
                    'volume': int(volume_base.get(ticker.upper(), 2_000_000) * (0.9 + ((idx % 9) / 20))),
                }
            )
        return rows

    def fetch_quarterly_fundamentals(self, ticker: str) -> list[dict[str, Any]]:
        presets = {
            'SOFI': [
                (2024, 2, 600, 300, 90, 65, 0.10, 40, 2500, 3200, 980, 0.50, 0.15),
                (2024, 3, 645, 327, 104, 74, 0.11, 55, 2550, 3180, 980, 0.51, 0.16),
                (2024, 4, 690, 353, 118, 84, 0.12, 70, 2600, 3170, 980, 0.51, 0.17),
                (2025, 1, 740, 385, 132, 96, 0.13, 85, 2650, 3150, 980, 0.52, 0.18),
            ],
            'RKLB': [
                (2024, 2, 105, 29, -20, -19, -0.05, -25, 520, 520, 510, 0.28, -0.19),
                (2024, 3, 116, 34, -14, -12, -0.03, -18, 510, 540, 511, 0.29, -0.12),
                (2024, 4, 127, 39, -8, -6, -0.02, -11, 500, 560, 512, 0.31, -0.06),
                (2025, 1, 141, 46, 1, 3, 0.01, 2, 495, 550, 513, 0.33, 0.01),
            ],
            'NOW': [
                (2024, 2, 2600, 2030, 720, 610, 2.95, 980, 8000, 2300, 205, 0.78, 0.28),
                (2024, 3, 2720, 2135, 770, 655, 3.16, 1030, 8150, 2280, 205.5, 0.78, 0.28),
                (2024, 4, 2860, 2245, 826, 705, 3.38, 1095, 8320, 2250, 206, 0.79, 0.29),
                (2025, 1, 3010, 2365, 888, 760, 3.65, 1160, 8500, 2210, 206.2, 0.79, 0.29),
            ],
            'EOSE': [
                (2024, 2, 45, 8, -30, -34, -0.12, -40, 120, 310, 250, 0.18, -0.67),
                (2024, 3, 52, 10, -26, -30, -0.10, -36, 112, 325, 266, 0.19, -0.58),
                (2024, 4, 58, 12, -21, -25, -0.09, -31, 104, 338, 281, 0.21, -0.43),
                (2025, 1, 67, 16, -12, -17, -0.06, -22, 95, 345, 300, 0.24, -0.25),
            ],
        }
        rows = presets.get(ticker.upper())
        if not rows:
            return []
        payload = []
        for year, quarter, revenue, gp, op_inc, net_inc, eps, fcf, cash, debt, shares, gm, om in rows:
            # Convert from millions → actual dollars (to match yfinance scale)
            _M = 1_000_000
            payload.append(
                {
                    'fiscal_year': year,
                    'fiscal_quarter': quarter,
                    'fiscal_period': f'{year}-Q{quarter}',
                    'revenue':              revenue  * _M,
                    'gross_profit':         gp       * _M,
                    'operating_income':     op_inc   * _M,
                    'net_income':           net_inc  * _M,
                    'eps': eps,                         # already per-share
                    'free_cash_flow':       fcf      * _M,
                    'cash_and_equivalents': cash     * _M,
                    'total_debt':           debt     * _M,
                    'shares_outstanding':   shares   * _M,
                    'gross_margin': gm,                 # ratio, no conversion
                    'operating_margin': om,             # ratio, no conversion
                    'reported_at': datetime(year, min(quarter * 3, 12), 15),
                }
            )
        return payload

    def fetch_events(self, ticker: str) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        templates = {
            'SOFI': [
                ('earnings', now + timedelta(days=18), 'Quarterly earnings', 'Upcoming earnings release.', 0.25, 78),
                ('analyst_upgrade', now - timedelta(days=9), 'Positive analyst revision', 'Estimate revision after stronger member growth.', 0.45, 65),
            ],
            'RKLB': [
                ('contract', now - timedelta(days=5), 'Defense launch contract', 'Multi-mission defense contract announced.', 0.70, 82),
                ('earnings', now + timedelta(days=25), 'Quarterly earnings', 'Upcoming earnings release.', 0.20, 72),
            ],
            'NOW': [
                ('earnings', now + timedelta(days=21), 'Quarterly earnings', 'Upcoming earnings release.', 0.20, 75),
                ('product_launch', now - timedelta(days=11), 'New AI workflow launch', 'Expanded enterprise AI offering.', 0.60, 68),
            ],
            'EOSE': [
                ('funding_risk', now - timedelta(days=13), 'Capital structure update', 'Market concerns around capital needs.', -0.45, 80),
                ('contract', now + timedelta(days=16), 'Grid storage deployment', 'Potential utility deployment catalyst.', 0.55, 70),
            ],
        }
        items = templates.get(ticker.upper(), [])
        return [
            {
                'event_type': t,
                'event_date': dt,
                'title': title,
                'summary': summary,
                'sentiment_score': sentiment,
                'importance_score': importance,
                'source': 'demo',
            }
            for t, dt, title, summary, sentiment, importance in items
        ]


class YFinanceMarketDataProvider:
    def __init__(self, history_days: int = 370) -> None:
        self.history_days = history_days
        self._yf = None

    @property
    def yf(self):
        if self._yf is None:
            import yfinance as yf  # lazy import so demo mode works without runtime dependency loading here
            self._yf = yf
        return self._yf

    def _ticker(self, ticker: str):
        return self.yf.Ticker(ticker)

    def fetch_asset_profile(self, ticker: str) -> dict[str, Any]:
        tk = self._ticker(ticker)
        info = tk.info or {}
        fast = getattr(tk, 'fast_info', {}) or {}
        exchange = info.get('exchange') or fast.get('exchange') or 'UNKNOWN'
        return {
            'ticker': ticker.upper(),
            'name': info.get('shortName') or info.get('longName') or ticker.upper(),
            'exchange': exchange,
            'sector': info.get('sector'),
            'industry': info.get('industry'),
            'country': info.get('country') or info.get('exchangeCountry') or 'US',
            'currency': info.get('currency') or 'USD',
            'market_cap': info.get('marketCap') or fast.get('market_cap'),
        }

    def fetch_price_history(self, ticker: str, days: int = 260) -> list[dict[str, Any]]:
        tk = self._ticker(ticker)
        df = tk.history(period='2y', interval='1d', auto_adjust=False, actions=False)
        if df.empty:
            return []
        df = df.tail(days).reset_index()
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            ts = row['Date']
            rows.append({
                'date': ts.date() if hasattr(ts, 'date') else ts,
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'close': float(row['Close']),
                'adjusted_close': float(row.get('Adj Close', row['Close'])),
                'volume': int(row['Volume'] or 0),
            })
        return rows

    def fetch_quarterly_fundamentals(self, ticker: str) -> list[dict[str, Any]]:
        tk = self._ticker(ticker)
        income = tk.quarterly_income_stmt
        balance = tk.quarterly_balance_sheet
        cashflow = tk.quarterly_cashflow
        shares = None
        try:
            shares = tk.get_shares_full(start='2020-01-01')
        except Exception:
            shares = None
        if income is None or getattr(income, 'empty', True):
            return []

        columns = list(income.columns)[:8]
        payload: list[dict[str, Any]] = []
        for col in columns:
            dt = col.to_pydatetime() if hasattr(col, 'to_pydatetime') else col
            year = dt.year
            quarter = ((dt.month - 1) // 3) + 1
            revenue = _get_statement_value(income, ['Total Revenue', 'Operating Revenue'], col)
            gross_profit = _get_statement_value(income, ['Gross Profit'], col)
            operating_income = _get_statement_value(income, ['Operating Income'], col)
            net_income = _get_statement_value(income, ['Net Income', 'Net Income Common Stockholders'], col)
            eps = None
            outstanding = _nearest_share_count(shares, dt) if shares is not None else None
            if net_income is not None and outstanding not in (None, 0):
                eps = float(net_income) / float(outstanding)
            free_cash_flow = _get_statement_value(cashflow, ['Free Cash Flow'], col)
            if free_cash_flow is None:
                op_cf = _get_statement_value(cashflow, ['Operating Cash Flow'], col)
                capex = _get_statement_value(cashflow, ['Capital Expenditure'], col)
                if op_cf is not None and capex is not None:
                    free_cash_flow = float(op_cf) + float(capex)
            cash = _get_statement_value(balance, ['Cash And Cash Equivalents', 'Cash Cash Equivalents And Short Term Investments'], col)
            debt = _get_statement_value(balance, ['Total Debt', 'Long Term Debt And Capital Lease Obligation', 'Long Term Debt'], col)
            gm = (float(gross_profit) / float(revenue)) if gross_profit is not None and revenue not in (None, 0) else None
            om = (float(operating_income) / float(revenue)) if operating_income is not None and revenue not in (None, 0) else None
            payload.append({
                'fiscal_year': year,
                'fiscal_quarter': quarter,
                'fiscal_period': f'{year}-Q{quarter}',
                'revenue': revenue,
                'gross_profit': gross_profit,
                'operating_income': operating_income,
                'net_income': net_income,
                'eps': eps,
                'free_cash_flow': free_cash_flow,
                'cash_and_equivalents': cash,
                'total_debt': debt,
                'shares_outstanding': outstanding,
                'gross_margin': gm,
                'operating_margin': om,
                'reported_at': dt,
            })
        return payload

    def fetch_events(self, ticker: str) -> list[dict[str, Any]]:
        tk = self._ticker(ticker)
        now = datetime.utcnow()
        items: list[dict[str, Any]] = []
        try:
            cal = tk.calendar
            if hasattr(cal, 'empty') and not cal.empty:
                if 'Earnings Date' in cal.index:
                    value = cal.loc['Earnings Date'].iloc[0]
                    if value is not None:
                        event_dt = value.to_pydatetime() if hasattr(value, 'to_pydatetime') else value
                        items.append({
                            'event_type': 'earnings',
                            'event_date': event_dt,
                            'title': 'Upcoming earnings',
                            'summary': 'Imported from yfinance calendar.',
                            'sentiment_score': 0.1,
                            'importance_score': 75,
                            'source': 'yfinance',
                        })
        except Exception:
            pass
        try:
            recs = tk.upgrades_downgrades
            if recs is not None and not recs.empty:
                recs = recs.tail(5).reset_index()
                for _, row in recs.iterrows():
                    action = str(row.get('Action', '')).lower()
                    event_type = 'analyst_upgrade' if 'up' in action else 'analyst_downgrade'
                    grade = row.get('ToGrade') or row.get('FromGrade') or 'rating change'
                    ts = row['GradeDate'] if 'GradeDate' in row else row.iloc[0]
                    event_dt = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
                    if event_dt >= now - timedelta(days=90):
                        items.append({
                            'event_type': event_type,
                            'event_date': event_dt,
                            'title': f'Analyst {event_type.split("_")[1]}',
                            'summary': f'Rating action: {grade}',
                            'sentiment_score': 0.4 if event_type == 'analyst_upgrade' else -0.4,
                            'importance_score': 60,
                            'source': 'yfinance',
                        })
        except Exception:
            pass
        return items


class HTTPMarketDataProvider:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if self.api_key:
            params.setdefault('apikey', self.api_key)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f'{self.base_url}/{path.lstrip("/")}', params=params)
            response.raise_for_status()
            return response.json()


def _get_statement_value(frame, names: list[str], column) -> float | None:
    if frame is None or getattr(frame, 'empty', True):
        return None
    for name in names:
        if name in frame.index:
            value = frame.loc[name, column]
            if value is None:
                return None
            try:
                if str(value).lower() == 'nan':
                    return None
            except Exception:
                pass
            return float(value)
    return None


def _nearest_share_count(series, dt: datetime) -> float | None:
    if series is None or getattr(series, 'empty', True):
        return None
    try:
        subset = series[series.index <= dt]
        if subset.empty:
            subset = series
        value = subset.iloc[-1]
        return float(value)
    except Exception:
        return None


def get_market_data_provider(settings: Settings) -> MarketDataProvider:
    provider_name = settings.data_provider.strip().lower()
    if provider_name == 'yfinance':
        return YFinanceMarketDataProvider(history_days=settings.yfinance_history_days)
    if provider_name == 'hybrid':
        return HybridMarketDataProvider(history_days=settings.yfinance_history_days)
    return DemoMarketDataProvider()


# ─────────────────────────────────────────────────────────────────────────────
# Hybrid provider: SEC XBRL for fundamentals, yfinance for everything else
# ─────────────────────────────────────────────────────────────────────────────

class HybridMarketDataProvider:
    """
    Best-of-both-worlds provider.

      Fundamentals → SEC EDGAR XBRL (point-in-time, free, no API key)
      Prices       → yfinance (clean historical OHLCV, free)
      Events       → yfinance (earnings calendar, analyst ratings)
      Profile      → yfinance (name, sector, market cap)

    Falls back to yfinance fundamentals if XBRL is unavailable for a ticker
    (e.g. non-US companies, very small filers pre-2009).

    Use this provider for backtesting that requires historical accuracy.
    Set DATA_PROVIDER=hybrid in .env.
    """

    def __init__(self, history_days: int = 370) -> None:
        self._yf_provider = YFinanceMarketDataProvider(history_days=history_days)

    def fetch_asset_profile(self, ticker: str) -> dict[str, Any]:
        return self._yf_provider.fetch_asset_profile(ticker)

    def fetch_price_history(self, ticker: str, days: int = 260) -> list[dict[str, Any]]:
        return self._yf_provider.fetch_price_history(ticker, days=days)

    def fetch_quarterly_fundamentals(self, ticker: str) -> list[dict[str, Any]]:
        import logging as _log
        _logger = _log.getLogger(__name__)

        # Try XBRL first
        try:
            from app.services.ingestion.xbrl import fetch_xbrl_fundamentals, xbrl_available
            if xbrl_available(ticker):
                rows = fetch_xbrl_fundamentals(ticker)
                if rows:
                    _logger.debug("XBRL fundamentals for %s: %d quarters", ticker, len(rows))
                    return rows
                _logger.debug("XBRL returned empty for %s — falling back to yfinance", ticker)
        except Exception as exc:
            _logger.warning("XBRL fetch error for %s: %s — falling back to yfinance", ticker, exc)

        # Fallback to yfinance
        return self._yf_provider.fetch_quarterly_fundamentals(ticker)

    def fetch_events(self, ticker: str) -> list[dict[str, Any]]:
        return self._yf_provider.fetch_events(ticker)
