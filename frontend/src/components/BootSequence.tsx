import { useEffect, useMemo, useState } from 'react'

/* Boot sequence (owner order 2026-08-29): once per browser session the
 * site opens like a terminal powering up — system-check lines type on,
 * resolve, and the overlay lifts into the dashboard. Pure theater on a
 * strict budget: ~1.8s total, skipped entirely for reduced-motion,
 * return visits in the same session, and anyone who taps it. The lines
 * name real subsystems; none of the check text asserts anything the
 * site does not actually do. */

const LINES = [
  'BETTORTOKEN OS · v14',
  'link: order ledger .......... OK',
  'link: whale detection bus ... OK',
  'link: venue accounts ........ OK',
  'integrity: figures are read live from the ledger',
  'ENTER',
]

const KEY = 'sa_booted'

export function BootSequence() {
  const skip = useMemo(() => {
    try {
      if (sessionStorage.getItem(KEY) === '1') return true
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch { return true }
  }, [])
  const [shown, setShown] = useState(0)
  const [gone, setGone] = useState(skip)
  const [lifting, setLifting] = useState(false)

  useEffect(() => {
    if (skip) return
    try { sessionStorage.setItem(KEY, '1') } catch { /* once only */ }
    const steps = LINES.map((_, i) => setTimeout(() => setShown(i + 1), 120 + i * 230))
    const lift = setTimeout(() => setLifting(true), 120 + LINES.length * 230 + 260)
    const done = setTimeout(() => setGone(true), 120 + LINES.length * 230 + 800)
    return () => { steps.forEach(clearTimeout); clearTimeout(lift); clearTimeout(done) }
  }, [skip])

  if (gone) return null
  return (
    <div className={`boot${lifting ? ' boot-lift' : ''}`}
      onClick={() => setGone(true)} role="presentation" aria-hidden>
      <div className="boot-box">
        {LINES.slice(0, shown).map((l, i) => (
          <div key={i} className={`boot-line${i === LINES.length - 1 ? ' boot-enter' : ''}`}>
            {i > 0 && i < LINES.length - 1 ? <span className="boot-caret">▸ </span> : null}{l}
          </div>
        ))}
        <span className="boot-cursor" />
      </div>
    </div>
  )
}
