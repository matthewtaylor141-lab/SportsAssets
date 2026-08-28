import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { adminApi } from '../lib/api'
import {
  DESK_RELOCK_EVENT, deskAdminToken, deskApi, deskToken, deskUnlock,
  type DeskAccounts, type KPosition,
} from '../lib/desk'
import {
  PriceChart, useDeskHistory,
  type HistoryPoint, type HistoryVenue,
} from '../components/PriceChart'
import { MarketFeed, Spark, type FeedCard } from '../components/MarketFeed'
import { MarketPage, type MarketMeta } from '../components/MarketPage'
import '../styles/desk2.css'
import '../styles/desk10.css'

// Desk v8 (owner order 2026-08-22, via questionnaire): the desk IS the
// venue app — opening it shows a venue-style FEED of large market cards
// (MarketFeed.tsx); tapping a card opens a FULL MARKET PAGE shaped like
// the real app's market screen (MarketPage.tsx); Markets / Positions /
// Activity tabs mirror venue navigation (bottom bar on the phone). This
// file is now the SHELL and the single owner of every money path — the
// v5→v7 machinery is here VERBATIM: desk gate, accounts poll + LIVE
// strip, the entire order placement flow (placingRef, review→confirm,
// watchRow relay polling, execution timeline), the entire cash-out flow
// (coPlacingRef, watchCoRow, protective-limit modal), blotter + cancel,
// toasts/haptics. Only the navigation/layout around them changed.

export type Venue = 'polymarket' | 'kalshi'

export interface Outcome { label: string; asset?: string; ticker?: string; us_slug?: string; price: number | null; no_price?: number | null }
export interface GameGroup { name: string; markets: Outcome[] }
export interface Position {
  asset: string; outcome: string | null; cost: number; fill_price: number
  shares: number; status: string; current_value: number | null
  to_win: number | null; pnl: number | null
}
export interface GameView { id: string; venue: Venue; title: string; groups: GameGroup[]; positions: Position[] }

