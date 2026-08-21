import { useCallback, useEffect, useRef, useState } from 'react'
import { adminApi } from '../lib/api'

// Desk v5 (owner order 2026-08-21: "indistinguishable from the actual
// Polymarket and Kalshi platform, minus the logos"). Two full venue
// skins — layout, information architecture, order flow and confirmation
// mirror each venue's own app. Browse is VENUE-NATIVE: the boards come
// from each venue's own event listing (every market they list renders,
// tennis alternate totals and game/set spreads included), and every
// order streams its execution live in the ticket — submitted → AI
// executing → filled @ price, with the measured latency shown, so the
// trader watches the AI counterparty place their order in real time.
// Execution is UNCHANGED: the walled-off 'manual' sleeve endpoints.

type Venue = 'polymarket' | 'kalshi'

interface Outcome { label: string; asset?: string; ticker?: string; us_slug?: string; price: number | null; no_price?: number | null }
interface GameCard { id: string; venue: Venue; league: string; title: string; outcomes: Outcome[]; markets_n?: number }
interface GameGroup { name: string; markets: Outcome[] }
interface Position {
  asset: string; outcome: string | null; cost: number; fill_price: number
  shares: number; status: string; current_value: number | null
  to_win: number | null; pnl: number | null
}
interface GameView { id: string; venue: Venue; title: string; groups: GameGroup[]; positions: Position[] }

interface Pick {
  venue: Venue
  label: string
  side: string
  ask: number
  asset?: string
  ticker?: string
  usSlug?: string
  kalshiSide?: 'yes' | 'no'
}

type Phase = 'idle' | 'submitting' | 'relaying' | 'filled' | 'partial' | 'unfilled' | 'error'
interface OrderRun {
  phase: Phase
  t0: number
  ms?: number
  rowId?: number
  fillPrice?: number | null
  filledShares?: number
  filledUsd?: number
  requestedUsd?: number
  error?: string
  title?: string
  outcome?: string
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
interface Blotter { trades: ManualTrade[]; day_spent: number; day_budget: number; max_per_order: number }

interface PMSearchMarket {
  slug: string
  title: string
  event_title: string | null
  outcomes: { outcome: string; asset: string; ask: number | null; bid: number | null }[]
}
interface KalshiSearchRow {
  ticker: string
  title: string
  sub_title: string
  yes_ask: number | null
  no_ask: number | null
}

const cents = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}¢`)
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`

const LEAGUES: { key: string; label: string; icon: string }[] = [
  { key: 'all', label: 'All', icon: '' },
  { key: 'tennis', label: 'Tennis', icon: '🎾' },
  { key: 'mlb', label: 'MLB', icon: '⚾' },
  { key: 'soccer', label: 'Soccer', icon: '⚽' },
  { key: 'wnba', label: 'WNBA', icon: '🏀' },
  { key: 'nba', label: 'NBA', icon: '🏀' },
  { key: 'nfl', label: 'NFL', icon: '🏈' },
  { key: 'nhl', label: 'NHL', icon: '🏒' },
  { key: 'esports', label: 'Esports', icon: '🎮' },
]
const SPORT_NAME: Record<string, string> = {
  mlb: 'BASEBALL', wnba: 'BASKETBALL', nba: 'BASKETBALL',
  nfl: 'FOOTBALL', nhl: 'HOCKEY', tennis: 'TENNIS',
  soccer: 'SOCCER', esports: 'ESPORTS',
}

