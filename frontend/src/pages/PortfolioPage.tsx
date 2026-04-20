import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchPortfolioPositions, fetchPortfolios } from '../api/endpoints'
import { api } from '../api/client'
import { AddPositionModal } from '../components/AddPositionModal'
import { RiskOverviewCard } from '../components/RiskOverviewCard'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { SectionCard } from '../components/SectionCard'
import { StatCard } from '../components/StatCard'
import type { Portfolio, Position } from '../types'

interface PositionRisk {
  position_id: string
  ticker: string
  weight_pct: number
  drawdown_pct: number
  risk_status: 'ok' | 'warning' | 'critical'
  risk_reasons: string[]
  stop_loss: {
    price: number
    distance_pct: number
    method: string
    reasoning: string
  } | null
}

interface RiskData {
  total_value: number
  total_invested: number
  total_pnl_pct: number
  num_positions: number
  top_ticker_concentration: number
  top_ticker_symbol: string | null
  top_sector_concentration: number
  top_sector_name: string | null
  diversification_score: 'low' | 'medium' | 'good'
  alerts: Array<{ level: 'info' | 'warning' | 'critical'; title: string; detail: string }>
  positions: PositionRisk[]
}

export function PortfolioPage() {
  const navigate = useNavigate()
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string>('')
  const [positions, setPositions] = useState<Position[]>([])
  const [risk, setRisk] = useState<RiskData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [positionRisks, setPositionRisks] = useState<Record<string, { risk_level: string; risk_reason: string; stop_price: number; distance_to_stop_pct: number }>>({})
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    async function loadPortfolios() {
      try {
        setLoading(true)
        const portfolioData = await fetchPortfolios()
        setPortfolios(portfolioData)
        if (portfolioData.length > 0) setSelectedPortfolioId(portfolioData[0].id)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
        setLoading(false)
      }
    }
    void loadPortfolios()
  }, [])

  async function loadPositions() {
    if (!selectedPortfolioId) { setLoading(false); return }
    try {
      setLoading(true)
      const [positionData, riskData] = await Promise.all([
        fetchPortfolioPositions(selectedPortfolioId),
        api.get<RiskData>(`/portfolios/${selectedPortfolioId}/risk`).catch(() => null),
      ])
      setPositions(positionData)
      setRisk(riskData)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro inesperado.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadPositions() }, [selectedPortfolioId])

  async function handleRefresh() {
    if (!selectedPortfolioId) return
    setRefreshing(true)
    try {
      await api.post(`/portfolios/${selectedPortfolioId}/positions/refresh`)
      await loadPositions()
      setRefreshKey(k => k + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao atualizar')
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete(positionId: string, ticker: string) {
    if (!confirm(`Remover posição em ${ticker}?`)) return
    try {
      await api.delete(`/portfolios/${selectedPortfolioId}/positions/${positionId}`)
      await loadPositions()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao remover')
    }
  }

  const summary = useMemo(() => {
    const invested = positions.reduce((sum, p) => sum + p.invested_amount, 0)
    const value    = positions.reduce((sum, p) => sum + (p.current_value ?? p.invested_amount), 0)
    const pnl      = value - invested
    const pnlPct   = invested ? (pnl / invested) * 100 : 0
    return { invested, value, pnl, pnlPct }
  }, [positions])

  const riskByPosition = useMemo(() => {
    const map: Record<string, PositionRisk> = {}
    if (risk) {
      for (const pr of risk.positions) {
        map[pr.position_id] = pr
      }
    }
    return map
  }, [risk])

  return (
    <div className="page-stack">
      <section className="page-banner card">
        <div>
          <h1>Carteira</h1>
          <p className="muted">Acompanhamento das tuas posições com P&L em tempo real.</p>
        </div>
        <div className="toolbar-row">
          {portfolios.length > 1 && (
            <select value={selectedPortfolioId} onChange={(e) => setSelectedPortfolioId(e.target.value)}>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}
          <button className="btn-secondary" onClick={handleRefresh} disabled={refreshing}>
            {refreshing ? '...' : '↻ Atualizar'}
          </button>
          <button className="btn-primary" onClick={() => setShowAddModal(true)}>
            + Adicionar Posição
          </button>
        </div>
      </section>

      <div className="stats-grid">
        <StatCard label="Investido" value={`$${summary.invested.toFixed(2)}`} />
        <StatCard label="Valor atual" value={`$${summary.value.toFixed(2)}`} />
        <StatCard
          label="P&L"
          value={`${summary.pnl >= 0 ? '+' : ''}$${summary.pnl.toFixed(2)}`}
          hint={`${summary.pnlPct >= 0 ? '+' : ''}${summary.pnlPct.toFixed(2)}%`}
        />
        <StatCard label="Posições" value={String(positions.length)} />
      </div>

      {/* Risk Overview */}
      {risk && risk.num_positions > 0 && (
        <SectionCard title="🛡️ Risk Overview">
          <div className="risk-grid">
            <div className={`risk-metric risk-${
              risk.top_ticker_concentration > 40 ? 'critical' :
              risk.top_ticker_concentration > 25 ? 'warning' : 'ok'
            }`}>
              <div className="risk-label">Top ticker</div>
              <div className="risk-value">
                {risk.top_ticker_symbol || '—'} · {risk.top_ticker_concentration.toFixed(0)}%
              </div>
              <div className="risk-hint muted small">
                {risk.top_ticker_concentration > 40 ? 'Excessiva — reduzir' :
                 risk.top_ticker_concentration > 25 ? 'Alta — atenção' : 'OK'}
              </div>
            </div>

            <div className={`risk-metric risk-${
              risk.top_sector_concentration > 60 ? 'critical' :
              risk.top_sector_concentration > 40 ? 'warning' : 'ok'
            }`}>
              <div className="risk-label">Top setor</div>
              <div className="risk-value">
                {risk.top_sector_name || '—'} · {risk.top_sector_concentration.toFixed(0)}%
              </div>
              <div className="risk-hint muted small">
                {risk.top_sector_concentration > 60 ? 'Diversificar setores' :
                 risk.top_sector_concentration > 40 ? 'Atenção' : 'OK'}
              </div>
            </div>

            <div className={`risk-metric risk-${
              risk.diversification_score === 'low' ? 'warning' :
              risk.diversification_score === 'medium' ? 'ok' : 'ok'
            }`}>
              <div className="risk-label">Diversificação</div>
              <div className="risk-value">
                {risk.num_positions} posições
              </div>
              <div className="risk-hint muted small">
                {risk.diversification_score === 'low' ? 'Baixa (ideal 8-15)' :
                 risk.diversification_score === 'medium' ? 'Média' : 'Boa'}
              </div>
            </div>
          </div>

          {risk.alerts.length > 0 && (
            <div className="risk-alerts">
              {risk.alerts.map((a, i) => (
                <div key={i} className={`risk-alert risk-alert-${a.level}`}>
                  <div className="risk-alert-title">{a.title}</div>
                  <div className="risk-alert-detail">{a.detail}</div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {selectedPortfolioId && <RiskOverviewCard portfolioId={selectedPortfolioId} refreshKey={refreshKey} />}

      <SectionCard title="Posições">
        {loading ? <LoadingState /> :
         error ? <ErrorState message={error} /> :
         positions.length === 0 ? (
          <div className="empty-state-portfolio">
            <p className="muted">Ainda não tens posições registadas.</p>
            <button className="btn-primary" onClick={() => setShowAddModal(true)}>
              Adicionar primeira posição
            </button>
          </div>
         ) : (
          <div className="table-wrapper">
            <table className="position-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Risco</th>
                  <th>Qtd</th>
                  <th>Entrada</th>
                  <th>Atual</th>
                  <th>Stop</th>
                  <th>P&L</th>
                  <th>Risk</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const currentPrice = p.current_value && p.quantity
                    ? p.current_value / p.quantity
                    : p.avg_cost
                  const value   = p.current_value ?? p.invested_amount
                  const pnl     = value - p.invested_amount
                  const pnlPct  = p.invested_amount ? (pnl / p.invested_amount) * 100 : 0
                  const pnlColor = pnl >= 0 ? '#22c55e' : '#f87171'
                  const posRisk = riskByPosition[p.id]
                  return (
                    <tr key={p.id} className="table-row-clickable" onClick={() => navigate(`/positions/${p.id}`)}>
                      <td className="strong table-ticker">{p.ticker}</td>
                      <td>
                        {posRisk ? (
                          <span className={`risk-pill risk-pill-${posRisk.risk_status}`} title={posRisk.risk_reasons.join(' · ') || 'OK'}>
                            {posRisk.risk_status === 'critical' ? '🔴' :
                             posRisk.risk_status === 'warning' ? '🟡' : '🟢'}
                          </span>
                        ) : '—'}
                      </td>
                      <td>{p.quantity.toFixed(4).replace(/\.?0+$/, '')}</td>
                      <td>${p.avg_cost.toFixed(2)}</td>
                      <td>${currentPrice.toFixed(2)}</td>
                      <td>
                        {posRisk?.stop_loss ? (
                          <span title={posRisk.stop_loss.reasoning}>
                            ${posRisk.stop_loss.price.toFixed(2)}
                            <br/>
                            <span className="muted small">-{posRisk.stop_loss.distance_pct.toFixed(1)}%</span>
                          </span>
                        ) : '—'}
                      </td>
                      <td style={{ color: pnlColor, fontWeight: 700 }}>
                        {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                        <br />
                        <span className="small" style={{ color: pnlColor }}>
                          {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                        </span>
                      </td>
                      <td>
                        {positionRisks[p.id] ? (
                          <span
                            className={`risk-indicator risk-${positionRisks[p.id].risk_level}`}
                            title={`${positionRisks[p.id].risk_reason} · Stop $${positionRisks[p.id].stop_price.toFixed(2)} (${positionRisks[p.id].distance_to_stop_pct.toFixed(1)}% abaixo)`}
                          >
                            {positionRisks[p.id].risk_level === 'red' ? '🔴' : positionRisks[p.id].risk_level === 'yellow' ? '🟡' : '🟢'}
                          </span>
                        ) : '—'}
                      </td>
                      <td>
                        <button
                          className="btn-delete"
                          onClick={(e) => { e.stopPropagation(); handleDelete(p.id, p.ticker) }}
                          title="Remover"
                        >✕</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {showAddModal && selectedPortfolioId && (
        <AddPositionModal
          portfolioId={selectedPortfolioId}
          onClose={() => setShowAddModal(false)}
          onAdded={loadPositions}
        />
      )}
    </div>
  )
}
