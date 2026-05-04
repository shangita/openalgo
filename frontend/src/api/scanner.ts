import { webClient } from './client'

export interface ScanSignal {
  signal_id: string
  symbol: string
  setup_id: 'A' | 'B'
  direction: 'LONG' | 'SHORT'
  ltp: number
  ema5: number
  rsi14: number
  target: number
  slope_pct: number
  distance_pct: number
  signal_time: string
  exchange?: string
}

export interface PaperPosition {
  position_id: string
  symbol: string
  setup_id: 'A' | 'B'
  direction: 'LONG' | 'SHORT'
  entry: number
  qty: number
  trailing_sl: number
  target: number
  status: 'OPEN' | 'CLOSED' | 'DATA_STALLED'
  pnl: number | null
  exit_reason: string | null
  opened_at: string
  closed_at: string | null
}

export interface PaperSummary {
  total_pnl: number
  win_rate: number
  setup_a_pnl: number
  setup_b_pnl: number
  total_trades: number
}

export interface ChartCandle {
  time: number
  open: number
  high: number
  low: number
  close: number
}

export interface ChartData {
  daily_candles: ChartCandle[]
  ema5_line: { time: number; value: number }[]
  intra_candles?: ChartCandle[]
  pdh?: number
  pdl?: number
}

export interface LogEntry {
  idx: number
  ts: string
  level: string
  src: string
  msg: string
}

export interface SchedulerStatus {
  running: boolean
  paused: boolean
  last_scan_a: string | null
  last_scan_b: string | null
  last_breakout_check: string | null
  next_run: string | null
}

export const scannerApi = {
  runOnce: async (): Promise<{ ok: boolean; data?: ScanSignal[]; error?: string }> => {
    const response = await webClient.post('/scanner/run-once', {})
    return response.data
  },

  startContinuous: async (): Promise<{ ok: boolean; error?: string }> => {
    const response = await webClient.post('/scanner/continuous/start', {})
    return response.data
  },

  stopContinuous: async (): Promise<{ ok: boolean; error?: string }> => {
    const response = await webClient.post('/scanner/continuous/stop', {})
    return response.data
  },

  getStatus: async (): Promise<{ ok: boolean; data?: SchedulerStatus }> => {
    const response = await webClient.get('/scanner/continuous/status')
    return response.data
  },

  getSignals: async (setup?: string, direction?: string): Promise<{ ok: boolean; data?: ScanSignal[] }> => {
    const params = new URLSearchParams()
    if (setup) params.set('setup', setup)
    if (direction) params.set('direction', direction)
    const qs = params.toString()
    const response = await webClient.get(`/scanner/signals${qs ? `?${qs}` : ''}`)
    return response.data
  },

  getPaperStatus: async (): Promise<{
    ok: boolean
    data?: { open: PaperPosition[]; closed: PaperPosition[]; summary: PaperSummary }
    error?: string
  }> => {
    const response = await webClient.get('/scanner/paper/status')
    return response.data
  },

  startPaperTrade: async (signalIds: string[]): Promise<{ ok: boolean; error?: string }> => {
    const response = await webClient.post('/scanner/paper/start', { signals: signalIds })
    return response.data
  },

  closePaperPosition: async (positionId: string): Promise<{ ok: boolean; error?: string }> => {
    const response = await webClient.post('/scanner/paper/stop', { position_id: positionId })
    return response.data
  },

  testTelegram: async (): Promise<{ ok: boolean; error?: string }> => {
    const response = await webClient.post('/scanner/telegram/test', {})
    return response.data
  },

  getLogs: async (since = 0): Promise<{ ok: boolean; data?: { logs: LogEntry[]; seq: number } }> => {
    const response = await webClient.get(`/scanner/logs?since=${since}`)
    return response.data
  },

  clearLogs: async (): Promise<{ ok: boolean }> => {
    const response = await webClient.post('/scanner/logs/clear', {})
    return response.data
  },

  getChartData: async (
    symbol: string,
    exchange: string,
    setup: string,
  ): Promise<{ ok: boolean; data?: ChartData; error?: string }> => {
    const response = await webClient.get(
      `/scanner/chart-data?symbol=${symbol}&exchange=${encodeURIComponent(exchange)}&setup=${setup}`,
    )
    return response.data
  },
}
