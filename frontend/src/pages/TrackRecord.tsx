import { useEffect, useMemo, useRef, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { PnlCalendar } from '../components/PnlCalendar'
import { LiveToday } from '../components/LiveToday'
import { ReportsCard } from '../components/ReportsCard'
import { fmtAgo, fmtCents, fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import { KalshiOpen, SINCE, TRRow, useKalshiOpen, useTrackRecord,
  useVenueTruth, VenueTruthData } from '../lib/record'

/* The AI trader's account, told from the venue's own records. Every number
 * is fetched from /api/track-record — real positions, real entry prices
 * (the venue's own VWAP), real settlements — windowed from Aug 1. When the
 * settled sample is thin the page says so; a record must be trustable
 * before it is impressive. */

const MIN_SETTLED = 30

/* Pointer-tracked 3D tilt for the hero tiles: writes --rx/--ry custom
 * properties the CSS consumes under a perspective parent. Pure transform,
 * so it composites on the GPU; hover-only media query keeps it off touch. */
function tilt(e: React.MouseEvent<HTMLDivElement>) {
  const el = e.currentTarget
  const r = el.getBoundingClientRect()
  const px = (e.clientX - r.left) / r.width - 0.5
  const py = (e.clientY - r.top) / r.height - 0.5
  el.style.setProperty('--rx', `${(-py * 5).toFixed(2)}deg`)
  el.style.setProperty('--ry', `${(px * 7).toFixed(2)}deg`)
}
function untilt(e: React.MouseEvent<HTMLDivElement>) {
  e.currentTarget.style.setProperty('--rx', '0deg')
  e.currentTarget.style.setProperty('--ry', '0deg')
}

function useCountUp(target: number, ms = 1100): number {
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

/** Scrolling ticker of the latest settled results — the sportsbook pulse. */
function ResultsTicker({ rows }: { rows: TRRow[] }) {
  const settled = rows.filter((r) => r.settled && r.pnl !== null).slice(0, 24)
  if (settled.length < 3) return null
  const items = [...settled, ...settled] // seamless loop
  return (
    <div className="tk-wrap" aria-hidden>
      <div className="tk-track">
        {items.map((r, i) => (
          <span key={i} className="tk-item">
            <span>{r.icon}</span>
            <span className="tk-title">{r.outcome || r.title}</span>
            <span className={`mono ${r.pnl! > 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(r.pnl)}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

function EquityCurve({ daily }: { daily: { date: string; pnl: number }[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const pts = useMemo(() => {
    let acc = 0
    return daily.map((d) => ({ date: d.date, cum: (acc += d.pnl), day: d.pnl }))
  }, [daily])
  if (pts.length < 2) return null
  // A daily-P&L bar band lives under the equity line — same x scale, its
  // own tiny magnitude scale — so one glance gives both the journey and
  // the size of each day's swing.
  const W = 720, H = 190, PAD = 12, BAND = 30
  const min = Math.min(0, ...pts.map((p) => p.cum))
  const max = Math.max(0.01, ...pts.map((p) => p.cum))
  const maxDay = Math.max(0.01, ...pts.map((p) => Math.abs(p.day)))
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - PAD * 2)
  const y = (v: number) =>
    H - PAD - BAND - ((v - min) / (max - min || 1)) * (H - PAD * 2 - BAND)
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' ')
  const up = pts[pts.length - 1].cum >= 0
  const stroke = up ? 'var(--good)' : 'var(--critical)'
  const h = hover !== null ? pts[hover] : null
  // Generous over-estimate of path length: the draw animation only needs
  // dashoffset >= true length, and measuring via ref costs a layout pass.
  const drawLen = Math.ceil((W + H) * 1.6)
  const endX = x(pts.length - 1)
  const endY = y(pts[pts.length - 1].cum)
  const barW = Math.max(1.5, ((W - PAD * 2) / pts.length) * 0.55)
  const barBase = H - 6
  return (
    <div className="tr-curve" role="img" aria-label="Cumulative realized profit by day, with daily P&L bars">
      <div className="tr-curve-stage">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          const fx = ((e.clientX - r.left) / r.width) * W
          setHover(Math.max(0, Math.min(pts.length - 1,
            Math.round(((fx - PAD) / (W - PAD * 2)) * (pts.length - 1)))))
        }}>
        <defs>
          <linearGradient id="tr-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
          <linearGradient id="tr-stroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.45" />
            <stop offset="72%" stopColor={stroke} />
            <stop offset="100%" stopColor={up ? '#7ee2a0' : '#ff9d9d'} />
          </linearGradient>
        </defs>
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="var(--baseline)" />
        {pts.map((p, i) => {
          const bh = Math.max(1.5, (Math.abs(p.day) / maxDay) * (BAND - 10))
          return (
            <rect key={p.date} className="tr-curve-bar"
              x={x(i) - barW / 2} y={barBase - bh} width={barW} height={bh}
              rx={Math.min(1.5, barW / 2)}
              fill={p.day >= 0 ? 'var(--good)' : 'var(--critical)'}
              opacity={hover === i ? 0.95 : 0.4} />
          )
        })}
        <path d={`${path} L${x(pts.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill="url(#tr-fill)" />
        <path d={path} fill="none" stroke="url(#tr-stroke)" strokeWidth="2.5"
          strokeLinejoin="round" strokeLinecap="round"
          className="tr-curve-draw"
          strokeDasharray={drawLen}
          style={{ ['--curve-len' as string]: drawLen }} />
        <circle className="tr-curve-dot" cx={endX} cy={endY} r="4"
          fill={stroke} stroke="var(--surface)" strokeWidth="1.5" />
        {h && (
          <g>
            <line x1={x(hover!)} x2={x(hover!)} y1={PAD} y2={H - PAD}
              stroke="var(--border-strong)" />
            <circle cx={x(hover!)} cy={y(h.cum)} r="4.5" fill={stroke}
              stroke="var(--surface)" strokeWidth="2" />
          </g>
        )}
      </svg>
      {/* HTML overlays track the stretched SVG by percentage so text never
          distorts under preserveAspectRatio="none". */}
      <span className={`tr-curve-badge ${up ? 'pos' : 'neg'}`}
        style={{ left: `${(endX / W) * 100}%`, top: `${(endY / H) * 100}%` }}>
        {fmtSignedUsd(pts[pts.length - 1].cum)}
      </span>
      {max > 0.01 && (
        <span className="tr-curve-y" style={{ top: `${(y(max) / H) * 100}%` }}>
          {fmtSignedUsd(max)}
        </span>
      )}
      {min < 0 && (
        <span className="tr-curve-y" style={{ top: `${(y(min) / H) * 100}%` }}>
          {fmtSignedUsd(min)}
        </span>
      )}
      </div>
      <div className="tr-curve-tip">
        {h ? (
          <>
            <span className="mono">{h.date}</span>
            <span className={h.day >= 0 ? 'pos' : 'neg'}>day {fmtSignedUsd(h.day)}</span>
            <span className={h.cum >= 0 ? 'pos' : 'neg'}>cumulative {fmtSignedUsd(h.cum)}</span>
          </>
        ) : <span className="muted">hover for daily detail</span>}
      </div>
    </div>
  )
}

function SportBreakdown({ rows }: { rows: TRRow[] }) {
  const sports = useMemo(() => {
    const by = new Map<string, { pnl: number; staked: number; n: number; wins: number; open: number; icon: string }>()
    for (const r of rows) {
      const b = by.get(r.sport) || { pnl: 0, staked: 0, n: 0, wins: 0, open: 0, icon: r.icon }
      if (r.settled && r.pnl !== null) {
        b.pnl += r.pnl; b.n += 1
        b.staked += r.stake
        if (r.pnl > 0) b.wins += 1
      } else b.open += 1
      by.set(r.sport, b)
    }
    return [...by.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [rows])
  if (!sports.length) return <EmptyState>The board fills in as games settle.</EmptyState>
  const maxAbs = Math.max(...sports.map(([, r]) => Math.abs(r.pnl)), 0.01)
  return (
    <div className="tr-sports">
      {sports.map(([name, r]) => (
        <div key={name} className="tr-sport-row"
          title={`${name}: ${r.n} settled (${r.wins} won), ${r.open} open, staked ${fmtUsd(r.staked, 2)}`}>
          <span className="tr-sport-name">
            <span className="tr-sport-ico">{r.icon}</span> {name}
            <span className="muted mono"> {r.wins}–{r.n - r.wins}{r.open ? ` · ${r.open} open` : ''}</span>
          </span>
          <div className="tr-sport-bar">
            <div className="tr-sport-zero" />
            <div className={`tr-sport-fill ${r.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
              style={{ width: `${(Math.abs(r.pnl) / maxAbs) * 50}%`,
                       ['--org' as string]: r.pnl >= 0 ? 'left' : 'right',
                       [r.pnl >= 0 ? 'left' : 'right' as any]: '50%' }} />
          </div>
          <span className={`tr-sport-val mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtSignedUsd(r.pnl)}
          </span>
        </div>
      ))}
    </div>
  )
}

/** 'KXWTAMATCH-26AUG04ANDPLI-AND' -> { series: 'WTA MATCH', tail: '26AUG04ANDPLI · AND' } */
function prettyTicker(t: string): { series: string; tail: string } {
  const parts = (t || '').split('-')
  const series = (parts[0] || '').replace(/^KX/, '').replace(/([A-Z]+?)(MATCH|GAME)$/, '$1 $2')
  return { series, tail: parts.slice(1).join(' · ') }
}

function KalshiBook({ k }: { k: KalshiOpen }) {
  if (!k || k.n === 0) return null
  return (
    <div className="card">
      <div className="card-title">
        KALSHI · LIVE BOOK · {k.n} open · {fmtUsd(k.cost, 2)} at cost
        {k.updated_at && <span className="muted"> · as of {fmtAgo(k.updated_at)}</span>}
      </div>
      <div className="kx-grid">
        {k.rows.map((r) => {
          const p = prettyTicker(r.ticker)
          return (
            <div key={r.ticker} className="kx-pos">
              <div className="kx-top">
                <span className="kx-series">{p.series}</span>
                {r.venue_status ? (
                  <span className="tr-chip settling"
                    title={`Venue status: ${r.venue_status} — game over, awaiting settlement`}>
                    ◌ SETTLING
                  </span>
                ) : (
                  <span className="tr-chip open">● LIVE</span>
                )}
              </div>
              <div className="kx-tail mono">{p.tail}</div>
              <div className="kx-nums mono">
                {r.qty}× @{fmtCents(r.avg_cost)}
                <span className="muted"> · cost {fmtUsd(r.cost, 2)}</span>
              </div>
            </div>
          )
        })}
      </div>
      <div className="tr-foot muted">
        Positions on the Kalshi venue account, read from the engine ledger —
        only venue-accepted fills are recorded. Settled Kalshi results join
        the record as they resolve.
      </div>
    </div>
  )
}

type Status = 'all' | 'won' | 'lost' | 'open'
type SortKey = 'time' | 'stake' | 'pnl'

/** The uncapped record straight from the venues' own ledgers (task #74):
 * PM's afterPosition.realized, Kalshi rebuilt from raw fills+settlements
 * with exact fees. No display cap, no attribution — the number that must
 * match the venue apps to the dollar over its window. */
function VenueTruthCard({ vt }: { vt: VenueTruthData }) {
  if (vt.building) return (
    <div className="card">
      <div className="card-title">VENUE TRUTH · RECONCILED P&amp;L</div>
      <EmptyState>First rebuild since server start is running — numbers
        appear within a few minutes.</EmptyState>
    </div>
  )
  const t = vt.total
  if (!t) return null
  const pm = vt.polymarket_us || {}
  const kx = vt.kalshi || {}
  return (
    <div className="card">
      <div className="tr-ledger-head">
        <div className="card-title">VENUE TRUTH · RECONCILED P&amp;L · since {vt.since}</div>
        <span className={`tr-chip ${t.realized >= 0 ? 'won' : 'lost'}`}>
          {fmtSignedUsd(t.realized)}
        </span>
      </div>
      <div className="tr-slip-nums mono" style={{ flexWrap: 'wrap', gap: 12 }}>
        {vt.all_time && (
          <span title={`Frozen day ledger + live window, since ${vt.all_time.since}`}>
            all-time <span className={vt.all_time.realized >= 0 ? 'pos' : 'neg'}>
              {fmtSignedUsd(vt.all_time.realized)}</span>
            {' '}({vt.all_time.wins}W–{vt.all_time.losses}L)
          </span>
        )}
        <span>{t.wins}W – {t.losses}L · {t.settled} settled</span>
        <span className="muted">on {fmtUsd(t.settled_cost, 2)} settled cost</span>
        {pm.error
          ? <span className="neg" title={pm.error}>Polymarket: unavailable</span>
          : <span>
              Polymarket <span className={(pm.realized ?? 0) >= 0 ? 'pos' : 'neg'}>
                {fmtSignedUsd(pm.realized ?? 0)}</span>
              {' '}· {pm.open ?? 0} open ({fmtUsd(pm.open_cost ?? 0, 2)})
            </span>}
        {kx.error
          ? <span className="neg" title={kx.error}>Kalshi: unavailable</span>
          : <span>
              Kalshi <span className={(kx.realized ?? 0) >= 0 ? 'pos' : 'neg'}>
                {fmtSignedUsd(kx.realized ?? 0)}</span>
              {' '}· {kx.open ?? 0} open ({fmtUsd(kx.open_cost ?? 0, 2)})
            </span>}
      </div>
      {(vt.daily || []).length > 0 && (
        <div className="tr-slips" style={{ marginTop: 10 }}>
          {(vt.daily || []).slice(0, 8).map((d) => (
            <div key={d.day} className="tr-slip-nums mono"
              style={{ justifyContent: 'space-between', padding: '2px 0' }}>
              <span className="muted">{d.day}</span>
              <span>{d.wins}W–{d.losses}L</span>
              <span className="muted">{fmtUsd(d.cost, 2)}</span>
              <span className={d.realized >= 0 ? 'pos' : 'neg'}>
                {fmtSignedUsd(d.realized)}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="tr-foot muted">
        Computed from the venues&apos; own books — Polymarket&apos;s
        per-market realized on each resolution, Kalshi rebuilt from raw
        fills and settlements with exact fees. Uncapped: every position,
        every size, manual and AI alike. Rolling window.
        {vt.partial && <> ⚠️ One venue is currently unavailable — totals
          cover the venue(s) shown only.</>}
        {vt.kalshi_note && <> ⚠️ Kalshi figures: {vt.kalshi_note}.</>}
        {vt.kalshi_window_incomplete && (
          <> {vt.kalshi_window_incomplete.n} Kalshi settlement(s) whose
          entries predate the window are excluded rather than guessed.</>
        )}
      </div>
    </div>
  )
}

function fmtAge(s: number): string {
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

export function TrackRecord() {
  const { data, err } = useTrackRecord()
  const { data: kalshi } = useKalshiOpen()
  const { data: venueTruth } = useVenueTruth()
  const [status, setStatus] = useState<Status>('all')
  const [sport, setSport] = useState('all')
  const [cat, setCat] = useState('all')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('time')
  const [limit, setLimit] = useState(60)

  const s = data?.summary
  const heroPnl = useCountUp(s?.net_pnl ?? 0)
  const heroDeployed = useCountUp(s?.deployed ?? 0)

  const sports = useMemo(() => [...new Set((data?.trades || []).map((r) => r.sport))].sort(), [data])
  const cats = useMemo(() => [...new Set((data?.trades || []).map((r) => r.category))].sort(), [data])

  // Headline KPIs, all derived from the same daily calendar the page
  // already shows — nothing here is a new claim, just a sharper lens.
  const kpis = useMemo(() => {
    const days = data?.daily || []
    if (!days.length) return null
    let best = days[0]
    for (const d of days) if (d.pnl > best.pnl) best = d
    const green = days.filter((d) => d.pnl > 0).length
    let streak = 0
    for (let i = days.length - 1; i >= 0; i--) {
      if (days[i].pnl > 0) streak++
      else break
    }
    // "today" chip only when the newest calendar day IS the viewer's
    // trading day (ET) — a stale label would be a lie.
    const todayEt = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
    const last = days[days.length - 1]
    return {
      best, green, total: days.length, streak,
      today: last.date === todayEt ? last.pnl : null,
    }
  }, [data])

  const ledger = useMemo(() => {
    let rows = data?.trades || []
    if (status === 'won') rows = rows.filter((r) => r.settled && (r.pnl || 0) > 0)
    if (status === 'lost') rows = rows.filter((r) => r.settled && (r.pnl || 0) <= 0)
    if (status === 'open') rows = rows.filter((r) => !r.settled)
    if (sport !== 'all') rows = rows.filter((r) => r.sport === sport)
    if (cat !== 'all') rows = rows.filter((r) => r.category === cat)
    if (q.trim()) {
      const needle = q.trim().toLowerCase()
      rows = rows.filter((r) =>
        (r.title + ' ' + (r.outcome || '') + ' ' + r.market_slug).toLowerCase().includes(needle))
    }
    const key = (r: TRRow) =>
      sort === 'time' ? (r.entry_ts || 0) : sort === 'stake' ? r.stake : (r.pnl ?? r.unrealized ?? 0)
    return [...rows].sort((a, b) => key(b) - key(a))
  }, [data, status, sport, cat, q, sort])

  // Cached numbers beat an error screen: only surface the failure when
  // there is nothing at all to show — a refresh hiccup on a page already
  // full of data should be invisible.
  if (err && (!data || !s)) return <EmptyState>{`Account API unreachable: ${err}`}</EmptyState>
  if (!data || !s) return (
    <div className="page tr-page">
      <div className="tr-hero tr-skel" style={{ height: 340 }} />
      <div className="tr-columns">
        <div className="card tr-skel" style={{ height: 300 }} />
        <div className="card tr-skel" style={{ height: 300 }} />
      </div>
    </div>
  )

  const early = s.settled < MIN_SETTLED

  // Snapshot freshness drives the badge: green under 2 minutes, amber to
  // 10, red past that. A page that cannot admit it is stale trains its
  // owner to distrust every number on it.
  //
  // The age comes from generated_at — when the PAYLOAD was built — not
  // from the embedded snapshot age. The server can serve a persisted
  // payload whose snapshot block froze along with it: on 2026-08-09 the
  // page showed 16-hour-old numbers under a green "SYNCED · 35s AGO"
  // badge because it trusted the frozen block's own claim.
  const genAt = (data as { generated_at?: number }).generated_at
  const payloadAge = genAt ? Math.max(0, Date.now() / 1000 - genAt) : null
  const snapAge = data.snapshot?.age_s ?? null
  const age = payloadAge !== null
    ? Math.max(payloadAge, snapAge ?? 0)
    : snapAge
  const refreshErr = data.snapshot?.refresh_error || null
  // Thresholds track the server's snapshot cadence (raw TTL 180s): under
  // ~5 min is simply "current"; amber and red mean something is wrong.
  const sync: 'ok' | 'lag' | 'stale' =
    age === null ? 'ok' : age < 300 ? 'ok' : age < 900 ? 'lag' : 'stale'

  return (
    <div className="page tr-page">
      <div className="tr-hero">
        <div className="tr-hero-head">
          <div className="tr-ident">
            <span className="tr-bot">🤖</span>
            <div>
              <div className="tr-name">BETTOR<span>EDGE</span> AI</div>
              <div className="tr-sub muted">
                live from the trading account · window {SINCE} → today
              </div>
            </div>
          </div>
          <div
            className={`tr-live${sync !== 'ok' ? ` tr-live-${sync}` : ''}`}
            title={refreshErr ? `last refresh error: ${refreshErr}` : undefined}>
            <span className="tr-pulse" />
            {sync === 'ok'
              ? <>SYNCED{age !== null && <> · {fmtAge(age)} AGO</>}</>
              : sync === 'lag'
                ? <>SYNC LAG · {fmtAge(age!)}</>
                : <>STALE · {fmtAge(age!)}</>}
          </div>
        </div>

        <div className="tr-hero-grid">
          <div className="tr-stat tr-stat-main" onMouseMove={tilt} onMouseLeave={untilt}>
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">NET P&amp;L · AI TRADER</div>
            <div className={`tr-stat-value tr-grad ${s.net_pnl >= 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(heroPnl)}
            </div>
            <div className="tr-stat-foot muted">
              {s.wins}W – {s.losses}L
              {s.win_rate !== null && <> · {fmtPct(s.win_rate, 0)} win rate</>}
              {' '}· {s.settled} settled
              {kpis?.today !== null && kpis?.today !== undefined && (
                <span className={`tr-today ${kpis.today >= 0 ? 'pos-bg' : 'neg-bg'}`}>
                  {kpis.today >= 0 ? '▲' : '▼'} {fmtSignedUsd(kpis.today)} today
                </span>
              )}
            </div>
          </div>
          <div className="tr-stat" onMouseMove={tilt} onMouseLeave={untilt}>
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">CAPITAL DEPLOYED</div>
            <div className="tr-stat-value">{fmtUsd(heroDeployed, 2)}</div>
            <div className="tr-stat-foot muted">
              {s.trades.toLocaleString()} positions · {s.open} open worth {fmtUsd(s.open_value, 2)}
              {kalshi && kalshi.n > 0 && (
                <> · +{fmtUsd(kalshi.cost, 2)} live on Kalshi ({kalshi.n})</>
              )}
            </div>
          </div>
          <div className="tr-stat" onMouseMove={tilt} onMouseLeave={untilt}>
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">ROI · TRADED CAPITAL</div>
            <div className="tr-ring-wrap">
              {s.roi !== null && (
                <div
                  className={`tr-ring${s.roi < 0 ? ' neg' : ''}`}
                  style={{ ['--v' as string]: Math.min(100, Math.abs(s.roi) * 1000) }}
                  title="Dial spans 0–10% ROI"
                  aria-hidden
                />
              )}
              <div>
                <div className={`tr-stat-value ${s.roi === null ? '' : s.roi >= 0 ? 'pos' : 'neg'}`}>
                  {s.roi === null ? '—' : fmtPct(s.roi)}
                </div>
                <div className="tr-stat-foot muted">on {fmtUsd(s.settled_stake, 2)} settled stake</div>
              </div>
            </div>
          </div>
        </div>

        {kpis && (
          <div className="tr-kpis">
            <div className="tr-kpi">
              <span className="tr-kpi-k">BEST DAY</span>
              <span className={`tr-kpi-v ${kpis.best.pnl >= 0 ? 'pos' : 'neg'}`}>
                {fmtSignedUsd(kpis.best.pnl)}
              </span>
              <span className="tr-kpi-s">{kpis.best.date.slice(5)}</span>
            </div>
            <div className="tr-kpi">
              <span className="tr-kpi-k">GREEN DAYS</span>
              <span className="tr-kpi-v">{kpis.green}<span className="tr-kpi-s"> / {kpis.total}</span></span>
            </div>
            {kpis.streak > 1 && (
              <div className="tr-kpi">
                <span className="tr-kpi-k">DAY STREAK</span>
                <span className="tr-kpi-v pos">{kpis.streak} 🔥</span>
              </div>
            )}
            <div className="tr-kpi">
              <span className="tr-kpi-k">VENUES LIVE</span>
              <span className="tr-kpi-v">{kalshi && kalshi.n > 0 ? 2 : 1}</span>
              <span className="tr-kpi-s">
                {kalshi && kalshi.n > 0 ? 'Polymarket + Kalshi' : 'Polymarket'}
              </span>
            </div>
          </div>
        )}

        {/* WHOLE ACCOUNT strip removed (owner 2026-08-09: "remove the
            full account balance from the frontend") — the page shows
            the AI's trading only; account-wide figures stay available
            in the API payload for reports and probes. */}

        {data.snapshot && data.snapshot.positions_complete === false && (
          <div className="tr-honesty">
            ⚠️ POSITION LIST TRUNCATED — the venue returned more open positions than
            one sync fetches ({data.snapshot.positions_pages} pages). Open counts are
            a floor, not a total.
          </div>
        )}

        {early && (
          <div className="tr-honesty">
            ⚖️ EARLY SAMPLE — {s.settled} of the {MIN_SETTLED} settlements this record
            requires before its return means anything. The AI holds itself to the same
            bar: no size increases until the record is earned.
          </div>
        )}

        <EquityCurve daily={data.daily} />
      </div>

      <LiveToday />

      <ResultsTicker rows={data.trades} />

      {kalshi && <KalshiBook k={kalshi} />}

      <div className="tr-columns">
        <div className="card">
          <div className="card-title">DAILY P&amp;L</div>
          {data.daily.length ? (
            <PnlCalendar days={data.daily.map((d) => ({
              date: d.date, pnl: d.pnl, volume: d.deployed, trades: d.trades,
              settled: d.settled, open: d.open }))} />
          ) : <EmptyState>First settlement day pending.</EmptyState>}
        </div>
        <div className="card">
          <div className="card-title">P&amp;L BY SPORT</div>
          <SportBreakdown rows={data.trades} />
        </div>
      </div>

      {venueTruth && <VenueTruthCard vt={venueTruth} />}

      <ReportsCard />

      <div className="card">
        <div className="tr-ledger-head">
          <div className="card-title">TRADE LEDGER · {ledger.length.toLocaleString()} positions</div>
          <div className="tr-filters">
            <input className="tr-search" placeholder="Search team, player, market…"
              value={q} onChange={(e) => setQ(e.target.value)} />
            {(['all', 'won', 'lost', 'open'] as Status[]).map((f) => (
              <button key={f} className={`tr-chipbtn ${status === f ? 'on' : ''}`}
                onClick={() => setStatus(f)}>{f.toUpperCase()}</button>
            ))}
            <select className="tr-select" value={sport} onChange={(e) => setSport(e.target.value)}>
              <option value="all">All sports</option>
              {sports.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <select className="tr-select" value={cat} onChange={(e) => setCat(e.target.value)}>
              <option value="all">All bet types</option>
              {cats.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <select className="tr-select" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="time">Newest</option>
              <option value="stake">Stake</option>
              <option value="pnl">P&amp;L</option>
            </select>
          </div>
        </div>

        {ledger.length === 0 ? (
          <EmptyState>Nothing matches this filter yet.</EmptyState>
        ) : (
          <div className="tr-slips">
            {ledger.slice(0, limit).map((r, i) => {
              const won = r.settled && (r.pnl || 0) > 0
              const st = !r.settled ? 'open' : won ? 'won' : 'lost'
              const toWin = r.entry_price && r.entry_price > 0
                ? r.stake / r.entry_price - r.stake : null
              return (
                <div key={r.market_slug} className={`tr-slip ${st}`}
                  style={{ ['--i' as string]: Math.min(i, 14) }}>
                  <div className="tr-slip-edge" aria-hidden />
                  <div className="tr-slip-main">
                    <div className="tr-slip-top">
                      <span className="tr-slip-sport">{r.icon}</span>
                      <span className="tr-slip-title">{r.title}</span>
                      <span className="tr-tag">{r.category}</span>
                      {r.sleeve === 'copy' && <span className="tr-tag">COPY</span>}
                      {r.cohort === 'manual' && <span className="tr-tag">MANUAL</span>}
                      {r.cohort === 'unattributed' && <span className="tr-tag">OWNER</span>}
                      {(r.cohort === 'over_pnl' || r.cohort === 'over_limit') && (
                        <span className="tr-tag">LARGE</span>
                      )}
                      {r.cashed_out && <span className="tr-tag">CASHED OUT</span>}
                      <span className={`tr-chip ${st}`}>
                        {st === 'open' ? '● LIVE' : st === 'won' ? '✓ WON' : '✕ LOST'}
                      </span>
                    </div>
                    <div className="tr-slip-mid">
                      <span className="tr-slip-outcome">{r.outcome || r.market_slug}</span>
                      {r.entry_ts && (
                        <span className="muted mono">
                          {new Date(r.entry_ts * 1000).toLocaleString(undefined,
                            { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      )}
                      {r.fills > 1 && <span className="tr-tag mono">{r.fills} fills</span>}
                    </div>
                    <div className="tr-slip-nums mono">
                      {r.entry_price && <span title="Venue VWAP entry">@{fmtCents(r.entry_price)}</span>}
                      <span title="Stake at cost">stake {fmtUsd(r.stake, 2)}</span>
                      {toWin !== null && <span className="muted">to win {fmtUsd(toWin, 2)}</span>}
                      {!r.settled && r.unrealized !== null && (
                        <span className={r.unrealized >= 0 ? 'pos' : 'neg'}
                          title="Mark-to-market vs cost">
                          mkt {fmtSignedUsd(r.unrealized)}
                        </span>
                      )}
                      <span className={`tr-slip-pnl ${!r.settled ? 'muted' : won ? 'pos' : 'neg'}`}>
                        {r.settled ? fmtSignedUsd(r.pnl) : '· · ·'}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {ledger.length > limit && (
          <button className="tr-more" onClick={() => setLimit((l) => l + 60)}>
            Show {Math.min(60, ledger.length - limit)} more
          </button>
        )}
        <div className="tr-foot muted">
          Source: the live venue account (positions + its own trade activities),
          window {SINCE} → today, refreshed every 30s. Entry prices are the venue's
          own VWAP. This record is the AI's trading only — engine, copies and the
          underdog cash-out sleeve, with cash-outs counted at sale; manual/owner
          trades are excluded from every figure on this page.
          {data.excluded_over_limit && data.excluded_over_limit.count > 0 && (
            <> {data.excluded_over_limit.count} larger position(s) are excluded from
            every figure above — combined stake{' '}
            {fmtUsd(data.excluded_over_limit.stake, 2)}, settled net{' '}
            {fmtSignedUsd(data.excluded_over_limit.net_pnl)}.</>
          )}
          {data.copy_sleeve && data.copy_sleeve.count > 0 && (
            <> Includes the whale-copy sleeve ({data.copy_sleeve.count} positions,
            settled net {fmtSignedUsd(data.copy_sleeve.net_pnl)}), tagged COPY in
            the ledger.</>
          )}
          {data.excluded_unattributed && data.excluded_unattributed.count > 0 && (
            <> {data.excluded_unattributed.count} non-AI account position(s)
            excluded (settled net {fmtSignedUsd(data.excluded_unattributed.net_pnl)}).</>
          )}
          {' '}{data.excluded_undatable > 0 &&
            `${data.excluded_undatable} position(s) excluded: the venue reported no datable entry.`}
        </div>
      </div>
    </div>
  )
}
