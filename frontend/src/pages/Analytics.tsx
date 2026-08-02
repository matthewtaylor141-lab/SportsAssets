import { useMemo, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import { TRRow, useTrackRecord } from '../lib/record'

/* Financial-grade analytics on the SAME account rows the Performance page
 * shows: equity + drawdown, daily P&L vs capital deployed, breakdowns by
 * bet type and sport. One series per axis, polarity in the status pair,
 * identity in labels — and any figure the sample cannot support reports
 * its n instead of a verdict. */

const MIN_N = 12

function EquityAndDrawdown({ daily }: { daily: { date: string; pnl: number }[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const pts = useMemo(() => {
    let acc = 0, peak = 0
    return daily.map((d) => {
      acc += d.pnl
      peak = Math.max(peak, acc)
      return { date: d.date, cum: acc, dd: acc - peak, day: d.pnl }
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
      <svg viewBox={`0 0 ${W} ${H + DH}`} className="an-svg"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          const fx = ((e.clientX - r.left) / r.width) * W
          setHover(Math.max(0, Math.min(pts.length - 1,
            Math.round(((fx - PAD) / (W - PAD * 2)) * (pts.length - 1)))))
        }}
        role="img" aria-label="Cumulative profit and drawdown by day">
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="var(--baseline)" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2.5"
          strokeLinejoin="round" strokeLinecap="round" />
        <g transform={`translate(0 ${H})`}>
          <line x1={PAD} x2={W - PAD} y1={4} y2={4} stroke="var(--baseline)" />
          <path d={dd} fill="rgba(224,82,82,0.25)" stroke="var(--critical)" strokeWidth="1.5" />
        </g>
        {h && (
          <g>
            <line x1={x(hover!)} x2={x(hover!)} y1={PAD} y2={H + DH - 4}
              stroke="var(--border-strong)" />
            <circle cx={x(hover!)} cy={y(h.cum)} r="4.5" fill="var(--accent)"
              stroke="var(--surface)" strokeWidth="2" />
          </g>
        )}
      </svg>
      <div className="tr-curve-tip">
        {h ? (
          <>
            <span className="mono">{h.date}</span>
            <span className={h.day >= 0 ? 'pos' : 'neg'}>day {fmtSignedUsd(h.day)}</span>
            <span className={h.cum >= 0 ? 'pos' : 'neg'}>equity {fmtSignedUsd(h.cum)}</span>
            <span className="neg">drawdown {fmtSignedUsd(h.dd)}</span>
          </>
        ) : <span className="muted">hover for daily detail</span>}
      </div>
    </div>
  )
}

function DailyColumns({ daily }: {
  daily: { date: string; pnl: number; deployed: number; settled: number }[]
}) {
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
              <div className={`an-col ${d.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
                style={{ height: `${(Math.abs(d.pnl) / maxAbs) * 46}%`,
                         [d.pnl >= 0 ? 'bottom' : 'top' as any]: '50%' }} />
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
            <span className="muted">
              {daily[hover].settled} settled · deployed {fmtUsd(daily[hover].deployed, 2)}
            </span>
          </>
        ) : <span className="muted">hover a day</span>}
      </div>
    </div>
  )
}

function DeployedColumns({ daily }: { daily: { date: string; deployed: number; trades: number }[] }) {
  const [hover, setHover] = useState<number | null>(null)
  if (!daily.length) return <EmptyState>No entries yet.</EmptyState>
  const max = Math.max(...daily.map((d) => d.deployed), 0.01)
  return (
    <div>
      <div className="an-row-title"><span>CAPITAL DEPLOYED PER DAY</span></div>
      <div className="an-cols an-cols-up" onMouseLeave={() => setHover(null)}>
        {daily.map((d, i) => (
          <div key={d.date} className="an-col-slot" onMouseEnter={() => setHover(i)}>
            <div className="an-col-track">
              <div className="an-col an-col-acc"
                style={{ height: `${(d.deployed / max) * 92}%`, bottom: 0 }} />
            </div>
          </div>
        ))}
      </div>
      <div className="tr-curve-tip">
        {hover !== null ? (
          <>
            <span className="mono">{daily[hover].date}</span>
            <span>{fmtUsd(daily[hover].deployed, 2)}</span>
            <span className="muted">{daily[hover].trades} positions entered</span>
          </>
        ) : <span className="muted">hover a day</span>}
      </div>
    </div>
  )
}

function GroupTable({ rows, by, title }: {
  rows: TRRow[]
  by: (r: TRRow) => string
  title: string
}) {
  const groups = useMemo(() => {
    const m = new Map<string, { staked: number; pnl: number; n: number; wins: number; open: number }>()
    for (const r of rows) {
      const g = m.get(by(r)) || { staked: 0, pnl: 0, n: 0, wins: 0, open: 0 }
      if (r.settled && r.pnl !== null) {
        g.staked += r.stake; g.pnl += r.pnl; g.n += 1
        if (r.pnl > 0) g.wins += 1
      } else g.open += 1
      m.set(by(r), g)
    }
    return [...m.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [rows, by])
  if (!groups.length) return <EmptyState>No trades yet.</EmptyState>
  const maxAbs = Math.max(...groups.map(([, g]) => Math.abs(g.pnl)), 0.01)
  return (
    <div>
      <div className="an-row-title">
        <span>{title}</span>
        <span className="muted">groups under n={MIN_N} report the sample, not a verdict</span>
      </div>
      <table className="an-table">
        <thead>
          <tr><th></th><th>settled</th><th>W–L</th><th>open</th><th>staked</th><th></th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {groups.map(([name, g]) => (
            <tr key={name} className={g.n && g.n < MIN_N ? 'thin' : ''}>
              <td>{name}</td>
              <td className="mono">{g.n}</td>
              <td className="mono">{g.wins}–{g.n - g.wins}</td>
              <td className="mono muted">{g.open}</td>
              <td className="mono">{fmtUsd(g.staked, 2)}</td>
              <td className="an-cell-bar">
                <div className="tr-sport-bar">
                  <div className="tr-sport-zero" />
                  <div className={`tr-sport-fill ${g.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}
                    style={{ width: `${(Math.abs(g.pnl) / maxAbs) * 50}%`,
                             [g.pnl >= 0 ? 'left' : 'right' as any]: '50%' }} />
                </div>
              </td>
              <td className={`mono ${g.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(g.pnl)}</td>
              <td className={`mono ${g.pnl >= 0 ? 'pos' : 'neg'}`}>
                {g.n >= MIN_N && g.staked > 0 ? fmtPct(g.pnl / g.staked) : g.n ? `n=${g.n}` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Analytics() {
  const { data, err } = useTrackRecord()
  if (err) return <EmptyState>{`Account API unreachable: ${err}`}</EmptyState>
  if (!data) return (
    <div className="page tr-page">
      <div className="card tr-skel" style={{ height: 280 }} />
      <div className="tr-columns">
        <div className="card tr-skel" style={{ height: 220 }} />
        <div className="card tr-skel" style={{ height: 220 }} />
      </div>
    </div>
  )

  return (
    <div className="page tr-page">
      <div className="card"><EquityAndDrawdown daily={data.daily} /></div>
      <div className="tr-columns">
        <div className="card"><DailyColumns daily={data.daily} /></div>
        <div className="card"><DeployedColumns daily={data.daily} /></div>
      </div>
      <div className="card">
        <GroupTable rows={data.trades} by={(r) => r.category} title="P&L BY BET TYPE" />
      </div>
      <div className="card">
        <GroupTable rows={data.trades} by={(r) => `${r.icon} ${r.sport}`} title="P&L BY SPORT" />
      </div>
    </div>
  )
}
