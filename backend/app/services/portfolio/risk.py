"""Portfolio risk analysis.

Computes three categories of risk signals:

1. Concentration risk (ticker + sector)
2. Per-position risk level (green/yellow/red)
3. Stop-loss suggestions (percentage + ATR + trailing)
"""

from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ─── Thresholds ──────────────────────────────────────────────────────────────
CONCENTRATION_TICKER_WARN     = 25.0
CONCENTRATION_TICKER_CRIT     = 40.0
CONCENTRATION_SECTOR_WARN     = 40.0
CONCENTRATION_SECTOR_CRIT     = 60.0
DIVERSIFICATION_MIN           = 5
DIVERSIFICATION_IDEAL_LOW     = 8
DIVERSIFICATION_IDEAL_HIGH    = 15
DIVERSIFICATION_MAX           = 25

DRAWDOWN_WARN                 = 5.0
DRAWDOWN_CRIT                 = 15.0
STOP_LOSS_PCT                 = 0.15
STOP_LOSS_TRAIL_PCT           = 0.20
STOP_LOSS_ATR_MULT            = 2.5


def compute_atr(prices: Sequence, period: int = 14) -> float | None:
    if not prices or len(prices) < period + 1:
        return None
    ordered = sorted(prices, key=lambda p: p.date)[-period-1:]
    trs: list[float] = []
    for i in range(1, len(ordered)):
        p = ordered[i]
        prev = ordered[i-1]
        tr = max(
            p.high - p.low,
            abs(p.high - prev.close),
            abs(p.low - prev.close),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def suggest_stop_loss(
    avg_cost: float,
    current_price: float,
    prices: Sequence,
    entry_date=None,
) -> dict:
    stop_pct = avg_cost * (1 - STOP_LOSS_PCT)

    atr = compute_atr(prices)
    stop_atr = (current_price - atr * STOP_LOSS_ATR_MULT) if atr else None

    stop_trail = None
    high_since_entry = None
    if prices and entry_date:
        relevant = [p for p in prices if p.date >= entry_date]
        if relevant:
            high_since_entry = max(p.high for p in relevant)
            stop_trail = high_since_entry * (1 - STOP_LOSS_TRAIL_PCT)

    candidates = [('percentage', stop_pct)]
    if stop_atr is not None and stop_atr > 0:
        candidates.append(('atr', stop_atr))
    if stop_trail is not None and stop_trail > avg_cost * 0.85:
        candidates.append(('trailing', stop_trail))

    method, stop_price = max(candidates, key=lambda x: x[1])
    distance_pct = ((current_price - stop_price) / current_price * 100) if current_price > 0 else 0

    return {
        'stop_price':       round(stop_price, 2),
        'method':           method,
        'distance_pct':     round(distance_pct, 2),
        'percentage_stop':  round(stop_pct, 2),
        'atr_stop':         round(stop_atr, 2) if stop_atr else None,
        'trailing_stop':    round(stop_trail, 2) if stop_trail else None,
        'atr_14':           round(atr, 3) if atr else None,
        'high_since_entry': round(high_since_entry, 2) if high_since_entry else None,
    }


def classify_position_risk(pnl_pct: float, current_price: float, stop_price: float) -> dict:
    distance_to_stop_pct = (
        (current_price - stop_price) / current_price * 100
        if current_price > 0 else 0
    )

    if pnl_pct < -DRAWDOWN_CRIT or distance_to_stop_pct < 0:
        level = 'red'
        reason = 'Below suggested stop-loss' if distance_to_stop_pct < 0 else f'Drawdown {pnl_pct:.1f}%'
    elif pnl_pct < -DRAWDOWN_WARN or distance_to_stop_pct < 3:
        level = 'yellow'
        reason = f'Close to stop ({distance_to_stop_pct:.1f}%)' if distance_to_stop_pct < 3 else f'Drawdown {pnl_pct:.1f}%'
    else:
        level = 'green'
        reason = 'Healthy'

    return {'level': level, 'reason': reason, 'distance_to_stop_pct': round(distance_to_stop_pct, 2)}


def compute_portfolio_risk(db: Session, portfolio_id: str) -> dict:
    from app.models.asset import Asset, AssetPriceDaily
    from app.models.portfolio import Position

    positions = (
        db.query(Position)
        .filter(Position.portfolio_id == portfolio_id, Position.status == 'open')
        .all()
    )

    if not positions:
        return {
            'total_value': 0, 'total_invested': 0, 'position_count': 0,
            'alerts': [],
            'concentration': {'top_ticker': None, 'top_sector': None, 'sector_breakdown': []},
            'position_risks': [],
            'diversification': {'status': 'empty', 'count': 0, 'target': '8-15'},
            'summary': {'red_count': 0, 'yellow_count': 0, 'green_count': 0},
        }

    per_position: list[dict] = []
    total_value = 0.0
    total_invested = 0.0
    sector_exposure: dict[str, float] = {}

    for pos in positions:
        asset = db.query(Asset).filter(Asset.id == pos.asset_id).first()
        if not asset:
            continue
        prices = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == pos.asset_id)
            .order_by(AssetPriceDaily.date.asc())
            .all()
        )
        if not prices:
            continue
        current_price = prices[-1].close
        current_value = pos.quantity * current_price
        pnl = current_value - pos.invested_amount
        pnl_pct = (pnl / pos.invested_amount * 100) if pos.invested_amount else 0

        stop_info = suggest_stop_loss(pos.avg_cost, current_price, prices, pos.first_buy_date)
        risk = classify_position_risk(pnl_pct, current_price, stop_info['stop_price'])

        total_value += current_value
        total_invested += pos.invested_amount
        sector = asset.sector or 'Unknown'
        sector_exposure[sector] = sector_exposure.get(sector, 0) + current_value

        per_position.append({
            'position_id':   pos.id,
            'ticker':        asset.ticker,
            'name':          asset.name,
            'sector':        sector,
            'current_value': round(current_value, 2),
            'current_price': round(current_price, 2),
            'invested':      round(pos.invested_amount, 2),
            'pnl':           round(pnl, 2),
            'pnl_pct':       round(pnl_pct, 2),
            'stop_price':    stop_info['stop_price'],
            'stop_method':   stop_info['method'],
            'distance_to_stop_pct': risk['distance_to_stop_pct'],
            'risk_level':    risk['level'],
            'risk_reason':   risk['reason'],
        })

    for p in per_position:
        p['weight_pct'] = round(p['current_value'] / total_value * 100, 2) if total_value else 0

    top_ticker = max(per_position, key=lambda p: p['weight_pct']) if per_position else None
    sorted_sectors = sorted(sector_exposure.items(), key=lambda x: -x[1])
    top_sector_name, top_sector_value = (sorted_sectors[0] if sorted_sectors else (None, 0))
    top_sector_pct = (top_sector_value / total_value * 100) if total_value else 0

    alerts: list[dict] = []

    if top_ticker and top_ticker['weight_pct'] >= CONCENTRATION_TICKER_CRIT:
        alerts.append({
            'severity': 'critical', 'category': 'concentration',
            'message': f"{top_ticker['ticker']} é {top_ticker['weight_pct']:.0f}% da carteira (crítico > 40%)",
        })
    elif top_ticker and top_ticker['weight_pct'] >= CONCENTRATION_TICKER_WARN:
        alerts.append({
            'severity': 'warning', 'category': 'concentration',
            'message': f"{top_ticker['ticker']} é {top_ticker['weight_pct']:.0f}% da carteira (alerta > 25%)",
        })

    if top_sector_pct >= CONCENTRATION_SECTOR_CRIT:
        alerts.append({
            'severity': 'critical', 'category': 'concentration',
            'message': f"Setor {top_sector_name} é {top_sector_pct:.0f}% da carteira (crítico > 60%)",
        })
    elif top_sector_pct >= CONCENTRATION_SECTOR_WARN:
        alerts.append({
            'severity': 'warning', 'category': 'concentration',
            'message': f"Setor {top_sector_name} é {top_sector_pct:.0f}% da carteira (alerta > 40%)",
        })

    n = len(per_position)
    if n < DIVERSIFICATION_MIN:
        alerts.append({
            'severity': 'warning', 'category': 'diversification',
            'message': f"Apenas {n} posição(ões) — considera diversificar para 8-15",
        })
    elif n > DIVERSIFICATION_MAX:
        alerts.append({
            'severity': 'warning', 'category': 'diversification',
            'message': f"{n} posições — carteira muito pulverizada",
        })

    red_positions = [p for p in per_position if p['risk_level'] == 'red']
    yellow_positions = [p for p in per_position if p['risk_level'] == 'yellow']

    for p in red_positions:
        alerts.append({
            'severity': 'critical', 'category': 'position',
            'message': f"{p['ticker']}: {p['risk_reason']}",
        })
    if yellow_positions:
        tickers = ', '.join(p['ticker'] for p in yellow_positions[:3])
        more = f' e mais {len(yellow_positions)-3}' if len(yellow_positions) > 3 else ''
        alerts.append({
            'severity': 'warning', 'category': 'position',
            'message': f"Posições sob pressão: {tickers}{more}",
        })

    if n == 0: div_status = 'empty'
    elif n < DIVERSIFICATION_MIN: div_status = 'under'
    elif n < DIVERSIFICATION_IDEAL_LOW: div_status = 'low'
    elif n <= DIVERSIFICATION_IDEAL_HIGH: div_status = 'ideal'
    elif n <= DIVERSIFICATION_MAX: div_status = 'high'
    else: div_status = 'over'

    return {
        'total_value': round(total_value, 2),
        'total_invested': round(total_invested, 2),
        'position_count': n,
        'alerts': alerts,
        'concentration': {
            'top_ticker': {
                'ticker': top_ticker['ticker'],
                'weight_pct': top_ticker['weight_pct'],
            } if top_ticker else None,
            'top_sector': {
                'name': top_sector_name,
                'weight_pct': round(top_sector_pct, 2),
            } if top_sector_name else None,
            'sector_breakdown': [
                {'sector': name, 'value': round(v, 2), 'weight_pct': round(v/total_value*100, 2)}
                for name, v in sorted_sectors
            ] if total_value else [],
        },
        'position_risks': per_position,
        'diversification': {
            'status': div_status,
            'count': n,
            'target': f'{DIVERSIFICATION_IDEAL_LOW}-{DIVERSIFICATION_IDEAL_HIGH}',
        },
        'summary': {
            'red_count': len(red_positions),
            'yellow_count': len(yellow_positions),
            'green_count': n - len(red_positions) - len(yellow_positions),
        },
    }
