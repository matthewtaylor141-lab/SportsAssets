import { useCallback, useEffect, useMemo, useState } from 'react'
import { adminApi } from '../lib/api'

// Desk v4 (owner directive 2026-08-12): venue-faithful trading modes.
// The desk renders as a light venue-grammar surface with a hard toggle
// between a Polymarket-style interface (category tabs, outcome rows
// with Yes/No buttons, right-rail Buy panel with amount + potential
// return) and a Kalshi-style interface (live strip, category sidebar
// with counts, market cards with % pills, right-rail ticket with
// YES/NO pills, odds, max payout and a Review->Confirm flow). Layout,
// information architecture and trading flow mirror each venue closely
// so a trader keeps their muscle memory; the surface carries our own
// identity, never the venues' marks. Execution is UNCHANGED: every
// order routes through the walled-off 'manual' sleeve endpoints
// (Polymarket fills synchronously; Kalshi relays via the engine), and
// positions hold to resolution — the Sell tab is present but honest
// about that.

type Venue = 'polymarket' | 'kalshi'

interface Outcome { label: string; asset?: string; ticker?: string; price: number | null; no_price?: number | null }
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
  kalshiSide?: 'yes' | 'no'
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
  { key: 'mlb', label: 'MLB', icon: '⚾' },
  { key: 'wnba', label: 'WNBA', icon: '🏀' },
  { key: 'tennis', label: 'Tennis', icon: '🎾' },
  { key: 'nba', label: 'NBA', icon: '🏀' },
  { key: 'nfl', label: 'NFL', icon: '🏈' },
  { key: 'nhl', label: 'NHL', icon: '🏒' },
]
const SPORT_NAME: Record<string, string> = {
  mlb: 'BASEBALL', wnba: 'BASKETBALL', nba: 'BASKETBALL',
  nfl: 'FOOTBALL', nhl: 'HOCKEY', atp: 'TENNIS', wta: 'TENNIS',
  tennis: 'TENNIS',
}

