import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import {
  DESK_RELOCK_EVENT, deskApi, deskAuthed, deskUnlock,
  type DeskAccounts, type KPosition, type PMPosition,
} from '../lib/desk'
import '../styles/desk2.css'
import '../styles/accounts9.css'

// Accounts (owner order 2026-08-22): the team's two-venue command
// center — live balances, open positions with marks and unrealized,
// and one-tap cash-out that deep-links into the desk ticket. Reads
// GET /api/desk/accounts behind the shared desk password; the page
// itself never knows the password or holds venue credentials.

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
const cents = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}¢`)
const pnlCls = (v: number | null | undefined) => ((v ?? 0) > 0 ? 'pos' : (v ?? 0) < 0 ? 'neg' : '')

// recent_trades shape is venue-snapshot-defined; read the common keys
// defensively so a payload tweak degrades to "—", never to a crash.
const trStr = (t: Record<string, unknown>, keys: string[]): string => {
  for (const k of keys) {
    const v = t[k]
    if (typeof v === 'string' && v) return v
    if (typeof v === 'number') return String(v)
  }
  return '—'
}
const trNum = (t: Record<string, unknown>, keys: string[]): number | null => {
  for (const k of keys) {
    const v = t[k]
    if (typeof v === 'number' && Number.isFinite(v)) return v
  }
  return null
}

// as_of arrives as epoch seconds (or an ISO string) — normalize to ms.
const asOfMs = (v: string | number): number | null => {
  if (typeof v === 'number') {
    return Number.isFinite(v) ? (v < 1e12 ? v * 1000 : v) : null
  }
  const p = Date.parse(v)
  return Number.isFinite(p) ? p : null
}

// Per-whale open copy exposure from the PUBLIC record (no auth).
type RideWhale = { whale: string; count: number; stake: number }
type RidePayload = { open?: { by_whale?: RideWhale[] } }

/** v9 capital ring: an inline SVG donut over totals.value. Gold =
 * committed_usd when the payload provides a committed split. When it
 * does not, the ring falls back to the VENUE split (Polymarket vs
 * Kalshi) — both parts computed from the payload's own venue figures,
 * drawn only when they reconcile to the total (v9 review: a
 * one-segment gray ring encoded nothing and repeated the number). */
function CapitalRing({ value, committed, kalshiPart }: {
  value: number | null | undefined
  committed: number | null | undefined
  kalshiPart: number | null | undefined
}) {
  const R = 48
  const C = 2 * Math.PI * R
  const split = value != null && value > 0 && committed != null && committed > 0
  // Venue fallback: only when both parts are real and sum inside the
  // total (never invented — a mismatch renders the neutral ring).
  const vsplit = !split && value != null && value > 0
    && kalshiPart != null && kalshiPart > 0 && kalshiPart <= value * 1.001
  const pmPart = vsplit ? Math.max(0, Math.round((value! - kalshiPart!) * 100) / 100) : null
  // Drawing clamp only — the legend always shows the raw dollars.
  const frac = split ? Math.min(1, Math.max(0, committed! / value!))
    : vsplit ? Math.min(1, Math.max(0, kalshiPart! / value!)) : 0
  const rest = split ? Math.round((value! - committed!) * 100) / 100 : null
  const label = split
    ? `Total value ${money(value)}: committed capital ${money(committed)}, account ${money(rest)}`
    : vsplit
      ? `Total value ${money(value)}: Kalshi ${money(kalshiPart)}, Polymarket ${money(pmPart)}`
      : `Total value ${money(value)}`
  return (
    <>
      <div className="ac9-ring" role="img" aria-label={label}>
        <svg width="120" height="120" viewBox="0 0 120 120" aria-hidden="true">
          <circle
            className={`ac9-arc-base${split || vsplit ? ' live' : ''}`}
            cx="60" cy="60" r={R} fill="none" strokeWidth="13"
          />
          {(split || vsplit) && (
            <circle
              className={split ? 'ac9-arc-gold' : 'ac9-arc-teal'}
              cx="60" cy="60" r={R} fill="none"
              strokeWidth="13" strokeDasharray={`${frac * C} ${C}`}
              transform="rotate(-90 60 60)"
            />
          )}
        </svg>
        <div className="ac9-ring-c">
          <div className="ac9-ring-v v9-money mono">{money(value)}</div>
          <div className="ac9-ring-l">total value</div>
        </div>
      </div>
      <div className="ac9-legend">
        {split ? (
          <>
            <div>
              <i className="ac9-sw gold" /> Committed capital
              <b className="mono">{money(committed)}</b>
            </div>
            <div>
              <i className="ac9-sw cyan" /> Account
              <b className="mono">{money(rest)}</b>
            </div>
          </>
        ) : vsplit ? (
          <>
            <div>
              <i className="ac9-sw teal" /> Kalshi
              <b className="mono">{money(kalshiPart)}</b>
            </div>
            <div>
              <i className="ac9-sw cyan" /> Polymarket
              <b className="mono">{money(pmPart)}</b>
            </div>
          </>
        ) : (
          <div>
            <i className="ac9-sw neutral" /> Account
            <b className="mono">{money(value)}</b>
          </div>
        )}
      </div>
    </>
  )
}

export function Accounts() {
  const [authed, setAuthed] = useState(() => deskAuthed())
  const [pw, setPw] = useState('')
  const [gateErr, setGateErr] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [data, setData] = useState<DeskAccounts | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [pmTab, setPmTab] = useState<'positions' | 'trades'>('positions')
  const [ride, setRide] = useState<RideWhale[]>([])
  const [chartBusy, setChartBusy] = useState<string | null>(null)
  const [chartNote, setChartNote] = useState<{ kind: 'ok' | 'miss'; text: string } | null>(null)
  const noteTimer = useRef<number | null>(null)
  // Freshness clock: re-render every 15s so the live pulse-dot honestly
  // decays once the snapshot crosses 60s of age.
  const [now, setNow] = useState(() => Date.now())
  const navigate = useNavigate()

  // Compact sticky summary: once the combined tiles scroll out under
  // the 52px nav, a condensed totals bar takes over. Display-only.
  const headRef = useRef<HTMLDivElement | null>(null)
  const [stuck, setStuck] = useState(false)
  const hasData = data != null
  useEffect(() => {
    const el = headRef.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      ([e]) => setStuck(!e.isIntersecting),
      { rootMargin: '-56px 0px 0px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [hasData])

  useEffect(() => {
    const relock = () => { setAuthed(false); setData(null) }
    window.addEventListener(DESK_RELOCK_EVENT, relock)
    return () => window.removeEventListener(DESK_RELOCK_EVENT, relock)
  }, [])

  const load = useCallback(() => {
    deskApi<DeskAccounts>('/api/desk/accounts')
      .then((d) => { setData(d); setLoadErr('') })
      .catch(() => setLoadErr('Accounts feed unreachable — retrying.'))
  }, [])

  useEffect(() => {
    if (!authed) return
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [authed, load])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000)
    return () => clearInterval(t)
  }, [])

  // "Riding on": per-whale open copy exposure from the public record.
  useEffect(() => {
    if (!authed) return
    let dead = false
    const go = () => api<RidePayload>('/api/copies-record')
      .then((d) => { if (!dead) setRide(d.open?.by_whale ?? []) })
      .catch(() => { /* card simply stays hidden */ })
    go()
    const t = setInterval(go, 60000)
    return () => { dead = true; clearInterval(t) }
  }, [authed])

  useEffect(() => () => {
    if (noteTimer.current) window.clearTimeout(noteTimer.current)
  }, [])

  const unlock = async () => {
    const secret = pw.trim()
    if (!secret || unlocking) return
    setUnlocking(true)
    const r = await deskUnlock(secret)
    setUnlocking(false)
    if (r.ok) { setAuthed(true); setGateErr(''); setPw('') }
    else setGateErr(r.error || 'Wrong password.')
  }

  const cashOutPm = (p: PMPosition) =>
    navigate(`/desk?co_venue=polymarket&co_slug=${encodeURIComponent(p.market_slug)}&co_outcome=${encodeURIComponent(p.outcome || '')}`)
  const cashOutK = (p: KPosition) =>
    navigate(`/desk?co_venue=kalshi&co_ticker=${encodeURIComponent(p.ticker)}`)

  // Chart a held PM position: resolve the venue slug to the global CLOB
  // token the desk charts key on. TradeDesk deep-links only the co_*
  // cash-out params (no market query param), so the token rides over in
  // sessionStorage ('sa_desk_prefill') for the desk's search to pick up.
  const chartPm = async (p: PMPosition) => {
    if (chartBusy != null) return
    setChartBusy(p.market_slug)
    if (noteTimer.current) window.clearTimeout(noteTimer.current)
    setChartNote(null)
    try {
      const r = await deskApi<{ asset: string | null; found: boolean }>(
        `/api/admin/slug-token?slug=${encodeURIComponent(p.market_slug)}`)
      if (r.found && r.asset) {
        try { sessionStorage.setItem('sa_desk_prefill', r.asset) } catch { /* best-effort */ }
        setChartNote({ kind: 'ok', text: 'market copied to desk search' })
        noteTimer.current = window.setTimeout(() => navigate('/desk'), 900)
        return
      }
      setChartNote({ kind: 'miss', text: 'no chartable token' })
    } catch {
      setChartNote({ kind: 'miss', text: 'token lookup failed' })
    } finally {
      setChartBusy(null)
    }
    noteTimer.current = window.setTimeout(() => setChartNote(null), 2500)
  }

  if (!authed) {
    return (
      <>
        <h1>Accounts</h1>
        <div className="card dk-gate">
          <p>Team access — enter the desk password.</p>
          <div className="dk-gate-row">
            <input
              className="input" type="password" value={pw}
              autoCapitalize="none" autoCorrect="off" autoComplete="current-password" spellCheck={false}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && unlock()}
              aria-label="Desk password"
            />
            <button className="btn" onClick={unlock} disabled={unlocking}>
              {unlocking ? 'Unlocking…' : 'Unlock'}
            </button>
          </div>
          {gateErr && <p className="dk-gate-err">{gateErr}</p>}
        </div>
      </>
    )
  }

  const pm = data?.polymarket
  const k = data?.kalshi
  const trades = (pm?.recent_trades || []) as Record<string, unknown>[]

  return (
    <>
      <h1>Accounts</h1>
      <p style={{ opacity: 0.75, marginTop: 0 }}>
        Both venue accounts, read live — balances, open exposure and marks
        exactly as the venues report them. Cash out routes through the desk ticket.
      </p>

      {data && stuck && (
        <div className="ac-sticky" role="status">
          <span>Total <b>{money(data.totals.value)}</b></span>
          <span>{data.totals.cash != null ? 'Cash' : 'Capital'}{' '}
            <b>{money(data.totals.cash ?? data.totals.trading_capital)}</b></span>
          <span>Unrl <b className={pnlCls(data.totals.unrealized)}>
            {signed(data.totals.unrealized)}
          </b></span>
        </div>
      )}

      {data && (
        <div className="ac9-hero" ref={headRef}>
          <div className="ac9-ringcard">
            <CapitalRing
              value={data.totals.value}
              committed={data.totals.committed_usd}
              kalshiPart={data.kalshi?.balance_usd != null
                ? Math.round(((data.kalshi.balance_usd || 0)
                    + data.kalshi.positions.reduce((s, p) =>
                        s + (p.value_usd ?? p.cost_usd ?? 0), 0)) * 100) / 100
                : null}
            />
          </div>
          <div className="ac9-tiles">
            <div className="ac-tile">
              <div className="ac-tile-l">Trading capital</div>
              <div className="ac-tile-v mono">
                {money(data.totals.trading_capital ?? data.totals.cash)}
              </div>
              {(data.totals.committed_usd ?? 0) > 0 && (
                <div className="ac-tile-note">incl. committed capital</div>
              )}
            </div>
            <div className="ac-tile">
              <div className="ac-tile-l">Unrealized P&L</div>
              <div className={`ac-tile-v mono ${pnlCls(data.totals.unrealized)}`}>
                {signed(data.totals.unrealized)}
              </div>
            </div>
          </div>
        </div>
      )}
      {data && (() => {
        const asOf = asOfMs(data.as_of)
        const fresh = asOf != null && now - asOf < 60_000
        return (
          <p className="ac-asof ac9-asof">
            {fresh && <span className="pulse-dot" aria-hidden="true" />}
            <span>
              as of {asOf != null ? new Date(asOf).toLocaleTimeString() : '—'}
              {' '}· refreshes every 30s
            </span>
          </p>
        )
      })()}
      {loadErr && data && (
        <div className="ac-degraded">{loadErr} Showing the last good snapshot.</div>
      )}
      {loadErr && !data && <div className="card"><p className="ac-empty">{loadErr}</p></div>}
      {!data && !loadErr && (
        <div className="ac-skel" aria-label="Loading accounts">
          <div className="skel" style={{ height: 152 }} />
          <div className="skel" style={{ height: 64 }} />
          <div className="skel" style={{ height: 240 }} />
        </div>
      )}

      {data && ride.length > 0 && (() => {
        const maxStake = Math.max(...ride.map((r) => r.stake || 0))
        return (
          <div className="ac9-ride">
            <div className="ac9-ride-h">
              Riding on <small>open copy exposure by whale</small>
            </div>
            {ride.map((r) => (
              <div className="ac9-ride-row" key={r.whale}>
                <span className="ac9-ride-name">{r.whale}</span>
                <div className="ac9-ride-bar" aria-hidden="true">
                  <i style={{ width: `${maxStake > 0 ? (r.stake / maxStake) * 100 : 0}%` }} />
                </div>
                <span className="ac9-ride-num mono">
                  <b>{money(r.stake)}</b> · {r.count} open
                </span>
              </div>
            ))}
          </div>
        )
      })()}

      {data && (
        <div className="ac-venues">
          {/* ── Polymarket ─────────────────────────────────────────── */}
          <div className="ac-card">
            <div className="ac-vhead">
              <span className="ac-vdot pm" />
              <span className="ac-vname">Polymarket</span>
              <span className={`ac-vtag${pm?.configured ? '' : ' off'}`}>
                {pm?.configured ? 'connected' : 'not configured'}
              </span>
            </div>
            <div className="ac-hero">
              <div className="ac-hero-num mono">{money(pm?.trading_capital ?? pm?.cash)}</div>
              <div className="ac-hero-l">
                TRADING CAPITAL
                {(pm?.committed_usd ?? 0) > 0 && (
                  <span className="ac-hero-note">incl. committed capital</span>
                )}
              </div>
            </div>
            <div className="ac-stats">
              <div className="ac-stat"><span>Open value</span><b>{money(pm?.open_value)}</b></div>
              <div className="ac-stat"><span>Unsettled</span><b>{money(pm?.unsettled_funds)}</b></div>
              <div className="ac-stat">
                <span>Realized P&L</span>
                <b className={pnlCls(pm?.realized_pnl)}>{signed(pm?.realized_pnl)}</b>
              </div>
            </div>
            {pm?.cash != null && (
              <details className="ac-details">
                <summary>details</summary>
                <div className="ac-stats">
                  <div className="ac-stat"><span>Live cash</span><b>{money(pm?.cash)}</b></div>
                  <div className="ac-stat"><span>Committed</span><b>{money(pm?.committed_usd)}</b></div>
                  <div className="ac-stat"><span>Buying power</span><b>{money(pm?.buying_power)}</b></div>
                  <div className="ac-stat"><span>Account value</span><b>{money(pm?.account_value)}</b></div>
                </div>
              </details>
            )}
            {(pm?.external_count ?? 0) > 0 && (
              <details className="ac-details">
                <summary>
                  external positions · {pm?.external_count} (placed on the
                  venue app, not by the platform — excluded from all
                  platform views and P&L)
                </summary>
                <div className="ac-postable">
                  {(pm?.external_positions ?? []).map((p, i) => (
                    <div className="ac-porow" key={i}>
                      <div className="ac-poname">
                        <b>{p.title ?? p.market_slug}</b>
                        <span>{p.outcome}</span>
                      </div>
                      <div className="ac-ponums mono">
                        Qty {p.qty} · Cost {money(p.cost)} · Value {money(p.value)}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}
            <div className="ac-tabs">
              <button className={pmTab === 'positions' ? 'on' : ''} onClick={() => setPmTab('positions')}>
                Positions {pm?.positions?.length ? `(${pm.positions.length})` : ''}
              </button>
              <button className={pmTab === 'trades' ? 'on' : ''} onClick={() => setPmTab('trades')}>
                Recent trades
              </button>
            </div>
            {chartNote && pmTab === 'positions' && (
              <div className={`ac9-note${chartNote.kind === 'miss' ? ' mut' : ''}`} role="status">
                {chartNote.text}
              </div>
            )}
            {pmTab === 'positions' ? (
              (pm?.positions?.length ?? 0) > 0 ? (
                <div className="ac-table">
                  <div className="ac-thead ac-cols-pm">
                    <span>Market</span><span className="num">Qty</span><span className="num">Cost</span>
                    <span className="num">Value</span><span className="num">Unrl</span><span className="num">Ret</span><span />
                  </div>
                  {pm!.positions.map((p) => {
                    const ret = p.unrealized != null && p.cost ? p.unrealized / p.cost : null
                    return (
                      <div className="ac-row ac-cols-pm" key={`${p.market_slug}-${p.outcome}`}>
                        <span className="ac-cell ac-main">
                          {p.title || p.market_slug}
                          <span className="ac-sub">{p.outcome}</span>
                        </span>
                        <span className="ac-cell num mono" data-l="Qty">{Math.round(p.qty)}</span>
                        <span className="ac-cell num mono" data-l="Cost">{money(p.cost)}</span>
                        <span className="ac-cell num mono" data-l="Value">{money(p.value)}</span>
                        <span className={`ac-cell num mono ${pnlCls(p.unrealized)}`} data-l="Unrealized">
                          {signed(p.unrealized)}
                        </span>
                        <span className={`ac-cell num mono ${pnlCls(ret)}`} data-l="Return">
                          {ret == null ? '—' : `${ret > 0 ? '+' : ''}${(ret * 100).toFixed(1)}%`}
                        </span>
                        <span className="ac-cell ac-act ac9-act">
                          <button className="ac-cashout" onClick={() => cashOutPm(p)}>Cash out</button>
                          <button
                            className="ac9-chart"
                            onClick={() => chartPm(p)}
                            disabled={chartBusy != null}
                          >
                            {chartBusy === p.market_slug ? '…' : 'Chart'}
                          </button>
                        </span>
                      </div>
                    )
                  })}
                </div>
              ) : <p className="ac-empty">No open Polymarket positions.</p>
            ) : (
              trades.length > 0 ? (
                <div className="ac-table">
                  <div className="ac-thead ac-cols-tr">
                    <span>When</span><span>Market</span><span>Side</span>
                    <span className="num">Price</span><span className="num">Size</span>
                  </div>
                  {trades.slice(0, 30).map((t, i) => (
                    <div className="ac-row ac-cols-tr" key={i}>
                      <span className="ac-cell" data-l="When">{trStr(t, ['time', 'at', 'ts', 'date'])}</span>
                      <span className="ac-cell ac-main">{trStr(t, ['title', 'market', 'slug', 'market_slug'])}</span>
                      <span className="ac-cell" data-l="Side">{trStr(t, ['side', 'outcome', 'action'])}</span>
                      <span className="ac-cell num mono" data-l="Price">{cents(trNum(t, ['price', 'avg_price', 'fill_price']))}</span>
                      <span className="ac-cell num mono" data-l="Size">
                        {trNum(t, ['usd', 'value', 'amount']) != null
                          ? money(trNum(t, ['usd', 'value', 'amount']))
                          : trNum(t, ['size', 'shares', 'qty']) != null
                            ? String(Math.round(trNum(t, ['size', 'shares', 'qty'])!)) : '—'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : <p className="ac-empty">No recent trades reported.</p>
            )}
          </div>

          {/* ── Kalshi ─────────────────────────────────────────────── */}
          <div className="ac-card">
            <div className="ac-vhead">
              <span className="ac-vdot k" />
              <span className="ac-vname">Kalshi</span>
              <span className={`ac-vtag${k?.configured ? '' : ' off'}`}>
                {k?.configured ? 'connected' : 'not configured'}
              </span>
            </div>
            {k?.degraded && (
              <div className="ac-degraded">
                Live account feed unavailable — showing engine-known positions at
                cost, without marks. Balances may lag.
              </div>
            )}
            <div className="ac-stats">
              <div className="ac-stat"><span>Balance</span><b>{money(k?.balance_usd)}</b></div>
              <div className="ac-stat"><span>Open exposure</span><b>{money(k?.exposure_usd)}</b></div>
              <div className="ac-stat"><span>Resting orders</span><b>{k?.resting ?? '—'}</b></div>
              <div className="ac-stat">
                <span>Feed age</span>
                <b className={k?.stale_s != null && k.stale_s > 120
                  ? 'ac9-stale' : undefined}>
                  {k?.stale_s == null ? '—'
                    : k.stale_s < 90 ? `${Math.round(k.stale_s)}s`
                    : `${Math.round(k.stale_s / 60)}m`}
                </b>
              </div>
            </div>
            {(k?.positions?.length ?? 0) > 0 ? (
              <div className="ac-table">
                <div className="ac-thead ac-cols-k">
                  <span>Market</span><span className="num">Qty</span><span className="num">Cost</span>
                  <span className="num">Bid</span><span className="num">Value</span><span className="num">Unrl</span><span />
                </div>
                {k!.positions.map((p) => (
                  <div className="ac-row ac-cols-k" key={p.ticker}>
                    <span className={`ac-cell ac-main${p.title ? '' : ' mono'}`}>
                      {p.title || p.ticker}
                      {p.title && <span className="ac-sub mono">{p.ticker}</span>}
                    </span>
                    <span className="ac-cell num mono" data-l="Qty">{Math.round(p.qty)}</span>
                    <span className="ac-cell num mono" data-l="Cost">{money(p.cost_usd)}</span>
                    <span className="ac-cell num mono" data-l="Bid">{cents(p.mark_bid)}</span>
                    <span className="ac-cell num mono" data-l="Value">{money(p.value_usd)}</span>
                    <span className={`ac-cell num mono ${pnlCls(p.unrealized)}`} data-l="Unrealized">
                      {signed(p.unrealized)}
                    </span>
                    <span className="ac-cell ac-act">
                      <button className="ac-cashout" onClick={() => cashOutK(p)}>Cash out</button>
                    </span>
                  </div>
                ))}
              </div>
            ) : <p className="ac-empty">No open Kalshi positions.</p>}
          </div>
        </div>
      )}
    </>
  )
}
