import { useCallback, useEffect, useState } from 'react'
import { adminApi } from '../lib/api'

// Manual trade desk (owner directive 2026-08-07): exchange-style market
// browser with a venue toggle — Polymarket cards with outcome odds,
// Kalshi event rows with YES prices in cents — and one-tap execution
// through the live account as the walled-off 'manual' sleeve.
// Polymarket fills synchronously; Kalshi relays through the engine
// (~10s), since only the engine holds Kalshi credentials.

type Venue = 'polymarket' | 'kalshi'

interface PmOutcome { outcome: string; asset: string; ask: number | null; bid: number | null }
interface PmMarket { slug: string; title: string; event_title: string | null; outcomes: PmOutcome[] }
interface KMarket {
  ticker: string; series: string; title: string; sub_title: string
  yes_ask: number | null; yes_bid: number | null
  no_ask: number | null; no_bid: number | null
  close_time: string | null
}

interface Pick {
  venue: Venue
  label: string        // market title
  side: string         // outcome / YES side
  ask: number
  asset?: string       // polymarket token
  ticker?: string      // kalshi ticker
}

interface ManualTrade {
  id: number | string
  placed_at: string | null
  title: string
  outcome: string | null
  status: string
  fill_price: number | null
  requested_usd: number
  filled_usd: number
  filled_shares: number
  pnl: number | null
  venue?: string
  error: string | null
}

interface Blotter {
  trades: ManualTrade[]
  day_spent: number
  day_budget: number
  max_per_order: number
}

const cents = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}¢`)
const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`

const SERIES_LABEL: Record<string, string> = {
  KXMLBGAME: 'MLB', KXWNBAGAME: 'WNBA', KXNBAGAME: 'NBA', KXNFLGAME: 'NFL',
  KXNHLGAME: 'NHL', KXATPMATCH: 'ATP', KXWTAMATCH: 'WTA',
}

