// v2
import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, ChevronsUpDown, RefreshCw, Activity, TrendingDown, TrendingUp, Zap } from 'lucide-react'
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import { useThemeStore } from '@/stores/themeStore'
import { deltaNeutralApi, type DeltaNeutralLeg, type DeltaNeutralResponse } from '@/api/delta-neutral'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { showToast } from '@/utils/toast'

const AUTO_REFRESH_INTERVAL = 15000

function convertExpiryForAPI(expiry: string): string {
  if (!expiry) return ''
  const parts = expiry.split('-')
  if (parts.length === 3) return `${parts[0]}${parts[1].toUpperCase()}${parts[2].slice(-2)}`
  return expiry.replace(/-/g, '').toUpperCase()
}

function fmt(v: number | null | undefined, d = 2) {
  return v == null ? '—' : v.toFixed(d)
}
function fmtSign(v: number | null | undefined, d = 2) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + v.toFixed(d)
}

// ── Pure-SVG payoff chart ──────────────────────────────────────────────────
function PayoffChart({ payoff, spotPrice, breakevens, isDark }: {
  payoff: { spot: number; pnl: number }[]
  spotPrice: number
  breakevens: number[]
  isDark: boolean
}) {
  if (!payoff || payoff.length === 0) return null

  const W = 800, H = 220
  const PAD = { top: 12, right: 20, bottom: 36, left: 64 }
  const cW = W - PAD.left - PAD.right
  const cH = H - PAD.top - PAD.bottom

  const allSpots = payoff.map(p => p.spot)
  const allPnls  = payoff.map(p => p.pnl)
  const sMin = Math.min(...allSpots)
  const sMax = Math.max(...allSpots)
  const pMin = Math.min(...allPnls, 0)
  const pMax = Math.max(...allPnls, 0)
  const pRange = (pMax - pMin) || 1
  const sRange = (sMax - sMin) || 1

  const sx = (s: number) => PAD.left + ((s - sMin) / sRange) * cW
  const sy = (p: number) => PAD.top + cH - ((p - pMin) / pRange) * cH
  const y0 = sy(0)

  const pts = payoff.map(p => `${sx(p.spot).toFixed(1)},${sy(p.pnl).toFixed(1)}`).join(' ')
  const curvePoints = payoff.map(p => `${sx(p.spot).toFixed(1)},${sy(p.pnl).toFixed(1)}`)
  const polyPoints = [
    `${PAD.left.toFixed(1)},${y0.toFixed(1)}`,
    ...curvePoints,
    `${(PAD.left + cW).toFixed(1)},${y0.toFixed(1)}`,
  ].join(' ')

  const grid = isDark ? '#2a2a2a' : '#e5e7eb'
  const axisText = isDark ? '#9ca3af' : '#6b7280'
  const bg = isDark ? '#0f0f0f' : '#ffffff'

  const yTicks = Array.from(new Set([pMin, pMin / 2, 0, pMax / 2, pMax]))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      <defs>
        <clipPath id="dn-profit-clip">
          <rect x={PAD.left} y={PAD.top} width={cW} height={Math.max(0, y0 - PAD.top)} />
        </clipPath>
        <clipPath id="dn-loss-clip">
          <rect x={PAD.left} y={y0} width={cW} height={Math.max(0, PAD.top + cH - y0)} />
        </clipPath>
      </defs>

      <rect x={PAD.left} y={PAD.top} width={cW} height={cH} fill={bg} />

      {yTicks.map((v, i) => (
        <line key={i} x1={PAD.left} x2={PAD.left + cW} y1={sy(v)} y2={sy(v)}
          stroke={grid} strokeWidth={1} strokeDasharray={v === 0 ? 'none' : '3,3'} />
      ))}

      {/* Green profit area */}
      <polygon points={polyPoints} fill="rgba(34,197,94,0.18)" clipPath="url(#dn-profit-clip)" />
      <polyline points={pts} fill="none" stroke="#22c55e" strokeWidth={2} clipPath="url(#dn-profit-clip)" />

      {/* Red loss area */}
      <polygon points={polyPoints} fill="rgba(239,68,68,0.18)" clipPath="url(#dn-loss-clip)" />
      <polyline points={pts} fill="none" stroke="#ef4444" strokeWidth={2} clipPath="url(#dn-loss-clip)" />

      {/* Zero line */}
      <line x1={PAD.left} x2={PAD.left + cW} y1={y0} y2={y0}
        stroke={isDark ? '#555' : '#9ca3af'} strokeWidth={1} />

      {/* Current spot line */}
      {spotPrice > 0 && spotPrice >= sMin && spotPrice <= sMax && (
        <g>
          <line x1={sx(spotPrice)} x2={sx(spotPrice)} y1={PAD.top} y2={PAD.top + cH}
            stroke="#3b82f6" strokeWidth={1.5} strokeDasharray="5,4" />
          <text x={sx(spotPrice) + 4} y={PAD.top + 12} fill="#3b82f6" fontSize={10}>Spot</text>
        </g>
      )}

      {/* Breakeven lines */}
      {breakevens.map((be, i) => be >= sMin && be <= sMax && (
        <g key={i}>
          <line x1={sx(be)} x2={sx(be)} y1={PAD.top} y2={PAD.top + cH}
            stroke="#f59e0b" strokeWidth={1} strokeDasharray="4,3" />
          <text x={sx(be) + 3} y={PAD.top + cH - 4} fill="#f59e0b" fontSize={9}>
            {be.toLocaleString()}
          </text>
        </g>
      ))}

      {/* Y-axis labels */}
      {yTicks.map((v, i) => (
        <text key={i} x={PAD.left - 6} y={sy(v) + 4} textAnchor="end" fill={axisText} fontSize={10}>
          {v >= 0 ? '+' : ''}{(v / 1000).toFixed(0)}k
        </text>
      ))}

      {/* X-axis labels */}
      {[0, 0.25, 0.5, 0.75, 1].map((t, i) => (
        <text key={i} x={sx(sMin + t * sRange)} y={H - 6} textAnchor="middle" fill={axisText} fontSize={10}>
          {Math.round(sMin + t * sRange).toLocaleString()}
        </text>
      ))}
      <text x={W / 2} y={H - 0} textAnchor="middle" fill={axisText} fontSize={10}>Spot at Expiry</text>
    </svg>
  )
}

