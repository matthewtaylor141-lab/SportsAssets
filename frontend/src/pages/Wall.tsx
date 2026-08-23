import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { deskUnlock, type DeskAccounts } from '../lib/desk'
import type { CopiesRecord } from '../lib/record'
import {
  WALL_RELOCK_EVENT, dayCategories, etDaysAgo, etHM, etToday, isCopyCategory,
  maybeRenewWallToken, wallApi, wallAuthed, wallBroadcast, wallFeed, wallUnlock,
  type BreakdownDay, type DailyBreakdown, type TodayLive, type WallFeedCard,
  type WallScreen, type WallState,
} from '../lib/wall'
import '../styles/wall.css'

// TV wall (owner order 2026-08-23): two always-on boards for the office
// TVs — /wall/kalshi and /wall/polymarket — plus /wall/control, the
// phone/Mac page that flips both TVs between the live books and a
// 7-day report. The walls are READ-ONLY by construction: they hold a
// 'wall'-scope token the server refuses on every mutating desk
// endpoint, they poll on fixed cadences, and they babysit themselves
// (failure watchdog → hard reload, nightly 04:10 ET reload, ±2px
// pixel shift burn-in guard). No hover affordances — a TV has no mouse.

type WallVenue = 'kalshi' | 'polymarket'

// ── Formatting ───────────────────────────────────────────────────────

const money = (v: number | null | undefined): string =>
  v == null
    ? '—'
    : `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const signed = (v: number | null | undefined): string =>
  v == null
    ? '—'
    : `${v > 0 ? '+' : v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const cents = (v: number | null | undefined): string =>
  v == null ? '—' : `${Math.round(v * 100)}¢`

const pnlCls = (v: number | null | undefined): string =>
  (v ?? 0) > 0 ? 'pos' : (v ?? 0) < 0 ? 'neg' : ''

const fmtDay = (iso: string): string =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' })

/** "KXMLBGAME-25AUG23SEANYY-SEA" → "MLB · SEA @ NYY · SEA". */
function prettyTicker(t: string): string {
  const parts = t.split('-')
  if (parts.length < 2) return t
  const league = (parts[0] || '').replace(/^KX/, '').replace(/(GAME|MATCH|SERIES)$/, '')
  const side = parts.length > 2 ? parts[parts.length - 1] : ''
  let mid = (parts[1] || '').replace(/^\d{2}[A-Z]{3}\d{2}/, '')
  if (/^[A-Z]{6}$/.test(mid)) mid = `${mid.slice(0, 3)} @ ${mid.slice(3)}`
  const bits = [league, mid, side].filter(Boolean)
  return bits.length ? bits.join(' · ') : t
}

// ── Plumbing hooks ───────────────────────────────────────────────────

/** 1s (or slower) re-render clock for the "updated Xs ago" readouts. */
function useClock(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(t)
  }, [ms])
  return now
}

interface Poll<T> { data: T | null; lastOk: number | null }

/**
 * Fixed-cadence poller with the wall's failure WATCHDOG: five
 * consecutive failures on any one poller means the page (or the TV's
 * browser) may be wedged — a full reload is the unattended reset
 * button. A 401 inside wallApi relocks the wall, which flips `enabled`
 * off before the watchdog can trip on a dead session.
 */
function useWallPoll<T>(load: () => Promise<T>, ms: number, enabled: boolean, key: string): Poll<T> {
  const loadRef = useRef(load)
  loadRef.current = load
  const [data, setData] = useState<T | null>(null)
  const [lastOk, setLastOk] = useState<number | null>(null)
  const failsRef = useRef(0)
  useEffect(() => {
    if (!enabled) return
    let dead = false
    failsRef.current = 0
    const tick = async () => {
      // Sliding session: every poll tick offers the token a renewal —
      // review 2026-08-23 found the renewer exported but never invoked,
      // which would have relocked every TV weekly. Self-throttled inside.
      void maybeRenewWallToken()
      try {
        const d = await loadRef.current()
        if (dead) return
        failsRef.current = 0
        setData(d)
        setLastOk(Date.now())
      } catch {
        if (dead) return
        failsRef.current += 1
        if (failsRef.current >= 5) window.location.reload()
      }
    }
    void tick()
    const t = window.setInterval(() => { void tick() }, ms)
    return () => { dead = true; window.clearInterval(t) }
  }, [ms, enabled, key])
  return { data, lastOk }
}

/** Wall session state + relock listener (any 401 drops back to the gate). */
function useWallSession(): [boolean, () => void] {
  const [authed, setAuthed] = useState(() => wallAuthed())
  useEffect(() => {
    const relock = () => setAuthed(false)
    window.addEventListener(WALL_RELOCK_EVENT, relock)
    return () => window.removeEventListener(WALL_RELOCK_EVENT, relock)
  }, [])
  return [authed, () => setAuthed(true)]
}

/** Burn-in guard: cycle the whole board through ±2px every 3 minutes. */
const SHIFTS: [number, number][] = [
  [0, 0], [2, 1], [1, 2], [-1, 1], [-2, -1], [0, -2], [2, -2], [-2, 2],
]
function usePixelShift(): { transform: string } {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => setI((v) => (v + 1) % SHIFTS.length), 180_000)
    return () => window.clearInterval(t)
  }, [])
  const [x, y] = SHIFTS[i % SHIFTS.length]
  return { transform: `translate(${x}px, ${y}px)` }
}

