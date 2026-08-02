import { useEffect, useMemo, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { api } from '../lib/api'
import { fmtUsd } from '../lib/format'

/* The evidence page: the engine's own telemetry, rendered for a team that
 * wants to AUDIT the software, not admire it. Everything here is the live
 * /api/engine/status heartbeat — the same funnel counters, adverse-
 * selection lab, execution costs and budget the engine steers itself by.
 * If a number is unmeasured it says so; measurement is the product. */

interface EngineStatus {
  beat_at?: string
  detail?: Record<string, any>
}

const API_BASE = '' // api() prefixes

function useEngine() {
  const [status, setStatus] = useState<EngineStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let dead = false
    const load = () =>
      api<EngineStatus>('/api/engine/status')
        .then((d) => !dead && (setStatus(d), setErr(null)))
        .catch((e) => !dead && setErr(String(e)))
    load()
    const t = setInterval(load, 30_000)
    return () => { dead = true; clearInterval(t) }
  }, [])
  return { status, err }
}

function Stage({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return (
    <div className="sy-stage">
      <div className="sy-stage-val mono">{value ?? '—'}</div>
      <div className="sy-stage-label">{label}</div>
      {sub && <div className="sy-stage-sub muted">{sub}</div>}
    </div>
  )
}

/** The decision funnel: what arrived, what survived each gate, what traded. */
function Funnel({ d }: { d: Record<string, any> }) {
  const stages = [
    { label: 'FEED EVENTS', value: d.feed_events },
    { label: 'MATCHED', value: d.matched },
    { label: 'TRADEABLE', value: d.tradeable },
    { label: 'BOOKS PRICED', value: d.books_checked },
    { label: 'ORDERS', value: d.logged, sub: d.mode },
  ]
  return (
    <div>
      <div className="an-row-title">
        <span>DECISION FUNNEL · LAST CYCLE ({d.cycle_s ?? '?'}s)</span>
        <span className="muted">refusal is the default; every drop has a named reason</span>
      </div>
      <div className="sy-funnel">
        {stages.map((s, i) => (
          <div key={s.label} className="sy-funnel-cell">
            <Stage {...s} />
            {i < stages.length - 1 && <span className="sy-arrow">→</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

/** Refusal reasons, ranked — the engine's blind spots and its discipline,
 * in one list. */
function Refusals({ d }: { d: Record<string, any> }) {
  const rows = useMemo(() => {
    const merged: Record<string, number> = {}
    for (const src of [d.rejects || {}, d.blockers || {}]) {
      for (const [k, v] of Object.entries(src)) {
        merged[k] = Math.max(merged[k] || 0, Number(v) || 0)
      }
    }
    return Object.entries(merged).sort((a, b) => b[1] - a[1]).slice(0, 14)
  }, [d])
  if (!rows.length) return <EmptyState>No refusals recorded this cycle.</EmptyState>
  const max = rows[0][1] || 1
  return (
    <div>
      <div className="an-row-title">
        <span>WHY THE ENGINE SAID NO · per cycle</span>
      </div>
      <div className="sy-refusals">
        {rows.map(([k, v]) => (
          <div key={k} className="sy-ref-row" title={`${k}: ${v} refusals in the last cycle`}>
            <span className="sy-ref-name mono">{k}</span>
            <div className="sy-ref-bar">
              <div style={{ width: `${(v / max) * 100}%` }} />
            </div>
            <span className="sy-ref-n mono">{v}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** Adverse-selection lab: fills vs the free-sample control group. */
function DriftLab({ d }: { d: Record<string, any> }) {
  const drift = d.edge_drift || {}
  const byCat: Record<string, any> = drift.by_category || {}
  const free = d.price_drift || {}
  const freeCat: Record<string, any> = free.by_category || {}
  const rev = d.reversion || d.shrink || {}
  const cats = [...new Set([...Object.keys(byCat), ...Object.keys(freeCat)])].sort()
  return (
    <div>
      <div className="an-row-title">
        <span>ADVERSE-SELECTION LAB</span>
        <span className="muted">
          fair value at entry vs one minute later — edge that evaporates was never ours
        </span>
      </div>
      <table className="an-table">
        <thead>
          <tr>
            <th>category</th>
            <th>our fills n</th><th>drift</th><th>retention</th>
            <th>control n</th><th>control drift</th>
          </tr>
        </thead>
        <tbody>
          {cats.map((c) => {
            const f = byCat[c] || {}
            const g = freeCat[c] || {}
            const bad = f.mean_drift_c != null && f.mean_drift_c < -1
            return (
              <tr key={c}>
                <td>{c}</td>
                <td className="mono">{f.n ?? '—'}</td>
                <td className={`mono ${bad ? 'neg' : ''}`}>
                  {f.mean_drift_c != null ? `${f.mean_drift_c > 0 ? '+' : ''}${f.mean_drift_c}¢` : 'unmeasured'}
                </td>
                <td className="mono">{f.retention ?? '—'}</td>
                <td className="mono muted">{g.n ?? '—'}</td>
                <td className="mono muted">
                  {g.mean_drift_c != null ? `${g.mean_drift_c > 0 ? '+' : ''}${g.mean_drift_c}¢` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="sy-note muted">
        Control = outcomes priced but not bought: staleness without selection. Our fills
        carry both, so fills-minus-control is the price of being chosen.
        {rev.keep != null && (
          <> Winner's-curse shrinkage: keep <b className="mono">{rev.keep}</b> on{' '}
          n={(rev.n ?? 0).toLocaleString()} — {rev.keep < 1 ? 'active' : 'inactive'}.</>
        )}
      </div>
    </div>
  )
}

/** Execution costs: what crossing costs vs what the model claims to win. */
function Costs({ d }: { d: Record<string, any> }) {
  const spread: Record<string, any> = d.spread_cost || {}
  const margin: Record<string, any> = d.entry_margin || {}
  const cats = [...new Set([...Object.keys(spread), ...Object.keys(margin)])].sort()
  if (!cats.length) return <EmptyState>No book snapshots yet.</EmptyState>
  return (
    <div>
      <div className="an-row-title">
        <span>EXECUTION ECONOMICS · profit per $ of turnover, measured AT ENTRY</span>
      </div>
      <table className="an-table">
        <thead>
          <tr>
            <th>category</th><th>n</th><th>spread</th><th>paid over mid</th>
            <th>claimed edge</th><th>gross margin</th><th>net margin</th>
          </tr>
        </thead>
        <tbody>
          {cats.map((c) => {
            const sp = spread[c] || {}
            const m = margin[c] || {}
            const net = m.net_margin
            return (
              <tr key={c}>
                <td>{c}</td>
                <td className="mono">{sp.n ?? m.n_fills ?? '—'}</td>
                <td className="mono">{sp.spread_c != null ? `${sp.spread_c}¢` : '—'}</td>
                <td className="mono">{sp.paid_over_mid_c != null ? `${sp.paid_over_mid_c}¢` : '—'}</td>
                <td className="mono">{sp.edge_c != null ? `${sp.edge_c}¢` : '—'}</td>
                <td className={`mono ${m.gross_margin > 0 ? 'pos' : m.gross_margin < 0 ? 'neg' : ''}`}>
                  {m.gross_margin != null ? `${(m.gross_margin * 100).toFixed(2)}%` : '—'}
                </td>
                <td className={`mono ${net > 0 ? 'pos' : net < 0 ? 'neg' : ''}`}>
                  {net != null ? `${(net * 100).toFixed(2)}%`
                    : m.retention_n != null ? `unmeasured (ret n=${m.retention_n})` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="sy-note muted">
        Net margin = (claimed edge × retention − paid over mid) / price. Converges in
        hundreds of fills where settled P&L needs thousands; blind to stable mapping
        errors, which is why settlement still outranks it.
      </div>
    </div>
  )
}

function Budget({ d }: { d: Record<string, any> }) {
  const b: Record<string, any> = d.budget || {}
  return (
    <div>
      <div className="an-row-title"><span>RISK STATE</span></div>
      <div className="sy-kvs">
        <div><span className="muted">mode</span><b className={`mono ${d.mode === 'PAPER' ? '' : 'pos'}`}>{d.mode ?? '—'}</b></div>
        <div><span className="muted">deployed today</span><b className="mono">{b.spent != null ? fmtUsd(b.spent, 2) : '—'}</b></div>
        <div><span className="muted">day budget</span><b className="mono">{b.day_cap != null ? fmtUsd(b.day_cap, 2) : '—'}</b></div>
        <div><span className="muted">bankroll</span><b className="mono">{b.bankroll != null ? fmtUsd(b.bankroll, 2) : '—'}</b></div>
        <div><span className="muted">loss halt at</span><b className="mono">{b.halt_at != null ? fmtUsd(b.halt_at, 2) : '—'}</b></div>
        <div><span className="muted">orphan sweep</span><b className="mono">{d.orphans ? `${d.orphans.orphans_cancelled}/${d.orphans.open} cancelled` : 'idle'}</b></div>
      </div>
    </div>
  )
}

const PERIODS = ['daily', 'weekly', 'monthly'] as const
const FORMATS = [
  { key: 'md', label: 'Report (MD)' },
  { key: 'csv', label: 'Ledger (CSV)' },
  { key: 'json', label: 'Raw (JSON)' },
] as const

function Reports() {
  const [copied, setCopied] = useState<string | null>(null)
  const copy = async (period: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/report?period=${period}&format=md`)
      await navigator.clipboard.writeText(await resp.text())
      setCopied(period)
      setTimeout(() => setCopied(null), 2000)
    } catch { /* clipboard denied: the download buttons remain */ }
  }
  return (
    <div>
      <div className="an-row-title">
        <span>REPORTS</span>
        <span className="muted">generated from the account at click time — never stale, never edited</span>
      </div>
      <div className="sy-reports">
        {PERIODS.map((p) => (
          <div key={p} className="sy-report-card">
            <div className="sy-report-title">{p.toUpperCase()}</div>
            <div className="sy-report-links">
              {FORMATS.map((f) => (
                <a key={f.key} className="tr-chipbtn"
                  href={`/api/report?period=${p}&format=${f.key}`}
                  target="_blank" rel="noreferrer">
                  {f.label}
                </a>
              ))}
              <button className={`tr-chipbtn ${copied === p ? 'on' : ''}`}
                onClick={() => copy(p)}>
                {copied === p ? '✓ Copied' : 'Copy for AI review'}
              </button>
            </div>
          </div>
        ))}
      </div>
      <div className="sy-note muted">
        "Copy for AI review" puts the markdown report on your clipboard — paste it to
        the AI cofounder and the numbers it reasons from are the account's own.
      </div>
    </div>
  )
}

export function System() {
  const { status, err } = useEngine()
  if (err) return <EmptyState>{`Engine telemetry unreachable: ${err}`}</EmptyState>
  if (!status) return (
    <div className="page tr-page">
      <div className="card tr-skel" style={{ height: 160 }} />
      <div className="card tr-skel" style={{ height: 280 }} />
    </div>
  )
  const d = status.detail || {}
  const beatAge = status.beat_at
    ? Math.round((Date.now() - new Date(status.beat_at).getTime()) / 1000)
    : null

  return (
    <div className="page tr-page">
      <div className="card">
        <div className="tr-ledger-head">
          <div className="card-title">ENGINE TELEMETRY</div>
          <div className={`tr-live ${beatAge !== null && beatAge < 120 ? '' : 'neg'}`}>
            <span className="tr-pulse" />
            {beatAge !== null ? `HEARTBEAT ${beatAge}s AGO` : 'NO HEARTBEAT'}
          </div>
        </div>
        <Funnel d={d} />
      </div>
      <div className="card"><Reports /></div>
      <div className="tr-columns">
        <div className="card"><Refusals d={d} /></div>
        <div className="card"><Budget d={d} /></div>
      </div>
      <div className="card"><DriftLab d={d} /></div>
      <div className="card"><Costs d={d} /></div>
      <div className="tr-foot muted">
        This page renders the engine's own heartbeat — the same numbers it steers
        itself by. Verdicts, refusal counts and measurements are produced by the
        trading process, not by this site.
      </div>
    </div>
  )
}