// ── Greek summary card ─────────────────────────────────────────────────────
function GreekCard({ label, value, d = 2, colorize = false, icon, sub, unit }: {
  label: string; value: number | null | undefined; d?: number
  colorize?: boolean; icon?: React.ReactNode; sub?: string; unit?: string
}) {
  const cls = colorize
    ? value == null ? '' : value > 0 ? 'text-green-500' : value < 0 ? 'text-red-500' : ''
    : ''
  return (
    <Card>
      <CardContent className="pt-4 pb-4">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</span>
          {icon && <span className="text-muted-foreground">{icon}</span>}
        </div>
        <div className={`text-2xl font-bold tabular-nums ${cls}`}>
          {value == null ? '—' : (value >= 0 ? '+' : '') + value.toFixed(d)}
          {unit && <span className="text-sm font-normal text-muted-foreground ml-1">{unit}</span>}
        </div>
        {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  )
}

// ── Leg table row ──────────────────────────────────────────────────────────
function LegRow({ leg }: { leg: DeltaNeutralLeg }) {
  const isShort = leg.quantity < 0
  const pnlCls = leg.pnl >= 0 ? 'text-green-500' : 'text-red-500'
  const dCls = leg.net_delta != null
    ? Math.abs(leg.net_delta) < 0.05 ? 'text-green-500' : 'text-amber-500' : ''
  return (
    <tr className="border-b border-border hover:bg-muted/30">
      <td className="px-3 py-2 text-xs font-mono">{leg.symbol}</td>
      <td className="px-3 py-2 text-center">
        <Badge variant={leg.option_type === 'CE' ? 'default' : 'secondary'} className="text-xs px-1.5">
          {leg.option_type}
        </Badge>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{leg.strike.toLocaleString()}</td>
      <td className="px-3 py-2 text-center">
        <Badge variant={isShort ? 'destructive' : 'outline'} className="text-xs px-1.5">
          {isShort ? 'SHORT' : 'LONG'} {Math.abs(leg.quantity)}
        </Badge>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{fmt(leg.average_price)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{fmt(leg.ltp)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{leg.iv != null ? `${fmt(leg.iv)}%` : '—'}</td>
      <td className={`px-3 py-2 text-right tabular-nums ${dCls}`}>{fmt(leg.net_delta, 4)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{fmt(leg.net_gamma, 4)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-amber-500">{fmtSign(leg.net_theta, 1)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-blue-500">{fmtSign(leg.net_vega, 1)}</td>
      <td className={`px-3 py-2 text-right tabular-nums font-semibold ${pnlCls}`}>{fmtSign(leg.pnl)}</td>
    </tr>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────
export default function DeltaNeutral() {
  const { mode } = useThemeStore()
  const isDark = mode === 'dark'
  const { fnoExchanges, defaultFnoExchange, defaultUnderlyings } = useSupportedExchanges()

  const [exchange, setExchange] = useState(defaultFnoExchange || 'NFO')
  const [underlyings, setUnderlyings] = useState<string[]>([])
  const [ulOpen, setUlOpen] = useState(false)
  const [underlying, setUnderlying] = useState('')
  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [data, setData] = useState<DeltaNeutralResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const reqRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Sync exchange when broker capabilities load
  useEffect(() => {
    if (defaultFnoExchange) {
      setExchange(prev =>
        fnoExchanges.some(x => x.value === prev) ? prev : defaultFnoExchange
      )
    }
  }, [defaultFnoExchange, fnoExchanges])

  // Fetch underlyings when exchange changes
  useEffect(() => {
    if (!exchange) return
    const defaults = defaultUnderlyings[exchange] || []
    setUnderlyings(defaults)
    setUnderlying(defaults[0] || '')
    setExpiries([])
    setExpiry('')
    setData(null)

    let cancelled = false
    deltaNeutralApi.getUnderlyings(exchange)
      .then(r => {
        if (cancelled || r.status !== 'success' || !r.underlyings.length) return
        setUnderlyings(r.underlyings)
        setUnderlying(prev => r.underlyings.includes(prev) ? prev : r.underlyings[0])
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [exchange]) // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch expiries when underlying changes
  useEffect(() => {
    if (!underlying || !exchange) return
    setExpiries([])
    setExpiry('')
    setData(null)

    let cancelled = false
    deltaNeutralApi.getExpiries(exchange, underlying)
      .then(r => {
        if (cancelled || r.status !== 'success' || !r.expiries.length) return
        setExpiries(r.expiries)
        setExpiry(r.expiries[0])
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [underlying, exchange])

  const load = useCallback(async (silent = false) => {
    if (!exchange) return
    const id = ++reqRef.current
    if (!silent) setLoading(true)
    try {
      const resp = await deltaNeutralApi.getPortfolio({
        underlying: underlying || '',
        exchange,
        expiry_date: expiry ? convertExpiryForAPI(expiry) : '',
      })
      if (id !== reqRef.current) return
      if (resp.status === 'error') {
        if (!silent) showToast.error(resp.message || 'Failed to fetch portfolio')
        return
      }
      setData(resp)
      setUpdatedAt(new Date())
    } catch {
      if (id !== reqRef.current) return
      if (!silent) showToast.error('Request failed — check your session')
    } finally {
      if (id === reqRef.current && !silent) setLoading(false)
    }
  }, [underlying, exchange, expiry])

  // Auto-load when expiry first populates
  useEffect(() => {
    if (expiry) load(false)
  }, [expiry]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (autoRefresh) timerRef.current = setInterval(() => load(true), AUTO_REFRESH_INTERVAL)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [autoRefresh, load])

  const port   = data?.portfolio
  const legs   = data?.legs ?? []
  const payoff = data?.payoff ?? []
  const spot   = data?.spot_price ?? 0
  const bes    = data?.breakevens ?? []
  const maxPnl = payoff.length ? Math.max(...payoff.map(p => p.pnl)) : 0
  const minPnl = payoff.length ? Math.min(...payoff.map(p => p.pnl)) : 0
  const score  = port != null
    ? Math.max(0, Math.round(100 - Math.min(100, Math.abs(port.net_delta) * 200)))
    : null

  return (
    <div className="py-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Delta Neutral Monitor</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Live portfolio Greeks, payoff at expiry and per-leg breakdown for algo-managed strategies
          </p>
        </div>
        {updatedAt && (
          <div className="text-xs text-muted-foreground self-end">
            Updated {updatedAt.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })} IST
          </div>
        )}
      </div>

      {/* Controls */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex flex-wrap items-end gap-3">

            {/* Exchange */}
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground font-medium">Exchange</label>
              <Select value={exchange} onValueChange={v => { setExchange(v); setData(null) }}>
                <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {fnoExchanges.map(x => (
                    <SelectItem key={x.value} value={x.value}>{x.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Underlying */}
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground font-medium">Underlying</label>
              <Popover open={ulOpen} onOpenChange={setUlOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" role="combobox" className="w-40 justify-between">
                    {underlying || 'Select…'}
                    <ChevronsUpDown className="ml-2 h-4 w-4 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-40 p-0">
                  <Command>
                    <CommandInput placeholder="Search…" />
                    <CommandList>
                      <CommandEmpty>Not found</CommandEmpty>
                      <CommandGroup>
                        {underlyings.map(u => (
                          <CommandItem key={u} value={u} onSelect={() => { setUnderlying(u); setUlOpen(false) }}>
                            <Check className={`mr-2 h-4 w-4 ${u === underlying ? 'opacity-100' : 'opacity-0'}`} />
                            {u}
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>

            {/* Expiry */}
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground font-medium">Expiry</label>
              <Select value={expiry} onValueChange={v => setExpiry(v === "__all__" ? "" : v)}>
                <SelectTrigger className="w-36"><SelectValue placeholder="Any expiry" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">Any expiry</SelectItem>
                  {expiries.map(e => <SelectItem key={e} value={e}>{e}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <Button onClick={() => load(false)} disabled={loading} className="h-9">
              <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
              {loading ? 'Loading…' : 'Load'}
            </Button>

            <Button
              variant={autoRefresh ? 'default' : 'outline'}
              onClick={() => setAutoRefresh(v => !v)}
              className="h-9"
            >
              <Activity className={`h-4 w-4 mr-2 ${autoRefresh ? 'animate-pulse' : ''}`} />
              Auto {autoRefresh ? 'ON' : 'OFF'}
            </Button>

            {spot > 0 && (
              <div className="ml-auto text-sm">
                <span className="text-muted-foreground">Spot: </span>
                <span className="font-semibold tabular-nums">{spot.toLocaleString()}</span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Loading skeleton */}
      {loading && !data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="pt-4 pb-4 space-y-2">
                  <div className="h-3 bg-muted rounded animate-pulse w-16" />
                  <div className="h-7 bg-muted rounded animate-pulse w-24" />
                  <div className="h-2 bg-muted rounded animate-pulse w-20" />
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              <RefreshCw className="h-7 w-7 mx-auto mb-2 animate-spin opacity-40" />
              <p className="text-sm">Fetching positions and computing Greeks…</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* No positions state */}
      {!loading && data && legs.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            <Activity className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-base font-medium">No open option positions</p>
            <p className="text-sm mt-1 opacity-70">
              {data.message || 'No option positions found for the selected filter.'}
            </p>
            <p className="text-xs mt-2 opacity-50">
              Start the Delta Neutral strategy from /python to begin trading.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Dashboard */}
      {!loading && port && legs.length > 0 && (
        <>
          {/* Greek cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <GreekCard label="Net Delta" value={port.net_delta} d={4} colorize
              icon={<Zap className="h-4 w-4" />}
              sub={score != null ? `Balance: ${score}/100` : undefined} />
            <GreekCard label="Net Gamma" value={port.net_gamma} d={4}
              icon={<Activity className="h-4 w-4" />} sub="Convexity risk" />
            <GreekCard label="Net Theta" value={port.net_theta} d={1} colorize
              icon={<TrendingDown className="h-4 w-4" />} sub="Daily decay" unit="₹/day" />
            <GreekCard label="Net Vega" value={port.net_vega} d={1}
              icon={<TrendingUp className="h-4 w-4" />} sub="IV sensitivity" unit="₹/1%" />
            <GreekCard label="Net Premium" value={port.net_premium} d={0} colorize
              sub="Received – Paid" unit="₹" />
            <GreekCard label="Total P&L" value={port.total_pnl} d={0} colorize
              sub="Live unrealised" unit="₹" />
          </div>

          {/* Delta health bar */}
          {score != null && (
            <Card>
              <CardContent className="py-3">
                <div className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground w-32">Delta Balance</span>
                  <div className="flex-1 h-2.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${score > 80 ? 'bg-green-500' : score > 50 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${score}%` }}
                    />
                  </div>
                  <span className={`text-xs font-semibold w-12 text-right ${
                    score > 80 ? 'text-green-500' : score > 50 ? 'text-amber-500' : 'text-red-500'}`}>
                    {score}/100
                  </span>
                  <Badge variant={score > 80 ? 'default' : score > 50 ? 'secondary' : 'destructive'}
                    className="text-xs">
                    {score > 80 ? 'Neutral' : score > 50 ? 'Near Neutral' : 'Hedge Needed'}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Payoff chart */}
          {payoff.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <CardTitle className="text-base">Payoff at Expiry</CardTitle>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span>Max: <span className="text-green-500 font-semibold">+{maxPnl.toFixed(0)}</span></span>
                    <span>Min: <span className="text-red-500 font-semibold">{minPnl.toFixed(0)}</span></span>
                    {bes.length > 0 && (
                      <span>BEs: <span className="font-semibold">{bes.map(b => b.toLocaleString()).join(' / ')}</span></span>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <PayoffChart payoff={payoff} spotPrice={spot} breakevens={bes} isDark={isDark} />
              </CardContent>
            </Card>
          )}

          {/* Position table */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                Open Positions ({legs.length} leg{legs.length !== 1 ? 's' : ''})
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/50">
                      {['Symbol','Type','Strike','Position','Avg','LTP','IV','Δ Net','Γ Net','Θ Net','V Net','P&L ₹']
                        .map((h, i) => (
                          <th key={i} className={`px-3 py-2 font-medium text-xs text-muted-foreground ${
                            i >= 4 ? 'text-right' : i === 1 || i === 3 ? 'text-center' : 'text-left'}`}>
                            {h}
                          </th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {legs.map(leg => <LegRow key={leg.symbol} leg={leg} />)}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-border bg-muted/30 font-semibold text-xs">
                      <td className="px-3 py-2 text-muted-foreground" colSpan={7}>Portfolio Total</td>
                      <td className={`px-3 py-2 text-right tabular-nums ${
                        Math.abs(port.net_delta) < 0.05 ? 'text-green-500' : 'text-amber-500'}`}>
                        {fmtSign(port.net_delta, 4)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtSign(port.net_gamma, 4)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-amber-500">
                        {fmtSign(port.net_theta, 1)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-blue-500">
                        {fmtSign(port.net_vega, 1)}
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums font-bold ${
                        port.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                        {fmtSign(port.total_pnl, 0)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* Initial empty state (no fetch yet) */}
      {!loading && !data && (
        <Card>
          <CardContent className="py-16 text-center text-muted-foreground">
            <Activity className="h-10 w-10 mx-auto mb-3 opacity-30" />
            <p className="text-base font-medium">Delta Neutral Monitor v2</p>
            <p className="text-sm mt-1 opacity-70">
              Select exchange + underlying above, then click <strong>Load</strong>
            </p>
            <p className="text-xs mt-2 opacity-50">
              Reads live open option positions and computes portfolio Greeks + payoff chart.
            </p>
          </CardContent>
        </Card>
      )}

    </div>
  )
}