/** Hard reload once a day when ET crosses 04:10–04:15 (venue quiet hour). */
let _nightlyFiredDay = ''   // module guard: write-broken storage must not
                            // burst-reload every 30s through the window
function useNightlyReload(): void {
  useEffect(() => {
    const t = window.setInterval(() => {
      const hm = etHM()
      if (hm < '04:10' || hm > '04:15') return
      const day = etToday()
      if (_nightlyFiredDay === day) return
      try {
        if (localStorage.getItem('sa_wall_reloaded') === day) return
        localStorage.setItem('sa_wall_reloaded', day)
        // A reload we cannot record is a reload we must not take — a
        // write-broken store would otherwise burst through the window.
        if (localStorage.getItem('sa_wall_reloaded') !== day) return
      } catch { return }
      _nightlyFiredDay = day
      window.location.reload()
    }, 30_000)
    return () => window.clearInterval(t)
  }, [])
}

// ── Shared chrome ────────────────────────────────────────────────────

function LiveDot({ newest, now }: { newest: number | null; now: number }) {
  const age = newest == null ? null : Math.max(0, Math.round((now - newest) / 1000))
  const cls = age == null ? 'stale' : age < 45 ? 'ok' : age < 180 ? 'lag' : 'stale'
  const label = age == null
    ? 'no data yet'
    : age < 120 ? `updated ${age}s ago` : `updated ${Math.round(age / 60)}m ago`
  return <div className={`wall-live ${cls}`}><i />{label}</div>
}

