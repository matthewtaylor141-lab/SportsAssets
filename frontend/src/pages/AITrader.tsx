import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { PnlCalendar } from '../components/PnlCalendar'
import { api } from '../lib/api'
import { fmtAgo, fmtCents, fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'

interface AIReport {
  source: string
  ratio: number
  days: number
  summary: {
    copies: number
    missed: number
    open: number
    settled: number
    staked: number
    open_exposure: number
    realized_pnl: number
    settled_staked: number
    counterfactual_pnl: number
    roi: number | null
    slippage_cost: number
    reaction_p50: number | null
    slippage_p50: number | null
    first_trade: string | null
  }
  daily: { date: string; pnl: number; volume: number; trades: number; counterfactual: number }[]
  recent: {
    id: number
    placed_at: string
    reaction_s: number | null
    status: string
    his_price: number
    fill_vwap: number | null
    slippage_cents: number | null
    clip_target: number
    filled_notional: number
    pnl: number | null
    counterfactual_pnl: number | null
    payout: number | null
    market_title: string | null
    outcome: string | null
    sport: string | null
  }[]
}

/** AI TRADER: paper account copying the reference whale at a size ratio.
 * Fills come from REAL residual order books at our real reaction time —
 * the 7-day P&L vs the counterfactual answers whether his own market
 * impact destroys the copied edge. */
export function AITrader() {
  const [report, setReport] = useState<AIReport | null>(null)
  const [days, setDays] = useState(7)

  useEffect(() => {
    const load = () => api<AIReport>(`/api/ai-trader?days=${days}`).then(setReport).catch(() => {})
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [days])

  const s = report?.summary
  const fillRate = s && s.copies > 0 ? (s.copies - s.missed) / s.copies : null

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <h1>🤖 AI Trader</h1>
        <span className="chip" style={{ color: 'var(--warning)', borderColor: 'var(--warning)' }}>
          PAPER TRADING — simulated fills, real books
        </span>
        {report && (
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>
            copies <strong>{report.source}</strong> at {(report.ratio * 100).toFixed(0)}% size
          </span>
        )}
        <span style={{ flex: 1 }} />
        {[7, 30].map((d) => (
          <button
            key={d}
            className="btn"
            style={days === d ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
            onClick={() => setDays(d)}
          >
            {d}d
          </button>
        ))}
      </div>
      <p className="sub">
        Every {report?.source ?? 'swisstony'} BUY we detect is "placed" instantly at the live
        residual book price for {((report?.ratio ?? 0.1) * 100).toFixed(0)}% of his stake, then
        settled by our resolution pipeline. The counterfactual column prices the same clips at{' '}
        <em>his</em> fills — the gap is exactly what his market impact costs a copier.
      </p>

      <div className="statgrid" style={{ marginBottom: 12 }}>
        <div className="stat">
          <div className="label">Copies</div>
          <div className="value">{s ? s.copies.toLocaleString() : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Fill rate</div>
          <div className="value">{fillRate != null ? fmtPct(fillRate, 0) : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Staked</div>
          <div className="value">{s ? fmtUsd(s.staked) : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Open exposure</div>
          <div className="value">{s ? fmtUsd(s.open_exposure) : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Realized P&L</div>
          <div className={`value ${(s?.realized_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {s ? fmtSignedUsd(s.realized_pnl) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">ROI (settled)</div>
          <div className={`value ${(s?.roi ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {s?.roi != null ? fmtPct(s.roi) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">At his prices</div>
          <div className={`value ${(s?.counterfactual_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
            {s ? fmtSignedUsd(s.counterfactual_pnl) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">Slippage cost</div>
          <div className={`value ${(s?.slippage_cost ?? 0) <= 0 ? 'pos' : 'neg'}`}>
            {s ? fmtUsd(s.slippage_cost) : '—'}
          </div>
        </div>
        <div className="stat">
          <div className="label">Reaction p50</div>
          <div className="value">{s?.reaction_p50 != null ? `${s.reaction_p50.toFixed(1)}s` : '—'}</div>
        </div>
        <div className="stat">
          <div className="label">Slippage p50</div>
          <div className="value">{s?.slippage_p50 != null ? `${s.slippage_p50.toFixed(1)}¢` : '—'}</div>
        </div>
      </div>

      {report && report.daily.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Daily settled P&L (ours vs at-his-prices)</h2>
          <PnlCalendar days={report.daily} />
          <div className="legend">
            <span style={{ color: 'var(--muted)' }}>
              Counterfactual by day:{' '}
              {report.daily.map((d) => `${d.date.slice(5)}: ${fmtSignedUsd(d.counterfactual)}`)
                .join(' · ')}
            </span>
          </div>
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <h2 style={{ margin: '12px 12px 6px' }}>Trades</h2>
        {!report ? (
          <EmptyState>Loading…</EmptyState>
        ) : report.recent.length === 0 ? (
          <EmptyState>
            No copies yet — rows appear seconds after {report.source}'s next BUY. (The copier only
            acts on fresh detections, never on imported history.)
          </EmptyState>
        ) : (
          <div className="scroll-x">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Market</th>
                  <th className="num">His</th>
                  <th className="num">Ours</th>
                  <th className="num">Slip</th>
                  <th className="num">Clip</th>
                  <th>Status</th>
                  <th className="num">P&L</th>
                  <th className="num">At his price</th>
                </tr>
              </thead>
              <tbody>
                {report.recent.map((t) => (
                  <tr key={t.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtAgo(t.placed_at)}</td>
                    <td>
                      {t.outcome && <strong>{t.outcome}</strong>}
                      {t.market_title && (
                        <span style={{ color: 'var(--muted)' }}> — {t.market_title}</span>
                      )}
                      {t.sport && t.sport !== 'unclassified' && (
                        <span className="chip" style={{ marginLeft: 6 }}>{t.sport}</span>
                      )}
                    </td>
                    <td className="num">{fmtCents(t.his_price)}</td>
                    <td className="num">{t.fill_vwap != null ? fmtCents(t.fill_vwap) : '—'}</td>
                    <td className="num">
                      {t.slippage_cents != null ? `${t.slippage_cents.toFixed(1)}¢` : '—'}
                    </td>
                    <td className="num">{fmtUsd(t.filled_notional || t.clip_target)}</td>
                    <td>
                      {t.status === 'settled' ? (
                        (t.pnl ?? 0) >= 0 ? <span className="pos">✓ Win</span>
                          : <span className="neg">✗ Loss</span>
                      ) : t.status === 'missed' ? (
                        <span style={{ color: 'var(--warning)' }}>missed (no book)</span>
                      ) : (
                        <span style={{ color: 'var(--muted)' }}>open</span>
                      )}
                    </td>
                    <td className={`num ${(t.pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                      {t.pnl != null ? fmtSignedUsd(t.pnl) : '—'}
                    </td>
                    <td className={`num ${(t.counterfactual_pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                      {t.counterfactual_pnl != null ? fmtSignedUsd(t.counterfactual_pnl) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
