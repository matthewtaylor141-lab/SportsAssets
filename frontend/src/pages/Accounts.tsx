import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  DESK_RELOCK_EVENT, deskApi, deskAuthed, deskUnlock,
  type DeskAccounts, type KPosition, type PMPosition,
} from '../lib/desk'
import '../styles/desk2.css'

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

export function Accounts() {
  const [authed, setAuthed] = useState(() => deskAuthed())
  const [pw, setPw] = useState('')
  const [gateErr, setGateErr] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [data, setData] = useState<DeskAccounts | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [pmTab, setPmTab] = useState<'positions' | 'trades'>('positions')
  const navigate = useNavigate()

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

      {data && (
        <div className="ac-head">
          <div className="ac-tile">
            <div className="ac-tile-l">Total value</div>
            <div className="ac-tile-v mono">{money(data.totals.value)}</div>
          </div>
          <div className="ac-tile">
            <div className="ac-tile-l">Total cash</div>
            <div className="ac-tile-v mono">{money(data.totals.cash)}</div>
          </div>
          <div className="ac-tile">
            <div className="ac-tile-l">Unrealized P&L</div>
            <div className={`ac-tile-v mono ${pnlCls(data.totals.unrealized)}`}>
              {signed(data.totals.unrealized)}
            </div>
          </div>
        </div>
      )}
      {data && (
        <p className="ac-asof">
          as of {new Date(data.as_of).toLocaleTimeString()} · refreshes every 30s
        </p>
      )}
      {loadErr && !data && <div className="card"><p className="ac-empty">{loadErr}</p></div>}
      {!data && !loadErr && <div className="tr-skel" style={{ height: 260, borderRadius: 12 }} />}

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
            <div className="ac-stats">
              <div className="ac-stat"><span>Account value</span><b>{money(pm?.account_value)}</b></div>
              <div className="ac-stat"><span>Cash</span><b>{money(pm?.cash)}</b></div>
              <div className="ac-stat"><span>Buying power</span><b>{money(pm?.buying_power)}</b></div>
              <div className="ac-stat"><span>Open value</span><b>{money(pm?.open_value)}</b></div>
              <div className="ac-stat"><span>Unsettled</span><b>{money(pm?.unsettled_funds)}</b></div>
              <div className="ac-stat">
                <span>Realized P&L</span>
                <b className={pnlCls(pm?.realized_pnl)}>{signed(pm?.realized_pnl)}</b>
              </div>
            </div>
            <div className="ac-tabs">
              <button className={pmTab === 'positions' ? 'on' : ''} onClick={() => setPmTab('positions')}>
                Positions {pm?.positions?.length ? `(${pm.positions.length})` : ''}
              </button>
              <button className={pmTab === 'trades' ? 'on' : ''} onClick={() => setPmTab('trades')}>
                Recent trades
              </button>
            </div>
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
                        <span className="ac-cell ac-act">
                          <button className="ac-cashout" onClick={() => cashOutPm(p)}>Cash out</button>
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
                <b>{k?.stale_s != null ? `${Math.round(k.stale_s)}s` : '—'}</b>
              </div>
            </div>
            {(k?.positions?.length ?? 0) > 0 ? (
              <div className="ac-table">
                <div className="ac-thead ac-cols-k">
                  <span>Ticker</span><span className="num">Qty</span><span className="num">Cost</span>
                  <span className="num">Bid</span><span className="num">Value</span><span className="num">Unrl</span><span />
                </div>
                {k!.positions.map((p) => (
                  <div className="ac-row ac-cols-k" key={p.ticker}>
                    <span className="ac-cell ac-main mono">{p.ticker}</span>
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
