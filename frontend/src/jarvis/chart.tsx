/* MERIDIAN's on-screen charts — dependency-free SVG, built to the
 * dataviz method for this page's single committed dark look.
 *
 * Palette (validated with the six-checks script against surface
 * #05070b, dark band L 0.48–0.67):
 *   categorical: #1d9fc9 (slot 1, cyan) · #b98a2e (slot 2, gold) — all
 *     six checks pass; ≥2 series get a legend AND direct end labels.
 *   diverging P&L: #17a06b (gain) / #e03d36 (loss) — CVD ΔE 7.1 sits
 *     in the 6–8 floor band, legal because sign is double-encoded:
 *     bars run up/down from a drawn baseline and extremes carry direct
 *     +/− labels. Never used as categorical slots.
 * Marks: 2px lines, ≥8px markers on hover, 4px rounded data-ends with
 * a squared baseline end, 2px gaps between bars, recessive grid
 * (horizontal only, 8% ink), text in ink tokens never series color.
 * Hover: full-column hit targets drive a crosshair + tooltip. */

import { useMemo, useRef, useState } from 'react'

export interface ChartPoint { x: string; y: number }
export interface ChartSeries { name: string; values: ChartPoint[] }
export interface ChartSpec {
  kind: 'line' | 'bars'
  title: string
  unit?: string            // '$' prefixes values
  series: ChartSeries[]    // 1–2 series (slot order fixed)
}

const CAT = ['#1d9fc9', '#b98a2e']
const POS = '#17a06b'
const NEG = '#e03d36'
const INK = 'rgba(222, 238, 248, 0.88)'
const INK_MUTED = 'rgba(148, 168, 184, 0.85)'
const GRID = 'rgba(222, 238, 248, 0.08)'

const W = 640
const H = 320
const PAD = { l: 54, r: 16, t: 18, b: 34 }

function fmt(v: number, unit?: string): string {
  const a = Math.abs(v)
  const s = a >= 10000 ? `${(v / 1000).toFixed(1)}k`
    : a >= 100 ? v.toFixed(0)
    : v.toFixed(2)
  return unit === '$' ? (v < 0 ? `−$${s.replace('-', '')}` : `$${s}`) : s
}

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (lo === hi) { lo -= 1; hi += 1 }
  const span = hi - lo
  const step0 = span / n
  const mag = 10 ** Math.floor(Math.log10(step0))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag)
    .find((s) => span / s <= n + 0.5) || mag * 10
  const start = Math.ceil(lo / step) * step
  const out: number[] = []
  for (let v = start; v <= hi + 1e-9; v += step) out.push(v)
  return out
}

