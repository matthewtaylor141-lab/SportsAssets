/* PriceChart — Wave-2 desk price history. Dependency-free SVG in the
 * house chart grammar (src/jarvis/chart.tsx is the reference): a single
 * 2px series line on a fixed 0–100¢ axis, recessive horizontal grid,
 * full-surface pointer target driving a crosshair + tooltip, a direct
 * end label for the last price, and an optional dashed entry-price
 * line. One series → no legend (the caption names it).
 *
 * Color never lives here: every mark wears a .pc-* class whose tokens
 * are mapped in styles/desk2.css — the dark app theme uses
 * --s1/--ink/--muted, the light .vdesk / .dxm venue skins remap the
 * same slots to their --vda-ink/--vd-* inks — so the one component is
 * legible on every desk surface.
 *
 * Data comes from GET /api/desk/history (venue error ⇒ points:[], the
 * chart degrades to an empty note, the desk never breaks). A module
 * cache dedupes concurrent readers of the same (venue,id,hours) — the
 * game view and the compact ticket chart share one fetch. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { deskApi } from '../lib/desk'

export type HistoryVenue = 'polymarket-us' | 'kalshi'
export interface HistoryPoint { t: number; p: number }

interface HistoryResp {
  venue: string; id: string; hours: number
  points: HistoryPoint[]; error?: string
}

// ── Shared fetch cache (55s TTL, under the server's 60s cache) ─────
const HIST_TTL_MS = 55_000
const histCache = new Map<string, { at: number; points: HistoryPoint[] }>()
const histInflight = new Map<string, Promise<HistoryPoint[]>>()

export function fetchHistory(
  venue: HistoryVenue, id: string, hours: number,
): Promise<HistoryPoint[]> {
  const key = `${venue}|${id}|${hours}`
  const hit = histCache.get(key)
  if (hit && Date.now() - hit.at < HIST_TTL_MS) return Promise.resolve(hit.points)
  const live = histInflight.get(key)
  if (live) return live
  const p = deskApi<HistoryResp>(
    `/api/desk/history?venue=${venue}&id=${encodeURIComponent(id)}&hours=${hours}`)
    .then((r) => {
      const pts = (r.points || [])
        .filter((q) => q && Number.isFinite(q.t) && Number.isFinite(q.p))
        .sort((a, b) => a.t - b.t)
      histCache.set(key, { at: Date.now(), points: pts })
      return pts
    })
    .finally(() => histInflight.delete(key))
  histInflight.set(key, p)
  return p
}

/** History for one market, refreshed every 60s while mounted.
 *  null = loading · [] = venue had nothing (chart shows a quiet note). */
export function useDeskHistory(
  venue: HistoryVenue | undefined, id: string | undefined, hours: number,
): HistoryPoint[] | null {
  const [points, setPoints] = useState<HistoryPoint[] | null>(null)
  useEffect(() => {
    setPoints(null)
    if (!venue || !id) return
    let dead = false
    const load = () =>
      fetchHistory(venue, id, hours)
        .then((pts) => { if (!dead) setPoints(pts) })
        .catch(() => { if (!dead) setPoints((prev) => prev ?? []) })
    load()
    const t = window.setInterval(load, 60_000)
    return () => { dead = true; window.clearInterval(t) }
  }, [venue, id, hours])
  return points
}

// ── Geometry ───────────────────────────────────────────────────────
const FULL = { w: 640, h: 220, pad: { l: 40, r: 14, t: 14, b: 26 } }
const MINI = { w: 320, h: 124, pad: { l: 30, r: 10, t: 10, b: 18 } }

