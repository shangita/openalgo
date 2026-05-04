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
}
