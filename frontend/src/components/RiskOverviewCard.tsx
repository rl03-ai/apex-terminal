import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface RiskAlert {
  severity: 'critical' | 'warning'
  category: 'concentration' | 'diversification' | 'position'
  message: string
}

interface SectorBreakdown {
  sector: string
  value: number
  weight_pct: number
}

interface RiskOverview {
  total_value: number
  total_invested: number
  position_count: number
  alerts: RiskAlert[]
  concentration: {
    top_ticker: { ticker: string; weight_pct: number } | null
    top_sector: { name: string; weight_pct: number } | null
    sector_breakdown: SectorBreakdown[]
  }
  position_risks: any[]
  diversification: { status: string; count: number; target: string }
  summary: { red_count: number; yellow_count: number; green_count: number }
}

interface Props {
  portfolioId: string
  refreshKey?: number
}

export function RiskOverviewCard({ portfolioId, refreshKey }: Props) {
  const [risk, setRisk] = useState<RiskOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setLoading(true)
        const data = await api.get<RiskOverview>(`/portfolios/${portfolioId}/risk`)
        if (!cancelled) setRisk(data)
      } catch {
        /* silently skip */
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [portfolioId, refreshKey])

  if (loading || !risk || risk.position_count === 0) return null

  const s = risk.summary
  const topT = risk.concentration.top_ticker
  const topS = risk.concentration.top_sector
  const div = risk.diversification

  const hasAlerts = risk.alerts.length > 0
  const criticalCount = risk.alerts.filter(a => a.severity === 'critical').length
  const warningCount  = risk.alerts.filter(a => a.severity === 'warning').length

  // Overall portfolio risk banner color
  const bannerLevel =
    criticalCount > 0 ? 'critical' :
    warningCount  > 0 ? 'warning' : 'healthy'

  return (
    <div className={`risk-card risk-banner-${bannerLevel}`}>
      <div className="risk-header" onClick={() => setExpanded(!expanded)}>
        <div>
          <div className="risk-title">
            🛡️ Risk Overview
          </div>
          <div className="risk-subtitle">
            {bannerLevel === 'critical' ? `${criticalCount} alerta${criticalCount>1?'s':''} crítico${criticalCount>1?'s':''}` :
             bannerLevel === 'warning'  ? `${warningCount} aviso${warningCount>1?'s':''}` :
             'Carteira saudável'}
          </div>
        </div>
        <div className="risk-health-pills">
          <span className="health-pill health-green">🟢 {s.green_count}</span>
          {s.yellow_count > 0 && <span className="health-pill health-yellow">🟡 {s.yellow_count}</span>}
          {s.red_count    > 0 && <span className="health-pill health-red">🔴 {s.red_count}</span>}
          <span className="risk-chevron">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {expanded && (
        <div className="risk-body">
          {/* Key metrics row */}
          <div className="risk-metrics">
            <div className="risk-metric">
              <div className="risk-metric-label">Top Ticker</div>
              <div className="risk-metric-value">
                {topT ? `${topT.ticker} ${topT.weight_pct.toFixed(0)}%` : '—'}
              </div>
            </div>
            <div className="risk-metric">
              <div className="risk-metric-label">Top Sector</div>
              <div className="risk-metric-value">
                {topS ? `${topS.name} ${topS.weight_pct.toFixed(0)}%` : '—'}
              </div>
            </div>
            <div className="risk-metric">
              <div className="risk-metric-label">Diversificação</div>
              <div className="risk-metric-value">
                {div.count} pos. ({div.status})
              </div>
            </div>
          </div>

          {/* Alerts */}
          {hasAlerts && (
            <div className="risk-alerts-list">
              {risk.alerts.map((a, i) => (
                <div key={i} className={`risk-alert risk-alert-${a.severity}`}>
                  <span className="risk-alert-icon">
                    {a.severity === 'critical' ? '🚨' : '⚠️'}
                  </span>
                  <span>{a.message}</span>
                </div>
              ))}
            </div>
          )}

          {/* Sector breakdown */}
          {risk.concentration.sector_breakdown.length > 1 && (
            <div className="sector-breakdown">
              <div className="sector-breakdown-title">Exposição por setor</div>
              <div className="sector-bars">
                {risk.concentration.sector_breakdown.map((s, i) => (
                  <div key={i} className="sector-bar-row">
                    <span className="sector-name">{s.sector}</span>
                    <div className="sector-bar-wrap">
                      <div
                        className="sector-bar"
                        style={{ width: `${Math.min(100, s.weight_pct)}%` }}
                      />
                    </div>
                    <span className="sector-pct">{s.weight_pct.toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
