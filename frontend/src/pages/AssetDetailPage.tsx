import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

interface AssetDetail {
  ticker: string
  name: string
  sector?: string
  industry?: string
  market_cap?: number
  score?: {
    total_score: number
    growth_score: number
    quality_score: number
    valuation_score: number
    market_score: number
    narrative_score: number
    risk_score: number
    state: string
    score_percentile?: number
    score_regime?: string
    score_trajectory?: string
    explanation?: Record<string, string[]>
  }
  prices?: Array<{ date: string; close: number }>
}

const STATE_COLORS: Record<string, string> = {
  active_setup: '#22c55e',
  confirming:   '#6ec1ff',
  emerging:     '#ff9f1a',
  dormant:      '#8ea0bb',
  broken:       '#f87171',
}

const SCORE_LABELS: Record<string, string> = {
  growth_score:    'Crescimento',
  quality_score:   'Qualidade',
  valuation_score: 'Valuation',
  market_score:    'Mercado',
  narrative_score: 'Catalisadores',
  risk_score:      'Risco',
}

const SCORE_DESCRIPTIONS: Record<string, string> = {
  growth_score:    'Revenue YoY, aceleração de margens, crescimento operacional',
  quality_score:   'FCF positivo, alavancagem, diluição de acções',
  valuation_score: 'EV/Sales, P/FCF, PEG ratio vs crescimento',
  market_score:    'Estrutura técnica, momentum 3M/6M, aceleração recente',
  narrative_score: 'Earnings beats, insider buying, catalisadores próximos',
  risk_score:      'Volatilidade, drawdown, risco de diluição',
}

function ScoreRow({ label, desc, value, isRisk = false }: {
  label: string; desc: string; value: number; isRisk?: boolean
}) {
  const display = isRisk ? 100 - value : value
  const color = display >= 70 ? '#22c55e' : display >= 50 ? '#ff9f1a' : '#f87171'
  return (
    <div className="score-row">
      <div className="score-row-header">
        <div>
          <div className="score-row-label">{label}</div>
          <div className="score-row-desc">{desc}</div>
        </div>
        <div className="score-row-value" style={{ color }}>{value.toFixed(0)}</div>
      </div>
      <div className="score-row-bar">
        <div className="score-row-fill" style={{ width: `${value}%`, background: isRisk ? '#f87171' : color }} />
      </div>
    </div>
  )
}

function MiniChart({ prices }: { prices: Array<{ date: string; close: number }> }) {
  if (!prices || prices.length < 2) return null
  const last90 = prices.slice(-90)
  const min = Math.min(...last90.map(p => p.close))
  const max = Math.max(...last90.map(p => p.close))
  const range = max - min || 1
  const w = 400, h = 120
  const pts = last90.map((p, i) => {
    const x = (i / (last90.length - 1)) * w
    const y = h - ((p.close - min) / range) * h
    return `${x},${y}`
  }).join(' ')
  const first = last90[0].close
  const last  = last90[last90.length - 1].close
  const isUp  = last >= first
  const color = isUp ? '#22c55e' : '#f87171'
  const pct   = ((last - first) / first * 100).toFixed(1)
  return (
    <div className="mini-chart-wrap">
      <div className="mini-chart-header">
        <span className="mini-chart-price">${last.toFixed(2)}</span>
        <span className="mini-chart-pct" style={{ color }}>{isUp ? '+' : ''}{pct}% (90d)</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width:'100%', height: 80 }}>
        <defs>
          <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3"/>
            <stop offset="100%" stopColor={color} stopOpacity="0"/>
          </linearGradient>
        </defs>
        <polygon points={`0,${h} ${pts} ${w},${h}`} fill="url(#chartGrad)" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="2" />
      </svg>
    </div>
  )
}

