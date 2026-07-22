export function fmtUsd(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`
  return `${sign}$${abs.toLocaleString(undefined, { maximumFractionDigits: digits })}`
}

export function fmtSignedUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const s = fmtUsd(v)
  return v > 0 ? `+${s}` : s
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function fmtCents(price: number): string {
  return `${Math.round(price * 100)}¢`
}

export function fmtAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.max(0, Math.floor(s))}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export function shortAddr(addr: string): string {
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`
}

export function whaleName(t: { whale_username: string | null; whale_id: number }): string {
  return t.whale_username || `whale#${t.whale_id}`
}
