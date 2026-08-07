import { useCallback, useEffect, useState } from 'react'
import { adminApi } from '../lib/api'

// Manual trade desk (owner directive 2026-08-07): an admin directs
// trades executed by the live account as the 'manual' sleeve — its own
// budget and P&L line, zero interaction with the autonomous copies.

interface SearchHit {
  slug: string
  title: string
  event_title: string | null
  outcome: string
  asset: string
}

interface ManualTrade {
  id: number
  placed_at: string | null
  title: string
  outcome: string | null
  status: string
  limit_price: number | null
  fill_price: number | null
  requested_usd: number
  filled_usd: number
  filled_shares: number
  pnl: number | null
  settled_at: string | null
  error: string | null
}

interface Blotter {
  trades: ManualTrade[]
  day_spent: number
  day_budget: number
  max_per_order: number
}

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`

export function TradeDesk() {
  const [token, setToken] = useState(() => sessionStorage.getItem('sa_admin_token') || '')
  const [authed, setAuthed] = useState(false)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [searching, setSearching] = useState(false)
  const [pick, setPick] = useState<SearchHit | null>(null)
  const [usd, setUsd] = useState('50')
  const [placing, setPlacing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [blotter, setBlotter] = useState<Blotter | null>(null)

  const unlock = useCallback(async (tok: string) => {
    try {
      const ping = await adminApi<{ match: boolean }>(
        '/api/admin/ping', tok, { method: 'POST', body: '{}' },
      )
      if (!ping.match) { setAuthed(false); setErr('Wrong admin token.'); return }
      setAuthed(true)
      setErr('')
      sessionStorage.setItem('sa_admin_token', tok)
      adminApi<Blotter>('/api/admin/manual-trades', tok).then(setBlotter).catch(() => {})
    } catch {
      setAuthed(false)
      setErr('API unreachable — check the service status.')
    }
  }, [])

  useEffect(() => {
    if (token) unlock(token)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!authed) return
    const t = setInterval(
      () => adminApi<Blotter>('/api/admin/manual-trades', token).then(setBlotter).catch(() => {}),
      15000,
    )
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed])

  const search = async () => {
    if (q.trim().length < 2) return
    setSearching(true)
    setPick(null)
    setResult(null)
    try {
      const r = await adminApi<{ results: SearchHit[] }>(
        `/api/admin/market-search?q=${encodeURIComponent(q.trim())}`, token,
      )
      setHits(r.results)
      setErr(r.results.length ? '' : 'No live markets match that search.')
    } catch {
      setErr('Search failed — try again.')
    } finally {
      setSearching(false)
    }
  }

  const place = async () => {
    if (!pick || placing) return
    const amount = parseFloat(usd)
    if (!(amount > 0)) { setErr('Enter a dollar amount.'); return }
    setPlacing(true)
    setResult(null)
    try {
      const r = await adminApi<any>('/api/admin/manual-trade', token, {
        method: 'POST',
        body: JSON.stringify({ asset: pick.asset, usd: amount }),
      })
      setResult(r)
      adminApi<Blotter>('/api/admin/manual-trades', token).then(setBlotter).catch(() => {})
    } catch {
      setResult({ ok: false, error: 'Request failed — the API may be redeploying.' })
    } finally {
      setPlacing(false)
    }
  }

  if (!authed) {
    return (
      <>
        <h1>Trade Desk</h1>
        <div className="card" style={{ maxWidth: 420 }}>
          <p>Enter the admin token (env <code>ADMIN_TOKEN</code>).</p>
          <input
            className="input"
            type="password"
            value={token}
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setToken(e.target.value.trim())}
            onKeyDown={(e) => e.key === 'Enter' && unlock(token.trim())}
            aria-label="Admin token"
          />
          <button className="btn" onClick={() => unlock(token.trim())}>Unlock</button>
          {err && <p style={{ color: 'var(--red, #f66)' }}>{err}</p>}
        </div>
      </>
    )
  }

  return (
    <>
      <h1>Trade Desk</h1>
      <p style={{ opacity: 0.75 }}>
        Directed trades run as the <b>manual</b> sleeve — separate budget and P&amp;L,
        no effect on autonomous copying.
        {blotter && (
          <> Today: <b>{money(blotter.day_spent)}</b> of {money(blotter.day_budget)} budget ·
            max {money(blotter.max_per_order)}/ticket.</>
        )}
      </p>

      <div className="card">
        <h2>1 · Find the market</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder="e.g. yankees, dodgers total, wnba"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            aria-label="Market search"
          />
          <button className="btn" onClick={search} disabled={searching}>
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>
        {hits.length > 0 && (
          <table style={{ width: '100%', marginTop: 12 }}>
            <thead>
              <tr><th style={{ textAlign: 'left' }}>Market</th>
                <th style={{ textAlign: 'left' }}>Side</th><th /></tr>
            </thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.asset}
                    style={pick?.asset === h.asset ? { outline: '1px solid var(--accent, #6cf)' } : undefined}>
                  <td>{h.title}<div style={{ opacity: 0.6, fontSize: 12 }}>{h.slug}</div></td>
                  <td>{h.outcome}</td>
                  <td>
                    <button className="btn" onClick={() => { setPick(h); setResult(null) }}>
                      {pick?.asset === h.asset ? 'Selected' : 'Select'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>2 · Size and place</h2>
        {pick ? (
          <>
            <p><b>{pick.title}</b> — {pick.outcome}</p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span>$</span>
              <input
                className="input"
                style={{ width: 110 }}
                inputMode="decimal"
                value={usd}
                onChange={(e) => setUsd(e.target.value)}
                aria-label="Dollar amount"
              />
              <button className="btn" onClick={place} disabled={placing}>
                {placing ? 'Placing…' : `Buy ${pick.outcome} for $${usd || '0'}`}
              </button>
            </div>
            <p style={{ opacity: 0.6, fontSize: 13, marginTop: 8 }}>
              Fill-or-kill at the live ask +2¢ protection — fills at that price or not at all.
            </p>
          </>
        ) : <p style={{ opacity: 0.6 }}>Pick a market above.</p>}
        {result && (
          <p style={{ color: result.ok ? 'var(--green, #6f6)' : 'var(--red, #f66)' }}>
            {result.ok
              ? `FILLED: ${result.filled_shares} contracts @ ${result.fill_price} (${result.title} — ${result.outcome})`
              : `Not placed: ${result.error}`}
          </p>
        )}
        {err && !result && <p style={{ color: 'var(--red, #f66)' }}>{err}</p>}
      </div>

      <div className="card">
        <h2>Blotter</h2>
        {blotter && blotter.trades.length > 0 ? (
          <table style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left' }}>Placed</th>
                <th style={{ textAlign: 'left' }}>Market</th>
                <th style={{ textAlign: 'left' }}>Side</th>
                <th>Status</th><th>Cost</th><th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {blotter.trades.map((t) => (
                <tr key={t.id}>
                  <td>{t.placed_at ? new Date(t.placed_at).toLocaleString() : '—'}</td>
                  <td>{t.title}</td>
                  <td>{t.outcome || '—'}</td>
                  <td style={{ textAlign: 'center' }}>{t.status}{t.error ? ` (${t.error.slice(0, 60)})` : ''}</td>
                  <td style={{ textAlign: 'right' }}>{money(t.filled_usd || t.requested_usd)}</td>
                  <td style={{ textAlign: 'right', color: (t.pnl ?? 0) > 0 ? 'var(--green, #6f6)' : (t.pnl ?? 0) < 0 ? 'var(--red, #f66)' : undefined }}>
                    {money(t.pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p style={{ opacity: 0.6 }}>No manual trades yet.</p>}
      </div>
    </>
  )
}
