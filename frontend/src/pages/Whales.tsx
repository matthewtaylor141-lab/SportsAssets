import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { api } from '../lib/api'
import { fmtUsd, shortAddr } from '../lib/format'
import type { Whale } from '../lib/types'

export function Whales() {
  const [whales, setWhales] = useState<Whale[] | null>(null)

  useEffect(() => {
    api<Whale[]>('/api/whales').then(setWhales).catch(() => setWhales([]))
  }, [])

  return (
    <>
      <h1>Whale Profiles</h1>
      <p className="sub">The tracked top sports traders, selected from the live leaderboard.</p>
      {whales === null ? (
        <EmptyState>Loading…</EmptyState>
      ) : whales.length === 0 ? (
        <EmptyState>
          Roster is empty. Run the roster seed (<code>make seed-roster</code>) against the live
          leaderboard to select the top sports wallets.
        </EmptyState>
      ) : (
        <div className="card scroll-x" style={{ padding: 0 }}>
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
                <th>Trader</th>
                <th>Wallet</th>
                <th className="num">All-time sports profit (leaderboard)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {whales.map((w) => (
                <tr key={w.id}>
                  <td>{w.source_rank ?? '—'}</td>
                  <td>
                    <Link to={`/whales/${w.id}`} style={{ fontWeight: 600, color: 'var(--ink)' }}>
                      {w.username || 'anonymous'}
                    </Link>{' '}
                    {w.pinned && <span className="chip">pinned</span>}
                  </td>
                  <td className="mono">{shortAddr(w.address)}</td>
                  <td className="num">{fmtUsd(w.sports_profit_alltime)}</td>
                  <td>
                    <Link to={`/whales/${w.id}`}>profile →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
