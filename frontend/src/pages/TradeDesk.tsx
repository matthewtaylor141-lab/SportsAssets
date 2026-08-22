import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { adminApi } from '../lib/api'
import {
  DESK_RELOCK_EVENT, deskAdminToken, deskApi, deskToken, deskUnlock,
  type DeskAccounts,
} from '../lib/desk'
import {
  PriceChart, Sparkline, fetchHistory, useDeskHistory,
  type HistoryPoint, type HistoryVenue,
} from '../components/PriceChart'
import '../styles/desk2.css'

// Desk v6 (owner order 2026-08-22, on top of the v5 venue skins): the
// team uses both venues "identically as if they were logged in on the
// native apps — better than the venues". New in v6: shared desk-password
// session (admin token still accepted), live account balances in the
// header, an account-wide positions panel with marks + unrealized and a
// protected CASH OUT flow (limit = bid − 2¢, floored at 1¢, IOC), cancel
// on pending Kalshi tickets, and the full 5-level book with spread/mid.
// Execution is UNCHANGED: the walled-off 'manual' sleeve endpoints, all
// v5 money locks intact (placingRef, dup guards, comma normalization).

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

// A cash-out target: one held position, either venue. Built from the
// /api/desk/accounts snapshot — the server re-quotes and re-clamps to
// held at execution, so these numbers are display-grade only.
interface CoTarget {
  venue: Venue
  title: string
  outcome?: string
  usSlug?: string
  ticker?: string
  held: number
  cost: number | null
  mark: number | null
  value: number | null
  unrealized: number | null
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
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
// Kalshi's published taker-fee formula, computed at the protective limit.
const kalshiFee = (count: number, price: number) =>
  0.07 * count * price * (1 - price)

const LEAGUES: { key: string; label: string; icon: string }[] = [
  { key: 'all', label: 'All', icon: '' },
  // Wave-2: the full open universe on both venues, not just sports.
  { key: 'everything', label: 'Everything', icon: '🌐' },
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

// ── Lazy card sparklines (Wave-2) ────────────────────────────────────
// A browse/search card only fetches history once it scrolls into view
// (IntersectionObserver, 120px lookahead), through a 12-slot limiter so
// a big grid never floods the API, into a ref-map cache owned by the
// page — re-renders and revisits are free. Read-only decoration: an
// error just leaves the reserved slot empty.
const SPARK_MAX = 12
let sparkActive = 0
const sparkQ: (() => void)[] = []
const sparkNext = () => {
  if (sparkActive >= SPARK_MAX) return
  const job = sparkQ.shift()
  if (job) { sparkActive++; job() }
}
const sparkSlot = <T,>(job: () => Promise<T>): Promise<T> =>
  new Promise<T>((resolve, reject) => {
    sparkQ.push(() => {
      job().then(resolve, reject).finally(() => { sparkActive--; sparkNext() })
    })
    sparkNext()
  })

function Spark({ venue, id, cacheRef }: {
  venue: HistoryVenue
  id: string
  cacheRef: { current: Map<string, HistoryPoint[]> }
}) {
  const key = `${venue}|${id}`
  const boxRef = useRef<HTMLSpanElement | null>(null)
  const [pts, setPts] = useState<HistoryPoint[] | null>(
    () => cacheRef.current.get(key) || null)
  useEffect(() => {
    const cached = cacheRef.current.get(key)
    if (cached) { setPts(cached); return }
    const el = boxRef.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    let dead = false
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((x) => x.isIntersecting)) return
      io.disconnect()
      sparkSlot(() => fetchHistory(venue, id, 24))
        .then((p) => { cacheRef.current.set(key, p); if (!dead) setPts(p) })
        .catch(() => { cacheRef.current.set(key, []); if (!dead) setPts([]) })
    }, { rootMargin: '120px' })
    io.observe(el)
    return () => { dead = true; io.disconnect() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return (
    <span className="pc-spark" ref={boxRef}>
      {pts && pts.length >= 2 ? <Sparkline points={pts} /> : null}
    </span>
  )
}

export function TradeDesk() {
  const [authed, setAuthed] = useState(() => deskToken() != null)
  const [pw, setPw] = useState('')
  const [unlocking, setUnlocking] = useState(false)
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
  const [acct, setAcct] = useState<DeskAccounts | null>(null)
  const [co, setCo] = useState<CoTarget | null>(null)
  const [coQty, setCoQty] = useState('')
  const [coAll, setCoAll] = useState(true)
  const [coRun, setCoRun] = useState<OrderRun | null>(null)
  const [coBid, setCoBid] = useState<number | null>(null)
  const [cancelling, setCancelling] = useState<number | string | null>(null)
  const [cancelErr, setCancelErr] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const pollRef = useRef<number | null>(null)
  const placingRef = useRef(false)
  const coPollRef = useRef<number | null>(null)
  const coPlacingRef = useRef(false)
  const coLinkDone = useRef(false)

  const isK = venue === 'kalshi'

  // ── Price history (Wave-2): venue + id straight from the pick ────
  // One hook feeds both the game-view chart and the compact ticket
  // chart (fetchHistory dedupes the wire call); 60s refresh inside.
  const [histHours, setHistHours] = useState<24 | 168>(24)
  const sparkCache = useRef(new Map<string, HistoryPoint[]>())
  const histVenue: HistoryVenue | undefined =
    pick ? (pick.venue === 'polymarket' ? 'polymarket-us' : 'kalshi') : undefined
  const histId = pick ? (pick.venue === 'polymarket' ? pick.asset : pick.ticker) : undefined
  const histPoints = useDeskHistory(histVenue, histId, histHours)
  // Cash-out modal chart: Kalshi positions are addressable by ticker;
  // Polymarket positions only carry the market slug (no token id for
  // the history API), so the modal chart is Kalshi-only for now and
  // hides gracefully otherwise.
  const coHistId = co?.venue === 'kalshi' ? co.ticker : undefined
  const coHistPoints = useDeskHistory(coHistId ? 'kalshi' : undefined, coHistId, 24)
  const coEntry = co && co.cost != null && co.cost > 0 && co.held > 0
    ? co.cost / co.held : null

  const loadBlotter = useCallback(() => {
    deskApi<Blotter>('/api/admin/manual-trades').then(setBlotter).catch(() => {})
  }, [])

  const loadAcct = useCallback(() => {
    deskApi<DeskAccounts>('/api/desk/accounts').then(setAcct).catch(() => {})
  }, [])

  // ── Desk session gate ────────────────────────────────────────────
  // Team members unlock with the desk password (12h session token);
  // the owner's admin token still works — the same input accepts both.
  const unlock = useCallback(async (secret: string) => {
    if (!secret || unlocking) return
    setUnlocking(true)
    const r = await deskUnlock(secret)
    if (r.ok) {
      setAuthed(true); setErr(''); setPw(''); setUnlocking(false)
      return
    }
    try {
      const ping = await adminApi<{ match: boolean }>(
        '/api/admin/ping', secret, { method: 'POST', body: '{}' },
      )
      if (ping.match) {
        sessionStorage.setItem('sa_admin_token', secret)
        setAuthed(true); setErr(''); setPw(''); setUnlocking(false)
        return
      }
    } catch { /* fall through to the desk-unlock error */ }
    setErr(r.error || 'Wrong password.')
    setUnlocking(false)
  }, [unlocking])

  useEffect(() => {
    // Resume: a live desk token self-verifies by expiry; a stored admin
    // token is verified with the same ping the v5 gate used.
    if (deskToken()) { setAuthed(true); return }
    const atok = deskAdminToken()
    if (atok) {
      adminApi<{ match: boolean }>('/api/admin/ping', atok, { method: 'POST', body: '{}' })
        .then((p) => { if (p.match) setAuthed(true) })
        .catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const relock = () => { setAuthed(false); setAcct(null) }
    window.addEventListener(DESK_RELOCK_EVENT, relock)
    return () => window.removeEventListener(DESK_RELOCK_EVENT, relock)
  }, [])

  useEffect(() => {
    if (!authed) return
    loadBlotter()
    const t = setInterval(loadBlotter, 12000)
    return () => clearInterval(t)
  }, [authed, loadBlotter])

  useEffect(() => {
    if (!authed) return
    loadAcct()
    const t = setInterval(loadAcct, 30000)
    return () => clearInterval(t)
  }, [authed, loadAcct])

  const loadGames = useCallback((v: Venue, lg: string) => {
    setLoading(true)
    deskApi<{ games: GameCard[]; counts?: Record<string, number> }>(
      `/api/admin/desk-games?venue=${v}&league=${lg}`)
      .then((r) => {
        setGames(r.games)
        if (r.counts) setCounts((c) => ({ ...c, [v]: r.counts! }))
        setErr(r.games.length ? '' : 'No games in this window — try another sport.')
      })
      .catch(() => setErr('Games failed to load — pull to retry.'))
      .finally(() => setLoading(false))
  }, [])

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
        deskApi<{ markets: PMSearchMarket[] }>(
          `/api/admin/market-search?q=${encodeURIComponent(query)}`)
          .then((r) => setPmResults(r.markets || []))
          .catch(() => setPmResults([]))
          .finally(() => setSearching(false))
      } else {
        deskApi<{ markets: KalshiSearchRow[] }>(
          `/api/admin/kalshi-markets?q=${encodeURIComponent(query)}`)
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
    deskApi<GameView>(`/api/admin/desk-game?venue=${g.venue}&id=${encodeURIComponent(g.id)}`)
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
      deskApi<{ bids: number[][]; asks: number[][] }>(
        `/api/admin/book?venue=${pick.venue}&id=${encodeURIComponent(id)}`,
      ).then((d) => { if (!dead) setDepth(d) }).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick])

  // While an order is in flight, re-picking is locked (audit 2026-08-21):
  // choose() reset `run`, which un-hid the Trade button mid-submit — a
  // second click fired a second real-money POST, and the first order's
  // response then grafted onto (or was silently swallowed by) the new
  // ticket. The lock lifts the moment the run reaches a terminal phase.
  const inFlight = () =>
    run?.phase === 'submitting' || run?.phase === 'relaying'
  const choose = (p: Pick) => {
    if (inFlight()) return
    setPick(p); setRun(null); setReviewing(false); setSide('buy')
  }

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
  const stopCoPoll = () => {
    if (coPollRef.current != null) { window.clearInterval(coPollRef.current); coPollRef.current = null }
  }
  useEffect(() => () => { stopPoll(); stopCoPoll() }, [])

  const watchRow = (rowId: number, t0: number) => {
    stopPoll()
    pollRef.current = window.setInterval(async () => {
      try {
        const r = await deskApi<any>(`/api/admin/manual-order?id=${rowId}&venue=kalshi`)
        if (!r.found || !r.terminal) return
        stopPoll()
        const ms = Math.round(performance.now() - t0)
        loadBlotter()
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
    // Synchronous re-entry lock: two clicks in the same tick both see
    // the pre-setRun state, so the state-based guard alone can double-
    // POST a real order. The ref closes that window; the phase guard
    // covers everything after the next render.
    if (!pick || placingRef.current || inFlight()) return
    const amount = parseFloat(usd)
    if (!(amount > 0)) { setErr('Enter a dollar amount.'); return }
    placingRef.current = true
    const t0 = performance.now()
    setRun({ phase: 'submitting', t0, title: pick.label, outcome: pick.side })
    try {
      const body = pick.venue === 'polymarket'
        ? { venue: 'polymarket-us', asset: pick.asset || '', us_slug: pick.usSlug || '', ask: pick.ask, title: `${pick.label} — ${pick.side}`, usd: amount }
        : { venue: 'kalshi', ticker: pick.ticker, side: pick.kalshiSide || 'yes', title: `${pick.label} — ${pick.side}`, usd: amount }
      const r = await deskApi<any>('/api/admin/manual-trade', {
        method: 'POST', body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      })
      const ms = Math.round(performance.now() - t0)
      placingRef.current = false
      setReviewing(false)
      loadBlotter()
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
      placingRef.current = false
      setReviewing(false)
      loadBlotter()
      setRun((prev) => prev && ({
        ...prev, phase: 'error',
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check Open Orders before retrying.'
          : `Request failed (${e?.message || 'network'}) — check Open Orders before retrying.`,
      }))
    }
  }

  // ── Cash-out flow ────────────────────────────────────────────────
  // Same fail-closed grammar as buys, mirrored: the server re-quotes
  // the bid, sells at (bid − 2¢, floored at 1¢) IOC, clamps to held,
  // and refuses when nothing is held or the book is empty. Kalshi
  // sells queue through the manual sleeve and stream their relay here
  // exactly like Kalshi buys.
  const coInFlight = () =>
    coRun?.phase === 'submitting' || coRun?.phase === 'relaying'

  const openCashOut = (t: CoTarget) => {
    if (coInFlight()) return
    setCo(t); setCoQty(''); setCoAll(true); setCoRun(null)
    setCoBid(t.mark)
  }
  const closeCashOut = () => {
    if (coRun?.phase === 'submitting') return
    stopCoPoll()
    setCo(null); setCoRun(null)
  }

  // Live re-quote while the modal is open (Kalshi books are addressable
  // by ticker; Polymarket positions carry only the slug, so the account
  // snapshot's mark stands in and the server re-quotes at execution).
  useEffect(() => {
    if (!co || co.venue !== 'kalshi' || !co.ticker) return
    let dead = false
    const load = () =>
      deskApi<{ bids: number[][]; asks: number[][] }>(
        `/api/admin/book?venue=kalshi&id=${encodeURIComponent(co.ticker!)}`,
      ).then((d) => {
        if (!dead && d.bids && d.bids.length) setCoBid(d.bids[0][0])
      }).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => { dead = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [co])

  const watchCoRow = (rowId: number, t0: number, requested: number) => {
    stopCoPoll()
    coPollRef.current = window.setInterval(async () => {
      try {
        const r = await deskApi<any>(`/api/admin/manual-order?id=${rowId}&venue=kalshi`)
        if (!r.found || !r.terminal) return
        stopCoPoll()
        const ms = Math.round(performance.now() - t0)
        loadBlotter(); loadAcct()
        if ((r.status === 'filled' || r.status === 'settled') && (r.filled_shares ?? 0) > 0) {
          const partial = requested > 0 && r.filled_shares < requested
          setCoRun((prev) => prev && ({
            ...prev, phase: partial ? 'partial' : 'filled', ms,
            fillPrice: r.fill_price, filledShares: r.filled_shares,
            filledUsd: r.filled_usd,
          }))
        } else if (r.status === 'unfilled' || r.status === 'cancelled') {
          setCoRun((prev) => prev && ({ ...prev, phase: 'unfilled', ms }))
        } else {
          setCoRun((prev) => prev && ({ ...prev, phase: 'error', ms, error: r.error || r.status }))
        }
      } catch { /* keep polling */ }
    }, 900)
  }

  const coHeld = co ? Math.max(0, Math.floor(co.held)) : 0
  const coCount = co ? (coAll ? coHeld : Math.floor(parseInt(coQty || '0', 10) || 0)) : 0
  const coLimit = coBid != null && coBid > 0 ? Math.max(0.01, coBid - 0.02) : null
  const coProceeds = coLimit != null && coCount > 0 ? coCount * coLimit : null
  const coFee = co?.venue === 'kalshi' && coLimit != null && coCount > 0
    ? kalshiFee(coCount, coLimit) : null
  const coValid = coCount > 0 && coCount <= coHeld

  const placeCashOut = async () => {
    // Same synchronous re-entry lock as buys — a doubled sell is a
    // doubled real-money order.
    if (!co || coPlacingRef.current || coInFlight() || !coValid) return
    coPlacingRef.current = true
    const t0 = performance.now()
    const requested = coCount
    setCoRun({ phase: 'submitting', t0, title: co.title, outcome: co.outcome })
    try {
      const body: Record<string, unknown> = co.venue === 'polymarket'
        ? { venue: 'polymarket-us', us_slug: co.usSlug || '', outcome: co.outcome || '' }
        : { venue: 'kalshi', ticker: co.ticker }
      if (!coAll) body.qty = requested
      const r = await deskApi<any>('/api/desk/cash-out', {
        method: 'POST', body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      })
      const ms = Math.round(performance.now() - t0)
      coPlacingRef.current = false
      loadBlotter()
      if (r.ok && r.queued && r.row_id) {
        if (r.quoted_bid != null) setCoBid(r.quoted_bid)
        setCoRun((prev) => prev && ({ ...prev, phase: 'relaying', rowId: r.row_id }))
        watchCoRow(r.row_id, t0, r.count ?? requested)
      } else if (r.ok && (r.filled_shares ?? 0) > 0) {
        const partial = r.filled_shares < requested
        setCoRun((prev) => prev && ({
          ...prev, phase: partial ? 'partial' : 'filled', ms,
          fillPrice: r.avg_price, filledShares: r.filled_shares,
          filledUsd: r.proceeds_usd,
        }))
        loadAcct()
        if (game) openGame({ id: game.id, venue: game.venue }, true)
      } else {
        const detail = r.error || r.detail || 'cash-out refused'
        setCoRun((prev) => prev && ({
          ...prev, phase: /did not fill|no bid|book moved/i.test(detail) ? 'unfilled' : 'error',
          ms, error: detail,
        }))
      }
    } catch (e: any) {
      coPlacingRef.current = false
      loadBlotter()
      setCoRun((prev) => prev && ({
        ...prev, phase: 'error',
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check Open Orders before retrying.'
          : `Request failed (${e?.message || 'network'}) — check Open Orders before retrying.`,
      }))
    }
  }

  // ── Account positions for the active venue skin ──────────────────
  const venuePositions: CoTarget[] = !acct ? [] : isK
    ? (acct.kalshi.positions || []).map((p) => ({
      venue: 'kalshi' as const, title: p.ticker, ticker: p.ticker,
      held: p.qty, cost: p.cost_usd, mark: p.mark_bid,
      value: p.value_usd, unrealized: p.unrealized,
    }))
    : (acct.polymarket.positions || []).map((p) => ({
      venue: 'polymarket' as const, title: p.title || p.market_slug,
      outcome: p.outcome, usSlug: p.market_slug, held: p.qty, cost: p.cost,
      mark: p.value != null && p.qty > 0 ? p.value / p.qty : null,
      value: p.value, unrealized: p.unrealized,
    }))

  // Deep link from the Accounts page: /desk?co_venue=…&co_slug=…&co_ticker=…
  // opens the matching position's cash-out modal once the snapshot lands.
  useEffect(() => {
    if (!authed || !acct || coLinkDone.current) return
    const v = searchParams.get('co_venue')
    if (!v) return
    coLinkDone.current = true
    if (v === 'kalshi') {
      setVenue('kalshi')
      const tk = searchParams.get('co_ticker')
      const p = (acct.kalshi.positions || []).find((x) => x.ticker === tk)
      if (p) openCashOut({
        venue: 'kalshi', title: p.ticker, ticker: p.ticker, held: p.qty,
        cost: p.cost_usd, mark: p.mark_bid, value: p.value_usd, unrealized: p.unrealized,
      })
    } else {
      setVenue('polymarket')
      const slug = searchParams.get('co_slug')
      const out = searchParams.get('co_outcome')
      const p = (acct.polymarket.positions || []).find(
        (x) => x.market_slug === slug && (!out || x.outcome === out))
      if (p) openCashOut({
        venue: 'polymarket', title: p.title || p.market_slug, outcome: p.outcome,
        usSlug: p.market_slug, held: p.qty, cost: p.cost,
        mark: p.value != null && p.qty > 0 ? p.value / p.qty : null,
        value: p.value, unrealized: p.unrealized,
      })
    }
    setSide('sell')
    setSearchParams({}, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, acct, searchParams])

  // ── Cancel a pending Kalshi queue ticket ─────────────────────────
  const cancelRow = async (id: number | string) => {
    if (cancelling != null) return
    setCancelling(id); setCancelErr('')
    try {
      const r = await deskApi<{ ok: boolean; cancelled?: boolean; error?: string }>(
        `/api/desk/manual-order/${id}`, { method: 'DELETE' })
      if (!r.ok) setCancelErr(r.error || 'Could not cancel — already picked up by the engine.')
    } catch {
      setCancelErr('Cancel failed — check Open Orders before retrying.')
    }
    setCancelling(null)
    loadBlotter()
  }

  const amount = parseFloat(usd)
  const askKnown = !!pick && pick.ask > 0
  const estLimit = askKnown ? Math.min(pick!.ask + 0.02, 0.99) : 0
  const estContracts = askKnown && amount > 0 ? Math.floor(amount / estLimit) : 0
  const estCost = estContracts * estLimit
  const estPayout = estContracts * 1
  const available = blotter ? Math.max(0, blotter.day_budget - blotter.day_spent) : null
  const busy = run?.phase === 'submitting' || run?.phase === 'relaying'
  const coBusy = coInFlight()

  const sportTag = (lg: string) => SPORT_NAME[lg] || 'SPORTS'
  const countFor = (lg: string) => counts[venue]?.[lg]

  if (!authed) {
    return (
      <>
        <h1>Trade Desk</h1>
        <div className="card dk-gate">
          <p>Team access — enter the desk password (admin token also accepted).</p>
          <div className="dk-gate-row">
            <input
              className="input" type="password" value={pw}
              autoCapitalize="none" autoCorrect="off" autoComplete="current-password" spellCheck={false}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && unlock(pw.trim())}
              aria-label="Desk password"
            />
            <button className="btn" onClick={() => unlock(pw.trim())} disabled={unlocking}>
              {unlocking ? 'Unlocking…' : 'Unlock'}
            </button>
          </div>
          {err && <p className="dk-gate-err">{err}</p>}
        </div>
      </>
    )
  }

  // ── Order execution timeline (shared: buys + cash-outs) ──────────
  const execTimeline = (r: OrderRun, sell: boolean, kal: boolean) => (
    <div className={`dx-run ${r.phase}`}>
      {(r.phase === 'submitting' || r.phase === 'relaying') && (
        <>
          <div className="dx-run-row on">
            <span className="dx-spin" /> {sell ? 'Cash-out submitted' : 'Order submitted'}
          </div>
          <div className={`dx-run-row${r.phase === 'relaying' ? ' on' : ''}`}>
            <span className="dx-spin" />
            {kal ? 'AI counterparty relaying to Kalshi…' : 'AI counterparty executing on Polymarket…'}
          </div>
        </>
      )}
      {(r.phase === 'filled' || r.phase === 'partial') && (
        <>
          <div className="dx-run-big">
            <span className="dx-check">✓</span>
            {r.phase === 'partial'
              ? (sell ? 'Partially cashed out' : 'Partially filled')
              : (sell ? 'Cashed out' : 'Order filled')}
          </div>
          <div className="dx-run-fill">
            <b>{Math.round(r.filledShares || 0)}</b> contracts @ <b>{cents(r.fillPrice)}</b>
            {' '}· {sell ? 'proceeds ' : ''}{money(r.filledUsd)}
            {r.phase === 'partial' && (sell
              ? ' — the rest stays open (book depth at your price)'
              : r.requestedUsd
                ? ` of ${money(r.requestedUsd)} requested (book depth at your price)` : '')}
          </div>
          {r.ms != null && (
            <div className="dx-run-lat">executed by the AI in {(r.ms / 1000).toFixed(1)}s</div>
          )}
        </>
      )}
      {r.phase === 'unfilled' && (
        <>
          <div className="dx-run-big neg"><span className="dx-x">✕</span> {sell ? 'Not sold' : 'Not filled'}</div>
          <div className="dx-run-fill">
            {sell
              ? 'No contracts sold at your protected price — the book moved. You still hold the position. Re-quote and try again.'
              : 'No contracts available at your protected price — the book moved. Nothing was spent. Re-quote and try again.'}
          </div>
        </>
      )}
      {r.phase === 'error' && (
        <>
          <div className="dx-run-big neg"><span className="dx-x">✕</span> {sell ? 'Not cashed out' : 'Not placed'}</div>
          <div className="dx-run-fill">{r.error}</div>
        </>
      )}
    </div>
  )
  const runPanel = run && execTimeline(run, false, isK)

  // ── Sell tab / positions panel content ───────────────────────────
  const sellList = (
    <div className="dx-sellpos">
      {!acct ? (
        <p className="vd-empty">Loading account positions…</p>
      ) : venuePositions.length === 0 ? (
        <p className="vd-empty">
          No open {isK ? 'Kalshi' : 'Polymarket'} positions on the account.
        </p>
      ) : venuePositions.map((t) => (
        <div className="dx-sellpos-row" key={`${t.usSlug || t.ticker}-${t.outcome || ''}`}>
          <div className="dx-sellpos-top">
            <span>{t.title}</span>
            <span className={(t.unrealized ?? 0) > 0 ? 'pos' : (t.unrealized ?? 0) < 0 ? 'neg' : ''}>
              {signed(t.unrealized)}
            </span>
          </div>
          <div className="dx-sellpos-sub">
            <span>{t.outcome || t.ticker} · {Math.round(t.held)} @ mark {cents(t.mark)}</span>
            <span>value {money(t.value)}</span>
          </div>
          <button className="dx-cashout" disabled={coBusy} onClick={() => openCashOut(t)}>
            CASH OUT
          </button>
        </div>
      ))}
    </div>
  )

  const acctPanel = (
    <div className="dx-acct">
      <div className="dx-acct-h">
        {isK ? 'Kalshi' : 'Polymarket'} account positions
        {acct && (
          <span>
            {isK
              ? `${money(acct.kalshi.balance_usd)} cash`
              : (acct.polymarket.cash != null
                  ? `${money(acct.polymarket.cash)} cash · ${money(acct.polymarket.open_value)} open`
                  : `${money(acct.polymarket.trading_capital)} trading capital · ${money(acct.polymarket.open_value)} open`)}
          </span>
        )}
      </div>
      {isK && acct?.kalshi.degraded && (
        <div className="dx-acct-empty">
          Live Kalshi feed degraded — showing engine-known positions at cost, no marks.
        </div>
      )}
      {!acct ? (
        <div className="dx-acct-empty">Loading account snapshot…</div>
      ) : venuePositions.length === 0 ? (
        <div className="dx-acct-empty">No open positions on this account.</div>
      ) : venuePositions.map((t) => (
        <div className="dx-acct-row" key={`${t.usSlug || t.ticker}-${t.outcome || ''}`}>
          <div className="dx-acct-name">
            {t.title}
            <small>{t.outcome || t.ticker}</small>
          </div>
          <span className="dx-acct-num">Qty <b>{Math.round(t.held)}</b></span>
          <span className="dx-acct-num hide-sm">Cost <b>{money(t.cost)}</b></span>
          <span className="dx-acct-num hide-sm">Mark <b>{cents(t.mark)}</b></span>
          <span className="dx-acct-num">
            Unrl <b className={(t.unrealized ?? 0) > 0 ? 'pos' : (t.unrealized ?? 0) < 0 ? 'neg' : ''}>
              {signed(t.unrealized)}
            </b>
          </span>
          <button className="dx-cashout" disabled={coBusy} onClick={() => openCashOut(t)}>
            CASH OUT
          </button>
        </div>
      ))}
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

  const bestBid = depth?.bids?.[0]?.[0]
  const bestAsk = depth?.asks?.[0]?.[0]

  const rail = (
    <aside className={`vd-rail${pick || side === 'sell' ? ' has-pick' : ''}`}>
      <div className={isK ? 'kx-ticket' : 'pmx-ticket'}>
        <div className="dx-tabs">
          <button className={`dx-tab${side === 'buy' ? ' on' : ''}`} onClick={() => setSide('buy')}>Buy</button>
          <button className={`dx-tab${side === 'sell' ? ' on' : ''}`} onClick={() => setSide('sell')}>Sell</button>
          <span className="dx-tab-right">Market ▾</span>
        </div>

        {side === 'sell' ? (
          <>
            <div className="dx-mkt-title">Cash out a held position</div>
            {sellList}
            <p className="dx-fine">
              Sells are protected: limit = live bid − 2¢ (never below 1¢), IOC —
              whatever doesn't sell at your price stays yours.
            </p>
          </>
        ) : !pick ? (
          <p className="vd-empty">Select an outcome to build your order.</p>
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

            {histId && (
              <PriceChart points={histPoints} hours={histHours} compact />
            )}

            <div className="dx-amount">
              <span className="dx-amount-label">Amount</span>
              <input
                className="dx-amount-in" inputMode="decimal" placeholder="$0"
                value={usd ? `$${usd}` : ''}
                disabled={busy}
                onChange={(e) => setUsd(
                  // Comma is a decimal point, never deleted: stripping it
                  // turned "12,50" into a $1,250 request (audit 2026-08-21).
                  e.target.value.replace(/,/g, '.').replace(/[^0-9.]/g, ''))}
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
                  <div className="dx-line">
                    <span>Est. fees</span>
                    <b>{estContracts > 0 ? money(kalshiFee(estContracts, estLimit)) : '$0.00'}</b>
                  </div>
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
                  {depth.asks.slice(0, 5).map(([p, s], i) => (
                    <div className="dx-book-row" key={i}>
                      <span className="neg">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="dx-book-h">Bids</div>
                  {depth.bids.slice(0, 5).map(([p, s], i) => (
                    <div className="dx-book-row" key={i}>
                      <span className="pos">{cents(p)}</span>
                      <span>{Math.round(s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                {bestBid != null && bestAsk != null && (
                  <div className="dx-book-mid">
                    <span>Spread {((bestAsk - bestBid) * 100).toFixed(1)}¢</span>
                    <span>Mid {(((bestAsk + bestBid) / 2) * 100).toFixed(1)}¢</span>
                  </div>
                )}
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
              Unfilled remainder cancels instantly. Cash out any time from the Sell tab.
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
        {(() => {
          const t = g.outcomes.find((o) => o.ticker)?.ticker
          return t ? <Spark venue="kalshi" id={t} cacheRef={sparkCache} /> : null
        })()}
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
          {(() => {
            const a = g.outcomes.find((x) => x.asset)?.asset
            return a ? <Spark venue="polymarket-us" id={a} cacheRef={sparkCache} /> : null
          })()}
        </div>
      </div>
    )
  }

  // ── Full game view (both skins) ──────────────────────────────────
  const gameView = game && (
    <div className={isK ? 'kx-game' : 'pmx-game'}>
      <button className="dx-back" onClick={() => { setGame(null) }}>← Back to all games</button>
      <h2 className="dx-game-title">{game.title}</h2>

      {pick && histId && pick.label === game.title && (
        <div className="pc-block">
          <div className="pc-head">
            <span className="pc-title">
              {pick.side}
              {isK && pick.kalshiSide === 'no' ? ' · market yes price' : ''}
              {' — price history'}
            </span>
            <div className="pc-hours" role="group" aria-label="History range">
              <button className={histHours === 24 ? 'on' : ''}
                      onClick={() => setHistHours(24)}>24h</button>
              <button className={histHours === 168 ? 'on' : ''}
                      onClick={() => setHistHours(168)}>7d</button>
            </div>
          </div>
          <PriceChart points={histPoints} hours={histHours} />
        </div>
      )}

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
            <div className="kx-card-foot">
              <Spark venue="kalshi" id={m.ticker} cacheRef={sparkCache} />
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
            {m.outcomes[0]?.asset && (
              <div className="pmx-card-foot">
                <Spark venue="polymarket-us" id={m.outcomes[0].asset} cacheRef={sparkCache} />
              </div>
            )}
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

  // Best-effort join from blotter rows to the account snapshot so open
  // rows pick up live marks + unrealized where the payload covers them
  // (title-keyed — anything ambiguous stays "—", never a wrong number).
  const rowMark = (t: ManualTrade): { mark: number | null; unrl: number | null } => {
    const none = { mark: null, unrl: null }
    if (!acct || t.status !== 'filled') return none
    const title = (t.title || '').toLowerCase()
    if ((t.venue || '').startsWith('kalshi')) {
      const hits = (acct.kalshi.positions || []).filter((p) =>
        title.includes(p.ticker.toLowerCase()) || (t.outcome || '').toLowerCase() === p.ticker.toLowerCase())
      return hits.length === 1 ? { mark: hits[0].mark_bid, unrl: hits[0].unrealized } : none
    }
    let hits = (acct.polymarket.positions || []).filter((p) =>
      p.title && title.includes(p.title.toLowerCase()))
    if (hits.length > 1) {
      hits = hits.filter((p) => p.outcome && title.includes(p.outcome.toLowerCase()))
    }
    if (hits.length !== 1) return none
    const p = hits[0]
    return {
      mark: p.value != null && p.qty > 0 ? p.value / p.qty : null,
      unrl: p.unrealized,
    }
  }
  const cancellable = (t: ManualTrade) =>
    t.status === 'queued' && (t.venue || '').startsWith('kalshi')

  return (
    <>
      <div className="vd-pagehead">
        <h1>Trade Desk</h1>
        <div className="vd-venues">
          {(['polymarket', 'kalshi'] as Venue[]).map((v) => (
            <button
              key={v}
              className={`vd-venue${venue === v ? ' on' : ''} ${v}`}
              onClick={() => {
                if (inFlight()) return
                setVenue(v); setGame(null); setPick(null); setQ(''); setRun(null)
              }}
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
        {acct && (
          <>
            {' '}
            <span className="dx-balances">
              {isK ? (
                <>Kalshi cash <b>{money(acct.kalshi.balance_usd)}</b> ·
                  buying power <b>{money(acct.kalshi.balance_usd)}</b></>
              ) : (
                <>{acct.polymarket.cash != null ? (
                    <>Polymarket cash <b>{money(acct.polymarket.cash)}</b> ·
                      buying power <b>{money(acct.polymarket.buying_power)}</b></>
                  ) : (
                    <>Polymarket trading capital{' '}
                      <b>{money(acct.polymarket.trading_capital)}</b>
                      {(acct.polymarket.committed_usd ?? 0) > 0 &&
                        <span className="vd-cc-note"> incl. committed capital</span>}
                    </>
                  )}</>
              )}
            </span>
          </>
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
              {acctPanel}
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
                {acctPanel}
              </main>
              {rail}
            </div>
          </>
        )}

        {/* ── Cash-out modal (self-skinned by the position's venue) ── */}
        {co && (
          <div
            className="dxm-overlay"
            onClick={(e) => { if (e.target === e.currentTarget) closeCashOut() }}
          >
            <div
              className={`dxm ${co.venue === 'kalshi' ? 'dxm--kx' : 'dxm--pmx'}`}
              role="dialog" aria-modal="true" aria-label="Cash out position"
            >
              <div className="dxm-h">
                <div className="dxm-title">
                  Cash out — {co.title}
                  <small>{co.venue === 'kalshi' ? co.ticker : co.outcome}</small>
                </div>
                <button
                  className="dxm-x" onClick={closeCashOut} aria-label="Close"
                  disabled={coRun?.phase === 'submitting'}
                >✕</button>
              </div>

              <div className="dx-line"><span>Held</span><b>{coHeld} contracts</b></div>
              <div className="dx-line">
                <span>{co.venue === 'kalshi' ? 'Current bid' : 'Mark (snapshot)'}</span>
                <b>{cents(coBid)}</b>
              </div>
              <div className="dx-line">
                <span>Protective limit</span>
                <b>{coLimit != null ? `${cents(coLimit)} (bid − 2¢)` : 'bid − 2¢ at execution'}</b>
              </div>

              {coHistId && (
                <PriceChart
                  points={coHistPoints} hours={24} entry={coEntry} compact
                  caption={coEntry != null ? 'Last 24h · entry marked' : 'Last 24h'}
                />
              )}

              <div className="dxm-qty">
                <button
                  className={`dxm-all${coAll ? ' on' : ''}`}
                  disabled={coBusy}
                  onClick={() => { setCoAll(true); setCoQty('') }}
                >All {coHeld}</button>
                <input
                  inputMode="numeric" placeholder="contracts"
                  value={coAll ? '' : coQty}
                  disabled={coBusy}
                  onChange={(e) => {
                    setCoAll(false)
                    setCoQty(e.target.value.replace(/[^0-9]/g, ''))
                  }}
                  onFocus={() => setCoAll(false)}
                  aria-label="Contracts to sell"
                />
              </div>
              {!coAll && coQty !== '' && !coValid && (
                <p className="dxm-err">Enter 1–{coHeld} contracts — you can't sell more than you hold.</p>
              )}

              <div className="dx-line"><span>Est. proceeds</span><b>{money(coProceeds)}</b></div>
              {co.venue === 'kalshi' && (
                <>
                  <div className="dx-line">
                    <span>Kalshi fee (est.)</span>
                    <b>{coFee != null ? `-${money(coFee)}` : '—'}</b>
                  </div>
                  <div className="dx-line">
                    <span>Est. net</span>
                    <b>{coProceeds != null && coFee != null ? money(coProceeds - coFee) : '—'}</b>
                  </div>
                </>
              )}

              {coRun && execTimeline(coRun, true, co.venue === 'kalshi')}

              {(!coRun || coRun.phase === 'unfilled' || coRun.phase === 'error') && (
                <button
                  className={`dxm-cta${coValid ? ' ready' : ''}`}
                  disabled={!coValid || coBusy}
                  onClick={placeCashOut}
                >
                  Sell {coValid ? coCount : '—'} @ {coLimit != null ? `${cents(coLimit)} or better` : 'bid − 2¢ or better'}
                </button>
              )}
              {(coRun?.phase === 'filled' || coRun?.phase === 'partial') && (
                <button className="dxm-cta ready" onClick={closeCashOut}>Done</button>
              )}

              <p className="dx-fine">
                The AI re-quotes the live bid at execution and sells IOC at
                bid − 2¢ (never below 1¢{co.venue === 'kalshi' ? ', engine clamps to held' : ''}).
                Whatever doesn't sell at your price stays yours — nothing ever
                sells below the protective limit.
              </p>
            </div>
          </div>
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
        {cancelErr && blotTab === 'open' && <p className="dk-gate-err">{cancelErr}</p>}
        {blotRows.length > 0 ? (
          <div className="rpt-table-wrap">
            <table className="rpt-table">
              <thead>
                <tr>
                  <th>Placed</th><th>Market</th><th>Side</th>
                  <th>Venue</th><th>Status</th><th>Cost</th>
                  {blotTab === 'open' && <><th>Mark</th><th>Unrl</th></>}
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {blotRows.map((t) => {
                  const m = blotTab === 'open' ? rowMark(t) : { mark: null, unrl: null }
                  return (
                    <tr key={t.id}>
                      <td>{t.placed_at ? new Date(t.placed_at).toLocaleTimeString() : '—'}</td>
                      <td>{t.title}</td>
                      <td>{t.outcome || '—'}</td>
                      <td>{t.venue || 'polymarket'}</td>
                      <td>
                        {t.status}{t.error ? ` (${t.error.slice(0, 40)})` : ''}
                        {blotTab === 'open' && cancellable(t) && (
                          <>
                            {' '}
                            <button
                              className="dx-cancel"
                              disabled={cancelling != null}
                              onClick={() => cancelRow(t.id)}
                            >{cancelling === t.id ? 'Cancelling…' : 'Cancel'}</button>
                          </>
                        )}
                      </td>
                      <td>{money(t.filled_usd || t.requested_usd)}</td>
                      {blotTab === 'open' && (
                        <>
                          <td>{cents(m.mark)}</td>
                          <td className={(m.unrl ?? 0) > 0 ? 'pos' : (m.unrl ?? 0) < 0 ? 'neg' : ''}>
                            {signed(m.unrl)}
                          </td>
                        </>
                      )}
                      <td className={(t.pnl ?? 0) > 0 ? 'pos' : (t.pnl ?? 0) < 0 ? 'neg' : ''}>{money(t.pnl)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : <p style={{ opacity: 0.6 }}>{blotTab === 'open' ? 'No open positions.' : 'No settled trades yet.'}</p>}
      </div>
    </>
  )
}
