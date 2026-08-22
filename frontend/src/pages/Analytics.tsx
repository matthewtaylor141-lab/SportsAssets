import { useMemo, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { fmtPct, fmtSignedUsd, fmtUsd } from '../lib/format'
import { CopiesDayWhale, CopiesWhaleSport, useCopiesRecord } from '../lib/record'
import { sportMeta } from './TrackRecord'

/* Financial-grade analytics on the SAME copies record the Performance
 * page headlines (owner order 2026-08-22: copy-whale numbers only, no
 * software/blended cohorts anywhere public): equity + drawdown, daily
 * P&L, per-whale form, ROI by sport. One series per axis, polarity in
 * the status pair, identity in labels — and any figure the sample
 * cannot support reports its n instead of a verdict. */

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
        <span>EQUITY &amp; DRAWDOWN · COPY PORTFOLIO</span>
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
        role="img" aria-label="Cumulative copy profit and drawdown by day">
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
  daily: { date: string; pnl: number; settled: number; wins: number }[]
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
              {daily[hover].wins}W–{daily[hover].settled - daily[hover].wins}L
              {' '}· {daily[hover].settled} settled
            </span>
          </>
        ) : <span className="muted">hover a day</span>}
      </div>
    </div>
  )
}

/** Cumulative-P&L sparkline for one whale's daily series (chronological). */
function Spark({ days }: { days: { day: string; pnl: number }[] }) {
  const pts = useMemo(() => {
    let acc = 0
    return days.map((d) => (acc += d.pnl))
  }, [days])
  if (pts.length < 2) return <span className="muted mono">n={pts.length}</span>
  const W = 120, H = 28, PAD = 2
  const min = Math.min(0, ...pts)
  const max = Math.max(0.01, ...pts)
  const x = (i: number) => PAD + (i / (pts.length - 1)) * (W - PAD * 2)
  const y = (v: number) => H - PAD - ((v - min) / (max - min || 1)) * (H - PAD * 2)
  const path = pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const up = pts[pts.length - 1] >= 0
  return (
    <svg className="an-spark" viewBox={`0 0 ${W} ${H}`} aria-hidden>
      <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="var(--baseline)" />
      <path d={path} fill="none"
        stroke={up ? 'var(--good)' : 'var(--critical)'}
        strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.2"
        fill={up ? 'var(--good)' : 'var(--critical)'} />
    </svg>
  )
}