const fmtWhen = (t: number, hours: number): string => {
  const d = new Date(t * 1000)
  return hours <= 24
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'numeric', day: 'numeric' })
}
const fmtTip = (t: number, hours: number): string => {
  const d = new Date(t * 1000)
  return hours <= 24
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : `${d.toLocaleDateString([], { month: 'numeric', day: 'numeric' })} ${
      d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

export function PriceChart({ points, hours, entry, compact, caption }: {
  points: HistoryPoint[] | null   // null = loading
  hours: number
  entry?: number | null           // avg entry price 0..1 → dashed line
  compact?: boolean
  caption?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dim = compact ? MINI : FULL
  const { w: W, h: H, pad: PAD } = dim

  const model = useMemo(() => {
    const pts = points || []
    const n = pts.length
    const t0 = n ? pts[0].t : 0
    const t1 = n ? pts[n - 1].t : 1
    const span = Math.max(1, t1 - t0)
    const px = (t: number) =>
      PAD.l + (W - PAD.l - PAD.r) * (n <= 1 ? 0.5 : (t - t0) / span)
    // Fixed 0–100¢ axis: the price IS a probability, the frame says so.
    const py = (c: number) =>
      PAD.t + (H - PAD.t - PAD.b) * (1 - Math.max(0, Math.min(100, c)) / 100)
    return { pts, n, t0, t1, px, py }
  }, [points, W, H, PAD])

  const { pts, n, t0, t1, px, py } = model
  const yTicks = compact ? [0, 50, 100] : [0, 25, 50, 75, 100]
  // 4 time labels across the domain (2 when compact), edge-anchored.
  const xTicks = (compact ? [0, 1] : [0, 1 / 3, 2 / 3, 1]).map((f) => t0 + (t1 - t0) * f)

  const onMove = (e: React.PointerEvent) => {
    if (n === 0) return
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const fx = ((e.clientX - rect.left) / rect.width) * W
    let best = 0
    let bestD = Infinity
    for (let i = 0; i < n; i++) {
      const d = Math.abs(px(pts[i].t) - fx)
      if (d < bestD) { bestD = d; best = i }
    }
    setHover(best)
  }

  const entryC = entry != null && entry > 0 ? Math.max(0, Math.min(1, entry)) * 100 : null
  const last = n > 0 ? pts[n - 1] : null

  return (
    <figure className={`pc-chart${compact ? ' compact' : ''}`}>
      {caption && <figcaption>{caption}</figcaption>}
      {points === null ? (
        <div className="pc-note">Loading price history…</div>
      ) : n < 2 ? (
        <div className="pc-note">No price history for this market yet.</div>
      ) : (
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={caption || 'Price history'}
             onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {/* recessive horizontal grid + ¢ axis */}
          {yTicks.map((c) => (
            <g key={c}>
              <line className="pc-grid" x1={PAD.l} x2={W - PAD.r}
                    y1={py(c)} y2={py(c)} strokeWidth="1" />
              <text className="pc-mut" x={PAD.l - 6} y={py(c) + 3.5}
                    textAnchor="end" fontSize={compact ? 9 : 10.5}>{c}¢</text>
            </g>
          ))}
          {/* sparse time labels */}
          {xTicks.map((t, i) => (
            <text key={i} className="pc-mut" x={px(t)} y={H - (compact ? 5 : 8)}
                  fontSize={compact ? 9 : 10}
                  textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}>
              {fmtWhen(t, hours)}
            </text>
          ))}

          {/* entry price: dashed, labeled — polarity vs entry is the read */}
          {entryC != null && (
            <g>
              <line className="pc-entry" x1={PAD.l} x2={W - PAD.r}
                    y1={py(entryC)} y2={py(entryC)}
                    strokeWidth="1" strokeDasharray="4 3" />
              <text className="pc-mut" x={PAD.l + 4}
                    y={py(entryC) - 4} fontSize={compact ? 9 : 10.5}>
                entry {Math.round(entryC)}¢
              </text>
            </g>
          )}

          {/* the series */}
          <polyline className="pc-line" fill="none" strokeWidth="2"
                    strokeLinejoin="round" strokeLinecap="round"
                    points={pts.map((q) => `${px(q.t)},${py(q.p * 100)}`).join(' ')} />
          {/* direct end label: last price in ink, never series color */}
          {last && (
            <text className="pc-ink" fontSize={compact ? 10 : 11} fontWeight={700}
                  x={Math.min(px(last.t) + 5, W - 2)} y={py(last.p * 100) + 3.5}>
              {Math.round(last.p * 100)}¢
            </text>
          )}

          {/* crosshair + tooltip */}
          {hover !== null && pts[hover] && (() => {
            const hp = pts[hover]
            const hx = px(hp.t)
            const bw = 96
            const bh = 34
            const bx = Math.min(Math.max(hx + 8, PAD.l), W - PAD.r - bw)
            const by = PAD.t + 2
            return (
              <g pointerEvents="none">
                <line className="pc-cross" x1={hx} x2={hx}
                      y1={PAD.t} y2={H - PAD.b} strokeWidth="1" />
                <circle className="pc-dot" cx={hx} cy={py(hp.p * 100)}
                        r="4" strokeWidth="2" />
                <rect className="pc-tip" x={bx} y={by} width={bw} height={bh} rx="6" />
                <text className="pc-mut" x={bx + 8} y={by + 13} fontSize="9.5">
                  {fmtTip(hp.t, hours)}
                </text>
                <text className="pc-ink" x={bx + 8} y={by + 27}
                      fontSize="11" fontWeight={700}>
                  {(hp.p * 100).toFixed(1)}¢
                </text>
              </g>
            )
          })()}
        </svg>
      )}
    </figure>
  )
}

/* ── Sparkline — browse-card trend hint, ~120×36, stroke only ────── */
export function Sparkline({ points, w = 120, h = 36 }: {
  points: HistoryPoint[]; w?: number; h?: number
}) {
  const path = useMemo(() => {
    const n = points.length
    if (n < 2) return null
    let lo = Infinity
    let hi = -Infinity
    for (const q of points) { if (q.p < lo) lo = q.p; if (q.p > hi) hi = q.p }
    // Keep at least a 6¢ window so flat markets read flat, not jittery.
    const mid = (lo + hi) / 2
    const half = Math.max(0.03, (hi - lo) / 2)
    lo = mid - half; hi = mid + half
    const t0 = points[0].t
    const span = Math.max(1, points[n - 1].t - t0)
    return points.map((q) =>
      `${(2 + (w - 4) * ((q.t - t0) / span)).toFixed(1)},${
        (2 + (h - 4) * (1 - (q.p - lo) / (hi - lo))).toFixed(1)}`).join(' ')
  }, [points, w, h])
  if (!path) return null
  return (
    <svg className="pc-sparkline" viewBox={`0 0 ${w} ${h}`} width={w} height={h}
         aria-hidden="true" focusable="false">
      <polyline className="pc-line" points={path} fill="none"
                strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
