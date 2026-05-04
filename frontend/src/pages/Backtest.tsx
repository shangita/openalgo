import { useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Play, RotateCcw, XCircle } from 'lucide-react'
import {
  btApi,
  type BtJob,
  type BtLogEntry,
  type BtStrategy,
  type WfaWindow,
  type BtTrade,
  type BtEquityPt,
  type BtScorecard,
} from '@/api/backtest'

// ─── Equity SVG chart ─────────────────────────────────────────────────────────

function EquityChart({ pts }: { pts: BtEquityPt[] }) {
  if (!pts.length) return null
  const W = 700, H = 160, PAD = { t: 10, r: 10, b: 24, l: 56 }
  const iW = W - PAD.l - PAD.r
  const iH = H - PAD.t - PAD.b
  const vals = pts.map(p => p.v)
  const minV = Math.min(...vals)
  const maxV = Math.max(...vals)
  const range = maxV - minV || 1
  const scaleX = (i: number) => PAD.l + (i / (pts.length - 1)) * iW
  const scaleY = (v: number) => PAD.t + iH - ((v - minV) / range) * iH
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${scaleX(i).toFixed(1)},${scaleY(p.v).toFixed(1)}`).join(' ')
  const fill = `${path} L${scaleX(pts.length - 1).toFixed(1)},${(PAD.t + iH).toFixed(1)} L${PAD.l.toFixed(1)},${(PAD.t + iH).toFixed(1)} Z`
  const lastV = vals[vals.length - 1]
  const pct = ((lastV - vals[0]) / vals[0]) * 100
  const color = pct >= 0 ? '#22c55e' : '#ef4444'

  const yTicks = [minV, (minV + maxV) / 2, maxV]
  const xTicks = [0, Math.floor(pts.length / 2), pts.length - 1]

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 200 }}>
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {yTicks.map((v, i) => {
          const y = scaleY(v)
          return (
            <g key={i}>
              <line x1={PAD.l} y1={y} x2={W - PAD.r} y2={y} stroke="#334155" strokeWidth="0.5" strokeDasharray="4 3" />
              <text x={PAD.l - 4} y={y + 4} textAnchor="end" fill="#64748b" fontSize="9">
                {v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toFixed(0)}
              </text>
            </g>
          )
        })}
        {xTicks.map((i) => {
          const x = scaleX(i)
          const d = new Date(pts[i].t).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
          return (
            <text key={i} x={x} y={H - 4} textAnchor="middle" fill="#64748b" fontSize="9">{d}</text>
          )
        })}
        <path d={fill} fill="url(#eqGrad)" />
        <path d={path} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
    </div>
  )
}

// ─── Scorecard card ───────────────────────────────────────────────────────────

const CRITERIA_META: Record<string, { label: string; fmt: (v: number | null) => string; threshold: string }> = {
  oos_is_sharpe_ratio:   { label: 'OOS/IS Sharpe',        fmt: v => v != null ? v.toFixed(2) : '—', threshold: '> 0.50' },
  min_trades:            { label: 'Total Trades',          fmt: v => v != null ? String(Math.round(v)) : '—', threshold: '≥ 200' },
  profit_factor:         { label: 'Profit Factor',         fmt: v => v != null ? v.toFixed(2) : '—', threshold: '> 1.30' },
  calmar:                { label: 'Calmar Ratio',          fmt: v => v != null ? v.toFixed(2) : '—', threshold: '> 0.50' },
  wfa_profitable_windows:{ label: 'WFA Profitable Windows', fmt: v => v != null ? String(Math.round(v)) : '—', threshold: '≥ 6/8' },
  param_sensitivity:     { label: 'Param Sensitivity σ/μ', fmt: v => v != null ? v.toFixed(2) : '—', threshold: '< 0.30' },
  mc_ruin_prob:          { label: 'MC Ruin Probability',   fmt: v => v != null ? `${(v * 100).toFixed(1)}%` : '—', threshold: '< 5%' },
  t_stat:                { label: 'T-Statistic',           fmt: v => v != null ? v.toFixed(2) : '—', threshold: '> 2.0' },
  profitable_regimes:    { label: 'Profitable Regimes',    fmt: v => v != null ? String(Math.round(v)) : '—', threshold: '≥ 3' },
  sharpe_2x_slip:        { label: 'Sharpe @ 2× Slippage',  fmt: v => v != null ? v.toFixed(2) : '—', threshold: '> 1.0' },
}

const METRIC_DISPLAY: Array<{ key: keyof BtScorecard; label: string; fmt: (v: number | null) => string }> = [
  { key: 'total_return', label: 'Total Return', fmt: v => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'sharpe',       label: 'Sharpe',       fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'max_drawdown', label: 'Max Drawdown', fmt: v => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'calmar',       label: 'Calmar',       fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'n_trades',     label: 'Trades',       fmt: v => v != null ? String(Math.round(v)) : '—' },
  { key: 'win_rate',     label: 'Win Rate',     fmt: v => v != null ? `${v.toFixed(1)}%` : '—' },
  { key: 'profit_factor',label: 'Profit Factor',fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 't_stat',       label: 'T-Stat',       fmt: v => v != null ? v.toFixed(2) : '—' },
]

function ScorecardPanel({ sc }: { sc: BtScorecard }) {
  const verdictColor = sc.verdict === 'PASS' ? 'text-green-400' : 'text-red-400'
  const verdictBg    = sc.verdict === 'PASS' ? 'bg-green-950/40 border-green-800' : 'bg-red-950/40 border-red-800'

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <div className={`flex items-center justify-between rounded-lg border p-4 ${verdictBg}`}>
        <div>
          <div className={`text-2xl font-bold ${verdictColor}`}>{sc.verdict}</div>
          <div className="text-sm text-slate-400">{sc.pass_count} / 10 criteria passed</div>
        </div>
        <div className="grid grid-cols-4 gap-3">
          {METRIC_DISPLAY.map(m => (
            <div key={m.key} className="text-center">
              <div className="text-xs text-slate-500">{m.label}</div>
              <div className="text-sm font-semibold text-slate-200">
                {m.fmt(sc[m.key] as number | null)}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 10 criteria grid */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {Object.entries(CRITERIA_META).map(([key, meta]) => {
          const passed = sc.checks?.[key as keyof typeof sc.checks] ?? false
          const rawVal = sc[key as keyof BtScorecard] as number | null
          return (
            <div
              key={key}
              className={`rounded-lg border p-3 ${passed ? 'border-green-800 bg-green-950/30' : 'border-red-900 bg-red-950/20'}`}
            >
              <div className="flex items-start justify-between gap-1">
                <div className="text-xs text-slate-400 leading-tight">{meta.label}</div>
                {passed
                  ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-400" />
                  : <XCircle className="h-3.5 w-3.5 shrink-0 text-red-400" />}
              </div>
              <div className="mt-1 text-base font-bold text-slate-200">{meta.fmt(rawVal)}</div>
              <div className="text-[10px] text-slate-600">{meta.threshold}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── WFA table ────────────────────────────────────────────────────────────────

function WfaTable({ windows }: { windows: WfaWindow[] }) {
  if (!windows.length) return <p className="text-sm text-slate-500">No WFA windows available.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700 text-left text-xs text-slate-500">
            <th className="pb-2 pr-4">Window</th>
            <th className="pb-2 pr-4">IS Sharpe</th>
            <th className="pb-2 pr-4">OOS Sharpe</th>
            <th className="pb-2 pr-4">OOS Return</th>
            <th className="pb-2 pr-4">OOS Trades</th>
            <th className="pb-2">Profitable</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((w, i) => (
            <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/40">
              <td className="py-1.5 pr-4 text-slate-400 text-xs">{w.window}</td>
              <td className="py-1.5 pr-4">{w.is_sharpe.toFixed(2)}</td>
              <td className={`py-1.5 pr-4 font-semibold ${w.oos_sharpe >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {w.oos_sharpe.toFixed(2)}
              </td>
              <td className={`py-1.5 pr-4 ${w.oos_return >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {w.oos_return.toFixed(1)}%
              </td>
              <td className="py-1.5 pr-4 text-slate-300">{w.oos_trades}</td>
              <td className="py-1.5">
                {w.profitable
                  ? <CheckCircle2 className="h-4 w-4 text-green-400" />
                  : <XCircle className="h-4 w-4 text-red-400" />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── Trade table ──────────────────────────────────────────────────────────────

function TradeTable({ trades }: { trades: BtTrade[] }) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? trades : trades.slice(0, 20)
  if (!trades.length) return <p className="text-sm text-slate-500">No trades recorded.</p>
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-left text-xs text-slate-500">
              <th className="pb-2 pr-3">Entry</th>
              <th className="pb-2 pr-3">Exit</th>
              <th className="pb-2 pr-3">Dir</th>
              <th className="pb-2 pr-3">Entry ₹</th>
              <th className="pb-2 pr-3">Exit ₹</th>
              <th className="pb-2 pr-3">PnL</th>
              <th className="pb-2">Ret %</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/40 text-xs">
                <td className="py-1 pr-3 text-slate-400">{t.entry_time.slice(0, 16)}</td>
                <td className="py-1 pr-3 text-slate-400">{t.exit_time.slice(0, 16)}</td>
                <td className={`py-1 pr-3 font-semibold ${t.direction.toLowerCase().includes('long') ? 'text-green-400' : 'text-red-400'}`}>
                  {t.direction.replace('Direction.', '')}
                </td>
                <td className="py-1 pr-3">{t.entry_price.toLocaleString('en-IN')}</td>
                <td className="py-1 pr-3">{t.exit_price.toLocaleString('en-IN')}</td>
                <td className={`py-1 pr-3 font-semibold ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(0)}
                </td>
                <td className={`py-1 ${t.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {t.return_pct >= 0 ? '+' : ''}{t.return_pct.toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {trades.length > 20 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-2 flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? 'Show less' : `Show all ${trades.length} trades`}
        </button>
      )}
    </div>
  )
}

// ─── Live log panel ───────────────────────────────────────────────────────────

function LogPanel({ entries }: { entries: BtLogEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [entries.length])

  return (
    <div className="h-48 overflow-y-auto rounded-lg bg-slate-950 p-3 font-mono text-xs">
      {entries.length === 0 && (
        <span className="text-slate-600">Waiting for job to start...</span>
      )}
      {entries.map(e => {
        const color = e.level === 'ERROR' ? 'text-red-400' : e.level === 'WARNING' ? 'text-yellow-400' : 'text-slate-300'
        return (
          <div key={e.seq} className={`${color} leading-5`}>{e.msg}</div>
        )
      })}
      <div ref={endRef} />
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Backtest() {
  const [strategies, setStrategies] = useState<BtStrategy[]>([])
  const [symbol, setSymbol] = useState('NIFTY')
  const [exchange, setExchange] = useState('NSE')
  const [barInterval, setBarInterval] = useState('D')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [strategyId, setStrategyId] = useState('ema_pullback')
  const [params, setParams] = useState<Record<string, number>>({})
  const [job, setJob] = useState<BtJob | null>(null)
  const [logs, setLogs] = useState<BtLogEntry[]>([])
  const logSinceRef = useRef(0)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [activeTab, setActiveTab] = useState<'scorecard' | 'equity' | 'wfa' | 'trades'>('scorecard')

  useEffect(() => {
    btApi.strategies().then(data => {
      setStrategies(data)
      if (data.length) {
        setStrategyId(data[0].id)
        setParams(data[0].default_params)
      }
    }).catch(() => {})
  }, [])

  const selectedStrat = strategies.find(s => s.id === strategyId)

  const handleStrategyChange = (id: string) => {
    setStrategyId(id)
    const strat = strategies.find(s => s.id === id)
    if (strat) setParams({ ...strat.default_params })
  }

  const fetchLogs = async () => {
    try {
      const res = await btApi.logs(logSinceRef.current)
      if (res.logs.length) {
        setLogs(prev => [...prev, ...res.logs])
        logSinceRef.current = res.seq
      }
    } catch (_) {}
  }

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  const startPolling = (jobId: string) => {
    stopPolling()
    const tick = () => {
      void fetchLogs()
      btApi.status(jobId).then(j => {
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          void fetchLogs()
          stopPolling()
        }
      }).catch(() => {})
    }
    pollRef.current = setInterval(tick, 1500)
  }

  const handleRun = async () => {
    stopPolling()
    setLogs([])
    logSinceRef.current = 0
    setJob(null)
    setActiveTab('scorecard')
    try {
      await btApi.clearLogs()
      const res = await btApi.run({
        symbol, exchange, interval: barInterval,
        start_date: startDate, end_date: endDate,
        strategy: strategyId, params,
      })
      startPolling(res.job_id)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to start job'
      setLogs([{ seq: 1, level: 'ERROR', msg }])
    }
  }

  const handleReset = () => {
    stopPolling()
    setJob(null)
    setLogs([])
    logSinceRef.current = 0
    if (selectedStrat) setParams({ ...selectedStrat.default_params })
  }

  const isRunning = job?.status === 'queued' || job?.status === 'running'
  const result = job?.result ?? null

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Backtest</h1>
        <p className="text-sm text-slate-500 mt-1">
          Run strategy backtests using Historify data with full scorecard validation
        </p>
      </div>

      {/* ── Config panel ── */}
      <div className="rounded-xl border border-slate-700 bg-slate-900 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Configuration</h2>

        {/* Row 1 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Symbol</label>
            <input
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Exchange</label>
            <select
              value={exchange}
              onChange={e => setExchange(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            >
              {['NSE', 'BSE', 'NFO', 'MCX', 'CDS'].map(ex => (
                <option key={ex} value={ex}>{ex}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Interval</label>
            <select
              value={barInterval}
              onChange={e => setBarInterval(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            >
              {[['D','Daily'],['1h','1 Hour'],['30m','30 Min'],['15m','15 Min'],['5m','5 Min']].map(([v,l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Strategy</label>
            <select
              value={strategyId}
              onChange={e => handleStrategyChange(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            >
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Row 2 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={e => setStartDate(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={e => setEndDate(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            />
          </div>
          {selectedStrat && Object.entries(params).slice(0, 2).map(([k, v]) => (
            <div key={k}>
              <label className="block text-xs text-slate-500 mb-1">{k}</label>
              <input
                type="number"
                value={v}
                step="0.5"
                onChange={e => setParams(prev => ({ ...prev, [k]: parseFloat(e.target.value) || 0 }))}
                className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
              />
            </div>
          ))}
        </div>

        {/* Row 3 — remaining params */}
        {selectedStrat && Object.entries(params).length > 2 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.entries(params).slice(2).map(([k, v]) => (
              <div key={k}>
                <label className="block text-xs text-slate-500 mb-1">{k}</label>
                <input
                  type="number"
                  value={v}
                  step="0.5"
                  onChange={e => setParams(prev => ({ ...prev, [k]: parseFloat(e.target.value) || 0 }))}
                  className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
                />
              </div>
            ))}
          </div>
        )}

        {selectedStrat && (
          <p className="text-xs text-slate-600">{selectedStrat.description}</p>
        )}

        {/* Buttons */}
        <div className="flex gap-3 pt-1">
          <button
            onClick={handleRun}
            disabled={isRunning}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Play className="h-4 w-4" />
            {isRunning ? 'Running…' : 'Run Backtest'}
          </button>
          <button
            onClick={handleReset}
            className="flex items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-400 hover:text-slate-200 hover:border-slate-500"
          >
            <RotateCcw className="h-4 w-4" />
            Reset
          </button>
          {job && (
            <div className="ml-auto flex items-center gap-2">
              {job.status === 'done' && <CheckCircle2 className="h-4 w-4 text-green-400" />}
              {job.status === 'error' && <AlertCircle className="h-4 w-4 text-red-400" />}
              <span className={`text-sm capitalize ${
                job.status === 'done' ? 'text-green-400' :
                job.status === 'error' ? 'text-red-400' :
                'text-yellow-400'
              }`}>{job.status}</span>
            </div>
          )}
        </div>
      </div>

      {/* ── Live log ── */}
      <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Live Log</h2>
          {isRunning && (
            <span className="flex items-center gap-1.5 text-xs text-yellow-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-yellow-400" />
              Running
            </span>
          )}
        </div>
        <LogPanel entries={logs} />
      </div>

      {/* ── Error ── */}
      {job?.status === 'error' && (
        <div className="rounded-xl border border-red-800 bg-red-950/30 p-4 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 shrink-0 text-red-400 mt-0.5" />
          <div>
            <div className="text-sm font-semibold text-red-400">Job failed</div>
            <div className="text-xs text-red-300 mt-0.5">{job.error}</div>
          </div>
        </div>
      )}

      {/* ── Results ── */}
      {result && (
        <div className="space-y-4">
          {/* Tab bar */}
          <div className="flex gap-1 border-b border-slate-700">
            {(['scorecard', 'equity', 'wfa', 'trades'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm capitalize border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-indigo-500 text-indigo-400'
                    : 'border-transparent text-slate-500 hover:text-slate-300'
                }`}
              >
                {tab === 'wfa' ? 'Walk-Forward' : tab === 'equity' ? 'Equity Curve' : tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
            <div className="ml-auto flex items-center pr-1 text-xs text-slate-600">
              {result.symbol} {result.exchange} · {result.interval} · {result.total_bars} bars
            </div>
          </div>

          {/* Tab content */}
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-5">
            {activeTab === 'scorecard' && <ScorecardPanel sc={result.scorecard} />}
            {activeTab === 'equity' && (
              <div>
                <h3 className="text-sm font-semibold text-slate-400 mb-3">Equity Curve</h3>
                <EquityChart pts={result.equity_curve} />
              </div>
            )}
            {activeTab === 'wfa' && (
              <div>
                <h3 className="text-sm font-semibold text-slate-400 mb-3">Walk-Forward Analysis</h3>
                <WfaTable windows={result.wfa_windows} />
              </div>
            )}
            {activeTab === 'trades' && (
              <div>
                <h3 className="text-sm font-semibold text-slate-400 mb-3">Trade List ({result.trades.length})</h3>
                <TradeTable trades={result.trades} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
