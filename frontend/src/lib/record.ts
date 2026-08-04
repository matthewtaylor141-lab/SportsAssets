import { usePolled } from './poll'

/* One data source for the whole site: /api/track-record — the ACTUAL venue
 * account, windowed from Aug 1 on the venue's own entry timestamps. The
 * engine's shadow mirror is telemetry, not a record, and never renders. */

export interface TRRow {
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
  summary: TRSummary
  daily: TRDaily[]
  trades: TRRow[]
  excluded_undatable: number
  excluded_over_limit: TRExcluded | null
  excluded_copy_sleeve?: TRExcluded | null
  excluded_unattributed?: TRExcluded | null
}

export const SINCE = '2026-08-01'
// The record presents strategy-sized positions. Anything costing more than
// this is an execution incident or a non-strategy trade, not the $1-$5
// strategy — excluded from every figure and ALWAYS disclosed on the page
// (count + net P&L), because a record that hides its exclusions is not a
// record.
export const MAX_STAKE = 100

export function useTrackRecord(refreshMs = 30_000) {
  return usePolled<TrackRecordData>(
    `/api/track-record?since=${SINCE}&max_stake=${MAX_STAKE}`,
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
