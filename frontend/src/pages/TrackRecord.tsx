import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { Lamp } from '../components/Lamp'
import { BrandLockup, BrandMark } from '../components/Brand'
import { HexField } from '../components/HexField'
import { HoloTerrain } from '../components/HoloTerrain'
import { Odometer } from '../components/Odometer'
import { DayStories, buildSlides } from '../components/DayStories'
import { useLiveFeed } from '../lib/sse'
import { EmptyState } from '../components/EmptyState'
import { PnlCalendar } from '../components/PnlCalendar'
import { LiveToday } from '../components/LiveToday'
import { fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import { ALL_TIME_SINCE, CopiesRecord, CopiesTrade, CopiesWhaleSport,
  useCopiesRecord } from '../lib/record'
import '../styles/record9.css'

// three.js rides in ONLY when someone flips the terrain to 3D — the
// main bundle stays exactly as heavy as before the feature existed.
const Terrain3D = lazy(() => import('../components/Terrain3D'))

/* THE public record — the whale copy portfolio and nothing else (owner
 * order 2026-08-22: "the performance data must exclusively show the
 * copy whales numbers — that is what we sell to investors"). Every
 * number on this page binds to /api/copies-record: uncapped, settled
 * copy trades from the order-level audit, per-whale split, full-window
 * daily series. Engine, arbitrage, manual and software trading render
 * nowhere here. When the settled sample is thin the page says so; a
 * record must be trustable before it is impressive. */

const MIN_SETTLED = 30

/** Sport buckets as the backend's sport_of() names them. */
const SPORT_META: Record<string, { icon: string; label: string }> = {
  basketball: { icon: 'BSK', label: 'Basketball' },
  wnba: { icon: 'WNB', label: 'WNBA' },
  football: { icon: 'FBL', label: 'Football' },
  baseball: { icon: 'BSE', label: 'Baseball' },
  hockey: { icon: 'HKY', label: 'Hockey' },
  tennis: { icon: 'TEN', label: 'Tennis' },
  soccer: { icon: 'SOC', label: 'Soccer / Intl' },
  esports: { icon: 'ESP', label: 'Esports' },
  unknown: { icon: 'OTH', label: 'Other' },
}
export function sportMeta(sport: string): { icon: string; label: string } {
  return SPORT_META[sport] || { icon: 'OTH', label: sport || 'Other' }
}

/** 'mlb-nyy-bos-2026-08-20-nyy' -> 'MLB · nyy bos 2026-08-20 nyy' */
function prettySlug(slug: string | null): string {
  if (!slug) return '—'
  const parts = slug.split('-')
  if (parts.length < 2) return slug
  return `${parts[0].toUpperCase()} · ${parts.slice(1).join(' ')}`
}

/** Venue id from the order audit ('polymarket-us'/'kalshi'…) -> ledger
 * chip. Unknown venues render nothing rather than guessing a label. */
function venueChip(venue: string | null | undefined): { cls: string; label: string } | null {
  if (!venue) return null
  if (venue.startsWith('kalshi')) return { cls: 'venue-k', label: 'Kalshi' }
  if (venue.startsWith('polymarket')) return { cls: 'venue-pm', label: 'Polymarket' }
  return null
}

/** Whale fill → our order, compact: '1.2s', '48s', '3.5m'. */
export function fmtLatency(s: number): string {
  if (s < 10) return `${s.toFixed(1)}s`
  if (s < 120) return `${Math.round(s)}s`
  return `${(s / 60).toFixed(1)}m`
}

/** localStorage key for the epoch/all-time display choice. */
// v3 key (owner order 2026-09-02): the front end was zeroed to the
// September 1st epoch, so a browser that had toggled ALL-TIME before
// starts on the fresh record again rather than carrying old history in.
const WINDOW_KEY = 'sa_tr_alltime_v3'


/** Scrolling ticker of the latest settled copy results. */
function ResultsTicker({ trades }: { trades: CopiesTrade[] }) {
  const settled = trades.slice(0, 24)
  if (settled.length < 3) return null
  const items = [...settled, ...settled] // seamless loop
  return (
    <div className="tk-wrap" aria-hidden>
      <div className="tk-track">
        {items.map((t, i) => (
          <span key={i} className="tk-item">
            <span className="tk-whale">{t.whale}</span>
            <span className="tk-title">{prettySlug(t.slug)}</span>
            <span className={`mono ${t.pnl > 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(t.pnl)}
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
    <div className="tr-curve" role="img" aria-label="Cumulative realized copy profit by day, with daily P&L bars">
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

function SportBreakdown({ rows }: { rows: CopiesWhaleSport[] }) {
  const sports = useMemo(() => {
    const by = new Map<string, { pnl: number; staked: number; n: number; wins: number }>()
    for (const r of rows) {
      const b = by.get(r.sport) || { pnl: 0, staked: 0, n: 0, wins: 0 }
      b.pnl += r.pnl; b.staked += r.staked; b.n += r.settled; b.wins += r.wins
      by.set(r.sport, b)
    }
    return [...by.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [rows])
  if (!sports.length) return <EmptyState>The board fills in as copy trades settle.</EmptyState>
  const maxAbs = Math.max(...sports.map(([, r]) => Math.abs(r.pnl)), 0.01)
  return (
    <div className="tr-sports">
      {sports.map(([sport, r]) => {
        const m = sportMeta(sport)
        return (
          <div key={sport} className="tr-sport-row"
            title={`${m.label}: ${r.n} settled (${r.wins} won), staked ${fmtUsd(r.staked, 2)}`}>
            <span className="tr-sport-name">
              <span className="nd-sport">{m.icon}</span> {m.label}
              <span className="muted mono"> {r.wins}–{r.n - r.wins}</span>
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
        )
      })}
    </div>
  )
}

/** Per-whale split of the copy record — who earns the capital. */
const CAT_COLORS = ['var(--cat1)', 'var(--cat2)', 'var(--cat3)', 'var(--cat4)', 'var(--cat5)', 'var(--cat6)']

/** G2: a whale that traded in the last 10 minutes breathes in its
 * categorical color — the operator sees who is awake at a glance. */
/** Rarity is EARNED (owner order 2026-08-29): tiers bind to verified
 * ROI on a real settled sample, never to styling whim — prismatic is
 * 25%+ on 20+ settlements, gold 10%+ on 10+, silver any positive ROI.
 * The tier chip repeats the number it was earned by. */
function rarity(w: { roi: number | null; settled: number }):
    { cls: string; chip: string; label: string } | null {
  if (w.roi == null) return null
  if (w.roi >= 0.25 && w.settled >= 20)
    return { cls: 'r-prism', chip: 'prism', label: 'PRISMATIC' }
  if (w.roi >= 0.10 && w.settled >= 10)
    return { cls: 'r-gold', chip: 'gold', label: 'GOLD' }
  if (w.roi > 0) return { cls: '', chip: 'silver', label: 'SILVER' }
  return null
}

function WhalesCard({ c }: { c: CopiesRecord }) {
  const { live } = useLiveFeed()
  // Gyro shine (mobile): one deviceorientation listener tilts every
  // card from the phone's attitude, so the foil moves as the hand
  // does. Android fires this freely; iOS gates it behind a permission
  // prompt we choose not to nag for — there the pointer path still
  // works. Desktop keeps per-card pointer tilt untouched.
  useEffect(() => {
    if (!window.matchMedia?.('(pointer: coarse)').matches) return
    const on = (e: DeviceOrientationEvent) => {
      if (e.beta == null || e.gamma == null) return
      const rx = Math.max(-8, Math.min(8, (e.beta - 45) * -0.22))
      const ry = Math.max(-9, Math.min(9, e.gamma * 0.28))
      document.querySelectorAll<HTMLElement>('.v13-wcard.v14-holo').forEach((el) => {
        el.style.setProperty('--rx', `${rx.toFixed(2)}deg`)
        el.style.setProperty('--ry', `${ry.toFixed(2)}deg`)
        el.style.setProperty('--mx', `${(50 + ry * 4).toFixed(1)}%`)
        el.style.setProperty('--my', `${(45 + rx * 4).toFixed(1)}%`)
      })
    }
    window.addEventListener('deviceorientation', on)
    return () => window.removeEventListener('deviceorientation', on)
  }, [])
  if (!c.by_whale.length) return null
  const maxAbs = Math.max(...c.by_whale.map((w) => Math.abs(w.pnl)), 0.01)
  const awake = new Set(
    live.filter((t) => t.ts && Date.now() - new Date(t.ts).getTime() < 600_000)
        .map((t) => (t.whale_username || '').toLowerCase()))
  return (
    <div className="card">
      <div className="tr-ledger-head">
        <div className="card-title">BY WHALE · WHO WE COPY · since {c.since}</div>
        <span className={`tr-chip ${c.total.pnl >= 0 ? 'won' : 'lost'}`}>
          {fmtSignedUsd(c.total.pnl)}
        </span>
      </div>
      <div className="v13-whales">
        {c.by_whale.map((w, i) => {
          const tier = rarity(w)
          return (
          <div key={w.whale} className={`v13-wcard v14-holo ${tier?.cls || ''}`}
            style={{ ['--wc' as string]: CAT_COLORS[i % CAT_COLORS.length] }}
            onPointerMove={(e) => {
              const el = e.currentTarget
              const r = el.getBoundingClientRect()
              const px = (e.clientX - r.left) / r.width
              const py = (e.clientY - r.top) / r.height
              el.style.setProperty('--rx', `${((0.5 - py) * 10).toFixed(2)}deg`)
              el.style.setProperty('--ry', `${((px - 0.5) * 12).toFixed(2)}deg`)
              el.style.setProperty('--mx', `${(px * 100).toFixed(1)}%`)
              el.style.setProperty('--my', `${(py * 100).toFixed(1)}%`)
            }}
            onPointerLeave={(e) => {
              const el = e.currentTarget
              el.style.setProperty('--rx', '0deg')
              el.style.setProperty('--ry', '0deg')
            }}>
            <span className="v14-rank" aria-label={`rank ${i + 1}`}>#{i + 1}</span>
            {tier && (
              <span className={`v14-tier ${tier.chip}`}
                title={`${tier.label} — earned by verified ROI on settled stake`}>
                {tier.label}
              </span>
            )}
            {awake.has(w.whale.toLowerCase()) && (
              <span className="v13-wlamp">
                <Lamp mode="breathe" color={CAT_COLORS[i % CAT_COLORS.length]} label="LIVE" />
              </span>
            )}
            <div className="v13-ava">
              {w.whale.replace(/^0x/i, '').slice(0, 2).toUpperCase()}
            </div>
            <div className="v13-wname" title={w.whale}>{w.whale}</div>
            <div className={`muted mono`} style={{ fontSize: 11.5 }}>
              {w.wins}W–{w.losses}L · {fmtUsd(w.staked, 0)} staked
            </div>
            <div className={`v13-wpnl ${w.pnl >= 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(w.pnl)}
            </div>
            <div className="v13-wmeta muted mono">
              {w.roi === null ? '— ROI' : `${fmtPct(w.roi)} ROI on settled stake`}
            </div>
            <div className="v13-wbar" aria-hidden>
              <i className={w.pnl >= 0 ? 'pos-bg' : 'neg-bg'}
                style={{ width: `${(Math.abs(w.pnl) / maxAbs) * 100}%` }} />
            </div>
          </div>
          )
        })}
      </div>
      <div className="tr-foot muted">
        Every settled copy order attributed to its source whale, uncapped,
        from the order-level audit. A whale joins this board only when
        promoted to the copy roster.
      </div>
    </div>
  )
}

type Status = 'all' | 'won' | 'lost'
type SortKey = 'time' | 'stake' | 'pnl'

function fmtAge(s: number): string {
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

export function TrackRecord() {
  // Epoch/all-time is DISPLAY state only (owner order 2026-08-28): the
  // API defaults to the epoch window; ?since= reaches the full history.
  // Each window is its own fetch + cache — the two never blend.
  const [allTime, setAllTime] = useState<boolean>(() => {
    try { return localStorage.getItem(WINDOW_KEY) === '1' } catch { return false }
  })
  const { data, err } = useCopiesRecord(30_000, allTime ? ALL_TIME_SINCE : undefined)
  const [status, setStatus] = useState<Status>('all')
  const [whale, setWhale] = useState('all')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('time')
  const [limit, setLimit] = useState(60)
  // Terrain dimension (owner order 2026-08-29): 3D lazy-loads three.js
  // on first flip; the preference sticks.
  const [terrain3d, setTerrain3d] = useState(() => {
    try { return localStorage.getItem('sa_terrain3d') === '1' } catch { return false }
  })
  const flipTerrain = () => {
    const next = !terrain3d
    try { localStorage.setItem('sa_terrain3d', next ? '1' : '0') } catch { /* pref */ }
    setTerrain3d(next)
  }
  const [storiesOpen, setStoriesOpen] = useState(false)

  const t = data?.total
  const heroPnl = t?.pnl ?? 0
  const heroOpen = data?.open?.stake ?? 0

  // Chronological daily series (the API serves newest-first).
  const chrono = useMemo(() => [...(data?.daily || [])].reverse(), [data])
  const trades = data?.trades || []
  const whales = useMemo(
    () => [...new Set(trades.map((r) => r.whale))].sort(), [trades])

  // Headline KPIs, all derived from the same daily calendar the page
  // already shows — nothing here is a new claim, just a sharper lens.
  const kpis = useMemo(() => {
    if (!chrono.length) return null
    let best = chrono[0]
    for (const d of chrono) if (d.pnl > best.pnl) best = d
    const green = chrono.filter((d) => d.pnl > 0).length
    let streak = 0
    for (let i = chrono.length - 1; i >= 0; i--) {
      if (chrono[i].pnl > 0) streak++
      else break
    }
    return { best, green, total: chrono.length, streak }
  }, [chrono])

  // "today" chip only when the server's ET scoreline has settled money.
  const today = (data?.today?.settled ?? 0) > 0 ? data!.today!.pnl : null

  const ledger = useMemo(() => {
    let rows = trades
    if (status === 'won') rows = rows.filter((r) => r.pnl > 0)
    if (status === 'lost') rows = rows.filter((r) => r.pnl <= 0)
    if (whale !== 'all') rows = rows.filter((r) => r.whale === whale)
    if (q.trim()) {
      const needle = q.trim().toLowerCase()
      rows = rows.filter((r) =>
        `${r.slug || ''} ${r.whale}`.toLowerCase().includes(needle))
    }
    if (sort === 'time') return rows // served newest-first already
    const key = (r: CopiesTrade) => (sort === 'stake' ? r.stake : r.pnl)
    return [...rows].sort((a, b) => key(b) - key(a))
  }, [trades, status, whale, q, sort])

  // Cached numbers beat an error screen: only surface the failure when
  // there is nothing at all to show — a refresh hiccup on a page already
  // full of data should be invisible.
  if (err && (!data || !t)) return <EmptyState>{`Record API unreachable: ${err}`}</EmptyState>
  if (!data || !t) return (
    <div className="page tr-page">
      <div className="tr-hero tr-skel" style={{ height: 340 }} />
      <div className="tr-columns">
        <div className="card tr-skel" style={{ height: 300 }} />
        <div className="card tr-skel" style={{ height: 300 }} />
      </div>
    </div>
  )

  const early = t.settled < MIN_SETTLED

  const toggleWindow = () => {
    const next = !allTime
    try { localStorage.setItem(WINDOW_KEY, next ? '1' : '0') } catch { /* pref only */ }
    setAllTime(next)
  }
  // The window's honest start label: the served `since` day — except in
  // all-time mode, where the request floor (2020) predates the record,
  // so the earliest settled day in the payload speaks instead.
  const windowStart = allTime && data.daily.length
    ? data.daily[data.daily.length - 1].day
    : data.since

  // Freshness badge from the payload's own build stamp: green under 5
  // minutes, amber to 15, red past that. A page that cannot admit it is
  // stale trains its owner to distrust every number on it.
  const genAt = data.generated_at ? Date.parse(data.generated_at) : NaN
  const age = Number.isFinite(genAt)
    ? Math.max(0, (Date.now() - genAt) / 1000)
    : null
  const sync: 'ok' | 'lag' | 'stale' =
    age === null ? 'ok' : age < 300 ? 'ok' : age < 900 ? 'lag' : 'stale'

  const openCount = data.open?.count ?? 0

  const slides = storiesOpen
    ? buildSlides(
        chrono.map((d) => ({ day: d.day, pnl: d.pnl, settled: d.settled,
          wins: d.wins })),
        trades, kpis?.streak ?? 0)
    : null

  return (
    <div className="page tr-page">
      {slides && (
        <DayStories slides={slides} onClose={() => setStoriesOpen(false)} />
      )}
      <div className="tr-hero nd-reticle">
        <HexField height={300} />
        <span className="nd-watermark"><BrandMark size={340} /></span>
        <div className="tr-hero-head">
          <div className="tr-ident">
            <div>
              <BrandLockup height={40} />
              <div className="tr-sub muted" style={{ marginTop: 8 }}>
                whale copy portfolio · live from the order ledger · window {windowStart} → today
              </div>
            </div>
          </div>
          <div className="tr-hero-side">
            <div className={`tr-live${sync !== 'ok' ? ` tr-live-${sync}` : ''}`}>
              <span className="tr-pulse" />
              {sync === 'ok'
                ? <>SYNCED{age !== null && <> · {fmtAge(age)} AGO</>}</>
                : sync === 'lag'
                  ? <>SYNC LAG · {fmtAge(age!)}</>
                  : <>STALE · {fmtAge(age!)}</>}
            </div>
            <button className="tr-story-btn" onClick={() => setStoriesOpen(true)}
              title="The last settled day as tap-through slides">
              ▶ DAILY RECAP
            </button>
          </div>
        </div>

        {(data.rebaselined || allTime) && (
          <div className="tr-epoch">
            <span className="pulse-dot gold" aria-hidden />
            <span className="tr-epoch-line">
              {allTime
                ? <>All-time record — every settled copy, {windowStart} → today</>
                : <>Record since <b>{data.epoch ?? data.since}</b> — the live copy era</>}
            </span>
            <button className={`tr-chipbtn${allTime ? ' on' : ''}`}
              onClick={toggleWindow}
              title="Same ledger, wider window — the epoch record and the full history never blend">
              ALL-TIME
            </button>
          </div>
        )}

        <div className="tr-hero-grid">
          <div className="tr-stat tr-stat-main">
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">NET P&amp;L · COPY PORTFOLIO</div>
            {/* Gradient-clipped text cannot paint the odometer's
                transformed digit rails (they fall outside the clip),
                so the rolling hero wears a solid glow instead. */}
            <div className={`tr-stat-value v9-money odo-hero ${t.pnl >= 0 ? 'pos' : 'neg'}`}>
              <Odometer value={heroPnl} render={fmtSignedUsd} countUp />
            </div>
            <div className="tr-stat-foot muted">
              {t.wins}W – {t.losses}L
              {t.win_rate !== null && <> · {fmtPct(t.win_rate, 0)} win rate</>}
              {' '}· {t.settled.toLocaleString()} settled
              {today !== null && (
                <span className={`tr-today ${today >= 0 ? 'pos-bg' : 'neg-bg'}`}>
                  {today >= 0 ? '▲' : '▼'} {fmtSignedUsd(today)} today
                </span>
              )}
              {data.kalshi_included && data.venues?.kalshi && (
                <div className="tr-venue-split mono">
                  Polymarket {fmtSignedUsd(data.venues.polymarket?.pnl ?? 0)}
                  {' '}· Kalshi {fmtSignedUsd(data.venues.kalshi.pnl ?? 0)}
                  {' '}— both venues, one record
                </div>
              )}
            </div>
          </div>
          <div className="tr-stat">
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">CAPITAL DEPLOYED · LIVE</div>
            <div className="tr-stat-value">
              <Odometer value={heroOpen} render={(v) => fmtUsd(v, 2)} countUp />
            </div>
            <div className="tr-stat-foot muted">
              {openCount} open cop{openCount === 1 ? 'y' : 'ies'} on the table
              {' '}· {fmtUsd(t.staked, 0)} staked &amp; settled since {windowStart.slice(5)}
            </div>
            {!!data.open?.by_whale?.length && (
              <div className="tr-open-whales">
                {data.open.by_whale.map((w) => (
                  <span key={w.whale} className="v9-chip">
                    {w.whale} <span className="muted">×{w.count}</span> {fmtUsd(w.stake, 0)}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="tr-stat">
            <span className="tr-sheen" aria-hidden />
            <div className="tr-stat-label">ROI · SETTLED STAKE</div>
            <div className="tr-ring-wrap">
              {t.roi !== null && (
                <div
                  className={`tr-ring${t.roi < 0 ? ' neg' : ''}`}
                  style={{ ['--v' as string]: Math.min(100, Math.abs(t.roi) * 400) }}
                  title="Dial spans 0–25% ROI"
                  aria-hidden
                />
              )}
              <div>
                <div className={`tr-stat-value ${t.roi === null ? '' : t.roi >= 0 ? 'pos' : 'neg'}`}>
                  {t.roi === null ? '—' : fmtPct(t.roi)}
                </div>
                <div className="tr-stat-foot muted">on {fmtUsd(t.staked, 2)} settled stake</div>
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
              <span className="tr-kpi-s">{kpis.best.day.slice(5)}</span>
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
              <span className="tr-kpi-k">WHALES COPIED</span>
              <span className="tr-kpi-v">{data.by_whale.length}</span>
              <span className="tr-kpi-s">promoted sources</span>
            </div>
          </div>
        )}

        {early && (
          <div className="tr-honesty">
            EARLY SAMPLE — {t.settled} of the {MIN_SETTLED} settlements this record
            requires before its return means anything. The platform holds itself to
            the same bar: no size increases until the record is earned.
          </div>
        )}

        <EquityCurve daily={chrono.map((d) => ({ date: d.day, pnl: d.pnl }))} />
      </div>

      <div className="card nd-reticle v14-terrain-card">
        <div className="card-title">P&amp;L TERRAIN · THE DAILY LEDGER IN RELIEF
          <button className={`tr-chipbtn tr-3d${terrain3d ? ' on' : ''}`}
            onClick={flipTerrain}
            title={terrain3d
              ? 'Back to the etched relief'
              : 'Orbitable WebGL — drag to spin, scroll to zoom'}>
            {terrain3d ? '2D' : '3D'}
          </button>
          <span style={{ marginLeft: 'auto', fontSize: 10 }} className="muted mono">
            {terrain3d ? 'drag orbits · wheel zooms' : 'green rises · red sinks · live scan'}
          </span>
        </div>
        {terrain3d ? (
          <Suspense fallback={<div className="tr-skel" style={{ height: 300 }} />}>
            <Terrain3D days={chrono.map((d) => ({ date: d.day, pnl: d.pnl }))} />
          </Suspense>
        ) : (
          <HoloTerrain days={chrono.map((d) => ({ date: d.day, pnl: d.pnl }))} />
        )}
      </div>

      <LiveToday />

      <ResultsTicker trades={trades} />

      <WhalesCard c={data} />

      <div className="tr-columns">
        <div className="card">
          <div className="card-title">DAILY P&amp;L</div>
          {data.daily.length ? (
            <PnlCalendar days={chrono.map((d) => ({
              date: d.day, pnl: d.pnl, trades: d.settled, settled: d.settled }))} />
          ) : <EmptyState>First settlement day pending.</EmptyState>}
        </div>
        <div className="card">
          <div className="card-title">P&amp;L BY SPORT</div>
          <SportBreakdown rows={data.by_whale_sport || []} />
        </div>
      </div>

      <div className="card">
        <div className="tr-ledger-head">
          <div className="card-title">
            COPY LEDGER · {ledger.length.toLocaleString()} settled trades
          </div>
          <div className="tr-filters">
            <input className="tr-search" placeholder="Search market or whale…"
              value={q} onChange={(e) => setQ(e.target.value)} />
            {(['all', 'won', 'lost'] as Status[]).map((f) => (
              <button key={f} className={`tr-chipbtn ${status === f ? 'on' : ''}`}
                onClick={() => setStatus(f)}>{f.toUpperCase()}</button>
            ))}
            <select className="tr-select" value={whale} onChange={(e) => setWhale(e.target.value)}>
              <option value="all">All whales</option>
              {whales.map((x) => <option key={x} value={x}>{x}</option>)}
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
              const won = r.pnl > 0
              const st = won ? 'won' : 'lost'
              const venue = venueChip(r.venue)
              return (
                <div key={`${r.slug}-${r.whale}-${i}`} className={`tr-slip ${st}`}
                  style={{ ['--i' as string]: Math.min(i, 14) }}>
                  <div className="tr-slip-edge" aria-hidden />
                  <div className="tr-slip-main">
                    <div className="tr-slip-top">
                      <span className="tr-slip-title">{prettySlug(r.slug)}</span>
                      <span className="tr-tag">{r.whale}</span>
                      {venue && <span className={`v9-chip ${venue.cls}`}>{venue.label}</span>}
                      {r.latency_s != null && (
                        <span className="v9-chip lat" title="Whale fill → our order">
                          {fmtLatency(r.latency_s)}
                        </span>
                      )}
                      {r.status === 'cashed_out' && <span className="tr-tag">CASHED OUT</span>}
                      <span className={`tr-chip ${st}`}>
                        {won ? '✓ WON' : '✕ LOST'}
                      </span>
                    </div>
                    <div className="tr-slip-nums mono">
                      {r.day && <span className="muted">{r.day}</span>}
                      <span title="Stake at fill">stake {fmtUsd(r.stake, 2)}</span>
                      <span className={`tr-slip-pnl ${won ? 'pos' : 'neg'}`}>
                        {fmtSignedUsd(r.pnl)}
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
        {/* page-specific facts only — the boilerplate disclaimer lives
            once, in the global footer (v9 review: it printed twice) */}
        <div className="tr-foot muted">
          Window {windowStart} → today, refreshed every 30s. Cash-outs are
          counted at sale. The ledger lists the
          {' '}{trades.length.toLocaleString()} newest settled copies; the
          totals above cover the full window.
        </div>
      </div>
    </div>
  )
}
