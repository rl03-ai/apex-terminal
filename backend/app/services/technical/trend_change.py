# PATCHED_BY_CHATGPT 2025-12-23 spot_safe_patterns pct_change_fill_method_none
# -*- coding: utf-8 -*-
"""
trend_change.py (v3-lite.3 — Full + Fast Entry)
- PATCH: INVEST spot (ACUMULADOR) — exporta cfg_scaled/scale; bias MTF baseado em mode (mais estável)
- Mantém toda a lógica:
  EMA/ADX/BB width & expansão, Breakouts, CUSUM;
  Score (z-features) + tanh + EWMA;
  Histerese enter/exit + debounce no MODO (LONG/SHORT/FLAT);
  Gate por ADX (config: entrada/manutenção separadas);
  Multi-timeframe (detect_mtf), SCOUT (5m/15m -> 1H);
  Padrões leves (estrutura & candles) + z-volume;
  Ensemble leve (SuperTrend/Donchian/MACD slope);
  NO-REPAINT opcional (ignorar última barra);
  Confirmação de entrada + min. hold.

- Ajustes para sinais mais rápidos:
  · enter_thr = 0.30
  · exit_thr  = 0.08
  · min_state_bars = 1
  · lookback_slope = 2
  · pesos reativos: w_bbexp ↑, w_break ↑, w_slope ↓
  · patterns_weight = 0.08, volume_boost = 0.25
  · ensemble leve com pesos reduzidos
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple, List
import numpy as np
import pandas as pd
import warnings

__all__ = [
    "TrendChangeConfig",
    "TrendChangeDetector",
    "decide",
    "decide_mtf_scout",
    "near_trigger_flag",
    "bias_from_tf",
    "preset_aggressive",
    "preset_balanced_plus",
    "preset_aggressive_15m",
    "preset_balanced_plus_15m",
    "USE_ADX_FOR_ENTRY",
    "ema",
    "adx",
    "bollinger_width",
    "cusum_filter",
]

# ------------------------------------------------------------------
# Global legacy (mantido por compat): usar preferencialmente flags na Config.
# ------------------------------------------------------------------
USE_ADX_FOR_ENTRY: bool = False

# =========================
#  Indicadores utilitários
# =========================

def ema(series: pd.Series, span: int) -> pd.Series:
    """EMA com min_periods=span para reduzir ruído em warm-up."""
    return series.ewm(span=int(span), adjust=False, min_periods=int(span)).mean()

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Retorna (+DI, -DI, ADX) usando suavização EWM clássica."""
    period = int(period)
    up = high.diff()
    dn = -low.diff()
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)

    tr = true_range(high, low, close)
    tr_ewm   = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di  = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean() / tr_ewm
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1/period, adjust=False, min_periods=period).mean() / tr_ewm

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100.0
    adx_val = dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    return plus_di.rename('+DI'), minus_di.rename('-DI'), adx_val.rename('ADX')

def bollinger_width(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.Series:
    ma = close.rolling(period, min_periods=period).mean()
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    upper = ma + n_std * sd
    lower = ma - n_std * sd
    width = (upper - lower) / ma.replace(0, np.nan)
    return width.rename('bb_width')

def cusum_filter(returns: pd.Series, h: pd.Series, drift: pd.Series) -> pd.Series:
    """
    CUSUM simétrico com thresholds adaptativos (h, drift) já escalados pelo sigma.
    Retorna série de {+1, 0, -1}.
    """
    r = returns.fillna(0.0).values
    # Pandas: fillna(method=...) está a ser descontinuado; usar ffill().
    h_val = h.ffill().replace(0, np.nan).values
    d_val = drift.fillna(0.0).values

    s_pos = 0.0
    s_neg = 0.0
    out = np.zeros_like(r, dtype=int)

    for i in range(len(r)):
        if np.isnan(h_val[i]):
            continue
        s_pos = max(0.0, s_pos + r[i] - d_val[i])
        s_neg = min(0.0, s_neg + r[i] + d_val[i])
        if s_pos > h_val[i]:
            out[i] = 1
            s_pos = 0.0
            s_neg = 0.0
        elif s_neg < -h_val[i]:
            out[i] = -1
            s_pos = 0.0
            s_neg = 0.0
    return pd.Series(out, index=returns.index, name='cusum_sig')

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df['high'], df['low'], df['close'])
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean().rename('ATR')

# =========================
#    Ensemble (estados)
# =========================

def supertrend_state(df: pd.DataFrame, atr_period: int = 10, mult: float = 3.0) -> pd.Series:
    """Aproximação de estado do SuperTrend (+1/-1)."""
    atr = _atr(df, atr_period)
    mid = (df['high'] + df['low']) / 2.0
    upper = mid + mult * atr
    lower = mid - mult * atr
    st = pd.Series(0, index=df.index, dtype=int)
    st[df['close'] > upper] = +1
    st[df['close'] < lower] = -1
    st = st.replace(0, np.nan).ffill().fillna(0).astype(int)
    return st.rename('st_state')

