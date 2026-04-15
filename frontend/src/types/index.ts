export type ScannerType =
  | 'structural_growth'
  | 'repricing'
  | 'quality_compounders'
  | 'narrative_plays'
  | 'speculative_asymmetry'

export interface ScannerResult {
  ticker: string
  asset_name: string
  scanner_type: ScannerType
  rank: number
  total_score: number
  priority_score: number
  state: string
  sector?: string
  why_selected: string[]
  // Enriched fields for filtering
  risk_score?: number
  score_percentile?: number
  score_regime?: string
  market_cap?: number
}

export interface Portfolio {
  id: string
  name: string
  base_currency: string
  total_invested?: number
  total_value?: number
  total_pnl?: number
  total_pnl_pct?: number
}

export interface Position {
  id: string
  portfolio_id: string
  ticker: string
  asset_name: string
  first_buy_date: string
  avg_cost: number
  quantity: number
  invested_amount: number
  current_price?: number
  current_value?: number
  pnl?: number
  pnl_pct?: number
  position_type: string
  horizon: string
  thesis: string
  thesis_status?: string
  total_score?: number
}

export interface PositionHistoryPoint {
  date: string
  market_value: number
  pnl_pct: number
  score_total: number
}

export interface PositionScenario {
  as_of_date: string
  bear_low: number
  bear_high: number
  base_low: number
  base_high: number
  bull_low: number
  bull_high: number
  bear_probability?: number
  base_probability?: number
  bull_probability?: number
}

export interface AlertItem {
  id: string
  alert_type: string
  severity: string
  title: string
  message: string
  is_read: boolean
  created_at: string
}
