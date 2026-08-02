import { useEffect, useMemo, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { api } from '../lib/api'
import { fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'

/* Financial-grade analytics on the AI trader's ledger.
 *
 * Four views, one discipline: every panel is computed from the same fill
 * rows the Performance page shows, one series per axis, polarity in the
 * status pair, identity carried by labels — and any figure the sample
 * cannot support says so instead of rendering a confident zero. */

interface EngineFill {
  id: number
  ts: string
  band: string | null
  league: string | null
  sport: string | null
  limit_price: number
  size_usd: number
  edge: number | null
  settled: boolean
  payout: number | null
  pnl: number | null
  settled_at: string | null
}

interface EngineSummary {
  totals: { fills: number; settled: number; staked: number; settled_staked: number; pnl: number }
  daily: { date: string; pnl: number; volume: number; trades: number }[]
}

const MIN_N = 12 // below this a per-bucket figure is noise, and says so

function useLedger() {
  const [fills, setFills] = useState<EngineFill[] | null>(null)
  const [summary, setSummary] = useState<EngineSummary | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([
      api<EngineFill[]>('/api/engine/fills?limit=500'),
      api<EngineSummary>('/api/engine/summary'),
    ])
      .then(([f, s]) => { setFills(f); setSummary(s) })
      .catch((e) => setErr(String(e)))
  }, [])
  return { fills, summary, err }
}

/** Equity curve with its drawdown rendered as an underwater area below. */
function EquityAndDrawdown({ daily }: { daily: EngineSummary['daily'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const pts = useMemo(() => {
    let acc = 0
    let peak = 0
    return daily.map((d) => {
      acc += d.pnl
      peak = Math.max(peak, acc)
      return { date: d.date, cum: acc, dd: acc - peak }
    })
  }, [daily])
  if (pts.length < 2) return <EmptyState>Two settled days make a curve — one is a dot.</EmptyState>

  const W = 860, H = 200, DH = 70, PAD = 14
  const min = Math.min(0, ...pts.map((p) => p.cum))
  const max = Math.max(0.01, ...pts.map((p) => p.cum))
  const ddMin = Math.min(-0.01, ...pts.map((p) => p.dd))
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - PAD * 2)
  const y = (v: number) => H - PAD - ((v - min) / (max - min)) * (H - PAD * 2)
  const dy = (v: number) => 4 + (v / ddMin) * (DH - 8)
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' ')
  const dd = `M${x(0)},4 ` + pts.map((p, i) => `L${x(i).toFixed(1)},${dy(p.dd).toFixed(1)}`).join(' ')
    + ` L${x(pts.length - 1).toFixed(1)},4 Z`
  const h = hover !== null ? pts[hover] : null
  const maxDD = Math.min(...pts.map((p) => p.dd))

  return (
    <div>
      <div className="an-row-title">
        <span>EQUITY &amp; DRAWDOWN</span>
        <span className="muted">max drawdown <b className="neg mono">{fmtSignedUsd(maxDD)}</b></span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H + DH}`} className="an-svg"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const fx = ((e.clientX - r.left) / r.width) * W
          setHover(Math.max(0, Math.min(pts.length - 1,
            Math.round(((fx - PAD) / (W - PAD * 2)) * (pts.length - 1)))))
        }}
        role="img" aria-label="Cumulative profit and drawdown by day"
      >
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="var(--baseline)" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        <g transform={`translate(0 ${H})`}>
          <line x1={PAD} x2={W - PAD} y1={4} y2={4} stroke="var(--baseline)" />
          <path d={dd} fill="rgba(224,82,82,0.25)" stroke="var(--critical)" strokeWidth="1.5" />
        </g>
        {h && (
          <g>
            <line x1={x(hover!)} x2={x(hover!)} y1={PAD} y2={H + DH - 4}
              stroke="var(--border-strong)" />
            <circle cx={x(hover!)} cy={y(h.cum)} r="4" fill="var(--accent)"
              stroke="var(--surface)" strokeWidth="2" />
          </g>
        )}
      </svg>
      <div className="tr-curve-tip">
        {h ? (
          <>
            <span className="mono">{h.date}</span>
            <span className={h.cum >= 0 ? 'pos' : 'neg'}>equity {fmtSignedUsd(h.cum)}</span>
            <span className="neg">drawdown {fmtSignedUsd(h.dd)}</span>
          </>
        ) : <span className="muted">hover for daily detail</span>}
      </div>
    </div>
  )
}

/** Daily P&L columns — thin marks, rounded data ends, baseline anchored. */
function DailyColumns({ daily }: { daily: EngineSummary['daily'] }) {
  const [hover, setHover] = useState<number | null>(null)
  if (!daily.length) return <EmptyState>No settled days yet.</EmptyState>
  const maxAbs = Math.max(...daily.map((d) => Math.abs(d.pnl)), 0.01)
  return (
    <div>
      <div className="an-row-title"><span>DAILY P&amp;L</span></div>
      <div className="an-cols" onMouseLeave={() => setHover(null)}>
        {daily.map((d, i) => (
          <div key={d.date} className="an-col-slot" onMouseEnter={() => setHover(i)}>
            <div className="an-col-track">
              <div
                className={`an-col ${d.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
                style={{
                  height: `${(Math.abs(d.pnl) / maxAbs) * 46}%`,
                  [d.pnl >= 0 ? 'bottom' : 'top' as any]: '50%',
                }}
              />
              <div className="an-col-base" />
            </div>
          </div>
        ))}
      </div>
      <div className="tr-curve-tip">
        {hover !== null ? (
          <>
            <span className="mono">{daily[hover].date}</span>
            <span className={daily[hover].pnl >= 0 ? 'pos' : 'neg'}>
              {fmtSignedUsd(daily[hover].pnl)}
            </span>
            <span className="muted">{daily[hover].trades} settled</span>
          </>
        ) : <span className="muted">hover a day</span>}
      </div>
    </div>
  )
}

