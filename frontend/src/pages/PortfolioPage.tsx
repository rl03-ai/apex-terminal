import { useEffect, useMemo, useState } from 'react'
import { fetchPortfolioPositions, fetchPortfolios } from '../api/endpoints'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { PortfolioTable } from '../components/PortfolioTable'
import { ScoreBar } from '../components/ScoreBar'
import { SectionCard } from '../components/SectionCard'
import { StatCard } from '../components/StatCard'
import type { Portfolio, Position } from '../types'

export function PortfolioPage() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string>('')
  const [positions, setPositions] = useState<Position[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function loadPortfolios() {
      try {
        setLoading(true)
        const portfolioData = await fetchPortfolios()
        setPortfolios(portfolioData)
        if (portfolioData.length > 0) {
          setSelectedPortfolioId(portfolioData[0].id)
        }
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
        setLoading(false)
      }
    }

    void loadPortfolios()
  }, [])

  useEffect(() => {
    async function loadPositions() {
      if (!selectedPortfolioId) {
        setLoading(false)
        return
      }
      try {
        setLoading(true)
        const positionData = await fetchPortfolioPositions(selectedPortfolioId)
        setPositions(positionData)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
      } finally {
        setLoading(false)
      }
    }

    void loadPositions()
  }, [selectedPortfolioId])

  const summary = useMemo(() => {
    const invested = positions.reduce((sum, item) => sum + item.invested_amount, 0)
    const value = positions.reduce((sum, item) => sum + (item.current_value || 0), 0)
    const pnl = value - invested
    const pnlPct = invested ? (pnl / invested) * 100 : 0
    const avgScore = positions.length ? positions.reduce((sum, item) => sum + (item.total_score || 0), 0) / positions.length : 0
    return { invested, value, pnlPct, avgScore }
  }, [positions])

  return (
    <div className="page-stack">
      <section className="page-banner card">
        <div>
          <h1>Carteira</h1>
          <p className="muted">Acompanhamento das posições registadas na plataforma.</p>
        </div>
        <div className="toolbar-row">
          <select value={selectedPortfolioId} onChange={(e) => setSelectedPortfolioId(e.target.value)}>
            {portfolios.map((portfolio) => (
              <option key={portfolio.id} value={portfolio.id}>
                {portfolio.name}
              </option>
            ))}
          </select>
        </div>
      </section>

      <div className="stats-grid">
        <StatCard label="Investido" value={summary.invested.toFixed(2)} />
        <StatCard label="Valor atual" value={summary.value.toFixed(2)} />
        <StatCard label="Retorno" value={`${summary.pnlPct.toFixed(2)}%`} />
        <StatCard label="Posições" value={String(positions.length)} />
      </div>

      <div className="two-col-grid">
        <SectionCard title="Portfolio pulse">
          <div className="detail-list">
            <div><span className="muted">Score médio</span><strong>{summary.avgScore.toFixed(1)}</strong></div>
            <ScoreBar value={summary.avgScore} />
            <div><span className="muted">Retorno total</span><strong>{summary.pnlPct.toFixed(2)}%</strong></div>
            <ScoreBar value={Math.max(0, Math.min(100, 50 + summary.pnlPct))} />
          </div>
        </SectionCard>

        <SectionCard title="Allocation view">
          <div className="signal-stack">
            {positions.slice(0, 5).map((position) => {
              const weight = summary.value ? ((position.current_value || 0) / summary.value) * 100 : 0
              return (
                <div className="signal-card" key={position.id}>
                  <div className="list-item-row">
                    <strong>{position.ticker}</strong>
                    <span className="muted small">{weight.toFixed(1)}%</span>
                  </div>
                  <div className="muted small">{position.asset_name}</div>
                  <ScoreBar value={weight} />
                </div>
              )
            })}
            {!positions.length ? <div className="muted">Sem posições registadas.</div> : null}
          </div>
        </SectionCard>
      </div>

      <SectionCard title="Posições">
        {loading ? <LoadingState /> : error ? <ErrorState message={error} /> : <PortfolioTable rows={positions} />}
      </SectionCard>
    </div>
  )
}
