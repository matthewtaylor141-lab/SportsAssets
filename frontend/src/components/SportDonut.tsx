import { useState } from 'react'
import { fmtUsd, fmtPct } from '../lib/format'

const SLOTS = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)', 'var(--s7)', 'var(--s8)']

/** Sport mix by notional deployed. Fixed slot order (assigned by sport rank at
 * first render, never re-cycled on filter), 2px surface gaps between segments,
 * legend always present, per-segment hover tooltip. >7 sports fold to Other. */
export function SportDonut({ mix }: { mix: { sport: string; notional: number }[] }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null)
  const shown = mix.slice(0, 7)
  const rest = mix.slice(7)
  const data = [...shown]
  if (rest.length) {
    data.push({ sport: 'Other', notional: rest.reduce((a, m) => a + m.notional, 0) })
  }
  const total = data.reduce((a, m) => a + m.notional, 0)
  if (total <= 0) return null

  const R = 70
  const r = 44
  const C = 80
  let angle = -Math.PI / 2
  const segs = data.map((d, i) => {
    const frac = d.notional / total
    const a0 = angle
    const a1 = angle + frac * Math.PI * 2
    angle = a1
    const large = a1 - a0 > Math.PI ? 1 : 0
    const p = (a: number, rad: number) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`
    return {
      d,
      i,
      frac,
      path: `M${p(a0, R)} A${R},${R} 0 ${large} 1 ${p(a1, R)} L${p(a1, r)} A${r},${r} 0 ${large} 0 ${p(a0, r)} Z`,
    }
  })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <svg viewBox="0 0 160 160" style={{ width: 160, flexShrink: 0 }} role="img" aria-label="Notional deployed by sport">
          {segs.map((s) => (
            <path
              key={s.d.sport}
              d={s.path}
              fill={SLOTS[s.i % SLOTS.length]}
              stroke="var(--surface)"
              strokeWidth={2}
              onMouseMove={(e) => setHover({ i: s.i, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHover(null)}
            />
          ))}
          <text x={C} y={C - 4} textAnchor="middle" fill="var(--ink)" fontSize={15} fontWeight={700}>
            {fmtUsd(total)}
          </text>
          <text x={C} y={C + 13} textAnchor="middle" fill="var(--muted)" fontSize={10}>
            deployed
          </text>
        </svg>
        <div className="legend" style={{ flexDirection: 'column', gap: 4 }}>
          {segs.map((s) => (
            <span key={s.d.sport}>
              <span className="swatch" style={{ background: SLOTS[s.i % SLOTS.length] }} />
              {s.d.sport} <span style={{ color: 'var(--muted)' }}>{fmtPct(s.frac, 0)}</span>
            </span>
          ))}
        </div>
      </div>
      {hover && (
        <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          {segs[hover.i].d.sport}: <strong>{fmtUsd(segs[hover.i].d.notional)}</strong> (
          {fmtPct(segs[hover.i].frac)})
        </div>
      )}
    </div>
  )
}
