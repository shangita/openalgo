import { webClient } from './client'

export interface DeltaNeutralLeg {
  symbol: string
  exchange: string
  option_type: 'CE' | 'PE'
  strike: number
  quantity: number
  average_price: number
  ltp: number
  days_to_expiry: number
  iv: number | null
  delta: number | null
  gamma: number | null
  theta: number | null
  vega: number | null
  net_delta: number | null
  net_gamma: number | null
  net_theta: number | null
  net_vega: number | null
  pnl: number
}

export interface HedgeLeg {
  symbol: string
  exchange: string
  type: 'FUT' | 'EQ' | 'HOLD'
  quantity: number
  average_price: number
  ltp: number
  pnl: number
}

export interface PortfolioGreeks {
  net_delta: number
  net_gamma: number
  net_theta: number
  net_vega: number
  net_premium: number
  total_pnl: number
}

export interface PayoffPoint {
  spot: number
  pnl: number
}

export interface DeltaNeutralData {
  underlying: string
  exchange: string
  expiry_date: string
  spot_price: number
  legs: DeltaNeutralLeg[]
  hedge_legs: HedgeLeg[]
  holding_legs: HedgeLeg[]
  portfolio: PortfolioGreeks
  payoff: PayoffPoint[]
  breakevens: number[]
  message?: string
}

export interface DeltaNeutralResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  spot_price?: number
  legs?: DeltaNeutralLeg[]
  hedge_legs?: HedgeLeg[]
  holding_legs?: HedgeLeg[]
  portfolio?: PortfolioGreeks
  payoff?: PayoffPoint[]
  breakevens?: number[]
}

export interface ExpiriesResponse {
  status: 'success' | 'error'
  expiries: string[]
}

export interface UnderlyingsResponse {
  status: 'success' | 'error'
  underlyings: string[]
}

export interface DnLogEntry {
  idx: number
  ts: string
  level: string
  src: string
  msg: string
}


// ── Strategy live-run types ────────────────────────────────────────────────

export interface DnStrategyState {
  strategy_id: string
  run_date: string
  hedge_lots: number
  entry_done: boolean
  ce_sym: string | null
  pe_sym: string | null
  futures_sym: string | null
  expiry: string | null
  atm_strike: number | null
  updated_at: string | null
}

export interface DnGreeksSnapshot {
  ts: string
  spot: number | null
  ce_ltp: number | null
  pe_ltp: number | null
  ce_iv: number | null
  pe_iv: number | null
  net_delta: number | null
  net_gamma: number | null
  net_theta: number | null
  net_vega: number | null
  pnl: number | null
  var_95: number | null
  cvar_95: number | null
  hedge_lots: number | null
}

export interface DnTradeEvent {
  ts: string
  event_type: 'ENTRY' | 'EXIT' | 'HEDGE' | 'STOP' | string
  symbol: string | null
  action: string | null
  quantity: number | null
  hedge_lots_after: number | null
  pnl: number | null
  reason: string | null
}

export const deltaNeutralApi = {
  getPortfolio: async (params: {
    underlying: string
    exchange: string
    expiry_date: string
  }): Promise<DeltaNeutralResponse> => {
    const response = await webClient.post<DeltaNeutralResponse>(
      '/deltaneutral/api/portfolio',
      params
    )
    return response.data
  },

  getExpiries: async (exchange: string, underlying: string): Promise<ExpiriesResponse> => {
    const response = await webClient.get<ExpiriesResponse>(
      `/search/api/expiries?exchange=${exchange}&underlying=${underlying}`
    )
    return response.data
  },

  getUnderlyings: async (exchange: string): Promise<UnderlyingsResponse> => {
    const response = await webClient.get<UnderlyingsResponse>(
      `/search/api/underlyings?exchange=${exchange}`
    )
    return response.data
  },

  getLogs: async (since = 0): Promise<{ ok: boolean; data?: { logs: DnLogEntry[]; seq: number } }> => {
    const response = await webClient.get(`/deltaneutral/logs?since=${since}`)
    return response.data
  },

  clearLogs: async (): Promise<{ ok: boolean }> => {
    const response = await webClient.post('/deltaneutral/logs/clear', {})
    return response.data
  },

  getStrategyState: async (): Promise<{ ok: boolean; data: DnStrategyState | null }> => {
    const r = await webClient.get('/deltaneutral/api/strategy/state')
    return r.data
  },

  getStrategyGreeks: async (limit = 120): Promise<{ ok: boolean; data: DnGreeksSnapshot[] }> => {
    const r = await webClient.get(`/deltaneutral/api/strategy/greeks?limit=${limit}`)
    return r.data
  },

  getStrategyTrades: async (): Promise<{ ok: boolean; data: DnTradeEvent[] }> => {
    const r = await webClient.get('/deltaneutral/api/strategy/trades')
    return r.data
  },

}