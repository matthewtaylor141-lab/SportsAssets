export function LatencyBadge({
  seconds,
  source,
}: {
  seconds: number | null | undefined
  source: 'chain' | 'poll' | 'backfill'
}) {
  // Imported history isn't a live detection — no latency to brag about.
  if (source === 'backfill' || seconds === null || seconds === undefined) return null
  const cls = seconds <= 3 ? 'fast' : seconds > 10 ? 'slow' : ''
  return (
    <span className={`latency ${cls}`} title={`Detection path: ${source === 'chain' ? 'on-chain (A)' : 'API poll (B)'}`}>
      detected {seconds.toFixed(1)}s after fill
    </span>
  )
}
