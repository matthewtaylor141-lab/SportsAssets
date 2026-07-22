import { useMemo, useState } from 'react'
import { fmtSignedUsd, fmtUsd } from '../lib/format'

export interface DayStat {
  date: string // YYYY-MM-DD
  pnl: number
  volume: number
  trades: number
}

/** Monthly calendar of daily realized P&L — green/red intensity per day,
 * month navigation, per-day tooltip with P&L / volume / trade count. */
export function PnlCalendar({ days }: { days: DayStat[] }) {
  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days])
  const latest = days.length ? days[days.length - 1].date : new Date().toISOString().slice(0, 10)
  const [cursor, setCursor] = useState(() => latest.slice(0, 7)) // YYYY-MM
  const [hover, setHover] = useState<{ d: DayStat; x: number; y: number } | null>(null)

  const [year, month] = cursor.split('-').map(Number)
  const first = new Date(Date.UTC(year, month - 1, 1))
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const lead = first.getUTCDay() // 0=Sun
  const maxAbs = useMemo(() => {
    const vals = days.filter((d) => d.date.startsWith(cursor)).map((d) => Math.abs(d.pnl))
    return Math.max(1, ...vals)
  }, [days, cursor])

  const monthTotal = days
    .filter((d) => d.date.startsWith(cursor))
    .reduce((a, d) => a + d.pnl, 0)

  const shift = (delta: number) => {
    const d = new Date(Date.UTC(year, month - 1 + delta, 1))
    setCursor(d.toISOString().slice(0, 7))
  }

  const cells: (DayStat | null | number)[] = [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => {
      const key = `${cursor}-${String(i + 1).padStart(2, '0')}`
      return byDate.get(key) ?? i + 1
    }),
  ]

  const cellStyle = (d: DayStat): React.CSSProperties => {
    const k = Math.min(1, Math.abs(d.pnl) / maxAbs)
    const [r, g, b] = d.pnl >= 0 ? [57, 135, 229] : [230, 103, 103]
    return {
      background: `rgba(${r},${g},${b},${0.12 + 0.55 * k})`,
      color: k > 0.5 ? '#fff' : 'var(--ink-2)',
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <button className="btn" onClick={() => shift(-1)} aria-label="Previous month">←</button>
        <strong style={{ minWidth: 130, textAlign: 'center' }}>
          {first.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })}
        </strong>
        <button className="btn" onClick={() => shift(1)} aria-label="Next month">→</button>
        <span style={{ flex: 1 }} />
        <span className={monthTotal >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 700 }}>
          {fmtSignedUsd(monthTotal)}
        </span>
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>month realized</span>
      </div>
      <div className="cal-grid">
        {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
          <div key={i} className="cal-head">{d}</div>
        ))}
        {cells.map((c, i) => {
          if (c === null) return <div key={i} />
          if (typeof c === 'number') return <div key={i} className="cal-cell quiet">{c}</div>
          const day = Number(c.date.slice(-2))
          return (
            <div
              key={i}
              className="cal-cell"
              style={cellStyle(c)}
              onMouseMove={(e) => setHover({ d: c, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHover(null)}
            >
              <span className="cal-day">{day}</span>
              <span className="cal-pnl">{fmtSignedUsd(c.pnl)}</span>
            </div>
          )
        })}
      </div>
      {hover && (
        <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <div>{hover.d.date}</div>
          <strong className={hover.d.pnl >= 0 ? 'pos' : 'neg'}>{fmtSignedUsd(hover.d.pnl)}</strong>{' '}
          realized · {hover.d.trades} trades · {fmtUsd(hover.d.volume)} volume
        </div>
      )}
    </div>
  )
}
