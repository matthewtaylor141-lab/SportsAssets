import { useEffect, useRef, useState } from 'react'
import { API_BASE } from './api'
import type { Trade } from './types'

export type ConnState = 'connecting' | 'live' | 'down'

/** Live trade stream over SSE. Provisional trades arrive as `trade`,
 * enrichment updates as `trade_update`; both are merged by id. */
export function useLiveFeed(onTrade?: (t: Trade) => void) {
  const [conn, setConn] = useState<ConnState>('connecting')
  const [live, setLive] = useState<Trade[]>([])
  const cb = useRef(onTrade)
  cb.current = onTrade

  useEffect(() => {
    const es = new EventSource(`${API_BASE}/stream`)
    es.onopen = () => setConn('live')
    es.onerror = () => setConn('down') // EventSource auto-reconnects

    const upsert = (t: Trade, fresh: boolean) => {
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
    }
    es.addEventListener('trade', (e) => upsert(JSON.parse((e as MessageEvent).data), true))
    es.addEventListener('trade_update', (e) => upsert(JSON.parse((e as MessageEvent).data), false))
    return () => es.close()
  }, [])

  return { conn, live }
}
