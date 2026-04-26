"""Institutional analysis from daily OHLCV bars.

Computes 5 institutional indicators adapted from the TradingView
"Institutional Dashboard" Pine Script to daily timeframe investing:

  1. VWAP 20d rolling — price vs. institutional average cost
  2. FVG (Fair Value Gap) — daily imbalance zones (support/resistance)
  3. Volume Delta — bull/bear volume accumulation/distribution
  4. POC 20d (Point of Control) — highest-volume price level
  5. Liquidity Sweep — stop-hunt pattern on daily bars

Each indicator returns +1 (bullish), 0 (neutral), or -1 (bearish).
Total score mapped to 0-100 for use in Decision Matrix.

All calculations use only daily OHLCV data already in the DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class InstitutionalSignal:
    """Result of institutional analysis for a single asset."""
    score: float                  # 0-100 composite
    bias: str                     # 'bullish', 'neutral', 'bearish'
    details: list[str]            # human-readable reasons
    factors: list[dict]           # [{name, value (+1/0/-1), detail}]

    # Raw indicator values
    vwap_20d: float | None = None
    vwap_bias: str = 'neutral'    # 'above', 'below', 'extended_above', 'extended_below'
    fvg_bull: dict | None = None  # {'top': x, 'bot': y} — unfilled bullish gap
    fvg_bear: dict | None = None
    poc_20d: float | None = None
    poc_bias: str = 'neutral'     # 'above', 'below'
    delta_trend: str = 'neutral'  # 'accumulation', 'distribution', 'neutral'
    sweep_recent: str | None = None  # 'sweep_low', 'sweep_high', None


def _compute_vwap(prices, period: int = 20) -> tuple[float | None, float | None]:
    """Rolling VWAP over last `period` days. Returns (vwap, std_dev)."""
    recent = prices[-period:] if len(prices) >= period else prices
    if not recent:
        return None, None
    total_pv = sum(((p.high + p.low + p.close) / 3) * (p.volume or 0) for p in recent)
    total_v = sum(p.volume or 0 for p in recent)
    if total_v == 0:
        return None, None
    vwap = total_pv / total_v

    # Standard deviation of typical price around VWAP
    hlc3_vals = [(p.high + p.low + p.close) / 3 for p in recent]
    mean = sum(hlc3_vals) / len(hlc3_vals)
    variance = sum((x - mean) ** 2 for x in hlc3_vals) / len(hlc3_vals)
    std = variance ** 0.5

    return vwap, std


def _find_fvg(prices, min_gap_pct: float = 0.1) -> tuple[dict | None, dict | None]:
    """Find most recent unfilled FVG (bullish and bearish).

    Bullish FVG: low[0] > high[2] — gap up (imbalance, acts as support)
    Bearish FVG: high[0] < low[2] — gap down (imbalance, acts as resistance)
    """
    if len(prices) < 3:
        return None, None

    bull_fvg = None
    bear_fvg = None
    current_close = prices[-1].close

    # Scan recent bars (most recent first)
    for i in range(len(prices) - 1, 1, -1):
        p0 = prices[i]      # current bar
        p2 = prices[i - 2]  # 2 bars ago

        # Bullish FVG: current low > 2-bars-ago high
        if bull_fvg is None:
            gap_size = (p0.low - p2.high) / p2.high * 100
            if p0.low > p2.high and gap_size >= min_gap_pct:
                # Check if still unfilled (current price above bot)
                if current_close >= p2.high:
                    bull_fvg = {'top': round(p0.low, 2), 'bot': round(p2.high, 2), 'gap_pct': round(gap_size, 2)}

        # Bearish FVG: current high < 2-bars-ago low
        if bear_fvg is None:
            if p0.high < p2.low:
                gap_size = (p2.low - p0.high) / p2.low * 100
                if gap_size >= min_gap_pct:
                    # Check if still unfilled (current price below top)
                    if current_close <= p2.low:
                        bear_fvg = {'top': round(p2.low, 2), 'bot': round(p0.high, 2), 'gap_pct': round(gap_size, 2)}

        if bull_fvg and bear_fvg:
            break

    return bull_fvg, bear_fvg


def _compute_volume_delta(prices, period: int = 10) -> tuple[str, float]:
    """Bull/bear volume delta over last `period` days.

    Bull volume: days where close >= open
    Bear volume: days where close < open

    Returns (trend, delta_ratio) where delta_ratio = (bull - bear) / total
    """
    recent = prices[-period:] if len(prices) >= period else prices
    if not recent:
        return 'neutral', 0.0

    bull_vol = sum(p.volume or 0 for p in recent if p.close >= p.open)
    bear_vol = sum(p.volume or 0 for p in recent if p.close < p.open)
    total = bull_vol + bear_vol
    if total == 0:
        return 'neutral', 0.0

    delta_ratio = (bull_vol - bear_vol) / total  # -1 to +1

    if delta_ratio > 0.15:
        return 'accumulation', round(delta_ratio, 3)
    elif delta_ratio < -0.15:
        return 'distribution', round(delta_ratio, 3)
    return 'neutral', round(delta_ratio, 3)


def _compute_poc(prices, period: int = 20, buckets: int = 20) -> float | None:
    """Simplified Point of Control: price level with highest volume."""
    recent = prices[-period:] if len(prices) >= period else prices
    if len(recent) < 5:
        return None

    lo = min(p.low for p in recent)
    hi = max(p.high for p in recent)
    if hi <= lo:
        return None

    step = (hi - lo) / buckets
    vol_by_bucket = [0.0] * buckets

    for p in recent:
        vol = p.volume or 0
        bar_range = p.high - p.low
        if bar_range < 1e-9:
            continue
        for b in range(buckets):
            b_lo = lo + step * b
            b_hi = lo + step * (b + 1)
            overlap_lo = max(b_lo, p.low)
            overlap_hi = min(b_hi, p.high)
            if overlap_hi > overlap_lo:
                fraction = (overlap_hi - overlap_lo) / bar_range
                vol_by_bucket[b] += vol * fraction

    poc_idx = vol_by_bucket.index(max(vol_by_bucket))
    poc = lo + step * poc_idx + step * 0.5
    return round(poc, 2)


def _detect_liquidity_sweep(prices, period: int = 20) -> str | None:
    """Detect recent liquidity sweep on last 3 bars.

    Sweep High: high > period_high[1:] AND close < period_high (bear trap)
    Sweep Low:  low  < period_low[1:]  AND close > period_low  (bull trap)
    """
    if len(prices) < period + 3:
        return None

    # Check last 3 bars for a sweep
    for i in range(len(prices) - 1, len(prices) - 4, -1):
        p = prices[i]
        lookback = prices[max(0, i - period): i]
        if not lookback:
            continue
        liq_high = max(pp.high for pp in lookback)
        liq_low  = min(pp.low  for pp in lookback)

        if p.high > liq_high and p.close < liq_high:
            return 'sweep_high'  # bear trap — institutions grabbed stops above
        if p.low < liq_low and p.close > liq_low:
            return 'sweep_low'   # bull trap — institutions grabbed stops below

    return None


def analyse_institutional(prices) -> InstitutionalSignal:
    """Run full institutional analysis on daily price series."""
    if not prices or len(prices) < 20:
        return InstitutionalSignal(
            score=50.0, bias='neutral',
            details=['Insufficient data (need 20+ days)'],
            factors=[],
        )

    current = prices[-1].close
    factors: list[dict] = []
    tally = 0  # sum of +1/0/-1

    # ── 1. VWAP 20d ──────────────────────────────────────────────────────────
    vwap, vwap_std = _compute_vwap(prices, 20)
    vwap_bias = 'neutral'
    if vwap:
        if vwap_std and current > vwap + vwap_std * 2:
            vwap_bias = 'extended_above'
            v_val = -1  # overextended — risk of pullback
            v_detail = f'Preço sobreextendido acima do VWAP ${vwap:.2f} (+2σ) — pullback possível'
        elif vwap_std and current < vwap - vwap_std * 2:
            vwap_bias = 'extended_below'
            v_val = 1   # oversold relative to VWAP
            v_detail = f'Preço sobreextendido abaixo do VWAP ${vwap:.2f} (-2σ) — bounce possível'
        elif current > vwap:
            vwap_bias = 'above'
            v_val = 1
            v_detail = f'Preço acima do VWAP ${vwap:.2f} — bias institucional bullish'
        else:
            vwap_bias = 'below'
            v_val = -1
            v_detail = f'Preço abaixo do VWAP ${vwap:.2f} — bias institucional bearish'
        factors.append({'name': 'VWAP 20d', 'value': v_val, 'detail': v_detail})
        tally += v_val

    # ── 2. FVG ────────────────────────────────────────────────────────────────
    fvg_bull, fvg_bear = _find_fvg(prices)
    if fvg_bull and not fvg_bear:
        f_val = 1
        f_detail = f'FVG bullish ${fvg_bull["bot"]}-${fvg_bull["top"]} — imbalance de suporte por preencher'
    elif fvg_bear and not fvg_bull:
        f_val = -1
        f_detail = f'FVG bearish ${fvg_bear["bot"]}-${fvg_bear["top"]} — imbalance de resistência por preencher'
    elif fvg_bull and fvg_bear:
        # Price relative to both gaps
        dist_bull = abs(current - (fvg_bull['bot'] + fvg_bull['top']) / 2)
        dist_bear = abs(current - (fvg_bear['bot'] + fvg_bear['top']) / 2)
        if dist_bull < dist_bear:
            f_val = 1
            f_detail = f'FVG bullish próximo ${fvg_bull["bot"]}-${fvg_bull["top"]} — suporte institucional'
        else:
            f_val = -1
            f_detail = f'FVG bearish próximo ${fvg_bear["bot"]}-${fvg_bear["top"]} — resistência institucional'
    else:
        f_val = 0
        f_detail = 'Sem FVG activo — mercado sem imbalances significativos'
    factors.append({'name': 'Fair Value Gap', 'value': f_val, 'detail': f_detail})
    tally += f_val

    # ── 3. Volume Delta ───────────────────────────────────────────────────────
    delta_trend, delta_ratio = _compute_volume_delta(prices, 10)
    if delta_trend == 'accumulation':
        d_val = 1
        d_detail = f'Acumulação: bull volume domina (+{delta_ratio*100:.0f}% net bull) — pressão compradora'
    elif delta_trend == 'distribution':
        d_val = -1
        d_detail = f'Distribuição: bear volume domina ({delta_ratio*100:.0f}% net bear) — pressão vendedora'
    else:
        d_val = 0
        d_detail = f'Volume equilibrado — sem sinal direcional claro'
    factors.append({'name': 'Volume Delta (10d)', 'value': d_val, 'detail': d_detail})
    tally += d_val

    # ── 4. POC 20d ────────────────────────────────────────────────────────────
    poc = _compute_poc(prices, 20)
    poc_bias = 'neutral'
    if poc:
        if current > poc:
            poc_bias = 'above'
            p_val = 1
            p_detail = f'Preço acima do POC ${poc:.2f} — zona de valor confirmada como suporte'
        else:
            poc_bias = 'below'
            p_val = -1
            p_detail = f'Preço abaixo do POC ${poc:.2f} — zona institucional a funcionar como resistência'
        factors.append({'name': 'POC 20d', 'value': p_val, 'detail': p_detail})
        tally += p_val

    # ── 5. Liquidity Sweep ────────────────────────────────────────────────────
    sweep = _detect_liquidity_sweep(prices, 20)
    if sweep == 'sweep_low':
        s_val = 1
        s_detail = 'Sweep Low recente — instituições capturaram stops abaixo e reverteram: bullish'
    elif sweep == 'sweep_high':
        s_val = -1
        s_detail = 'Sweep High recente — instituições capturaram stops acima e reverteram: bearish'
    else:
        s_val = 0
        s_detail = 'Sem liquidity sweep recente'
    factors.append({'name': 'Liquidity Sweep', 'value': s_val, 'detail': s_detail})
    tally += s_val

    # ── Composite score ───────────────────────────────────────────────────────
    max_possible = len(factors)
    if max_possible == 0:
        score = 50.0
    else:
        # Map tally from [-max, +max] to [0, 100]
        score = 50.0 + (tally / max_possible) * 40.0
        score = max(10.0, min(90.0, score))

    if tally >= 2:
        bias = 'bullish'
    elif tally <= -2:
        bias = 'bearish'
    else:
        bias = 'neutral'

    details = [f['detail'] for f in factors]

    return InstitutionalSignal(
        score=round(score, 1),
        bias=bias,
        details=details,
        factors=factors,
        vwap_20d=round(vwap, 2) if vwap else None,
        vwap_bias=vwap_bias,
        fvg_bull=fvg_bull,
        fvg_bear=fvg_bear,
        poc_20d=poc,
        poc_bias=poc_bias,
        delta_trend=delta_trend,
        sweep_recent=sweep,
    )
