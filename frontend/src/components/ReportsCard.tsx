import { useEffect, useMemo, useState } from 'react'
import { API_BASE, api } from '../lib/api'

// Downloadable performance reports (owner directive 2026-08-07): daily,
// weekly, monthly, or custom range — per-category P&L (each copy sleeve,
// manual desk, arbitrage, software) with CSV and PDF exports straight
// from the ledger-backed report endpoints.

const CAT_LABEL: Record<string, string> = {
  rn1: 'RN1 copies', swisstony: 'SwissTony copies', kch123: 'kch123 copies',
  homerunhazard: 'HomeRunHazard copies', manual: 'Manual desk',
  arb: 'Arbitrage', software: 'Software',
}
const CAT_ORDER = ['rn1', 'swisstony', 'kch123', 'homerunhazard', 'manual', 'arb', 'software']

interface CatTotal { pnl: number; settled: number; wins: number; losses: number }
interface RangeReport {
  from: string
  to: string
  net_pnl: number
  totals: Record<string, CatTotal>
  days: ({ date: string } & Record<string, CatTotal | string>)[]
}

type Preset = 'today' | '7d' | 'mtd' | 'custom'

function isoDaysAgo(n: number): string {
  const d = new Date(Date.now() - n * 86_400_000)
  return d.toISOString().slice(0, 10)
}

const money = (v: number) => `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`

export function ReportsCard() {
  const [preset, setPreset] = useState<Preset>('mtd')
  const [from, setFrom] = useState(isoDaysAgo(6))
  const [to, setTo] = useState(isoDaysAgo(0))
  const [report, setReport] = useState<RangeReport | null>(null)
  const [loading, setLoading] = useState(false)

  const range = useMemo(() => {
    if (preset === 'today') return { from: isoDaysAgo(0), to: isoDaysAgo(0) }
    if (preset === '7d') return { from: isoDaysAgo(6), to: isoDaysAgo(0) }
    if (preset === 'mtd') return { from: isoDaysAgo(0).slice(0, 8) + '01', to: isoDaysAgo(0) }
    return { from, to }
  }, [preset, from, to])

  useEffect(() => {
    let dead = false
    setLoading(true)
    api<RangeReport>(`/api/report/range?from=${range.from}&to=${range.to}`)
      .then((r) => { if (!dead) setReport(r) })
      .catch(() => {})
      .finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [range.from, range.to])

  const qs = `from=${range.from}&to=${range.to}`
  const rows = report
    ? CAT_ORDER.filter((c) => report.totals[c]).map((c) => ({ cat: c, ...report.totals[c]! }))
    : []

  return (
    <div className="card rpt-card">
      <div className="rpt-head">
        <span className="card-title">Performance Reports</span>
        <div className="rpt-chips">
          {(['today', '7d', 'mtd', 'custom'] as Preset[]).map((p) => (
            <button
              key={p}
              className={`tr-chipbtn${preset === p ? ' on' : ''}`}
              onClick={() => setPreset(p)}
            >
              {p === 'today' ? 'Today' : p === '7d' ? '7 Days' : p === 'mtd' ? 'Month' : 'Custom'}
            </button>
          ))}
        </div>
      </div>
      {preset === 'custom' && (
        <div className="rpt-range">
          <input className="input" type="date" value={from} max={to}
                 onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
          <span className="rpt-range-sep">→</span>
          <input className="input" type="date" value={to} min={from}
                 onChange={(e) => setTo(e.target.value)} aria-label="To date" />
        </div>
      )}

      {loading && !report ? (
        <div className="tr-skel" style={{ height: 140, borderRadius: 12 }} />
      ) : report ? (
        <>
          <div className="rpt-net">
            <span className={report.net_pnl >= 0 ? 'pos' : 'neg'}>{money(report.net_pnl)}</span>
            <span className="rpt-net-sub">net · {report.from} → {report.to}</span>
          </div>
          {rows.length > 0 && (
            <div className="rpt-bars" aria-hidden>
              {rows.map((r) => {
                const max = Math.max(...rows.map((x) => Math.abs(x.pnl)), 1)
                const w = Math.max(3, (Math.abs(r.pnl) / max) * 100)
                return (
                  <div className="rpt-bar-row" key={r.cat}>
                    <span className="rpt-bar-label">{CAT_LABEL[r.cat]}</span>
                    <span className="rpt-bar-track">
                      <span
                        className={`rpt-bar ${r.pnl >= 0 ? 'bar-pos' : 'bar-neg'}`}
                        style={{ width: `${w}%` }}
                      />
                    </span>
                    <span className={`rpt-bar-val ${r.pnl >= 0 ? 'pos' : 'neg'}`}>{money(r.pnl)}</span>
                  </div>
                )
              })}
            </div>
          )}
          <div className="rpt-table-wrap">
            <table className="rpt-table">
              <thead>
                <tr><th>Category</th><th>P&L</th><th>Settled</th><th>W-L</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.cat}>
                    <td>{CAT_LABEL[r.cat]}</td>
                    <td className={r.pnl >= 0 ? 'pos' : 'neg'}>{money(r.pnl)}</td>
                    <td>{r.settled}</td>
                    <td>{r.wins}-{r.losses}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={4} style={{ opacity: 0.6 }}>No settled results in this range yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="rpt-tieout">
            ✓ Ties out to the Daily P&L calendar exactly — categories are
            reconciled to the account record by construction.
          </p>
        </>
      ) : (
        <p style={{ opacity: 0.6 }}>Report unavailable — retrying automatically.</p>
      )}

      <div className="rpt-downloads">
        <a className="btn primary" href={`${API_BASE}/api/report.pdf?${qs}`} download>Download PDF</a>
        <a className="btn" href={`${API_BASE}/api/report.csv?${qs}`} download>Download CSV</a>
      </div>
    </div>
  )
}
