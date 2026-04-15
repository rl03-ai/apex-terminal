"""Insider trades engine — SEC EDGAR Form 4.

Data source: SEC EDGAR public APIs (free, no API key required).
  - CIK lookup:     https://www.sec.gov/files/company_tickers.json
  - Submissions:    https://data.sec.gov/submissions/CIK{padded}.json
  - Filing index:   https://www.sec.gov/Archives/edgar/data/{cik}/{accession}-index.htm

Rate limits (SEC fair-access policy):
  - Max 10 requests/second
  - Required User-Agent: "company name email@domain.com"

Form 4 transaction codes:
  P  = Open market purchase          → strongly bullish
  S  = Open market sale              → bearish (but could be planned)
  A  = Grant / award                 → neutral (compensation)
  D  = Disposition (non-open-market) → neutral
  F  = Tax withholding sale          → neutral (forced, ignore)
  M  = Exercise of option            → depends on subsequent action
  G  = Gift                          → neutral

Sentiment scoring:
  Net $ bought / total traded → maps to -1..+1
  Filtered: only P and S transactions count (open market)
  Filtered: only trades in the last 90 days by default
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_SEC_BASE = "https://data.sec.gov"
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_USER_AGENT = "apex-terminal research@apex-terminal.io"
_REQUEST_DELAY = 0.12   # ~8 requests/second, safely under 10


# ─────────────────────────────────────────────────────────────────────────────
# CIK lookup (cached in-process)
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_cik_map() -> dict[str, str]:
    """
    Fetch and cache the full ticker→CIK mapping from SEC.
    Returns dict: ticker.upper() → zero-padded 10-digit CIK string.
    """
    try:
        req = urllib.request.Request(
            _SEC_TICKERS_URL,
            headers={'User-Agent': _USER_AGENT, 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read().decode('utf-8'))

        mapping: dict[str, str] = {}
        for entry in raw.values():
            ticker = str(entry.get('ticker', '')).upper().strip()
            cik = str(entry.get('cik_str', '')).zfill(10)
            if ticker:
                mapping[ticker] = cik
        logger.info("SEC CIK map loaded: %d tickers", len(mapping))
        return mapping
    except Exception as exc:
        logger.warning("CIK map fetch failed: %s", exc)
        return {}


def get_cik(ticker: str) -> str | None:
    """Return zero-padded 10-digit CIK for ticker, or None if not found."""
    mapping = _get_cik_map()
    return mapping.get(ticker.upper())


# ─────────────────────────────────────────────────────────────────────────────
# Filing fetcher
# ─────────────────────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict | None:
    try:
        time.sleep(_REQUEST_DELAY)
        req = urllib.request.Request(
            url,
            headers={'User-Agent': _USER_AGENT, 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as exc:
        logger.debug("SEC request failed %s: %s", url, exc)
        return None


def _parse_form4_from_submissions(submissions: dict, days: int = 90) -> list[dict[str, Any]]:
    """
    Parse Form 4 filings from the SEC submissions JSON.
    Returns a list of raw filing metadata dicts (not yet full transaction detail).
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    filings = submissions.get('filings', {}).get('recent', {})

    forms = filings.get('form', [])
    dates = filings.get('filingDate', [])
    accessions = filings.get('accessionNumber', [])
    descriptions = filings.get('primaryDocument', [])

    result: list[dict] = []
    for form, date_str, accession, doc in zip(forms, dates, accessions, descriptions):
        if form not in ('4', '4/A'):
            continue
        try:
            filing_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if filing_dt < cutoff:
            # Filings are in reverse-chronological order; stop when too old
            break
        result.append({
            'filing_date': filing_dt,
            'accession': accession.replace('-', ''),
            'form': form,
            'doc': doc,
        })
    return result


