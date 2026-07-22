import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { api } from '../lib/api'
import { fmtSignedUsd, fmtUsd } from '../lib/format'
import type { EventView } from '../lib/types'

/** Per-event view of all tracked whales' net positions. Disagreement is signal. */
export function Markets() {
  const [events, setEvents] = useState<EventView[] | null>(null)

  useEffect(() => {
    api<EventView[]>('/api/events').then(setEvents).catch(() => setEvents([]))
  }, [])

  return (
    <>
      <h1>Markets</h1>
      <p className="sub">
        Where the whales are positioned, event by event — same side, or against each other.
      </p>
      {events === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : events.length === 0 ? (
        <EmptyState>No positions yet. This view builds as tracked whales take positions.</EmptyState>
      ) : (
        events.map((ev) => (
          <div className="card" key={ev.event_slug}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 15 }}>{ev.event_title || ev.event_slug}</strong>
              {ev.sport && <span className="chip">{ev.sport}</span>}
              {ev.disagreement && (
                <span className="chip" style={{ color: 'var(--warning)', borderColor: 'var(--warning)' }}>
                  ⚔ whales disagree
                </span>
              )}
              <span style={{ flex: 1 }} />
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                total exposure {fmtUsd(ev.total_exposure)}
              </span>
            </div>
            <div className="scroll-x">
              <table className="data" style={{ marginTop: 8 }}>
                <thead>
                  <tr>
                    <th>Whale</th>
                    <th>Outcome</th>
                    <th className="num">Net shares</th>
                    <th className="num">Exposure</th>
                    <th className="num">Realized P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {ev.positions
                    .sort((a, b) => b.exposure - a.exposure)
                    .map((p, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 600 }}>{p.username || `whale#${p.whale_id}`}</td>
                        <td>{p.outcome || '—'}</td>
                        <td className="num">
                          {p.net_shares.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </td>
                        <td className="num">{fmtUsd(p.exposure)}</td>
                        <td className={`num ${p.realized_pnl >= 0 ? 'pos' : 'neg'}`}>
                          {fmtSignedUsd(p.realized_pnl)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </>
  )
}
