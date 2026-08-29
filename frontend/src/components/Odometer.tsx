import { useEffect, useMemo, useRef, useState } from 'react'

/* Living numbers (owner order 2026-08-29): every money figure rolls
 * like a gauge instead of snapping. Each digit is a vertical 0-9 rail
 * translated to the current digit; a value change re-translates and
 * the wrapper radiates a pos/neg pulse ring. Digits are keyed from the
 * RIGHT so a new leading digit ("999.99" -> "1,004.10") extends the
 * left edge instead of re-rolling every column. Non-digits (.,$-+)
 * render static. Honesty rule: the DOM always contains the exact
 * rendered string — the roll is presentation, never interpolation of
 * numbers we were not given. Reduced-motion renders plain text. */

const RAIL = '0123456789'

function prefersStill(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch { return false }
}

export function Odometer({ value, render, countUp = false, className }: {
  value: number
  render: (v: number) => string
  /** roll up from 0 on first mount (heroes only — tables must not swarm) */
  countUp?: boolean
  className?: string
}) {
  const still = useMemo(prefersStill, [])
  const prev = useRef<number | null>(null)
  const [pulse, setPulse] = useState<'pos' | 'neg' | null>(null)
  // Count-up: first paint shows 0, then the real value one frame later
  // so the rails roll into place. Skipped under reduced motion.
  const [shown, setShown] = useState(countUp && !still ? 0 : value)

  useEffect(() => {
    if (shown !== value) {
      const raf = requestAnimationFrame(() => setShown(value))
      return () => cancelAnimationFrame(raf)
    }
  }, [value, shown])

  useEffect(() => {
    if (prev.current !== null && value !== prev.current) {
      setPulse(value > prev.current ? 'pos' : 'neg')
    }
    prev.current = value
  }, [value])

  const text = render(shown)
  if (still) {
    return <span className={`odo ${className || ''}`}>{render(value)}</span>
  }
  const chars = [...text]
  const n = chars.length
  return (
    <span
      className={`odo ${className || ''}`}
      data-pulse={pulse ?? undefined}
      onAnimationEnd={() => setPulse(null)}
      aria-label={text}
    >
      {chars.map((ch, i) => {
        const key = `c${n - i}` // right-anchored identity
        if (!/\d/.test(ch)) {
          return <span key={key} className="odo-ch" aria-hidden>{ch}</span>
        }
        const d = ch.charCodeAt(0) - 48
        return (
          <span key={key} className="odo-slot" aria-hidden>
            <span className="odo-rail"
              style={{ transform: `translateY(${-d}em)` }}>
              {[...RAIL].map((r) => <span key={r} className="odo-d">{r}</span>)}
            </span>
          </span>
        )
      })}
    </span>
  )
}
