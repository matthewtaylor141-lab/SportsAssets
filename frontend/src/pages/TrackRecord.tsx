import { useEffect, useMemo, useRef, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { PnlCalendar } from '../components/PnlCalendar'
import { api } from '../lib/api'
import { fmtCents, fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'

/* The AI trader's public track record, presented like a sportsbook account.
 *
 * Every number on this page is read from the engine's own records via the
 * API — nothing is typed in, and the page renders exactly what the ledger
 * supports. When the settled sample is below the reporting threshold the
 * page SAYS so instead of dressing a hot streak as a record: the whole
 * point of a track record is that it can be trusted. */

interface EngineFill {
  id: number
  ts: string
  venue: string
  market_id: string
  outcome_id: string
  league: string | null
  band: string | null
  limit_price: number
  size_usd: number
  fair_value: number | null
  edge: number | null
  settled: boolean
  payout: number | null
  pnl: number | null
  settled_at: string | null
  market_title: string | null
  sport: string | null
  outcome: string | null
}

interface EngineSummary {
  totals: {
    fills: number
    settled: number
    staked: number
    settled_staked: number
    pnl: number
    roi: number | null
  }
  daily: { date: string; pnl: number; volume: number; trades: number }[]
}

const MIN_SETTLED_FOR_RECORD = 30 // mirrors edge.reporting.figures

const SPORT_ICON: Record<string, string> = {
  baseball: '⚾', basketball: '🏀', football: '🏈', soccer: '⚽',
  hockey: '🏒', tennis: '🎾', golf: '⛳', mma: '🥊', boxing: '🥊',
}

function sportIcon(s: string | null): string {
  if (!s) return '🎯'
  const k = s.toLowerCase()
  for (const key of Object.keys(SPORT_ICON)) if (k.includes(key)) return SPORT_ICON[key]
  return '🎯'
}

function sportName(f: EngineFill): string {
  return f.sport || f.league?.toUpperCase() || 'Other'
}

/** Animated count-up for hero figures — numbers should feel alive, never lie. */
function useCountUp(target: number, ms = 900): number {
  const [v, setV] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const begin = from.current
    let raf = 0
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / ms)
      const eased = 1 - Math.pow(1 - k, 3)
      setV(begin + (target - begin) * eased)
      if (k < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

/** Cumulative-P&L equity curve with crosshair + tooltip. One series, one axis. */
function EquityCurve({ daily }: { daily: EngineSummary['daily'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const pts = useMemo(() => {
    let acc = 0
    return daily.map((d) => ({ date: d.date, cum: (acc += d.pnl), day: d.pnl }))
  }, [daily])
  if (pts.length < 2) return null

  const W = 720, H = 180, PAD = 12
  const min = Math.min(0, ...pts.map((p) => p.cum))
  const max = Math.max(0, ...pts.map((p) => p.cum))
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - PAD * 2)
  const y = (v: number) => H - PAD - ((v - min) / (max - min || 1)) * (H - PAD * 2)
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' ')
  const area = `${path} L${x(pts.length - 1).toFixed(1)},${y(0)} L${x(0)},${y(0)} Z`
  const up = pts[pts.length - 1].cum >= 0
  const stroke = up ? 'var(--good)' : 'var(--critical)'
  const h = hover !== null ? pts[hover] : null

  return (
    <div className="tr-curve" role="img" aria-label="Cumulative realized profit by day">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.target as SVGElement).closest('svg')!.getBoundingClientRect()
          const fx = ((e.clientX - r.left) / r.width) * W
          setHover(Math.max(0, Math.min(pts.length - 1,
            Math.round(((fx - PAD) / (W - PAD * 2)) * (pts.length - 1)))))
        }}
      >
        <defs>
          <linearGradient id="tr-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="var(--baseline)" strokeWidth="1" />
        <path d={area} fill="url(#tr-fill)" />
        <path d={path} fill="none" stroke={stroke} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {h && (
          <g>
            <line x1={x(hover!)} x2={x(hover!)} y1={PAD} y2={H - PAD}
              stroke="var(--border-strong)" strokeWidth="1" />
            <circle cx={x(hover!)} cy={y(h.cum)} r="4" fill={stroke}
              stroke="var(--surface)" strokeWidth="2" />
          </g>
        )}
      </svg>
      <div className="tr-curve-tip">
        {h ? (
          <>
            <span className="mono">{h.date}</span>
            <span className={h.day >= 0 ? 'pos' : 'neg'}>day {fmtSignedUsd(h.day)}</span>
            <span className={h.cum >= 0 ? 'pos' : 'neg'}>total {fmtSignedUsd(h.cum)}</span>
          </>
        ) : (
          <span className="muted">hover for daily detail</span>
        )}
      </div>
    </div>
  )
}