function WallGate({ label, onUnlocked }: { label: string; onUnlocked: () => void }) {
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const submit = async () => {
    const secret = pw.trim()
    if (!secret || busy) return
    setBusy(true)
    setErr('')
    const r = await wallUnlock(secret)
    if (r.ok) {
      setPw('')
      onUnlocked()
    } else {
      setErr(r.error || 'Wrong password.')
    }
    setBusy(false)
  }
  return (
    <div className="wall-gate">
      <div className="wall-gate-box">
        <div className="wall-gate-brand">BETTORTOKEN <b>· {label}</b></div>
        <div className="wall-gate-sub">
          Enter the desk password once — this screen stays unlocked for 7 days and renews itself.
        </div>
        <div className="wall-gate-row">
          <input
            type="password" autoFocus value={pw}
            autoCapitalize="none" autoCorrect="off" spellCheck={false}
            onChange={(e) => setPw(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
            aria-label="Desk password"
          />
          <button onClick={() => void submit()} disabled={busy}>
            {busy ? 'UNLOCKING…' : 'UNLOCK'}
          </button>
        </div>
        <div className="wall-gate-err">{err}</div>
      </div>
    </div>
  )
}

// ── Report board (shared by both walls) ──────────────────────────────

interface ReportRow { copies: number; manual: number; arb: number; external: number; unatt: number }

function reportRow(d: BreakdownDay): ReportRow {
  const r: ReportRow = { copies: 0, manual: 0, arb: 0, external: 0, unatt: 0 }
  for (const [cat, c] of dayCategories(d)) {
    if (cat === 'software') r.unatt += c.pnl
    else if (cat === 'manual') r.manual += c.pnl
    else if (cat === 'arb') r.arb += c.pnl
    else if (cat === 'external') r.external += c.pnl
    else if (isCopyCategory(cat)) r.copies += c.pnl
  }
  return r
}

function ReportBoard({ state, breakdown }: {
  state: WallState | null
  breakdown: DailyBreakdown | null
}) {
  const from = state?.from || etDaysAgo(6)
  const to = state?.to || etToday()
  // The public endpoint is month-to-date with no params — the range
  // filter happens here, client-side, on the ET day strings.
  const days = (breakdown?.days || []).filter((d) => d.date >= from && d.date <= to)
  let net = 0
  let settled = 0
  let wins = 0
  let losses = 0
  for (const d of days) {
    for (const [, c] of dayCategories(d)) {
      net += c.pnl
      settled += c.settled
      wins += c.wins
      losses += c.losses
    }
  }
  const winRate = wins + losses > 0 ? wins / (wins + losses) : null
  return (
    <>
      <div className="wall-rtiles">
        <div className="wall-rtile">
          <div className="wall-tile-l">Net P&L</div>
          <div className={`wall-tile-v num ${pnlCls(net)}`}>{signed(net)}</div>
        </div>
        <div className="wall-rtile">
          <div className="wall-tile-l">Settled trades</div>
          <div className="wall-tile-v num">{settled.toLocaleString('en-US')}</div>
        </div>
        <div className="wall-rtile">
          <div className="wall-tile-l">Win rate</div>
          <div className="wall-tile-v num">
            {winRate == null ? '—' : `${Math.round(winRate * 100)}%`}
          </div>
        </div>
      </div>
      <div className="wall-h">
        Per-day results
        <small className="wall-rrange">{from} → {to} · ET days</small>
      </div>
      <div className="wall-rtable">
        <div className="wall-rhead">
          <span>Date</span>
          <span className="r">Account</span>
          <span className="r">Copies</span>
          <span className="r">Manual</span>
          <span className="r hide-narrow">Arb</span>
          <span className="r hide-narrow">External</span>
          <span className="r">Unattributed</span>
        </div>
        {days.length === 0 ? (
          <div className="wall-empty">
            {breakdown == null ? 'Loading the report…' : 'No settled days in this range yet.'}
          </div>
        ) : days.map((d) => {
          const r = reportRow(d)
          return (
            <div className="wall-rrow" key={d.date}>
              <span className="d">{fmtDay(d.date)}</span>
              <span className={`r ${pnlCls(d.account)}`}>{signed(d.account)}</span>
              <span className={`r ${pnlCls(r.copies)}`}>{signed(r.copies)}</span>
              <span className={`r ${pnlCls(r.manual)}`}>{signed(r.manual)}</span>
              <span className={`r hide-narrow ${pnlCls(r.arb)}`}>{signed(r.arb)}</span>
              <span className={`r hide-narrow ${pnlCls(r.external)}`}>{signed(r.external)}</span>
              <span className={`r ${pnlCls(r.unatt)}`}>{signed(r.unatt)}</span>
            </div>
          )
        })}
      </div>
    </>
  )
}

// ── MERIDIAN scene boards (owner 2026-08-23: "he should use both
// screens... bring him to life") — the walls become MERIDIAN's own
// displays. A scene carries per-screen directives; each board below is
// one thing MERIDIAN can throw onto a TV. ────────────────────────────

/** MERIDIAN's presence on the wall: a small breathing core. Gold and
 * fast while presenting, cyan and calm otherwise. Pure CSS. */
function WallOrb({ presenting }: { presenting: boolean }) {
  return <span className={`wall-orb${presenting ? ' presenting' : ''}`} aria-hidden />
}

/** Giant message board — MERIDIAN literally writing on the wall. */
function HeadlineBoard({ screen, live }: { screen: WallScreen; live: TodayLive | null }) {
  return (
    <div className="wall-headline">
      <div className="wall-headline-orb"><span className="wall-orb presenting big" /></div>
      <div className="wall-headline-text">{screen.text || 'MERIDIAN'}</div>
      {screen.stat ? (
        <div className="wall-headline-stat num">{screen.stat}</div>
      ) : live ? (
        <div className={`wall-headline-stat num ${pnlCls(live.pnl)}`}>
          {signed(live.pnl)} <small>today · {live.wins}W–{live.settled - live.wins}L</small>
        </div>
      ) : null}
    </div>
  )
}

/** Full-bleed copies P&L chart board (either TV). */
function ChartBoard({ copies, live }: { copies: CopiesRecord | null; live: TodayLive | null }) {
  return (
    <>
      <div className="wall-rtiles">
        <div className="wall-rtile">
          <div className="wall-tile-l">Today</div>
          <div className={`wall-tile-v num ${pnlCls(live?.pnl)}`}>{signed(live?.pnl)}</div>
        </div>
        <div className="wall-rtile">
          <div className="wall-tile-l">Settled today</div>
          <div className="wall-tile-v num">
            {live ? `${live.settled}` : '—'}
            {live && <small className="wall-wl"> {live.wins}W–{live.settled - live.wins}L</small>}
          </div>
        </div>
        <div className="wall-rtile">
          <div className="wall-tile-l">All-time copies</div>
          <div className={`wall-tile-v num ${pnlCls(copies?.total?.pnl)}`}>{signed(copies?.total?.pnl)}</div>
        </div>
      </div>
      <div className="wall-chart grow">
        <div className="wall-h">
          Copies P&L — daily<small>green/red bars daily · blue line cumulative · last bar live today</small>
        </div>
        <PnlChart copies={copies} live={live} />
      </div>
    </>
  )
}

/** MERIDIAN's projected chart, full-bleed on a TV: any series he is
 * holding (price history, P&L, whatever), drawn in the HUD language —
 * layered glow strokes (no SVG filters: Tizen-safe), area fill, grid,
 * endpoint badges. */
const SERIES_COLORS = ['#69e0ff', '#e8c877', '#3ddc97']

function ProjectedChartBoard({ screen }: { screen: WallScreen }) {
  const ch = screen.chart
  if (!ch || !ch.series.length) {
    return <div className="wall-empty">Waiting for MERIDIAN's chart…</div>
  }
  const W = 1600
  const H = 640
  const padL = 30
  const padR = 190
  const padT = 40
  const padB = 56
  const iw = W - padL - padR
  const ih = H - padT - padB
  const n = Math.max(...ch.series.map((s) => s.values.length))
  const all = ch.series.flatMap((s) => s.values)
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  if (hi === lo) { hi += 1; lo -= 1 }
  const pad = (hi - lo) * 0.08
  lo -= pad
  hi += pad
  const x = (i: number) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v: number) => padT + ((hi - v) / (hi - lo)) * ih
  const gridYs = [0, 0.25, 0.5, 0.75, 1].map((f) => padT + f * ih)
  const bar = ch.kind === 'bar'
  const bw = Math.max(4, (iw / Math.max(1, n)) * 0.55)
  return (
    <>
      {ch.title && <div className="wall-h chart-title">{ch.title}</div>}
      <div className="wall-chart grow">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" role="img"
          aria-label={ch.title || 'MERIDIAN chart'}>
          {gridYs.map((gy, gi) => (
            <g key={gi}>
              <line x1={padL} x2={W - padR} y1={gy} y2={gy}
                stroke="rgba(105,224,255,0.1)" strokeWidth={1} />
              <text x={W - padR + 12} y={gy + 6} fill="#71808f" fontSize={19}
                fontFamily="'JetBrains Mono', ui-monospace, monospace">
                {(hi - (gi / 4) * (hi - lo)).toFixed(Math.abs(hi - lo) < 5 ? 2 : 0)}
              </text>
            </g>
          ))}
          {ch.series.map((s, si) => {
            const col = SERIES_COLORS[si % SERIES_COLORS.length]
            if (bar) {
              return (
                <g key={si}>
                  {s.values.map((v, i) => (
                    <rect key={i} x={x(i) - bw / 2} y={Math.min(y(v), y(Math.max(lo, 0)))}
                      width={bw}
                      height={Math.max(2, Math.abs(y(v) - y(Math.max(lo, Math.min(hi, 0)))))}
                      rx={3} fill={col} opacity={0.85} />
                  ))}
                </g>
              )
            }
            const pts = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
            const area = `${padL},${padT + ih} ${pts} ${x(s.values.length - 1).toFixed(1)},${padT + ih}`
            const last = s.values[s.values.length - 1]
            return (
              <g key={si}>
                {si === 0 && (
                  <polygon points={area} fill={col} opacity={0.09} />
                )}
                <polyline points={pts} fill="none" stroke={col} strokeWidth={11}
                  opacity={0.16} strokeLinejoin="round" strokeLinecap="round" />
                <polyline points={pts} fill="none" stroke={col} strokeWidth={4}
                  strokeLinejoin="round" strokeLinecap="round" />
                <circle cx={x(s.values.length - 1)} cy={y(last)} r={8} fill={col} />
                <text x={x(s.values.length - 1) + 16} y={y(last) - 12 + si * 30}
                  fill={col} fontSize={24} fontWeight={700}
                  fontFamily="'JetBrains Mono', ui-monospace, monospace">
                  {s.name ? `${s.name} ` : ''}{Math.abs(last) < 5 ? last.toFixed(2) : Math.round(last).toLocaleString('en-US')}
                </text>
              </g>
            )
          })}
          {(ch.labels || []).length > 1 && [0, Math.floor((n - 1) / 2), n - 1].map((i) => (
            ch.labels![i] != null && (
              <text key={i} x={x(i)} y={H - 16} fill="#71808f" fontSize={19}
                textAnchor="middle"
                fontFamily="'JetBrains Mono', ui-monospace, monospace">
                {ch.labels![i]}
              </text>
            )
          ))}
        </svg>
      </div>
    </>
  )
}

/** Whale leaderboard board — the copy sleeves ranked by P&L. */
function WhalesBoard({ copies }: { copies: CopiesRecord | null }) {
  const rows = [...(copies?.by_whale || [])].sort((a, b) => b.pnl - a.pnl)
  return (
    <>
      <div className="wall-h">
        Whale copy sleeves<small>settled results · uncapped · since {copies?.since || '—'}</small>
      </div>
      <div className="wall-rtable whales">
        <div className="wall-rhead">
          <span>Whale</span>
          <span className="r">Settled</span>
          <span className="r">W–L</span>
          <span className="r hide-narrow">Staked</span>
          <span className="r">ROI</span>
          <span className="r">P&L</span>
        </div>
        {rows.length === 0 ? (
          <div className="wall-empty">{copies == null ? 'Loading the sleeves…' : 'No settled copies yet.'}</div>
        ) : rows.map((w) => (
          <div className="wall-rrow" key={w.whale}>
            <span className="d">{w.whale}</span>
            <span className="r">{w.settled.toLocaleString('en-US')}</span>
            <span className="r">{w.wins}–{w.losses}</span>
            <span className="r hide-narrow">{money(w.staked)}</span>
            <span className={`r ${pnlCls(w.roi)}`}>{w.roi == null ? '—' : `${(w.roi * 100).toFixed(1)}%`}</span>
            <span className={`r ${pnlCls(w.pnl)}`}>{signed(w.pnl)}</span>
          </div>
        ))}
      </div>
    </>
  )
}

/** Bottom ticker tape — content loops forever (duplicated for the
 * seamless CSS marquee). Kalshi wall runs prices; PM wall runs the
 * day's settles. */
function TapeBar({ items }: { items: { key: string; text: string; cls?: string }[] }) {
  if (items.length === 0) return null
  const strip = items.map((it) => (
    <span className={`wall-tape-item ${it.cls || ''}`} key={it.key}>{it.text}</span>
  ))
  const strip2 = items.map((it) => (
    <span className={`wall-tape-item ${it.cls || ''}`} key={`${it.key}-2`} aria-hidden>{it.text}</span>
  ))
  return (
    <div className="wall-tape" aria-hidden>
      <div className="wall-tape-inner">{strip}{strip2}</div>
    </div>
  )
}

// ── PM wall: daily copies P&L chart (self-contained SVG) ─────────────

const CHART = { W: 1200, H: 420, padL: 24, padR: 96, padT: 28, padB: 46 }

function PnlChart({ copies, live }: { copies: CopiesRecord | null; live: TodayLive | null }) {
  const today = etToday()
  // copies-record daily is newest-first; take the newest 30 CLOSED days
  // (today comes from /api/today-live as the live bar, so an early
  // settlement landing in `daily` never double-paints the day).
  const hist = (copies?.daily || [])
    .filter((d) => d.day < today)
    .slice(0, 30)
    .reverse()
  const bars: { day: string; pnl: number; isLive: boolean }[] =
    hist.map((d) => ({ day: d.day, pnl: d.pnl, isLive: false }))
  if (live) bars.push({ day: today, pnl: live.pnl, isLive: true })
  if (bars.length === 0) return <div className="wall-empty">Copies P&L warming up…</div>

  const { W, H, padL, padR, padT, padB } = CHART
  const iw = W - padL - padR
  const ih = H - padT - padB
  const n = bars.length
  const step = iw / n
  const bw = Math.max(6, step * 0.62)
  const maxPos = Math.max(0, ...bars.map((b) => b.pnl))
  const maxNeg = Math.max(0, ...bars.map((b) => -b.pnl))
  const span = maxPos + maxNeg || 1
  const zeroY = padT + (maxPos / span) * ih
  const scale = ih / span

  let acc = 0
  const cum = bars.map((b) => { acc += b.pnl; return acc })
  const cmin = Math.min(0, ...cum)
  const cmax = Math.max(0, ...cum)
  const cspan = cmax - cmin || 1
  const cy = (v: number) => padT + ((cmax - v) / cspan) * ih
  const cx = (i: number) => padL + i * step + step / 2
  const linePts = cum.map((v, i) => `${cx(i).toFixed(1)},${cy(v).toFixed(1)}`).join(' ')
  const total = cum[cum.length - 1] ?? 0
  const tickEvery = Math.max(1, Math.ceil(n / 6))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" role="img"
      aria-label="Daily copies P&L, last 30 days plus live today">
      <line x1={padL} x2={W - padR} y1={zeroY} y2={zeroY}
        stroke="rgba(255,255,255,0.18)" strokeWidth={1.5} strokeDasharray="5 5" />
      {bars.map((b, i) => {
        const h = Math.max(2, Math.abs(b.pnl) * scale)
        const y = b.pnl >= 0 ? zeroY - h : zeroY
        const fill = b.pnl > 0 ? '#17a06b' : b.pnl < 0 ? '#e03d36' : '#3a3f46'
        return (
          <rect key={b.day} className={b.isLive ? 'wall-live-bar' : undefined}
            x={cx(i) - bw / 2} y={y} width={bw} height={h} rx={3} fill={fill} />
        )
      })}
      <polyline points={linePts} fill="none" stroke="#69e0ff" strokeWidth={12}
        strokeLinejoin="round" strokeLinecap="round" opacity={0.14} />
      <polyline points={linePts} fill="none" stroke="#69e0ff" strokeWidth={4}
        strokeLinejoin="round" strokeLinecap="round" opacity={0.95} />
      {n > 0 && (
        <g>
          <circle cx={cx(n - 1)} cy={cy(total)} r={7} fill="#69e0ff" />
          <text x={Math.min(cx(n - 1) + 14, W - 4)} y={cy(total) + 6} fill="#69e0ff"
            fontSize={22} fontWeight={700}
            fontFamily="'JetBrains Mono', ui-monospace, monospace">
            {signed(total)}
          </text>
        </g>
      )}
      {bars.map((b, i) => (
        (i % tickEvery === 0 || b.isLive) && (
          <text key={`t-${b.day}`} x={cx(i)} y={H - 14} fill={b.isLive ? '#69e0ff' : '#71808f'}
            fontSize={17} fontWeight={b.isLive ? 700 : 500} textAnchor="middle"
            fontFamily="'JetBrains Mono', ui-monospace, monospace">
            {b.isLive ? 'LIVE' : b.day.slice(5)}
          </text>
        )
      ))}
    </svg>
  )
}

