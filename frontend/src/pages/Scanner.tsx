import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import {
  Activity, BarChart3, ChevronDown, ChevronRight, ChevronUp, Play,
  RefreshCw, Send, Square, Terminal, TrendingUp, Trash2, Zap,
  CheckSquare, Square as SquareIcon,
} from 'lucide-react'
import {
  scannerApi,
  type ChartData,
  type LogEntry,
  type ScanSignal,
  type PaperPosition,
  type PaperSummary,
  type SchedulerStatus,
} from '@/api/scanner'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { showToast } from '@/utils/toast'

const POLL_MS = 5000

// ── Helpers ────────────────────────────────────────────────────────────────

// Extract the server-side error message from an axios rejection
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function serverErr(err: unknown, fallback = 'Request failed'): string {
  const e = err as any
  return e?.response?.data?.error || e?.response?.data?.message || e?.message || fallback
}

function fmt2(v: number | null | undefined) {
  return v == null ? '—' : v.toFixed(2)
}

function fmtPnl(v: number | null | undefined) {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function relTime(iso: string | null) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return h < 24 ? `${h}h ago` : new Date(iso).toLocaleDateString()
}

// ── Inline SVG candlestick + EMA5 chart ───────────────────────────────────

function CandleChart({ data, setupId }: { data: ChartData; setupId: 'A' | 'B' }) {
  const candles = setupId === 'B' && data.intra_candles?.length
    ? data.intra_candles
    : data.daily_candles
  const ema5 = data.ema5_line
  const { pdh, pdl } = data

  if (!candles.length) return (
    <div className="text-center text-muted-foreground text-xs py-6">No data</div>
  )

  const W = 580, H = 165
  const PAD = { top: 10, right: setupId === 'B' ? 36 : 14, bottom: 22, left: 54 }
  const cW = W - PAD.left - PAD.right
  const cH = H - PAD.top - PAD.bottom
  const n = candles.length

  const allP = [
    ...candles.flatMap(c => [c.high, c.low]),
    ...ema5.map(p => p.value),
    ...(pdh != null ? [pdh] : []),
    ...(pdl != null ? [pdl] : []),
  ]
  const rawMin = Math.min(...allP)
  const rawMax = Math.max(...allP)
  const margin = (rawMax - rawMin) * 0.06 || rawMax * 0.01
  const pMin = rawMin - margin
  const pMax = rawMax + margin
  const pRange = pMax - pMin

  const sy = (p: number) => PAD.top + cH * (1 - (p - pMin) / pRange)
  const slot = cW / n
  const bw = Math.max(3, slot * 0.55)
  const cx = (i: number) => PAD.left + (i + 0.5) * slot

  const grid = '#1e293b'
  const axis = '#64748b'

  const yTicks = [rawMin, (rawMin + rawMax) / 2, rawMax]

  const emaPoints = ema5
    .map((p, i) => `${cx(i).toFixed(1)},${sy(p.value).toFixed(1)}`)
    .join(' ')

  const fmtPrice = (v: number) =>
    v >= 10000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded" style={{ maxHeight: H }}>
      <rect width={W} height={H} fill="#0f172a" rx={4} />

      {/* grid */}
      {yTicks.map((v, i) => (
        <line key={i}
          x1={PAD.left} x2={PAD.left + cW} y1={sy(v)} y2={sy(v)}
          stroke={grid} strokeWidth={1} />
      ))}

      {/* PDH / PDL for Setup B */}
      {pdh != null && (
        <g>
          <line x1={PAD.left} x2={PAD.left + cW} y1={sy(pdh)} y2={sy(pdh)}
            stroke="#22c55e" strokeWidth={1} strokeDasharray="5,3" opacity={0.8} />
          <text x={PAD.left + cW + 3} y={sy(pdh) + 4} fill="#22c55e" fontSize={9}>PDH</text>
        </g>
      )}
      {pdl != null && (
        <g>
          <line x1={PAD.left} x2={PAD.left + cW} y1={sy(pdl)} y2={sy(pdl)}
            stroke="#ef4444" strokeWidth={1} strokeDasharray="5,3" opacity={0.8} />
          <text x={PAD.left + cW + 3} y={sy(pdl) + 4} fill="#ef4444" fontSize={9}>PDL</text>
        </g>
      )}

      {/* candles */}
      {candles.map((c, i) => {
        const up = c.close >= c.open
        const color = up ? '#22c55e' : '#ef4444'
        const bTop = sy(Math.max(c.open, c.close))
        const bBot = sy(Math.min(c.open, c.close))
        const bH = Math.max(1, bBot - bTop)
        const x = cx(i)
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={sy(c.high)} y2={sy(c.low)} stroke={color} strokeWidth={1} />
            <rect x={x - bw / 2} y={bTop} width={bw} height={bH} fill={color} opacity={0.9} />
          </g>
        )
      })}

      {/* EMA5 line */}
      {emaPoints && (
        <polyline points={emaPoints} fill="none"
          stroke="#3b82f6" strokeWidth={1.5} strokeLinejoin="round" />
      )}

      {/* y-axis labels */}
      {yTicks.map((v, i) => (
        <text key={i} x={PAD.left - 4} y={sy(v) + 4}
          textAnchor="end" fill={axis} fontSize={9} fontFamily="monospace">
          {fmtPrice(v)}
        </text>
      ))}

      {/* x-axis labels */}
      {candles.map((c, i) => {
        if (i % Math.max(1, Math.floor(n / 5)) !== 0) return null
        const label = setupId === 'B'
          ? new Date(c.time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
          : new Date(c.time).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' })
        return (
          <text key={i} x={cx(i)} y={H - 4}
            textAnchor="middle" fill={axis} fontSize={8}>
            {label}
          </text>
        )
      })}

      {/* legend */}
      <line x1={PAD.left + 4} x2={PAD.left + 18} y1={PAD.top + 9} y2={PAD.top + 9}
        stroke="#3b82f6" strokeWidth={1.5} />
      <text x={PAD.left + 22} y={PAD.top + 13} fill="#3b82f6" fontSize={9}>EMA5</text>
    </svg>
  )
}

