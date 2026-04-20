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
  risk_level: 'red' | 'yellow' | 'green'
  risk_reason: string
  stop_price: number
  distance_to_stop_pct: number
}

export function PortfolioPage() {
  const navigate = useNavigate()
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string>('')
  const [positions, setPositions] = useState<Position[]>([])
  const [positionRisks, setPositionRisks] = useState<Record<string, PositionRisk>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
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
      const positionData = await fetchPortfolioPositions(selectedPortfolioId)
      setPositions(positionData)

      // Fetch per-position risk info
      try {
        const riskData: any = await api.get(`/portfolios/${selectedPortfolioId}/risk`)
        const map: Record<string, PositionRisk> = {}
        for (const pr of (riskData.position_risks || [])) {
          map[pr.position_id] = {
            position_id: pr.position_id,
            risk_level: pr.risk_level,
            risk_reason: pr.risk_reason,
            stop_price: pr.stop_price,
            distance_to_stop_pct: pr.distance_to_stop_pct,
          }
        }
        setPositionRisks(map)
      } catch { /* risk endpoint may fail silently */ }

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
      setRefreshKey(k => k + 1)
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

      {selectedPortfolioId && (
        <RiskOverviewCard portfolioId={selectedPortfolioId} refreshKey={refreshKey} />
      )}

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
                  <th>Qtd</th>
                  <th>Entrada</th>
                  <th>Preço atual</th>
                  <th>Investido</th>
                  <th>Valor atual</th>
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
                  const risk = positionRisks[p.id]
                  return (
                    <tr key={p.id} className="table-row-clickable" onClick={() => navigate(`/positions/${p.id}`)}>
                      <td className="strong table-ticker">{p.ticker}</td>
                      <td>{p.quantity.toFixed(4).replace(/\.?0+$/, '')}</td>
                      <td>${p.avg_cost.toFixed(2)}</td>
                      <td>${currentPrice.toFixed(2)}</td>
                      <td>${p.invested_amount.toFixed(2)}</td>
                      <td>${value.toFixed(2)}</td>
                      <td style={{ color: pnlColor, fontWeight: 700 }}>
                        {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                        <br />
                        <span className="small" style={{ color: pnlColor }}>
                          {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                        </span>
                      </td>
                      <td>
                        {risk ? (
                          <span
                            className={`risk-indicator risk-${risk.risk_level}`}
                            title={`${risk.risk_reason} · Stop $${risk.stop_price.toFixed(2)} (${risk.distance_to_stop_pct.toFixed(1)}% abaixo)`}
                          >
                            {risk.risk_level === 'red' ? '🔴' : risk.risk_level === 'yellow' ? '🟡' : '🟢'}
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
          onAdded={() => { void loadPositions(); setRefreshKey(k => k + 1) }}
        />
      )}
    </div>
  )
}
