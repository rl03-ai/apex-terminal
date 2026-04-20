import { api } from './client'
import type {
  AlertItem,
  Portfolio,
  Position,
  PositionHistoryPoint,
  PositionScenario,
  ScannerResult,
  ScannerType,
} from '../types'

export async function fetchScannerResults(scannerType: ScannerType, minScore = 0) {
  return api.get<ScannerResult[]>(`/scanner/results?scanner_type=${scannerType}&min_score=${minScore}`)
}

export async function fetchTopOpportunities() {
  return api.get<ScannerResult[]>('/scanner/top-opportunities')
}

export async function fetchPortfolios() {
  return api.get<Portfolio[]>('/portfolios')
}

export async function fetchPortfolioPositions(portfolioId: string) {
  return api.get<Position[]>(`/portfolios/${portfolioId}/positions`)
}

export async function fetchPosition(positionId: string) {
  return api.get<Position>(`/positions/${positionId}`)
}

export async function fetchPositionHistory(positionId: string) {
  return api.get<PositionHistoryPoint[]>(`/positions/${positionId}/history`)
}

export async function fetchPositionScenarios(positionId: string) {
  return api.get<PositionScenario[]>(`/positions/${positionId}/scenarios`)
}

export async function fetchAlerts() {
  return api.get<AlertItem[]>('/alerts')
}

export async function fetchScannerSectors() {
  return api.get<string[]>('/scanner/sectors')
}

export interface EarlySignalItem {
  id: string
  ticker: string
  name: string
  sector?: string
  first_detected_date: string
  first_detected_price: number
  current_price: number
  pct_move_since: number
  signal_score: number
  total_score: number
  criteria_passed: string[]
  days_active: number
}

export async function fetchEarlySignals(limit = 10) {
  return api.get<EarlySignalItem[]>(`/early-signals?limit=${limit}`)
}
