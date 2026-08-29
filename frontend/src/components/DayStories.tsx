import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'

/* Daily recap stories (owner order 2026-08-29): the last settled day
 * retold as tap-through full-screen slides — story format, auto-
 * advancing, thumb-navigable. Every number comes from the SAME copies
 * record payload the Performance page already renders; the stories are
 * a lens on data on screen, never a second source that could drift. */

export type StoryTrade = {
  whale: string; slug: string | null; pnl: number; stake: number
  settled_at?: string | null; day?: string | null
}
export type StoryDay = { day: string; pnl: number; settled: number
  wins?: number }

const DUR_MS = 5000
const fmtUsd = (v: number) =>
  `$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
const fmtSigned = (v: number) => `${v < 0 ? '−' : '+'}${fmtUsd(v)}`

type Slide = {
  k: string; big: string; tone: 'pos' | 'neg' | ''
  sub: React.ReactNode; cta?: boolean
}

export function buildSlides(daily: StoryDay[], trades: StoryTrade[],
                            streak: number): Slide[] | null {
  // "Yesterday" = the most recent day with settled money (today shows
  // once it has settlements, which reads as a live scoreboard anyway).
  const day = [...daily].reverse().find((d) => d.settled > 0)
  if (!day) return null
  const dayTrades = trades.filter((t) =>
    (t.day || t.settled_at || '').slice(0, 10) === day.day)
  const byWhale = new Map<string, number>()
  dayTrades.forEach((t) =>
    byWhale.set(t.whale, (byWhale.get(t.whale) || 0) + t.pnl))
  const topWhale = [...byWhale.entries()].sort((a, b) => b[1] - a[1])[0]
  const bigWin = [...dayTrades].sort((a, b) => b.pnl - a.pnl)[0]
  // W-L comes from the day row itself, never re-derived from trades[]
  // (a truncated sample would understate wins next to the full-day
  // settled count on the same slide).
  const wins = day.wins ?? null

  const slides: Slide[] = [{
    k: `THE ${day.day.slice(5)} RECAP`,
    big: fmtSigned(day.pnl),
    tone: day.pnl >= 0 ? 'pos' : 'neg',
    sub: <>{day.settled} copies settled{wins != null
      && <> · {wins}W–{day.settled - wins}L</>}.
      Every figure straight from the order ledger.</>,
  }]
  if (topWhale) {
    slides.push({
      k: 'WHALE OF THE DAY',
      big: topWhale[0].length > 12 ? topWhale[0].slice(0, 12) + '…' : topWhale[0],
      tone: topWhale[1] >= 0 ? 'pos' : 'neg',
      sub: <>{fmtSigned(topWhale[1])} across their copies — the roster
        earns its spots daily.</>,
    })
  }
  if (bigWin && bigWin.pnl > 0) {
    slides.push({
      k: 'BIGGEST WIN',
      big: fmtSigned(bigWin.pnl),
      tone: 'pos',
      sub: <>{bigWin.whale} · {(bigWin.slug || 'market').slice(0, 52)} ·
        {' '}{fmtUsd(bigWin.stake)} staked.</>,
    })
  }
  if (streak > 1) {
    slides.push({
      k: 'THE STREAK',
      big: `${streak} 🔥`,
      tone: 'pos',
      sub: <>green days in a row and counting.</>,
    })
  }
  slides.push({
    k: 'THE FULL LEDGER',
    big: 'REPORTS',
    tone: '',
    sub: <>Whale-by-whale performance, latency, and downloadable PDF
      reports live one tap away.</>,
    cta: true,
  })
  return slides
}

export function DayStories({ slides, onClose }: {
  slides: Slide[]; onClose: () => void
}) {
  const [i, setI] = useState(0)
  const navigate = useNavigate()
  const n = slides.length
  const still = useMemo(() => {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches }
    catch { return false }
  }, [])

  useEffect(() => {
    if (still) return // manual taps only — no timed rush
    const t = setTimeout(() =>
      i < n - 1 ? setI(i + 1) : onClose(), DUR_MS)
    return () => clearTimeout(t)
  }, [i, n, onClose, still])

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') setI((v) => Math.min(v + 1, n - 1))
      if (e.key === 'ArrowLeft') setI((v) => Math.max(v - 1, 0))
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [n, onClose])

  const s = slides[i]
  // Portaled to <body>: the page-fade animation on <main> makes any
  // transformed ancestor the containing block for position:fixed, which
  // shoved this overlay below the fold when rendered in place.
  return createPortal(
    <div className="story" role="dialog" aria-label="Daily recap">
      <div className="story-frame">
        <div className="story-bars" aria-hidden>
          {slides.map((_, j) => (
            <i key={`${j}-${j === i ? 'run' : ''}`}
              className={j < i ? 'done' : j === i ? 'run' : ''}
              style={{ ['--dur' as string]: `${DUR_MS}ms` }} />
          ))}
        </div>
        <button className="story-close" onClick={onClose} aria-label="Close">✕</button>
        <div className="story-slide" key={i}>
          <span className="story-k">{s.k}</span>
          <span className={`story-big ${s.tone}`}>{s.big}</span>
          <span className="story-sub">{s.sub}</span>
          {s.cta && (
            <span className="story-cta">
              <button className="rpt-pdf"
                onClick={(e) => { e.stopPropagation(); navigate('/reports') }}>
                OPEN REPORTS
              </button>
            </span>
          )}
        </div>
        <div className="story-foot">tap right for next · left to rewind</div>
        <div className="story-tapzones" aria-hidden>
          <i onClick={() => (i > 0 ? setI(i - 1) : onClose())} />
          <i onClick={() => (i < n - 1 ? setI(i + 1) : onClose())} />
        </div>
      </div>
    </div>,
    document.body,
  )
}
