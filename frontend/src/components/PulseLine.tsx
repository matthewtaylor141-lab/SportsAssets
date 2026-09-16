import { useEffect, useRef, useState } from 'react'
import { lastEventAge, subscribe, subscribeConn, type ConnState } from '../lib/sse'

const N = 45
const BASE = 10
const SPIKE = [10, 4, 16, 7, 10]

/** The stream's heartbeat: a 90x20 oscilloscope trace in the command
 * rail. Amber EKG spikes scroll left as SSE events arrive; a dashed
 * red flatline means the wire is down. Peripheral-vision connection
 * awareness — no reading required. */
export function PulseLine() {
  const [conn, setConn] = useState<ConnState>('connecting')
  const [pts, setPts] = useState<number[]>(() => Array(N).fill(BASE))
  const queue = useRef<number[]>([])
  const reduced = useRef(
    typeof matchMedia !== 'undefined' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches)

  useEffect(() => {
    const offConn = subscribeConn(setConn)
    const offTrade = subscribe(() => { queue.current.push(...SPIKE) })
    if (reduced.current) return () => { offConn(); offTrade() }
    const t = setInterval(() => {
      setPts((p) => {
        const next = p.slice(1)
        next.push(queue.current.length ? queue.current.shift()! : BASE)
        return next
      })
    }, 250)
    return () => { clearInterval(t); offConn(); offTrade() }
  }, [])

  const shown = conn === 'live' ? pts : Array(N).fill(BASE)
  const age = lastEventAge()
  return (
    <span
      className="nd-pulse"
      data-conn={conn}
      role="status"
      aria-label={`stream ${conn}`}
      title={`stream ${conn}${age != null ? ` · last event ${Math.round(age)}s ago` : ''}`}
    >
      <svg width="90" height="20" viewBox="0 0 90 20" aria-hidden>
        <polyline points={shown.map((y, i) => `${i * 2},${y}`).join(' ')} />
      </svg>
    </span>
  )
}
