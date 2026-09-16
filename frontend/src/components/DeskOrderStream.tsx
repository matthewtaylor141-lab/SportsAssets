/**
 * Live order confirmations (owner order 2026-08-28).
 *
 * One EventSource on /api/desk/stream: every live_orders INSERT and
 * status change, pushed the instant it commits (Postgres trigger →
 * pg_notify → SSE — no polling in the path). The desk renders them
 * as venue-styled confirmation toasts plus a compact tape line in
 * the portal strip, so the team SEES the autonomous trader (and
 * their own manual tickets) act in real time.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '../lib/api'
import { deskAdminToken, deskToken } from '../lib/desk'

export type OrderEvt = {
  id: number
  op: 'INSERT' | 'UPDATE'
  status: string
  side: string | null
  venue: string | null
  whale: string | null
  slug: string | null
  shares: number | string | null
  fill_price: number | string | null
  filled_usd: number | string | null
  requested_usd: number | string | null
  pnl: number | string | null
  error: string | null
  at: number
}

/** Statuses worth a toast — 'submitting' rides the tape only, so a
 * fill never arrives as two stacked cards. */
const TOAST_STATUSES = new Set([
  'filled', 'unfilled', 'rejected', 'error', 'cashed_out', 'open',
  'cancelled', 'settled',
  // an add leg that filled and was booked onto its standing row (2026-09-02)
  'merged',
])
const TOAST_TTL_MS = 6500
const MAX_TOASTS = 4

export function useOrderStream(enabled: boolean) {
  const [last, setLast] = useState<OrderEvt | null>(null)
  const [toasts, setToasts] = useState<OrderEvt[]>([])
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id))
    const h = timers.current.get(id)
    if (h) { clearTimeout(h); timers.current.delete(id) }
  }, [])

  useEffect(() => {
    if (!enabled) return
    const tok = deskToken() ?? deskAdminToken()
    if (!tok) return
    const es = new EventSource(
      `${API_BASE}/api/desk/stream?token=${encodeURIComponent(tok)}`)
    const onOrder = (e: MessageEvent) => {
      let evt: OrderEvt
      try { evt = JSON.parse(e.data) } catch { return }
      setLast(evt)
      if (!TOAST_STATUSES.has(evt.status)) return
      setToasts((t) => {
        // a status change replaces the same order's earlier card
        const kept = t.filter((x) => x.id !== evt.id)
        return [...kept, evt].slice(-MAX_TOASTS)
      })
      const h = setTimeout(() => dismiss(evt.id), TOAST_TTL_MS)
      const prior = timers.current.get(evt.id)
      if (prior) clearTimeout(prior)
      timers.current.set(evt.id, h)
    }
    es.addEventListener('order', onOrder as EventListener)
    return () => {
      es.removeEventListener('order', onOrder as EventListener)
      es.close()
      timers.current.forEach((h) => clearTimeout(h))
      timers.current.clear()
    }
  }, [enabled, dismiss])

  return { last, toasts, dismiss }
}

const n = (v: number | string | null | undefined): number | null => {
  if (v == null) return null
  const f = typeof v === 'number' ? v : parseFloat(v)
  return Number.isFinite(f) ? f : null
}

export function evtHeadline(e: OrderEvt): { title: string; body: string; tone: 'ok' | 'bad' | 'dim' } {
  const who = e.whale ? e.whale.toUpperCase() : 'MANUAL'
  const px = n(e.fill_price)
  // a merged add leg's money lives on its standing row (its own
  // filled_usd is zeroed), so the leg's asked amount is what it spent
  const usd = e.status === 'merged'
    ? (n(e.requested_usd) ?? n(e.filled_usd))
    : (n(e.filled_usd) ?? n(e.requested_usd))
  const cents = px != null ? `${Math.round(px * 100)}¢` : ''
  const amt = usd != null ? `$${usd.toFixed(2)}` : ''
  const mkt = (e.slug || '').slice(0, 44)
  switch (e.status) {
    case 'filled':
      return { title: 'Order filled', body: `${who} · ${amt}${cents ? ` @ ${cents}` : ''} · ${mkt}`, tone: 'ok' }
    case 'merged':
      return { title: 'Add leg merged', body: `${who} · ${amt} · ${e.error || 'booked onto the standing row'} · ${mkt}`, tone: 'ok' }
    case 'open':
      return { title: 'Order resting on the book', body: `${who} · ${amt}${cents ? ` @ ${cents}` : ''} · ${mkt}`, tone: 'ok' }
    case 'cashed_out': {
      const p = n(e.pnl)
      return {
        title: 'Position closed',
        body: `${who} · ${p != null ? `${p >= 0 ? '+' : ''}$${p.toFixed(2)} realized · ` : ''}${mkt}`,
        tone: p != null && p < 0 ? 'bad' : 'ok',
      }
    }
    case 'settled': {
      const p = n(e.pnl)
      return {
        title: 'Position settled',
        body: `${who} · ${p != null ? `${p >= 0 ? '+' : ''}$${p.toFixed(2)} · ` : ''}${mkt}`,
        tone: p != null && p < 0 ? 'bad' : 'ok',
      }
    }
    case 'unfilled':
      return { title: 'Order not filled', body: `${who} · ${mkt}`, tone: 'dim' }
    case 'cancelled':
      return { title: 'Order cancelled', body: `${who} · ${mkt}`, tone: 'dim' }
    case 'rejected':
    case 'error':
      return { title: 'Order rejected', body: `${who} · ${e.error || 'venue refused'} · ${mkt}`, tone: 'bad' }
    default:
      return { title: e.status, body: `${who} · ${mkt}`, tone: 'dim' }
  }
}

export function tapeLine(e: OrderEvt): string {
  const who = e.whale ? e.whale.toUpperCase() : 'MANUAL'
  const px = n(e.fill_price)
  const usd = e.status === 'merged'
    ? (n(e.requested_usd) ?? n(e.filled_usd))
    : (n(e.filled_usd) ?? n(e.requested_usd))
  const bits = [
    e.whale ? 'AI' : 'DESK', who, e.status,
    usd != null ? `$${usd.toFixed(0)}` : '',
    px != null ? `@ ${Math.round(px * 100)}¢` : '',
    (e.slug || '').slice(0, 34),
  ].filter(Boolean)
  return bits.join(' · ')
}

/** The venue-styled toast stack. Lives inside .dxp so the active
 * skin's variables style it; both skins defined in desk10.css. */
export function OrderToasts({ toasts, dismiss }: {
  toasts: OrderEvt[]
  dismiss: (id: number) => void
}) {
  if (!toasts.length) return null
  return (
    <div className="dxp-toasts" role="log" aria-live="polite">
      {toasts.map((t) => {
        const h = evtHeadline(t)
        return (
          <div key={`${t.id}:${t.status}`} className={`dxp-toast dxp-toast--${h.tone}`}>
            <span className="dxp-toast-ic" aria-hidden>
              {h.tone === 'bad' ? '✕' : h.tone === 'dim' ? '·' : '✓'}
            </span>
            <span className="dxp-toast-tx">
              <b>{h.title}</b>
              <em>{h.body}</em>
            </span>
            <button className="dxp-toast-x" onClick={() => dismiss(t.id)} aria-label="Dismiss">✕</button>
          </div>
        )
      })}
    </div>
  )
}