export function TradeDesk() {
  const [token, setToken] = useState(() => sessionStorage.getItem('sa_admin_token') || '')
  const [authed, setAuthed] = useState(false)
  const [err, setErr] = useState('')
  const [venue, setVenue] = useState<Venue>('polymarket')
  const [league, setLeague] = useState('all')
  const [games, setGames] = useState<GameCard[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [game, setGame] = useState<GameView | null>(null)
  const [loading, setLoading] = useState(false)
  const [pick, setPick] = useState<Pick | null>(null)
  const [usd, setUsd] = useState('')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [reviewing, setReviewing] = useState(false)
  const [placing, setPlacing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [blotter, setBlotter] = useState<Blotter | null>(null)
  const [depth, setDepth] = useState<{ bids: number[][]; asks: number[][] } | null>(null)
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [pmResults, setPmResults] = useState<PMSearchMarket[]>([])
  const [kResults, setKResults] = useState<KalshiSearchRow[]>([])

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
    const t = setInterval(() => loadBlotter(token), 15000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed])

  const loadGames = useCallback((v: Venue, lg: string) => {
    setLoading(true)
    adminApi<{ games: GameCard[] }>(`/api/admin/desk-games?venue=${v}&league=${lg}`, token)
      .then((r) => {
        setGames(r.games)
        setCounts((c) => ({ ...c, [`${v}:${lg}`]: r.games.length }))
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

  // Debounced venue-aware search, unchanged from Desk v3.
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
    }, 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, venue, authed])

  const openGame = (g: { id: string; venue: Venue }) => {
    setGame(null)
    setResult(null)
    setLoading(true)
    adminApi<GameView>(`/api/admin/desk-game?venue=${g.venue}&id=${encodeURIComponent(g.id)}`, token)
      .then(setGame)
      .catch(() => setErr('Game failed to load.'))
      .finally(() => setLoading(false))
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
    const t = setInterval(load, 6000)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick])

  const choose = (p: Pick) => { setPick(p); setResult(null); setReviewing(false) }

  const place = async () => {
    if (!pick || placing) return
    const amount = parseFloat(usd)
    if (!(amount > 0)) { setErr('Enter a dollar amount.'); return }
    setPlacing(true)
    setResult(null)
    try {
      const body = pick.venue === 'polymarket'
        ? { venue: 'polymarket-us', asset: pick.asset, usd: amount }
        : { venue: 'kalshi', ticker: pick.ticker, side: pick.kalshiSide || 'yes', title: `${pick.label} — ${pick.side}`, usd: amount }
      const r = await adminApi<any>('/api/admin/manual-trade', token, {
        method: 'POST', body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      })
      setResult(r)
      setReviewing(false)
      loadBlotter(token)
      if (game) openGame({ id: game.id, venue: game.venue })
    } catch (e: any) {
      setResult({
        ok: false,
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check the blotter before retrying.'
          : `Request failed (${e?.message || 'network'}) — check the blotter before retrying.`,
      })
      setReviewing(false)
      loadBlotter(token)
    } finally {
      setPlacing(false)
    }
  }

  const amount = parseFloat(usd)
  const estLimit = pick ? Math.min(pick.ask + 0.02, 0.99) : 0
  const estContracts = pick && amount > 0 ? Math.floor(amount / estLimit) : 0
  const estCost = estContracts * estLimit
  const estPayout = estContracts * 1
  const available = blotter ? Math.max(0, blotter.day_budget - blotter.day_spent) : null

  const sportTag = (lg: string) => SPORT_NAME[lg] || 'SOCCER'

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

  // ── Order rail ────────────────────────────────────────────────────
  // Kalshi grammar: BUY/SELL tabs, YES/NO pills, dollars, available
  // balance, odds, max payout, Review -> Confirm. Polymarket grammar:
  // Buy/Sell tabs, outcome buttons, amount + quick-adds, one-tap
  // Trade with avg price / shares / potential return.
  const flipKalshiSide = (want: 'yes' | 'no') => {
    if (!pick || pick.venue !== 'kalshi' || !game) {
      return
    }
    for (const grp of game.groups) {
      for (const mk of grp.markets) {
        if (mk.ticker === pick.ticker) {
          const price = want === 'yes' ? mk.price : mk.no_price
          if (price != null) {
            choose({
              ...pick,
              ask: price,
              kalshiSide: want,
              side: want === 'yes' ? (mk.label || 'YES') : `NO ${mk.label || ''}`.trim(),
            })
          }
        }
      }
    }
  }

  const rail = (
    <aside className={`vd-rail${pick ? ' has-pick' : ''}`}>
      <div className="vd-ticket">
        <div className="vd-tabs">
          <button className={`vd-tab${side === 'buy' ? ' on' : ''}`} onClick={() => setSide('buy')}>
            {isK ? 'BUY' : 'Buy'}
          </button>
          <button className={`vd-tab${side === 'sell' ? ' on' : ''}`} onClick={() => setSide('sell')}>
            {isK ? 'SELL' : 'Sell'}
          </button>
          <span className="vd-tab-right">{isK ? 'DOLLARS ▾' : 'Market ▾'}</span>
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
            <div className="vd-mkt-title">{pick.label}</div>
            <div className="vd-mkt-outcome">{pick.side}</div>

            {isK ? (
              <div className="vd-yesno">
                <button
                  className={`vd-pill yes${pick.kalshiSide !== 'no' ? ' on' : ''}`}
                  onClick={() => flipKalshiSide('yes')}
                >
                  YES {pick.kalshiSide !== 'no' ? cents(pick.ask) : ''}
                </button>
                <button
                  className={`vd-pill no${pick.kalshiSide === 'no' ? ' on' : ''}`}
                  onClick={() => flipKalshiSide('no')}
                >
                  NO {pick.kalshiSide === 'no' ? cents(pick.ask) : ''}
                </button>
              </div>
            ) : (
              <div className="vd-yesno">
                <button className="vd-pill yes on">Yes {cents(pick.ask)}</button>
              </div>
            )}

            <div className="vd-amount">
              <input
                className="vd-amount-in" inputMode="decimal" placeholder="$0"
                value={usd ? `$${usd}` : ''}
                onChange={(e) => setUsd(e.target.value.replace(/[^0-9.]/g, ''))}
                aria-label="Dollar amount"
              />
              {!isK && (
                <div className="vd-quick">
                  {[1, 5, 25, 100].map((v) => (
                    <button key={v} onClick={() => setUsd(String((amount > 0 ? amount : 0) + v))}>
                      +${v}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {available != null && (
              <div className="vd-line sub">
                Desk account · {money(available)} available today
              </div>
            )}

            {isK ? (
              <>
                <div className="vd-line"><span>Odds</span><b>{pct(pick.ask)} chance</b></div>
                <div className="vd-line">
                  <span>Max payout</span>
                  <b className="vd-big">{estContracts > 0 ? money(estPayout) : '$0'}</b>
                </div>
              </>
            ) : (
              estContracts > 0 && (
                <>
                  <div className="vd-line"><span>Avg price</span><b>{cents(estLimit)}</b></div>
                  <div className="vd-line"><span>Shares</span><b>{estContracts}</b></div>
                  <div className="vd-line">
                    <span>Potential return</span>
                    <b className="pos">{money(estPayout)} ({estCost > 0 ? Math.round(((estPayout - estCost) / estCost) * 100) : 0}%)</b>
                  </div>
                </>
              )
            )}

            {depth && (depth.asks.length > 0 || depth.bids.length > 0) && (
              <div className="vd-book">
                <div>
                  <div className="vd-book-h">Asks</div>
                  {depth.asks.slice(0, 3).map(([p, s], i) => (
                    <div className="vd-book-row" key={i}>
                      <span className="neg">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="vd-book-h">Bids</div>
                  {depth.bids.slice(0, 3).map(([p, s], i) => (
                    <div className="vd-book-row" key={i}>
                      <span className="pos">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {isK ? (
              !reviewing ? (
                <button
                  className={`vd-cta k${amount > 0 ? ' ready' : ''}`}
                  disabled={!(amount > 0)}
                  onClick={() => setReviewing(true)}
                >
                  Review Buy
                </button>
              ) : (
                <button className="vd-cta k ready" disabled={placing} onClick={place}>
                  {placing ? 'Placing…' : `Confirm Buy · ${money(amount)}`}
                </button>
              )
            ) : (
              <button
                className={`vd-cta p${amount > 0 ? ' ready' : ''}`}
                disabled={!(amount > 0) || placing}
                onClick={place}
              >
                {placing ? 'Placing…' : `Buy ${pick.side}`}
              </button>
            )}

            <p className="vd-fine">
              Fill-or-kill at the live ask +2¢ protection.
              {isK && ' Orders place via the engine within ~10 seconds.'}
              {' '}Positions hold to resolution.
            </p>

            {result && (
              <p className={`vd-result ${result.ok ? 'pos' : 'neg'}`}>
                {result.ok
                  ? result.queued
                    ? `QUEUED: ${result.count} contracts @ ≤${cents(result.limit_price)} — ${result.detail}`
                    : `FILLED: ${result.filled_shares} contracts @ ${cents(result.fill_price)} (${result.title} — ${result.outcome})`
                  : `Not placed: ${result.error}`}
              </p>
            )}
          </>
        )}
      </div>
    </aside>
  )

  // ── Kalshi-mode market card ───────────────────────────────────────
  const kCard = (g: GameCard) => (
    <div className="kd-card" key={`${g.venue}-${g.id}`}>
      <div className="kd-card-top">
        <span className="kd-sport">{sportTag(g.league)}</span>
        <span className="kd-series">{g.league.toUpperCase()}</span>
      </div>
      <button className="kd-title" onClick={() => openGame(g)}>{g.title}</button>
      <div className="kd-outcomes">
        {g.outcomes.slice(0, 2).map((o) => (
          <div className="kd-outcome" key={o.label}>
            <span className={`kd-oname u${g.outcomes.indexOf(o) % 2}`}>{o.label}</span>
            <button
              className={`kd-pct${pick?.ticker === o.ticker && pick?.ticker != null ? ' on' : ''}`}
              disabled={o.price == null}
              onClick={() => o.price != null && choose({
                venue: 'kalshi', label: g.title, side: o.label, ask: o.price,
                ticker: o.ticker, kalshiSide: 'yes',
              })}
            >
              {pct(o.price)}
            </button>
          </div>
        ))}
      </div>
      <div className="kd-card-foot">
        <button className="kd-more" onClick={() => openGame(g)}>
          {g.markets_n ? `${g.markets_n} markets` : 'All markets'}
        </button>
      </div>
    </div>
  )

  // ── Polymarket-mode market card ───────────────────────────────────
  // "No" on a two-outcome game buys the OTHER side — exactly the
  // economics the venue's No button carries on a binary game.
  const pCard = (g: GameCard) => {
    const other = (o: Outcome) => g.outcomes.find((x) => x !== o)
    return (
      <div className="pd-card" key={`${g.venue}-${g.id}`}>
        <div className="pd-card-head">
          <span className="pd-icon">{LEAGUES.find((l) => l.key === g.league)?.icon || '⚽'}</span>
          <button className="pd-title" onClick={() => openGame(g)}>{g.title}</button>
        </div>
        {g.outcomes.slice(0, 2).map((o) => {
          const alt = other(o)
          return (
            <div className="pd-row" key={o.label}>
              <span className="pd-oname">{o.label}</span>
              <span className="pd-opct">{pct(o.price)}</span>
              <button
                className={`pd-btn yes${pick?.asset != null && pick?.asset === o.asset ? ' on' : ''}`}
                disabled={o.price == null}
                onClick={() => o.price != null && choose({
                  venue: 'polymarket', label: g.title, side: o.label,
                  ask: o.price, asset: o.asset,
                })}
              >Yes</button>
              <button
                className={`pd-btn no${pick?.asset != null && alt && pick?.asset === alt.asset ? ' on' : ''}`}
                disabled={alt?.price == null}
                onClick={() => alt?.price != null && choose({
                  venue: 'polymarket', label: g.title, side: alt.label,
                  ask: alt.price, asset: alt.asset,
                })}
              >No</button>
            </div>
          )
        })}
        <div className="pd-card-foot">
          <button className="pd-more" onClick={() => openGame(g)}>
            {g.markets_n ? `${g.markets_n} markets →` : 'All markets →'}
          </button>
        </div>
      </div>
    )
  }

  // ── Full game view (both modes) ───────────────────────────────────
  const gameView = game && (
    <div className={isK ? 'kd-game' : 'pd-game'}>
      <button className="vd-back" onClick={() => { setGame(null) }}>← Back</button>
      <h2 className="vd-game-title">{game.title}</h2>

      {game.positions.length > 0 && (
        <div className="vd-pos">
          <div className="vd-pos-h">Your positions</div>
          {game.positions.map((p) => (
            <div className="vd-pos-row" key={p.asset}>
              <b>{p.outcome || 'position'}</b>
              <span>Cost {money(p.cost)} @ {cents(p.fill_price)}</span>
              <span>Now {money(p.current_value)}</span>
              <span>To win <b>{money(p.to_win)}</b></span>
              {p.pnl != null && (
                <span className={p.pnl >= 0 ? 'pos' : 'neg'}>settled {money(p.pnl)}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {game.groups.map((grp) => (
        <div className="vd-group" key={grp.name}>
          <div className="vd-group-h">{grp.name}</div>
          {grp.markets.map((mk) => (
            <div className="vd-group-row" key={mk.asset || mk.ticker}>
              <span className="vd-group-label">{mk.label}</span>
              {isK ? (
                <>
                  <button
                    className={`vd-mini yes${pick?.ticker === mk.ticker && pick?.kalshiSide !== 'no' ? ' on' : ''}`}
                    disabled={mk.price == null}
                    onClick={() => mk.price != null && choose({
                      venue: 'kalshi', label: game.title, side: mk.label || 'YES',
                      ask: mk.price, ticker: mk.ticker, kalshiSide: 'yes',
                    })}
                  >Yes {cents(mk.price)}</button>
                  <button
                    className={`vd-mini no${pick?.ticker === mk.ticker && pick?.kalshiSide === 'no' ? ' on' : ''}`}
                    disabled={mk.no_price == null}
                    onClick={() => mk.no_price != null && choose({
                      venue: 'kalshi', label: game.title, side: `NO ${mk.label}`,
                      ask: mk.no_price!, ticker: mk.ticker, kalshiSide: 'no',
                    })}
                  >No {cents(mk.no_price)}</button>
                </>
              ) : (
                <button
                  className={`vd-mini yes${pick?.asset === mk.asset ? ' on' : ''}`}
                  disabled={mk.price == null}
                  onClick={() => mk.price != null && choose({
                    venue: 'polymarket', label: game.title, side: mk.label || 'Yes',
                    ask: mk.price, asset: mk.asset,
                  })}
                >Buy {cents(mk.price)}</button>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )

  // ── Search results (venue-styled) ─────────────────────────────────
  const searchBlock = q.trim().length >= 2 && (
    searching && pmResults.length === 0 && kResults.length === 0 ? (
      <div className="tr-skel" style={{ height: 140, borderRadius: 12 }} />
    ) : isK ? (
      <div className="kd-list">
        {kResults.map((m) => (
          <div className="kd-card" key={m.ticker}>
            <div className="kd-card-top"><span className="kd-sport">SEARCH</span></div>
            <div className="kd-title as-text">{m.title.replace(' Winner?', '')}</div>
            <div className="kd-outcomes">
              <div className="kd-outcome">
                <span className="kd-oname u0">{m.sub_title || 'YES'}</span>
                <button
                  className={`kd-pct${pick?.ticker === m.ticker && pick?.kalshiSide === 'yes' ? ' on' : ''}`}
                  disabled={m.yes_ask == null}
                  onClick={() => m.yes_ask != null && choose({
                    venue: 'kalshi', label: m.title.replace(' Winner?', ''),
                    side: m.sub_title || 'YES', ask: m.yes_ask,
                    ticker: m.ticker, kalshiSide: 'yes',
                  })}
                >{pct(m.yes_ask)}</button>
              </div>
              <div className="kd-outcome">
                <span className="kd-oname u1">No</span>
                <button
                  className={`kd-pct${pick?.ticker === m.ticker && pick?.kalshiSide === 'no' ? ' on' : ''}`}
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
      <div className="pd-grid">
        {pmResults.map((m) => (
          <div className="pd-card" key={m.slug}>
            <div className="pd-card-head">
              <span className="pd-icon">🔎</span>
              <span className="pd-title as-text">{m.title || m.slug}</span>
            </div>
            {m.outcomes.map((o) => (
              <div className="pd-row" key={o.asset}>
                <span className="pd-oname">{o.outcome}</span>
                <span className="pd-opct">{pct(o.ask)}</span>
                <button
                  className={`pd-btn yes${pick?.asset === o.asset ? ' on' : ''}`}
                  disabled={o.ask == null}
                  onClick={() => o.ask != null && choose({
                    venue: 'polymarket', label: m.title || m.slug,
                    side: o.outcome, ask: o.ask, asset: o.asset,
                  })}
                >Yes</button>
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

  const strip = useMemo(() => games.slice(0, 6), [games])

  return (
    <>
      <div className="vd-pagehead">
        <h1>Trade Desk</h1>
        <div className="vd-venues">
          {(['polymarket', 'kalshi'] as Venue[]).map((v) => (
            <button
              key={v}
              className={`vd-venue${venue === v ? ' on' : ''} ${v}`}
              onClick={() => { setVenue(v); setGame(null); setPick(null); setQ('') }}
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

      <div className={`vdesk ${isK ? 'vdesk--k' : 'vdesk--p'}`}>
        {isK ? (
          /* ── KALSHI-GRAMMAR SURFACE ─────────────────────────────── */
          <>
            {!game && strip.length > 0 && (
              <div className="kd-strip">
                {strip.map((g) => (
                  <button className="kd-mini" key={g.id} onClick={() => openGame(g)}>
                    <div className="kd-mini-h">{g.league.toUpperCase()}</div>
                    {g.outcomes.slice(0, 2).map((o) => (
                      <div className="kd-mini-row" key={o.label}>
                        <span>{o.label}</span>
                        <b>{cents(o.price)}</b>
                      </div>
                    ))}
                  </button>
                ))}
              </div>
            )}
            <div className="kd-body">
              <nav className="kd-side">
                {LEAGUES.map((l) => {
                  const n = counts[`kalshi:${l.key}`]
                  return (
                    <button
                      key={l.key}
                      className={`kd-side-row${league === l.key ? ' on' : ''}`}
                      onClick={() => { setLeague(l.key); setGame(null) }}
                    >
                      <span>{l.icon && `${l.icon} `}{l.label}</span>
                      <span className="kd-side-n">{n != null ? `(${n})` : '›'}</span>
                    </button>
                  )
                })}
              </nav>
              <main className="kd-main">
                <div className="kd-main-h">
                  <h2>Sports</h2>
                  <input
                    className="kd-search" placeholder="Search markets…"
                    value={q} onChange={(e) => setQ(e.target.value)}
                    aria-label="Search markets"
                  />
                </div>
                {q.trim().length >= 2 ? searchBlock : game ? gameView : (
                  loading && games.length === 0 ? (
                    <div className="tr-skel" style={{ height: 220, borderRadius: 12 }} />
                  ) : (
                    <div className="kd-list">{games.map(kCard)}</div>
                  )
                )}
                {err && q.trim().length < 2 && <p className="vd-empty">{err}</p>}
              </main>
              {rail}
            </div>
          </>
        ) : (
          /* ── POLYMARKET-GRAMMAR SURFACE ─────────────────────────── */
          <>
            <div className="pd-tabs">
              {LEAGUES.map((l) => (
                <button
                  key={l.key}
                  className={`pd-tabbtn${league === l.key ? ' on' : ''}`}
                  onClick={() => { setLeague(l.key); setGame(null) }}
                >
                  {l.icon && `${l.icon} `}{l.label}
                </button>
              ))}
              <input
                className="pd-search" placeholder="Search markets…"
                value={q} onChange={(e) => setQ(e.target.value)}
                aria-label="Search markets"
              />
            </div>
            <div className="pd-body">
              <main className="pd-main">
                {q.trim().length >= 2 ? searchBlock : game ? gameView : (
                  loading && games.length === 0 ? (
                    <div className="tr-skel" style={{ height: 220, borderRadius: 12 }} />
                  ) : (
                    <div className="pd-grid">{games.map(pCard)}</div>
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
        <div className="card-title">BLOTTER</div>
        {blotter && blotter.trades.length > 0 ? (
          <div className="rpt-table-wrap">
            <table className="rpt-table">
              <thead>
                <tr>
                  <th>Placed</th><th>Market</th><th>Side</th>
                  <th>Venue</th><th>Status</th><th>Cost</th><th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {blotter.trades.map((t) => (
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
        ) : <p style={{ opacity: 0.6 }}>No manual trades yet.</p>}
      </div>
    </>
  )
}
