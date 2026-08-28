import { useEffect, useRef, useState } from 'react'
import { API_BASE } from './api'
import type { Trade } from './types'

export type ConnState = 'connecting' | 'live' | 'down'

/* ── singleton bus (NIGHT DESK v11) ──────────────────────────────────
 * One shared EventSource feeds every consumer — the feed hook, the
 * nav PulseLine, the tape, the whale-awake lamps — instead of each
 * mounting its own connection. The public hook API is unchanged. */

type TradeFn = (t: Trade, fresh: boolean) => void
type ConnFn = (c: ConnState) => void

const tradeSubs = new Set<TradeFn>()
const connSubs = new Set<ConnFn>()
let es: EventSource | null = null
let connState: ConnState = 'connecting'
let lastEventAt = 0

function emitConn(c: ConnState) {
  connState = c
  connSubs.forEach((fn) => fn(c))
}

function ensure() {
  if (es) return
  es = new EventSource(`${API_BASE}/stream`)
  es.onopen = () => emitConn('live')
  es.onerror = () => emitConn('down') // EventSource auto-reconnects
  const relay = (fresh: boolean) => (e: Event) => {
    lastEventAt = Date.now()
    const t = JSON.parse((e as MessageEvent).data) as Trade
    tradeSubs.forEach((fn) => fn(t, fresh))
  }
  es.addEventListener('trade', relay(true))
  es.addEventListener('trade_update', relay(false))
}

export function subscribe(fn: TradeFn): () => void {
  ensure()
  tradeSubs.add(fn)
  return () => tradeSubs.delete(fn)
}

export function subscribeConn(fn: ConnFn): () => void {
  ensure()
  fn(connState)
  connSubs.add(fn)
  return () => connSubs.delete(fn)
}

export function lastEventAge(): number | null {
  return lastEventAt ? (Date.now() - lastEventAt) / 1000 : null
}

/** Live trade stream over SSE. Provisional trades arrive as `trade`,
 * enrichment updates as `trade_update`; both are merged by id.
 * Public API identical to the pre-v11 hook. */
export function useLiveFeed(onTrade?: (t: Trade) => void) {
  const [conn, setConn] = useState<ConnState>(connState)
  const [live, setLive] = useState<Trade[]>([])
  const cb = useRef(onTrade)
  cb.current = onTrade

  useEffect(() => {
    const offConn = subscribeConn(setConn)
    const offTrade = subscribe((t, fresh) => {
      setLive((prev) => {
        const idx = prev.findIndex((p) => p.id === t.id)
        if (idx >= 0) {
          const next = [...prev]
          next[idx] = { ...next[idx], ...t }
          return next
        }
        if (fresh && cb.current) cb.current(t)
        return [t, ...prev].slice(0, 200)
      })
    })
    return () => { offConn(); offTrade() }
  }, [])

  return { conn, live }
}