export interface Pick {
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
  // Display-grade only: the ask (buys) / bid (sells) shown at submit,
  // so the FILLED state can show fill-vs-quote slippage in cents.
  quotedPx?: number | null
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
export interface CoTarget {
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

export interface ManualTrade {
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

// ── v9: GET /api/admin/open-orders payload (PM resting book, reconciled
// against the venue on each read, + Kalshi pending queue) ─────────────
interface OOPmRow {
  id: number | string; us_market_slug: string; title?: string | null
  side: string; limit_price: number; requested_shares: number
  filled_shares: number; requested_usd: number; placed_at: string | null
  order_id: string
}
interface OOKRow {
  id: number | string; ticker: string; title?: string | null
  side: string; action: string; limit_price: number | null
  count: number | null; usd: number | null; status: string
  created_at: string | null
}
interface OpenOrdersPayload { polymarket: OOPmRow[]; kalshi: OOKRow[] }
/** One unified Open-orders row, either venue. Display-grade only. */
/** Venue slugs read like machine ids — surface the matchup instead
 * ("aec-atp-fritz-rune-2026-08-28" -> "Fritz Rune · ATP · 2026-08-28").
 * Display-only; the slug stays the identity everywhere else. */
function prettyOoSlug(slug: string): string {
  const m = slug.match(/^[a-z]+-([a-z0-9]+)-(.+?)(?:-(\d{4}-\d{2}-\d{2}))?(?:-[a-z0-9]*pt\d+)?$/)
  if (!m) return slug
  const names = (m[2] || '').split('-')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(' ')
  if (!names) return slug
  return `${names} · ${m[1].toUpperCase()}${m[3] ? ` · ${m[3]}` : ''}`
}

interface OORow {
  key: string; venue: Venue; id: number | string; title: string
  sub: string; px: number | null; filled: number; total: number | null
  usd: number | null; at: string | null; status: string
}

// ── v9: POST /api/admin/manual-limit run — a SEPARATE, additive state
// machine so the market ticket's OrderRun flow stays byte-identical. ──
type LimPhase = 'submitting' | 'open' | 'filled' | 'partial' | 'error'
interface LimRun {
  phase: LimPhase; t0: number; ms?: number
  px: number                    // requested limit, in cents
  restPx?: number | null        // server-confirmed limit_price (dollars)
  fillPrice?: number | null; filledShares?: number
  error?: string; title?: string
}

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
// v9: compact order age ("41s", "12m", "3h 05m", "2d") for Open orders.
const ageOf = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, '0')}m`
  return `${Math.floor(h / 24)}d`
}
const signed = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`
// Kalshi's published taker-fee formula, computed at the protective limit.
const kalshiFee = (count: number, price: number) =>
  0.07 * count * price * (1 - price)

// v8: the league list + lazy card-chart machinery (Spark) moved whole
// into components/MarketFeed.tsx — the feed owns browsing now.

// ── v10 venue chrome (owner order 2026-08-28: the desk is a PORTAL —
// indistinguishable from the venue itself). Wordmarks are drawn in
// code (text + tiny SVG glyph), no fetched assets. ──────────────────
function PmWordmark() {
  return (
    <span className="dxp-logo" aria-label="Polymarket portal">
      <svg className="glyph" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M12 2 3 7v10l9 5 9-5V7l-9-5Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <path d="M12 22V12M12 12 3 7M12 12l9-5" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      </svg>
      Polymarket
    </span>
  )
}
function KxWordmark() {
  return (
    <span className="dxp-logo" aria-label="Kalshi portal">
      <span className="glyph" aria-hidden>K</span>
      kalshi
    </span>
  )
}
function DxpIcon({ name }: { name: 'markets' | 'portfolio' | 'activity' }) {
  const common = {
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 1.8, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const, 'aria-hidden': true,
  }
  switch (name) {
    case 'markets':
      return <svg {...common}><path d="M4 17l4.2-4.2 3 3L19 8" /><path d="M14 8h5v5" /></svg>
    case 'portfolio':
      return <svg {...common}><rect x="3" y="6" width="18" height="13" rx="2.5" /><path d="M16 12.5h.01" /><path d="M3 9.5h18" /></svg>
    default:
      return <svg {...common}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>
  }
}

export function TradeDesk() {
  const [authed, setAuthed] = useState(() => deskToken() != null)
  const [pw, setPw] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [err, setErr] = useState('')
  const [venue, setVenue] = useState<Venue>('polymarket')
  const [league, setLeague] = useState('all')
  // v8 shell route: which desk screen is showing (venue-app tabs).
  const [tab, setTab] = useState<'markets' | 'positions' | 'activity'>('markets')
  const [game, setGame] = useState<GameView | null>(null)
  // Display-grade card meta (close time / volume / chartable id) handed
  // over by the feed card that opened the market page. Non-null marks
  // "market page open" even while the board detail is still loading.
  const [gameMeta, setGameMeta] = useState<MarketMeta | null>(null)
  const [bookOpen, setBookOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [pick, setPick] = useState<Pick | null>(null)
  const [usd, setUsd] = useState('')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [reviewing, setReviewing] = useState(false)
  const [run, setRun] = useState<OrderRun | null>(null)
  const [blotter, setBlotter] = useState<Blotter | null>(null)
  const [blotTab, setBlotTab] = useState<'open' | 'orders' | 'history'>('open')
  const [depth, setDepth] = useState<{ bids: number[][]; asks: number[][] } | null>(null)
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [pmResults, setPmResults] = useState<PMSearchMarket[]>([])
  const [kResults, setKResults] = useState<KalshiSearchRow[]>([])
  const [acct, setAcct] = useState<DeskAccounts | null>(null)
  const [acctAt, setAcctAt] = useState<number | null>(null)
  const [acctDown, setAcctDown] = useState(false)
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null)
  const [sheetDrag, setSheetDrag] = useState(0)
  const [co, setCo] = useState<CoTarget | null>(null)
  const [coQty, setCoQty] = useState('')
  const [coAll, setCoAll] = useState(true)
  const [coRun, setCoRun] = useState<OrderRun | null>(null)
  const [coBid, setCoBid] = useState<number | null>(null)
  const [cancelling, setCancelling] = useState<number | string | null>(null)
  const [cancelErr, setCancelErr] = useState('')
  // ── v9 additive state: PM limit ticket + unified Open-orders tab ──
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market')
  const [limitPx, setLimitPx] = useState('')       // cents string, 1-99
  const [limRun, setLimRun] = useState<LimRun | null>(null)
  const [oo, setOo] = useState<OpenOrdersPayload | null>(null)
  const [ooAt, setOoAt] = useState<number | null>(null)
  const [ooArmed, setOoArmed] = useState<string | null>(null)
  const [ooBusy, setOoBusy] = useState<string | null>(null)
  const [ooNote, setOoNote] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const pollRef = useRef<number | null>(null)
  const placingRef = useRef(false)
  const coPollRef = useRef<number | null>(null)
  const coPlacingRef = useRef(false)
  const coLinkDone = useRef(false)
  const toastTimer = useRef<number | null>(null)
  const dragYRef = useRef<number | null>(null)
  const prevRunPhase = useRef<Phase | null>(null)
  const prevCoPhase = useRef<Phase | null>(null)
  // v9 refs: limit-order dup guard + one-shot prefills.
  const limPlacingRef = useRef(false)
  const limPrefilled = useRef(false)
  const prevLimPhase = useRef<LimPhase | null>(null)
  const ooArmTimer = useRef<number | null>(null)
  const deepLinkDone = useRef(false)

  const isK = venue === 'kalshi'

  // ── Price history (Wave-2): venue + id straight from the pick ────
  // v8: the big market-page chart owns its own range toggles inside
  // MarketPage.tsx; the ticket's compact chart stays on a fixed 24h
  // window (fetchHistory's module cache dedupes any shared id).
  const histHours = 24
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
    deskApi<DeskAccounts>('/api/desk/accounts')
      .then((d) => { setAcct(d); setAcctAt(Date.now()); setAcctDown(false) })
      .catch(() => setAcctDown(true))
  }, [])

  // v9: open orders (PM resting book + Kalshi queue). Read-only GET.
  const loadOpenOrders = useCallback(() => {
    deskApi<OpenOrdersPayload>('/api/admin/open-orders')
      .then((d) => { setOo(d); setOoAt(Date.now()) })
      .catch(() => {})
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

  // v9: one read on unlock feeds the Open-orders badge; the 5s poll
  // runs ONLY while the Open-orders tab is actually on screen.
  useEffect(() => {
    if (!authed) return
    loadOpenOrders()
  }, [authed, loadOpenOrders])
  const ooVisible = authed && tab === 'activity' && blotTab === 'orders'
  useEffect(() => {
    if (!ooVisible) return
    loadOpenOrders()
    const t = window.setInterval(loadOpenOrders, 5000)
    return () => window.clearInterval(t)
  }, [ooVisible, loadOpenOrders])

  // v8: feed loading (desk-feed, 30s poll, league counts) lives inside
  // MarketFeed.tsx — the shell only owns the market-page detail fetch.

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

  // ── v8 navigation (presentation only — openGame stays the loader) ──
  const openFeedCard = (c: FeedCard) => {
    setGameMeta({
      close_time: c.close_time ?? null,
      volume_usd: c.volume_usd ?? null,
      history_id: c.history_id ?? null,
    })
    setQ('')
    openGame({ id: c.id, venue: c.venue })
  }
  // Kalshi search rows carry the full market ticker; its first two
  // segments ARE the desk-game event id (same derivation the board
  // uses), so a search result can open the real market page.
  const openFromKalshiSearch = (ticker: string) => {
    const id = ticker.split('-').slice(0, 2).join('-')
    if (!id) return
    setGameMeta({ close_time: null, volume_usd: null, history_id: ticker })
    setQ('')
    openGame({ id, venue: 'kalshi' })
  }
  const backToFeed = () => { setGame(null); setGameMeta(null) }
  const onMarketPage = game != null || gameMeta != null

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

  // v9: prefill the limit input once per pick with best bid + 1¢ the
  // moment a book is loaded — never overwrites anything already typed.
  useEffect(() => {
    if (isK || orderType !== 'limit' || limPrefilled.current) return
    if (limitPx !== '') { limPrefilled.current = true; return }
    const bb = depth?.bids?.[0]?.[0]
    if (bb == null) return
    limPrefilled.current = true
    setLimitPx(String(Math.min(99, Math.max(1, Math.round(bb * 100) + 1))))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depth, orderType, isK])

  // ── v9 deep-link prefill: another page drops a token/asset string
  // into sessionStorage 'sa_desk_prefill' (or links /desk?market=…)
  // and the desk runs its existing market search with it on arrival. ──
  useEffect(() => {
    if (!authed || deepLinkDone.current) return
    deepLinkDone.current = true
    let stored: string | null = null
    try {
      stored = sessionStorage.getItem('sa_desk_prefill')
      if (stored != null) sessionStorage.removeItem('sa_desk_prefill')
    } catch { stored = null }
    const fromUrl = searchParams.get('market')
    if (fromUrl != null) {
      const next = new URLSearchParams(searchParams)
      next.delete('market')
      setSearchParams(next, { replace: true })
    }
    const val = (stored || fromUrl || '').trim()
    if (!val) return
    setTab('markets')
    setGame(null); setGameMeta(null)
    setQ(val)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed, searchParams])

  // While an order is in flight, re-picking is locked (audit 2026-08-21):
  // choose() reset `run`, which un-hid the Trade button mid-submit — a
  // second click fired a second real-money POST, and the first order's
  // response then grafted onto (or was silently swallowed by) the new
  // ticket. The lock lifts the moment the run reaches a terminal phase.
  const inFlight = () =>
    run?.phase === 'submitting' || run?.phase === 'relaying'
  // v9: the limit ticket carries the same re-pick lock discipline.
  const limInFlight = () => limRun?.phase === 'submitting'
  const choose = (p: Pick) => {
    if (inFlight() || limInFlight()) return
    setPick(p); setRun(null); setReviewing(false); setSide('buy')
    setLimRun(null); setLimitPx(''); limPrefilled.current = false
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
    setRun({
      phase: 'submitting', t0, title: pick.label, outcome: pick.side,
      quotedPx: pick.ask > 0 ? pick.ask : null,
    })
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

  // ── v9: GTC limit order (PM ticket, Limit mode) ──────────────────
  // POST /api/admin/manual-limit — a NEW endpoint with its own state
  // machine. Same fail-closed grammar as the market ticket: synchronous
  // re-entry lock, no auto-retry ever, API error strings verbatim.
  // status 'open' means the order is RESTING on the venue's book (GTC).
  const placeLimit = async () => {
    if (!pick || pick.venue !== 'polymarket' || limPlacingRef.current || limInFlight()) return
    const amt = parseFloat(usd)
    const px = parseInt(limitPx || '', 10)
    if (!(amt > 0)) { setErr('Enter a dollar amount.'); return }
    if (!(Number.isFinite(px) && px >= 1 && px <= 99)) return
    limPlacingRef.current = true
    const t0 = performance.now()
    setLimRun({ phase: 'submitting', t0, px, title: pick.label })
    try {
      // Same identity rules as the market ticket: catalog picks carry
      // the token asset; venue-board rows carry the market slug.
      const body: Record<string, unknown> = {
        usd: amt, limit_price: px / 100,
        note: `${pick.label} — ${pick.side}`,
      }
      if (pick.asset) body.asset = pick.asset
      if (pick.usSlug) body.us_slug = pick.usSlug
      const r = await deskApi<any>('/api/admin/manual-limit', {
        method: 'POST', body: JSON.stringify(body),
        signal: AbortSignal.timeout(90000),
      })
      const ms = Math.round(performance.now() - t0)
      limPlacingRef.current = false
      loadBlotter()
      loadOpenOrders()
      if (r.ok && r.status === 'open') {
        setLimRun((prev) => prev && ({
          ...prev, phase: 'open', ms,
          restPx: r.limit_price ?? null,
          fillPrice: r.fill_price ?? null,
          filledShares: r.filled_shares || 0,
        }))
      } else if (r.ok && (r.status === 'filled' || r.status === 'settled'
        || (r.filled_shares ?? 0) > 0)) {
        const wanted = Math.floor(amt / (px / 100))
        const partial = r.status !== 'filled' && r.status !== 'settled'
          && wanted > 0 && (r.filled_shares || 0) < wanted
        setLimRun((prev) => prev && ({
          ...prev, phase: partial ? 'partial' : 'filled', ms,
          restPx: r.limit_price ?? null,
          fillPrice: r.fill_price ?? null,
          filledShares: r.filled_shares || 0,
        }))
        if (game) openGame({ id: game.id, venue: game.venue }, true)
      } else {
        setLimRun((prev) => prev && ({
          ...prev, phase: 'error', ms,
          error: r.error || (r.status ? `Order ${r.status}.` : 'Limit order refused.'),
        }))
      }
    } catch (e: any) {
      limPlacingRef.current = false
      loadBlotter()
      loadOpenOrders()
      setLimRun((prev) => prev && ({
        ...prev, phase: 'error',
        error: e?.name === 'TimeoutError'
          ? 'Still working after 90s — check Open orders before retrying.'
          : `Request failed (${e?.message || 'network'}) — check Open orders before retrying.`,
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
    setCoRun({ phase: 'submitting', t0, title: co.title, outcome: co.outcome, quotedPx: coBid })
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
        setCoRun((prev) => prev && ({
          ...prev, phase: 'relaying', rowId: r.row_id,
          quotedPx: r.quoted_bid ?? prev.quotedPx,
        }))
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
  // v9: the backend now enriches Kalshi positions with a human title —
  // prefer it wherever a raw ticker used to show (ticker stays as the
  // sub-line / identity key).
  const kTitle = (p: KPosition) =>
    (p as KPosition & { title?: string | null }).title || p.ticker
  const venuePositions: CoTarget[] = !acct ? [] : isK
    ? (acct.kalshi.positions || []).map((p) => ({
      venue: 'kalshi' as const, title: kTitle(p), ticker: p.ticker,
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
        venue: 'kalshi', title: kTitle(p), ticker: p.ticker, held: p.qty,
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

  // ── v9: unified Open-orders cancel — two-tap inline confirm, then
  // DELETE /api/desk/manual-order/{id}?venue=… (polymarket cancels the
  // resting book order; kalshi is the existing pending-queue cancel).
  // A cancel that raced a fill reports the fill — surfaced verbatim. ──
  const armOoCancel = (key: string) => {
    setOoArmed(key); setOoNote('')
    if (ooArmTimer.current != null) window.clearTimeout(ooArmTimer.current)
    ooArmTimer.current = window.setTimeout(() => setOoArmed(null), 4000)
  }
  useEffect(() => () => {
    if (ooArmTimer.current != null) window.clearTimeout(ooArmTimer.current)
  }, [])
  const ooCancel = async (row: OORow) => {
    if (ooBusy != null) return
    setOoBusy(row.key); setOoArmed(null); setOoNote('')
    try {
      const r = await deskApi<{ ok: boolean; cancelled?: boolean; filled_shares?: number; error?: string }>(
        `/api/desk/manual-order/${row.id}?venue=${row.venue}`, { method: 'DELETE' })
      if (r.ok && (r.filled_shares ?? 0) > 0) {
        setOoNote(`Cancel raced a fill — ${Math.round(r.filled_shares!)} contracts filled before the cancel landed${r.cancelled ? '; the remainder is cancelled' : ''}.`)
      } else if (r.ok && r.cancelled) {
        fireToast('Order cancelled — nothing filled.', true)
      } else {
        setOoNote(r.error || 'Not cancelled — the venue may have already filled it. List refreshed.')
      }
    } catch {
      setOoNote('Cancel failed — the order may still be resting. Check the list before retrying.')
    }
    setOoBusy(null)
    loadOpenOrders()
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

  // ── v9: limit-mode derived values (display only until placeLimit) ──
  const limPxNum = parseInt(limitPx || '', 10)
  const limPxOk = Number.isFinite(limPxNum) && limPxNum >= 1 && limPxNum <= 99
  const limPrice = limPxOk ? limPxNum / 100 : 0
  const limContracts = limPxOk && amount > 0 ? Math.floor(amount / limPrice) : 0
  const limCost = limContracts * limPrice
  const limBusy = limRun?.phase === 'submitting'
  const limReady = limPxOk && amount > 0 && limContracts > 0 && !limBusy
  const limitMode = !isK && orderType === 'limit'
  const stepPx = (d: number) => {
    const bb = depth?.bids?.[0]?.[0]
    const cur = Number.isFinite(limPxNum) ? limPxNum
      : bb != null ? Math.min(99, Math.max(1, Math.round(bb * 100) + 1)) : 50
    limPrefilled.current = true
    setLimitPx(String(Math.min(99, Math.max(1, cur + d))))
  }

  // ── Presentation pulses (v7 portal skin — no money-path changes) ──
  // 1s clock re-render drives the "synced Ns ago" age in the portal
  // strip; a 100ms ticker runs ONLY while an order is in flight and
  // feeds the live execution clock in the timeline.
  const [, setClockTick] = useState(0)
  useEffect(() => {
    if (!authed) return
    const t = window.setInterval(() => setClockTick((c) => c + 1), 1000)
    return () => window.clearInterval(t)
  }, [authed])

  const [execNow, setExecNow] = useState(0)
  useEffect(() => {
    if (!(busy || coBusy || limBusy)) return
    setExecNow(performance.now())
    const t = window.setInterval(() => setExecNow(performance.now()), 100)
    return () => window.clearInterval(t)
  }, [busy, coBusy, limBusy])

  // Result toasts + a 30ms haptic tap, observed from run/coRun phase
  // transitions — the placing/polling callbacks stay untouched.
  const fireToast = (msg: string, ok: boolean) => {
    if (toastTimer.current != null) window.clearTimeout(toastTimer.current)
    setToast({ msg, ok })
    toastTimer.current = window.setTimeout(() => setToast(null), 3800)
  }
  const buzz = () => {
    try { navigator.vibrate?.(30) } catch { /* not supported */ }
  }
  useEffect(() => () => {
    if (toastTimer.current != null) window.clearTimeout(toastTimer.current)
  }, [])

  useEffect(() => {
    const p = run?.phase ?? null
    if (p === prevRunPhase.current) return
    prevRunPhase.current = p
    if (p === 'filled' || p === 'partial') {
      buzz()
      fireToast(
        `${p === 'partial' ? 'Partially filled' : 'Filled'} · ${Math.round(run?.filledShares || 0)} @ ${cents(run?.fillPrice)}${run?.ms != null ? ` · ${(run.ms / 1000).toFixed(1)}s` : ''}`,
        true)
    } else if (p === 'unfilled') {
      buzz(); fireToast('Not filled — the book moved. Nothing was spent.', false)
    } else if (p === 'error') {
      buzz(); fireToast('Order not placed — see the ticket for details.', false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.phase])

  useEffect(() => {
    const p = coRun?.phase ?? null
    if (p === prevCoPhase.current) return
    prevCoPhase.current = p
    if (p === 'filled' || p === 'partial') {
      buzz()
      fireToast(
        `${p === 'partial' ? 'Partially cashed out' : 'Cashed out'} · ${Math.round(coRun?.filledShares || 0)} @ ${cents(coRun?.fillPrice)} · ${money(coRun?.filledUsd)}`,
        true)
    } else if (p === 'unfilled') {
      buzz(); fireToast('Not sold — the book moved. You still hold the position.', false)
    } else if (p === 'error') {
      buzz(); fireToast('Cash-out refused — see the modal for details.', false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coRun?.phase])

  // v9: limit-order result toasts (same observer pattern as above).
  useEffect(() => {
    const p = limRun?.phase ?? null
    if (p === prevLimPhase.current) return
    prevLimPhase.current = p
    if (p === 'open') {
      buzz()
      fireToast(
        `Resting on the book at ${limRun?.restPx != null ? cents(limRun.restPx) : `${limRun?.px}¢`} — manage under Open orders`,
        true)
    } else if (p === 'filled' || p === 'partial') {
      buzz()
      fireToast(
        `${p === 'partial' ? 'Partially filled' : 'Filled'} · ${Math.round(limRun?.filledShares || 0)} @ ${cents(limRun?.fillPrice)}${limRun?.ms != null ? ` · ${(limRun.ms / 1000).toFixed(1)}s` : ''}`,
        true)
    } else if (p === 'error') {
      buzz(); fireToast('Limit order not placed — see the ticket for details.', false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limRun?.phase])

  const acctAgeS = acctAt == null ? null : Math.max(0, Math.round((Date.now() - acctAt) / 1000))
  // Bottom-sheet dismissal (mobile scrim tap / drag-down). Locked while
  // any order is in flight — same visibility rule the CTA already uses.
  const sheetOpen = !!pick || side === 'sell'
  const dismissSheet = () => {
    if (busy || coBusy || limBusy) return
    setPick(null)
    if (side === 'sell') setSide('buy')
  }

  if (!authed) {
    // v10: the portal sign-in — the last BettorToken-branded screen
    // before the venue takes over the viewport.
    return (
      <div className="dxp dxp--pm">
        <div className="dxp-gate">
          <div className="dxp-gate-card">
            <div className="dxp-gate-venues">
              <i /> LIVE VENUE PORTAL · POLYMARKET + KALSHI
            </div>
            <h2>BettorToken Desk</h2>
            <p>
              Direct access to the live venue accounts. Every order placed
              here executes on the real exchange. Team password (admin
              token also accepted).
            </p>
            <input
              type="password" value={pw} placeholder="Desk password"
              autoCapitalize="none" autoCorrect="off" autoComplete="current-password" spellCheck={false}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && unlock(pw.trim())}
              aria-label="Desk password"
            />
            <button onClick={() => unlock(pw.trim())} disabled={unlocking}>
              {unlocking ? 'Unlocking…' : 'Enter the venue'}
            </button>
            {err && <p className="dxp-gate-err">{err}</p>}
          </div>
        </div>
      </div>
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
          <div className="dx-run-clock">
            <span>live execution clock</span>
            <b>{(Math.max(0, (execNow || performance.now()) - r.t0) / 1000).toFixed(2)}s</b>
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
          {r.quotedPx != null && r.fillPrice != null && (() => {
            const d = Math.round((r.fillPrice! - r.quotedPx!) * 100)
            const better = sell ? d > 0 : d < 0
            return (
              <div className="dx-run-slip">
                quoted {cents(r.quotedPx)} → filled {cents(r.fillPrice)} ·{' '}
                {d === 0
                  ? <b>on quote — zero slippage</b>
                  : (
                    <b className={better ? 'pos' : 'neg'}>
                      {Math.abs(d)}¢ {better ? 'price improvement' : 'slippage'}
                    </b>
                  )}
              </div>
            )
          })()}
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

  // ── v9: limit-order execution panel (separate machine — the market
  // execTimeline above stays untouched). 'open' = resting GTC. ───────
  const goOpenOrders = () => { setTab('activity'); setBlotTab('orders') }
  const limPanel = limRun && (
    <div className={`dx-run ${limRun.phase}`}>
      {limRun.phase === 'submitting' && (
        <>
          <div className="dx-run-row on">
            <span className="dx-spin" /> Limit order submitted
          </div>
          <div className="dx-run-row">
            <span className="dx-spin" /> AI counterparty placing on the Polymarket book…
          </div>
          <div className="dx-run-clock">
            <span>live execution clock</span>
            <b>{(Math.max(0, (execNow || performance.now()) - limRun.t0) / 1000).toFixed(2)}s</b>
          </div>
        </>
      )}
      {limRun.phase === 'open' && (
        <>
          <div className="dx-run-big v9-open">
            <span className="dx-check">✓</span> Resting on the book
          </div>
          <div className="dx-run-fill">
            Resting on the book at{' '}
            <b>{limRun.restPx != null ? cents(limRun.restPx) : `${limRun.px}¢`}</b>
            {' '}(good-til-cancelled)
            {(limRun.filledShares || 0) > 0 && (
              <> — <b>{Math.round(limRun.filledShares!)}</b> already filled
                {limRun.fillPrice != null ? <> @ <b>{cents(limRun.fillPrice)}</b></> : null}</>
            )} — manage under Open orders.
          </div>
          <button className="v9-goto" onClick={goOpenOrders}>View open orders →</button>
        </>
      )}
      {(limRun.phase === 'filled' || limRun.phase === 'partial') && (
        <>
          <div className="dx-run-big">
            <span className="dx-check">✓</span>
            {limRun.phase === 'partial' ? 'Partially filled' : 'Order filled'}
          </div>
          <div className="dx-run-fill">
            <b>{Math.round(limRun.filledShares || 0)}</b> contracts @{' '}
            <b>{cents(limRun.fillPrice)}</b>
            {' '}· {money((limRun.filledShares || 0) * (limRun.fillPrice || 0))}
          </div>
          {limRun.ms != null && (
            <div className="dx-run-lat">executed by the AI in {(limRun.ms / 1000).toFixed(1)}s</div>
          )}
        </>
      )}
      {limRun.phase === 'error' && (
        <>
          <div className="dx-run-big neg"><span className="dx-x">✕</span> Not placed</div>
          <div className="dx-run-fill">{limRun.error}</div>
        </>
      )}
    </div>
  )

  // ── Sell tab / positions panel content ───────────────────────────
  const sellList = (
    <div className="dx-sellpos">
      {!acct ? (
        <div className="dx-skel-rows bare" aria-label="Loading account positions">
          <div className="tr-skel" /><div className="tr-skel" /><div className="tr-skel" />
        </div>
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
              : `${money(acct.polymarket.trading_capital ?? acct.polymarket.cash)} trading capital · ${money(acct.polymarket.open_value)} open`}
          </span>
        )}
      </div>
      {isK && acct?.kalshi.degraded && (
        <div className="dx-acct-empty">
          Live Kalshi feed degraded — showing engine-known positions at cost, no marks.
        </div>
      )}
      {!acct ? (
        <div className="dx-skel-rows" aria-label="Loading account snapshot">
          <div className="tr-skel" /><div className="tr-skel" /><div className="tr-skel" />
        </div>
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
    <aside className={`vd-rail${sheetOpen ? ' has-pick' : ''}`}>
      {sheetOpen && <div className="dx-sheet-scrim" onClick={dismissSheet} aria-hidden="true" />}
      <div
        className={isK ? 'kx-ticket' : 'pmx-ticket'}
        // fresh DOM per open: the sheet is a promoted fixed layer, and
        // a rebuilt subtree sidesteps stale-raster compositors (the
        // v10 shot pass caught a capture of the pre-theme layer)
        key={sheetOpen ? 'sheet-open' : 'sheet-closed'}
        style={sheetDrag > 0 ? { transform: `translateY(${sheetDrag}px)`, transition: 'none' } : undefined}
      >
        <div
          className="dx-sheet-handle"
          aria-hidden="true"
          onTouchStart={(e) => { dragYRef.current = e.touches[0].clientY }}
          onTouchMove={(e) => {
            if (dragYRef.current == null) return
            const d = e.touches[0].clientY - dragYRef.current
            setSheetDrag(d > 0 ? d : 0)
          }}
          onTouchEnd={() => {
            const d = sheetDrag
            dragYRef.current = null
            setSheetDrag(0)
            if (d > 90) dismissSheet()
          }}
        >
          <span />
        </div>
        <div className="dx-tabs">
          <button className={`dx-tab${side === 'buy' ? ' on' : ''}`} onClick={() => setSide('buy')}>Buy</button>
          <button className={`dx-tab${side === 'sell' ? ' on' : ''}`} onClick={() => setSide('sell')}>Sell</button>
          {/* v9: PM ticket gets Polymarket's segmented Market/Limit pill;
              the Kalshi ticket keeps its static label. */}
          {!isK && side === 'buy' ? (
            <div className="v9-otype" role="group" aria-label="Order type">
              <button
                className={orderType === 'market' ? 'on' : ''}
                disabled={busy || limBusy}
                onClick={() => setOrderType('market')}
              >Market</button>
              <button
                className={orderType === 'limit' ? 'on' : ''}
                disabled={busy || limBusy}
                onClick={() => setOrderType('limit')}
              >Limit</button>
            </div>
          ) : (
            <span className="dx-tab-right">Market ▾</span>
          )}
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
                disabled={busy || limBusy}
                onChange={(e) => setUsd(
                  // Comma is a decimal point, never deleted: stripping it
                  // turned "12,50" into a $1,250 request (audit 2026-08-21).
                  e.target.value.replace(/,/g, '.').replace(/[^0-9.]/g, ''))}
                aria-label="Dollar amount"
              />
              <div className="dx-quick">
                {[1, 20, 100].map((v) => (
                  <button key={v} disabled={busy || limBusy} onClick={() => setUsd(String((amount > 0 ? amount : 0) + v))}>
                    +${v}
                  </button>
                ))}
                <button
                  disabled={busy || limBusy || available == null}
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
            ) : limitMode ? (
              <>
                {/* v9: Limit mode — price in cents (1-99), GTC. */}
                <div className="v9-limit">
                  <span className="dx-amount-label">Limit price</span>
                  <div className="v9-limitbox">
                    <button
                      className="v9-step" disabled={limBusy}
                      onClick={() => stepPx(-1)} aria-label="Lower limit price 1 cent"
                    >−</button>
                    <div className="v9-pxwrap">
                      <input
                        className="v9-px" inputMode="numeric"
                        value={limitPx}
                        placeholder={bestBid != null
                          ? String(Math.min(99, Math.max(1, Math.round(bestBid * 100) + 1)))
                          : '50'}
                        disabled={limBusy}
                        onChange={(e) => {
                          limPrefilled.current = true
                          setLimitPx(e.target.value.replace(/[^0-9]/g, '').slice(0, 2))
                        }}
                        aria-label="Limit price in cents"
                      />
                      <span className="v9-cent">¢</span>
                    </div>
                    <button
                      className="v9-step" disabled={limBusy}
                      onClick={() => stepPx(1)} aria-label="Raise limit price 1 cent"
                    >+</button>
                  </div>
                  {limitPx !== '' && !limPxOk && (
                    <p className="dxm-err">Limit price must be 1–99¢.</p>
                  )}
                </div>
                {(bestBid != null || bestAsk != null) && (
                  <div className="dx-line sub">
                    <span>Best bid {cents(bestBid)} · best ask {cents(bestAsk)}</span>
                    {limPxOk && bestAsk != null && limPxNum >= Math.round(bestAsk * 100) && (
                      <span>crosses the ask — fills now</span>
                    )}
                  </div>
                )}
                <div className="dx-line"><span>Shares (floor)</span><b>{limContracts > 0 ? limContracts : '—'}</b></div>
                <div className="dx-line"><span>Est. cost</span><b>{limContracts > 0 ? money(limCost) : '—'}</b></div>
                <div className="dx-line">
                  <span>To win</span>
                  <b className="dx-big pos">{limContracts > 0 ? money(limContracts) : '$0'}
                    {limContracts > 0 && limCost > 0 && (
                      <span className="dx-ret"> (+{Math.round(((limContracts - limCost) / limCost) * 100)}%)</span>
                    )}</b>
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
              <div className="dx-bookwrap">
                {/* v8: the ladder is collapsible like the venue apps'
                    market screens — wrapper only, ladder untouched. */}
                <button
                  className="dx-booktoggle"
                  onClick={() => setBookOpen((o) => !o)}
                  aria-expanded={bookOpen}
                >
                  Order book
                  {bestBid != null && bestAsk != null && (
                    <span>spread {((bestAsk - bestBid) * 100).toFixed(1)}¢</span>
                  )}
                  <i className={bookOpen ? 'up' : ''}>▾</i>
                </button>
                {bookOpen && (() => {
                  // v9: full venue depth — every level the book returns
                  // (up to 10/side), each with a background bar scaled to
                  // the CUMULATIVE size from best price (venue-app look).
                  // Display only; same 5s poll, same payload.
                  const cum = (rows: number[][]) => {
                    let c = 0
                    return rows.map(([p, s]) => ({ p, s, c: c += s }))
                  }
                  const askLvls = cum(depth.asks)
                  const bidLvls = cum(depth.bids)
                  const maxC = Math.max(
                    askLvls.length ? askLvls[askLvls.length - 1].c : 0,
                    bidLvls.length ? bidLvls[bidLvls.length - 1].c : 0,
                    1)
                  return (
              <div className="dx-book">
                <div>
                  <div className="dx-book-h">Asks</div>
                  {askLvls.map((l, i) => (
                    <div className="dx-book-row v9-lvl" key={i}>
                      <i
                        className="v9-dbar ask" aria-hidden="true"
                        style={{ width: `${Math.min(100, (l.c / maxC) * 100)}%` }}
                      />
                      <span className="neg">{cents(l.p)}</span>
                      <span>{Math.round(l.s).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="dx-book-h">Bids</div>
                  {bidLvls.map((l, i) => (
                    <div className="dx-book-row v9-lvl" key={i}>
                      <i
                        className="v9-dbar bid" aria-hidden="true"
                        style={{ width: `${Math.min(100, (l.c / maxC) * 100)}%` }}
                      />
                      <span className="pos">{cents(l.p)}</span>
                      <span>{Math.round(l.s).toLocaleString()}</span>
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
                  )
                })()}
              </div>
            )}

            {limitMode ? limPanel : runPanel}

            {limitMode ? (
              <>
                {/* v9: manual-limit CTA — its own dup-guarded flow; the
                    market Trade button below stays byte-identical. */}
                {(!limRun || limRun.phase === 'error') && (
                  <button
                    className={`pmx-cta${limReady ? ' ready' : ''}`}
                    disabled={!limReady}
                    onClick={placeLimit}
                  >{limReady
                    ? `Place limit order · ${limContracts} @ ${limPxNum}¢`
                    : 'Place limit order'}</button>
                )}
                {(limRun?.phase === 'open' || limRun?.phase === 'filled'
                  || limRun?.phase === 'partial') && (
                  <button
                    className="pmx-cta ready"
                    onClick={() => { setLimRun(null); setUsd('') }}
                  >Place another order</button>
                )}
              </>
            ) : (
              <>
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
              </>
            )}

            <p className="dx-fine">
              {limitMode
                ? <>Good-til-cancelled: your order rests on the venue book at
                  your price until it fills or you cancel it — manage it any
                  time under Activity → Open orders.</>
                : <>Fill up to your amount at your price or better — never worse.
                  Unfilled remainder cancels instantly. Cash out any time from the Sell tab.</>}
            </p>
          </>
        )}
      </div>
    </aside>
  )

  // v8: the old kCard/pCard browse cards and the inline gameView were
  // replaced by MarketFeed.tsx (venue-style feed cards) and
  // MarketPage.tsx (full venue-style market page).

  // ── Search results ───────────────────────────────────────────────
  const searchBlock = q.trim().length >= 2 && (
    searching && pmResults.length === 0 && kResults.length === 0 ? (
      <div className="tr-skel" style={{ height: 140, borderRadius: 12 }} />
    ) : isK ? (
      <div className="kx-list">
        {kResults.map((m) => (
          <div className="kx-card" key={m.ticker}>
            <div className="kx-card-top"><span className="kx-sport">SEARCH</span></div>
            <button className="kx-title" onClick={() => openFromKalshiSearch(m.ticker)}>
              {m.title.replace(' Winner?', '')}
            </button>
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

  // ── v9: unified Open-orders rows (PM resting book, reconciled with
  // the venue on each read, + Kalshi pending queue), newest first. ────
  const ooRows: OORow[] = !oo ? [] : [
    ...(oo.polymarket || []).map((r): OORow => ({
      key: `pm-${r.id}`, venue: 'polymarket', id: r.id,
      title: r.title || prettyOoSlug(r.us_market_slug),
      sub: (r.side || 'buy').toUpperCase(),
      px: r.limit_price ?? null, filled: r.filled_shares || 0,
      total: r.requested_shares ?? null, usd: r.requested_usd ?? null,
      at: r.placed_at, status: 'resting',
    })),
    ...(oo.kalshi || []).map((r): OORow => ({
      key: `k-${r.id}`, venue: 'kalshi', id: r.id,
      title: r.title || r.ticker,
      sub: `${(r.action || 'buy').toUpperCase()} ${(r.side || 'yes').toUpperCase()}`,
      px: r.limit_price ?? null, filled: 0, total: r.count ?? null,
      usd: r.usd ?? null, at: r.created_at, status: r.status || 'queued',
    })),
  ].sort((a, b) =>
    (b.at ? new Date(b.at).getTime() : 0) - (a.at ? new Date(a.at).getTime() : 0))
  const ooCount = ooRows.length
  const ooAgeS = ooAt == null ? null : Math.max(0, Math.round((Date.now() - ooAt) / 1000))

  // ── v10 venue chrome derived values ──────────────────────────────
  const pmPortfolio = acct
    ? (acct.polymarket.account_value
      ?? acct.polymarket.trading_capital ?? acct.polymarket.cash) : null
  const kxPortfolio = acct
    ? (acct.kalshi.balance_usd != null
      ? acct.kalshi.balance_usd + (acct.kalshi.exposure_usd ?? 0) : null) : null
  const portfolioVal = isK ? kxPortfolio : pmPortfolio
  const cashVal = isK ? acct?.kalshi.balance_usd
    : (acct?.polymarket.cash ?? acct?.polymarket.trading_capital)
  const switchVenue = (v: Venue) => {
    if (inFlight() || limInFlight()) return
    setVenue(v); setGame(null); setPick(null); setQ(''); setRun(null)
    setGameMeta(null)
    setLimRun(null); setLimitPx(''); setOrderType('market')
    limPrefilled.current = false
  }
  const goMarkets = () => { setTab('markets'); setGame(null); setGameMeta(null) }

  return (
    // key={venue}: a venue switch rebuilds the whole portal DOM — the
    // sticky nav lives on its own compositor layer and a pure CSS-var
    // swap can leave the OLD venue's paint on screen (observed in the
    // v10 shot pass: Kalshi nav rendering Polymarket's dark layer).
    <div className={`dxp ${isK ? 'dxp--kx' : 'dxp--pm'}`} key={venue}>
      {/* ── the ONLY non-venue chrome: a 26px portal strip ── */}
      <div className="dxp-portal" role="status">
        <span className="dxp-live"><i />BETTORTOKEN PORTAL</span>
        <span className="dxp-note">
          {acctDown ? (
            <>reconnecting to the live {isK ? 'Kalshi' : 'Polymarket'} account feed…</>
          ) : (
            <>orders execute on the <b>real {isK ? 'Kalshi' : 'Polymarket'} account</b>
              {' '}· manual sleeve
              {blotter && (
                <> · today <b>{money(blotter.day_spent)}</b> of {money(blotter.day_budget)}
                  {' '}· max {money(blotter.max_per_order)}/order</>
              )}</>
          )}
        </span>
        <span className="dxp-sync">
          {acctDown ? 'retrying' : acctAgeS == null ? 'syncing…'
            : acctAgeS < 3 ? 'synced now' : `synced ${acctAgeS}s`}
        </span>
        <div className="dxp-modes" role="group" aria-label="Venue">
          {(['polymarket', 'kalshi'] as Venue[]).map((v) => (
            <button
              key={v}
              className={venue === v ? 'on' : ''}
              onClick={() => switchVenue(v)}
            >{v === 'polymarket' ? 'Polymarket' : 'Kalshi'}</button>
          ))}
        </div>
        <Link className="dxp-exit" to="/" aria-label="Exit the venue portal">✕</Link>
      </div>

      {/* ── venue top nav (wordmark, search, sections, account) ── */}
      <header className="dxp-nav">
        {isK ? <KxWordmark /> : <PmWordmark />}
        <div className="dxp-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
          </svg>
          <input
            type="search" value={q}
            placeholder={`Search ${isK ? 'Kalshi' : 'Polymarket'} markets`}
            onChange={(e) => {
              setQ(e.target.value)
              if (e.target.value.trim().length >= 2 && tab !== 'markets') setTab('markets')
            }}
            aria-label="Search markets"
          />
          {q !== '' && (
            <button className="clr" onClick={() => setQ('')} aria-label="Clear search">✕</button>
          )}
        </div>
        <nav className="dxp-links" aria-label="Venue sections">
          <button className={tab === 'markets' ? 'on' : ''} onClick={goMarkets}>
            Markets
          </button>
          <button className={tab === 'positions' ? 'on' : ''} onClick={() => setTab('positions')}>
            Portfolio
            {venuePositions.length > 0 && <span className="n">{venuePositions.length}</span>}
          </button>
          <button className={tab === 'activity' ? 'on' : ''} onClick={() => setTab('activity')}>
            Activity
            {ooCount + openTrades.length > 0 && (
              <span className="n">{ooCount + openTrades.length}</span>
            )}
          </button>
        </nav>
        <div className="dxp-acct">
          <span className={`dxp-stat hide-m${(portfolioVal ?? 0) > (cashVal ?? 0) ? ' gain' : ''}`}>
            <small>Portfolio</small><b>{money(portfolioVal)}</b>
          </span>
          <span className="dxp-stat">
            <small>Cash</small><b>{money(cashVal)}</b>
          </span>
          <Link to="/accounts" className="dxp-deposit">Deposit</Link>
          <span className="dxp-avatar" title="BettorToken desk session">BT</span>
        </div>
      </header>

      <div className="dxp-body">

      {/* the venue surface only exists where venue content renders —
          on Activity it painted an empty white pill (v9 shot pass) */}
      {tab !== 'activity' && (
      <div className={`vdesk vdesk--v8 ${isK ? 'vdesk--kx' : 'vdesk--pmx'}`}>
        {tab === 'markets' && (
          <div className={`v8-body${onMarketPage || sheetOpen ? ' rail-on' : ''}`}>
            <main className="v8-main">
              {q.trim().length >= 2 ? searchBlock : onMarketPage ? (
                <MarketPage
                  venue={venue}
                  game={game}
                  meta={gameMeta}
                  loading={loading}
                  pick={pick}
                  choose={choose}
                  onBack={backToFeed}
                  positions={venuePositions}
                  openCashOut={openCashOut}
                  coBusy={coBusy}
                  trades={blotter?.trades || []}
                />
              ) : (
                <MarketFeed
                  venue={venue}
                  league={league}
                  onLeague={setLeague}
                  pick={pick}
                  choose={choose}
                  onOpen={openFeedCard}
                  sparkCache={sparkCache}
                />
              )}
              {err && q.trim().length < 2 && <p className="vd-empty">{err}</p>}
            </main>
            {rail}
          </div>
        )}
        {tab === 'positions' && (
          <div className="v8-body">
            <main className="v8-main">{acctPanel}</main>
          </div>
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
      )}

      {tab === 'activity' && (
      <div className="dxp-activity">
        <div className="dx-blot-tabs">
          <button className={blotTab === 'open' ? 'on' : ''} onClick={() => setBlotTab('open')}>
            Open {openTrades.length ? `(${openTrades.length})` : ''}
          </button>
          <button className={blotTab === 'orders' ? 'on' : ''} onClick={() => setBlotTab('orders')}>
            Open orders
            {ooCount > 0 && <span className="v8-tab-n">{ooCount}</span>}
          </button>
          <button className={blotTab === 'history' ? 'on' : ''} onClick={() => setBlotTab('history')}>
            History
          </button>
        </div>
        {blotTab === 'orders' ? (
          /* ── v9: unified Open-orders view — PM resting limit orders
             (reconciled against the venue book on every read) plus the
             Kalshi pending queue, with two-tap inline cancel. ── */
          <div className="v9-oo">
            <div className="v9-oo-head">
              <span className="pulse-dot" aria-hidden="true" />
              <span>Working orders on the venue books — reconciled live</span>
              <span className="v9-oo-age">
                {ooAgeS == null ? 'syncing…'
                  : ooAgeS < 3 ? 'checked just now' : `checked ${ooAgeS}s ago`}
              </span>
            </div>
            {ooNote && <p className="dk-gate-err">{ooNote}</p>}
            {!oo ? (
              <div className="v9-oo-skel" aria-label="Loading open orders">
                <div className="skel" /><div className="skel" /><div className="skel" />
              </div>
            ) : ooRows.length === 0 ? (
              <p style={{ opacity: 0.6 }}>
                No working orders. Limit orders resting on the book and queued
                Kalshi tickets appear here the moment they're live.
              </p>
            ) : (
              <div className="v9-oo-list">
                <div className="oo-row v9-oo-h" aria-hidden="true">
                  <span>Market</span><span>Side</span><span>Limit</span>
                  <span>Filled / size</span><span />
                </div>
                {ooRows.map((r) => (
                  <div className="oo-row" key={r.key}>
                    <div className="v9-oo-mkt">
                      <span className="v9-oo-title">{r.title}</span>
                      <small>
                        <span className={`v9-chip ${r.venue === 'kalshi' ? 'venue-k' : 'venue-pm'}`}>
                          {r.venue === 'kalshi' ? 'Kalshi' : 'Polymarket'}
                        </span>
                        <span>{ageOf(r.at)} old · {r.status}</span>
                      </small>
                    </div>
                    <span className="v9-oo-side">{r.sub}</span>
                    <span className="v9-oo-px">{cents(r.px)}</span>
                    <span className="v9-oo-size">
                      {r.total != null
                        ? `${Math.round(r.filled)}/${Math.round(r.total)}`
                        : r.usd != null ? money(r.usd) : '—'}
                    </span>
                    <span className="v9-oo-act">
                      {ooArmed === r.key ? (
                        <span className="v9-oo-conf">
                          <small>Cancel?</small>
                          <button
                            className="oo-cancel v9-armed"
                            disabled={ooBusy != null}
                            onClick={() => ooCancel(r)}
                          >Yes</button>
                        </span>
                      ) : (
                        <button
                          className="oo-cancel"
                          disabled={ooBusy != null}
                          onClick={() => armOoCancel(r.key)}
                        >{ooBusy === r.key ? 'Cancelling…' : 'Cancel'}</button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
        <>
        {cancelErr && blotTab === 'open' && <p className="dk-gate-err">{cancelErr}</p>}
        {blotRows.length > 0 ? (
          <>
          {/* Phone: same rows as touch cards (table hidden <720px). */}
          <div className="dx-blot-cards">
            {blotRows.map((t) => {
              const m = blotTab === 'open' ? rowMark(t) : { mark: null, unrl: null }
              return (
                <div className="dx-blot-card" key={t.id}>
                  <div className="dx-blot-card-top">
                    <b>{t.title}</b>
                    <span className={`dx-blot-st ${t.status}`}>{t.status}</span>
                  </div>
                  <div className="dx-blot-card-sub">
                    {t.outcome || '—'} · {t.venue || 'polymarket'}
                    {t.placed_at ? ` · ${new Date(t.placed_at).toLocaleTimeString()}` : ''}
                  </div>
                  <div className="dx-blot-nums">
                    <span>Cost <b>{money(t.filled_usd || t.requested_usd)}</b></span>
                    {blotTab === 'open' && <span>Mark <b>{cents(m.mark)}</b></span>}
                    {blotTab === 'open' && (
                      <span>Unrl <b className={(m.unrl ?? 0) > 0 ? 'pos' : (m.unrl ?? 0) < 0 ? 'neg' : ''}>
                        {signed(m.unrl)}
                      </b></span>
                    )}
                    <span>P&L <b className={(t.pnl ?? 0) > 0 ? 'pos' : (t.pnl ?? 0) < 0 ? 'neg' : ''}>
                      {money(t.pnl)}
                    </b></span>
                  </div>
                  {t.error && <div className="dx-blot-err">{t.error.slice(0, 80)}</div>}
                  {blotTab === 'open' && cancellable(t) && (
                    <button
                      className="dx-cancel"
                      disabled={cancelling != null}
                      onClick={() => cancelRow(t.id)}
                    >{cancelling === t.id ? 'Cancelling…' : 'Cancel'}</button>
                  )}
                </div>
              )
            })}
          </div>
          <div className="rpt-table-wrap dx-blot-table">
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
          </>
        ) : !blotter ? (
          <div className="dx-skel-rows bare" aria-label="Loading trades">
            <div className="tr-skel" /><div className="tr-skel" /><div className="tr-skel" />
          </div>
        ) : <p style={{ opacity: 0.6 }}>{blotTab === 'open' ? 'No open positions.' : 'No settled trades yet.'}</p>}
        </>
        )}
      </div>
      )}

      </div>{/* /.dxp-body */}

      {/* ── mobile: the venue's own bottom nav ── */}
      <nav className="dxp-tabbar" aria-label="Venue sections">
        <button className={tab === 'markets' ? 'on' : ''} onClick={goMarkets}>
          <DxpIcon name="markets" />
          <span>Markets</span>
        </button>
        <button className={tab === 'positions' ? 'on' : ''} onClick={() => setTab('positions')}>
          <DxpIcon name="portfolio" />
          <span>Portfolio</span>
          {venuePositions.length > 0 && <span className="n">{venuePositions.length}</span>}
        </button>
        <button className={tab === 'activity' ? 'on' : ''} onClick={() => setTab('activity')}>
          <DxpIcon name="activity" />
          <span>Activity</span>
          {ooCount + openTrades.length > 0 && (
            <span className="n">{ooCount + openTrades.length}</span>
          )}
        </button>
      </nav>

      {toast && (
        <div className={`dx-toast${toast.ok ? '' : ' bad'}`} role="status">{toast.msg}</div>
      )}
    </div>
  )
}
