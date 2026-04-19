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
