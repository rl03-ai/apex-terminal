import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

interface SubScores {
  quality: number
  timing: number
  regime: number
  risk_reward: number
  institutional: number
}

interface MatrixRow {
  asset_id: string
  ticker: string
  name: string
  sector?: string
  current_price: number
  is_watchlist: boolean
  in_early_signals: boolean
  insider_signal: string | null
  setup_score: number
  verdict: 'STRONG_SETUP' | 'GOOD' | 'WAIT' | 'AVOID'
  sub_scores: SubScores
  regime: string
  regime_confidence: number
  rr_details: { upside_pct: number; stop_distance_pct: number; rr_ratio: number }
  total_score: number
}

interface MatrixResponse {
  total_count?: number
  count: number
  verdict_counts: Record<string, number>
  matrix: MatrixRow[]
}

const VERDICT_LABELS: Record<string, string> = {
  STRONG_SETUP: '✅ STRONG',
  GOOD:         '🟢 GOOD',
  WAIT:         '🟡 WAIT',
  AVOID:        '❌ AVOID',
}

export function DecisionMatrixPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<MatrixResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [minVerdict, setMinVerdict] = useState<string>('GOOD')
  const [filterSector, setFilterSector] = useState<string>('all')
  const [onlyWatchlist, setOnlyWatchlist] = useState(false)
  const [excludeHeld, setExcludeHeld] = useState(true)
  const [selectedRow, setSelectedRow] = useState<MatrixRow | null>(null)

  async function load() {
    try {
      setLoading(true)
      const params = new URLSearchParams()
      params.set('only_watchlist', String(onlyWatchlist))
      params.set('exclude_held', String(excludeHeld))
      params.set('min_verdict', minVerdict)
      params.set('limit', '15')
      const result = await api.get<MatrixResponse>(`/decision-matrix?${params}`)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [onlyWatchlist, excludeHeld, minVerdict])

  const sectors = useMemo(() => {
    if (!data) return []
    return Array.from(new Set(data.matrix.map(r => r.sector || 'Unknown'))).sort()
  }, [data])

  const filtered = useMemo(() => {
    if (!data) return []
    return data.matrix.filter(r => {
      if (filterSector !== 'all' && (r.sector || 'Unknown') !== filterSector) return false
      return true
    })
  }, [data, filterSector])

  async function toggleWatchlist(ticker: string, isWatched: boolean) {
    try {
      if (isWatched) {
        await api.delete(`/watchlist/${ticker}`)
      } else {
        await api.post(`/watchlist/${ticker}`)
      }
      await load()
    } catch (err) {
      console.error(err)
    }
  }

  return (
    <div className="page-stack">
      <section className="page-banner card">
        <div>
          <h1>Decision Matrix</h1>
          <p className="muted">
            Setup composto de qualidade + timing + regime + R/R para escolher entradas.
          </p>
        </div>
      </section>

      {/* Verdict filter */}
      {data && (
        <div className="verdict-summary">
          <button
            className={`verdict-pill ${minVerdict === 'STRONG_SETUP' ? 'active verdict-strong_setup' : ''}`}
            onClick={() => setMinVerdict('STRONG_SETUP')}
          >
            ✅ STRONG ({data.verdict_counts.STRONG_SETUP || 0})
          </button>
          <button
            className={`verdict-pill ${minVerdict === 'GOOD' ? 'active verdict-good' : ''}`}
            onClick={() => setMinVerdict('GOOD')}
          >
            🟢 GOOD+ ({(data.verdict_counts.STRONG_SETUP || 0) + (data.verdict_counts.GOOD || 0)})
          </button>
          <button
            className={`verdict-pill ${minVerdict === 'WAIT' ? 'active verdict-wait' : ''}`}
            onClick={() => setMinVerdict('WAIT')}
          >
            🟡 WAIT+ ({(data.verdict_counts.STRONG_SETUP || 0) + (data.verdict_counts.GOOD || 0) + (data.verdict_counts.WAIT || 0)})
          </button>
          <button
            className={`verdict-pill ${minVerdict === 'all' ? 'active' : ''}`}
            onClick={() => setMinVerdict('all')}
          >
            Tudo ({data.total_count || data.count})
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="card matrix-filters">
        <label className="matrix-filter-toggle">
          <input
            type="checkbox"
            checked={onlyWatchlist}
            onChange={(e) => setOnlyWatchlist(e.target.checked)}
          />
          ⭐ Só watchlist
        </label>
        <label className="matrix-filter-toggle">
          <input
            type="checkbox"
            checked={excludeHeld}
            onChange={(e) => setExcludeHeld(e.target.checked)}
          />
          Esconder o que tenho na carteira
        </label>
        <select value={filterSector} onChange={(e) => setFilterSector(e.target.value)}>
          <option value="all">Todos os setores</option>
          {sectors.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <button className="btn-secondary" onClick={load}>↻</button>
      </div>

      {/* Matrix */}
      {loading ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#8ea0bb' }}>
          A calcular matriz…
        </div>
      ) : error ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#f87171' }}>
          {error}
        </div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#8ea0bb' }}>
          Sem resultados para os filtros aplicados.
        </div>
      ) : (
        <div className="card">
          <div className="table-wrapper">
            <table className="matrix-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Ticker</th>
                  <th>Setup</th>
                  <th>Verdict</th>
                  <th className="hide-mobile">Quality</th>
                  <th className="hide-mobile">Timing</th>
                  <th className="hide-mobile">Regime</th>
                  <th className="hide-mobile">R/R</th>
                  <th className="hide-mobile">Inst.</th>
                  <th className="hide-mobile">Sinais</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr
                    key={r.ticker}
                    className="table-row-clickable"
                    onClick={() => setSelectedRow(r)}
                  >
                    <td>
                      <button
                        className={`star-btn ${r.is_watchlist ? 'star-active' : ''}`}
                        onClick={(e) => { e.stopPropagation(); void toggleWatchlist(r.ticker, r.is_watchlist) }}
                        title={r.is_watchlist ? 'Remover da watchlist' : 'Adicionar à watchlist'}
                      >
                        {r.is_watchlist ? '⭐' : '☆'}
                      </button>
                    </td>
                    <td>
                      <div className="strong table-ticker">{r.ticker}</div>
                      <div className="muted small hide-mobile">{r.name}</div>
                    </td>
                    <td>
                      <div className="setup-score-cell">
                        <strong className={`setup-${r.verdict.toLowerCase()}`}>
                          {r.setup_score.toFixed(0)}
                        </strong>
                      </div>
                    </td>
                    <td>
                      <span className={`verdict-pill-small verdict-${r.verdict.toLowerCase()}`}>
                        {VERDICT_LABELS[r.verdict]}
                      </span>
                    </td>
                    <td className="hide-mobile">
                      <SubScoreBar score={r.sub_scores.quality} />
                    </td>
                    <td className="hide-mobile">
                      <SubScoreBar score={r.sub_scores.timing} />
                    </td>
                    <td className="hide-mobile">
                      <SubScoreBar score={r.sub_scores.regime} />
                      <div className="muted small">{r.regime.replace('_', ' ')}</div>
                    </td>
                    <td className="hide-mobile">
                      <SubScoreBar score={r.sub_scores.risk_reward} />
                      <div className="muted small">{r.rr_details.rr_ratio.toFixed(1)}x</div>
                    </td>
                    <td className="hide-mobile">
                      <SubScoreBar score={r.sub_scores.institutional} />
                    </td>
                    <td className="hide-mobile">
                      <div className="signal-icons">
                        {r.in_early_signals && <span title="Early Signal" className="signal-icon">⚡</span>}
                        {r.insider_signal && <span title={r.insider_signal} className="signal-icon">💼</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Detail drawer */}
      {selectedRow && (
        <div className="modal-backdrop" onClick={() => setSelectedRow(null)}>
          <div className="modal-content matrix-detail" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2>{selectedRow.ticker}</h2>
                <div className="muted small">{selectedRow.name}</div>
              </div>
              <button className="modal-close" onClick={() => setSelectedRow(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="matrix-detail-verdict">
                <div className={`big-verdict verdict-${selectedRow.verdict.toLowerCase()}`}>
                  {VERDICT_LABELS[selectedRow.verdict]}
                </div>
                <div className="big-setup-score">
                  Setup: <strong>{selectedRow.setup_score.toFixed(0)}</strong>
                </div>
              </div>
              <div className="matrix-sub-grid">
                <SubScoreCard label="Quality"      score={selectedRow.sub_scores.quality}      hint={`Score estrutural: ${selectedRow.total_score.toFixed(0)}`} />
                <SubScoreCard label="Timing"       score={selectedRow.sub_scores.timing}       hint={`${selectedRow.in_early_signals ? '⚡ Early signal · ' : ''}${selectedRow.insider_signal || ''}`} />
                <SubScoreCard label="Regime"       score={selectedRow.sub_scores.regime}       hint={`${selectedRow.regime.replace('_', ' ')} · conf ${(selectedRow.regime_confidence * 100).toFixed(0)}%`} />
                <SubScoreCard label="Risk/Reward"  score={selectedRow.sub_scores.risk_reward}  hint={`Upside ${selectedRow.rr_details.upside_pct.toFixed(0)}% · Stop ${selectedRow.rr_details.stop_distance_pct.toFixed(0)}% · R/R ${selectedRow.rr_details.rr_ratio.toFixed(1)}x`} />
                <SubScoreCard label="Institutional" score={selectedRow.sub_scores.institutional} hint="VWAP · FVG · Delta · POC · Sweeps" />
              </div>
              <div className="matrix-detail-actions">
                <button className="btn-secondary" onClick={() => navigate(`/asset/${selectedRow.ticker}`)}>
                  Ver detalhe completo →
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function SubScoreBar({ score }: { score: number }) {
  const color = score >= 70 ? '#22c55e' : score >= 50 ? '#fbbf24' : '#f87171'
  return (
    <div className="sub-score-cell">
      <div className="sub-score-num" style={{ color }}>{score.toFixed(0)}</div>
      <div className="sub-score-track">
        <div className="sub-score-fill" style={{ width: `${Math.max(0, Math.min(100, score))}%`, background: color }} />
      </div>
    </div>
  )
}

function SubScoreCard({ label, score, hint }: { label: string; score: number; hint: string }) {
  const color = score >= 70 ? '#22c55e' : score >= 50 ? '#fbbf24' : '#f87171'
  return (
    <div className="card sub-score-card">
      <div className="muted small">{label}</div>
      <div className="sub-score-big" style={{ color }}>{score.toFixed(0)}</div>
      <div className="muted small">{hint || '—'}</div>
    </div>
  )
}