// ── Stat card ──────────────────────────────────────────────────────────────

function StatCard({ label, value, colorize = false, icon, sub }: {
  label: string; value: string; colorize?: boolean; icon?: React.ReactNode; sub?: string
}) {
  const isPos = colorize && value.startsWith('+')
  const isNeg = colorize && value.startsWith('-')
  const cls = isPos ? 'text-green-500' : isNeg ? 'text-red-500' : ''
  return (
    <Card>
      <CardContent className="pt-4 pb-4">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</span>
          {icon && <span className="text-muted-foreground">{icon}</span>}
        </div>
        <div className={`text-2xl font-bold tabular-nums ${cls}`}>{value}</div>
        {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  )
}

// ── Signal row ─────────────────────────────────────────────────────────────

function SignalRow({ sig, selected, onToggle, chartOpen, onToggleChart }: {
  sig: ScanSignal
  selected: boolean
  onToggle: (id: string) => void
  chartOpen: boolean
  onToggleChart: () => void
}) {
  const isLong = sig.direction === 'LONG'
  const distCls = sig.distance_pct >= 5 ? 'text-amber-500' : 'text-muted-foreground'
  return (
    <tr className="border-b border-border hover:bg-muted/30">
      <td className="px-3 py-2 text-center">
        <button onClick={() => onToggle(sig.signal_id)}
          className="text-muted-foreground hover:text-primary transition-colors">
          {selected
            ? <CheckSquare className="h-4 w-4 text-primary" />
            : <SquareIcon className="h-4 w-4" />}
        </button>
      </td>
      <td className="px-3 py-2 font-mono text-sm font-semibold">{sig.symbol}</td>
      <td className="px-3 py-2 text-center">
        <Badge variant={sig.setup_id === 'A' ? 'default' : 'secondary'} className="text-xs px-1.5">
          {sig.setup_id === 'A' ? 'Pullback' : 'Breakout'}
        </Badge>
      </td>
      <td className="px-3 py-2 text-center">
        <Badge variant={isLong ? 'outline' : 'destructive'}
          className={`text-xs px-1.5 ${isLong ? 'text-green-500 border-green-500' : ''}`}>
          {isLong ? '▲ LONG' : '▼ SHORT'}
        </Badge>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{fmt2(sig.ltp)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-blue-400">{fmt2(sig.ema5)}</td>
      <td className={`px-3 py-2 text-right tabular-nums ${distCls}`}>
        {sig.distance_pct != null ? `${sig.distance_pct.toFixed(2)}%` : '—'}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{fmt2(sig.rsi14)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-emerald-500">{fmt2(sig.target)}</td>
      <td className="px-3 py-2 text-right text-xs text-muted-foreground">{relTime(sig.signal_time)}</td>
      <td className="px-3 py-2 text-center">
        <button onClick={onToggleChart}
          className="text-muted-foreground hover:text-primary transition-colors"
          title="Toggle chart">
          {chartOpen
            ? <ChevronDown className="h-4 w-4" />
            : <ChevronRight className="h-4 w-4" />}
        </button>
      </td>
    </tr>
  )
}

// ── Paper position row ─────────────────────────────────────────────────────

function PositionRow({ pos, onClose }: { pos: PaperPosition; onClose?: (id: string) => void }) {
  const isOpen = pos.status === 'OPEN' || pos.status === 'DATA_STALLED'
  const isLong = pos.direction === 'LONG'
  const pnlCls = (pos.pnl ?? 0) >= 0 ? 'text-green-500' : 'text-red-500'
  return (
    <tr className="border-b border-border hover:bg-muted/30">
      <td className="px-3 py-2 font-mono text-sm font-semibold">{pos.symbol}</td>
      <td className="px-3 py-2 text-center">
        <Badge variant={pos.setup_id === 'A' ? 'default' : 'secondary'} className="text-xs px-1.5">{pos.setup_id}</Badge>
      </td>
      <td className="px-3 py-2 text-center">
        <Badge variant={isLong ? 'outline' : 'destructive'}
          className={`text-xs px-1.5 ${isLong ? 'text-green-500 border-green-500' : ''}`}>
          {isLong ? '▲' : '▼'} {pos.direction}
        </Badge>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{fmt2(pos.entry)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{pos.qty}</td>
      <td className="px-3 py-2 text-right tabular-nums text-red-400">{fmt2(pos.trailing_sl)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-emerald-500">{fmt2(pos.target)}</td>
      <td className={`px-3 py-2 text-right tabular-nums font-semibold ${pnlCls}`}>{fmtPnl(pos.pnl)}</td>
      <td className="px-3 py-2 text-center">
        {isOpen
          ? <Badge variant={pos.status === 'DATA_STALLED' ? 'destructive' : 'default'} className="text-xs">
              {pos.status === 'DATA_STALLED' ? 'STALLED' : 'OPEN'}
            </Badge>
          : <span className="text-xs text-muted-foreground">{pos.exit_reason ?? 'CLOSED'}</span>}
      </td>
      <td className="px-3 py-2 text-right text-xs text-muted-foreground">{relTime(pos.opened_at)}</td>
      <td className="px-3 py-2 text-center">
        {isOpen && onClose && (
          <Button variant="ghost" size="sm"
            className="h-6 px-2 text-xs text-red-500 hover:text-red-400"
            onClick={() => onClose(pos.position_id)}>
            Close
          </Button>
        )}
      </td>
    </tr>
  )
}

// ── Filter pill ────────────────────────────────────────────────────────────

function Pill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className={`px-3 py-1 rounded-full text-xs font-medium transition-colors border ${
        active
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-transparent text-muted-foreground border-border hover:border-primary/50 hover:text-foreground'
      }`}>
      {label}
    </button>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function Scanner() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const [signals, setSignals] = useState<ScanSignal[]>([])
  const [setupFilter, setSetupFilter] = useState<'ALL' | 'A' | 'B'>('ALL')
  const [dirFilter, setDirFilter] = useState<'ALL' | 'LONG' | 'SHORT'>('ALL')
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const [openPos, setOpenPos] = useState<PaperPosition[]>([])
  const [closedPos, setClosedPos] = useState<PaperPosition[]>([])
  const [summary, setSummary] = useState<PaperSummary | null>(null)

  // Chart state
  const [expandedChart, setExpandedChart] = useState<string | null>(null)
  const [chartCache, setChartCache] = useState<Record<string, ChartData>>({})
  const [chartLoading, setChartLoading] = useState<string | null>(null)

  // Live log state
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logSince, setLogSince] = useState(0)
  const [logOpen, setLogOpen] = useState(true)
  const logEndRef = useRef<HTMLDivElement>(null)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Fetch all data ───────────────────────────────────────────────────────

  const fetchAll = useCallback(async (silent = true) => {
    try {
      const [statusRes, signalsRes, paperRes, logsRes] = await Promise.all([
        scannerApi.getStatus(),
        scannerApi.getSignals(
          setupFilter !== 'ALL' ? setupFilter : undefined,
          dirFilter !== 'ALL' ? dirFilter : undefined,
        ),
        scannerApi.getPaperStatus(),
        scannerApi.getLogs(logSince),
      ])
      if (statusRes.ok && statusRes.data) setStatus(statusRes.data)
      if (signalsRes.ok && signalsRes.data) setSignals(signalsRes.data)
      if (paperRes.ok && paperRes.data) {
        setOpenPos(paperRes.data.open)
        setClosedPos(paperRes.data.closed)
        setSummary(paperRes.data.summary)
      }
      if (logsRes.ok && logsRes.data?.logs.length) {
        const newLogs = logsRes.data.logs
        setLogs(prev => [...prev, ...newLogs].slice(-300))
        setLogSince(newLogs[newLogs.length - 1].idx)
      }
      setUpdatedAt(new Date())
    } catch {
      if (!silent) showToast.error('Failed to fetch scanner data')
    }
  }, [setupFilter, dirFilter, logSince])

  useEffect(() => { fetchAll(false) }, [setupFilter, dirFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (autoRefresh) timerRef.current = setInterval(() => fetchAll(true), POLL_MS)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [autoRefresh, fetchAll])

  // Auto-scroll log panel to bottom on new entries
  useEffect(() => {
    if (logOpen) logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs, logOpen])

  // ── Chart toggle ─────────────────────────────────────────────────────────

  const handleToggleChart = useCallback(async (sig: ScanSignal) => {
    const id = sig.signal_id
    if (expandedChart === id) {
      setExpandedChart(null)
      return
    }
    setExpandedChart(id)
    if (chartCache[id]) return

    setChartLoading(id)
    try {
      const exchange = sig.exchange ?? 'NSE'
      const res = await scannerApi.getChartData(sig.symbol, exchange, sig.setup_id)
      if (res.ok && res.data) {
        setChartCache(prev => ({ ...prev, [id]: res.data! }))
      } else {
        showToast.error(res.error || 'Chart data unavailable')
        setExpandedChart(null)
      }
    } catch {
      showToast.error('Failed to load chart')
      setExpandedChart(null)
    } finally {
      setChartLoading(null)
    }
  }, [expandedChart, chartCache])

  // ── Actions ──────────────────────────────────────────────────────────────

  const handleRunOnce = async () => {
    setActionLoading('run')
    try {
      const res = await scannerApi.runOnce()
      if (res.ok) {
        const count = res.data?.length ?? 0
        showToast.success(`Scan complete — ${count} signal${count !== 1 ? 's' : ''} found`)
        fetchAll(true)
      } else {
        showToast.error(res.error || 'Scan failed')
      }
    } catch (err) {
      showToast.error(serverErr(err, 'Scan failed — check broker login'))
    } finally {
      setActionLoading(null)
    }
  }

  const handleToggleContinuous = async () => {
    const isRunning = status?.running
    setActionLoading('continuous')
    try {
      const res = isRunning ? await scannerApi.stopContinuous() : await scannerApi.startContinuous()
      if (res.ok) {
        showToast.success(isRunning ? 'Scanner stopped' : 'Continuous scanner started')
        fetchAll(true)
      } else {
        showToast.error(res.error || 'Action failed')
      }
    } catch (err) {
      showToast.error(serverErr(err))
    } finally {
      setActionLoading(null)
    }
  }

  const handlePaperTrade = async () => {
    if (selected.size === 0) { showToast.error('Select at least one signal'); return }
    setActionLoading('paper')
    try {
      const res = await scannerApi.startPaperTrade(Array.from(selected))
      if (res.ok) {
        showToast.success('Paper positions opened')
        setSelected(new Set())
        fetchAll(true)
      } else {
        showToast.error(res.error || 'Failed to open positions')
      }
    } catch (err) {
      showToast.error(serverErr(err))
    } finally {
      setActionLoading(null)
    }
  }

  const handleClosePosition = async (positionId: string) => {
    try {
      const res = await scannerApi.closePaperPosition(positionId)
      if (res.ok) { showToast.success('Position closed'); fetchAll(true) }
      else showToast.error(res.error || 'Failed to close')
    } catch (err) { showToast.error(serverErr(err)) }
  }

  const handleTelegramTest = async () => {
    setActionLoading('telegram')
    try {
      const res = await scannerApi.testTelegram()
      if (res.ok) showToast.success('Telegram test message sent')
      else showToast.error(res.error || 'Failed — check env vars')
    } catch (err) { showToast.error(serverErr(err)) }
    finally { setActionLoading(null) }
  }

  const handleClearLogs = async () => {
    try {
      await scannerApi.clearLogs()
      setLogs([])
      setLogSince(0)
    } catch { /* silent */ }
  }

  const toggleSignal = (id: string) => {
    setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  const isRunning = status?.running ?? false
  const isPaused = status?.paused ?? false
  const statusLabel = isPaused ? 'Paused' : isRunning ? 'Running' : 'Idle'
  const statusVariant: 'default' | 'secondary' | 'destructive' = isPaused ? 'destructive' : isRunning ? 'default' : 'secondary'

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="py-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BarChart3 className="h-6 w-6 text-green-500" />
            Dual-Setup Scanner
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Nifty 50 · Setup A: EMA Pullback · Setup B: EMA Breakout · Paper Trading
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {updatedAt && <span>Updated {updatedAt.toLocaleTimeString()}</span>}
          <Button variant="ghost" size="sm"
            className={`h-7 px-2 ${autoRefresh ? 'text-primary' : 'text-muted-foreground'}`}
            onClick={() => setAutoRefresh(p => !p)}
            title={autoRefresh ? 'Auto-refresh on' : 'Auto-refresh off'}>
            <RefreshCw className={`h-3.5 w-3.5 ${autoRefresh ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      {/* Control bar */}
      <Card>
        <CardContent className="pt-5 pb-5">
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm"
              disabled={actionLoading === 'run'} onClick={handleRunOnce} className="gap-1.5">
              <Zap className="h-4 w-4" />
              {actionLoading === 'run' ? 'Scanning…' : 'Run Scan Once'}
            </Button>

            <Button variant={isRunning ? 'destructive' : 'default'} size="sm"
              disabled={actionLoading === 'continuous'} onClick={handleToggleContinuous} className="gap-1.5">
              {isRunning
                ? <><Square className="h-4 w-4" /> Stop Continuous</>
                : <><Play className="h-4 w-4" /> Start Continuous</>}
            </Button>

            <div className="flex items-center gap-1.5">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <Badge variant={statusVariant}>{statusLabel}</Badge>
            </div>

            {isRunning && status?.last_scan_a && (
              <span className="text-xs text-muted-foreground hidden sm:block">
                Last scan: {relTime(status.last_scan_a)}
              </span>
            )}

            <div className="ml-auto">
              <Button variant="ghost" size="sm"
                disabled={actionLoading === 'telegram'} onClick={handleTelegramTest}
                className="gap-1.5 text-muted-foreground">
                <Send className="h-4 w-4" />
                Test Telegram
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Summary stats */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
          <StatCard label="Total P&L" value={fmtPnl(summary.total_pnl)} colorize
            icon={<TrendingUp className="h-4 w-4" />} />
          <StatCard label="Win Rate"
            value={summary.total_trades > 0 ? `${summary.win_rate}%` : '—'}
            icon={<Activity className="h-4 w-4" />} sub={`${summary.total_trades} trades`} />
          <StatCard label="Setup A P&L" value={fmtPnl(summary.setup_a_pnl)} colorize
            icon={<BarChart3 className="h-4 w-4" />} sub="Pullback" />
          <StatCard label="Setup B P&L" value={fmtPnl(summary.setup_b_pnl)} colorize
            icon={<BarChart3 className="h-4 w-4" />} sub="Breakout" />
          <StatCard label="Open Positions" value={String(openPos.length)}
            icon={<Activity className="h-4 w-4" />} />
        </div>
      )}

      {/* Signals table */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="text-base">Today's Signals</CardTitle>
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex gap-1">
                {(['ALL', 'A', 'B'] as const).map(f => (
                  <Pill key={f}
                    label={f === 'ALL' ? 'All' : f === 'A' ? 'Pullback' : 'Breakout'}
                    active={setupFilter === f} onClick={() => setSetupFilter(f)} />
                ))}
              </div>
              <div className="flex gap-1">
                {(['ALL', 'LONG', 'SHORT'] as const).map(f => (
                  <Pill key={f} label={f === 'ALL' ? 'All' : f}
                    active={dirFilter === f} onClick={() => setDirFilter(f)} />
                ))}
              </div>
              {selected.size > 0 && (
                <Button size="sm" disabled={actionLoading === 'paper'}
                  onClick={handlePaperTrade} className="h-7 px-3 text-xs gap-1">
                  <Play className="h-3 w-3" />
                  Paper Trade ({selected.size})
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {signals.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">
              No signals today — run a scan or wait for the next scheduled scan
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="px-3 py-2 w-8" />
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-center">Setup</th>
                    <th className="px-3 py-2 text-center">Dir</th>
                    <th className="px-3 py-2 text-right">LTP</th>
                    <th className="px-3 py-2 text-right">EMA5</th>
                    <th className="px-3 py-2 text-right">Dist%</th>
                    <th className="px-3 py-2 text-right">RSI</th>
                    <th className="px-3 py-2 text-right">Target</th>
                    <th className="px-3 py-2 text-right">When</th>
                    <th className="px-3 py-2 text-center">Chart</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map(sig => (
                    <Fragment key={sig.signal_id}>
                      <SignalRow
                        sig={sig}
                        selected={selected.has(sig.signal_id)}
                        onToggle={toggleSignal}
                        chartOpen={expandedChart === sig.signal_id}
                        onToggleChart={() => handleToggleChart(sig)}
                      />
                      {expandedChart === sig.signal_id && (
                        <tr>
                          <td colSpan={11} className="px-4 py-3 bg-muted/10 border-b border-border">
                            <div className="max-w-2xl">
                              <p className="text-xs text-muted-foreground mb-2 flex items-center gap-2">
                                <span className="font-semibold text-foreground">{sig.symbol}</span>
                                <span>·</span>
                                <span>{sig.setup_id === 'B' && chartCache[sig.signal_id]?.intra_candles?.length
                                  ? '5-min bars'
                                  : 'Daily bars'}</span>
                                <span>·</span>
                                <span className="text-blue-400">Blue = EMA5</span>
                                {sig.setup_id === 'B' && (
                                  <>
                                    <span>·</span>
                                    <span className="text-green-400">PDH</span>
                                    <span className="text-red-400">PDL</span>
                                  </>
                                )}
                              </p>
                              {chartLoading === sig.signal_id ? (
                                <div className="h-[165px] flex items-center justify-center text-muted-foreground text-sm">
                                  Loading chart…
                                </div>
                              ) : chartCache[sig.signal_id] ? (
                                <CandleChart
                                  data={chartCache[sig.signal_id]}
                                  setupId={sig.setup_id}
                                />
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Live Log Panel */}
      <Card>
        <CardHeader className="pb-2 pt-4">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2">
              <Terminal className="h-4 w-4 text-green-500" />
              Live Logs
              {logs.length > 0 && (
                <Badge variant="secondary" className="text-xs">{logs.length}</Badge>
              )}
            </CardTitle>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                title="Clear logs" onClick={handleClearLogs}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                onClick={() => setLogOpen(p => !p)}>
                {logOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </Button>
            </div>
          </div>
        </CardHeader>
        {logOpen && (
          <CardContent className="pt-0 pb-3">
            <div className="bg-[#050a0e] border border-border rounded font-mono text-xs h-52 overflow-y-auto p-3">
              {logs.length === 0 ? (
                <span className="text-slate-600">Waiting for scanner activity — run a scan or start continuous mode…</span>
              ) : (
                logs.map(log => {
                  const lvlCls =
                    log.level === 'ERROR' ? 'text-red-400' :
                    log.level === 'WARNING' ? 'text-amber-400' :
                    'text-emerald-500'
                  const msgCls =
                    log.level === 'ERROR' ? 'text-red-300' :
                    log.level === 'WARNING' ? 'text-amber-300' :
                    log.msg.toLowerCase().includes('signal') ? 'text-yellow-300' :
                    log.msg.toLowerCase().includes('closed') || log.msg.toLowerCase().includes('target') ? 'text-green-300' :
                    log.msg.toLowerCase().includes('open') ? 'text-blue-300' :
                    'text-slate-300'
                  return (
                    <div key={log.idx} className="flex gap-2 leading-5 min-w-0">
                      <span className="text-slate-600 shrink-0">{log.ts}</span>
                      <span className={`shrink-0 w-14 ${lvlCls}`}>{log.level}</span>
                      <span className="text-slate-500 shrink-0 w-20 truncate">{log.src}</span>
                      <span className={`${msgCls} break-all`}>{log.msg}</span>
                    </div>
                  )
                })
              )}
              <div ref={logEndRef} />
            </div>
          </CardContent>
        )}
      </Card>

      {/* Open Positions */}
      {openPos.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4 text-green-500" />
              Open Positions
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-center">Setup</th>
                    <th className="px-3 py-2 text-center">Dir</th>
                    <th className="px-3 py-2 text-right">Entry</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Trail SL</th>
                    <th className="px-3 py-2 text-right">Target</th>
                    <th className="px-3 py-2 text-right">P&L</th>
                    <th className="px-3 py-2 text-center">Status</th>
                    <th className="px-3 py-2 text-right">Opened</th>
                    <th className="px-3 py-2 text-center">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {openPos.map(pos => (
                    <PositionRow key={pos.position_id} pos={pos} onClose={handleClosePosition} />
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Trade History */}
      {closedPos.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
              Trade History
              <Badge variant="secondary" className="text-xs">{closedPos.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-center">Setup</th>
                    <th className="px-3 py-2 text-center">Dir</th>
                    <th className="px-3 py-2 text-right">Entry</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Trail SL</th>
                    <th className="px-3 py-2 text-right">Target</th>
                    <th className="px-3 py-2 text-right">P&L</th>
                    <th className="px-3 py-2 text-center">Exit</th>
                    <th className="px-3 py-2 text-right">Opened</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {closedPos.map(pos => <PositionRow key={pos.position_id} pos={pos} />)}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

    </div>
  )
}
