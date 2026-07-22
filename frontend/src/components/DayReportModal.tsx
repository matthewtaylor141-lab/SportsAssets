import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { fmtSignedUsd, fmtUsd } from '../lib/format'

interface DayBet {
  settled_at: string
  sport: string
  label: string
  odds: string
  stake: number
  result: string
  pnl: number
}

interface DayReport {
  date: string
  pnl: number
  stake: number
  wins: number
  losses: number
  settled_count: number
  trades_placed: number
  volume_placed: number
  sports: { sport: string; pnl: number; stake: number; wins: number; losses: number; bets: DayBet[] }[]
}

/** Calendar day drill-down: every bet settled that day, sportsbook-labeled,
 * grouped by sport, ranked most → least profitable. */
export function DayReportModal({
  whaleId,
  day,
  onClose,
}: {
  whaleId: number
  day: string
  onClose: () => void
}) {
  const [report, setReport] = useState<DayReport | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    setReport(null)
    api<DayReport>(`/api/whales/${whaleId}/day/${day}`).then(setReport).catch(() => setError(true))
  }, [whaleId, day])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <strong style={{ fontSize: 16 }}>
              {new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, {
                weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC',
              })}
            </strong>
            {report && (
              <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                {report.settled_count} settled · {report.wins}-{report.losses} ·{' '}
                {fmtUsd(report.stake)} staked · {report.trades_placed.toLocaleString()} trades placed (
                {fmtUsd(report.volume_placed)})
              </div>
            )}
          </div>
          <span style={{ flex: 1 }} />
          {report && (
            <span className={`modal-pnl ${report.pnl >= 0 ? 'pos' : 'neg'}`}>
              {fmtSignedUsd(report.pnl)}
            </span>
          )}
          <button className="btn" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          {error ? (
            <p style={{ color: 'var(--critical)' }}>Failed to load the day report.</p>
          ) : !report ? (
            <p style={{ color: 'var(--muted)' }}>Loading…</p>
          ) : report.sports.length === 0 ? (
            <p style={{ color: 'var(--muted)' }}>
              No bets settled this day.
              {report.trades_placed > 0 &&
                ` ${report.trades_placed.toLocaleString()} trades were placed — they settle when their markets resolve.`}
            </p>
          ) : (
            report.sports.map((s) => (
              <div key={s.sport} style={{ marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '10px 0 6px' }}>
                  <strong>{s.sport}</strong>
                  <span className={s.pnl >= 0 ? 'pos' : 'neg'} style={{ fontWeight: 700 }}>
                    {fmtSignedUsd(s.pnl)}
                  </span>
                  <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                    {s.wins}-{s.losses} · {fmtUsd(s.stake)} staked
                  </span>
                </div>
                <div className="scroll-x">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Bet</th>
                        <th className="num">Odds</th>
                        <th className="num">Stake</th>
                        <th>Result</th>
                        <th className="num">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.bets.map((b, i) => (
                        <tr key={i}>
                          <td>{b.label}</td>
                          <td className="num">{b.odds}</td>
                          <td className="num">{fmtUsd(b.stake)}</td>
                          <td>{b.result}</td>
                          <td className={`num ${b.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(b.pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
