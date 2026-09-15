import { usePolled } from './poll'

/* One data source for the whole site: /api/track-record — the ACTUAL venue
 * account, windowed from Aug 1 on the venue's own entry timestamps. The
 * engine's shadow mirror is telemetry, not a record, and never renders. */

export interface TRRow {
  sleeve?: 'engine' | 'copy'
  /** Whole-account basis (owner 2026-08-08): which cohort the row is. */
  cohort?: 'record' | 'manual' | 'unattributed' | 'over_pnl' | 'over_limit'
  cashed_out?: boolean
  market_slug: string
  title: string
  outcome: string | null
  sport: string
  icon: string
  category: string
  league: string
  entry_ts: number | null
  entry_date: string | null
  entry_price: number | null
  fills: number
  qty: number
  stake: number
  value: number
  settled: boolean
  settled_ts: number | null
  pnl: number | null
  unrealized: number | null
}

export interface TRDaily {
  date: string
  deployed: number
  trades: number
  open: number
  pnl: number
  settled: number
  wins: number
  pnl_estimated: boolean
}

export interface TRSummary {
  trades: number
  open: number
  settled: number
  wins: number
  losses: number
  deployed: number
  open_value: number
  net_pnl: number
  settled_stake: number
  roi: number | null
  win_rate: number | null
}

export interface TRExcluded {
  limit?: number
  count: number
  open?: number
  stake: number
  net_pnl: number
}

export interface TRSnapshot {
  raw_ts: number
  age_s: number
  positions_pages: number | null
  positions_complete: boolean
  refresh_error: string | null
  refresh_error_streak: number
}

/** Whole-account tie-out: AI cohort + every disclosed exclusion. This is
 * the number that must match the venue app's own P&L view. */
export interface TRAccount {
  trades: number
  open: number
  stake: number
  net_pnl: number
}

export interface TrackRecordData {
  configured: boolean
  error?: string
  activities_source?: string
  archive_rows?: number
  window_rows?: number
  since: string
  snapshot?: TRSnapshot
  account?: TRAccount
  /** The AI-only subset, disclosed now that the headline is the account. */
  record_subset?: { trades: number; settled: number; wins: number
                    losses: number; net_pnl: number; settled_stake: number }
  summary: TRSummary
  daily: TRDaily[]
  trades: TRRow[]
  excluded_undatable: number
  excluded_over_limit: TRExcluded | null
  /** Copy-sleeve cohort stats — its rows are IN the record, tagged. */
  copy_sleeve?: TRExcluded | null
  excluded_unattributed?: TRExcluded | null
}

export interface KalshiOpenRow {
  ticker: string
  qty: number
  avg_cost: number
  cost: number
  /** Venue market status when past trading but unresolved (e.g. 'closed',
   * 'determined') — the game is over and the row awaits settlement. */
  venue_status?: string
}

export interface KalshiOpen {
  updated_at?: string
  n: number
  cost: number
  rows: KalshiOpenRow[]
}

/** The engine's open book on the second venue, published every cycle. */
export function useKalshiOpen(refreshMs = 30_000) {
  return usePolled<KalshiOpen>('/api/kalshi-open', refreshMs)
}

/* Venue-truth rebuild (task #74): the uncapped record computed straight
 * from the venues' own ledgers — PM's afterPosition.realized, Kalshi's
 * raw fills+settlements with exact fees. Rolling window; the number that
 * reconciles to the venue apps to the dollar. */

export interface VTVenueSummary {
  error?: string
  markets?: number
  settled?: number
  wins?: number
  losses?: number
  realized?: number
  settled_cost?: number
  open?: number
  open_cost?: number
}

export interface VTDay {
  day: string
  settled: number
  wins: number
  losses: number
  cost: number
  realized: number
}

export interface VenueTruthData {
  methodology: 'venue-truth'
  building?: boolean
  since: string
  polymarket_us?: VTVenueSummary
  kalshi?: VTVenueSummary
  total?: { settled: number; wins: number; losses: number
            realized: number; settled_cost: number }
  daily?: VTDay[]
  partial?: boolean
  /** Days the rolling window has moved past, frozen in the day ledger. */
  frozen_days?: VTDay[]
  /** Window + frozen history combined — the accreting record. */
  all_time?: { since: string; settled: number; wins: number; losses: number
               realized: number; settled_cost: number }
  history_error?: string
  kalshi_note?: string
  kalshi_window_incomplete?: { n: number; tickers: string[] }
  age_s?: number
}

export function useVenueTruth(refreshMs = 60_000) {
  return usePolled<VenueTruthData>('/api/venue-truth', refreshMs)
}

/* The COPIES cohort — THE public record (owner order 2026-08-22: "the
 * performance data must exclusively show the copy whales numbers").
 * Uncapped, venue-backed via the order-level audit, per-whale split,
 * full-window daily series, row-level ledger, live open book. */

export interface CopiesWhale {
  whale: string
  settled: number
  wins: number
  losses: number
  pnl: number
  staked: number
  roi: number | null
}

export interface CopiesWhaleSport {
  whale: string
  sport: string
  settled: number
  wins: number
  losses: number
  pnl: number
  staked: number
  roi: number | null
}