export function AssetDetailPage() {
  const { ticker } = useParams<{ ticker: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<AssetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!ticker) return
    setLoading(true)
    Promise.all([
      api.get<any>(`/assets/${ticker}/score`).catch(() => null),
      api.get<any>(`/assets/${ticker}/prices`).catch(() => null),
    ]).then(([scoreData, priceData]) => {
      setData({
        ticker: ticker.toUpperCase(),
        name: scoreData?.name || ticker,
        sector: scoreData?.sector,
        industry: scoreData?.industry,
        market_cap: scoreData?.market_cap,
        score: scoreData?.score || scoreData,
        prices: priceData?.prices || priceData || [],
      })
    }).catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [ticker])

  if (loading) return (
    <div className="page-stack">
      <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#8ea0bb' }}>
        A carregar dados de {ticker}…
      </div>
    </div>
  )

  if (error || !data) return (
    <div className="page-stack">
      <button className="back-btn" onClick={() => navigate(-1)}>← Voltar</button>
      <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#f87171' }}>
        Erro ao carregar {ticker}. Verifica se o ticker foi ingerido.
      </div>
    </div>
  )

  const score = data.score
  const stateColor = STATE_COLORS[score?.state || 'dormant'] || '#8ea0bb'
  const mcap = data.market_cap
  const mcapStr = mcap
    ? mcap >= 1e12 ? `$${(mcap/1e12).toFixed(1)}T`
    : mcap >= 1e9  ? `$${(mcap/1e9).toFixed(1)}B`
    : `$${(mcap/1e6).toFixed(0)}M`
    : '—'

  const scoreFields = ['growth_score','quality_score','valuation_score','market_score','narrative_score'] as const
  const explanation = score?.explanation || {}

  return (
    <div className="page-stack">
      <button className="back-btn" onClick={() => navigate(-1)}>← Voltar</button>

      {/* Header */}
      <div className="card asset-header">
        <div>
          <div className="asset-ticker">{data.ticker}</div>
          <div className="asset-name">{data.name}</div>
          <div className="asset-meta">
            {data.sector && <span className="meta-chip">{data.sector}</span>}
            {data.industry && <span className="meta-chip">{data.industry}</span>}
            {mcap !== '—' && <span className="meta-chip">{mcapStr}</span>}
          </div>
        </div>
        <div className="asset-score-big">
          <div className="asset-score-number" style={{ color: stateColor }}>
            {score?.total_score?.toFixed(1) ?? '—'}
          </div>
          <div className="asset-score-state" style={{ color: stateColor }}>
            {score?.state?.replace('_', ' ') ?? '—'}
          </div>
          {score?.score_percentile != null && (
            <div className="asset-score-pct">p{score.score_percentile.toFixed(0)} do universo</div>
          )}
        </div>
      </div>

      {/* Price chart */}
      {data.prices && data.prices.length > 0 && (
        <div className="card">
          <div className="section-header"><h2>Preço (90 dias)</h2></div>
          <MiniChart prices={data.prices} />
        </div>
      )}

      {/* Score breakdown */}
      {score && (
        <div className="card">
          <div className="section-header"><h2>Breakdown do Score</h2></div>
          <div className="score-breakdown">
            {scoreFields.map(field => (
              <ScoreRow
                key={field}
                label={SCORE_LABELS[field]}
                desc={SCORE_DESCRIPTIONS[field]}
                value={(score as any)[field] ?? 50}
              />
            ))}
            <ScoreRow
              label="Risco"
              desc={SCORE_DESCRIPTIONS.risk_score}
              value={(score as any).risk_score ?? 50}
              isRisk
            />
          </div>
        </div>
      )}

      {/* Explanation */}
      {Object.keys(explanation).length > 0 && (
        <div className="card">
          <div className="section-header"><h2>Razões detalhadas</h2></div>
          <div className="explanation-grid">
            {Object.entries(explanation).map(([key, reasons]) => (
              Array.isArray(reasons) && reasons.length > 0 ? (
                <div key={key} className="explanation-section">
                  <div className="explanation-label">{SCORE_LABELS[key + '_score'] || key}</div>
                  {reasons.map((r, i) => (
                    <div key={i} className="explanation-reason">• {r}</div>
                  ))}
                </div>
              ) : null
            ))}
          </div>
        </div>
      )}

      {/* Regime */}
      {score?.score_regime && (
        <div className="card">
          <div className="section-header"><h2>Regime de Score</h2></div>
          <div className="regime-display">
            <span className="regime-badge" style={{
              background: STATE_COLORS[score.score_regime?.toLowerCase().includes('up') ? 'active_setup'
                : score.score_regime?.toLowerCase().includes('down') ? 'broken' : 'emerging'] + '22',
              color: STATE_COLORS[score.score_regime?.toLowerCase().includes('up') ? 'active_setup'
                : score.score_regime?.toLowerCase().includes('down') ? 'broken' : 'emerging'],
              border: `1px solid currentColor`,
              padding: '0.4rem 1rem',
              borderRadius: '999px',
              fontWeight: 700,
            }}>
              {score.score_regime}
            </span>
            {score.score_trajectory && (
              <span className="muted small" style={{ marginLeft: '1rem' }}>
                Trajectória: {score.score_trajectory}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
