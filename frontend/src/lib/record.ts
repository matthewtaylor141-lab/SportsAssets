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

export const SINCE = '2026-08-01'
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
