import { useMemo, useRef, useState } from 'react'
import { fmtSignedUsd } from '../lib/format'

interface Point {
  ts: string
  cumulative_pnl: number
}

/** Cumulative realized P&L line. Single series (title names it — no legend),
 * 2px line, crosshair + tooltip hover layer, recessive grid. */
export function EquityCurve({ points, height = 220 }: { points: Point[]; height?: number }) {
  const ref = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState<{ i: number; cx: number; cy: number } | null>(null)
  const W = 720
  const H = height
  const pad = { l: 8, r: 8, t: 12, b: 22 }

  const model = useMemo(() => {
    if (points.length < 2) return null
    const xs = points.map((p) => new Date(p.ts).getTime())
    const ys = points.map((p) => p.cumulative_pnl)
    const x0 = Math.min(...xs)
    const x1 = Math.max(...xs)
    const yMin = Math.min(0, ...ys)
    const yMax = Math.max(0, ...ys)
    const X = (t: number) => pad.l + ((t - x0) / Math.max(1, x1 - x0)) * (W - pad.l - pad.r)
    const Y = (v: number) =>
      pad.t + (1 - (v - yMin) / Math.max(1e-9, yMax - yMin)) * (H - pad.t - pad.b)
    const coords = points.map((p, i) => ({ x: X(xs[i]), y: Y(ys[i]), p }))
    return { coords, zeroY: Y(0), x0, x1 }
  }, [points, H])

  if (!model) return null
  const path = model.coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')

  const onMove = (e: React.MouseEvent) => {
    const rect = ref.current!.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    let best = 0
    let bestDist = Infinity
    model.coords.forEach((c, i) => {
      const d = Math.abs(c.x - px)
      if (d < bestDist) {
        bestDist = d
        best = i
      }
    })
    setHover({ i: best, cx: e.clientX, cy: e.clientY })
  }

  const h = hover ? model.coords[hover.i] : null
  return (
    <div style={{ position: 'relative' }}>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="Cumulative realized profit and loss over time"
      >
        <line x1={pad.l} x2={W - pad.r} y1={model.zeroY} y2={model.zeroY} stroke="var(--baseline)" strokeWidth={1} />
        <path d={path} fill="none" stroke="var(--s1)" strokeWidth={2} strokeLinejoin="round" />
        {h && (
          <>
            <line x1={h.x} x2={h.x} y1={pad.t} y2={H - pad.b} stroke="var(--grid)" strokeWidth={1} />
            <circle cx={h.x} cy={h.y} r={4} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
          </>
        )}
        <text x={pad.l} y={H - 6} fill="var(--muted)" fontSize={11}>
          {new Date(model.x0).toLocaleDateString()}
        </text>
        <text x={W - pad.r} y={H - 6} fill="var(--muted)" fontSize={11} textAnchor="end">
          {new Date(model.x1).toLocaleDateString()}
        </text>
      </svg>
      {h && hover && (
        <div className="tooltip" style={{ left: hover.cx + 12, top: hover.cy + 12 }}>
          <div>{new Date(h.p.ts).toLocaleString()}</div>
          <strong className={h.p.cumulative_pnl >= 0 ? 'pos' : 'neg'}>
            {fmtSignedUsd(h.p.cumulative_pnl)}
          </strong>{' '}
          realized
        </div>
      )}
    </div>
  )
}
