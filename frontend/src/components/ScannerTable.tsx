import { useNavigate } from 'react-router-dom'
import type { ScannerResult } from '../types'
import { EmptyState } from './EmptyState'
import { ScoreBar } from './ScoreBar'
import { StatusPill } from './StatusPill'

interface ScannerTableProps {
  rows: ScannerResult[]
  compact?: boolean
}

function toneForScore(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 70) return 'good'
  if (score >= 55) return 'warn'
  return 'bad'
}

function whyText(why: any): string {
  if (!why) return '—'
  if (Array.isArray(why)) return why.slice(0, 3).join(' · ') || '—'
  // why_selected is a dict with 'highlights' key
  if (why.highlights && Array.isArray(why.highlights)) {
    return why.highlights.slice(0, 3).join(' · ') || '—'
  }
  // fallback: flatten all values
  const vals = Object.values(why as Record<string, any>)
    .flatMap(v => Array.isArray(v) ? v : [String(v)])
    .filter(v => typeof v === 'string' && v.length > 2)
  return vals.slice(0, 2).join(' · ') || '—'
}

export function ScannerTable({ rows, compact = false }: ScannerTableProps) {
  const navigate = useNavigate()

  if (!rows.length) return <EmptyState message="Sem resultados para mostrar." />

  return (
    <div className="table-wrapper terminal-table-wrapper">
      <table>
        <thead>
          <tr>
            {!compact && <th>#</th>}
            <th>Ticker</th>
            <th>Empresa</th>
            <th>Score</th>
            {!compact && <th>Estado</th>}
            {!compact && <th>Setor</th>}
            <th>Razão principal</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={`${row.scanner_type}-${row.ticker}-${i}`}
              className="table-row-clickable"
              onClick={() => navigate(`/asset/${row.ticker}`)}
            >
              {!compact && <td className="muted">{row.rank}</td>}
              <td className="strong table-ticker">{row.ticker}</td>
              <td>{row.asset_name}</td>
              <td>
                <div className="score-cell">
                  <StatusPill text={row.total_score.toFixed(1)} tone={toneForScore(row.total_score)} />
                  <ScoreBar value={row.total_score} />
                </div>
              </td>
              {!compact && <td>{row.state}</td>}
              {!compact && <td>{row.sector || '—'}</td>}
              <td className="muted small">{whyText(row.why_selected)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
