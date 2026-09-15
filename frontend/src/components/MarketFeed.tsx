// Desk v8 (owner order 2026-08-22): the venue-style FEED — the desk
// home is a scrolling feed of LARGE market cards (chart preview, live
// prices, volume, league chips), the shape of the real venue apps'
// home screens. Data: GET /api/admin/desk-feed (30s poll), which is
// built on the desk-games internals server-side; until that endpoint
// deploys the feed falls back to /api/admin/desk-games mapped into the
// same card shape (volume/close null — never invented). Pure browse
// surface: outcome buttons hand a Pick UP to TradeDesk's choose() and
// card taps hand the card up to the market page — no money logic here.

import { useEffect, useMemo, useRef, useState } from 'react'
import { deskApi } from '../lib/desk'
import {
  Sparkline, fetchHistory,
  type HistoryPoint, type HistoryVenue,
} from './PriceChart'
import type { Pick as DeskPick, Venue } from '../pages/TradeDesk'

export const LEAGUES: { key: string; label: string; icon: string }[] = [
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
// v10 venue card anatomy: the icon tile every venue card leads with.
const LEAGUE_ICON: Record<string, string> = {
  mlb: '⚾', wnba: '🏀', nba: '🏀', nfl: '🏈', nhl: '🏒',
  tennis: '🎾', soccer: '⚽', esports: '🎮', everything: '🌐',
}

// ── Lazy card charts (Wave-2 machinery, relocated from TradeDesk) ──
// A card only fetches history once it scrolls into view
// (IntersectionObserver, 120px lookahead), through a 12-slot limiter so
// a big feed never floods the API, into a ref-map cache owned by the
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

export function Spark({ venue, id, cacheRef, wide }: {
  venue: HistoryVenue
  id: string
  cacheRef: { current: Map<string, HistoryPoint[]> }
  /** v8: full-card-width × 56px feed chart, tinted by trend. */
  wide?: boolean
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
  const tone = wide && pts && pts.length >= 2
    ? (pts[pts.length - 1].p >= pts[0].p ? 'pos' as const : 'neg' as const)
    : undefined
  return (
    <span className={wide ? 'mf-spark' : 'pc-spark'} ref={boxRef}>
      {pts && pts.length >= 2
        ? (wide
            ? <Sparkline points={pts} w={344} h={56} tone={tone} />
            : <Sparkline points={pts} />)
        : null}
    </span>
  )
}

// ── Feed card shape (DESK v8 CONTRACT) ─────────────────────────────
export interface FeedOutcome {
  label: string
  /** pm token (asset) or kalshi ticker */
  id: string
  price: number | null
  /** carried through the desk-games fallback so PM orders keep working */
  us_slug?: string
}
export interface FeedCard {
  id: string
  venue: Venue
  title: string
  league: string
  volume_usd: number | null
  close_time: string | null
  outcomes: FeedOutcome[]
  history_id: string | null
  markets_n?: number
}

/** PM CLOB token ids are long digit strings; venue slugs never are. */
export const TOKEN_RE = /^\d{6,}$/

const cents = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}¢`)
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const fmtVol = (v: number | null | undefined): string | null =>
  v == null ? null
    : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M Vol`
      : v >= 1e3 ? `$${Math.round(v / 1e3)}K Vol`
        : `$${Math.round(v)} Vol`
const fmtClose = (iso: string | null | undefined): string | null => {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const sameDay = d.toDateString() === new Date().toDateString()
  // Owner report 2026-08-29: a bare "Sep 1" told the team nothing —
  // game cards need the day AND the start time to be operable.
  return sameDay
    ? `Today · ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
    : `${d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })} · ${
        d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

// desk-games card → feed card (fallback path only; volume/close stay
// null — the desk never invents numbers).
interface LegacyGame {
  id: string; venue: Venue; league: string; title: string; markets_n?: number
  outcomes: { label: string; asset?: string; ticker?: string; us_slug?: string; price: number | null }[]
}
const mapLegacy = (g: LegacyGame): FeedCard => ({
  id: g.id, venue: g.venue, title: g.title, league: g.league,
  volume_usd: null, close_time: null, markets_n: g.markets_n,
  outcomes: (g.outcomes || []).map((o) => ({
    label: o.label, id: o.ticker || o.asset || '', price: o.price, us_slug: o.us_slug,
  })),
  history_id: (g.outcomes || []).map((o) => o.ticker || o.asset).find(Boolean) || null,
})

// Last-good cards per (venue,league) so returning from a market page
// paints instantly instead of flashing a skeleton; the 30s poll then
// refreshes in place.
const feedCache = new Map<string, { cards: FeedCard[]; counts?: Record<string, number> }>()

// Cached-first paint (owner report 2026-08-29: "the desk takes forever
// to load"): the in-memory cache died on every reload, so a mobile
// visit always opened to blank skeletons while the venue listing was
// fetched. The last good feed now persists per session and paints
// INSTANTLY on open; the network fetch revalidates behind it.
const FEED_SS_KEY = 'sa_desk_feed'
try {
  const raw = sessionStorage.getItem(FEED_SS_KEY)
  if (raw) {
    for (const [k, v] of Object.entries(JSON.parse(raw))) {
      feedCache.set(k, v as { cards: FeedCard[]; counts?: Record<string, number> })
    }
  }
} catch { /* cold start or blocked storage — fetch path unaffected */ }
const persistFeed = () => {
  try {
    sessionStorage.setItem(FEED_SS_KEY,
      JSON.stringify(Object.fromEntries(feedCache)))
  } catch { /* quota/blocked — memory cache still works */ }
}

export function MarketFeed({ venue, league, onLeague, pick, choose, onOpen, sparkCache }: {
  venue: Venue
  league: string
  onLeague: (l: string) => void
  pick: DeskPick | null
  choose: (p: DeskPick) => void
  onOpen: (c: FeedCard) => void
  sparkCache: { current: Map<string, HistoryPoint[]> }
}) {
  const isK = venue === 'kalshi'
  const cacheKey = `${venue}|${league}`
  const [cards, setCards] = useState<FeedCard[] | null>(
    () => feedCache.get(cacheKey)?.cards ?? null)
  const [counts, setCounts] = useState<Record<string, number>>(
    () => feedCache.get(cacheKey)?.counts ?? {})
  const [err, setErr] = useState('')
  const [flt, setFlt] = useState('')

  useEffect(() => {
    let dead = false
    const key = `${venue}|${league}`
    const hit = feedCache.get(key)
    setCards(hit ? hit.cards : null)
    if (hit?.counts) setCounts(hit.counts)
    setErr('')
    const apply = (cs: FeedCard[], cn?: Record<string, number>) => {
      feedCache.set(key, { cards: cs, counts: cn })
      persistFeed()
      if (dead) return
      setCards(cs)
      if (cn) setCounts(cn)
      setErr(cs.length ? '' : 'No markets in this window — try another category.')
    }
    const load = () => {
      deskApi<{ cards: FeedCard[]; counts?: Record<string, number> }>(
        `/api/admin/desk-feed?venue=${venue}&league=${league}`)
        .then((r) => apply(r.cards || [], r.counts))
        .catch(() =>
          // Feed endpoint unreachable (or not deployed yet): the board
          // cards keep the desk alive in the same card grammar.
          deskApi<{ games: LegacyGame[]; counts?: Record<string, number> }>(
            `/api/admin/desk-games?venue=${venue}&league=${league}`)
            .then((r) => apply((r.games || []).map(mapLegacy), r.counts))
            .catch(() => { if (!dead) setErr('Markets failed to load — retrying in 30s.') }))
    }
    load()
    const t = window.setInterval(load, 30_000)
    return () => { dead = true; window.clearInterval(t) }
  }, [venue, league])

  const hv: HistoryVenue = isK ? 'kalshi' : 'polymarket-us'
  // A PM outcome id may be a CLOB token (contract shape) or the venue
  // us_slug (what the live listing actually carries) — tokens are long
  // digit strings, slugs never are. Orders must put each in the right
  // Pick field: asset feeds the book/history/API asset param, us_slug
  // feeds the manual-trade slug param.
  const pmParts = (o: FeedOutcome) => TOKEN_RE.test(o.id)
    ? { asset: o.id, usSlug: o.us_slug }
    : { asset: undefined, usSlug: o.us_slug || o.id || undefined }
  const sel = (o: FeedOutcome) => {
    if (isK) return !!o.id && pick?.ticker === o.id
    const p = pmParts(o)
    return (!!p.asset && pick?.asset === p.asset)
      || (!!p.usSlug && pick?.usSlug === p.usSlug)
  }
  const buyable = (o: FeedOutcome) =>
    isK ? o.price != null : (o.price != null || !!o.us_slug || !!o.id)
  const buy = (c: FeedCard, o: FeedOutcome) => {
    if (!buyable(o)) return
    if (isK) {
      choose({
        venue: 'kalshi', label: c.title, side: o.label,
        ask: o.price as number, ticker: o.id, kalshiSide: 'yes',
      })
    } else {
      choose({
        venue: 'polymarket', label: c.title, side: o.label,
        ask: o.price ?? 0, ...pmParts(o),
      })
    }
  }

  const card = (c: FeedCard) => {
    const volTxt = fmtVol(c.volume_usd)
    const closeTxt = fmtClose(c.close_time)
    const two = c.outcomes.length === 2
    const icon = LEAGUE_ICON[c.league] || '📊'
    // v10: an explicit Yes/No market (single outcome, or Yes-labelled)
    // gets the venue's "chance" dial; a matchup gets side buttons.
    const single = c.outcomes.length === 1
    const chance = single ? c.outcomes[0]?.price ?? null : null
    // Kalshi cards carry the venue's mini price chart; Polymarket event
    // cards don't chart on the browse grid — matching each venue.
    const chartId = isK && c.history_id ? c.history_id : null
    const arc = chance != null ? Math.max(0.02, Math.min(1, chance)) : 0
    return (
      <article className="mf-card" key={`${c.venue}-${c.id}`}>
        <header
          className="mf-head" role="button" tabIndex={0}
          onClick={() => onOpen(c)}
          onKeyDown={(e) => { if (e.key === 'Enter') onOpen(c) }}
        >
          <span className="mf-ico" aria-hidden>{icon}</span>
          <h3 className="mf-title">{c.title}</h3>
          {chance != null && (
            <span className="mf-chance" aria-label={`${Math.round(chance * 100)}% chance`}>
              <svg width="54" height="28" viewBox="0 0 54 28" aria-hidden>
                <path
                  d="M4 26 A 23 23 0 0 1 50 26" fill="none"
                  stroke="currentColor" strokeOpacity="0.18" strokeWidth="4"
                  strokeLinecap="round"
                />
                <path
                  d="M4 26 A 23 23 0 0 1 50 26" fill="none"
                  stroke={chance >= 0.5 ? 'var(--v-green)' : 'var(--v-red2)'}
                  strokeWidth="4" strokeLinecap="round"
                  strokeDasharray={`${arc * 72.3} 200`}
                />
              </svg>
              <b>{Math.round(chance * 100)}%</b>
              <small>chance</small>
            </span>
          )}
        </header>
        {chartId && (
          <div className="mf-chartrow" onClick={() => onOpen(c)} aria-hidden="true">
            <Spark venue={hv} id={chartId} cacheRef={sparkCache} wide />
          </div>
        )}
        <div className="mf-outcomes">
          {single ? (
            <div className="mf-split">
              <button
                className={`g${sel(c.outcomes[0]) ? ' on' : ''}`}
                disabled={!buyable(c.outcomes[0])}
                onClick={() => buy(c, c.outcomes[0])}
              >
                Buy Yes {c.outcomes[0].price != null ? cents(c.outcomes[0].price) : ''}
              </button>
              {/* the No side is orderable from the full market page —
                  the card button walks there, venue-style */}
              <button className="r" onClick={() => onOpen(c)}>
                Buy No {c.outcomes[0].price != null ? cents(1 - (c.outcomes[0].price as number)) : ''}
              </button>
            </div>
          ) : two ? (
            <div className="mf-split">
              {c.outcomes.map((o, i) => (
                <button
                  key={o.id || o.label}
                  className={`t${i === 0 ? ' a' : ' b'}${sel(o) ? ' on' : ''}`}
                  disabled={!buyable(o)}
                  onClick={() => buy(c, o)}
                >
                  <span>{o.label}</span><b>{o.price != null ? cents(o.price) : '—'}</b>
                </button>
              ))}
            </div>
          ) : c.outcomes.slice(0, 3).map((o) => (
            <div className="mf-row" key={o.id || o.label}>
              <span className="mf-oname">{o.label}</span>
              <span className="mf-opct">{pct(o.price)}</span>
              <span className="mf-yn">
                <button
                  className={`mf-yes${sel(o) ? ' on' : ''}`}
                  disabled={!buyable(o)}
                  onClick={() => buy(c, o)}
                >{isK ? cents(o.price) : `Yes ${o.price != null ? cents(o.price) : ''}`}</button>
              </span>
            </div>
          ))}
        </div>
        <footer className="mf-foot">
          {volTxt && <span>{volTxt}.</span>}
          {closeTxt && <span>{closeTxt}</span>}
          <span className="spacer" />
          <button className="mf-open" onClick={() => onOpen(c)}>
            {c.markets_n ? `${c.markets_n} markets` : 'View'} ›
          </button>
        </footer>
      </article>
    )
  }

  // ── Findability (owner order 2026-08-29: "easy to use — not hard to
  // find what a team member is looking for") ────────────────────────
  // Games sort by start time and group under day headers, and a filter
  // box narrows the loaded feed INSTANTLY on team/league/title — no
  // server round-trip, complementing the header's deep search.
  const dayLabel = (iso: string | null): string => {
    if (!iso) return 'NO START TIME LISTED'
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return 'NO START TIME LISTED'
    const today = new Date(); const tom = new Date(Date.now() + 86400e3)
    if (d.toDateString() === today.toDateString()) return 'TODAY'
    if (d.toDateString() === tom.toDateString()) return 'TOMORROW'
    return d.toLocaleDateString([], {
      weekday: 'long', month: 'short', day: 'numeric' }).toUpperCase()
  }
  const sections = useMemo(() => {
    if (!cards) return null
    const needle = flt.trim().toLowerCase()
    const list = !needle ? cards : cards.filter((c) =>
      c.title.toLowerCase().includes(needle)
      || c.league.toLowerCase().includes(needle)
      || c.outcomes.some((o) => (o.label || '').toLowerCase().includes(needle)))
    const sorted = [...list].sort((a, b) => {
      const ta = a.close_time ? Date.parse(a.close_time) : Infinity
      const tb = b.close_time ? Date.parse(b.close_time) : Infinity
      if (ta !== tb) return ta - tb
      return (b.volume_usd ?? 0) - (a.volume_usd ?? 0)
    })
    const out: { label: string; items: FeedCard[] }[] = []
    for (const c of sorted) {
      const lbl = dayLabel(c.close_time)
      const last = out[out.length - 1]
      if (last && last.label === lbl) last.items.push(c)
      else out.push({ label: lbl, items: [c] })
    }
    return out
  }, [cards, flt])

  return (
    <div className="mf">
      <div className="mf-chips" role="tablist" aria-label="Categories">
        {LEAGUES.map((l) => {
          const n = counts[l.key]
          return (
            <button
              key={l.key} role="tab" aria-selected={league === l.key}
              className={`mf-chip${league === l.key ? ' on' : ''}`}
              onClick={() => onLeague(l.key)}
            >
              {isK && l.icon ? `${l.icon} ` : ''}{l.label}{n != null ? ` · ${n}` : ''}
            </button>
          )
        })}
      </div>
      {cards !== null && cards.length > 6 && (
        <div className="mf-filter">
          <input
            value={flt}
            onChange={(e) => setFlt(e.target.value)}
            placeholder="Filter games — team, league…"
            aria-label="Filter loaded games"
          />
          {flt && <button className="clr" onClick={() => setFlt('')}
            aria-label="Clear filter">✕</button>}
        </div>
      )}
      {cards === null ? (
        <div className="mf-feed" aria-label="Loading markets">
          <div className="tr-skel mf-skel" /><div className="tr-skel mf-skel" />
          <div className="tr-skel mf-skel" /><div className="tr-skel mf-skel" />
        </div>
      ) : (
        <div className="mf-feed">
          {sections!.map((s) => (
            <section key={s.label} className="mf-sec">
              <h4 className="mf-sec-h">{s.label}
                <small> · {s.items.length} game{s.items.length === 1 ? '' : 's'}</small>
              </h4>
              {s.items.map(card)}
            </section>
          ))}
          {sections!.length === 0 && (
            <p className="vd-empty">No games match — clear the filter.</p>
          )}
        </div>
      )}
      {err && <p className="vd-empty">{err}</p>}
    </div>
  )
}