export function TradeDesk() {
  const [token, setToken] = useState(() => sessionStorage.getItem('sa_admin_token') || '')
  const [authed, setAuthed] = useState(false)
  const [err, setErr] = useState('')
  const [venue, setVenue] = useState<Venue>('polymarket')
  const [q, setQ] = useState('')
  const [pmMarkets, setPmMarkets] = useState<PmMarket[]>([])
  const [kMarkets, setKMarkets] = useState<KMarket[]>([])
  const [loading, setLoading] = useState(false)
  const [pick, setPick] = useState<Pick | null>(null)
  const [usd, setUsd] = useState('50')
  const [placing, setPlacing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [blotter, setBlotter] = useState<Blotter | null>(null)
  const [depth, setDepth] = useState<{ bids: number[][]; asks: number[][] } | null>(null)

  // Venue accent: generic exchange colors so each tab reads like its
  // venue's kind of screen (green Kalshi, blue Polymarket) without
  // wearing either company's brand.
  const accent = venue === 'kalshi' ? '#1dc98b' : '#3b82f6'

  const loadBlotter = useCallback((tok: string) => {
    adminApi<Blotter>('/api/admin/manual-trades', tok).then(setBlotter).catch(() => {})
  }, [])

  const unlock = useCallback(async (tok: string) => {
    try {
      const ping = await adminApi<{ match: boolean }>(
        '/api/admin/ping', tok, { method: 'POST', body: '{}' },
      )
      if (!ping.match) { setAuthed(false); setErr('Wrong admin token.'); return }
      setAuthed(true)
      setErr('')
      sessionStorage.setItem('sa_admin_token', tok)
      loadBlotter(tok)
    } catch {
      setAuthed(false)
      setErr('API unreachable — check the service status.')
    }
  }, [loadBlotter])

  useEffect(() => {
    if (token) unlock(token)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!authed) return
    const t = setInterval(() => loadBlotter(token), 12000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed])

  const browse = async (v: Venue = venue, query: string = q) => {
    setLoading(true)
    setResult(null)
    try {
      if (v === 'polymarket') {
        if (query.trim().length < 2) { setPmMarkets([]); setErr('Type at least 2 characters to search Polymarket.'); return }
        const r = await adminApi<{ markets: PmMarket[] }>(
          `/api/admin/market-search?q=${encodeURIComponent(query.trim())}`, token,
        )
        setPmMarkets(r.markets)
        setErr(r.markets.length ? '' : 'No live markets match.')
      } else {
        const r = await adminApi<{ markets: KMarket[] }>(
          `/api/admin/kalshi-markets?q=${encodeURIComponent(query.trim())}`, token,
        )
        setKMarkets(r.markets)
        setErr(r.markets.length ? '' : 'No open Kalshi markets match.')
      }
    } catch {
      setErr('Market load failed — try again.')
    } finally {
      setLoading(false)
    }
  }

  const switchVenue = (v: Venue) => {
    setVenue(v)
    setPick(null)
    setResult(null)
    setErr('')
    if (v === 'kalshi') browse(v, q)
  }

  useEffect(() => {
    // Live liquidity for the picked side — the venue's actual book.
    setDepth(null)
    if (!pick) return
    const id = pick.venue === 'polymarket' ? pick.asset : pick.ticker
    if (!id) return
    let dead = false
    const load = () =>
      adminApi<{ bids: number[][]; asks: number[][] }>(
        `/api/admin/book?venue=${pick.venue}&id=${encodeURIComponent(id)}`, token,
      ).then((d) => { if (!dead) setDepth(d) }).catch(() => {})
    load()
    const t = setInterval(load, 6000)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick])

  const place = async () => {
    if (!pick || placing) return
    const amount = parseFloat(usd)
    if (!(amount > 0)) { setErr('Enter a dollar amount.'); return }
    setPlacing(true)
    setResult(null)
    try {
      const body = pick.venue === 'polymarket'
        ? { venue: 'polymarket-us', asset: pick.asset, usd: amount }
        : { venue: 'kalshi', ticker: pick.ticker, side: pick.side.toLowerCase(), title: `${pick.label} — ${pick.side}`, usd: amount }
      const r = await adminApi<any>('/api/admin/manual-trade', token, {
        method: 'POST', body: JSON.stringify(body),
        // Placement resolves the market on the venue and previews the
        // order before committing — routinely 20-40s. The default 20s
        // API timeout aborted mid-placement (owner report 2026-08-07).
        signal: AbortSignal.timeout(90000),
      })
      setResult(r)
      loadBlotter(token)
    } catch (e: any) {
      setResult({
        ok: false,
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check the blotter before retrying.'
          : `Request failed (${e?.message || 'network'}) — check the blotter before retrying.`,
      })
      loadBlotter(token)
    } finally {
      setPlacing(false)
    }
  }

  const estLimit = pick ? Math.min(pick.ask + 0.02, 0.99) : 0
  const estContracts = pick && parseFloat(usd) > 0 ? Math.floor(parseFloat(usd) / estLimit) : 0
  const estCost = estContracts * estLimit
  const estPayout = estContracts * 1

  if (!authed) {
    return (
      <>
        <h1>Trade Desk</h1>
        <div className="card" style={{ maxWidth: 420 }}>
          <p>Enter the admin token (env <code>ADMIN_TOKEN</code>).</p>
          <input
            className="input" type="password" value={token}
            autoCapitalize="none" autoCorrect="off" autoComplete="off" spellCheck={false}
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

  const priceBtn = (label: string, price: number | null, selected: boolean, onPick: () => void) => (
    <button
      className="btn"
      disabled={price == null}
      onClick={onPick}
      style={{
        minWidth: 130, display: 'flex', justifyContent: 'space-between', gap: 10,
        borderLeft: `3px solid ${accent}`,
        ...(selected ? { outline: `2px solid ${accent}` } : {}),
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      <b style={{ color: accent }}>{cents(price)}</b>
    </button>
  )

  return (
    <>
      <h1>Trade Desk</h1>
      <p style={{ opacity: 0.75 }}>
        Directed trades run as the <b>manual</b> sleeve — separate budget and P&amp;L,
        zero effect on autonomous trading.
        {blotter && (
          <> Today: <b>{money(blotter.day_spent)}</b> of {money(blotter.day_budget)} ·
            max {money(blotter.max_per_order)}/ticket.</>
        )}
      </p>

      <div className="card">
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          {(['polymarket', 'kalshi'] as Venue[]).map((v) => (
            <button
              key={v}
              className="btn"
              onClick={() => switchVenue(v)}
              style={venue === v ? { outline: '2px solid var(--accent, #6cf)', fontWeight: 700 } : {}}
            >
              {v === 'polymarket' ? 'Polymarket' : 'Kalshi'}
            </button>
          ))}
          <input
            className="input" style={{ flex: 1 }}
            placeholder={venue === 'polymarket' ? 'Search markets — e.g. yankees, wnba total' : 'Filter Kalshi — e.g. yankees (empty = all open)'}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && browse()}
            aria-label="Market search"
          />
          <button className="btn" onClick={() => browse()} disabled={loading}>
            {loading ? 'Loading…' : venue === 'polymarket' ? 'Search' : 'Browse'}
          </button>
        </div>

        {venue === 'polymarket' && pmMarkets.map((m) => (
          <div key={m.slug} style={{ padding: '10px 0', borderTop: '1px solid rgba(128,128,128,.2)' }}>
            <div style={{ marginBottom: 6 }}>
              <b>{m.title}</b>
              {m.event_title && m.event_title !== m.title && (
                <span style={{ opacity: 0.6 }}> · {m.event_title}</span>
              )}
              <div style={{ opacity: 0.5, fontSize: 12 }}>{m.slug}</div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {m.outcomes.map((o) =>
                priceBtn(o.outcome, o.ask, pick?.asset === o.asset, () => {
                  if (o.ask != null) {
                    setPick({ venue: 'polymarket', label: m.title, side: o.outcome, ask: o.ask, asset: o.asset })
                    setResult(null)
                  }
                }))}
            </div>
          </div>
        ))}

        {venue === 'kalshi' && kMarkets.map((m) => (
          <div key={m.ticker} style={{ padding: '10px 0', borderTop: '1px solid rgba(128,128,128,.2)', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <span style={{ opacity: 0.6, fontSize: 12, marginRight: 8 }}>
                {SERIES_LABEL[m.series] || m.series}
              </span>
              <b>{m.title}</b>
              {m.sub_title && <span style={{ opacity: 0.6 }}> · {m.sub_title}</span>}
              <div style={{ opacity: 0.5, fontSize: 12 }}>{m.ticker}</div>
            </div>
            {priceBtn('YES', m.yes_ask, pick?.ticker === m.ticker && pick?.side === 'YES', () => {
              if (m.yes_ask != null) {
                setPick({ venue: 'kalshi', label: m.title, side: 'YES', ask: m.yes_ask, ticker: m.ticker })
                setResult(null)
              }
            })}
            {priceBtn('NO', m.no_ask, pick?.ticker === m.ticker && pick?.side === 'NO', () => {
              if (m.no_ask != null) {
                setPick({ venue: 'kalshi', label: m.title, side: 'NO', ask: m.no_ask, ticker: m.ticker })
                setResult(null)
              }
            })}
          </div>
        ))}
        {err && <p style={{ color: 'var(--red, #f66)' }}>{err}</p>}
      </div>

      <div className="card">
        <h2>Ticket</h2>
        {pick ? (
          <>
            <p>
              <b>{pick.label}</b> — {pick.side} @ <b>{cents(pick.ask)}</b>
              <span style={{ opacity: 0.6 }}> ({pick.venue})</span>
            </p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span>$</span>
              <input
                className="input" style={{ width: 100 }} inputMode="decimal"
                value={usd} onChange={(e) => setUsd(e.target.value)}
                aria-label="Dollar amount"
              />
              {[10, 25, 50, 100].map((v) => (
                <button key={v} className="btn" onClick={() => setUsd(String(v))}>${v}</button>
              ))}
            </div>
            {estContracts > 0 && (
              <p style={{ opacity: 0.8, marginTop: 8 }}>
                ≈ <b>{estContracts}</b> contracts · cost ≈ {money(estCost)} ·
                pays <b>{money(estPayout)}</b> if it hits
                (profit {money(estPayout - estCost)})
              </p>
            )}
            {depth && (depth.asks.length > 0 || depth.bids.length > 0) && (
              <div style={{ display: 'flex', gap: 24, margin: '10px 0', fontSize: 13 }}>
                <div>
                  <div style={{ opacity: 0.6 }}>Asks (you buy)</div>
                  {depth.asks.slice(0, 3).map(([p, s], i) => (
                    <div key={i} style={{ display: 'flex', gap: 12, justifyContent: 'space-between', minWidth: 130 }}>
                      <span style={{ color: 'var(--red, #f66)' }}>{cents(p)}</span>
                      <span style={{ opacity: 0.7 }}>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{ opacity: 0.6 }}>Bids</div>
                  {depth.bids.slice(0, 3).map(([p, s], i) => (
                    <div key={i} style={{ display: 'flex', gap: 12, justifyContent: 'space-between', minWidth: 130 }}>
                      <span style={{ color: 'var(--green, #6f6)' }}>{cents(p)}</span>
                      <span style={{ opacity: 0.7 }}>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <button
              className="btn" onClick={place} disabled={placing}
              style={{ marginTop: 6, background: accent, color: '#08131a', fontWeight: 700 }}
            >
              {placing ? 'Placing…' : `Buy ${pick.side} for $${usd || '0'}`}
            </button>
            <p style={{ opacity: 0.6, fontSize: 13, marginTop: 8 }}>
              Fill-or-kill at the live ask +2¢ protection.
              {pick.venue === 'kalshi' && ' Kalshi orders place via the engine within ~10 seconds.'}
            </p>
          </>
        ) : <p style={{ opacity: 0.6 }}>Pick a side above.</p>}
        {result && (
          <p style={{ color: result.ok ? 'var(--green, #6f6)' : 'var(--red, #f66)' }}>
            {result.ok
              ? result.queued
                ? `QUEUED: ${result.count} contracts @ ≤${cents(result.limit_price)} — ${result.detail}`
                : `FILLED: ${result.filled_shares} contracts @ ${cents(result.fill_price)} (${result.title} — ${result.outcome})`
              : `Not placed: ${result.error}`}
          </p>
        )}
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
                <th>Venue</th><th>Status</th><th>Cost</th><th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {blotter.trades.map((t) => (
                <tr key={t.id}>
                  <td>{t.placed_at ? new Date(t.placed_at).toLocaleString() : '—'}</td>
                  <td>{t.title}</td>
                  <td>{t.outcome || '—'}</td>
                  <td style={{ textAlign: 'center' }}>{t.venue || 'polymarket'}</td>
                  <td style={{ textAlign: 'center' }}>{t.status}{t.error ? ` (${t.error.slice(0, 50)})` : ''}</td>
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
