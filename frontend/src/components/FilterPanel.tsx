import { useEffect, useRef, useState } from 'react'
import type { ScannerResult } from '../types'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type MarketCapBucket = 'micro' | 'small' | 'mid' | 'large' | 'mega'

export interface FilterState {
  sectors: string[]           // empty = all
  marketCaps: MarketCapBucket[] // empty = all
  maxRisk: number             // 0-100, default 100
  minPercentile: number       // 0-100, default 0
  regimes: string[]           // empty = all
}

export const DEFAULT_FILTERS: FilterState = {
  sectors: [],
  marketCaps: [],
  maxRisk: 100,
  minPercentile: 0,
  regimes: [],
}

// ─────────────────────────────────────────────────────────────────────────────
// Market cap buckets
// ─────────────────────────────────────────────────────────────────────────────

const MARKET_CAP_BUCKETS: Array<{ key: MarketCapBucket; label: string; range: string; min: number; max: number }> = [
  { key: 'micro', label: 'Micro', range: '< $300M', min: 0, max: 300_000_000 },
  { key: 'small', label: 'Small', range: '$300M–2B', min: 300_000_000, max: 2_000_000_000 },
  { key: 'mid', label: 'Mid', range: '$2B–10B', min: 2_000_000_000, max: 10_000_000_000 },
  { key: 'large', label: 'Large', range: '$10B–200B', min: 10_000_000_000, max: 200_000_000_000 },
  { key: 'mega', label: 'Mega', range: '> $200B', min: 200_000_000_000, max: Infinity },
]

const REGIME_OPTIONS = [
  { key: 'STRONG_UPTREND', label: 'Strong ↑', color: '#22c55e' },
  { key: 'UPTREND', label: 'Uptrend', color: '#6ec1ff' },
  { key: 'TOPPING', label: 'Topping', color: '#ff9f1a' },
  { key: 'RANGING', label: 'Ranging', color: '#8ea0bb' },
  { key: 'DOWNTREND', label: 'Downtrend', color: '#f87171' },
  { key: 'BASING', label: 'Basing', color: '#a78bfa' },
]

// ─────────────────────────────────────────────────────────────────────────────
// Filter logic
// ─────────────────────────────────────────────────────────────────────────────

export function applyFilters(rows: ScannerResult[], filters: FilterState): ScannerResult[] {
  return rows.filter((row) => {
    // Sector
    if (filters.sectors.length > 0 && !filters.sectors.includes(row.sector || '')) return false

    // Market cap
    if (filters.marketCaps.length > 0) {
      const cap = row.market_cap ?? 0
      const inBucket = filters.marketCaps.some((key) => {
        const bucket = MARKET_CAP_BUCKETS.find((b) => b.key === key)
        return bucket && cap >= bucket.min && cap < bucket.max
      })
      if (!inBucket) return false
    }

    // Max risk
    if (filters.maxRisk < 100) {
      const risk = row.risk_score ?? 50
      if (risk > filters.maxRisk) return false
    }

    // Min percentile
    if (filters.minPercentile > 0) {
      const pct = row.score_percentile ?? 50
      if (pct < filters.minPercentile) return false
    }

    // Regime
    if (filters.regimes.length > 0 && !filters.regimes.includes(row.score_regime || '')) return false

    return true
  })
}

export function countActiveFilters(filters: FilterState): number {
  let count = 0
  if (filters.sectors.length > 0) count++
  if (filters.marketCaps.length > 0) count++
  if (filters.maxRisk < 100) count++
  if (filters.minPercentile > 0) count++
  if (filters.regimes.length > 0) count++
  return count
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function FilterSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="filter-section">
      <div className="filter-section-title">{title}</div>
      {children}
    </div>
  )
}

function PillToggle({
  active,
  onClick,
  children,
  accent,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  accent?: string
}) {
  return (
    <button
      className={`filter-pill ${active ? 'filter-pill-active' : ''}`}
      style={active && accent ? { borderColor: accent, color: accent, background: `${accent}18` } : undefined}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  )
}

