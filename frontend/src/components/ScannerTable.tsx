import type { ScannerResult } from '../types'
import { EmptyState } from './EmptyState'
import { ScoreBar } from './ScoreBar'
import { StatusPill } from './StatusPill'

interface ScannerTableProps {
  rows: ScannerResult[]
}

function toneForScore(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 80) return 'good'
  if (score >= 65) return 'warn'
  return 'bad'
}

export function ScannerTable({ rows }: ScannerTableProps) {
  if (!rows.length) {
    return <EmptyState message="Sem resultados para mostrar." />
  }

  return (
    <div className="table-wrapper terminal-table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Ticker</th>
            <th>Empresa</th>
            <th>Score</th>
            <th>Leitura</th>
            <th>Setor</th>
            <th>Motivo</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.scanner_type}-${row.ticker}`}>
              <td>{row.rank}</td>
              <td className="strong table-ticker">{row.ticker}</td>
              <td>{row.asset_name}</td>
              <td>
                <div className="score-cell">
                  <StatusPill text={row.total_score.toFixed(1)} tone={toneForScore(row.total_score)} />
                  <ScoreBar value={row.total_score} />
                </div>
              </td>
              <td>{row.state}</td>
              <td>{row.sector || '—'}</td>
              <td>{row.why_selected?.slice(0, 2).join(' · ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
