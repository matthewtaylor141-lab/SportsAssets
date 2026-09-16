import { Link } from 'react-router-dom'
import { fmtAgo, fmtCents, fmtUsd, whaleName } from '../lib/format'
import type { Trade } from '../lib/types'
import { LatencyBadge } from './LatencyBadge'

export function TradeRow({ trade, fresh = false }: { trade: Trade; fresh?: boolean }) {
  const market = trade.event_title || trade.market_title
  return (
    <div className={`trade-row${fresh ? ' fresh' : ''}`}>
      <Link className="whale" to={`/whales/${trade.whale_id}`}>
        {whaleName(trade)}
      </Link>
      <span className={`side ${trade.side}`}>{trade.side}</span>
      <span className="market">
        {trade.outcome ? <strong>{trade.outcome}</strong> : <em>enriching…</em>}
        {' @ '}
        {fmtCents(trade.price)}
        {market ? <span style={{ color: 'var(--muted)' }}> — {market}</span> : null}
      </span>
      {trade.sport && trade.sport !== 'unclassified' && <span className="chip">{trade.sport}</span>}
      <span className="notional">{fmtUsd(trade.notional)}</span>
      <LatencyBadge seconds={trade.latency_s} source={trade.source} />
      <span className="when">{fmtAgo(trade.ts)}</span>
    </div>
  )
}
