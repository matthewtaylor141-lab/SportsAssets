import { useEffect, useRef, useState } from 'react'
import { api } from './api'

/** Poll a GET endpoint with a stale-while-revalidate cache.
 *
 * The last good payload is kept in localStorage and rendered on mount
 * BEFORE any network round-trip, so a returning visitor sees a full page
 * instantly — even when the API is cold (a fresh deploy's first fetch can
 * take tens of seconds) the screen shows the last known numbers, then
 * swaps in fresh ones. Polling pauses while the tab is hidden (a
 * background tab was re-downloading a multi-hundred-KB payload every 30s
 * for nobody) and refetches immediately when the tab is shown again.
 *
 * `accept` maps an app-level error inside a 200 payload (e.g. {error})
 * to an error string; such payloads are neither rendered nor cached.
 */
export function usePolled<T>(
  path: string,
  refreshMs = 30_000,
  accept?: (d: T) => string | null,
  // Return false to REFUSE replacing the current data with the incoming
  // payload (e.g. a freshly-booted API briefly serving a degraded
  // snapshot). Refused payloads are not rendered and not cached; the next
  // poll tries again.
  keep?: (prev: T, next: T) => boolean,
) {
  const key = `sa_cache:${path}`
  const [data, setData] = useState<T | null>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw ? (JSON.parse(raw) as T) : null
    } catch {
      return null
    }
  })
  const [err, setErr] = useState<string | null>(null)
  // Synchronous mirror of `data` for the keep() comparison — React state
  // updaters are async, so deciding inside one and acting outside races.
  const dataRef = useRef<T | null>(data)

  useEffect(() => {
    // Path changed (e.g. a filter in the URL): show that path's cache, not
    // the previous one's.
    try {
      const raw = localStorage.getItem(key)
      dataRef.current = raw ? (JSON.parse(raw) as T) : null
    } catch {
      dataRef.current = null
    }
    setData(dataRef.current)
    let dead = false
    const load = async () => {
      try {
        const d = await api<T>(path)
        if (dead) return
        const bad = accept?.(d) ?? null
        if (bad) {
          setErr(bad)
          return
        }
        const prev = dataRef.current
        if (prev !== null && keep && !keep(prev, d)) return
        dataRef.current = d
        setData(d)
        setErr(null)
        try {
          localStorage.setItem(key, JSON.stringify(d))
        } catch {
          /* quota: stale cache beats no cache */
        }
      } catch (e) {
        if (!dead) setErr(String(e))
      }
    }
    load()
    const t = window.setInterval(() => {
      if (!document.hidden) load()
    }, refreshMs)
    const onShow = () => {
      if (!document.hidden) load()
    }
    document.addEventListener('visibilitychange', onShow)
    return () => {
      dead = true
      clearInterval(t)
      document.removeEventListener('visibilitychange', onShow)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, refreshMs])

  return { data, err }
}