/** Per-whale form: full-window record plus each whale's own curve. */
function WhaleForm({ whales, dailyByWhale }: {
  whales: { whale: string; settled: number; wins: number; losses: number
            pnl: number; staked: number; roi: number | null }[]
  dailyByWhale: CopiesDayWhale[]
}) {
  const series = useMemo(() => {
    const by = new Map<string, { day: string; pnl: number }[]>()
    // Served newest-first; sparkline wants chronological.
    for (let i = dailyByWhale.length - 1; i >= 0; i--) {
      const d = dailyByWhale[i]
      const arr = by.get(d.whale) || []
      arr.push({ day: d.day, pnl: d.pnl })
      by.set(d.whale, arr)
    }
    return by
  }, [dailyByWhale])
  if (!whales.length) return <EmptyState>No settled copy trades yet.</EmptyState>
  return (
    <div>
      <div className="an-row-title">
        <span>PER-WHALE FORM</span>
        <span className="muted">curve = cumulative P&amp;L, recent window</span>
      </div>
      <table className="an-table">
        <thead>
          <tr><th></th><th>form</th><th>settled</th><th>W–L</th><th>staked</th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {whales.map((w) => (
            <tr key={w.whale} className={w.settled < MIN_N ? 'thin' : ''}>
              <td>{w.whale}</td>
              <td><Spark days={series.get(w.whale) || []} /></td>
              <td className="mono">{w.settled}</td>
              <td className="mono">{w.wins}–{w.losses}</td>
              <td className="mono">{fmtUsd(w.staked, 2)}</td>
              <td className={`mono ${w.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(w.pnl)}</td>
              <td className={`mono ${w.pnl >= 0 ? 'pos' : 'neg'}`}>
                {w.settled >= MIN_N && w.roi !== null ? fmtPct(w.roi) : `n=${w.settled}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** ROI by sport, aggregated across whales from the whale×sport split. */
function SportRoi({ rows }: { rows: CopiesWhaleSport[] }) {
  const sports = useMemo(() => {
    const m = new Map<string, { staked: number; pnl: number; n: number; wins: number }>()
    for (const r of rows) {
      const g = m.get(r.sport) || { staked: 0, pnl: 0, n: 0, wins: 0 }
      g.staked += r.staked; g.pnl += r.pnl; g.n += r.settled; g.wins += r.wins
      m.set(r.sport, g)
    }
    return [...m.entries()].sort((a, b) => b[1].pnl - a[1].pnl)
  }, [rows])
  if (!sports.length) return <EmptyState>No settled copy trades yet.</EmptyState>
  const maxAbs = Math.max(...sports.map(([, g]) => Math.abs(g.pnl)), 0.01)
  return (
    <div>
      <div className="an-row-title">
        <span>ROI BY SPORT</span>
        <span className="muted">groups under n={MIN_N} report the sample, not a verdict</span>
      </div>
      <table className="an-table">
        <thead>
          <tr><th></th><th>settled</th><th>W–L</th><th>staked</th><th></th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {sports.map(([sport, g]) => {
            const m = sportMeta(sport)
            return (
              <tr key={sport} className={g.n && g.n < MIN_N ? 'thin' : ''}>
                <td>{m.icon} {m.label}</td>
                <td className="mono">{g.n}</td>
                <td className="mono">{g.wins}–{g.n - g.wins}</td>
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
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** The full whale × sport split — where each whale actually earns. */
function WhaleSportTable({ rows }: { rows: CopiesWhaleSport[] }) {
  if (!rows.length) return <EmptyState>No settled copy trades yet.</EmptyState>
  return (
    <div>
      <div className="an-row-title">
        <span>WHALE × SPORT</span>
        <span className="muted">every cell of the copy record</span>
      </div>
      <table className="an-table">
        <thead>
          <tr><th>whale</th><th>sport</th><th>settled</th><th>W–L</th><th>staked</th><th>P&amp;L</th><th>ROI</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const m = sportMeta(r.sport)
            return (
              <tr key={`${r.whale}-${r.sport}`} className={r.settled < MIN_N ? 'thin' : ''}>
                <td>{r.whale}</td>
                <td>{m.icon} {m.label}</td>
                <td className="mono">{r.settled}</td>
                <td className="mono">{r.wins}–{r.losses}</td>
                <td className="mono">{fmtUsd(r.staked, 2)}</td>
                <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>{fmtSignedUsd(r.pnl)}</td>
                <td className={`mono ${r.pnl >= 0 ? 'pos' : 'neg'}`}>
                  {r.settled >= MIN_N && r.roi !== null ? fmtPct(r.roi) : `n=${r.settled}`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function Analytics() {
  const { data, err } = useCopiesRecord()
  const chrono = useMemo(() => [...(data?.daily || [])].reverse(), [data])
  if (err && !data) return <EmptyState>{`Record API unreachable: ${err}`}</EmptyState>
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
      <div className="card">
        <EquityAndDrawdown daily={chrono.map((d) => ({ date: d.day, pnl: d.pnl }))} />
      </div>
      <div className="tr-columns">
        <div className="card">
          <DailyColumns daily={chrono.map((d) => ({
            date: d.day, pnl: d.pnl, settled: d.settled, wins: d.wins }))} />
        </div>
        <div className="card">
          <SportRoi rows={data.by_whale_sport || []} />
        </div>
      </div>
      <div className="card">
        <WhaleForm whales={data.by_whale} dailyByWhale={data.daily_by_whale || []} />
      </div>
      <div className="card">
        <WhaleSportTable rows={data.by_whale_sport || []} />
      </div>
      <div className="tr-foot muted" style={{ padding: '0 4px' }}>
        Performance shown is the whale copy portfolio: every settled copy
        trade, uncapped. Full account statements available to investors on
        request.
      </div>
    </div>
  )
}