def donchian_state(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """+1 se close rompeu máx N (vs anterior); -1 se rompeu mín N (vs anterior)."""
    hi = df['high'].rolling(lookback, min_periods=lookback).max()
    lo = df['low'].rolling(lookback, min_periods=lookback).min()
    up = (df['close'] > hi.shift(1)).astype(int)
    dn = (df['close'] < lo.shift(1)).astype(int) * -1
    sig = (up + dn).replace(0, np.nan).ffill().fillna(0).astype(int)
    return sig.rename('don_state')

def macd_slope_state(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """+1/-1 pela inclinação do histograma MACD (mais responsivo para viragens)."""
    ema_f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd = ema_f - ema_s
    sigl = macd.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd - sigl
    slope = hist.diff()
    # PATCH: garantir Series (evita erros .replace/.ffill)
    st = pd.Series(np.sign(slope.values), index=slope.index)
    st = st.replace(0, np.nan).ffill().fillna(0).astype(int)
    return st.rename('macd_slope')

# =========================
#           Config
# =========================

@dataclass
class TrendChangeConfig:
    # Tendência / vol
    ema_fast: int = 12
    ema_slow: int = 50
    adx_period: int = 14
    bb_period: int = 20
    vol_period: int = 20
    lookback_slope: int = 2

    # Filtros
    min_adx: float = 15.0
    compress_pct: float = 0.25
    expand_rate_min: float = 0.05

    # CUSUM
    cusum_h_mult: float = 2.5
    cusum_drift_mult: float = 0.0

    # Pesos do score — alinhados com standard CTA institucional (soma=1)
    #
    # Alterações face à versão anterior:
    #   w_adx  (ADX slope)  → removido: ADX slope é lagging e penaliza tendências maduras
    #   w_di   (DI+/DI- cruzamento + nível) → NOVO: confirmação direccional institucional
    #   w_bbexp (BB width)  → reduzido 25%→18%: era simétrico, agora direccionado (×sign EMA)
    #   w_cusum             → subido 8%→15%: único filtro com memória estrutural
    #   w_slope, w_break    → ligeiramente ajustados para fechar a soma em 1.0
    w_slope: float = 0.27   # EMA diff slope (direcção + momentum)
    w_di:    float = 0.22   # DI+/DI- direccional (NOVO — substitui ADX slope)
    w_bbexp: float = 0.18   # BB width × sign(EMA diff) — agora direccional
    w_break: float = 0.18   # breakouts (mantido)
    w_cusum: float = 0.15   # CUSUM — filtro de regime com memória (era 8%)

    # Suavização & histerese
    score_ewm_span: int = 6
    enter_thr: float = 0.30
    exit_thr: float = 0.08
    min_state_bars: int = 1

    # -------- Padrões & volume --------
    use_pattern_flags: bool = True
    patterns_weight: float = 0.08
    use_volume: bool = True
    vol_z_window: int = 20
    min_vol_z_for_break: float = 0.0
    volume_boost: float = 0.25

    # -------- Estabilidade / no-repaint --------
    enter_confirm_bars: int = 1
    min_hold_bars: int = 2
    use_closed_only: bool = True
    drop_last_n: int = 0

    # -------- Ensemble (opcional) --------
    use_alt_ensemble: bool = True
    w_supertrend: float = 0.03
    w_donchian: float = 0.03
    w_macd: float = 0.02
    donchian_lookback: int = 20
    supertrend_atr_period: int = 10
    supertrend_mult: float = 3.0

    # -------- Gate por ADX (config) --------
    use_adx_for_entry: bool = False
    use_adx_for_maintain: bool = True

    def validate(self) -> None:
        assert 0 <= self.compress_pct <= 1
        assert 0 < self.score_ewm_span
        assert 0 <= self.exit_thr < self.enter_thr < 1.0
        assert self.min_state_bars >= 1
        assert 0.0 <= self.volume_boost <= 1.0
        assert self.enter_confirm_bars >= 1
        assert self.min_hold_bars >= 0
        assert self.drop_last_n >= 0
        # Normalização dos pesos do score, com aviso caso a soma seja diferente de 1
        wsum = self.w_slope + self.w_di + self.w_bbexp + self.w_break + self.w_cusum
        if not np.isclose(wsum, 1.0, atol=1e-6):
            warnings.warn(
                f"TrendChangeConfig: pesos do score não somam 1 (soma={wsum:.3f}); "
                "a normalização automática será aplicada.",
                RuntimeWarning,
            )
            self.w_slope /= wsum
            self.w_di    /= wsum
            self.w_bbexp /= wsum
            self.w_break /= wsum
            self.w_cusum /= wsum

# =========================
#   Padrões leves (flags)
# =========================

def _market_structure_flag(df: pd.DataFrame) -> pd.Series:
    hh = df['high'] > df['high'].shift(1)
    hl = df['low']  > df['low'].shift(1)
    lh = df['high'] < df['high'].shift(1)
    ll = df['low']  < df['low'].shift(1)

    up_seq = (hh & hl).rolling(3, min_periods=1).sum() >= 2
    dn_seq = (lh & ll).rolling(3, min_periods=1).sum() >= 2

    ms = pd.Series(0, index=df.index, dtype=int)
    ms[up_seq] = +1
    ms[dn_seq] = -1
    return ms.rename('ms_flag')

def _candle_flags(df: pd.DataFrame) -> pd.DataFrame:
    o = df['open']
    h = df['high']
    l = df['low']
    c = df['close']
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)

    # Engulfings
    prev_o = o.shift(1); prev_c = c.shift(1)
    bull_eng = ((prev_c < prev_o) & (c > o) & (c >= prev_o) & (o <= prev_c)).astype(int)
    bear_eng = ((prev_c > prev_o) & (c < o) & (c <= prev_o) & (o >= prev_c)).astype(int)

    upper_w = h - c.where(c >= o, o)
    lower_w = (o.where(c >= o, c)) - l
    pin_up   = ((upper_w >= 2*body) & (upper_w >= 0.6*rng) & (body <= 0.4*rng)).astype(int)
    pin_down = ((lower_w >= 2*body) & (lower_w >= 0.6*rng) & (body <= 0.4*rng)).astype(int)

    inside = ((h <= h.shift(1)) & (l >= l.shift(1))).astype(int)

    return pd.DataFrame({
        'bull_engulf': bull_eng.fillna(0),
        'bear_engulf': bear_eng.fillna(0),
        'pin_up': pin_up.fillna(0),
        'pin_down': pin_down.fillna(0),
        'inside': inside.fillna(0),
    }, index=df.index)

def _volume_z(vol: pd.Series, win: int) -> pd.Series:
    lv = np.log(vol.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    m = lv.rolling(win, min_periods=win).median()
    s = lv.rolling(win, min_periods=win).std(ddof=0)
    z = (lv - m) / s.replace(0, np.nan)
    return z.replace([np.inf, -np.inf], 0.0).fillna(0.0).clip(-5, 5).rename('zvol')

# =========================
#         Detector
# =========================

class TrendChangeDetector:
    """
    Detector de mudança de tendência com score composto + histerese estática (sem custos).
    API:
      - detect(df_ohlc) -> DataFrame (score/state/thresholds/mode/…)
      - detect_mtf({tf: df_ohlc, ...}, gate=True) -> dict[tf] -> DataFrame com 'bias'
    """

    def __init__(self, cfg: Optional[TrendChangeConfig] = None):
        self.cfg = cfg or TrendChangeConfig()
        self.cfg.validate()


    def _effective_cfg(self, n_bars: int) -> TrendChangeConfig:
        """
        Ajusta automaticamente os períodos quando há poucos dados (muito comum em SPOT,
        especialmente em TFs altos como 1M/3d). Isto evita que o detector devolva vazio
        e force o caller a cair em fallback com conf fixa.
        """
        c = self.cfg
        # "mínimo confortável" para os indicadores base
        need = max(c.ema_slow, c.bb_period, c.vol_period, c.adx_period, 50)
        if n_bars >= need:
            return c

        # Escala suave (0.5..1.0) — nunca reduz para valores ridículos
        scale = max(0.55, min(1.0, n_bars / max(1.0, float(need))))
        def _clamp_int(x: int, lo: int, hi: int) -> int:
            return int(max(lo, min(hi, x)))

        # Ajustes principais
        ema_slow = _clamp_int(int(round(c.ema_slow * scale)), 20, c.ema_slow)
        ema_fast = _clamp_int(int(round(c.ema_fast * scale)), 8, min(c.ema_fast, max(10, ema_slow - 2)))
        bb_period = _clamp_int(int(round(c.bb_period * scale)), 14, c.bb_period)
        vol_period = _clamp_int(int(round(c.vol_period * scale)), 14, c.vol_period)
        adx_period = _clamp_int(int(round(c.adx_period * scale)), 10, c.adx_period)

        # Auxiliares
        lookback_slope = _clamp_int(int(round(c.lookback_slope * scale)), 1, c.lookback_slope)
        vol_z_window = _clamp_int(int(round(c.vol_z_window * scale)), 14, c.vol_z_window)
        don_lookback = _clamp_int(int(round(c.donchian_lookback * scale)), 14, c.donchian_lookback)

        cfg2 = replace(
            c,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            bb_period=bb_period,
            vol_period=vol_period,
            adx_period=adx_period,
            lookback_slope=lookback_slope,
            vol_z_window=vol_z_window,
            donchian_lookback=don_lookback,
        )
        cfg2.validate()
        return cfg2

    # -------- Features --------
    def _features(self, df: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        cols_needed = {'open','high','low','close'}
        if not cols_needed.issubset(df.columns):
            raise ValueError(f"DataFrame precisa de colunas {cols_needed}, recebeu {set(df.columns)}")

        ohlc = df[['open','high','low','close']].copy()
        out = pd.DataFrame(index=df.index)

        # EMAs e slopes
        ema_f = ema(ohlc['close'], c.ema_fast)
        ema_s = ema(ohlc['close'], c.ema_slow)
        out['ema_fast'] = ema_f
        out['ema_slow'] = ema_s
        out['ema_diff'] = ema_f - ema_s
        out['ema_diff_slope'] = out['ema_diff'].diff(c.lookback_slope)
        out['ema_fast_slope'] = ema_f.diff(c.lookback_slope)

        # ADX + DI direccional
        di_p, di_m, adx_val = adx(ohlc['high'], ohlc['low'], ohlc['close'], c.adx_period)
        out['di_plus']  = di_p
        out['di_minus'] = di_m
        out['adx']      = adx_val
        out['adx_slope'] = adx_val.diff(c.lookback_slope)  # mantido para compatibilidade

        # di_signal: componente direccional institucional (DI+/DI- × nível ADX normalizado)
        # Combina:
        #   - direcção: sign(DI+ - DI-)  →  +1 bullish, -1 bearish
        #   - magnitude: nível normalizado de ADX  →  força da tendência
        # Resultado: positivo em bull forte, negativo em bear forte, ~0 em range
        di_diff  = di_p - di_m
        adx_norm = (adx_val / 50.0).clip(0.0, 1.0)   # normaliza ADX: 50 = saturação
        out['di_signal'] = di_diff * adx_norm          # direccional + ponderado pela força

        # Bandas de Bollinger
        bb_w = bollinger_width(ohlc['close'], c.bb_period)
        out['bb_width'] = bb_w
        out['bb_exp_rate'] = bb_w.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        # Volatilidade (sigma)
        ret = ohlc['close'].pct_change(fill_method=None)
        sigma = ret.rolling(c.vol_period, min_periods=c.vol_period).std(ddof=0)
        out['vol'] = sigma

        # Percentil da largura (na janela vol_period)
        def _last_pct(window: pd.Series) -> float:
            if window.isna().all():
                return np.nan
            return float(window.rank(pct=True).iloc[-1])
        out['bb_width_pct'] = bb_w.rolling(c.vol_period, min_periods=c.vol_period).apply(_last_pct, raw=False)

        # bb_width_dir: BB width direccional — multiplica pelo sinal da EMA diff
        # Solução para o problema de simetria: bb_width_pct sobe tanto em bear como em bull.
        # Ao multiplicar por sign(ema_diff), a expansão só contribui positivamente
        # quando alinhada com a tendência das EMAs.
        ema_sign = np.sign(out['ema_diff']).replace(0, 1)  # 0 → neutro → assume +1
        out['bb_width_dir'] = out['bb_width_pct'] * ema_sign

        # Breakouts
        hi = ohlc['high'].rolling(c.bb_period, min_periods=c.bb_period).max()
        lo = ohlc['low'].rolling(c.bb_period, min_periods=c.bb_period).min()
        out['break_up'] = (ohlc['close'] > hi.shift(1)).astype(int)
        out['break_dn'] = (ohlc['close'] < lo.shift(1)).astype(int)

        # CUSUM
        h = (sigma * c.cusum_h_mult)
        drift = (sigma * c.cusum_drift_mult)
        out['cusum_sig'] = cusum_filter(ret.fillna(0.0), h=h, drift=drift)

        # Padrões & volume
        if c.use_pattern_flags or c.use_volume:
            ms_flag = _market_structure_flag(ohlc)
            candles = _candle_flags(ohlc)
            out = out.join(ms_flag, how='left')
            out = out.join(candles, how='left')

            if 'volume' in df.columns and c.use_volume:
                zvol = _volume_z(df['volume'], c.vol_z_window)
                out['zvol'] = zvol
            else:
                out['zvol'] = 0.0

        out = out.dropna()
        return out

    # -------- Core score --------
    def _zscore(self, x: pd.Series, win: int) -> pd.Series:
        m = x.rolling(win, min_periods=win).mean()
        s = x.rolling(win, min_periods=win).std(ddof=0)
        z = (x - m) / s.replace(0, np.nan)
        return z.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    def _smooth(self, score_raw: pd.Series) -> pd.Series:
        span = max(1, int(self.cfg.score_ewm_span))
        return score_raw.ewm(span=span, adjust=False, min_periods=span).mean().rename('score')

    def _raw_score_core(self, feats: pd.DataFrame) -> pd.Series:
        c = self.cfg

        # Slope EMA — direcção + momentum (inalterado)
        z_slope = self._zscore(feats['ema_diff_slope'], c.vol_period)

        # DI direccional — substitui ADX slope (que era lagging e não direccional)
        # di_signal = (DI+ - DI-) × (ADX/50): positivo em bull forte, negativo em bear forte
        z_di = self._zscore(feats['di_signal'], c.vol_period)

        # BB width direccional — multiplica pelo sign(EMA diff) para evitar simetria
        # Antes: z_bb subia em qualquer breakout (bull ou bear)
        # Agora: só contribui positivamente quando alinhado com a tendência EMA
        z_bb = self._zscore(feats['bb_width_dir'], c.vol_period)

        # Breakouts e CUSUM (inalterados)
        break_raw = (feats['break_up'] - feats['break_dn']).astype(float)
        cu = feats['cusum_sig'].astype(float)
        z_break = self._zscore(break_raw, c.vol_period)
        z_cu    = self._zscore(cu,        c.vol_period)

        raw = (
            c.w_slope * z_slope +
            c.w_di    * z_di    +
            c.w_bbexp * z_bb    +   # agora direccional
            c.w_break * z_break +
            c.w_cusum * z_cu
        )
        return raw.clip(-5, 5).rename('score_core')

    def _patterns_subscore(self, df: pd.DataFrame, feats: pd.DataFrame) -> Tuple[pd.Series, List[str]]:
        c = self.cfg
        if not c.use_pattern_flags:
            return pd.Series(0.0, index=feats.index, name='pat_raw'), []

        pat = feats[['ms_flag','bull_engulf','bear_engulf','pin_up','pin_down','inside']].copy()
        pat_raw = (
            0.4 * pat['ms_flag'].astype(float) +
            0.2 * pat['bull_engulf'].astype(float) -
            0.2 * pat['bear_engulf'].astype(float) +
            0.1 * pat['pin_up'].astype(float) -
            0.1 * pat['pin_down'].astype(float)
        ).clip(-2, 2)

        last_flags: List[str] = []
        # Guard: pode acontecer 'pat' vazio (poucos dados / features NaN). Neste caso, devolve score 0.
        if pat is None or len(pat) == 0:
            return 0.0, {}
        last = pat.iloc[-1]
        if last['bull_engulf']: last_flags.append('bull_engulf')
        if last['bear_engulf']: last_flags.append('bear_engulf')
        if last['pin_up']:      last_flags.append('pin_up')
        if last['pin_down']:    last_flags.append('pin_down')
        if last['inside']:      last_flags.append('inside_bar')
        if feats['ms_flag'].iloc[-1] == 1:  last_flags.append('hh-hl')
        if feats['ms_flag'].iloc[-1] == -1: last_flags.append('lh-ll')

        return pat_raw.rename('pat_raw'), last_flags

    def _enter_threshold_series(self, feats: pd.DataFrame) -> pd.Series:
        c = self.cfg
        thr = pd.Series(c.enter_thr, index=feats.index, name='enter_thr')
        if 'bb_width_pct' in feats.columns:
            low_vol = feats['bb_width_pct'] <= c.compress_pct
            thr.loc[low_vol] = np.maximum(c.enter_thr * 0.8, c.exit_thr + 0.05)
        return thr

    # -------- Núcleo principal --------
    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        # Garantir dados mínimos antes de calcular as features.
        # Em SPOT (especialmente TFs altos como 1M/3d), é comum termos poucas barras.
        # Em vez de devolver vazio (e forçar fallback no caller), ajustamos períodos
        # automaticamente para funcionar com o histórico disponível.
        orig_cfg = self.cfg
        n_bars = len(df)

        # Escala (telemetria): replicar o mesmo critério do _effective_cfg para reportar ao caller
        c0 = self.cfg
        need = max(c0.ema_slow, c0.bb_period, c0.vol_period, c0.adx_period, 50)
        if n_bars >= need:
            scale_factor = 1.0
        else:
            scale_factor = float(max(0.55, min(1.0, n_bars / max(1.0, float(need)))))

        cfg_eff = self._effective_cfg(n_bars)
        cfg_scaled = (cfg_eff is not orig_cfg)
        if cfg_scaled:
            self.cfg = cfg_eff
        c = self.cfg
        if c.use_closed_only and c.drop_last_n > 0 and len(df) > c.drop_last_n:
            df = df.iloc[:-c.drop_last_n]

        try:
            feats = self._features(df)

            raw_core = self._raw_score_core(feats)
            pat_raw, pat_flags = self._patterns_subscore(df, feats)

            raw_total = raw_core + float(c.patterns_weight) * pat_raw

            if self.cfg.use_alt_ensemble:
                idx = feats.index
                df_idx = df.loc[idx]
                st_sig   = supertrend_state(df_idx, c.supertrend_atr_period, c.supertrend_mult)
                donchian_sig  = donchian_state(df_idx, c.donchian_lookback)
                macd_sig = macd_slope_state(df_idx['close'])
                alt = (c.w_supertrend * st_sig.astype(float) +
                       c.w_donchian  * donchian_sig.astype(float) +
                       c.w_macd      * macd_sig.astype(float)).clip(-1, 1)
                raw_total = (raw_total + alt).clip(-3, 3)

            score_raw = np.tanh(raw_total).rename('score_raw')
            score = self._smooth(score_raw)

            enter_thr = self._enter_threshold_series(feats)
            exit_thr = pd.Series(c.exit_thr, index=feats.index, name='exit_thr')

            adx_ok = feats['adx'] > c.min_adx
            compress = feats['bb_width_pct'] <= c.compress_pct
            expanding = feats['bb_exp_rate'] > c.expand_rate_min

            mode: List[int] = []
            state_txt: List[str] = []
            confidence: List[float] = []
            reason: List[str] = []
            inconsistent_flags: List[bool] = []

            last_mode = 0
            stable_cnt = 0
            hold_cnt = 0
            enter_streak = 0

            for i, ts in enumerate(feats.index):
                s = float(score.iloc[i])
                thr_e = float(enter_thr.iloc[i])
                thr_x = float(exit_thr.iloc[i])
                adx_good = bool(adx_ok.iloc[i])
                is_exp = bool(expanding.iloc[i])
                is_comp = bool(compress.iloc[i])

                want_long  = (s >  thr_e) and (adx_good or not c.use_adx_for_entry)
                want_short = (s < -thr_e) and (adx_good or not c.use_adx_for_entry)

                if (last_mode == 0) and (want_long or want_short):
                    enter_streak += 1
                else:
                    enter_streak = 0

                proposed = last_mode
                if last_mode == 0:
                    if enter_streak >= c.enter_confirm_bars:
                        proposed = +1 if want_long else (-1 if want_short else 0)
                elif last_mode == +1:
                    can_exit = (hold_cnt >= c.min_hold_bars)
                    adx_exit = (not adx_good) if c.use_adx_for_maintain else False
                    if (abs(s) < thr_x or adx_exit) and can_exit:
                        proposed = 0
                elif last_mode == -1:
                    can_exit = (hold_cnt >= c.min_hold_bars)
                    adx_exit = (not adx_good) if c.use_adx_for_maintain else False
                    if (abs(s) < thr_x or adx_exit) and can_exit:
                        proposed = 0

                # Debounce
                if proposed == last_mode:
                    stable_cnt += 1
                    accepted = last_mode
                else:
                    if stable_cnt >= c.min_state_bars:
                        accepted = proposed
                        stable_cnt = 0
                        hold_cnt = 0
                    else:
                        accepted = last_mode
                        stable_cnt += 1

                hold_cnt += 1

                if accepted == +1: base_state = 'UPTREND'
                elif accepted == -1: base_state = 'DOWNTREND'
                else: base_state = 'RANGE'

                near_switch = (abs(s) < (thr_x * 0.9) and is_comp) or (abs(s) < (thr_e * 0.9) and is_exp and not adx_good)
                curr_state = 'TRANSITION' if near_switch else base_state

                adx_comp = max(0.0, (feats['adx'].iloc[i] - c.min_adx) / max(c.min_adx, 1e-6))

                # Confiança corrigida — problema anterior:
                #   conf = 0.4*abs(score) + 0.6*adx_comp
                #   → com ADX=50 e score=0: conf=0.60 (limiar INVEST_FULL sem direcção!)
                #
                # Solução: ADX só contribui proporcionalmente à força do score direccional.
                # score_weight sobe de 0 para 1 conforme abs(score) passa de 0 para enter_thr.
                # Garante que conf alta requer TANTO ADX alto COMO score com direcção.
                thr_e_i = float(enter_thr.iloc[i])
                score_weight = min(1.0, abs(s) / max(thr_e_i, 1e-6))
                conf = float(np.clip(
                    0.40 * abs(s) + 0.60 * min(1.0, adx_comp) * score_weight,
                    0.0, 1.0
                ))

                reasons = []
                if is_comp: reasons.append('compressão')
                if is_exp: reasons.append('expansão')
                if adx_good: reasons.append('ADX forte')
                if feats['break_up'].iloc[i] > 0: reasons.append('breakout↑')
                if feats['break_dn'].iloc[i] > 0: reasons.append('breakout↓')
                if feats['cusum_sig'].iloc[i] == 1: reasons.append('CUSUM↑')
                if feats['cusum_sig'].iloc[i] == -1: reasons.append('CUSUM↓')
                if self.cfg.use_pattern_flags and i == len(feats.index) - 1:
                    reasons.extend(pat_flags)

                # Flag de inconsistência entre direção do estado e sinal do score
                inc = _state_score_inconsistent(base_state, s)
                inconsistent_flags.append(bool(inc))
                if inc:
                    reasons.append('state/score mismatch')

                mode.append(accepted)
                state_txt.append(curr_state)
                confidence.append(round(conf, 3))
                reason.append(', '.join(reasons) if reasons else '—')
                last_mode = accepted

            out = pd.DataFrame({
                'score_raw': score_raw.loc[feats.index],
                'score': score,
                'enter_thr': enter_thr,
                'exit_thr': exit_thr,
                'mode': mode,
                'state': state_txt,
                'confidence': confidence,
                'reason': reason,
                'inconsistent': inconsistent_flags,
            }, index=feats.index)

            if 'zvol' in feats.columns:
                out['zvol'] = feats['zvol']

            out['near_flag'] = near_trigger_flag(out, margin=0.05)


            # Telemetria (caller/UI): indica se a config foi escalada por histórico curto

            out['cfg_scaled'] = bool(cfg_scaled)

            out['scale'] = float(scale_factor)
        finally:
            self.cfg = orig_cfg

        return out

    # -------- Multi-TF (gating top-down) --------
        # -------- Multi-TF (gating top-down) --------
    def _tf_rank(self, tf: str) -> int:
        """
        Ranking simples de timeframes para ordenar do maior para o menor.
        """
        order_map = {
            "1M": 70,
            "1w": 60,
            "3d": 55,
            "1d": 50,
            "12h": 45,
            "8h": 42,
            "4h": 40,
            "2h": 35,
            "1h": 30,
            "30m": 25,
            "15m": 20,
            "5m": 10,
            "1m": 5,
        }
        return order_map.get(tf, 0)

    def detect_mtf(self, data_by_tf: Dict[str, pd.DataFrame], gate: bool = True, bias_tf: Optional[str] = None) -> Dict[str, pd.DataFrame]:
        """
        Processa múltiplos timeframes:
          - calcula o detector em todos os TF fornecidos (ex.: 5m, 15m, 1h, 4h, 1d, 1w)
          - define um 'bias' global top-down.

        Se `bias_tf` for fornecido e existir em `data_by_tf`, é usado como timeframe
        de referência para o bias. Caso contrário, é usada por omissão a 1d se existir,
        ou então o timeframe mais alto disponível.
        """
        if not data_by_tf:
            return {}

        # ordena TFs do MAIOR para o MENOR (1w > 1d > 4h > 1h > 15m > 5m…)
        tfs = sorted(data_by_tf.keys(), key=self._tf_rank, reverse=True)

        # corre o detector em todos os TFs disponíveis
        results = {tf: self.detect(data_by_tf[tf]) for tf in tfs}

        # ---------------- Bias global (top-down) ----------------
        bias_long = 0
        bias_strength = 0.0

        if gate:
            # preferência: usar 1d como bias principal, mas permitir override via `bias_tf`
            if bias_tf is not None and bias_tf in results:
                bias_tf_eff = bias_tf
            elif "1d" in results:
                bias_tf_eff = "1d"
            else:
                # se não houver 1d, usa o TF mais elevado da lista
                bias_tf_eff = tfs[0]

            if bias_tf_eff is not None and bias_tf_eff in results and len(results[bias_tf_eff]):
                last = results[bias_tf_eff].iloc[-1]
                # Bias baseado em 'mode' (mais estável do que 'state', que pode ficar em TRANSITION)
                try:
                    m = int(last.get('mode', 0))
                except Exception:
                    m = 0
                if m == 1:
                    bias_long = 1
                elif m == -1:
                    bias_long = -1
                # força/convicção relativa do bias (normalizada pelo threshold de entrada)
                try:
                    thr_ref = float(last["enter_thr"])
                    sc_ref = float(last["score"])
                    if thr_ref > 0:
                        bias_strength = float(abs(sc_ref) / thr_ref)
                    else:
                        bias_strength = float(abs(sc_ref))
                    bias_strength = float(np.clip(bias_strength, 0.0, 3.0))
                except Exception:
                    bias_strength = 0.0

        # adiciona a coluna 'bias' em cada TF
        for tf, df_out in results.items():
            res = df_out.copy()
            res["bias"] = bias_long
            res["bias_strength"] = bias_strength
            results[tf] = res

        return results

# =========================
#    Helpers MTF e UI
# =========================

def bias_from_tf(out_tf: pd.DataFrame) -> pd.Series:
    cond_long  = (out_tf['state'] == 'UPTREND')   & (out_tf['score'] >  out_tf['enter_thr'])
    cond_short = (out_tf['state'] == 'DOWNTREND') & (out_tf['score'] < -out_tf['enter_thr'])
    bias = pd.Series(0, index=out_tf.index, dtype=int)
    bias.loc[cond_long]  = 1
    bias.loc[cond_short] = -1
    return bias.rename('bias')

def near_trigger_flag(df_out: pd.DataFrame, margin: float = 0.05) -> pd.Series:
    s = df_out['score'].astype(float)
    thr = df_out['enter_thr'].astype(float)
    return ((s.abs() >= thr*(1-margin)) & (s.abs() <= thr*(1+margin))).rename('near_flag')

def decide(df_out: pd.DataFrame, bias: int = 0, bias_suppress_abs: float = 0.6) -> pd.Series:
    score = df_out['score'].astype(float)
    state = df_out['state'].astype(str)
    thr_e = df_out['enter_thr'].astype(float)
    thr_x = df_out['exit_thr'].astype(float)

    decision = pd.Series('HOLD', index=df_out.index, dtype=object)

    if bias == 1:
        decision.loc[(score < 0) & (score.abs() < bias_suppress_abs)] = 'FLAT'
    elif bias == -1:
        decision.loc[(score > 0) & (score.abs() < bias_suppress_abs)] = 'FLAT'

    long_cond = (score > thr_e) & state.isin(['UPTREND', 'TRANSITION'])
    short_cond = (score < -thr_e) & state.isin(['DOWNTREND', 'TRANSITION'])
    decision.loc[long_cond] = 'LONG'
    decision.loc[short_cond] = 'SHORT'

    flat_cond = (score.abs() < thr_x) | (state == 'RANGE')
    decision.loc[flat_cond] = 'FLAT'

    return decision

def decide_mtf_scout(
    out_ref: pd.DataFrame,
    out_bias: Optional[pd.DataFrame] = None,
    out_timing: Optional[pd.DataFrame] = None,
    scout_dir: Optional[str] = None,
) -> pd.Series:
    base = decide(out_ref, bias=0)

    if out_bias is not None:
        bf = bias_from_tf(out_bias)
        suppress = (bf == -1) & (base == 'LONG') | (bf == 1) & (base == 'SHORT')
        base.loc[suppress] = 'FLAT'

    if out_timing is not None:
        near = near_trigger_flag(out_timing, margin=0.08)
        base.loc[near & (base == 'FLAT')] = 'SCOUT'

    if scout_dir is not None:
        scout_long  = scout_dir.upper() == 'LONG'
        scout_short = scout_dir.upper() == 'SHORT'
        base.loc[scout_long  & (base == 'FLAT')] = 'SCOUT_LONG'
        base.loc[scout_short & (base == 'FLAT')] = 'SCOUT_SHORT'
    return base

def _dir_from_state(state: str) -> int:
    s = (state or '').upper()
    if 'UP' in s: return +1
    if 'DOWN' in s: return -1
    return 0

def _state_score_inconsistent(state: str, score: float) -> bool:
    d = _dir_from_state(state)
    if d == 0: return False
    return (score > 0 and d < 0) or (score < 0 and d > 0)

# =========================
#  Presets de configuração
# =========================

def preset_aggressive() -> TrendChangeConfig:
    cfg = TrendChangeConfig()
    cfg.enter_thr = 0.28
    cfg.exit_thr = 0.06
    cfg.score_ewm_span = 5
    cfg.min_state_bars = 1
    cfg.patterns_weight = 0.1
    cfg.volume_boost = 0.3
    cfg.validate()
    return cfg

def preset_balanced_plus() -> TrendChangeConfig:
    cfg = TrendChangeConfig()
    cfg.enter_thr = 0.32
    cfg.exit_thr = 0.10
    cfg.score_ewm_span = 7
    cfg.min_state_bars = 2
    cfg.patterns_weight = 0.06
    cfg.volume_boost = 0.2
    cfg.validate()
    return cfg

def preset_aggressive_15m() -> TrendChangeConfig:
    cfg = preset_aggressive()
    cfg.bb_period = 18
    cfg.vol_period = 18
    cfg.enter_thr = 0.30
    cfg.exit_thr = 0.08
    cfg.validate()
    return cfg

def preset_balanced_plus_15m() -> TrendChangeConfig:
    cfg = preset_balanced_plus()
    cfg.bb_period = 22
    cfg.vol_period = 22
    cfg.validate()
    return cfg