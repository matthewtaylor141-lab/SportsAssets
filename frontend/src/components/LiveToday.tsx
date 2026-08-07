import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'

// Second-latency settlement strip (owner report 2026-08-07: "won 4
// trades, page didn't move"). Fed by /api/today-live — our own ledger,
// not the venue snapshot — so wins appear within seconds of resolution,
// slide in, and the day P&L ticks live.

interface Settle {
  title: string
  outcome: string | null
  whale: string | null
  pnl: number
  at: string | null
}

interface Live {
  pnl: number
  settled: number
  wins: number
  recent: Settle[]
}

const money = (v: number) => `${v < 0 ? '-' : '+'}$${Math.abs(v).toFixed(2)}`

function ago(iso: string | null): string {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 90) return `${Math.round(s)}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

export function LiveToday() {
  const [live, setLive] = useState<Live | null>(null)
  const [flash, setFlash] = useState(false)
  const prevSettled = useRef<number | null>(null)

  useEffect(() => {
    let dead = false
    const load = () =>
      api<Live>('/api/today-live').then((d) => {
        if (dead) return
        if (prevSettled.current !== null && d.settled > prevSettled.current) {
          setFlash(true)
          setTimeout(() => setFlash(false), 2400)
        }
        prevSettled.current = d.settled
        setLive(d)
      }).catch(() => {})
    load()
    const t = setInterval(load, 12000)
    return () => { dead = true; clearInterval(t) }
  }, [])

  if (!live || (live.settled === 0 && live.recent.length === 0)) return null

  return (
    <div className={`live-strip${flash ? ' live-flash' : ''}`}>
      <div className="live-head">
        <span className="live-dot" />
        <span className="live-label">LIVE · TODAY'S SETTLEMENTS</span>
        <span className={`live-pnl ${live.pnl >= 0 ? 'pos' : 'neg'}`}>{money(live.pnl)}</span>
        <span className="live-sub">{live.wins}W–{live.settled - live.wins}L · updates in seconds</span>
      </div>
      <div className="live-rows">
        {live.recent.slice(0, 4).map((r, i) => (
          <div className="live-row" key={`${r.at}-${i}`}>
            <span className={`live-chip ${r.pnl >= 0 ? 'pos-bg' : 'neg-bg'}`}>
              {r.pnl >= 0 ? 'WIN' : 'LOSS'}
            </span>
            <span className="live-title">
              {r.title}{r.outcome ? ` — ${r.outcome}` : ''}
            </span>
            <span className={`live-amt ${r.pnl >= 0 ? 'pos' : 'neg'}`}>{money(r.pnl)}</span>
            <span className="live-when">{ago(r.at)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
