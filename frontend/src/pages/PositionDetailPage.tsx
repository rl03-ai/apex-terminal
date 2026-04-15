import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { fetchPosition, fetchPositionHistory, fetchPositionScenarios } from '../api/endpoints'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { ScoreBar } from '../components/ScoreBar'
import { SectionCard } from '../components/SectionCard'
import { StatCard } from '../components/StatCard'
import { StatusPill } from '../components/StatusPill'
import type { Position, PositionHistoryPoint, PositionScenario } from '../types'

export function PositionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [position, setPosition] = useState<Position | null>(null)
  const [history, setHistory] = useState<PositionHistoryPoint[]>([])
  const [scenarios, setScenarios] = useState<PositionScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      if (!id) return
      try {
        setLoading(true)
        const [positionData, historyData, scenarioData] = await Promise.all([
          fetchPosition(id),
          fetchPositionHistory(id),
          fetchPositionScenarios(id),
        ])
        setPosition(positionData)
        setHistory(historyData)
        setScenarios(Array.isArray(scenarioData) ? scenarioData : scenarioData ? [scenarioData] : [])
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
      } finally {
        setLoading(false)
      }
    }

    void load()
  }, [id])

  const latestScenario = useMemo(() => scenarios[0], [scenarios])
  const maxScenario = latestScenario ? latestScenario.bull_high : 0

  if (loading) return <LoadingState />
  if (error || !position) return <ErrorState message={error || 'Posição não encontrada.'} />

  return (
    <div className="page-stack">
      <section className="page-banner card">
        <div>
          <div className="hero-kicker">Position detail</div>
          <h1>{position.ticker}</h1>
          <p className="muted">{position.asset_name}</p>
        </div>
        <StatusPill text={position.thesis_status || 'sem estado'} tone="warn" />
      </section>

      <div className="stats-grid">
        <StatCard label="Custo médio" value={position.avg_cost.toFixed(2)} />
        <StatCard label="Preço atual" value={position.current_price?.toFixed(2) ?? '—'} />
        <StatCard label="Retorno" value={position.pnl_pct !== undefined ? `${position.pnl_pct.toFixed(2)}%` : '—'} />
        <StatCard label="Score atual" value={position.total_score?.toFixed(1) ?? '—'} />
      </div>

      <div className="two-col-grid emphasis-grid">
        <SectionCard title="Tese">
          <div className="detail-list">
            <div><span className="muted">Horizonte</span><strong>{position.horizon}</strong></div>
            <div><span className="muted">Tipo</span><strong>{position.position_type}</strong></div>
            <div><span className="muted">Entrada</span><strong>{position.first_buy_date}</strong></div>
            <div><span className="muted">Tese</span><strong className="text-right">{position.thesis}</strong></div>
            <div><span className="muted">Força do score</span><strong>{position.total_score?.toFixed(1) ?? '—'}</strong></div>
            <ScoreBar value={position.total_score || 0} />
          </div>
        </SectionCard>

        <SectionCard title="Cenários">
          {latestScenario ? (
            <div className="scenario-stack">
              <ScenarioRow label="Bear" low={latestScenario.bear_low} high={latestScenario.bear_high} max={maxScenario} />
              <ScenarioRow label="Base" low={latestScenario.base_low} high={latestScenario.base_high} max={maxScenario} />
              <ScenarioRow label="Bull" low={latestScenario.bull_low} high={latestScenario.bull_high} max={maxScenario} />
            </div>
          ) : (
            <div className="muted">Sem cenários calculados.</div>
          )}
        </SectionCard>
      </div>

      <SectionCard title="Histórico da posição">
        {!history.length ? (
          <div className="muted">Sem histórico disponível.</div>
        ) : (
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={[...history].reverse()}>
                <XAxis dataKey="date" hide />
                <YAxis yAxisId="left" />
                <YAxis yAxisId="right" orientation="right" />
                <Tooltip />
                <Line yAxisId="left" type="monotone" dataKey="market_value" stroke="#ff9f1a" strokeWidth={2.25} dot={false} />
                <Line yAxisId="right" type="monotone" dataKey="score_total" stroke="#6ec1ff" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </SectionCard>
    </div>
  )
}

function ScenarioRow({ label, low, high, max }: { label: string; low: number; high: number; max: number }) {
  const midpoint = (low + high) / 2
  return (
    <div className="scenario-row">
      <div className="list-item-row">
        <strong>{label}</strong>
        <span>{low.toFixed(2)} – {high.toFixed(2)}</span>
      </div>
      <ScoreBar value={max ? (midpoint / max) * 100 : 0} />
    </div>
  )
}
