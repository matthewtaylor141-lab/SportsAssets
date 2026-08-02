import { useEffect, useState } from 'react'
import { api } from './api'

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

export interface TrackRecordData {
  configured: boolean
  error?: string
  since: string
  summary: TRSummary
  daily: TRDaily[]
  trades: TRRow[]
  excluded_undatable: number
}

export const SINCE = '2026-08-01'

export function useTrackRecord(refreshMs = 30_000) {
  const [data, setData] = useState<TrackRecordData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let dead = false
    const load = () =>
      api<TrackRecordData>(`/api/track-record?since=${SINCE}`)
        .then((d) => {
          if (dead) return
          if (d.error) setErr(d.error)
          else { setData(d); setErr(null) }
        })
        .catch((e) => !dead && setErr(String(e)))
    load()
    const t = setInterval(load, refreshMs)
    return () => { dead = true; clearInterval(t) }
  }, [refreshMs])
  return { data, err }
}
