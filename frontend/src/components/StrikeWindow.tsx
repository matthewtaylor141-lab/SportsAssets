import { useEffect, useState } from 'react'
import { subscribe } from '../lib/sse'

type Dot = { key: number; x: number; hot: boolean; color: string }

/** The latency story as one instrument: log-scale axis 0.5s → 5m,
 * cyan p50/p95 notches, and each live detection dropping in as a dot
 * that fades over 60s. The frame, axis, and notches never animate —
 * the decaying dot field is what makes it alive. */
export function StrikeWindow({ p50, p95, w = 640, h = 56, mini = false }: {
  p50?: number | null
  p95?: number | null
  w?: number
  h?: number
  mini?: boolean
}) {
  const pad = 14
  const x = (t: number) =>
    pad + (Math.log10(Math.max(0.5, Math.min(300, t)) / 0.5) / Math.log10(600)) * (w - 2 * pad)
  const [dots, setDots] = useState<Dot[]>([])

  useEffect(() => subscribe((t, fresh) => {
    if (!fresh) return
    const lag = (t as { detect_lag_s?: number }).detect_lag_s
    const lat = typeof lag === 'number' && lag > 0 ? lag : null
    if (lat == null) return
    const hot = p95 != null && lat > p95
    setDots((d) => [...d.slice(-60), {
      key: Date.now() + Math.random(), x: x(lat), hot, color: 'var(--amber)',
    }])
  }), [p95])

  const ticks = mini ? [1, 10, 60, 300] : [0.5, 1, 2, 5, 10, 30, 60, 120, 300]
  const lbl = (t: number) => (t < 60 ? `${t}s` : `${t / 60}m`)
  const base = h - (mini ? 10 : 14)
  return (
    <svg className="nd-strike" width={w} height={h} role="img"
      aria-label={`copy latency, p50 ${p50 ?? '?'}s p95 ${p95 ?? '?'}s`}>
      <line x1={pad} y1={base} x2={w - pad} y2={base} stroke="var(--line-strong)" />
      {ticks.map((t) => (
        <g key={t}>
          <line className="grid" x1={x(t)} y1={6} x2={x(t)} y2={base} />
          {!mini && <text x={x(t)} y={h - 2} textAnchor="middle">{lbl(t)}</text>}
        </g>
      ))}
      {p50 != null && (
        <g>
          <line className="notch" x1={x(p50)} y1={4} x2={x(p50)} y2={base} strokeWidth="2" />
          {!mini && <text className="notch-lb" x={x(p50)} y={10} dx={4}>p50</text>}
        </g>
      )}
      {p95 != null && (
        <g>
          <line className="notch" x1={x(p95)} y1={4} x2={x(p95)} y2={base} strokeWidth="2" />
          {!mini && <text className="notch-lb" x={x(p95)} y={10} dx={4}>p95</text>}
        </g>
      )}
      {dots.map((d) => (
        <circle key={d.key} cx={d.x} cy={base - 8} r={3}
          fill={d.color}
          stroke={d.hot ? 'var(--neg)' : 'none'}
          onAnimationEnd={() => setDots((ds) => ds.filter((z) => z.key !== d.key))}
        />
      ))}
    </svg>
  )
}