export function TradeDesk() {
  const [token, setToken] = useState(() => sessionStorage.getItem('sa_admin_token') || '')
  const [authed, setAuthed] = useState(false)
  const [err, setErr] = useState('')
  const [venue, setVenue] = useState<Venue>('polymarket')
  const [league, setLeague] = useState('all')
  const [games, setGames] = useState<GameCard[]>([])
  const [counts, setCounts] = useState<Record<string, Record<string, number>>>({})
  const [game, setGame] = useState<GameView | null>(null)
  const [loading, setLoading] = useState(false)
  const [pick, setPick] = useState<Pick | null>(null)
  const [usd, setUsd] = useState('')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [reviewing, setReviewing] = useState(false)
  const [run, setRun] = useState<OrderRun | null>(null)
  const [blotter, setBlotter] = useState<Blotter | null>(null)
  const [blotTab, setBlotTab] = useState<'open' | 'history'>('open')
  const [depth, setDepth] = useState<{ bids: number[][]; asks: number[][] } | null>(null)
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [pmResults, setPmResults] = useState<PMSearchMarket[]>([])
  const [kResults, setKResults] = useState<KalshiSearchRow[]>([])
  const pollRef = useRef<number | null>(null)

  const isK = venue === 'kalshi'

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

  const loadGames = useCallback((v: Venue, lg: string) => {
    setLoading(true)
    adminApi<{ games: GameCard[]; counts?: Record<string, number> }>(
      `/api/admin/desk-games?venue=${v}&league=${lg}`, token)
      .then((r) => {
        setGames(r.games)
        if (r.counts) setCounts((c) => ({ ...c, [v]: r.counts! }))
        setErr(r.games.length ? '' : 'No games in this window — try another sport.')
      })
      .catch(() => setErr('Games failed to load — pull to retry.'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (authed) loadGames(venue, league)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, venue, league])

  useEffect(() => {
    const query = q.trim()
    if (!authed || query.length < 2) { setPmResults([]); setKResults([]); setSearching(false); return }
    setSearching(true)
    const t = setTimeout(() => {
      if (venue === 'polymarket') {
        adminApi<{ markets: PMSearchMarket[] }>(
          `/api/admin/market-search?q=${encodeURIComponent(query)}`, token)
          .then((r) => setPmResults(r.markets || []))
          .catch(() => setPmResults([]))
          .finally(() => setSearching(false))
      } else {
        adminApi<{ markets: KalshiSearchRow[] }>(
          `/api/admin/kalshi-markets?q=${encodeURIComponent(query)}`, token)
          .then((r) => setKResults(r.markets || []))
          .catch(() => setKResults([]))
          .finally(() => setSearching(false))
      }
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, venue, authed])

  const openGame = (g: { id: string; venue: Venue }, keepRun = false) => {
    if (!keepRun) { setGame(null); setRun(null); setLoading(true) }
    adminApi<GameView>(`/api/admin/desk-game?venue=${g.venue}&id=${encodeURIComponent(g.id)}`, token)
      .then(setGame)
      .catch(() => { if (!keepRun) setErr('Game failed to load.') })
      .finally(() => { if (!keepRun) setLoading(false) })
  }

  useEffect(() => {
    setDepth(null)
    setReviewing(false)
    if (!pick) return
    const id = pick.venue === 'polymarket' ? pick.asset : pick.ticker
    if (!id) return
    let dead = false
    const load = () =>
      adminApi<{ bids: number[][]; asks: number[][] }>(
        `/api/admin/book?venue=${pick.venue}&id=${encodeURIComponent(id)}`, token,
      ).then((d) => { if (!dead) setDepth(d) }).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick])

  const choose = (p: Pick) => { setPick(p); setRun(null); setReviewing(false) }

  // ── Live order lifecycle ─────────────────────────────────────────
  // The ticket streams the AI counterparty's execution: a Polymarket
  // order returns synchronously (typical fill in 1-3s); a Kalshi order
  // returns queued and the ticket polls its row at 900ms until the
  // 2-second engine relay reports the fill. Either way the trader sees
  // submitted → executing → FILLED @ price, with the measured wall
  // time, without ever leaving the ticket.
  const stopPoll = () => {
    if (pollRef.current != null) { window.clearInterval(pollRef.current); pollRef.current = null }
  }
  useEffect(() => () => stopPoll(), [])

  const watchRow = (rowId: number, t0: number) => {
    stopPoll()
    pollRef.current = window.setInterval(async () => {
      try {
        const r = await adminApi<any>(`/api/admin/manual-order?id=${rowId}`, token)
        if (!r.found || !r.terminal) return
        stopPoll()
        const ms = Math.round(performance.now() - t0)
        loadBlotter(token)
        if (game) openGame({ id: game.id, venue: game.venue }, true)
        if (r.status === 'filled' || r.status === 'settled') {
          const partial = r.filled_usd > 0 && r.requested_usd > 0
            && r.filled_usd < r.requested_usd * 0.98
          setRun((prev) => prev && ({
            ...prev, phase: partial ? 'partial' : 'filled', ms,
            fillPrice: r.fill_price, filledShares: r.filled_shares,
            filledUsd: r.filled_usd, requestedUsd: r.requested_usd,
          }))
        } else if (r.status === 'unfilled') {
          setRun((prev) => prev && ({ ...prev, phase: 'unfilled', ms }))
        } else {
          setRun((prev) => prev && ({ ...prev, phase: 'error', ms, error: r.error || r.status }))
        }
      } catch { /* keep polling */ }
    }, 900)
  }

  const place = async () => {
    if (!pick || run?.phase === 'submitting' || run?.phase === 'relaying') return
    const amount = parseFloat(usd)
    if (!(amount > 0)) { setErr('Enter a dollar amount.'); return }
    const t0 = performance.now()
    setRun({ phase: 'submitting', t0, title: pick.label, outcome: pick.side })
    try {
      const body = pick.venue === 'polymarket'
        ? { venue: 'polymarket-us', asset: pick.asset || '', us_slug: pick.usSlug || '', ask: pick.ask, title: `${pick.label} — ${pick.side}`, usd: amount }
        : { venue: 'kalshi', ticker: pick.ticker, side: pick.kalshiSide || 'yes', title: `${pick.label} — ${pick.side}`, usd: amount }
      const r = await adminApi<any>('/api/admin/manual-trade', token, {
        method: 'POST', body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      })
      const ms = Math.round(performance.now() - t0)
      setReviewing(false)
      loadBlotter(token)
      if (r.ok && r.queued && r.row_id) {
        setRun((prev) => prev && ({ ...prev, phase: 'relaying', rowId: r.row_id }))
        watchRow(r.row_id, t0)
      } else if (r.ok && (r.filled_shares ?? 0) > 0) {
        const filledUsd = (r.filled_shares || 0) * (r.fill_price || 0)
        const partial = amount > 0 && filledUsd > 0 && filledUsd < amount * 0.9
        setRun((prev) => prev && ({
          ...prev, phase: partial ? 'partial' : 'filled', ms,
          fillPrice: r.fill_price, filledShares: r.filled_shares,
          filledUsd, requestedUsd: amount,
        }))
        if (game) openGame({ id: game.id, venue: game.venue }, true)
      } else {
        setRun((prev) => prev && ({
          ...prev, phase: r.ok === false && /did not fill/i.test(r.error || '') ? 'unfilled' : 'error',
          ms, error: r.error || 'order did not fill at the protected limit',
        }))
      }
    } catch (e: any) {
      setReviewing(false)
      loadBlotter(token)
      setRun((prev) => prev && ({
        ...prev, phase: 'error',
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check Open Orders before retrying.'
          : `Request failed (${e?.message || 'network'}) — check Open Orders before retrying.`,
      }))
    }
  }

  const amount = parseFloat(usd)
  const askKnown = !!pick && pick.ask > 0
  const estLimit = askKnown ? Math.min(pick!.ask + 0.02, 0.99) : 0
  const estContracts = askKnown && amount > 0 ? Math.floor(amount / estLimit) : 0
  const estCost = estContracts * estLimit
  const estPayout = estContracts * 1
  const available = blotter ? Math.max(0, blotter.day_budget - blotter.day_spent) : null
  const busy = run?.phase === 'submitting' || run?.phase === 'relaying'

  const sportTag = (lg: string) => SPORT_NAME[lg] || 'SPORTS'
  const countFor = (lg: string) => counts[venue]?.[lg]

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

  // ── Order execution timeline (shared, venue-skinned) ─────────────
  const runPanel = run && (
    <div className={`dx-run ${run.phase}`}>
      {(run.phase === 'submitting' || run.phase === 'relaying') && (
        <>
          <div className="dx-run-row on">
            <span className="dx-spin" /> Order submitted
          </div>
          <div className={`dx-run-row${run.phase === 'relaying' ? ' on' : ''}`}>
            <span className="dx-spin" />
            {isK ? 'AI counterparty relaying to Kalshi…' : 'AI counterparty executing on Polymarket…'}
          </div>
        </>
      )}
      {(run.phase === 'filled' || run.phase === 'partial') && (
        <>
          <div className="dx-run-big">
            <span className="dx-check">✓</span>
            {run.phase === 'partial' ? 'Partially filled' : 'Order filled'}
          </div>
          <div className="dx-run-fill">
            <b>{Math.round(run.filledShares || 0)}</b> contracts @ <b>{cents(run.fillPrice)}</b>
            {' '}· {money(run.filledUsd)}
            {run.phase === 'partial' && run.requestedUsd
              ? ` of ${money(run.requestedUsd)} requested (book depth at your price)` : ''}
          </div>
          {run.ms != null && (
            <div className="dx-run-lat">executed by the AI in {(run.ms / 1000).toFixed(1)}s</div>
          )}
        </>
      )}
      {run.phase === 'unfilled' && (
        <>
          <div className="dx-run-big neg"><span className="dx-x">✕</span> Not filled</div>
          <div className="dx-run-fill">
            No contracts available at your protected price — the book moved.
            Nothing was spent. Re-quote and try again.
          </div>
        </>
      )}
      {run.phase === 'error' && (
        <>
          <div className="dx-run-big neg"><span className="dx-x">✕</span> Not placed</div>
          <div className="dx-run-fill">{run.error}</div>
        </>
      )}
    </div>
  )

  // ── Order rail ───────────────────────────────────────────────────
  const flipKalshiSide = (want: 'yes' | 'no') => {
    if (!pick || pick.venue !== 'kalshi' || !game) return
    for (const grp of game.groups) {
      for (const mk of grp.markets) {
        if (mk.ticker === pick.ticker) {
          const price = want === 'yes' ? mk.price : mk.no_price
          if (price != null) {
            choose({
              ...pick, ask: price, kalshiSide: want,
              side: want === 'yes' ? (mk.label || 'YES') : `NO ${mk.label || ''}`.trim(),
            })
          }
        }
      }
    }
  }

  const rail = (
    <aside className={`vd-rail${pick ? ' has-pick' : ''}`}>
      <div className={isK ? 'kx-ticket' : 'pmx-ticket'}>
        <div className="dx-tabs">
          <button className={`dx-tab${side === 'buy' ? ' on' : ''}`} onClick={() => setSide('buy')}>Buy</button>
          <button className={`dx-tab${side === 'sell' ? ' on' : ''}`} onClick={() => setSide('sell')}>Sell</button>
          <span className="dx-tab-right">Market ▾</span>
        </div>

        {!pick ? (
          <p className="vd-empty">Select an outcome to build your order.</p>
        ) : side === 'sell' ? (
          <p className="vd-empty">
            This account holds to resolution — positions settle automatically
            at full value when they win. Selling isn't offered on the desk.
          </p>
        ) : (
          <>
            <div className="dx-mkt-title">{pick.label}</div>

            {isK ? (
              <div className="dx-yesno">
                <button
                  className={`kx-pill yes${pick.kalshiSide !== 'no' ? ' on' : ''}`}
                  onClick={() => flipKalshiSide('yes')}
                >Yes {pick.kalshiSide !== 'no' ? cents(pick.ask) : ''}</button>
                <button
                  className={`kx-pill no${pick.kalshiSide === 'no' ? ' on' : ''}`}
                  onClick={() => flipKalshiSide('no')}
                >No {pick.kalshiSide === 'no' ? cents(pick.ask) : ''}</button>
              </div>
            ) : (
              <div className="dx-outcome-line">
                <span className="dx-outcome-name">{pick.side}</span>
                <span className="pmx-price">{askKnown ? cents(pick.ask) : 'quoted at execution'}</span>
              </div>
            )}

            <div className="dx-amount">
              <span className="dx-amount-label">Amount</span>
              <input
                className="dx-amount-in" inputMode="decimal" placeholder="$0"
                value={usd ? `$${usd}` : ''}
                disabled={busy}
                onChange={(e) => setUsd(e.target.value.replace(/[^0-9.]/g, ''))}
                aria-label="Dollar amount"
              />
              <div className="dx-quick">
                {[1, 20, 100].map((v) => (
                  <button key={v} disabled={busy} onClick={() => setUsd(String((amount > 0 ? amount : 0) + v))}>
                    +${v}
                  </button>
                ))}
                <button
                  disabled={busy || available == null}
                  onClick={() => available != null && setUsd(String(Math.floor(Math.min(available, blotter?.max_per_order ?? available))))}
                >Max</button>
              </div>
            </div>

            {available != null && (
              <div className="dx-line sub">Desk account · {money(available)} available today</div>
            )}

            {isK ? (
              <>
                {askKnown && (
                  <div className="dx-line"><span>Contracts</span><b>{estContracts > 0 ? estContracts : '—'}</b></div>
                )}
                {askKnown && (
                  <div className="dx-line"><span>Est. fees</span><b>$0.00</b></div>
                )}
                <div className="dx-line">
                  <span>Payout if Yes</span>
                  <b className="dx-big pos">{estContracts > 0 ? money(estPayout) : '$0'}</b>
                </div>
              </>
            ) : (
              estContracts > 0 && (
                <>
                  <div className="dx-line"><span>Avg price</span><b>{cents(estLimit)}</b></div>
                  <div className="dx-line"><span>Shares</span><b>{estContracts}</b></div>
                  <div className="dx-line">
                    <span>To win</span>
                    <b className="dx-big pos">{money(estPayout)}
                      <span className="dx-ret"> (+{estCost > 0 ? Math.round(((estPayout - estCost) / estCost) * 100) : 0}%)</span></b>
                  </div>
                </>
              )
            )}

            {depth && (depth.asks.length > 0 || depth.bids.length > 0) && (
              <div className="dx-book">
                <div>
                  <div className="dx-book-h">Asks</div>
                  {depth.asks.slice(0, 3).map(([p, s], i) => (
                    <div className="dx-book-row" key={i}>
                      <span className="neg">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="dx-book-h">Bids</div>
                  {depth.bids.slice(0, 3).map(([p, s], i) => (
                    <div className="dx-book-row" key={i}>
                      <span className="pos">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {runPanel}

            {(!run || run.phase === 'unfilled' || run.phase === 'error') && (
              isK ? (
                !reviewing ? (
                  <button
                    className={`kx-cta${amount > 0 ? ' ready' : ''}`}
                    disabled={!(amount > 0)}
                    onClick={() => setReviewing(true)}
                  >Review order</button>
                ) : (
                  <button className="kx-cta ready confirm" onClick={place}>
                    Confirm · Buy {pick.kalshiSide === 'no' ? 'No' : 'Yes'} · {money(amount)}
                  </button>
                )
              ) : (
                <button
                  className={`pmx-cta${amount > 0 ? ' ready' : ''}`}
                  disabled={!(amount > 0)}
                  onClick={place}
                >Trade</button>
              )
            )}
            {(run?.phase === 'filled' || run?.phase === 'partial') && (
              <button
                className={isK ? 'kx-cta ready' : 'pmx-cta ready'}
                onClick={() => { setRun(null); setUsd('') }}
              >Place another order</button>
            )}

            <p className="dx-fine">
              Fill up to your amount at your price or better — never worse.
              Unfilled remainder cancels instantly. Positions hold to resolution.
            </p>
          </>
        )}
      </div>
    </aside>
  )

  // ── Kalshi-skin market card ──────────────────────────────────────
  const kCard = (g: GameCard) => (
    <div className="kx-card" key={`${g.venue}-${g.id}`}>
      <div className="kx-card-top">
        <span className="kx-sport">{sportTag(g.league)}</span>
        <span className="kx-series">{g.league.toUpperCase()}</span>
      </div>
      <button className="kx-title" onClick={() => openGame(g)}>{g.title}</button>
      <div className="kx-outcomes">
        {g.outcomes.slice(0, 2).map((o) => (
          <div className="kx-outcome" key={o.label}>
            <span className="kx-oname">{o.label}</span>
            <button
              className={`kx-pct${pick?.ticker != null && pick?.ticker === o.ticker ? ' on' : ''}`}
              disabled={o.price == null}
              onClick={() => o.price != null && choose({
                venue: 'kalshi', label: g.title, side: o.label, ask: o.price,
                ticker: o.ticker, kalshiSide: 'yes',
              })}
            >{pct(o.price)}</button>
          </div>
        ))}
      </div>
      <div className="kx-card-foot">
        <button className="kx-more" onClick={() => openGame(g)}>
          {g.markets_n ? `${g.markets_n} markets` : 'All markets'}
        </button>
      </div>
    </div>
  )

  // ── Polymarket-skin market card ──────────────────────────────────
  const pCard = (g: GameCard) => {
    const other = (o: Outcome) => g.outcomes.find((x) => x !== o)
    const sel = (o: Outcome | undefined) =>
      !!o && ((o.us_slug && pick?.usSlug === o.us_slug) || (o.asset && pick?.asset === o.asset))
    const buy = (o: Outcome) => choose({
      venue: 'polymarket', label: g.title, side: o.label,
      ask: o.price ?? 0, asset: o.asset, usSlug: o.us_slug,
    })
    return (
      <div className="pmx-card" key={`${g.venue}-${g.id}`}>
        <div className="pmx-card-head">
          <span className="pmx-icon">{LEAGUES.find((l) => l.key === g.league)?.icon || '🏟'}</span>
          <button className="pmx-title" onClick={() => openGame(g)}>{g.title}</button>
        </div>
        {g.outcomes.slice(0, 2).map((o) => {
          const alt = other(o)
          return (
            <div className="pmx-row" key={o.label}>
              <span className="pmx-oname">{o.label}</span>
              <span className="pmx-opct">{pct(o.price)}</span>
              <button
                className={`pmx-btn yes${sel(o) ? ' on' : ''}`}
                disabled={o.price == null && !o.us_slug}
                onClick={() => buy(o)}
              >Yes {o.price != null ? cents(o.price) : ''}</button>
              <button
                className={`pmx-btn no${sel(alt) ? ' on' : ''}`}
                disabled={!alt || (alt.price == null && !alt.us_slug)}
                onClick={() => alt && buy(alt)}
              >No {alt?.price != null ? cents(alt.price) : ''}</button>
            </div>
          )
        })}
        <div className="pmx-card-foot">
          <button className="pmx-more" onClick={() => openGame(g)}>
            {g.markets_n ? `${g.markets_n} markets →` : 'All markets →'}
          </button>
        </div>
      </div>
    )
  }

  // ── Full game view (both skins) ──────────────────────────────────
  const gameView = game && (
    <div className={isK ? 'kx-game' : 'pmx-game'}>
      <button className="dx-back" onClick={() => { setGame(null) }}>← Back to all games</button>
      <h2 className="dx-game-title">{game.title}</h2>

      {game.positions.length > 0 && (
        <div className="dx-pos">
          <div className="dx-pos-h">Your positions</div>
          {game.positions.map((p) => (
            <div className="dx-pos-row" key={p.asset}>
              <b>{p.outcome || 'position'}</b>
              <span>Cost {money(p.cost)} @ {cents(p.fill_price)}</span>
              <span>To win <b>{money(p.to_win)}</b></span>
              {p.pnl != null && (
                <span className={p.pnl >= 0 ? 'pos' : 'neg'}>settled {money(p.pnl)}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {game.groups.map((grp) => (
        <div className="dx-group" key={grp.name}>
          <div className="dx-group-h">{grp.name}<span className="dx-group-n">{grp.markets.length}</span></div>
          {grp.markets.map((mk) => (
            <div className="dx-group-row" key={mk.us_slug || mk.asset || mk.ticker || mk.label}>
              <span className="dx-group-label">{mk.label}</span>
              {isK ? (
                <>
                  <button
                    className={`dx-mini yes${pick?.ticker === mk.ticker && pick?.kalshiSide !== 'no' ? ' on' : ''}`}
                    disabled={mk.price == null}
                    onClick={() => mk.price != null && choose({
                      venue: 'kalshi', label: game.title, side: mk.label || 'YES',
                      ask: mk.price, ticker: mk.ticker, kalshiSide: 'yes',
                    })}
                  >Yes {cents(mk.price)}</button>
                  <button
                    className={`dx-mini no${pick?.ticker === mk.ticker && pick?.kalshiSide === 'no' ? ' on' : ''}`}
                    disabled={mk.no_price == null}
                    onClick={() => mk.no_price != null && choose({
                      venue: 'kalshi', label: game.title, side: `NO ${mk.label}`,
                      ask: mk.no_price!, ticker: mk.ticker, kalshiSide: 'no',
                    })}
                  >No {cents(mk.no_price)}</button>
                </>
              ) : (
                <button
                  className={`dx-mini yes${(mk.asset && pick?.asset === mk.asset)
                    || (mk.us_slug && pick?.usSlug === mk.us_slug) ? ' on' : ''}`}
                  disabled={mk.price == null && !mk.us_slug}
                  onClick={() => (mk.price != null || mk.us_slug) && choose({
                    venue: 'polymarket', label: game.title, side: mk.label || 'Yes',
                    ask: mk.price ?? 0, asset: mk.asset, usSlug: mk.us_slug,
                  })}
                >Buy{mk.price != null ? ` ${cents(mk.price)}` : ''}</button>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )

  // ── Search results ───────────────────────────────────────────────
  const searchBlock = q.trim().length >= 2 && (
    searching && pmResults.length === 0 && kResults.length === 0 ? (
      <div className="tr-skel" style={{ height: 140, borderRadius: 12 }} />
    ) : isK ? (
      <div className="kx-list">
        {kResults.map((m) => (
          <div className="kx-card" key={m.ticker}>
            <div className="kx-card-top"><span className="kx-sport">SEARCH</span></div>
            <div className="kx-title as-text">{m.title.replace(' Winner?', '')}</div>
            <div className="kx-outcomes">
              <div className="kx-outcome">
                <span className="kx-oname">{m.sub_title || 'YES'}</span>
                <button
                  className={`kx-pct${pick?.ticker === m.ticker && pick?.kalshiSide === 'yes' ? ' on' : ''}`}
                  disabled={m.yes_ask == null}
                  onClick={() => m.yes_ask != null && choose({
                    venue: 'kalshi', label: m.title.replace(' Winner?', ''),
                    side: m.sub_title || 'YES', ask: m.yes_ask,
                    ticker: m.ticker, kalshiSide: 'yes',
                  })}
                >{pct(m.yes_ask)}</button>
              </div>
              <div className="kx-outcome">
                <span className="kx-oname">No</span>
                <button
                  className={`kx-pct${pick?.ticker === m.ticker && pick?.kalshiSide === 'no' ? ' on' : ''}`}
                  disabled={m.no_ask == null}
                  onClick={() => m.no_ask != null && choose({
                    venue: 'kalshi', label: m.title.replace(' Winner?', ''),
                    side: `NO ${m.sub_title || ''}`.trim(), ask: m.no_ask,
                    ticker: m.ticker, kalshiSide: 'no',
                  })}
                >{pct(m.no_ask)}</button>
              </div>
            </div>
          </div>
        ))}
        {kResults.length === 0 && !searching && (
          <p className="vd-empty">No live markets match "{q.trim()}".</p>
        )}
      </div>
    ) : (
      <div className="pmx-grid">
        {pmResults.map((m) => (
          <div className="pmx-card" key={m.slug}>
            <div className="pmx-card-head">
              <span className="pmx-icon">🔎</span>
              <span className="pmx-title as-text">{m.title || m.slug}</span>
            </div>
            {m.outcomes.map((o) => (
              <div className="pmx-row" key={o.asset}>
                <span className="pmx-oname">{o.outcome}</span>
                <span className="pmx-opct">{pct(o.ask)}</span>
                <button
                  className={`pmx-btn yes${pick?.asset === o.asset ? ' on' : ''}`}
                  disabled={o.ask == null}
                  onClick={() => o.ask != null && choose({
                    venue: 'polymarket', label: m.title || m.slug,
                    side: o.outcome, ask: o.ask, asset: o.asset,
                  })}
                >Yes {cents(o.ask)}</button>
              </div>
            ))}
          </div>
        ))}
        {pmResults.length === 0 && !searching && (
          <p className="vd-empty">No live markets match "{q.trim()}".</p>
        )}
      </div>
    )
  )

  // ── Blotter: Open orders / History, venue-portfolio shape ────────
  const openTrades = (blotter?.trades || []).filter((t) =>
    ['filled', 'submitting', 'queued'].includes(t.status))
  const histTrades = (blotter?.trades || []).filter((t) =>
    !['filled', 'submitting', 'queued'].includes(t.status))
  const blotRows = blotTab === 'open' ? openTrades : histTrades

  return (
    <>
      <div className="vd-pagehead">
        <h1>Trade Desk</h1>
        <div className="vd-venues">
          {(['polymarket', 'kalshi'] as Venue[]).map((v) => (
            <button
              key={v}
              className={`vd-venue${venue === v ? ' on' : ''} ${v}`}
              onClick={() => { setVenue(v); setGame(null); setPick(null); setQ(''); setRun(null) }}
            >
              {v === 'polymarket' ? 'Polymarket' : 'Kalshi'} mode
            </button>
          ))}
        </div>
      </div>
      <p style={{ opacity: 0.75, marginTop: 0 }}>
        Orders execute through the AI's live accounts as the <b>manual</b> sleeve —
        separate budget, zero effect on autonomous trading.
        {blotter && (
          <> Today: <b>{money(blotter.day_spent)}</b> of {money(blotter.day_budget)} ·
            max {money(blotter.max_per_order)}/ticket.</>
        )}
      </p>

      <div className={`vdesk ${isK ? 'vdesk--kx' : 'vdesk--pmx'}`}>
        {isK ? (
          <div className="kx-body">
            <nav className="kx-side">
              {LEAGUES.map((l) => {
                const n = countFor(l.key)
                return (
                  <button
                    key={l.key}
                    className={`kx-side-row${league === l.key ? ' on' : ''}`}
                    onClick={() => { setLeague(l.key); setGame(null) }}
                  >
                    <span>{l.icon && `${l.icon} `}{l.label}</span>
                    <span className="kx-side-n">{n != null ? n : '›'}</span>
                  </button>
                )
              })}
            </nav>
            <main className="kx-main">
              <div className="kx-main-h">
                <h2>Sports</h2>
                <input
                  className="kx-search" placeholder="Search markets…"
                  value={q} onChange={(e) => setQ(e.target.value)}
                  aria-label="Search markets"
                />
              </div>
              {q.trim().length >= 2 ? searchBlock : game ? gameView : (
                loading && games.length === 0 ? (
                  <div className="tr-skel" style={{ height: 220, borderRadius: 12 }} />
                ) : (
                  <div className="kx-list">{games.map(kCard)}</div>
                )
              )}
              {err && q.trim().length < 2 && <p className="vd-empty">{err}</p>}
            </main>
            {rail}
          </div>
        ) : (
          <>
            <div className="pmx-tabs">
              {LEAGUES.map((l) => {
                const n = countFor(l.key)
                return (
                  <button
                    key={l.key}
                    className={`pmx-tabbtn${league === l.key ? ' on' : ''}`}
                    onClick={() => { setLeague(l.key); setGame(null) }}
                  >
                    {l.icon && `${l.icon} `}{l.label}{n != null ? ` ${n}` : ''}
                  </button>
                )
              })}
              <input
                className="pmx-search" placeholder="Search markets…"
                value={q} onChange={(e) => setQ(e.target.value)}
                aria-label="Search markets"
              />
            </div>
            <div className="pmx-body">
              <main className="pmx-main">
                {q.trim().length >= 2 ? searchBlock : game ? gameView : (
                  loading && games.length === 0 ? (
                    <div className="tr-skel" style={{ height: 220, borderRadius: 12 }} />
                  ) : (
                    <div className="pmx-grid">{games.map(pCard)}</div>
                  )
                )}
                {err && q.trim().length < 2 && <p className="vd-empty">{err}</p>}
              </main>
              {rail}
            </div>
          </>
        )}
      </div>

      <div className="card">
        <div className="dx-blot-tabs">
          <button className={blotTab === 'open' ? 'on' : ''} onClick={() => setBlotTab('open')}>
            Positions {openTrades.length ? `(${openTrades.length})` : ''}
          </button>
          <button className={blotTab === 'history' ? 'on' : ''} onClick={() => setBlotTab('history')}>
            History
          </button>
        </div>
        {blotRows.length > 0 ? (
          <div className="rpt-table-wrap">
            <table className="rpt-table">
              <thead>
                <tr>
                  <th>Placed</th><th>Market</th><th>Side</th>
                  <th>Venue</th><th>Status</th><th>Cost</th><th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {blotRows.map((t) => (
                  <tr key={t.id}>
                    <td>{t.placed_at ? new Date(t.placed_at).toLocaleTimeString() : '—'}</td>
                    <td>{t.title}</td>
                    <td>{t.outcome || '—'}</td>
                    <td>{t.venue || 'polymarket'}</td>
                    <td>{t.status}{t.error ? ` (${t.error.slice(0, 40)})` : ''}</td>
                    <td>{money(t.filled_usd || t.requested_usd)}</td>
                    <td className={(t.pnl ?? 0) > 0 ? 'pos' : (t.pnl ?? 0) < 0 ? 'neg' : ''}>{money(t.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p style={{ opacity: 0.6 }}>{blotTab === 'open' ? 'No open positions.' : 'No settled trades yet.'}</p>}
      </div>
    </>
  )
}
