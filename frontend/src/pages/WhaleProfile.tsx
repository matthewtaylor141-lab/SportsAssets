import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { EquityCurve } from '../components/EquityCurve'
import { SportDonut } from '../components/SportDonut'
import { TradeRow } from '../components/TradeRow'
import { api } from '../lib/api'
import { fmtPct, fmtSignedUsd, fmtUsd, shortAddr } from '../lib/format'
import type { WhaleProfile as Profile } from '../lib/types'

const WINDOWS = ['7d', '30d', 'all'] as const

export function WhaleProfile() {
  const { id } = useParams()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [win, setWin] = useState<(typeof WINDOWS)[number]>('all')
  const [error, setError] = useState(false)

  useEffect(() => {
    setProfile(null)
    api<Profile>(`/api/whales/${id}`).then(setProfile).catch(() => setError(true))
  }, [id])

  if (error) return <EmptyState>Whale not found.</EmptyState>
  if (!profile) return <EmptyState>Loading profile…</EmptyState>

  const { whale, stats, open_positions, recent_trades, equity_curve, sport_mix } = profile
  const rows = stats.filter((s) => s.window === win)
  const allTime = stats.filter((s) => s.window === 'all')
  const totalRealized = allTime.reduce((a, s) => a + s.realized_pnl, 0)
  const totalExposure = allTime.reduce((a, s) => a + s.open_exposure, 0)

  return (
    <>
      <h1>
        {whale.username || 'anonymous'}{' '}
        <span className="mono" style={{ fontWeight: 400, color: 'var(--muted)' }}>
          {shortAddr(whale.address)}
        </span>
      </h1>
      <p className="sub">
        Leaderboard rank #{whale.source_rank ?? '—'} · tracked since{' '}
        {new Date(whale.added_at).toLocaleDateString()}
        {!whale.active && ' · no longer tracked (history retained)'}
      </p>

      <div className="statgrid" style={{ marginBottom: 12 }}>
        <div className="stat">
          <div className="label">Realized P&L (tracked)</div>
          <div className={`value ${totalRealized >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(totalRealized)}</div>
        </div>
        <div className="stat">
          <div className="label">Open exposure</div>
          <div className="value">{fmtUsd(totalExposure)}</div>
        </div>
        <div className="stat">
          <div className="label">Leaderboard all-time</div>
          <div className="value">{fmtUsd(whale.sports_profit_alltime)}</div>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Cumulative realized P&L</h2>
          {equity_curve.length >= 2 ? (
            <EquityCurve points={equity_curve} />
          ) : (
            <EmptyState>No realized P&L yet — curve appears after the first settled trade.</EmptyState>
          )}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Sport mix (notional deployed)</h2>
          {sport_mix.length > 0 ? (
            <SportDonut mix={sport_mix} />
          ) : (
            <EmptyState>No trades recorded yet.</EmptyState>
          )}
        </div>
      </div>

      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <h2 style={{ margin: 0, flex: 1 }}>Per-sport performance</h2>
          {WINDOWS.map((w) => (
            <button
              key={w}
              className="btn"
              style={win === w ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
              onClick={() => setWin(w)}
            >
              {w === 'all' ? 'All-time' : w}
            </button>
          ))}
        </div>
        {rows.length === 0 ? (
          <EmptyState>No settled activity in this window.</EmptyState>
        ) : (
          <div className="scroll-x">
            <table className="data" style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>Sport</th>
                  <th className="num">Markets</th>
                  <th className="num">W-L</th>
                  <th className="num">Win %</th>
                  <th className="num">Realized P&L</th>
                  <th className="num">Notional</th>
                  <th className="num">ROI</th>
                  <th className="num">Open exposure</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.sport}>
                    <td>{s.sport}</td>
                    <td className="num">{s.markets_traded}</td>
                    <td className="num">
                      {s.wins}-{s.losses}
                      {s.scratches ? `-${s.scratches}` : ''}
                    </td>
                    <td className="num">{fmtPct(s.win_pct)}</td>
                    <td className={`num ${s.realized_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(s.realized_pnl)}</td>
                    <td className="num">{fmtUsd(s.notional)}</td>
                    <td className={`num ${(s.roi ?? 0) >= 0 ? 'pos' : 'neg'}`}>{fmtPct(s.roi)}</td>
                    <td className="num">{fmtUsd(s.open_exposure)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Open positions</h2>
        {open_positions.length === 0 ? (
          <EmptyState>No open positions.</EmptyState>
        ) : (
          <div className="scroll-x">
            <table className="data">
              <thead>
                <tr>
                  <th>Market</th>
                  <th>Outcome</th>
                  <th className="num">Shares</th>
                  <th className="num">Avg cost</th>
                  <th className="num">Exposure</th>
                </tr>
              </thead>
              <tbody>
                {open_positions.map((p) => (
                  <tr key={p.token_id}>
                    <td>{p.event_title || p.market_title || p.condition_id}</td>
                    <td>{p.outcome || '—'}</td>
                    <td className="num">{p.net_shares.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                    <td className="num">{Math.round(p.avg_cost * 100)}¢</td>
                    <td className="num">{fmtUsd(p.exposure)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 0 }}>
        <h2 style={{ margin: '12px 12px 4px' }}>Recent trades</h2>
        {recent_trades.length === 0 ? (
          <EmptyState>No trades yet.</EmptyState>
        ) : (
          recent_trades.map((t) => <TradeRow key={t.id} trade={t} />)
        )}
      </div>
    </>
  )
}
