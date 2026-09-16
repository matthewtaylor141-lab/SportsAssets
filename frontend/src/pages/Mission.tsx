import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { deskAdminToken, deskToken } from '../lib/desk'
import { fillPing, knock, sonar, useSound } from '../lib/sound'
import { useLiveFeed } from '../lib/sse'
import type { Trade } from '../lib/types'
import { BrandLockup } from '../components/Brand'
import { Odometer } from '../components/Odometer'
import { useOrderStream, type OrderEvt } from '../components/DeskOrderStream'
import '../styles/mission.css'

/* MISSION CONTROL (owner order 2026-08-29): the copy pipeline as
 * theater. The product's magic is a sub-second chain detection firing
 * a proportional copy — this room makes that visible the instant it
 * happens: whale blips bloom on a radar sweep, each detection card
 * carries its live latency, and our own orders light the funnel
 * DETECTED → MAPPED → FIRED → FILLED as the SSE events land. Data
 * discipline is the same as every page: everything here is the live
 * ledger stream (/stream + /api/feed seed + /api/desk/stream when the
 * desk is unlocked) — nothing is simulated, nothing is replayed. */

const fmtUsd = (v: number) =>
  `$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
const fmtSigned = (v: number) => `${v < 0 ? '−' : '+'}${fmtUsd(v)}`

/** Deterministic radar position per whale: hash → angle/orbit. Stable
 * across renders and sessions so the room becomes spatial memory. */
function radarPos(name: string): { x: number; y: number } {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  const angle = (h % 360) * (Math.PI / 180)
  const orbit = 0.35 + ((h >>> 9) % 1000) / 1000 * 0.52
  return { x: 50 + Math.cos(angle) * orbit * 46, y: 50 + Math.sin(angle) * orbit * 46 }
}

type Detection = Trade & { seenAt: number }

const LANES = ['DETECTED', 'MAPPED', 'FIRED', 'FILLED'] as const

/** Order lifecycle → funnel stage. `submitting` is the mapper handing
 * to the executor; open/filled are venue truth from the SSE trigger. */
function stageOf(evt: OrderEvt): number {
  switch (evt.status) {
    case 'submitting': return 1
    case 'open': return 2
    // 'merged': an add leg that filled and was booked onto its standing
    // row (adds, 2026-09-02) -- a fill, not a row still in flight
    case 'filled': case 'settled': case 'cashed_out': case 'merged': return 3
    default: return evt.status === 'rejected' || evt.status === 'error'
      || evt.status === 'unfilled' || evt.status === 'cancelled' ? -1 : 1
  }
}

function LatencyBig({ v }: { v: number | null }) {
  if (v == null) return <span className="mc-lat-v muted">—</span>
  const beat = v <= 0
  return (
    <span className={`mc-lat-v ${beat ? 'pos' : v < 5 ? '' : 'neg'}`}>
      {beat ? '−' : '+'}{Math.abs(v).toFixed(2)}s
    </span>
  )
}

export default function Mission() {
  const { armed, toggle } = useSound()
  const [dets, setDets] = useState<Detection[]>([])
  const [flare, setFlare] = useState<Map<string, number>>(new Map())
  const [today, setToday] = useState<{ pnl: number; settled: number } | null>(null)
  const [open, setOpen] = useState<{ count: number; stake: number } | null>(null)
  const seen = useRef<Set<number>>(new Set())
  const deskUnlocked = !!(deskToken() ?? deskAdminToken())
  const { toasts } = useOrderStream(deskUnlocked)
  const [orders, setOrders] = useState<OrderEvt[]>([])
  const lastOrder = useRef<Map<number, string>>(new Map())

  // Seed with the last 40 ledger rows so the room is alive at open,
  // then let the SSE stream take over. BUYs only — the radar tracks
  // entries; exits ride the order funnel when we mirror them.
  useEffect(() => {
    let dead = false
    api<Trade[]>('/api/feed?limit=40&side=BUY')
      .then((rows) => {
        if (dead) return
        rows.forEach((r) => seen.current.add(r.id))
        setDets(rows.map((r) => ({ ...r, seenAt: 0 })))
      })
      .catch(() => { /* stream fills in */ })
    return () => { dead = true }
  }, [])

  useLiveFeed((t) => {
    if (t.side !== 'BUY' || seen.current.has(t.id)) return
    seen.current.add(t.id)
    sonar()
    const who = (t.whale_username || '').toLowerCase()
    setFlare((m) => new Map(m).set(who, Date.now()))
    setDets((prev) => [{ ...t, seenAt: Date.now() }, ...prev].slice(0, 40))
  })

  // Order stream → funnel rows (latest state per order id wins) +
  // audio: a fill earns the ping, a refusal the low knock.
  useEffect(() => {
    for (const evt of toasts) {
      const prev = lastOrder.current.get(evt.id)
      if (prev === evt.status) continue
      lastOrder.current.set(evt.id, evt.status)
      if (evt.status === 'filled' || evt.status === 'merged') fillPing()
      if (evt.status === 'rejected' || evt.status === 'error') knock()
    }
    setOrders((cur) => {
      const byId = new Map(cur.map((o) => [o.id, o]))
      toasts.forEach((t) => byId.set(t.id, t))
      return [...byId.values()].sort((a, b) => b.at - a.at).slice(0, 12)
    })
  }, [toasts])

  // Headline gauges: today's settled P&L and open exposure, from the
  // same public record endpoint the Performance page trusts.
  useEffect(() => {
    let dead = false
    const go = () => api<{ today?: { pnl: number; settled: number } | null
                          open?: { count: number; stake: number | null } }>(
      '/api/copies-record')
      .then((d) => {
        if (dead) return
        if (d.today) setToday({ pnl: d.today.pnl, settled: d.today.settled })
        if (d.open) setOpen({ count: d.open.count, stake: d.open.stake ?? 0 })
      })
      .catch(() => { /* keep last */ })
    go()
    const t = setInterval(go, 30_000)
    return () => { dead = true; clearInterval(t) }
  }, [])

  // Radar roster: every whale seen in the detection window, flaring
  // for 90s after their latest fill.
  const [, forceTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => forceTick((v) => v + 1), 5000)
    return () => clearInterval(t)
  }, [])
  const whales = useMemo(() => {
    const names = new Map<string, Detection>()
    for (const d of dets) {
      const k = (d.whale_username || '?').toLowerCase()
      if (!names.has(k)) names.set(k, d)
    }
    return [...names.entries()]
  }, [dets])

  const lastLat = dets.find((d) => d.latency_s != null)?.latency_s ?? null
  const p50 = useMemo(() => {
    const xs = dets.map((d) => d.latency_s).filter((x): x is number => x != null)
      .sort((a, b) => a - b)
    return xs.length ? xs[Math.floor(xs.length / 2)] : null
  }, [dets])
  const perHour = useMemo(() => {
    const cutoff = Date.now() - 3600_000
    return dets.filter((d) => new Date(d.detected_at).getTime() > cutoff).length
  }, [dets])

  return (
    <div className="mc-room">
      <div className="mc-scan" aria-hidden />
      <header className="mc-head">
        <BrandLockup height={26} />
        <span className="mc-title">MISSION CONTROL</span>
        <span className="mc-live"><i className="pulse-dot" /> LIVE LEDGER STREAM</span>
        <span className="mc-spacer" />
        <button className={`mc-sound${armed ? ' on' : ''}`} onClick={toggle}
          title="Sonar per detection, ping per fill — synthesized, subtle">
          {armed ? '🔊 SOUND ON' : '🔇 SOUND OFF'}
        </button>
        <Link to="/" className="mc-exit" aria-label="Exit Mission Control">✕</Link>
      </header>

      <div className="mc-grid">
        {/* ── Radar ── */}
        <section className="mc-radar-card nd-reticle">
          <div className="mc-card-t">WHALE RADAR
            <span className="muted"> · {whales.length} on scope · blip = fill</span>
          </div>
          <div className="mc-radar">
            <div className="mc-radar-rings" aria-hidden>
              <i /><i /><i /><span className="mc-sweep" />
            </div>
            {whales.map(([name, d]) => {
              const p = radarPos(name)
              const hot = (flare.get(name) ?? 0) > Date.now() - 90_000
              return (
                <span key={name}
                  className={`mc-blip${hot ? ' hot' : ''}`}
                  style={{ left: `${p.x}%`, top: `${p.y}%` }}
                  title={`${d.whale_username} · last ${fmtUsd(d.notional)} ${d.sport}`}>
                  <b>{(d.whale_username || '?').slice(0, 10)}</b>
                </span>
              )
            })}
          </div>
          <div className="mc-lat">
            <div>
              <span className="mc-lat-k">LAST DETECTION</span>
              <LatencyBig v={lastLat} />
              <span className="mc-lat-s muted">chain print → our ledger</span>
            </div>
            <div>
              <span className="mc-lat-k">P50 · WINDOW</span>
              <LatencyBig v={p50} />
              <span className="mc-lat-s muted">negative = we see it before the venue prints it</span>
            </div>
          </div>
        </section>

        {/* ── Detection stream ── */}
        <section className="mc-stream-card nd-reticle">
          <div className="mc-card-t">DETECTIONS
            <span className="muted"> · {perHour} last hour</span>
          </div>
          <div className="mc-stream">
            {dets.slice(0, 14).map((d) => (
              <div key={d.id}
                className={`mc-det${d.seenAt && Date.now() - d.seenAt < 6000 ? ' fresh' : ''}`}>
                <span className="mc-det-who">{d.whale_username}</span>
                <span className="mc-det-mkt" title={d.market_title || d.market_slug || ''}>
                  {(d.market_title || d.market_slug || d.event_slug || '—').slice(0, 44)}
                </span>
                <span className="mc-det-num mono">
                  {fmtUsd(d.notional)} @ {Math.round(d.price * 100)}¢
                </span>
                <span className={`mc-det-lat mono${(d.latency_s ?? 1) <= 0 ? ' pos' : ''}`}>
                  {d.latency_s == null ? '' :
                    `${d.latency_s <= 0 ? '−' : '+'}${Math.abs(d.latency_s).toFixed(1)}s`}
                </span>
              </div>
            ))}
            {!dets.length && <p className="muted">Listening for whale fills…</p>}
          </div>
        </section>

        {/* ── Copy funnel ── */}
        <section className="mc-funnel-card nd-reticle">
          <div className="mc-card-t">COPY FUNNEL
            <span className="muted"> · our orders, live from the blotter</span>
          </div>
          {deskUnlocked ? (
            <div className="mc-funnel">
              <div className="mc-lanes" aria-hidden>
                {LANES.map((l) => <span key={l}>{l}</span>)}
              </div>
              {orders.map((o) => {
                const st = stageOf(o)
                return (
                  <div key={o.id} className={`mc-ord${st < 0 ? ' dead' : ''}`}>
                    <span className="mc-ord-who">{o.whale || 'manual'}</span>
                    <span className="mc-ord-mkt">{(o.slug || '').slice(0, 34)}</span>
                    <span className="mc-ord-track" aria-hidden>
                      {LANES.map((_, i) => (
                        <i key={i} className={
                          st < 0 && i === 1 ? 'x'
                            : i <= Math.max(st, 0) && st >= 0 ? 'on' : ''} />
                      ))}
                    </span>
                    <span className={`mc-ord-st${st === 3 ? ' pos' : st < 0 ? ' neg' : ''}`}>
                      {st < 0 ? o.status.toUpperCase() : LANES[Math.max(st, 0)]}
                    </span>
                  </div>
                )
              })}
              {!orders.length && (
                <p className="muted mc-idle">Armed. The next mapped whale
                  entry animates through this funnel the second it fires.</p>
              )}
            </div>
          ) : (
            <p className="muted mc-idle">
              Order funnel rides the desk stream — unlock the <Link to="/desk">Desk</Link> once
              in this browser and live order lifecycles appear here.
            </p>
          )}
        </section>

        {/* ── Gauges ── */}
        <section className="mc-gauges">
          <div className="mc-gauge nd-reticle">
            <span className="mc-gauge-k">TODAY · SETTLED</span>
            <Odometer className={`mc-gauge-v ${((today?.pnl ?? 0) >= 0) ? 'pos' : 'neg'}`}
              value={today?.pnl ?? 0} render={fmtSigned} countUp />
            <span className="mc-gauge-s muted">{today?.settled ?? 0} settlements</span>
          </div>
          <div className="mc-gauge nd-reticle">
            <span className="mc-gauge-k">ON THE TABLE</span>
            <Odometer className="mc-gauge-v" value={open?.stake ?? 0}
              render={fmtUsd} countUp />
            <span className="mc-gauge-s muted">{open?.count ?? 0} open copies</span>
          </div>
          <div className="mc-gauge nd-reticle">
            <span className="mc-gauge-k">DETECTIONS / HR</span>
            <Odometer className="mc-gauge-v" value={perHour}
              render={(v) => `${Math.round(v)}`} countUp />
            <span className="mc-gauge-s muted">whale entries on scope</span>
          </div>
        </section>
      </div>
    </div>
  )
}
