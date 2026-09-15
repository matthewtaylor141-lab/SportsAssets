import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fmtPct, fmtSignedUsd } from '../lib/format'
import type { MatrixCell } from '../lib/types'

/** Diverging color for ROI: blue (positive) ↔ neutral gray midpoint ↔ red
 * (negative), per validated diverging pair. Value never rides on color alone —
 * the ROI figure and W-L record are printed in every cell. */
function cellColor(roi: number | null): { bg: string; ink: string } {
  if (roi === null) return { bg: 'var(--surface-2)', ink: 'var(--muted)' }
  const t = Math.max(-1, Math.min(1, roi / 0.5)) // ±50% ROI saturates the scale
  const neutral: [number, number, number] = [56, 56, 53] // #383835
  const blue: [number, number, number] = [57, 135, 229] // #3987e5
  const red: [number, number, number] = [230, 103, 103] // #e66767
  const pole = t >= 0 ? blue : red
  const k = Math.abs(t)
  const mix = neutral.map((n, i) => Math.round(n + (pole[i] - n) * k))
  return { bg: `rgb(${mix.join(',')})`, ink: k > 0.55 ? '#ffffff' : 'var(--ink-2)' }
}

export function HeatMatrix({ cells, sports }: { cells: MatrixCell[]; sports: string[] }) {
  const nav = useNavigate()
  const [hover, setHover] = useState<{ cell: MatrixCell; x: number; y: number } | null>(null)

  const whales = [...new Map(cells.map((c) => [c.whale_id, c])).values()]
  const byKey = new Map(cells.map((c) => [`${c.whale_id}|${c.sport}`, c]))

  return (
    <div className="scroll-x">
      <table className="data" style={{ borderCollapse: 'separate', borderSpacing: 0 }}>
        <thead>
          <tr>
            <th>Whale</th>
            {sports.map((s) => (
              <th key={s} className="num">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {whales.map((w) => (
            <tr key={w.whale_id}>
              <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                {w.username || `whale#${w.whale_id}`}
              </td>
              {sports.map((s) => {
                const c = byKey.get(`${w.whale_id}|${s}`)
                if (!c)
                  return (
                    <td key={s} className="num" style={{ color: 'var(--muted)' }}>
                      —
                    </td>
                  )
                const { bg, ink } = cellColor(c.roi)
                return (
                  <td key={s} style={{ padding: 2 }}>
                    <div
                      className="matrix-cell"
                      style={{ background: bg, color: ink }}
                      onClick={() => nav(`/?whale=${c.whale_id}&sport=${encodeURIComponent(s)}`)}
                      onMouseMove={(e) => setHover({ cell: c, x: e.clientX, y: e.clientY })}
                      onMouseLeave={() => setHover(null)}
                      title=""
                    >
                      <div className="roi">{fmtPct(c.roi)}</div>
                      <div className="wl">
                        {c.wins}-{c.losses}
                      </div>
                    </div>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="legend">
        <span>
          <span className="swatch" style={{ background: '#e66767' }} /> negative ROI
        </span>
        <span>
          <span className="swatch" style={{ background: '#383835' }} /> flat
        </span>
        <span>
          <span className="swatch" style={{ background: '#3987e5' }} /> positive ROI
        </span>
        <span style={{ color: 'var(--muted)' }}>cell shows ROI and W-L · click → filtered feed</span>
      </div>
      {hover && (
        <div className="tooltip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <div>
            <strong>{hover.cell.username || `whale#${hover.cell.whale_id}`}</strong> · {hover.cell.sport}
          </div>
          <div>
            ROI {fmtPct(hover.cell.roi)} · {hover.cell.wins}W-{hover.cell.losses}L
            {hover.cell.scratches ? `-${hover.cell.scratches}S` : ''}
          </div>
          <div>
            Realized {fmtSignedUsd(hover.cell.realized_pnl)} on {fmtSignedUsd(hover.cell.notional).replace('+', '')}{' '}
            · {hover.cell.markets_traded} markets
          </div>
        </div>
      )}
    </div>
  )
}
