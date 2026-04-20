import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAlerts, fetchEarlySignals, fetchInsiderAlerts, fetchPortfolios, fetchPortfolioPositions, fetchTopOpportunities } from '../api/endpoints'
import type { EarlySignalItem, InsiderAlertItem } from '../api/endpoints'
import type { AlertItem, Portfolio, Position, ScannerResult } from '../types'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { ScannerTable } from '../components/ScannerTable'
import { ScoreBar } from '../components/ScoreBar'
import { SectionCard } from '../components/SectionCard'
import { StatCard } from '../components/StatCard'
import { StatusPill } from '../components/StatusPill'

export function DashboardPage() {
  const navigate = useNavigate()
  const [scannerRows, setScannerRows] = useState<ScannerResult[]>([])
  const [earlySignals, setEarlySignals] = useState<EarlySignalItem[]>([])
  const [insiderAlerts, setInsiderAlerts] = useState<InsiderAlertItem[]>([])
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        setLoading(true)
        const [scannerData, portfolioData, alertData, earlyData, insiderData] = await Promise.all([
          fetchTopOpportunities(),
          fetchPortfolios(),
          fetchAlerts(),
          fetchEarlySignals(10).catch(() => []),
          fetchInsiderAlerts(15).catch(() => []),
        ])
        setEarlySignals(earlyData)
        setInsiderAlerts(insiderData)
        // Deduplicate by ticker — keep highest priority_score per ticker
        const seen = new Map<string, typeof scannerData[0]>()
        for (const row of scannerData) {
          const existing = seen.get(row.ticker)
          if (!existing || row.priority_score > existing.priority_score) {
            seen.set(row.ticker, row)
          }
        }
        setScannerRows(Array.from(seen.values()).sort((a,b) => b.priority_score - a.priority_score))
        setPortfolios(portfolioData)
        setAlerts(alertData)

        if (portfolioData.length > 0) {
          const firstPortfolioPositions = await fetchPortfolioPositions(portfolioData[0].id)
          setPositions(firstPortfolioPositions)
        }
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
      } finally {
        setLoading(false)
      }
    }

    void load()
  }, [])

  const stats = useMemo(() => {
    const totalValue = positions.reduce((sum, item) => sum + (item.current_value || 0), 0)
    const totalInvested = positions.reduce((sum, item) => sum + item.invested_amount, 0)
    const totalPnL = totalValue - totalInvested
    const totalPnLPct = totalInvested ? (totalPnL / totalInvested) * 100 : 0
    const avgScore = scannerRows.length
      ? scannerRows.reduce((sum, item) => sum + item.total_score, 0) / scannerRows.length
      : 0

    return {
      topSignals: scannerRows.length,
      portfolioCount: portfolios.length,
      totalValue,
      totalPnLPct,
      avgScore,
    }
  }, [positions, portfolios.length, scannerRows])

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} />

  return (
    <div className="page-stack">
      <section className="hero-panel card">
        <div>
          <div className="hero-kicker">Apex overview</div>
          <h1>Investment terminal</h1>
          <p className="muted hero-copy">
            Scanner estrutural, carteira acompanhada por tese e leitura de risco com foco em investimento spot.
          </p>
        </div>
        <div className="hero-metrics">
          <div className="hero-metric">
            <span className="muted small">Average scanner score</span>
            <strong>{stats.avgScore.toFixed(1)}</strong>
            <ScoreBar value={stats.avgScore} />
          </div>
          <div className="hero-metric">
            <span className="muted small">Portfolio return</span>
            <strong>{stats.totalPnLPct.toFixed(2)}%</strong>
            <ScoreBar value={Math.max(0, Math.min(100, 50 + stats.totalPnLPct))} />
          </div>
        </div>
      </section>

      <div className="stats-grid">
        <StatCard label="Top signals" value={String(stats.topSignals)} hint="Candidatos atuais" />
        <StatCard label="Carteiras" value={String(stats.portfolioCount)} hint="Ativas" />
        <StatCard label="Valor atual" value={stats.totalValue.toFixed(2)} hint="Soma das posições" />
        <StatCard label="Retorno total" value={`${stats.totalPnLPct.toFixed(2)}%`} hint="Não realizado" />
      </div>

      <div className="two-col-grid emphasis-grid">
        <SectionCard title="Top opportunities">
          <ScannerTable rows={scannerRows.slice(0, 15)} />
        </SectionCard>

        <SectionCard title="Opportunity tape">
          <div className="signal-stack">
            {scannerRows.slice(0, 6).map((row) => (
              <div className="signal-card" key={row.ticker} onClick={() => navigate(`/asset/${row.ticker}`)} style={{cursor:'pointer'}}>
                <div className="list-item-row">
                  <div>
                    <div className="signal-ticker">{row.ticker}</div>
                    <div className="muted small">{row.asset_name}</div>
                  </div>
                  <StatusPill text={row.total_score.toFixed(1)} tone={row.total_score >= 80 ? 'good' : row.total_score >= 65 ? 'warn' : 'bad'} />
                </div>
                <div className="muted small">{String((Array.isArray(row.why_selected) ? row.why_selected[0] : Object.values(row.why_selected || {})[0]) || 'Sem detalhe.')}</div>
                <ScoreBar value={row.priority_score} />
              </div>
            ))}
            {!scannerRows.length ? <div className="muted">Sem sinais ativos.</div> : null}
          </div>
        </SectionCard>
      </div>

      {/* Early Signals — high-priority section */}
      {earlySignals.length > 0 && (
        <SectionCard title={`⚡ Early Signals (${earlySignals.length})`}>
          <div className="early-signals-grid">
            {earlySignals.map((es) => (
              <div
                className="early-signal-card"
                key={es.id}
                onClick={() => navigate(`/asset/${es.ticker}`)}
              >
                <div className="es-header">
                  <strong className="es-ticker">{es.ticker}</strong>
                  <span className="es-score">{es.signal_score.toFixed(0)}</span>
                </div>
                <div className="es-name">{es.name}</div>
                <div className="es-price-row">
                  <span>${es.current_price.toFixed(2)}</span>
                  <span className={`es-pct ${es.pct_move_since >= 0 ? 'pos' : 'neg'}`}>
                    {es.pct_move_since >= 0 ? '+' : ''}{es.pct_move_since.toFixed(1)}% desde detecção
                  </span>
                </div>
                <div className="es-criteria">
                  {es.criteria_passed.map((c) => (
                    <span key={c} className={`es-criterion es-criterion-${c}`}>
                      {c === 'fundamentals'  ? '💎 Fund' :
                       c === 'breakout'      ? '🚀 Breakout' :
                       c === 'regime_flip'   ? '🔄 Flip' :
                       c === 'momentum'      ? '📈 Mom' : c}
                    </span>
                  ))}
                </div>
                <div className="es-footer muted small">
                  Detectado {es.days_active}d · Score estrutural {es.total_score.toFixed(0)}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Insider Alerts — gestão a comprar */}
      {insiderAlerts.length > 0 && (
        <SectionCard title={`💼 Insider Alerts (${insiderAlerts.length})`}>
          <div className="insider-alerts-grid">
            {insiderAlerts.map((ia) => (
              <div
                className={`insider-alert-card signal-${ia.signal_type.toLowerCase()}`}
                key={ia.id}
                onClick={() => navigate(`/asset/${ia.ticker}`)}
              >
                <div className="es-header">
                  <strong className="es-ticker">{ia.ticker}</strong>
                  <span className={`ia-badge ia-${ia.signal_type.toLowerCase()}`}>
                    {ia.signal_type === 'CLUSTER_BUY'   ? '👥 CLUSTER' :
                     ia.signal_type === 'LARGE_BUY'     ? '💰 LARGE' :
                     '👔 EXEC'}
                  </span>
                </div>
                <div className="es-name">{ia.name}</div>
                <div className="ia-money">${(ia.dollar_amount/1000).toFixed(0)}k comprados</div>
                <div className="muted small">
                  {ia.num_insiders} insider{ia.num_insiders > 1 ? 's' : ''} · {ia.num_transactions} transacç{ia.num_transactions > 1 ? 'ões' : 'ão'}
                </div>
                <div className="es-footer muted small">
                  Score {ia.total_score.toFixed(0)} · maior trans. ${(ia.largest_single/1000).toFixed(0)}k
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      <div className="two-col-grid">
        <SectionCard title="Alertas recentes">
          <div className="list-stack">
            {alerts.slice(0, 6).map((alert) => (
              <div className="list-item terminal-list-item" key={alert.id}>
                <div className="list-item-row">
                  <strong>{alert.title}</strong>
                  <StatusPill text={alert.severity} tone={alert.severity === 'high' ? 'bad' : 'warn'} />
                </div>
                <div className="muted small">{alert.message}</div>
              </div>
            ))}
            {!alerts.length ? <div className="muted">Sem alertas.</div> : null}
          </div>
        </SectionCard>

        <SectionCard title="Resumo da carteira">
          <div className="signal-stack">
            {positions.slice(0, 6).map((position) => (
              <div className="signal-card" key={position.id}>
                <div className="list-item-row">
                  <div>
                    <div className="signal-ticker">{position.ticker}</div>
                    <div className="muted small">{position.asset_name}</div>
                  </div>
                  <StatusPill
                    text={position.pnl_pct !== undefined ? `${position.pnl_pct.toFixed(2)}%` : '—'}
                    tone={position.pnl_pct && position.pnl_pct >= 0 ? 'good' : 'bad'}
                  />
                </div>
                <div className="muted small">{position.thesis_status || 'sem estado'}</div>
                <ScoreBar value={position.total_score || 0} />
              </div>
            ))}
            {!positions.length ? <div className="muted">Sem posições.</div> : null}
          </div>
        </SectionCard>
      </div>
    </div>
  )
}
