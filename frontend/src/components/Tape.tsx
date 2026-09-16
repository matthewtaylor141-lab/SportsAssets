import { useEffect, useRef, useState } from 'react'
import { subscribe } from '../lib/sse'
import type { Trade } from '../lib/types'

const MAX = 40

/** The persistent whale-detection tape: every fresh detection enters
 * with an amber scan sweep and marches left. Fixed heights — an SSE
 * arrival may never shift layout. Hover pauses. */
export function Tape() {
  const [rows, setRows] = useState<Trade[]>([])
  const track = useRef<HTMLDivElement | null>(null)
  const x = useRef(0)
  const paused = useRef(false)

  useEffect(() => subscribe((t, fresh) => {
    if (!fresh) return
    setRows((r) => [t, ...r].slice(0, MAX))
  }), [])

  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      if (!paused.current && track.current) {
        x.current -= 60 * dt
        const w = track.current.scrollWidth / 2 || 1
        if (-x.current > w) x.current += w
        track.current.style.transform = `translateX(${x.current}px)`
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [])

  if (!rows.length) return null
  const cells = [...rows, ...rows] // loop seam
  return (
    <div
      className="nd-tape"
      onMouseEnter={() => { paused.current = true }}
      onMouseLeave={() => { paused.current = false }}
      role="log"
      aria-label="live whale detections"
    >
      <div className="nd-tape-track" ref={track}>
        {cells.map((t, i) => (
          <span className={`nd-tape-cell${i === 0 ? ' nd-tape-new' : ''}`} key={`${t.id}:${i}`}>
            <span className="w">{(t.whale_username || '?').toUpperCase()}</span>
            <span className={t.side === 'BUY' ? 'b' : 's'}>{t.side}</span>
            <span>{t.price != null ? `${Math.round(Number(t.price) * 100)}¢` : ''}</span>
            <span>{t.notional != null ? `$${Math.round(Number(t.notional)).toLocaleString()}` : ''}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