export interface CopiesDay {
  day: string
  settled: number
  wins: number
  losses: number
  pnl: number
}

export interface CopiesDayWhale {
  day: string
  whale: string
  settled: number
  wins: number
  losses: number
  pnl: number
  staked: number
}

/** One settled/cashed-out copy row from the public ledger (≤400 newest). */
export interface CopiesTrade {
  day: string | null
  whale: string
  slug: string | null
  stake: number
  pnl: number
  status: string
  sport?: string | null
  /** Venue id as the executor recorded it, e.g. 'polymarket-us'/'kalshi'. */
  venue?: string | null
  /** Whale fill → our order, seconds; null when unmeasured. */
  latency_s?: number | null
}

/** Per-whale open exposure — who the table is riding on right now. */
export interface CopiesOpenWhale {
  whale: string
  count: number
  stake: number
}

export interface CopiesRecord {
  cohort: 'copies'
  uncapped: boolean
  since: string
  generated_at?: string
  total: { settled: number; wins: number; losses: number
           pnl: number; staked: number; roi: number | null
           win_rate: number | null }
  by_whale: CopiesWhale[]
  by_whale_sport?: CopiesWhaleSport[]
  /** Newest-first, FULL since-window (31-day truncation lifted). */
  daily: CopiesDay[]
  daily_by_whale?: CopiesDayWhale[]
  /** What the copy sleeves have on the table right now. */
  open?: { count: number; stake: number; by_whale?: CopiesOpenWhale[] }
  trades?: CopiesTrade[]
  today?: { pnl: number; settled: number; wins: number; losses: number }
  /** Per-venue split — kalshi is null until the engine export lands. */
  venues?: {
    polymarket?: { pnl: number; staked: number; settled: number } | null
    kalshi?: { pnl: number; staked: number; settled: number } | null
  }
  /** True when the Kalshi copy sleeve is merged into the totals. */
  kalshi_included?: boolean
  /** True when the served window starts at the display epoch (no
   * ?since= override) — the record restarted from zero on that day. */
  rebaselined?: boolean
  /** The display epoch day, ET (COPIES_EPOCH server-side). */
  epoch?: string
}

/** ?since= that reaches past the display epoch to the whole history. */
export const ALL_TIME_SINCE = '2020-01-01'

export function useCopiesRecord(refreshMs = 30_000, since?: string) {
  // Each window is its own path, so usePolled's per-path cache and the
  // monotone guard below both compare epoch-to-epoch or all-time-to-
  // all-time — the two datasets never mix.
  return usePolled<CopiesRecord>(
    since ? `/api/copies-record?since=${since}` : '/api/copies-record',
    refreshMs,
    undefined,
    // Same monotone guard as the account record: settlements since a
    // fixed start only grow, so a payload whose settled count collapses
    // is a degraded snapshot (mid-boot, partial hydrate) — hold the
    // last good numbers instead of shrinking the public headline. The
    // check binds payloads of the SAME window only: a server-side
    // re-baseline (COPIES_EPOCH, owner order 2026-08-28) moves `since`
    // forward on the default path and the count legitimately restarts —
    // refusing that payload would pin the page to the pre-epoch cache.
    (prev, next) => {
      if ((prev.since || '') !== (next.since || '')) return true
      return (next.total?.settled ?? 0) >= (prev.total?.settled ?? 0) * 0.9
    },
  )
}

// ONE DISPLAY EPOCH (owner order 2026-09-02: "zero out our frontend
// completely — only show performance and account data starting September
// 1st"). The server owns the epoch (DISPLAY_EPOCH); the client asks for
// its default window and never pins an earlier day. History stays
// reachable through the explicit ALL-TIME toggle only.
export const SINCE = '2026-09-01'
// No stake cap (owner directive 2026-08-11: "include the excluded bucket").
// The $100 cap dated from the $1-$5 ticket era, when a larger position could
// only be an execution incident; at $50/$100 copy clips, legitimate positions
// cross it routinely and the cap was silently carving real trades out of the
// headline. Every position now counts. The $100 single-trade P&L swing
// exclusion (over_pnl, owner directive 2026-08-06) is separate and stands.

export function useTrackRecord(refreshMs = 30_000) {
  return usePolled<TrackRecordData>(
    `/api/track-record?since=${SINCE}`,
    refreshMs,
    (d) => d.error || null,
    // NEVER display a downgrade: a freshly-booted API serves the venue's
    // ~2-day window for the seconds until its archive hydrates, and
    // accepting that snapshot made Aug 1 "vanish" from the site three
    // times on 2026-08-03. History can grow or hold — it cannot shrink.
    // Two independent checks, because the source label alone let a
    // shrunken snapshot through on 2026-08-04: (a) refuse the
    // archive->window source transition, (b) refuse any payload whose
    // settled count collapses — settlements since a fixed start date are
    // monotone, so a big drop is a degraded snapshot, not news.
    (prev, next) => {
      if (prev.activities_source !== 'venue_window' &&
          next.activities_source === 'venue_window') return false
      const ps = prev.summary?.settled ?? 0
      const ns = next.summary?.settled ?? 0
      return ns >= ps * 0.9
    },
  )
}