function RangeSlider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  formatValue,
  invert = false,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  formatValue: (v: number) => string
  invert?: boolean
}) {
  const pct = invert
    ? ((max - value) / (max - min)) * 100
    : ((value - min) / (max - min)) * 100

  return (
    <div className="filter-range">
      <div className="filter-range-header">
        <span>{label}</span>
        <span className="filter-range-value">{formatValue(value)}</span>
      </div>
      <div className="filter-range-track">
        <div
          className="filter-range-fill"
          style={{ width: `${pct}%`, ...(invert ? { left: `${100 - pct}%`, width: `${pct}%` } : {}) }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main FilterPanel
// ─────────────────────────────────────────────────────────────────────────────

interface FilterPanelProps {
  filters: FilterState
  onChange: (f: FilterState) => void
  availableSectors: string[]
  resultCount: number
  totalCount: number
}

export function FilterPanel({ filters, onChange, availableSectors, resultCount, totalCount }: FilterPanelProps) {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const activeCount = countActiveFilters(filters)

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  function toggleSector(s: string) {
    const next = filters.sectors.includes(s)
      ? filters.sectors.filter((x) => x !== s)
      : [...filters.sectors, s]
    onChange({ ...filters, sectors: next })
  }

  function toggleMarketCap(k: MarketCapBucket) {
    const next = filters.marketCaps.includes(k)
      ? filters.marketCaps.filter((x) => x !== k)
      : [...filters.marketCaps, k]
    onChange({ ...filters, marketCaps: next })
  }

  function toggleRegime(r: string) {
    const next = filters.regimes.includes(r)
      ? filters.regimes.filter((x) => x !== r)
      : [...filters.regimes, r]
    onChange({ ...filters, regimes: next })
  }

  function reset() {
    onChange(DEFAULT_FILTERS)
  }

  return (
    <div className="filter-root" ref={panelRef}>
      {/* Toggle button */}
      <button className="filter-toggle" onClick={() => setOpen((v) => !v)} type="button">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M2 4h12M4 8h8M6 12h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        Filtros
        {activeCount > 0 && <span className="filter-badge">{activeCount}</span>}
      </button>

      {/* Results counter */}
      <span className="filter-counter">
        {resultCount === totalCount ? (
          <span className="muted small">{totalCount} resultados</span>
        ) : (
          <>
            <span className="filter-counter-active">{resultCount}</span>
            <span className="muted small"> / {totalCount}</span>
          </>
        )}
      </span>

      {/* Dropdown panel */}
      {open && (
        <div className="filter-panel card">
          <div className="filter-panel-header">
            <span className="filter-panel-title">Filtros avançados</span>
            {activeCount > 0 && (
              <button className="filter-reset" onClick={reset} type="button">
                Limpar {activeCount}
              </button>
            )}
          </div>

          {/* ── Sector ─────────────────────────────────────────────────── */}
          <FilterSection title="Setor">
            <div className="filter-pill-grid">
              {availableSectors.length === 0 ? (
                <span className="muted small">A carregar setores…</span>
              ) : (
                availableSectors.map((s) => (
                  <PillToggle
                    key={s}
                    active={filters.sectors.includes(s)}
                    onClick={() => toggleSector(s)}
                  >
                    {s}
                  </PillToggle>
                ))
              )}
            </div>
          </FilterSection>

          {/* ── Market cap ─────────────────────────────────────────────── */}
          <FilterSection title="Market cap">
            <div className="filter-pill-row">
              {MARKET_CAP_BUCKETS.map((b) => (
                <PillToggle
                  key={b.key}
                  active={filters.marketCaps.includes(b.key)}
                  onClick={() => toggleMarketCap(b.key)}
                >
                  <span>{b.label}</span>
                  <span className="filter-pill-sub">{b.range}</span>
                </PillToggle>
              ))}
            </div>
          </FilterSection>

          {/* ── Risk ───────────────────────────────────────────────────── */}
          <FilterSection title="Risco máximo">
            <RangeSlider
              label="Risk score ≤"
              value={filters.maxRisk}
              min={20}
              max={100}
              step={5}
              onChange={(v) => onChange({ ...filters, maxRisk: v })}
              formatValue={(v) => (v === 100 ? 'sem limite' : `≤ ${v}`)}
              invert
            />
          </FilterSection>

          {/* ── Percentile ─────────────────────────────────────────────── */}
          <FilterSection title="Percentil mínimo">
            <RangeSlider
              label="Score percentil ≥"
              value={filters.minPercentile}
              min={0}
              max={90}
              step={5}
              onChange={(v) => onChange({ ...filters, minPercentile: v })}
              formatValue={(v) => (v === 0 ? 'sem mínimo' : `≥ p${v}`)}
            />
          </FilterSection>

          {/* ── Regime ─────────────────────────────────────────────────── */}
          <FilterSection title="Regime de score">
            <div className="filter-pill-row filter-pill-wrap">
              {REGIME_OPTIONS.map((r) => (
                <PillToggle
                  key={r.key}
                  active={filters.regimes.includes(r.key)}
                  onClick={() => toggleRegime(r.key)}
                  accent={r.color}
                >
                  {r.label}
                </PillToggle>
              ))}
            </div>
          </FilterSection>
        </div>
      )}
    </div>
  )
}