/** Per-sport settled P&L — thin horizontal bars, polarity-colored, labeled. */
function SportBreakdown({ fills }: { fills: EngineFill[] }) {
  const rows = useMemo(() => {
    const by = new Map<string, { pnl: number; staked: number; n: number; wins: number; icon: string }>()
    for (const f of fills) {
      if (!f.settled || f.pnl === null) continue
      const key = sportName(f)
      const r = by.get(key) || { pnl: 0, staked: 0, n: 0, wins: 0, icon: sportIcon(f.sport || f.league) }
      r.pnl += f.pnl
      r.staked += f.size_usd
      r.n += 1
      if (f.pnl > 0) r.wins += 1
      by.set(key, r)
    }
    return [...by.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [fills])
  if (!rows.length) return <EmptyState>No settled trades yet — the board fills in as games resolve.</EmptyState>

  const maxAbs = Math.max(...rows.map(([, r]) => Math.abs(r.pnl)), 0.01)
  return (
    <div className="tr-sports">
      {rows.map(([name, r]) => (
        <div key={name} className="tr-sport-row" title={`${name}: ${r.n} settled, ${r.wins} won, staked ${fmtUsd(r.staked, 2)}`}>
          <span className="tr-sport-name">
            <span className="tr-sport-ico">{r.icon}</span> {name}
            <span className="muted mono"> {r.wins}–{r.n - r.wins}</span>
          </span>
          <div className="tr-sport-bar">
            <div className="tr-sport-zero" />
            <div
              className={`tr-sport-fill ${r.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
              style={{
                width: `${(Math.abs(r.pnl) / maxAbs) * 50}%`,
                [r.pnl >= 0 ? 'left' : 'right' as any]: '50%',
              }}
            />
          </div>
          <span className={`tr-sport-val mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtSignedUsd(r.pnl)}
          </span>
        </div>
      ))}
    </div>
  )
}

type LedgerFilter = 'all' | 'won' | 'lost' | 'open'

export function TrackRecord() {
  const [fills, setFills] = useState<EngineFill[] | null>(null)
  const [summary, setSummary] = useState<EngineSummary | null>(null)
  const [filter, setFilter] = useState<LedgerFilter>('all')
  const [sport, setSport] = useState<string>('all')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let dead = false
    const load = () =>
      Promise.all([
        api<EngineFill[]>('/api/engine/fills?limit=500'),
        api<EngineSummary>('/api/engine/summary'),
      ])
        .then(([f, s]) => {
          if (dead) return
          setFills(f)
          setSummary(s)
          setErr(null)
        })
        .catch((e) => !dead && setErr(String(e)))
    load()
    const t = setInterval(load, 30_000) // live account feel: refresh quietly
    return () => { dead = true; clearInterval(t) }
  }, [])

  const settled = useMemo(() => (fills || []).filter((f) => f.settled && f.pnl !== null), [fills])
  const wins = settled.filter((f) => (f.pnl || 0) > 0)
  const netPnl = summary?.totals.pnl ?? settled.reduce((a, f) => a + (f.pnl || 0), 0)
  const deployed = summary?.totals.staked ?? 0
  const settledStaked = summary?.totals.settled_staked ?? 0
  const roi = settledStaked > 0 ? netPnl / settledStaked : null
  const openCount = (fills || []).filter((f) => !f.settled).length
  const earlySample = settled.length < MIN_SETTLED_FOR_RECORD

  const heroPnl = useCountUp(netPnl)
  const heroDeployed = useCountUp(deployed)

  const sports = useMemo(
    () => [...new Set((fills || []).map((f) => sportName(f)))].sort(),
    [fills],
  )
  const ledger = useMemo(() => {
    let rows = fills || []
    if (filter === 'won') rows = rows.filter((f) => f.settled && (f.pnl || 0) > 0)
    if (filter === 'lost') rows = rows.filter((f) => f.settled && (f.pnl || 0) <= 0)
    if (filter === 'open') rows = rows.filter((f) => !f.settled)
    if (sport !== 'all') rows = rows.filter((f) => sportName(f) === sport)
    return rows.slice(0, 120)
  }, [fills, filter, sport])

  if (err) return <EmptyState>{`API unreachable: ${err}`}</EmptyState>
  if (!fills || !summary) return <EmptyState>Loading the record…</EmptyState>

  return (
    <div className="page tr-page">
      {/* ── hero: the account card ─────────────────────────────────── */}
      <div className="tr-hero">
        <div className="tr-hero-head">
          <div className="tr-ident">
            <span className="tr-bot">🤖</span>
            <div>
              <div className="tr-name">BETTOR<span>EDGE</span> AI</div>
              <div className="tr-sub muted">
                autonomous trader · every figure below is read from its own ledger
              </div>
            </div>
          </div>
          <div className="tr-live">
            <span className="tr-pulse" /> SYNCED
          </div>
        </div>

        <div className="tr-hero-grid">
          <div className="tr-stat tr-stat-main">
            <div className="tr-stat-label">NET P&amp;L (settled)</div>
            <div className={`tr-stat-value ${netPnl >= 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(heroPnl)}
            </div>
            <div className="tr-stat-foot muted">
              {wins.length}W – {settled.length - wins.length}L
              {settled.length > 0 && <> · {fmtPct(wins.length / settled.length, 0)} win rate</>}
            </div>
          </div>
          <div className="tr-stat">
            <div className="tr-stat-label">CAPITAL DEPLOYED</div>
            <div className="tr-stat-value">{fmtUsd(heroDeployed, 2)}</div>
            <div className="tr-stat-foot muted">{fills.length.toLocaleString()} AI trades · {openCount} open</div>
          </div>
          <div className="tr-stat">
            <div className="tr-stat-label">ROI ON TRADED CAPITAL</div>
            <div className={`tr-stat-value ${roi === null ? '' : roi >= 0 ? 'pos' : 'neg'}`}>
              {roi === null ? '—' : fmtPct(roi)}
            </div>
            <div className="tr-stat-foot muted">
              on {fmtUsd(settledStaked, 2)} settled stake
            </div>
          </div>
        </div>

        {earlySample && (
          <div className="tr-honesty">
            ⚖️ EARLY SAMPLE — {settled.length} settled of the {MIN_SETTLED_FOR_RECORD} this
            record requires before its return means anything. The AI applies the same
            rule to itself and will not size up until the record is earned.
          </div>
        )}

        <EquityCurve daily={summary.daily} />
      </div>

      {/* ── calendar + sports ──────────────────────────────────────── */}
      <div className="tr-columns">
        <div className="card">
          <div className="card-title">DAILY P&amp;L</div>
          {summary.daily.length ? (
            <PnlCalendar days={summary.daily.map((d) => ({
              date: d.date, pnl: d.pnl, volume: d.volume, trades: d.trades,
            }))} />
          ) : (
            <EmptyState>First settlement day pending.</EmptyState>
          )}
        </div>
        <div className="card">
          <div className="card-title">P&amp;L BY SPORT (settled)</div>
          <SportBreakdown fills={fills} />
        </div>
      </div>

      {/* ── the ledger: a sportsbook bet history ───────────────────── */}
      <div className="card">
        <div className="tr-ledger-head">
          <div className="card-title">AI TRADE LEDGER</div>
          <div className="tr-filters">
            {(['all', 'won', 'lost', 'open'] as LedgerFilter[]).map((f) => (
              <button
                key={f}
                className={`tr-chipbtn ${filter === f ? 'on' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f.toUpperCase()}
              </button>
            ))}
            <select className="tr-select" value={sport} onChange={(e) => setSport(e.target.value)}>
              <option value="all">All sports</option>
              {sports.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>

        {ledger.length === 0 ? (
          <EmptyState>Nothing matches this filter yet.</EmptyState>
        ) : (
          <div className="tr-slips">
            {ledger.map((f) => {
              const won = f.settled && (f.pnl || 0) > 0
              const status = !f.settled ? 'OPEN' : won ? 'WON' : 'LOST'
              const toWin = f.limit_price > 0 ? f.size_usd / f.limit_price - f.size_usd : 0
              return (
                <div key={f.id} className={`tr-slip ${status.toLowerCase()}`}>
                  <div className="tr-slip-edge" aria-hidden />
                  <div className="tr-slip-main">
                    <div className="tr-slip-top">
                      <span className="tr-slip-sport">{sportIcon(f.sport || f.league)}</span>
                      <span className="tr-slip-title">
                        {f.market_title || f.market_id}
                      </span>
                      <span className={`tr-chip ${status.toLowerCase()}`}>
                        {status === 'OPEN' ? '● OPEN' : status === 'WON' ? '✓ WON' : '✕ LOST'}
                      </span>
                    </div>
                    <div className="tr-slip-mid">
                      <span className="tr-slip-outcome">{f.outcome || f.outcome_id}</span>
                      {f.band && <span className="tr-tag mono">{f.band}</span>}
                      {f.edge !== null && (
                        <span className="tr-tag mono" title="Model edge at entry">
                          edge +{(f.edge * 100).toFixed(1)}¢
                        </span>
                      )}
                    </div>
                    <div className="tr-slip-nums mono">
                      <span title="Entry price">@{fmtCents(f.limit_price)}</span>
                      <span title="Stake">stake {fmtUsd(f.size_usd, 2)}</span>
                      <span title="Payout if won" className="muted">to win {fmtUsd(toWin, 2)}</span>
                      <span className={`tr-slip-pnl ${!f.settled ? 'muted' : won ? 'pos' : 'neg'}`}>
                        {f.settled ? fmtSignedUsd(f.pnl) : '· · ·'}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        <div className="tr-foot muted">
          Showing {ledger.length} of {fills.length} AI-placed trades · source: engine ledger,
          refreshed every 30s · no manual trades appear here.
        </div>
      </div>
    </div>
  )
}
