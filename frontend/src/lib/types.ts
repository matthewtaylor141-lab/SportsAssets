export interface Trade {
  id: number
  whale_id: number
  whale_username: string | null
  tx_hash: string
  asset: string
  condition_id: string | null
  side: 'BUY' | 'SELL'
  outcome: string | null
  outcome_index: number | null
  size: number
  price: number
  notional: number
  market_title: string | null
  market_slug: string | null
  event_slug: string | null
  event_title?: string | null
  sport: string
  ts: string
  source: 'chain' | 'poll'
  detected_at: string
  latency_s: number | null
  enriched?: boolean
}

export interface Whale {
  id: number
  address: string
  username: string | null
  pinned: boolean
  banned?: boolean
  active: boolean
  source_rank: number | null
  sports_profit_alltime: number | null
  added_at: string
  removed_at: string | null
}

export interface SportStats {
  whale_id: number
  sport: string
  window: '7d' | '30d' | 'all'
  markets_traded: number
  wins: number
  losses: number
  scratches: number
  win_pct: number | null
  realized_pnl: number
  notional: number
  roi: number | null
  avg_position: number | null
  open_exposure: number
}

export interface MatrixCell extends SportStats {
  username: string | null
  address: string
}

export interface WhaleProfile {
  whale: Whale
  stats: SportStats[]
  open_positions: OpenPosition[]
  recent_trades: Trade[]
  equity_curve: { ts: string; cumulative_pnl: number }[]
  sport_mix: { sport: string; notional: number }[]
}

export interface OpenPosition {
  condition_id: string | null
  token_id: string
  outcome: string | null
  net_shares: number
  avg_cost: number
  realized_pnl: number
  exposure: number
  market_title: string | null
  event_title: string | null
  sport: string | null
  last_trade_ts: string
}

export interface EventView {
  event_slug: string
  event_title: string | null
  sport: string | null
  total_exposure: number
  disagreement: boolean
  positions: {
    whale_id: number
    username: string | null
    outcome: string | null
    net_shares: number
    exposure: number
    realized_pnl: number
  }[]
}

export interface Prefs {
  user_key: string
  min_notional: number
  muted_whales: number[]
  sports: string[]
}

export interface LatencySummary {
  count: number
  p50: number | null
  p95: number | null
  max: number | null
}