// ── Kalshi wall content ──────────────────────────────────────────────

const MAX_POS_ROWS = 12
const MAX_STRIP_CARDS = 8

function KalshiBook({ acct, cards }: {
  acct: DeskAccounts | null
  cards: { c: WallFeedCard; m: number; d: number }[]
}) {
  const k = acct?.kalshi
  const positions = [...(k?.positions || [])]
    .sort((a, b) => (b.value_usd ?? -1) - (a.value_usd ?? -1))
  const shown = positions.slice(0, MAX_POS_ROWS)
  return (
    <>
      <div className="wall-hero">
        <div className="wall-big">
          <div className="wall-biglabel">Balance</div>
          <div className="wall-bignum">{money(k?.balance_usd)}</div>
        </div>
        <div className="wall-tiles">
          <div className="wall-tile">
            <div className="wall-tile-l">Open exposure</div>
            <div className="wall-tile-v">{money(k?.exposure_usd)}</div>
          </div>
          <div className="wall-tile">
            <div className="wall-tile-l">Resting orders</div>
            <div className="wall-tile-v">{k?.resting ?? '—'}</div>
          </div>
        </div>
      </div>
      <div className="wall-h">
        Open positions
        {k?.degraded && <small>live feed degraded — engine-known book, no marks</small>}
      </div>
      <div className="wall-posgrid">
        <div className="wall-thead">
          <span>Market</span>
          <span className="r">Qty</span>
          <span className="r hide-narrow">Cost</span>
          <span className="r hide-narrow">Mark</span>
          <span className="r">Value</span>
          <span className="r">Unrealized</span>
        </div>
        <div className="wall-rows">
          {acct == null ? (
            <div className="wall-empty">Connecting to the desk…</div>
          ) : shown.length === 0 ? (
            <div className="wall-empty">No open Kalshi positions.</div>
          ) : shown.map((p) => (
            <div className="wall-row" key={p.ticker}>
              <span className="wall-mktname">
                {prettyTicker(p.ticker)}<small>{p.ticker}</small>
              </span>
              <span className="r">{Math.round(p.qty)}</span>
              <span className="r hide-narrow">{money(p.cost_usd)}</span>
              <span className="r hide-narrow">{cents(p.mark_bid)}</span>
              <span className="r">{money(p.value_usd)}</span>
              <span className={`r ${pnlCls(p.unrealized)}`}>{signed(p.unrealized)}</span>
            </div>
          ))}
        </div>
        {positions.length > shown.length && (
          <div className="wall-more">+ {positions.length - shown.length} more open positions</div>
        )}
      </div>
      <div className="wall-h">
        Live markets<small>yes ask · biggest movers first</small>
      </div>
      <div className="wall-strip">
        {cards.length === 0 ? (
          <div className="wall-empty">Market feed warming up…</div>
        ) : cards.slice(0, MAX_STRIP_CARDS).map(({ c, m, d }) => (
          <div className="wall-mkt" key={c.id}>
            <div className="wall-mkt-title">{c.title}</div>
            <div className="wall-mkt-outs">
              {c.outcomes.slice(0, 2).map((o, i) => (
                <div className="wall-mkt-out" key={o.id || i}>
                  <span>{o.label}</span>
                  <b className="wall-mkt-px">{cents(o.price)}</b>
                </div>
              ))}
              {m >= 0.005 && (
                <div className={`wall-mkt-d ${d >= 0 ? 'pos' : 'neg'}`}>
                  {d >= 0 ? '▲' : '▼'} {Math.abs(Math.round(m * 100))}¢ move
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

// ── Polymarket wall content ──────────────────────────────────────────

function PolymarketBook({ acct, copies, live }: {
  acct: DeskAccounts | null
  copies: CopiesRecord | null
  live: TodayLive | null
}) {
  const pm = acct?.polymarket
  // Money display discipline (owner, NON-NEGOTIABLE): the PM headline
  // is trading_capital with its exact committed-capital label — never
  // any cash figure, never a derived one. Only when the API withholds
  // trading_capital does the board fall back to the account value.
  const tc = pm?.trading_capital
  const bigLabel = tc != null ? 'Trading capital — incl. committed capital' : 'Account value'
  const bigVal = tc != null ? tc : acct?.totals.value ?? null
  const positions = [...(pm?.positions || [])]
    .sort((a, b) => (b.value ?? -1) - (a.value ?? -1))
  const shown = positions.slice(0, MAX_POS_ROWS)
  return (
    <>
      <div className="wall-hero">
        <div className="wall-big">
          <div className="wall-biglabel">{bigLabel}</div>
          <div className="wall-bignum">{money(bigVal)}</div>
        </div>
        <div className="wall-tiles">
          <div className="wall-tile">
            <div className="wall-tile-l">Open value</div>
            <div className="wall-tile-v">{money(pm?.open_value)}</div>
          </div>
          <div className="wall-tile">
            <div className="wall-tile-l">Unsettled</div>
            <div className="wall-tile-v">{money(pm?.unsettled_funds)}</div>
          </div>
        </div>
      </div>
      <div className="wall-chart">
        <div className="wall-h">
          Copies P&L — daily<small>green/red bars daily · blue line cumulative · last bar live today</small>
        </div>
        <PnlChart copies={copies} live={live} />
      </div>
      <div className="wall-h">Open platform positions</div>
      <div className="wall-posgrid pm">
        <div className="wall-thead">
          <span>Market</span>
          <span className="r">Qty</span>
          <span className="r hide-narrow">Cost</span>
          <span className="r">Value</span>
          <span className="r">Unrealized</span>
        </div>
        <div className="wall-rows">
          {acct == null ? (
            <div className="wall-empty">Connecting to the desk…</div>
          ) : shown.length === 0 ? (
            <div className="wall-empty">No open platform positions.</div>
          ) : shown.map((p) => (
            <div className="wall-row" key={`${p.market_slug}-${p.outcome}`}>
              <span className="wall-mktname">
                {p.outcome || p.title || p.market_slug}
                <small>{p.title || p.market_slug}</small>
              </span>
              <span className="r">{Math.round(p.qty)}</span>
              <span className="r hide-narrow">{money(p.cost)}</span>
              <span className="r">{money(p.value)}</span>
              <span className={`r ${pnlCls(p.unrealized)}`}>{signed(p.unrealized)}</span>
            </div>
          ))}
        </div>
        {positions.length > shown.length && (
          <div className="wall-more">+ {positions.length - shown.length} more open positions</div>
        )}
      </div>
    </>
  )
}

// ── The wall board (auth'd shell: pollers, watchdogs, mode switch) ───

function WallBoard({ venue }: { venue: WallVenue }) {
  useNightlyReload()
  const shift = usePixelShift()
  const now = useClock(1000)
  const isK = venue === 'kalshi'

  const acct = useWallPoll<DeskAccounts>(
    () => wallApi('/api/desk/accounts'), 15_000, true, 'acct')
  const state = useWallPoll<WallState>(
    () => wallApi('/api/wall/state'), 10_000, true, 'state')
  const feed = useWallPoll<WallFeedCard[]>(
    () => wallFeed('kalshi', 'all'), 25_000, isK, 'feed')

  // MERIDIAN SCENES: resolve what THIS screen should draw. A scene's
  // TTL is enforced client-side — when it lapses the wall hands itself
  // back to the live book without waiting for anyone.
  const st = state.data
  const ttlLapsed = st?.set_at != null && st.ttl_s != null
    && now / 1000 > st.set_at + st.ttl_s
  const effMode: WallState['mode'] = ttlLapsed ? 'book' : (st?.mode ?? 'book')
  const myScreen: WallScreen | null = effMode === 'scene'
    ? (st?.screens?.[venue]
       ?? { kind: 'headline', text: st?.headline || 'MERIDIAN' })
    : null
  const kind = effMode === 'scene' ? (myScreen?.kind ?? 'book') : effMode
  const presenting = effMode === 'scene'
  const report = kind === 'report'

  // today-live runs on BOTH walls now (header strip + PM chart + the
  // settle tape); copies powers the PM chart plus any scene chart or
  // whale board on either TV.
  const live = useWallPoll<TodayLive>(
    () => wallApi('/api/today-live'), 20_000, true, 'live')
  const copies = useWallPoll<CopiesRecord>(
    () => wallApi('/api/copies-record'), 120_000,
    !isK || kind === 'chart' || kind === 'whales', 'copies')
  const breakdown = useWallPoll<DailyBreakdown>(
    () => wallApi('/api/daily-breakdown'), 60_000, report, 'breakdown')

  // Movers: remember each outcome's last polled price; a card's score
  // is its biggest inter-poll move (decayed 0.6x per quiet poll so a
  // mover holds its spot for a few minutes, then drifts back).
  const prevPx = useRef(new Map<string, number>())
  const movesRef = useRef(new Map<string, { m: number; d: number }>())
  const cards = useMemo(() => {
    const cs = feed.data || []
    const prev = prevPx.current
    const moves = movesRef.current
    for (const c of cs) {
      let mag = 0
      let dir = 0
      for (const o of c.outcomes) {
        if (o.price == null || !o.id) continue
        const p = prev.get(o.id)
        if (p != null && Math.abs(o.price - p) > mag) {
          mag = Math.abs(o.price - p)
          dir = o.price - p
        }
        prev.set(o.id, o.price)
      }
      const old = moves.get(c.id)
      if (mag > 0) moves.set(c.id, { m: mag, d: dir })
      else if (old) moves.set(c.id, { m: old.m * 0.6, d: old.d })
    }
    const scored = cs.map((c) => {
      const mv = moves.get(c.id)
      return { c, m: mv?.m ?? 0, d: mv?.d ?? 0 }
    })
    // "Biggest movers first" only once we HAVE history (a second poll);
    // until then the feed's own volume-desc order stands.
    if (scored.some((s) => s.m >= 0.005)) scored.sort((a, b) => b.m - a.m)
    return scored
  }, [feed.data])

  const newest = [acct.lastOk, state.lastOk, feed.lastOk, live.lastOk,
    copies.lastOk, breakdown.lastOk]
    .reduce<number | null>((a, b) => (b != null && (a == null || b > a) ? b : a), null)

  // Ticker tape: Kalshi wall runs live prices; PM wall runs the day's
  // settles as they land.
  const tape = isK
    ? cards.slice(0, 14).map(({ c }) => ({
        key: c.id,
        text: `${c.title}  ${c.outcomes.slice(0, 2)
          .map((o) => `${o.label} ${cents(o.price)}`).join(' · ')}`,
      }))
    : (live.data?.recent || []).slice(0, 14).map((t, i) => ({
        key: `${t.title}-${i}`,
        text: `${t.pnl >= 0 ? '✓' : '✕'} ${t.outcome || t.title}  ${signed(t.pnl)}`,
        cls: t.pnl >= 0 ? 'pos' : 'neg',
      }))

  // The scene report range rides the screen directive when it has one.
  const reportState: WallState | null = myScreen?.kind === 'report'
    ? { ...(st as WallState), from: myScreen.from || st?.from || null,
        to: myScreen.to || st?.to || null }
    : state.data

  return (
    <div className={`wall${presenting ? ' presenting' : ''}`} style={shift}>
      <div className="wall-scan" aria-hidden />
      <div className="wall-top">
        <div className="wall-brand">
          <WallOrb presenting={presenting} />
          BETTORTOKEN <b>· {isK ? 'KALSHI' : 'POLYMARKET'}</b>
        </div>
        {live.data && (
          <div className="wall-today num">
            <i>TODAY</i>
            <b className={pnlCls(live.data.pnl)}>{signed(live.data.pnl)}</b>
            <span>{live.data.wins}W–{live.data.settled - live.data.wins}L</span>
          </div>
        )}
        <div className={`wall-mode${report ? ' report' : ''}${presenting ? ' scene' : ''}`}>
          {presenting ? 'MERIDIAN · PRESENTING'
            : report ? 'REPORT · LAST 7 DAYS' : 'LIVE BOOKS'}
        </div>
        <div className="wall-clock num">{new Date(now).toLocaleTimeString('en-US', {
          timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', second: '2-digit',
        })} <i>ET</i></div>
      </div>
      {presenting && st?.headline && (
        <div className="wall-banner"><i>◉</i> {st.headline}</div>
      )}
      {kind === 'report' ? (
        <ReportBoard state={reportState} breakdown={breakdown.data} />
      ) : kind === 'chart' ? (
        myScreen?.chart
          ? <ProjectedChartBoard screen={myScreen} />
          : <ChartBoard copies={copies.data} live={live.data} />
      ) : kind === 'whales' ? (
        <WhalesBoard copies={copies.data} />
      ) : kind === 'headline' && myScreen ? (
        <HeadlineBoard screen={myScreen} live={live.data} />
      ) : isK ? (
        <KalshiBook acct={acct.data} cards={cards} />
      ) : (
        <PolymarketBook acct={acct.data} copies={copies.data} live={live.data} />
      )}
      <TapeBar items={tape} />
      <LiveDot newest={newest} now={now} />
    </div>
  )
}

// ── Routes ───────────────────────────────────────────────────────────

/** TV board — /wall/kalshi and /wall/polymarket (integrator wires both). */
export function WallPage({ venue }: { venue: WallVenue }) {
  const [authed, onUnlocked] = useWallSession()
  if (!authed) {
    return <WallGate label={`${venue.toUpperCase()} WALL`} onUnlocked={onUnlocked} />
  }
  return <WallBoard venue={venue} />
}

/** Desk phone/Mac page — /wall/control: flips both TVs between modes. */
export function WallControl() {
  const [authed, onUnlocked] = useWallSession()
  const [pw, setPw] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [gateErr, setGateErr] = useState('')
  const [busy, setBusy] = useState<'book' | 'report' | null>(null)
  const [okMsg, setOkMsg] = useState('')
  const [err, setErr] = useState('')
  const [bump, setBump] = useState(0)
  const now = useClock(1000)
  const state = useWallPoll<WallState>(
    () => wallApi('/api/wall/state'), 10_000, authed, `cstate-${bump}`)

  const unlock = async () => {
    const secret = pw.trim()
    if (!secret || unlocking) return
    setUnlocking(true)
    setGateErr('')
    const r = await wallUnlock(secret)
    if (r.ok) {
      // Same password also mints a real desk session (sessionStorage,
      // 12h) — broadcast needs a desk-role credential; the wall token
      // alone is read-only by contract.
      await deskUnlock(secret)
      setPw('')
      onUnlocked()
    } else {
      setGateErr(r.error || 'Wrong password.')
    }
    setUnlocking(false)
  }

  const send = async (mode: 'book' | 'report') => {
    if (busy) return
    setBusy(mode)
    setOkMsg('')
    setErr('')
    const body = mode === 'book'
      ? { mode }
      : { mode, from: etDaysAgo(6), to: etToday() }
    const r = await wallBroadcast(body)
    if (r.ok) {
      setOkMsg(mode === 'book'
        ? 'Both walls are showing the live books.'
        : 'Both walls are showing the 7-day report.')
      setBump((b) => b + 1) // refresh the state indicator immediately
    } else {
      setErr(r.error || 'Broadcast failed.')
    }
    setBusy(null)
  }

  const st = state.data
  const setAgo = st?.set_at != null
    ? Math.max(0, Math.round(now / 1000 - st.set_at))
    : null
  const origin = window.location.origin

  if (!authed) {
    return (
      <>
        <h1>Wall Control</h1>
        <div className="card wallc">
          <p>Enter the desk password to control the office TVs.</p>
          <div className="dk-gate-row" style={{ display: 'flex', gap: 8 }}>
            <input
              className="input" type="password" value={pw} autoFocus
              autoCapitalize="none" autoCorrect="off" spellCheck={false}
              style={{ flex: 1 }}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void unlock() }}
              aria-label="Desk password"
            />
            <button className="btn primary" onClick={() => void unlock()} disabled={unlocking}>
              {unlocking ? 'Unlocking…' : 'Unlock'}
            </button>
          </div>
          <p className="wallc-err">{gateErr}</p>
        </div>
      </>
    )
  }

  return (
    <>
      <h1>Wall Control</h1>
      <p className="sub">Flips both office TVs between the live books and the weekly report.</p>
      <div className="wallc">
        <div className="card">
          <div className="wallc-state">
            <i />
            <span>Walls now showing:</span>
            <b>{st == null ? '…' : st.mode === 'report' ? 'REPORT' : 'BOOKS'}</b>
            {st?.mode === 'report' && st.from && st.to && (
              <span className="mono">{st.from} → {st.to}</span>
            )}
            {setAgo != null && (
              <span>· set {setAgo < 120 ? `${setAgo}s` : `${Math.round(setAgo / 60)}m`} ago</span>
            )}
          </div>
        </div>
        <div className="wallc-btns">
          <button
            className={`wallc-btn${st?.mode === 'book' ? ' on' : ''}`}
            disabled={busy != null}
            onClick={() => void send('book')}
          >
            {busy === 'book' ? 'SWITCHING…' : 'SHOW BOOKS'}
            <small>Live balances, positions and markets on both TVs</small>
          </button>
          <button
            className={`wallc-btn${st?.mode === 'report' ? ' on' : ''}`}
            disabled={busy != null}
            onClick={() => void send('report')}
          >
            {busy === 'report' ? 'SWITCHING…' : 'SHOW REPORT (last 7 days)'}
            <small>{etDaysAgo(6)} → {etToday()} · ET days</small>
          </button>
        </div>
        {okMsg && <p className="wallc-okmsg">{okMsg}</p>}
        {err && <p className="wallc-err">{err}</p>}
        <div className="card wallc-hints">
          <b>TV setup</b><br />
          Open on the Kalshi TV: <code>{origin}/wall/kalshi</code><br />
          Open on the Polymarket TV: <code>{origin}/wall/polymarket</code><br />
          Enter the desk password once per TV — the wall token lasts 7 days, renews
          itself silently, and the board hard-reloads nightly at 4:10am ET. TVs pick
          up a mode change within 10 seconds.
        </div>
      </div>
    </>
  )
}

/** Default export for the lazy route: /wall/kalshi, /wall/polymarket,
 * /wall/control all resolve here (App.tsx mounts one "/wall/*" route). */
export default function WallRouter() {
  const { pathname } = useLocation()
  if (pathname.endsWith('/control')) return <WallControl />
  return <WallPage venue={pathname.includes('kalshi') ? 'kalshi' : 'polymarket'} />
}
