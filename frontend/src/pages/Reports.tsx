import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  DESK_RELOCK_EVENT, deskAdminToken, deskApi, deskAuthed, deskToken,
  deskUnlock,
} from '../lib/desk'
import { API_BASE } from '../lib/api'

// Management reports (owner order 2026-08-28): every copy trade on the
// ledger assigned to its whale, the whale-fill → our-execution latency
// next to each one, and per-whale results pivoted by sport, trade type,
// and period (daily / ISO-weekly / monthly / all-time). Reads
// /api/admin/copy-reports + /api/admin/copy-ledger behind the desk
// gate; CSV downloads carry the same session headers.

type Period = 'daily' | 'weekly' | 'monthly' | 'all'
const PERIODS: Period[] = ['daily', 'weekly', 'monthly', 'all']

interface Cell {
  whale: string; sport: string; category: string; bucket: string
  n: number; wins: number; losses: number
  staked: number; pnl: number; roi: number | null
  lat_avg_s: number | null; lat_p50_s: number | null; lat_n: number
}
interface WhaleTotal {
  whale: string; n: number; wins: number; losses: number
  staked: number; pnl: number; roi: number | null
  lat_avg_s: number | null; lat_p50_s: number | null; lat_n: number
}
interface Report {
  period: Period; rows: Cell[]; by_whale: WhaleTotal[]
  latency: { n: number; avg_s: number | null; p50_s: number | null }
  generated_at: string
}
interface LedgerRow {
  id: number; whale: string; day: string | null; slug: string | null
  sport: string; category: string; side: string | null
  venue: string | null; status: string | null
  stake: number; pnl: number | null
  whale_ts: string | null; detected_at: string | null
  placed_at: string | null; settled_at: string | null
  latency_s: number | null; detect_lag_s: number | null
}

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${(v * 100).toFixed(1)}%`
const secs = (v: number | null | undefined) =>
  v == null ? '—' : v < 90 ? `${v.toFixed(1)}s` : `${(v / 60).toFixed(1)}m`
const cls = (v: number | null | undefined) =>
  (v ?? 0) > 0 ? 'pos' : (v ?? 0) < 0 ? 'neg' : ''

function venueChip(v: string | null) {
  if (!v) return null
  const k = v.includes('kalshi')
  return (
    <span className={`v9-chip ${k ? 'venue-k' : 'venue-pm'}`}>
      {k ? 'Kalshi' : 'Polymarket'}
    </span>
  )
}

export default function Reports() {
  const [authed, setAuthed] = useState(() => deskAuthed())
  const [pw, setPw] = useState('')
  const [gateErr, setGateErr] = useState('')
  const [unlocking, setUnlocking] = useState(false)

  const [period, setPeriod] = useState<Period>('monthly')
  const [whale, setWhale] = useState<string>('')
  const [rep, setRep] = useState<Report | null>(null)
  const [ledger, setLedger] = useState<LedgerRow[] | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const relock = () => setAuthed(false)
    window.addEventListener(DESK_RELOCK_EVENT, relock)
    return () => window.removeEventListener(DESK_RELOCK_EVENT, relock)
  }, [])

  const load = useCallback(async () => {
    if (!deskAuthed()) return
    setErr('')
    try {
      const q = whale ? `&whale=${encodeURIComponent(whale)}` : ''
      const [r, l] = await Promise.all([
        deskApi<Report>(`/api/admin/copy-reports?period=${period}${q}`),
        deskApi<{ rows: LedgerRow[] }>(
          `/api/admin/copy-ledger?limit=200${q}`),
      ])
      setRep(r)
      setLedger(l.rows)
    } catch (e) {
      setErr((e as { message?: string })?.message || 'load failed')
    }
  }, [period, whale])

  useEffect(() => { if (authed) void load() }, [authed, load])

  const unlock = async () => {
    const secret = pw.trim()
    if (!secret || unlocking) return
    setUnlocking(true)
    const r = await deskUnlock(secret)
    setUnlocking(false)
    if (r.ok) { setAuthed(true); setGateErr('') } else setGateErr(r.error || '')
  }

  // CSV downloads need the session headers, so a bare href can't work —
  // fetch with the same tokens and hand the bytes to the browser.
  const download = useCallback(async (kind: 'report' | 'ledger') => {
    const path = kind === 'report'
      ? `/api/admin/copy-reports?period=${period}&format=csv${whale ? `&whale=${encodeURIComponent(whale)}` : ''}`
      : `/api/admin/copy-ledger?format=csv${whale ? `&whale=${encodeURIComponent(whale)}` : ''}`
    const headers: Record<string, string> = {}
    const d = deskToken(); const a = deskAdminToken()
    if (d) headers['X-Desk-Token'] = d
    if (a) headers['X-Admin-Token'] = a
    try {
      const resp = await fetch(`${API_BASE}${path}`, { headers })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const el = document.createElement('a')
      el.href = url
      el.download = kind === 'report'
        ? `copy-report-${period}${whale ? `-${whale}` : ''}.csv`
        : `copy-ledger${whale ? `-${whale}` : ''}.csv`
      el.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setErr((e as { message?: string })?.message || 'download failed')
    }
  }, [period, whale])

  const rows = useMemo(() => rep?.rows ?? [], [rep])

  if (!authed) {
    return (
      <div className="card" style={{ maxWidth: 420, margin: '48px auto' }}>
        <h1>Reports</h1>
        <p className="sub">Management reports are a desk surface — unlock
          with the desk password.</p>
        <input className="input" type="password" placeholder="Desk password"
          value={pw} onChange={(e) => setPw(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && unlock()} autoFocus />
        {gateErr && <p className="sub" style={{ color: 'var(--critical)' }}>{gateErr}</p>}
        <button className="btn" onClick={unlock} disabled={unlocking}>
          {unlocking ? 'Unlocking…' : 'Unlock'}
        </button>
      </div>
    )
  }

  return (
    <>
      <h1>Management Reports</h1>
      <p className="sub">
        Every copy trade assigned to its whale, with the whale-fill →
        execution latency beside it. Results by sport, trade type, and
        period — served from the order-level audit ledger, uncapped.
        {rep && (
          <> <span className="v9-chip lat">
            copy latency p50 {secs(rep.latency.p50_s)} · n={rep.latency.n}
          </span></>
        )}
      </p>

      <div className="rpt-controls">
        <div className="rpt-seg" role="tablist" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p} role="tab" aria-selected={p === period}
              className={p === period ? 'active' : ''}
              onClick={() => setPeriod(p)}>
              {p === 'all' ? 'All-time' : p[0].toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
        <div className="rpt-dl">
          <a role="button" tabIndex={0} onClick={() => download('report')}
            onKeyDown={(e) => e.key === 'Enter' && download('report')}>
            ⬇ Report CSV
          </a>
          <a role="button" tabIndex={0} onClick={() => download('ledger')}
            onKeyDown={(e) => e.key === 'Enter' && download('ledger')}>
            ⬇ Ledger CSV
          </a>
        </div>
      </div>

      {err && <p className="sub" style={{ color: 'var(--critical)' }}>{err}</p>}

      <h2>By whale</h2>
      <div className="rpt-whale-cards">
        {(rep?.by_whale ?? []).map((w) => (
          <div key={w.whale}
            className={`rpt-wc${whale === w.whale ? ' sel' : ''}`}
            onClick={() => setWhale(whale === w.whale ? '' : w.whale)}>
            <div className="n">{w.whale}</div>
            <div className="line">
              <span>{w.wins}-{w.losses} · {w.n} settled</span>
              <span className={cls(w.pnl)}>{money(w.pnl)}</span>
            </div>
            <div className="line">
              <span>staked {money(w.staked)}</span>
              <span>ROI {pct(w.roi)}</span>
            </div>
            <div className="line">
              <span className="rpt-lat">
                lat p50 {secs(w.lat_p50_s)}{w.lat_n ? ` · n=${w.lat_n}` : ''}
              </span>
            </div>
          </div>
        ))}
        {!rep && Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="rpt-wc"><div className="skel" style={{ height: 64 }} /></div>
        ))}
      </div>

      <h2>{whale ? `${whale} — ` : ''}whale × sport × type
        {period !== 'all' ? ` × ${period}` : ''}</h2>
      <div className="card" style={{ overflowX: 'auto' }}>
        <table className="data rpt-pivot">
          <thead>
            <tr>
              <th>Whale</th>
              {period !== 'all' && <th>{period === 'weekly' ? 'Week of' : period === 'monthly' ? 'Month' : 'Day'}</th>}
              <th>Sport</th><th>Type</th>
              <th className="num">N</th><th className="num">W-L</th>
              <th className="num">Staked</th><th className="num">P&L</th>
              <th className="num">ROI</th>
              <th className="num">Lat avg</th><th className="num">Lat p50</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>{r.whale}</td>
                {period !== 'all' && <td>{r.bucket}</td>}
                <td>{r.sport}</td><td>{r.category}</td>
                <td className="num">{r.n}</td>
                <td className="num">{r.wins}-{r.losses}</td>
                <td className="num">{money(r.staked)}</td>
                <td className={`num ${cls(r.pnl)}`}>{money(r.pnl)}</td>
                <td className="num">{pct(r.roi)}</td>
                <td className="num rpt-lat">{secs(r.lat_avg_s)}</td>
                <td className="num rpt-lat">{secs(r.lat_p50_s)}</td>
              </tr>
            ))}
            {rep && rows.length === 0 && (
              <tr><td colSpan={11} style={{ color: 'var(--muted)' }}>
                No settled copy trades in this window.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h2>The ledger — every copy trade, whale-assigned, latency beside it</h2>
      <div className="card" style={{ overflowX: 'auto' }}>
        <table className="data rpt-pivot">
          <thead>
            <tr>
              <th>Day</th><th>Whale</th><th>Market</th><th>Sport</th>
              <th>Type</th><th>Venue</th>
              <th className="num">Stake</th><th className="num">P&L</th>
              <th className="num">Latency</th><th className="num">Detect lag</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {(ledger ?? []).map((r) => (
              <tr key={r.id}>
                <td style={{ whiteSpace: 'nowrap' }}>{r.day ?? '—'}</td>
                <td>{r.whale}</td>
                <td style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  title={r.slug ?? ''}>{r.slug ?? '—'}</td>
                <td>{r.sport}</td><td>{r.category}</td>
                <td>{venueChip(r.venue)}</td>
                <td className="num">{money(r.stake)}</td>
                <td className={`num ${cls(r.pnl)}`}>{money(r.pnl)}</td>
                <td className="num">
                  {r.latency_s != null
                    ? <span className="v9-chip lat">{secs(r.latency_s)}</span>
                    : '—'}
                </td>
                <td className="num rpt-lat">{secs(r.detect_lag_s)}</td>
                <td>{r.status ?? '—'}</td>
              </tr>
            ))}
            {ledger && ledger.length === 0 && (
              <tr><td colSpan={11} style={{ color: 'var(--muted)' }}>
                No rows yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
      {rep && (
        <p className="sub">Generated {new Date(rep.generated_at).toLocaleString()} ·
          latency = whale fill → our order fire (reaction), sweep-lane rows
          measured from detection.</p>
      )}
    </>
  )
}