def _fetch_form4_xml(cik_raw: str, accession_clean: str) -> str | None:
    """Fetch the raw XML of a Form 4 filing."""
    # Accession numbers are stored without dashes in the path
    # but the directory uses the format XXXXXXXXXX-XX-XXXXXX
    acc_dashed = f"{accession_clean[:10]}-{accession_clean[10:12]}-{accession_clean[12:]}"
    url = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik_raw)}/{accession_clean}/{acc_dashed}.txt"
    )
    try:
        time.sleep(_REQUEST_DELAY)
        req = urllib.request.Request(
            url,
            headers={'User-Agent': _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode('utf-8', errors='ignore')
    except Exception:
        return None


def _parse_form4_transactions(xml_text: str, filing_date: datetime) -> list[dict[str, Any]]:
    """
    Extract non-derivative transactions from Form 4 XML.
    Returns list of transaction dicts.
    """
    if not xml_text:
        return []

    transactions: list[dict[str, Any]] = []

    try:
        import xml.etree.ElementTree as ET
        # Form 4 XML may have an XML declaration + SGML wrapper; extract the XML part
        xml_start = xml_text.find('<?xml')
        if xml_start == -1:
            xml_start = xml_text.find('<XML>')
            if xml_start != -1:
                xml_start = xml_text.find('<', xml_start + 5)
        if xml_start == -1:
            return []

        xml_clean = xml_text[xml_start:]
        # Strip anything after the closing tag
        root = ET.fromstring(xml_clean)

        # Reporting owner name
        owner_name = ''
        owner_el = root.find('.//reportingOwner/reportingOwnerId/rptOwnerName')
        if owner_el is not None and owner_el.text:
            owner_name = owner_el.text.strip()

        # Non-derivative transactions (open market buys/sells)
        for tx in root.findall('.//nonDerivativeTransaction'):
            try:
                code_el = tx.find('.//transactionCoding/transactionCode')
                code = code_el.text.strip() if code_el is not None and code_el.text else ''
                if code not in ('P', 'S'):
                    continue  # Only open-market transactions

                shares_el = tx.find('.//transactionAmounts/transactionShares/value')
                price_el = tx.find('.//transactionAmounts/transactionPricePerShare/value')
                date_el = tx.find('.//transactionDate/value')

                shares = float(shares_el.text) if shares_el is not None and shares_el.text else 0.0
                price = float(price_el.text) if price_el is not None and price_el.text else 0.0
                tx_value = shares * price

                tx_date = filing_date
                if date_el is not None and date_el.text:
                    try:
                        tx_date = datetime.strptime(date_el.text.strip(), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                transactions.append({
                    'owner': owner_name,
                    'code': code,           # P = buy, S = sell
                    'shares': shares,
                    'price': price,
                    'value': tx_value,
                    'date': tx_date,
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Form 4 XML parse error: %s", exc)

    return transactions


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

def _transactions_to_sentiment(transactions: list[dict]) -> tuple[float, float]:
    """
    Returns (sentiment_score: -1..+1, importance_score: 0..100).
    sentiment > 0 = net buying, < 0 = net selling.
    """
    total_bought = sum(t['value'] for t in transactions if t['code'] == 'P')
    total_sold = sum(t['value'] for t in transactions if t['code'] == 'S')
    total_traded = total_bought + total_sold

    if total_traded == 0:
        return 0.0, 30.0

    net_ratio = (total_bought - total_sold) / total_traded  # -1..+1

    # Importance scales with $ value
    if total_traded >= 5_000_000:
        importance = 90.0
    elif total_traded >= 1_000_000:
        importance = 78.0
    elif total_traded >= 500_000:
        importance = 68.0
    elif total_traded >= 100_000:
        importance = 58.0
    else:
        importance = 45.0

    return round(net_ratio, 3), importance


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_insider_events(ticker: str, days: int = 90, max_filings: int = 10) -> list[dict[str, Any]]:
    """
    Fetch insider trade events from SEC EDGAR Form 4 filings.

    Returns a list of AssetEvent-compatible dicts.
    Limits full XML parsing to max_filings most recent Form 4s.
    """
    cik = get_cik(ticker)
    if cik is None:
        logger.debug("No CIK found for %s — skipping insider fetch", ticker)
        return []

    submissions = _get_json(f"{_SEC_BASE}/submissions/CIK{cik}.json")
    if not submissions:
        return []

    filings = _parse_form4_from_submissions(submissions, days=days)
    if not filings:
        return []

    cik_raw = str(int(cik))  # strip leading zeros for URL path
    all_transactions: list[dict] = []

    for filing in filings[:max_filings]:
        xml_text = _fetch_form4_xml(cik_raw, filing['accession'])
        txs = _parse_form4_transactions(xml_text or '', filing['filing_date'])
        all_transactions.extend(txs)

    if not all_transactions:
        return []

    # Group by type and create events
    events: list[dict[str, Any]] = []
    buys = [t for t in all_transactions if t['code'] == 'P']
    sells = [t for t in all_transactions if t['code'] == 'S']
    sentiment, importance = _transactions_to_sentiment(all_transactions)

    # One summary event per direction
    if buys:
        total_val = sum(t['value'] for t in buys)
        most_recent = max(buys, key=lambda t: t['date'])
        events.append({
            'event_type': 'insider_buy',
            'event_date': most_recent['date'],
            'title': f"Insider purchase: {len(buys)} transaction(s), ${total_val:,.0f}",
            'summary': (
                f"{most_recent['owner']} and others bought ${total_val:,.0f} total "
                f"across {len(buys)} Form 4 filing(s) in the last {days} days."
            ),
            'sentiment_score': min(0.85, 0.4 + 0.05 * len(buys)),
            'importance_score': importance,
            'source': 'sec_edgar_form4',
            'external_id': f"insider_buy_{ticker}_{str(most_recent['date'])[:10]}",
        })

    if sells:
        total_val = sum(t['value'] for t in sells)
        most_recent = max(sells, key=lambda t: t['date'])
        events.append({
            'event_type': 'insider_sell',
            'event_date': most_recent['date'],
            'title': f"Insider sale: {len(sells)} transaction(s), ${total_val:,.0f}",
            'summary': (
                f"{most_recent['owner']} and others sold ${total_val:,.0f} total "
                f"across {len(sells)} Form 4 filing(s) in the last {days} days."
            ),
            'sentiment_score': max(-0.70, -0.3 - 0.05 * len(sells)),
            'importance_score': importance * 0.7,  # sells weighted lower
            'source': 'sec_edgar_form4',
            'external_id': f"insider_sell_{ticker}_{str(most_recent['date'])[:10]}",
        })

    logger.debug("Insider events for %s: %d (%d buys, %d sells)", ticker, len(events), len(buys), len(sells))
    return events


def compute_insider_catalyst_score(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate insider events into a single catalyst score (0-100)."""
    insider_events = [e for e in events if e.get('event_type') in ('insider_buy', 'insider_sell')]
    if not insider_events:
        return {'score': 50.0, 'signal': 'neutral', 'description': 'No recent insider activity.'}

    buy_events = [e for e in insider_events if e['event_type'] == 'insider_buy']
    sell_events = [e for e in insider_events if e['event_type'] == 'insider_sell']

    buy_importance = max((e.get('importance_score', 0) for e in buy_events), default=0)
    sell_importance = max((e.get('importance_score', 0) for e in sell_events), default=0)

    if buy_importance > sell_importance:
        signal = 'buying'
        score = 50.0 + buy_importance * 0.4
    elif sell_importance > buy_importance:
        signal = 'selling'
        score = 50.0 - sell_importance * 0.3
    else:
        signal = 'mixed'
        score = 50.0

    return {
        'score': round(max(0.0, min(100.0, score)), 2),
        'signal': signal,
        'buy_events': len(buy_events),
        'sell_events': len(sell_events),
        'description': insider_events[0].get('title', ''),
    }
