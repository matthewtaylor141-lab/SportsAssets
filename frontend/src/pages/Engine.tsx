import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { PnlCalendar } from '../components/PnlCalendar'
import { api } from '../lib/api'
import { fmtAgo, fmtCents, fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'

interface EngineSummary {
  totals: {
    fills: number
    settled: number
    staked: number
    settled_staked: number
    pnl: number
    roi: number | null
    first_ts: string | null
  }
  by_venue: { venue: string; fills: number; settled_staked: number; pnl: number }[]
  by_league: { league: string | null; fills: number; pnl: number }[]
  daily: { date: string; pnl: number; volume: number; trades: number }[]
}

interface EngineFill {
  id: number
  ts: string
  venue: string
  market_id: string
  outcome_id: string
  league: string | null
  band: string | null
  limit_price: number
  size_usd: number
  fair_value: number | null
  edge: number | null
  would_fill: boolean
  whale_alignment: { same_side?: { whale: string }[]; opposed?: { whale: string }[] } | null
  settled: boolean
  payout: number | null
  pnl: number | null
  market_title: string | null
  sport: string | null
  outcome: string | null
}

/** OUR model's recommendations — strictly separate from whale data. Every row
 * is a shadow fill the engine would have taken, recorded internally and
 * settled by our own resolution pipeline. */
interface EngineStatus {
  status: string
  beat_at?: string
  detail?: {
    feed_events?: number
    matched?: number
    books_checked?: number
    logged?: number
    rejects?: Record<string, number>
    candidates?: Record<string, number>
    error?: string
  }
}

export function Engine() {
  const [summary, setSummary] = useState<EngineSummary | null>(null)
  const [fills, setFills] = useState<EngineFill[] | null>(null)
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [venue, setVenue] = useState('')

  const refresh = () => {
    api<EngineSummary>('/api/engine/summary').then(setSummary).catch(() => {})
    api<EngineStatus>('/api/engine/status').then(setStatus).catch(() => {})
    api<EngineFill[]>(`/api/engine/fills?limit=100${venue ? `&venue=${venue}` : ''}`)
      .then(setFills)
      .catch(() => setFills([]))
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 30000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [venue])

  const t = summary?.totals

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <h1>⚡ Engine</h1>
        <span className="chip" style={{ color: 'var(--warning)', borderColor: 'var(--warning)' }}>
          SHADOW MODE — no live orders
        </span>
      </div>
      <p className="sub">
        Our internal model's recommendations, recorded and settled in our own database. Separate
        from whale data — this is what <strong>we</strong> would have traded, when sharp-book fair
        value beats the venue price by the measured threshold.
      </p>

      <div className="card" style={{ padding: '10px 14px' }}>
        {!status || status.status === 'never_reported' ? (
          <span style={{ color: 'var(--warning)' }}>
            ⚠ Engine has never reported in. Check that the edge-shadow worker is deployed with
            EDGE_ODDS_API_KEY set, and that EDGE_INGEST_TOKEN (worker) matches ENGINE_INGEST_TOKEN
            (API).
          </span>
        ) : status.status === 'error' ? (
          <span style={{ color: 'var(--critical)' }}>
            ✗ Last cycle failed {status.beat_at ? fmtAgo(status.beat_at) : ''}:{' '}
            <span className="mono">{status.detail?.error}</span>
          </span>
        ) : (
          <span style={{ color: 'var(--ink-2)', fontSize: 13 }}>
            <span style={{ color: 'var(--good)' }}>●</span> Last cycle{' '}
            {status.beat_at ? fmtAgo(status.beat_at) : '—'} · {status.detail?.feed_events ?? 0} feed
            events · {status.detail?.matched ?? 0} venue matches ·{' '}
            {status.detail?.books_checked ?? 0} books checked · {status.detail?.logged ?? 0} logged
            {status.detail?.candidates && (
              <> · candidates: {Object.entries(status.detail.candidates)
                .map(([v, n]) => `${v} ${n}`).join(', ')}</>
            )}
            {status.detail?.rejects && Object.keys(status.detail.rejects).length > 0 && (
              <span style={{ color: 'var(--muted)' }}>
                {' '}· rejected: {Object.entries(status.detail.rejects)
                  .map(([k, n]) => `${k} ×${n}`).join(', ')}
              </span>
            )}
          </span>
        )}
      </div>

      <div className="statgrid" style={{ marginBottom: 12 }}>
        <div className="stat">
          <div className="label">Recommendations</div>
          <div className="value">{t ? t.fills.toLocaleString() : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Would-be staked</div>
          <div className="value">{t ? fmtUsd(t.staked) : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Settled</div>
          <div className="value">{t ? t.settled.toLocaleString() : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Shadow P&L</div>
          <div className={`value ${(t?.pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {t ? fmtSignedUsd(t.pnl) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">ROI (settled)</div>
          <div className={`value ${(t?.roi ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {t?.roi != null ? fmtPct(t.roi) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">Tracking since</div>
          <div className="value" style={{ fontSize: 14 }}>
            {t?.first_ts ? new Date(t.first_ts).toLocaleDateString() : '—'}
          </div>
        </div>
      </div>

      {summary && summary.daily.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Shadow P&L calendar</h2>
          <PnlCalendar days={summary.daily} />
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 12px 4px' }}>
          <h2 style={{ margin: 0, flex: 1 }}>Recommendations</h2>
          {['', 'polymarket', 'kalshi'].map((v) => (
            <button
              key={v || 'all'}
              className="btn"
              style={venue === v ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
              onClick={() => setVenue(v)}
            >
              {v || 'All venues'}
            </button>
          ))}
        </div>
        {fills === null ? (
          <EmptyState>Loading…</EmptyState>
        ) : fills.length === 0 ? (
          <EmptyState>
            No recommendations recorded yet. Note this tab is NOT a copy of whale trades — the
            engine only logs when its own edge threshold is met on an allowlisted league and band.
            The status strip above shows each cycle's funnel: if feed events are 0, the odds key or
            sport coverage is the issue; if matches are 0, no venue markets line up with today's
            feed events; if everything is rejected, the thresholds are doing their job.
          </EmptyState>
        ) : (
          <div className="scroll-x">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Venue</th>
                  <th>Market</th>
                  <th className="num">Entry</th>
                  <th className="num">Fair</th>
                  <th className="num">Edge</th>
                  <th className="num">Size</th>
                  <th>Whales</th>
                  <th>Result</th>
                  <th className="num">P&L</th>
                </tr>
              </thead>
              <tbody>
                {fills.map((f) => {
                  const aligned = f.whale_alignment?.same_side?.length || 0
                  const opposed = f.whale_alignment?.opposed?.length || 0
                  return (
                    <tr key={f.id}>
                      <td style={{ whiteSpace: 'nowrap' }}>{fmtAgo(f.ts)}</td>
                      <td>
                        <span className="chip">{f.venue}</span>
                      </td>
                      <td>
                        {f.outcome ? <strong>{f.outcome}</strong> : f.outcome_id.slice(0, 14)}
                        {f.market_title && (
                          <span style={{ color: 'var(--muted)' }}> — {f.market_title}</span>
                        )}
                        {f.league && <span className="chip" style={{ marginLeft: 6 }}>{f.league}</span>}
                      </td>
                      <td className="num">{fmtCents(f.limit_price)}</td>
                      <td className="num">{f.fair_value != null ? fmtCents(f.fair_value) : '—'}</td>
                      <td className="num pos">
                        {f.edge != null ? `+${(f.edge * 100).toFixed(1)}¢` : '—'}
                      </td>
                      <td className="num">{fmtUsd(f.size_usd)}</td>
                      <td>
                        {aligned > 0 && (
                          <span className="chip" style={{ color: 'var(--good)', borderColor: 'var(--good)' }}>
                            +{aligned} whale{aligned > 1 ? 's' : ''}
                          </span>
                        )}
                        {opposed > 0 && (
                          <span className="chip" style={{ color: 'var(--critical)', borderColor: 'var(--critical)' }}>
                            {opposed} opposed
                          </span>
                        )}
                        {!aligned && !opposed && <span style={{ color: 'var(--muted)' }}>—</span>}
                      </td>
                      <td>
                        {f.settled ? (
                          (f.pnl ?? 0) >= 0 ? '✓ Win' : '✗ Loss'
                        ) : f.would_fill ? (
                          <span style={{ color: 'var(--muted)' }}>open</span>
                        ) : (
                          <span style={{ color: 'var(--warning)' }}>thin book</span>
                        )}
                      </td>
                      <td className={`num ${(f.pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                        {f.settled && f.pnl != null ? fmtSignedUsd(f.pnl) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
