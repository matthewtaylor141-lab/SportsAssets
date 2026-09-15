import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { TradeRow } from '../components/TradeRow'
import { api } from '../lib/api'
import type { ConnState } from '../lib/sse'
import type { Trade, Whale } from '../lib/types'

/** Landing page: real-time trade stream (SSE) over a history backfill, with
 * whale/sport/side/min-notional filters and an audible ping for desk use. */
export function LiveFeed({ live, conn }: { live: Trade[]; conn: ConnState }) {
  const [params, setParams] = useSearchParams()
  const [history, setHistory] = useState<Trade[] | null>(null)
  const [whales, setWhales] = useState<Whale[]>([])
  const [ping, setPing] = useState(() => localStorage.getItem('sa_ping') === '1')
  const audio = useRef<AudioContext | null>(null)

  const whale = params.get('whale') || ''
  const sport = params.get('sport') || ''
  const side = params.get('side') || ''
  const minNotional = params.get('min') || ''

  useEffect(() => {
    api<Whale[]>('/api/whales').then(setWhales).catch(() => {})
  }, [])

  useEffect(() => {
    const q = new URLSearchParams({ limit: '100' })
    if (whale) q.set('whale_id', whale)
    if (sport) q.set('sport', sport)
    if (side) q.set('side', side)
    if (minNotional) q.set('min_notional', minNotional)
    api<Trade[]>(`/api/feed?${q}`).then(setHistory).catch(() => setHistory([]))
  }, [whale, sport, side, minNotional])

  // Audible ping on new live trades (toggle persists).
  const liveCount = live.length
  useEffect(() => {
    if (!ping || liveCount === 0) return
    audio.current ??= new AudioContext()
    const ctx = audio.current
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.frequency.value = 880
    gain.gain.setValueAtTime(0.06, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25)
    osc.connect(gain).connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.25)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveCount])

  const merged = useMemo(() => {
    const seen = new Set<number>()
    const match = (t: Trade) =>
      (!whale || t.whale_id === Number(whale)) &&
      (!sport || t.sport === sport) &&
      (!side || t.side === side) &&
      (!minNotional || t.notional >= Number(minNotional))
    const out: Trade[] = []
    for (const t of [...live.filter(match), ...(history || [])]) {
      if (!seen.has(t.id)) {
        seen.add(t.id)
        out.push(t)
      }
    }
    return out
  }, [live, history, whale, sport, side, minNotional])

  const liveIds = useMemo(() => new Set(live.slice(0, 5).map((t) => t.id)), [live])
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }
  const sports = [...new Set(merged.map((t) => t.sport).filter((s) => s !== 'unclassified'))].sort()

  return (
    <>
      <h1>Live Feed</h1>
      <p className="sub">Every fill by the tracked whales, streamed as it lands on-chain.</p>
      <div className="feed-filters">
        <select value={whale} onChange={(e) => setParam('whale', e.target.value)} aria-label="Filter by whale">
          <option value="">All whales</option>
          {whales.map((w) => (
            <option key={w.id} value={w.id}>
              {w.username || w.address.slice(0, 10)}
            </option>
          ))}
        </select>
        <select value={sport} onChange={(e) => setParam('sport', e.target.value)} aria-label="Filter by sport">
          <option value="">All sports</option>
          {sports.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <select value={side} onChange={(e) => setParam('side', e.target.value)} aria-label="Filter by side">
          <option value="">Buys + sells</option>
          <option value="BUY">BUY only</option>
          <option value="SELL">SELL only</option>
        </select>
        <input
          type="number"
          placeholder="Min $"
          value={minNotional}
          style={{ width: 90 }}
          onChange={(e) => setParam('min', e.target.value)}
          aria-label="Minimum notional"
        />
        <button
          className="btn"
          onClick={() => {
            const next = !ping
            setPing(next)
            localStorage.setItem('sa_ping', next ? '1' : '0')
          }}
        >
          {ping ? '🔔 Ping on' : '🔕 Ping off'}
        </button>
      </div>
      <div className="card" style={{ padding: 0 }}>
        {history === null ? (
          <EmptyState>Loading feed…</EmptyState>
        ) : merged.length === 0 ? (
          <EmptyState>
            {conn === 'live'
              ? 'Connected — no trades match yet. The feed fills as tracked whales trade.'
              : 'Waiting for the stream… make sure the API is running.'}
          </EmptyState>
        ) : (
          merged.map((t) => <TradeRow key={t.id} trade={t} fresh={liveIds.has(t.id)} />)
        )}
      </div>
    </>
  )
}