/** ROI by entry-price band — the engine's own selection surface, graded. */
function BandTable({ fills }: { fills: EngineFill[] }) {
  const rows = useMemo(() => {
    const by = new Map<string, { staked: number; pnl: number; n: number; wins: number }>()
    for (const f of fills) {
      if (!f.settled || f.pnl === null || !f.band) continue
      const r = by.get(f.band) || { staked: 0, pnl: 0, n: 0, wins: 0 }
      r.staked += f.size_usd; r.pnl += f.pnl; r.n += 1
      if (f.pnl > 0) r.wins += 1
      by.set(f.band, r)
    }
    return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [fills])
  if (!rows.length) return <EmptyState>Band grades appear at first settlements.</EmptyState>
  const maxAbs = Math.max(...rows.map(([, r]) => Math.abs(r.pnl)), 0.01)
  return (
    <div>
      <div className="an-row-title">
        <span>P&amp;L BY ENTRY PRICE BAND</span>
        <span className="muted">buckets under n={MIN_N} report the sample, not a verdict</span>
      </div>
      <table className="an-table">
        <thead>
          <tr><th>band</th><th>n</th><th>W–L</th><th>staked</th><th></th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {rows.map(([band, r]) => (
            <tr key={band} className={r.n < MIN_N ? 'thin' : ''}>
              <td className="mono">{band}</td>
              <td className="mono">{r.n}</td>
              <td className="mono">{r.wins}–{r.n - r.wins}</td>
              <td className="mono">{fmtUsd(r.staked, 2)}</td>
              <td className="an-cell-bar">
                <div className="tr-sport-bar">
                  <div className="tr-sport-zero" />
                  <div
                    className={`tr-sport-fill ${r.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
                    style={{
                      width: `${(Math.abs(r.pnl) / maxAbs) * 50}%`,
                      [r.pnl >= 0 ? 'left' : 'right' as any]: '50%',
                    }}
                  />
                </div>
              </td>
              <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(r.pnl)}</td>
              <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>
                {r.n >= MIN_N ? fmtPct(r.pnl / r.staked) : `n=${r.n}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Full per-sport table — the same data the Performance bars summarize. */
function SportTable({ fills }: { fills: EngineFill[] }) {
  const rows = useMemo(() => {
    const by = new Map<string, { staked: number; pnl: number; n: number; wins: number; open: number }>()
    for (const f of fills) {
      const key = f.sport || f.league?.toUpperCase() || 'Other'
      const r = by.get(key) || { staked: 0, pnl: 0, n: 0, wins: 0, open: 0 }
      if (f.settled && f.pnl !== null) {
        r.staked += f.size_usd; r.pnl += f.pnl; r.n += 1
        if (f.pnl > 0) r.wins += 1
      } else r.open += 1
      by.set(key, r)
    }
    return [...by.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [fills])
  if (!rows.length) return <EmptyState>No trades yet.</EmptyState>
  return (
    <div>
      <div className="an-row-title"><span>SPORT LEDGER (settled + open)</span></div>
      <table className="an-table">
        <thead>
          <tr><th>sport</th><th>settled</th><th>W–L</th><th>open</th><th>staked</th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {rows.map(([name, r]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="mono">{r.n}</td>
              <td className="mono">{r.wins}–{r.n - r.wins}</td>
              <td className="mono muted">{r.open}</td>
              <td className="mono">{fmtUsd(r.staked, 2)}</td>
              <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(r.pnl)}</td>
              <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>
                {r.n >= MIN_N && r.staked > 0 ? fmtPct(r.pnl / r.staked) : r.n ? `n=${r.n}` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Analytics() {
  const { fills, summary, err } = useLedger()
  if (err) return <EmptyState>{`API unreachable: ${err}`}</EmptyState>
  if (!fills || !summary) return <EmptyState>Computing…</EmptyState>

  return (
    <div className="page tr-page">
      <div className="card"><EquityAndDrawdown daily={summary.daily} /></div>
      <div className="tr-columns">
        <div className="card"><DailyColumns daily={summary.daily} /></div>
        <div className="card"><BandTable fills={fills} /></div>
      </div>
      <div className="card"><SportTable fills={fills} /></div>
    </div>
  )
}
