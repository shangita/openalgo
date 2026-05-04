import axios from 'axios'

export interface BtStrategy {
  id: string
  label: string
  description: string
  default_params: Record<string, number>
}

export interface BtScorecard {
  sharpe: number | null
  calmar: number | null
  max_drawdown: number | null
  total_return: number | null
  n_trades: number
  win_rate: number | null
  profit_factor: number | null
  t_stat: number | null
  wfa_profitable_windows: number
  wfa_n_windows: number
  oos_is_sharpe_ratio: number | null
  mc_ruin_prob: number | null
  param_sensitivity: number | null
  sharpe_2x_slip: number | null
  n_profitable_regimes: number
  pass_count: number
  verdict: 'PASS' | 'FAIL'
  checks: {
    oos_is_sharpe_ratio: boolean
    min_trades: boolean
    profit_factor: boolean
    calmar: boolean
    wfa_profitable_windows: boolean
    param_sensitivity: boolean
    mc_ruin_prob: boolean
    t_stat: boolean
    profitable_regimes: boolean
    sharpe_2x_slip: boolean
  }
}

export interface BtEquityPt {
  t: number
  v: number
}

export interface WfaWindow {
  window: string
  is_sharpe: number
  oos_sharpe: number
  oos_return: number
  oos_trades: number
  profitable: boolean
}

export interface BtTrade {
  entry_time: string
  exit_time: string
  direction: string
  entry_price: number
  exit_price: number
  pnl: number
  return_pct: number
}

export interface BtResult {
  scorecard: BtScorecard
  equity_curve: BtEquityPt[]
  trades: BtTrade[]
  wfa_windows: WfaWindow[]
  total_bars: number
  symbol: string
  exchange: string
  interval: string
  strategy: string
}

export interface BtJob {
  job_id: string
  status: 'queued' | 'running' | 'done' | 'error'
  symbol: string
  exchange: string
  interval: string
  start_date: string
  end_date: string
  strategy: string
  params: Record<string, number>
  result: BtResult | null
  error: string | null
  submitted_at: string
  finished_at: string | null
}

export interface BtLogEntry {
  seq: number
  level: string
  msg: string
}

const api = axios.create({ withCredentials: true })

const wrap = async <T>(p: Promise<{ data: { ok: boolean; data: T; error: string | null } }>): Promise<T> => {
  const res = await p
  if (!res.data.ok) throw new Error(res.data.error ?? 'Unknown error')
  return res.data.data
}

export const btApi = {
  strategies: () => wrap<BtStrategy[]>(api.get('/backtest/api/strategies')),

  run: (payload: {
    symbol: string
    exchange: string
    interval: string
    start_date: string
    end_date: string
    strategy: string
    params: Record<string, number>
  }) => wrap<{ job_id: string }>(api.post('/backtest/api/run', payload)),

  status: (jobId: string) => wrap<BtJob>(api.get(`/backtest/api/status/${jobId}`)),

  logs: (since: number) =>
    wrap<{ logs: BtLogEntry[]; seq: number }>(
      api.get('/backtest/logs', { params: { since } })
    ),

  clearLogs: () => wrap<{ message: string }>(api.post('/backtest/logs/clear')),
}
