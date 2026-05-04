import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp,
  Database, Play, XCircle,
} from 'lucide-react'
import {
  btApi,
  type BtDataset,
  type BtPythonStrategy,
  type BtJob,
  type BtLogEntry,
  type BtResult,
  type BtScorecard,
  type WfaWindow,
  type BtTrade,
  type BtEquityPt,
} from '@/api/backtest'

// ─── Equity SVG ──────────────────────────────────────────────────────────────

function EquityChart({ pts }: { pts: BtEquityPt[] }) {
  if (!pts.length) return null
  const W = 700, H = 160, PL = 56, PR = 10, PT = 10, PB = 24
  const iW = W - PL - PR, iH = H - PT - PB
  const vals = pts.map(p => p.v)
  const minV = Math.min(...vals), maxV = Math.max(...vals)
  const range = maxV - minV || 1
  const sx = (i: number) => PL + (i / (pts.length - 1)) * iW
  const sy = (v: number) => PT + iH - ((v - minV) / range) * iH
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.v).toFixed(1)}`).join(' ')
  const fill = `${path} L${sx(pts.length - 1).toFixed(1)},${(PT + iH).toFixed(1)} L${PL},${(PT + iH).toFixed(1)} Z`
  const pct = ((vals[vals.length - 1] - vals[0]) / vals[0]) * 100
  const col = pct >= 0 ? '#22c55e' : '#ef4444'
  const yTicks = [minV, (minV + maxV) / 2, maxV]
  const xIdx = [0, Math.floor(pts.length / 2), pts.length - 1]
  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 200 }}>
        <defs>
          <linearGradient id="eqG" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={col} stopOpacity="0.3" />
            <stop offset="100%" stopColor={col} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="#334155" strokeWidth="0.5" strokeDasharray="4 3" />
            <text x={PL - 4} y={sy(v) + 4} textAnchor="end" fill="#64748b" fontSize="9">
              {v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toFixed(0)}
            </text>
          </g>
        ))}
        {xIdx.map(i => (
          <text key={i} x={sx(i)} y={H - 4} textAnchor="middle" fill="#64748b" fontSize="9">
            {new Date(pts[i].t).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })}
          </text>
        ))}
        <path d={fill} fill="url(#eqG)" />
        <path d={path} fill="none" stroke={col} strokeWidth="1.5" />
      </svg>
    </div>
  )
}

// ─── Scorecard ────────────────────────────────────────────────────────────────

const CRITERIA: Array<{ key: keyof BtScorecard; label: string; threshold: string; fmt: (v: number | null) => string }> = [
  { key: 'oos_is_sharpe_ratio',    label: 'OOS/IS Sharpe',         threshold: '> 0.50', fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'n_trades',               label: 'Total Trades',           threshold: '≥ 200',  fmt: v => v != null ? String(Math.round(v)) : '—' },
  { key: 'profit_factor',          label: 'Profit Factor',          threshold: '> 1.30', fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'calmar',                 label: 'Calmar Ratio',           threshold: '> 0.50', fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'wfa_profitable_windows', label: 'WFA Windows',            threshold: '≥ 6/8',  fmt: v => v != null ? String(Math.round(v)) : '—' },
  { key: 'param_sensitivity',      label: 'Param Sensitivity σ/μ',  threshold: '< 0.30', fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'mc_ruin_prob',           label: 'MC Ruin Prob',           threshold: '< 5%',   fmt: v => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 't_stat',                 label: 'T-Statistic',            threshold: '> 2.0',  fmt: v => v != null ? v.toFixed(2) : '—' },
  { key: 'n_profitable_regimes',   label: 'Profitable Regimes',     threshold: '≥ 3',    fmt: v => v != null ? String(Math.round(v)) : '—' },
  { key: 'sharpe_2x_slip',         label: 'Sharpe @ 2× Slip',       threshold: '> 1.0',  fmt: v => v != null ? v.toFixed(2) : '—' },
]

const CRITERIA_KEYS: Record<string, keyof typeof CRITERIA[0]> = {}
CRITERIA.forEach(c => { CRITERIA_KEYS[c.key as string] = 'key' })

const SUMMARY_METRICS = [
  { key: 'total_return', label: 'Return',        fmt: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'sharpe',       label: 'Sharpe',        fmt: (v: number | null) => v != null ? v.toFixed(2) : '—' },
  { key: 'max_drawdown', label: 'Max DD',         fmt: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '—' },
  { key: 'calmar',       label: 'Calmar',         fmt: (v: number | null) => v != null ? v.toFixed(2) : '—' },
  { key: 'n_trades',     label: 'Trades',         fmt: (v: number | null) => v != null ? String(Math.round(v)) : '—' },
  { key: 'win_rate',     label: 'Win Rate',       fmt: (v: number | null) => v != null ? `${v.toFixed(1)}%` : '—' },
  { key: 'profit_factor',label: 'Profit Factor',  fmt: (v: number | null) => v != null ? v.toFixed(2) : '—' },
  { key: 't_stat',       label: 'T-Stat',         fmt: (v: number | null) => v != null ? v.toFixed(2) : '—' },
]

function ScorecardPanel({ sc }: { sc: BtScorecard }) {
  const isPass = sc.verdict === 'PASS'
  return (
    <div className="space-y-4">
      <div className={`flex flex-wrap items-center justify-between gap-4 rounded-lg border p-4 ${isPass ? 'border-green-800 bg-green-950/30' : 'border-red-900 bg-red-950/20'}`}>
        <div>
          <div className={`text-3xl font-bold ${isPass ? 'text-green-400' : 'text-red-400'}`}>{sc.verdict}</div>
          <div className="text-sm text-slate-500">{sc.pass_count} / 10 criteria</div>
        </div>
        <div className="grid grid-cols-4 gap-3 sm:grid-cols-8">
          {SUMMARY_METRICS.map(m => (
            <div key={m.key} className="text-center">
              <div className="text-[10px] text-slate-600">{m.label}</div>
              <div className="text-sm font-semibold text-slate-200">{m.fmt(sc[m.key as keyof BtScorecard] as number | null)}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {CRITERIA.map(c => {
          const passed = sc.checks?.[c.key as keyof typeof sc.checks] ?? false
          const val = sc[c.key] as number | null
          return (
            <div key={c.key as string} className={`rounded-lg border p-3 ${passed ? 'border-green-800 bg-green-950/30' : 'border-red-900 bg-red-950/20'}`}>
              <div className="flex items-start justify-between gap-1">
                <span className="text-xs text-slate-400 leading-tight">{c.label}</span>
                {passed ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-400" /> : <XCircle className="h-3.5 w-3.5 shrink-0 text-red-400" />}
              </div>
              <div className="mt-1 text-base font-bold text-slate-200">{c.fmt(val)}</div>
              <div className="text-[10px] text-slate-600">{c.threshold}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── WFA table ────────────────────────────────────────────────────────────────

function WfaTable({ windows }: { windows: WfaWindow[] }) {
  if (!windows.length) return <p className="text-sm text-slate-500">No WFA data.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700 text-left text-xs text-slate-500">
            {['Window', 'IS Sharpe', 'OOS Sharpe', 'OOS Return', 'OOS Trades', 'Profitable'].map(h => (
              <th key={h} className="pb-2 pr-4">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {windows.map((w, i) => (
            <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/40">
              <td className="py-1.5 pr-4 text-xs text-slate-400">{w.window}</td>
              <td className="py-1.5 pr-4">{w.is_sharpe.toFixed(2)}</td>
              <td className={`py-1.5 pr-4 font-semibold ${w.oos_sharpe >= 0 ? 'text-green-400' : 'text-red-400'}`}>{w.oos_sharpe.toFixed(2)}</td>
              <td className={`py-1.5 pr-4 ${w.oos_return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{w.oos_return.toFixed(1)}%</td>
              <td className="py-1.5 pr-4">{w.oos_trades}</td>
              <td className="py-1.5">{w.profitable ? <CheckCircle2 className="h-4 w-4 text-green-400" /> : <XCircle className="h-4 w-4 text-red-400" />}</td>
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
  const shown = expanded ? trades : trades.slice(0, 25)
  if (!trades.length) return <p className="text-sm text-slate-500">No trades.</p>
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-700 text-left text-slate-500">
              {['Entry', 'Exit', 'Dir', 'Entry ₹', 'Exit ₹', 'PnL', 'Ret %'].map(h => (
                <th key={h} className="pb-2 pr-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/40">
                <td className="py-1 pr-3 text-slate-400">{t.entry_time}</td>
                <td className="py-1 pr-3 text-slate-400">{t.exit_time}</td>
                <td className={`py-1 pr-3 font-semibold ${t.direction.toLowerCase().includes('long') ? 'text-green-400' : 'text-red-400'}`}>{t.direction}</td>
                <td className="py-1 pr-3">{t.entry_price.toLocaleString('en-IN')}</td>
                <td className="py-1 pr-3">{t.exit_price.toLocaleString('en-IN')}</td>
                <td className={`py-1 pr-3 font-semibold ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(0)}</td>
                <td className={t.return_pct >= 0 ? 'text-green-400' : 'text-red-400'}>{t.return_pct >= 0 ? '+' : ''}{t.return_pct.toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {trades.length > 25 && (
        <button onClick={() => setExpanded(!expanded)} className="mt-2 flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300">
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? 'Show less' : `Show all ${trades.length} trades`}
        </button>
      )}
    </div>
  )
}

// ─── Log panel ────────────────────────────────────────────────────────────────

function LogPanel({ entries }: { entries: BtLogEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [entries.length])
  return (
    <div className="h-44 overflow-y-auto rounded-lg bg-slate-950 p-3 font-mono text-xs">
      {entries.length === 0 && <span className="text-slate-600">Waiting for job to start …</span>}
      {entries.map(e => (
        <div key={e.seq} className={e.level === 'ERROR' ? 'text-red-400' : e.level === 'WARNING' ? 'text-yellow-400' : 'text-slate-300'}>
          {e.msg}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

// ─── Results ──────────────────────────────────────────────────────────────────

function ResultsPanel({ result }: { result: BtResult }) {
  const [tab, setTab] = useState<'scorecard' | 'equity' | 'wfa' | 'trades'>('scorecard')
  return (
    <div className="space-y-4">
      {/* Run info bar */}
      <div className="rounded-lg border border-slate-700 bg-slate-800/50 px-4 py-3 flex flex-wrap gap-4 text-sm">
        <div><span className="text-slate-500">Symbol </span><span className="font-semibold text-slate-200">{result.symbol}</span></div>
        <div><span className="text-slate-500">Exchange </span><span className="font-semibold text-slate-200">{result.exchange}</span></div>
        <div><span className="text-slate-500">Interval </span><span className="font-semibold text-slate-200">{result.interval}</span></div>
        <div><span className="text-slate-500">Bars </span><span className="font-semibold text-slate-200">{result.total_bars.toLocaleString()}</span></div>
        <div><span className="text-slate-500">Strategy </span><span className="font-semibold text-slate-200">{result.strategy_name}</span></div>
        <div><span className="text-slate-500">Type </span><span className="font-semibold text-indigo-400">{result.bt_type}</span></div>
        {Object.entries(result.bt_params).map(([k, v]) => (
          <div key={k}><span className="text-slate-500">{k.replace(/_/g, ' ')} </span><span className="font-semibold text-slate-300">{v}</span></div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-700">
        {(['scorecard', 'equity', 'wfa', 'trades'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm capitalize border-b-2 transition-colors ${tab === t ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'}`}>
            {t === 'wfa' ? 'Walk-Forward' : t === 'equity' ? 'Equity Curve' : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900 p-5">
        {tab === 'scorecard' && <ScorecardPanel sc={result.scorecard} />}
        {tab === 'equity' && <EquityChart pts={result.equity_curve} />}
        {tab === 'wfa' && <WfaTable windows={result.wfa_windows} />}
        {tab === 'trades' && <TradeTable trades={result.trades} />}
      </div>
    </div>
  )
}

// ─── Compatibility check ──────────────────────────────────────────────────────

const PERIOD_KEYS = new Set(['fast', 'slow', 'rsi_period', 'ema_period'])
const WFA_N_SPLITS = 8
const WFA_TRAIN_FRAC = 0.70

// Words that appear in strategy names but are NOT instrument identifiers
const GENERIC_TOKENS = new Set([
  'regime','adaptive','sided','two','three','strategy','setup',
  'scalp','trend','swing','reversal','momentum','breakout','pullback',
  'crossover','combined','multi','intraday','daily','weekly','monthly',
  'edge','short','long','bullish','bearish','neutral','hybrid',
  'mean','rsi','ema','macd','vwap','atr','version','the','and','for',
])

function instrumentTokens(name: string): string[] {
  return name
    .split(/[\s\-_.]+/)
    .map(t => t.toLowerCase())
    .filter(t =>
      t.length >= 4 &&
      !/^v\d/.test(t) &&       // v1, v3.1
      !/^\d+$/.test(t) &&       // pure numbers / dates
      !GENERIC_TOKENS.has(t)
    )
}

function checkInstrumentMatch(ds: BtDataset, st: BtPythonStrategy): { matched: boolean | null; hint: string } {
  const tokens = [...instrumentTokens(st.name), ...instrumentTokens(st.id)]
  if (tokens.length === 0) return { matched: null, hint: '' }
  const symLower = ds.symbol.toLowerCase()
  for (const t of tokens) {
    if (symLower.includes(t)) return { matched: true, hint: t }
  }
  return { matched: false, hint: tokens[0] }
}

interface CompatCheck {
  label: string
  detail: string
  status: 'pass' | 'warn' | 'fail'
  fatal: boolean
}

function checkCompatibility(ds: BtDataset, st: BtPythonStrategy): { checks: CompatCheck[]; blocked: boolean } {
  const maxPeriod = Math.max(
    ...Object.entries(st.bt_params).filter(([k]) => PERIOD_KEYS.has(k)).map(([, v]) => v),
    1
  )
  const windowSize = Math.floor(ds.record_count / WFA_N_SPLITS)
  const isSize     = Math.floor(windowSize * WFA_TRAIN_FRAC)
  const oosSize    = windowSize - isSize

  const wfaOk    = ds.record_count >= WFA_N_SPLITS * 60
  const oosPass  = oosSize >= 30
  const oosWarn  = oosSize >= 10 && oosSize < 30
  const wrmPass  = isSize >= maxPeriod * 3
  const wrmWarn  = isSize >= maxPeriod && isSize < maxPeriod * 3
  const trdPass  = ds.record_count >= 1500
  const trdWarn  = ds.record_count >= 700 && ds.record_count < 1500

  const { matched, hint } = checkInstrumentMatch(ds, st)

  const checks: CompatCheck[] = [
    {
      label:  'Instrument match',
      detail: matched === null
        ? `Cannot determine target instrument from strategy name`
        : matched
        ? `"${hint}" found in ${ds.symbol} — strategy designed for this instrument`
        : `Strategy appears designed for "${hint}", not ${ds.symbol} — results may be unreliable`,
      status: matched === null ? 'warn' : matched ? 'pass' : 'warn',
      fatal:  false,
    },
    {
      label:  'WFA window size',
      detail: `${windowSize} bars/window across ${WFA_N_SPLITS} splits (need ≥ 60)`,
      status: wfaOk ? 'pass' : 'fail',
      fatal:  !wfaOk,
    },
    {
      label:  'OOS bars per window',
      detail: `${oosSize} OOS bars (30% of ${windowSize}, need ≥ 10)`,
      status: oosPass ? 'pass' : oosWarn ? 'warn' : 'fail',
      fatal:  oosSize < 10,
    },
    {
      label:  'Indicator warmup',
      detail: `IS window ${isSize} bars vs longest period ${maxPeriod} (need ≥ ${maxPeriod}×3=${maxPeriod * 3})`,
      status: wrmPass ? 'pass' : wrmWarn ? 'warn' : 'fail',
      fatal:  isSize < maxPeriod,
    },
    {
      label:  'Trade count (≥ 200 needed)',
      detail: ds.record_count >= 1500
        ? `${ds.record_count.toLocaleString()} bars — likely sufficient`
        : `${ds.record_count.toLocaleString()} bars — may not generate enough trades`,
      status: trdPass ? 'pass' : trdWarn ? 'warn' : 'fail',
      fatal:  false,
    },
  ]

  return { checks, blocked: checks.some(c => c.fatal) }
}

function CompatibilityPanel({ ds, st }: { ds: BtDataset; st: BtPythonStrategy }) {
  const { checks, blocked } = checkCompatibility(ds, st)
  const hasWarn = checks.some(c => c.status === 'warn')

  const borderCls = blocked
    ? 'border-red-800 bg-red-950/20'
    : hasWarn
    ? 'border-yellow-800 bg-yellow-950/10'
    : 'border-green-800 bg-green-950/20'

  const badgeCls = blocked
    ? 'bg-red-900/50 text-red-300'
    : hasWarn
    ? 'bg-yellow-900/50 text-yellow-300'
    : 'bg-green-900/50 text-green-300'

  const badgeLabel = blocked ? 'BLOCKED' : hasWarn ? 'CAUTION' : 'READY'

  return (
    <div className={`rounded-xl border p-4 space-y-3 ${borderCls}`}>
      {/* Header row */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Compatibility Check</h2>
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${badgeCls}`}>{badgeLabel}</span>
        </div>
        <div className="flex gap-4 text-xs text-slate-400">
          <span><span className="text-slate-500">Dataset </span>{ds.symbol} · {ds.exchange} · {ds.interval} · {ds.record_count.toLocaleString()} bars</span>
          <span><span className="text-slate-500">Strategy </span>{st.name} · {st.bt_type}</span>
        </div>
      </div>

      {/* Check rows */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {checks.map(c => (
          <div key={c.label} className="flex items-start gap-2">
            {c.status === 'pass' && <CheckCircle2 className="h-4 w-4 shrink-0 text-green-400 mt-0.5" />}
            {c.status === 'warn' && <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-400 mt-0.5" />}
            {c.status === 'fail' && <XCircle       className="h-4 w-4 shrink-0 text-red-400 mt-0.5" />}
            <div>
              <div className="text-xs font-medium text-slate-300">{c.label}</div>
              <div className="text-[11px] text-slate-500 leading-tight">{c.detail}</div>
            </div>
          </div>
        ))}
      </div>

      {blocked && (
        <p className="text-xs text-red-400 mt-1">Dataset has insufficient bars for this strategy. Choose a larger dataset or a strategy with shorter indicator periods.</p>
      )}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function Backtest() {
  const [datasets, setDatasets] = useState<BtDataset[]>([])
  const [strategies, setStrategies] = useState<BtPythonStrategy[]>([])
  const [loading, setLoading] = useState(true)
  const [dsFilter, setDsFilter] = useState('')

  const [selectedDataset, setSelectedDataset] = useState<BtDataset | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<BtPythonStrategy | null>(null)

  const [job, setJob] = useState<BtJob | null>(null)
  const [logs, setLogs] = useState<BtLogEntry[]>([])
  const logSinceRef = useRef(0)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    Promise.all([btApi.datasets(), btApi.pythonStrategies()])
      .then(([ds, st]) => { setDatasets(ds); setStrategies(st) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const filteredDatasets = dsFilter.trim()
    ? datasets.filter(d =>
        d.symbol.toLowerCase().includes(dsFilter.toLowerCase()) ||
        d.exchange.toLowerCase().includes(dsFilter.toLowerCase()) ||
        d.interval.toLowerCase().includes(dsFilter.toLowerCase())
      )
    : datasets

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  const clearResults = () => {
    stopPolling()
    setJob(null)
    setLogs([])
    logSinceRef.current = 0
  }

  const startPolling = (jobId: string) => {
    stopPolling()
    const tick = () => {
      void btApi.logs(logSinceRef.current).then(res => {
        if (res.logs.length) { setLogs(p => [...p, ...res.logs]); logSinceRef.current = res.seq }
      })
      btApi.status(jobId).then(j => {
        setJob(j)
        if (j.status === 'done' || j.status === 'error') {
          btApi.logs(logSinceRef.current).then(res => {
            if (res.logs.length) { setLogs(p => [...p, ...res.logs]); logSinceRef.current = res.seq }
          }).catch(() => {})
          stopPolling()
        }
      }).catch(() => {})
    }
    pollRef.current = setInterval(tick, 1500)
  }

  const handleRun = () => {
    if (!selectedDataset || !selectedStrategy) return
    stopPolling()
    setLogs([])
    logSinceRef.current = 0
    setJob(null)
    btApi.clearLogs().catch(() => {})
    btApi.run({ dataset_key: selectedDataset.key, strategy_id: selectedStrategy.id })
      .then(res => startPolling(res.job_id))
      .catch(err => {
        const msg = err instanceof Error ? err.message : 'Failed to start'
        setLogs([{ seq: 1, level: 'ERROR', msg }])
      })
  }

  const isRunning = job?.status === 'queued' || job?.status === 'running'
  const compat = selectedDataset && selectedStrategy ? checkCompatibility(selectedDataset, selectedStrategy) : null
  const canRun = !!selectedDataset && !!selectedStrategy && !isRunning && !(compat?.blocked)

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Backtest</h1>
        <p className="text-sm text-slate-500 mt-1">Select a dataset from Historify and a strategy to run a full scorecard backtest</p>
      </div>

      {loading && <p className="text-slate-500 text-sm">Loading datasets and strategies …</p>}

      {!loading && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* ── Dataset picker ── */}
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Database className="h-4 w-4 text-indigo-400" /> Dataset
              </h2>
              {selectedDataset && (
                <span className="text-xs text-indigo-400 font-medium">
                  {selectedDataset.symbol} · {selectedDataset.exchange} · {selectedDataset.interval}
                </span>
              )}
            </div>

            <input
              placeholder="Filter by symbol, exchange or interval …"
              value={dsFilter}
              onChange={e => setDsFilter(e.target.value)}
              className="w-full rounded bg-slate-800 px-2.5 py-1.5 text-sm text-slate-200 border border-slate-700 focus:outline-none focus:border-indigo-500"
            />

            <div className="max-h-64 overflow-y-auto rounded border border-slate-800">
              {filteredDatasets.length === 0 && (
                <div className="py-8 text-center text-sm text-slate-600">No Historify data found</div>
              )}
              {filteredDatasets.map(d => {
                const isSelected = selectedDataset?.key === d.key
                return (
                  <button
                    key={d.key}
                    onClick={() => { clearResults(); setSelectedDataset(d) }}
                    className={`w-full flex items-center justify-between px-3 py-2 text-left text-sm border-b border-slate-800 last:border-0 hover:bg-slate-800/60 transition-colors ${isSelected ? 'bg-indigo-950/60 text-indigo-300' : 'text-slate-300'}`}
                  >
                    <div className="flex items-center gap-3">
                      {isSelected && <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-indigo-400" />}
                      {!isSelected && <div className="h-3.5 w-3.5" />}
                      <span className="font-medium">{d.symbol}</span>
                      <span className="text-slate-500 text-xs">{d.exchange}</span>
                      <span className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-400">{d.interval}</span>
                    </div>
                    <div className="text-right text-xs text-slate-500">
                      <div>{d.record_count.toLocaleString()} bars</div>
                      <div>{d.first_date} → {d.last_date}</div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* ── Strategy picker ── */}
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">Strategy</h2>
              {selectedStrategy && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-indigo-400 font-medium">{selectedStrategy.bt_type}</span>
                  {selectedStrategy.has_custom_signals
                    ? <span className="rounded-full bg-emerald-900/60 border border-emerald-700 px-2 py-0.5 text-[10px] text-emerald-400 font-semibold">Custom Signals</span>
                    : <span className="rounded-full bg-slate-700/60 px-2 py-0.5 text-[10px] text-slate-500">Approx. Signals</span>}
                </div>
              )}
            </div>

            <div className="max-h-64 overflow-y-auto space-y-2">
              {strategies.length === 0 && (
                <div className="py-8 text-center text-sm text-slate-600">No Python strategies found</div>
              )}
              {strategies.map(s => {
                const isSelected = selectedStrategy?.id === s.id
                return (
                  <button
                    key={s.id}
                    onClick={() => { clearResults(); setSelectedStrategy(s) }}
                    className={`w-full rounded-lg border px-4 py-3 text-left transition-colors hover:border-indigo-700 ${isSelected ? 'border-indigo-600 bg-indigo-950/50' : 'border-slate-700 bg-slate-800/30'}`}
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="text-sm font-semibold text-slate-200">{s.name}</div>
                        <div className="text-xs text-slate-500 mt-0.5">{s.file}</div>
                      </div>
                      <div className="flex flex-col items-end gap-1 ml-2 shrink-0">
                        <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[10px] text-slate-400">{s.bt_type}</span>
                        {s.has_custom_signals
                          ? <span className="rounded-full bg-emerald-900/60 border border-emerald-700 px-2 py-0.5 text-[10px] text-emerald-400 font-semibold">Custom</span>
                          : <span className="rounded-full bg-slate-700/60 px-2 py-0.5 text-[10px] text-slate-500">Approx.</span>}
                      </div>
                    </div>
                    {Object.keys(s.key_params).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {Object.entries(s.key_params).map(([k, v]) => (
                          <span key={k} className="rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] text-slate-400">
                            {k.replace(/_/g, '')}={v}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── Compatibility panel ── */}
      {!loading && selectedDataset && selectedStrategy && (
        <CompatibilityPanel ds={selectedDataset} st={selectedStrategy} />
      )}

      {/* ── Run button ── */}
      {!loading && (
        <div className="flex items-center gap-4">
          <button
            onClick={handleRun}
            disabled={!canRun}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Play className="h-4 w-4" />
            {isRunning ? 'Running …' : 'Run Backtest'}
          </button>

          {job && (
            <div className="flex items-center gap-2">
              {job.status === 'done'  && <CheckCircle2 className="h-4 w-4 text-green-400" />}
              {job.status === 'error' && <AlertCircle className="h-4 w-4 text-red-400" />}
              <span className={`text-sm capitalize ${job.status === 'done' ? 'text-green-400' : job.status === 'error' ? 'text-red-400' : 'text-yellow-400'}`}>
                {job.status}
              </span>
            </div>
          )}
        </div>
      )}

      {/* ── Live log ── */}
      {(logs.length > 0 || isRunning) && (
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Live Log</h2>
            {isRunning && <span className="flex items-center gap-1.5 text-xs text-yellow-400"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-yellow-400" />Running</span>}
          </div>
          <LogPanel entries={logs} />
        </div>
      )}

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
      {job?.result && <ResultsPanel result={job.result} />}
    </div>
  )
}
