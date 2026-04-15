import { Link } from 'react-router-dom'
import type { Position } from '../types'
import { EmptyState } from './EmptyState'
import { ScoreBar } from './ScoreBar'
import { StatusPill } from './StatusPill'

interface PortfolioTableProps {
  rows: Position[]
}

function formatPct(value?: number) {
  if (value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(2)}%`
}

function tone(value?: number): 'good' | 'warn' | 'bad' | 'neutral' {
  if (value === undefined || Number.isNaN(value)) return 'neutral'
  if (value > 0) return 'good'
  if (value < 0) return 'bad'
  return 'neutral'
}

export function PortfolioTable({ rows }: PortfolioTableProps) {
  if (!rows.length) return <EmptyState message="Sem posições registadas." />

  return (
    <div className="table-wrapper terminal-table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Empresa</th>
            <th>Custo médio</th>
            <th>Preço atual</th>
            <th>Retorno</th>
            <th>Score</th>
            <th>Tese</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="strong table-ticker">
                <Link to={`/positions/${row.id}`}>{row.ticker}</Link>
              </td>
              <td>{row.asset_name}</td>
              <td>{row.avg_cost.toFixed(2)}</td>
              <td>{row.current_price?.toFixed(2) ?? '—'}</td>
              <td>
                <StatusPill text={formatPct(row.pnl_pct)} tone={tone(row.pnl_pct)} />
              </td>
              <td>
                <div className="score-cell compact-score-cell">
                  <span>{row.total_score?.toFixed(1) ?? '—'}</span>
                  {row.total_score !== undefined ? <ScoreBar value={row.total_score} /> : null}
                </div>
              </td>
              <td>{row.thesis_status || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
