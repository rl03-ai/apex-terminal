import { useEffect, useMemo, useState } from 'react'
import { fetchScannerResults, fetchScannerSectors } from '../api/endpoints'
import { FilterPanel, DEFAULT_FILTERS, applyFilters, countActiveFilters } from '../components/FilterPanel'
import type { FilterState } from '../components/FilterPanel'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { ScannerTable } from '../components/ScannerTable'
import { ScoreBar } from '../components/ScoreBar'
import { SectionCard } from '../components/SectionCard'
import type { ScannerResult, ScannerType } from '../types'

const scannerOptions: Array<{ value: ScannerType; label: string; description: string }> = [
  { value: 'repricing',          label: 'Repricing',           description: 'Melhoria operacional + reconhecimento gradual do mercado.' },
  { value: 'early_growth',       label: 'Early Growth',        description: 'Crescimento em fase inicial com qualidade em formação.' },
  { value: 'quality_compounder', label: 'Quality Compounder',  description: 'Qualidade, consistência e menor ruído.' },
  { value: 'narrative',          label: 'Narrative',           description: 'Narrativa forte com catalisadores relevantes.' },
  { value: 'speculative',        label: 'Speculative',         description: 'Upside alto, risco maior e sizing controlado.' },
]

export function ScannerPage() {
  const [scannerType, setScannerType] = useState<ScannerType>('repricing')
  const [rows, setRows] = useState<ScannerResult[]>([])
  const [sectors, setSectors] = useState<string[]>([])
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load scanner results
  useEffect(() => {
    async function load() {
      try {
        setLoading(true)
        const data = await fetchScannerResults(scannerType)
        setRows(data)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Erro inesperado.')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [scannerType])

  // Load sectors once
  useEffect(() => {
    fetchScannerSectors()
      .then(setSectors)
      .catch(() => {
        // Derive from current rows if API not available
        const fromRows = Array.from(new Set(rows.map((r) => r.sector).filter(Boolean) as string[])).sort()
        setSectors(fromRows)
      })
  }, [])

  // Derive sectors from rows as fallback
  const availableSectors = useMemo(() => {
    if (sectors.length > 0) return sectors
    return Array.from(new Set(rows.map((r) => r.sector).filter(Boolean) as string[])).sort()
  }, [sectors, rows])

  // Apply filters
  const filteredRows = useMemo(() => applyFilters(rows, filters), [rows, filters])

  const currentOption = scannerOptions.find((o) => o.value === scannerType)
  const avgScore = filteredRows.length
    ? filteredRows.reduce((s, r) => s + r.total_score, 0) / filteredRows.length
    : 0

  const activeFilterCount = countActiveFilters(filters)

  return (
    <div className="page-stack">
      <section className="page-banner card">
        <div>
          <h1>Scanner</h1>
          <p className="muted">Pesquisa de oportunidades por perfil de investimento.</p>
        </div>
        <div className="toolbar-row">
          <select value={scannerType} onChange={(e) => setScannerType(e.target.value as ScannerType)}>
            {scannerOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <FilterPanel
            filters={filters}
            onChange={setFilters}
            availableSectors={availableSectors}
            resultCount={filteredRows.length}
            totalCount={rows.length}
          />
        </div>
      </section>

      <div className="stats-grid scanner-mini-grid">
        <StatChip label="Perfil" value={currentOption?.label || '—'} />
        <StatChip label="Leitura" value={currentOption?.description || '—'} />
        <StatChip
          label="Resultados"
          value={activeFilterCount > 0 ? `${filteredRows.length} / ${rows.length}` : String(rows.length)}
        />
        <StatChip label="Score médio" value={avgScore.toFixed(1)} withBar scoreValue={avgScore} />
      </div>

      {/* Active filter summary chips */}
      {activeFilterCount > 0 && (
        <div className="active-filters-row">
          {filters.sectors.map((s) => (
            <ActiveFilterChip
              key={`s-${s}`}
              label={s}
              onRemove={() => setFilters({ ...filters, sectors: filters.sectors.filter((x) => x !== s) })}
            />
          ))}
          {filters.marketCaps.map((k) => (
            <ActiveFilterChip
              key={`m-${k}`}
              label={`Cap: ${k}`}
              onRemove={() => setFilters({ ...filters, marketCaps: filters.marketCaps.filter((x) => x !== k) })}
            />
          ))}
          {filters.maxRisk < 100 && (
            <ActiveFilterChip
              label={`Risco ≤ ${filters.maxRisk}`}
              onRemove={() => setFilters({ ...filters, maxRisk: 100 })}
            />
          )}
          {filters.minPercentile > 0 && (
            <ActiveFilterChip
              label={`Percentil ≥ p${filters.minPercentile}`}
              onRemove={() => setFilters({ ...filters, minPercentile: 0 })}
            />
          )}
          {filters.regimes.map((r) => (
            <ActiveFilterChip
              key={`r-${r}`}
              label={r.replace('_', ' ')}
              onRemove={() => setFilters({ ...filters, regimes: filters.regimes.filter((x) => x !== r) })}
            />
          ))}
        </div>
      )}

      <SectionCard title="Resultados">
        {loading ? (
          <LoadingState />
        ) : error ? (
          <ErrorState message={error} />
        ) : (
          <ScannerTable rows={filteredRows} />
        )}
      </SectionCard>
    </div>
  )
}

function StatChip({
  label,
  value,
  withBar = false,
  scoreValue = 0,
}: {
  label: string
  value: string
  withBar?: boolean
  scoreValue?: number
}) {
  return (
    <div className="card stat-chip">
      <div className="muted small">{label}</div>
      <div className="stat-chip-value">{value}</div>
      {withBar ? <ScoreBar value={scoreValue} /> : null}
    </div>
  )
}

function ActiveFilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="active-filter-chip">
      {label}
      <button onClick={onRemove} type="button" aria-label="Remover filtro">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </span>
  )
}
