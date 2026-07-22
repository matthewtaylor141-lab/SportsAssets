import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { EquityCurve } from '../components/EquityCurve'
import { PnlCalendar } from '../components/PnlCalendar'
import { SportDonut } from '../components/SportDonut'
import { TradeRow } from '../components/TradeRow'
import { API_BASE, api } from '../lib/api'
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

  const { whale, stats, summary, open_positions, recent_trades, equity_curve, daily, sport_mix } =
    profile
  const rows = stats.filter((s) => s.window === win)
  const openValue = open_positions.reduce((a, p) => a + (p.exposure || 0), 0)
  const reportUrl = (period: string) => `${API_BASE}/api/whales/${whale.id}/report.pdf?period=${period}`

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ marginBottom: 0 }}>
          {whale.username || 'anonymous'}{' '}
          <span className="mono" style={{ fontWeight: 400, color: 'var(--muted)' }}>
            {shortAddr(whale.address)}
          </span>
        </h1>
        <span style={{ flex: 1 }} />
        <a className="btn" href={reportUrl('weekly')}>
          ⤓ Weekly PDF
        </a>
        <a className="btn" href={reportUrl('monthly')}>
          ⤓ Monthly PDF
        </a>
        <a className="btn primary" href={`${API_BASE}/api/whales/${whale.id}/settled-report.pdf`}>
          ⤓ Full settled history
        </a>
      </div>
      <p className="sub">
        Leaderboard rank #{whale.source_rank ?? '—'} · tracked since{' '}
        {new Date(whale.added_at).toLocaleDateString()}
        {!whale.active && ' · no longer tracked (history retained)'}
      </p>

      <div className="statgrid" style={{ marginBottom: 12 }}>
        <div className="stat">
          <div className="label">Realized P&L</div>
          <div className={`value ${summary.realized_pnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtSignedUsd(summary.realized_pnl)}
          </div>
        </div>
        <div className="stat">
          <div className="label">% earned</div>
          <div className={`value ${(summary.pct_earned ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {fmtPct(summary.pct_earned)}
          </div>
        </div>
        <div className="stat">
          <div className="label">Volume traded</div>
          <div className="value">{fmtUsd(summary.volume_traded)}</div>
        </div>
        <div className="stat">
          <div className="label">Max drawdown</div>
          <div className="value neg">{summary.max_drawdown ? fmtUsd(summary.max_drawdown) : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Trades</div>
          <div className="value">{summary.trade_count.toLocaleString()}</div>
        </div>
        <div className="stat">
          <div className="label">Open positions value</div>
          <div className="value">{fmtUsd(openValue)}</div>
        </div>
        <div className="stat">
          <div className="label">Leaderboard all-time</div>
          <div className="value">{fmtUsd(whale.sports_profit_alltime)}</div>
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>P&L calendar</h2>
        {daily.length > 0 ? (
          <PnlCalendar days={daily} />
        ) : (
          <EmptyState>Daily P&L appears once trades settle.</EmptyState>
        )}
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
                  <th className="num">Now</th>
                  <th className="num">Live value</th>
                  <th className="num">Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {open_positions.map((p) => (
                  <tr key={p.token_id}>
                    <td>{p.event_title || p.market_title || p.condition_id}</td>
                    <td>{p.outcome || '—'}</td>
                    <td className="num">{p.net_shares.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                    <td className="num">{p.avg_cost != null ? `${Math.round(p.avg_cost * 100)}¢` : '—'}</td>
                    <td className="num">{p.cur_price != null ? `${Math.round(p.cur_price * 100)}¢` : '—'}</td>
                    <td className="num">{fmtUsd(p.exposure)}</td>
                    <td className={`num ${(p.cash_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                      {p.cash_pnl != null ? fmtSignedUsd(p.cash_pnl) : '—'}
                    </td>
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
