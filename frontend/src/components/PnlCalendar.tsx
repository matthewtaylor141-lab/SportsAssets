import { useMemo, useState } from 'react'
import { fmtSignedUsd, fmtUsd } from '../lib/format'

export interface DayStat {
  date: string // YYYY-MM-DD
  pnl: number
  /** Stake/volume for the day — omitted by feeds that don't track it. */
  volume?: number
  trades: number
  settled?: number
  open?: number
}

/** Monthly calendar of daily realized P&L — green/red intensity per day,
 * month navigation, per-day tooltip with P&L / volume / trade count. */
export function PnlCalendar({
  days,
  onSelect,
}: {
  days: DayStat[]
  onSelect?: (date: string) => void
}) {
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

  // A day whose money is still on the table is LIVE, not lost: red at $0
  // taught the owner the page was wrong. Red/green is reserved for days
  // where settled money says so; a live day gets a neutral "in play" cell.
  // "Live" = most of the day's money is still in play and realized P&L is
  // still a rounding error. A handful of tiny early settlements must not
  // flip the whole day red while 30+ positions are unresolved.
  const isLive = (d: DayStat) =>
    (d.open ?? 0) > 0 && Math.abs(d.pnl) < 1 && (d.open ?? 0) > (d.settled ?? 0)

  const cellStyle = (d: DayStat): React.CSSProperties => {
    if (isLive(d)) {
      return {
        background: 'rgba(148,163,184,0.14)',
        color: 'var(--ink-2)',
        border: '1px dashed rgba(148,163,184,0.45)',
      }
    }
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
              className={`cal-cell${onSelect ? ' clickable' : ''}`}
              style={cellStyle(c)}
              onMouseMove={(e) => setHover({ d: c, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(c.date)}
              role={onSelect ? 'button' : undefined}
              title={onSelect ? 'Click for the day report' : undefined}
            >
              <span className="cal-day">{day}</span>
              <span className="cal-pnl">
                {isLive(c) ? `${c.open} live` : fmtSignedUsd(c.pnl)}
              </span>
            </div>
          )
        })}
      </div>
      {hover && (
        <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <div>{hover.d.date}</div>
          <strong className={hover.d.pnl >= 0 ? 'pos' : 'neg'}>{fmtSignedUsd(hover.d.pnl)}</strong>{' '}
          realized · {hover.d.trades} trades
          {hover.d.volume !== undefined && <> · {fmtUsd(hover.d.volume)} volume</>}
          {(hover.d.open ?? 0) > 0 && (
            <div className="muted">{hover.d.open} still open — settles when games finish</div>
          )}
        </div>
      )}
    </div>
  )
}