export function MeridianChart({ spec }: { spec: ChartSpec }) {
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  const model = useMemo(() => {
    const series = (spec.series || []).slice(0, 2)
      .map((s) => ({ ...s, values: (s.values || []).slice(0, 120) }))
    const n = Math.max(1, ...series.map((s) => s.values.length))
    const ys = series.flatMap((s) => s.values.map((p) => p.y))
    let lo = Math.min(0, ...ys)
    let hi = Math.max(0, ...ys)
    if (lo === hi) { lo -= 1; hi += 1 }
    const pad = (hi - lo) * 0.08
    hi += pad
    if (lo < 0) lo -= pad
    const px = (i: number) =>
      PAD.l + ((W - PAD.l - PAD.r) * (n === 1 ? 0.5 : i / (n - 1)))
    const py = (v: number) =>
      PAD.t + (H - PAD.t - PAD.b) * (1 - (v - lo) / (hi - lo))
    return { series, n, lo, hi, px, py }
  }, [spec])

  const { series, n, lo, hi, px, py } = model
  if (series.length === 0 || n === 0) return null
  const diverging = spec.kind === 'bars' && series.length === 1 && lo < 0
  const ticks = niceTicks(lo, hi)
  const zero = py(Math.max(lo, Math.min(hi, 0)))
  const labels = series[0].values.map((p) => p.x)
  const xTickEvery = Math.max(1, Math.ceil(n / 6))

  const onMove = (e: React.PointerEvent) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const fx = ((e.clientX - rect.left) / rect.width) * W
    const i = Math.round(((fx - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1))
    setHover(Math.max(0, Math.min(n - 1, i)))
  }

  const barW = Math.max(3, Math.min(26,
    (W - PAD.l - PAD.r) / Math.max(1, n) - 2))   // 2px surface gap

  return (
    <figure className="jv-chart">
      <figcaption>{spec.title}</figcaption>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={spec.title}
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {/* recessive horizontal grid + value axis */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={py(t)} y2={py(t)}
                  stroke={GRID} strokeWidth="1" />
            <text x={PAD.l - 8} y={py(t) + 3.5} textAnchor="end"
                  fontSize="10.5" fill={INK_MUTED}>{fmt(t, spec.unit)}</text>
          </g>
        ))}
        {/* baseline when zero is in range */}
        {lo < 0 && hi > 0 && (
          <line x1={PAD.l} x2={W - PAD.r} y1={zero} y2={zero}
                stroke={INK_MUTED} strokeWidth="1" />
        )}
        {/* x labels, sparse */}
        {labels.map((l, i) => (i % xTickEvery === 0 || i === n - 1) && (
          <text key={i} x={px(i)} y={H - 10} textAnchor="middle"
                fontSize="10" fill={INK_MUTED}>{l}</text>
        ))}

        {spec.kind === 'bars' && series.length === 1 &&
          series[0].values.map((p, i) => {
            const up = p.y >= 0
            const y0 = zero
            const y1 = py(p.y)
            const h = Math.max(1.5, Math.abs(y1 - y0))
            const r = Math.min(4, barW / 2, h)     // rounded DATA end,
            const x = px(i) - barW / 2             // squared baseline end
            const top = up ? y1 : y0
            const d = up
              ? `M ${x} ${top + h} V ${top + r} Q ${x} ${top} ${x + r} ${top} H ${x + barW - r} Q ${x + barW} ${top} ${x + barW} ${top + r} V ${top + h} Z`
              : `M ${x} ${top} V ${top + h - r} Q ${x} ${top + h} ${x + r} ${top + h} H ${x + barW - r} Q ${x + barW} ${top + h} ${x + barW} ${top + h - r} V ${top} Z`
            return <path key={i} d={d}
                         fill={diverging ? (up ? POS : NEG) : CAT[0]}
                         opacity={hover === null || hover === i ? 1 : 0.45} />
          })}

        {spec.kind === 'line' && series.map((s, si) => {
          const pts = s.values.map((p, i) => `${px(i)},${py(p.y)}`).join(' ')
          return (
            <g key={s.name}>
              <polyline points={pts} fill="none" stroke={CAT[si]}
                        strokeWidth="2" strokeLinejoin="round"
                        strokeLinecap="round" />
              {/* direct end label (identity never color-alone) */}
              {s.values.length > 0 && (
                <text x={Math.min(px(s.values.length - 1) + 6, W - 4)}
                      y={py(s.values[s.values.length - 1].y) + 3.5}
                      fontSize="10.5" fill={INK}>
                  {series.length > 1 ? s.name
                    : fmt(s.values[s.values.length - 1].y, spec.unit)}
                </text>
              )}
            </g>
          )
        })}

        {/* selective direct labels on the diverging extremes */}
        {diverging && (() => {
          const vs = series[0].values
          const iMax = vs.reduce((b, p, i) => (p.y > vs[b].y ? i : b), 0)
          const iMin = vs.reduce((b, p, i) => (p.y < vs[b].y ? i : b), 0)
          return [iMax, iMin].filter((i, k, a) => a.indexOf(i) === k)
            .map((i) => (
              <text key={i} x={px(i)}
                    y={py(vs[i].y) + (vs[i].y >= 0 ? -6 : 14)}
                    textAnchor="middle" fontSize="10.5" fill={INK}>
                {fmt(vs[i].y, spec.unit)}
              </text>
            ))
        })()}

        {/* hover: crosshair + tooltip */}
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={px(hover)} x2={px(hover)} y1={PAD.t} y2={H - PAD.b}
                  stroke="rgba(222,238,248,0.25)" strokeWidth="1" />
            {spec.kind === 'line' && series.map((s, si) =>
              s.values[hover] && (
                <circle key={si} cx={px(hover)} cy={py(s.values[hover].y)}
                        r="4.5" fill={CAT[si]}
                        stroke="#05070b" strokeWidth="2" />
              ))}
            {(() => {
              const rows = series
                .filter((s) => s.values[hover])
                .map((s) => ({ name: s.name, v: s.values[hover].y }))
              if (rows.length === 0) return null
              const bw = 118
              const bh = 20 + rows.length * 15
              const bx = Math.min(Math.max(px(hover) + 10, PAD.l),
                                  W - PAD.r - bw)
              const by = PAD.t + 4
              return (
                <g>
                  <rect x={bx} y={by} width={bw} height={bh} rx="7"
                        fill="rgba(10,16,24,0.95)"
                        stroke="rgba(105,224,255,0.25)" />
                  <text x={bx + 9} y={by + 14} fontSize="10"
                        fill={INK_MUTED}>{labels[hover]}</text>
                  {rows.map((r, k) => (
                    <text key={k} x={bx + 9} y={by + 29 + k * 15}
                          fontSize="11" fill={INK}>
                      {series.length > 1 ? `${r.name}  ` : ''}{fmt(r.v, spec.unit)}
                    </text>
                  ))}
                </g>
              )
            })()}
          </g>
        )}
      </svg>
      {series.length > 1 && (
        <div className="jv-chart-legend">
          {series.map((s, si) => (
            <span key={s.name}>
              <i style={{ background: CAT[si] }} />{s.name}
            </span>
          ))}
        </div>
      )}
    </figure>
  )
}

export function parseChartSpec(raw: unknown): ChartSpec | null {
  try {
    const o = (typeof raw === 'string' ? JSON.parse(raw) : raw) as ChartSpec
    if (!o || (o.kind !== 'line' && o.kind !== 'bars')) return null
    if (!Array.isArray(o.series) || o.series.length === 0) return null
    for (const s of o.series) {
      if (!Array.isArray(s.values)) return null
      s.values = s.values
        .filter((p) => p && typeof p.y === 'number' && Number.isFinite(p.y))
        .map((p) => ({ x: String(p.x), y: p.y }))
    }
    return o
  } catch {
    return null
  }
}
