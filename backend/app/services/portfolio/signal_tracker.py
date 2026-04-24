"""Signal Tracker.

For each open position, monitors whether the original entry thesis remains valid.
Combines two dimensions:

1. Signal Health — is the original signal still active?
   - Technical regime (is it still UPTREND?)
   - Structural score trajectory (improving/stable/declining?)
   - Early signal status (still active or exited?)
   - Insider buying recency (still fresh or stale?)
   - Distance to stop (growing = good, shrinking = bad)

2. Price Momentum — how is the position actually performing?
   - Return since entry vs expected (beating/in-line/lagging)
   - Short-term momentum (1w, 2w, 1m returns since entry)
   - Momentum velocity (accelerating/stable/decelerating/reversing)

Final Verdict:
  MANTER       — signal intact, momentum positive or neutral
  MONITORIZAR  — mixed signals, watch closely
  REVER        — multiple negative signals, consider exit

Each factor produces a +1 (positive), 0 (neutral), or -1 (negative) contribution.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def compute_signal_tracker(
    db: Session,
    portfolio_id: str,
    position_id: str,
) -> dict:
    from app.models.asset import (
        Asset, AssetPriceDaily, AssetScoreDaily,
        AssetEvent, AssetTechnicalSnapshot,
    )
    from app.models.portfolio import Position
    from app.models.early_signal import EarlySignal
    from app.models.insider_alert import InsiderAlertCache

    pos = db.query(Position).filter(
        Position.id == position_id,
        Position.portfolio_id == portfolio_id,
    ).first()
    if not pos:
        return {'error': 'Position not found'}

    asset = db.query(Asset).filter(Asset.id == pos.asset_id).first()
    if not asset:
        return {'error': 'Asset not found'}

    today = date.today()
    entry_date = pos.first_buy_date

    # ── Price data ────────────────────────────────────────────────────────────
    prices = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == pos.asset_id)
        .order_by(AssetPriceDaily.date.asc())
        .all()
    )
    if not prices:
        return {'error': 'No price data'}

    current_price = prices[-1].close
    prices_since_entry = [p for p in prices if p.date >= entry_date]
    entry_price_actual = prices_since_entry[0].close if prices_since_entry else pos.avg_cost

    # ── Score data ────────────────────────────────────────────────────────────
    scores = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == pos.asset_id)
        .order_by(desc(AssetScoreDaily.date))
        .limit(30)
        .all()
    )
    score_now = scores[0] if scores else None
    score_7d = next((s for s in scores if s.date <= today - timedelta(days=7)), None)
    score_14d = next((s for s in scores if s.date <= today - timedelta(days=14)), None)
    score_entry = next((s for s in scores if s.date <= entry_date + timedelta(days=3)), None)

    # ── Factors ───────────────────────────────────────────────────────────────
    factors: list[dict] = []
    score_tally = 0  # sum of +1/0/-1

    # FACTOR 1: Technical regime
    regime_now = (score_now.score_regime or 'UNKNOWN') if score_now else 'UNKNOWN'
    regime_entry = (score_entry.score_regime or 'UNKNOWN') if score_entry else 'UNKNOWN'

    bullish_regimes = {'STRONG_UPTREND', 'UPTREND'}
    bearish_regimes = {'DOWNTREND', 'TOPPING'}

    if regime_now in bullish_regimes:
        r_val = 1
        r_label = f'Regime {regime_now.replace("_", " ")} — momentum técnico intacto'
    elif regime_now in bearish_regimes:
        r_val = -1
        r_label = f'Regime virou para {regime_now.replace("_", " ")} — sinal original comprometido'
    else:
        r_val = 0
        r_label = f'Regime {regime_now.replace("_", " ")} — neutro, aguardar confirmação'

    factors.append({'name': 'Regime técnico', 'value': r_val, 'detail': r_label, 'critical': r_val == -1})
    score_tally += r_val

    # FACTOR 2: Score trajectory since entry
    if score_now and score_entry:
        score_delta = score_now.total_score - score_entry.total_score
        if score_delta >= 3:
            s_val = 1
            s_label = f'Score subiu {score_delta:+.1f} desde entrada ({score_entry.total_score:.0f} → {score_now.total_score:.0f})'
        elif score_delta <= -5:
            s_val = -1
            s_label = f'Score desceu {score_delta:+.1f} desde entrada — qualidade a deteriorar'
        else:
            s_val = 0
            s_label = f'Score estável ({score_now.total_score:.0f} vs {score_entry.total_score:.0f} na entrada)'
        factors.append({'name': 'Score estrutural', 'value': s_val, 'detail': s_label, 'critical': s_val == -1 and score_delta <= -8})
        score_tally += s_val

    # FACTOR 3: Score 7d momentum
    if score_now and score_7d:
        delta_7d = score_now.total_score - score_7d.total_score
        if delta_7d >= 2:
            m_val = 1
            m_label = f'Score a subir esta semana ({delta_7d:+.1f} em 7 dias)'
        elif delta_7d <= -3:
            m_val = -1
            m_label = f'Score a cair esta semana ({delta_7d:+.1f} em 7 dias)'
        else:
            m_val = 0
            m_label = f'Score estável na última semana ({delta_7d:+.1f})'
        factors.append({'name': 'Momentum do score (7d)', 'value': m_val, 'detail': m_label, 'critical': False})
        score_tally += m_val

    # FACTOR 4: Early signal still active?
    early = db.query(EarlySignal).filter(
        EarlySignal.asset_id == pos.asset_id,
        EarlySignal.is_active == True,
    ).first()
    early_any = db.query(EarlySignal).filter(
        EarlySignal.asset_id == pos.asset_id,
    ).order_by(desc(EarlySignal.first_detected_date)).first()

    if early:
        pct = early.pct_move_since or 0
        e_val = 1
        e_label = f'Early signal ainda activo (preço subiu {pct:+.1f}% desde detecção)'
    elif early_any and early_any.exit_reason:
        if 'moved' in (early_any.exit_reason or '').lower():
            e_val = 1  # exited because price moved — that's good!
            e_label = f'Early signal expirou por movimento de preço ✓ (tese confirmada)'
        else:
            e_val = 0
            e_label = f'Early signal expirou: {early_any.exit_reason}'
    else:
        e_val = 0
        e_label = 'Sem early signal registado para esta posição'
    factors.append({'name': 'Early Signal', 'value': e_val, 'detail': e_label, 'critical': False})
    score_tally += e_val

    # FACTOR 5: Insider activity recency
    insider = db.query(InsiderAlertCache).filter(
        InsiderAlertCache.asset_id == pos.asset_id
    ).first()
    if insider:
        days_ago = (today - insider.most_recent_date.date()).days if insider.most_recent_date else 999
        if days_ago <= 30:
            i_val = 1
            i_label = f'Insider buying recente ({days_ago}d atrás) — {insider.signal_type}'
        elif days_ago <= 60:
            i_val = 0
            i_label = f'Insider buying a envelhecer ({days_ago}d atrás)'
        else:
            i_val = -1
            i_label = f'Insider buying antigo ({days_ago}d atrás) — sinal expirado'
        factors.append({'name': 'Insider activity', 'value': i_val, 'detail': i_label, 'critical': False})
        score_tally += i_val

    # FACTOR 6: Distance to stop (from risk service)
    try:
        from app.services.portfolio.risk import suggest_stop_loss
        stop_info = suggest_stop_loss(pos.avg_cost, current_price, prices, entry_date)
        dist = stop_info['distance_pct']
        if dist >= 12:
            st_val = 1
            st_label = f'Stop a {dist:.1f}% — posição com folga confortável'
        elif dist >= 5:
            st_val = 0
            st_label = f'Stop a {dist:.1f}% — monitorizar proximidade'
        else:
            st_val = -1
            st_label = f'Stop a apenas {dist:.1f}% — risco elevado de acionar stop'
        factors.append({'name': 'Distância ao stop', 'value': st_val, 'detail': st_label, 'critical': dist < 3})
        score_tally += st_val
    except Exception:
        pass

    # ── Price momentum ─────────────────────────────────────────────────────────
    returns: dict[str, float | None] = {}
    if prices_since_entry:
        # Return since entry
        ret_entry = ((current_price - entry_price_actual) / entry_price_actual * 100) if entry_price_actual else 0
        returns['since_entry'] = round(ret_entry, 2)

        # 1 week
        price_1w = next((p.close for p in reversed(prices_since_entry) if p.date <= today - timedelta(days=7)), None)
        if price_1w:
            returns['1w'] = round((current_price - price_1w) / price_1w * 100, 2)

        # 2 weeks
        price_2w = next((p.close for p in reversed(prices_since_entry) if p.date <= today - timedelta(days=14)), None)
        if price_2w:
            returns['2w'] = round((current_price - price_2w) / price_2w * 100, 2)

        # 1 month
        price_1m = next((p.close for p in reversed(prices_since_entry) if p.date <= today - timedelta(days=30)), None)
        if price_1m:
            returns['1m'] = round((current_price - price_1m) / price_1m * 100, 2)

    # Momentum velocity
    r_1w = returns.get('1w')
    r_2w = returns.get('2w')
    if r_1w is not None and r_2w is not None:
        r_prev_week = r_2w - r_1w  # approximate last week before current week
        if r_1w > 2 and r_1w > r_prev_week:
            velocity = 'accelerating'
            velocity_label = '📈 A acelerar'
        elif r_1w > 0:
            velocity = 'stable_up'
            velocity_label = '↗ A subir (estável)'
        elif r_1w > -2:
            velocity = 'lateral'
            velocity_label = '→ Lateral'
        elif r_1w < -2 and r_1w < r_prev_week:
            velocity = 'decelerating'
            velocity_label = '📉 A desacelerar'
        else:
            velocity = 'reversing'
            velocity_label = '⬇ A reverter'
    else:
        velocity = 'unknown'
        velocity_label = '— Dados insuficientes'

    # ── Verdict ────────────────────────────────────────────────────────────────
    critical_count = sum(1 for f in factors if f.get('critical'))
    positive_count = sum(1 for f in factors if f['value'] > 0)
    negative_count = sum(1 for f in factors if f['value'] < 0)

    if critical_count >= 2 or negative_count >= 3:
        verdict = 'REVER'
        verdict_color = 'red'
        verdict_detail = 'Múltiplos factores negativos — avalia saída ou stop'
    elif critical_count == 1 or (negative_count >= 2 and positive_count < negative_count):
        verdict = 'MONITORIZAR'
        verdict_color = 'yellow'
        verdict_detail = 'Sinais mistos — acompanha de perto nos próximos dias'
    else:
        verdict = 'MANTER'
        verdict_color = 'green'
        verdict_detail = 'Tese original mantém-se — continua a acompanhar'

    # Upcoming events
    upcoming_events: list[dict] = []
    cutoff_future = today + timedelta(days=45)
    events = (
        db.query(AssetEvent)
        .filter(
            AssetEvent.asset_id == pos.asset_id,
            AssetEvent.event_date >= today,
            AssetEvent.event_date <= cutoff_future,
        )
        .order_by(AssetEvent.event_date.asc())
        .limit(3)
        .all()
    )
    for e in events:
        upcoming_events.append({
            'date': e.event_date.strftime('%Y-%m-%d') if e.event_date else None,
            'type': e.event_type,
            'title': e.title,
        })

    return {
        'ticker': asset.ticker,
        'name': asset.name,
        'verdict': verdict,
        'verdict_color': verdict_color,
        'verdict_detail': verdict_detail,
        'score_now': round(score_now.total_score, 1) if score_now else None,
        'regime_now': regime_now,
        'factors': factors,
        'score_tally': score_tally,
        'returns': returns,
        'velocity': velocity,
        'velocity_label': velocity_label,
        'current_price': round(current_price, 2),
        'entry_price': round(entry_price_actual, 2),
        'days_held': (today - entry_date).days if entry_date else 0,
        'upcoming_events': upcoming_events,
    }
