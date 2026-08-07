export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const RETRYABLE_STATUS = new Set([429, 502, 503, 504])

// Last-good response cache: during a deploy blip the pages keep showing
// real data instead of flashing "API unreachable" (owner directive
// 2026-08-07: no-glitch app). GETs only; never cached across reloads.
const lastGood = new Map<string, unknown>()

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Headers must merge AFTER the init spread: spreading init last let a
  // caller's headers object REPLACE the merged one, silently dropping
  // Content-Type — every admin POST with a JSON body then went out as
  // text/plain and the server 422'd it (trade desk, 2026-08-07).
  const { headers: initHeaders, ...rest } = init || {}
  const method = (init?.method || 'GET').toUpperCase()
  // Reads retry through deploy blips; writes NEVER auto-retry — a
  // retried order is a double order.
  const retries = method === 'GET' ? 2 : 0
  let lastErr: unknown
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const resp = await fetch(`${API_BASE}${path}`, {
        signal: init?.signal ?? AbortSignal.timeout(30000),
        ...rest,
        headers: { 'Content-Type': 'application/json', ...(initHeaders || {}) },
      })
      if (!resp.ok) {
        if (RETRYABLE_STATUS.has(resp.status) && attempt < retries) {
          await sleep(700 * (attempt + 1))
          continue
        }
        throw new Error(`HTTP ${resp.status}${resp.statusText ? ` ${resp.statusText}` : ''}`)
      }
      const data = (await resp.json()) as T
      if (method === 'GET') lastGood.set(path, data)
      return data
    } catch (e) {
      lastErr = e
      const transient = e instanceof TypeError
        || (e as { name?: string })?.name === 'TimeoutError'
        || (e as { name?: string })?.name === 'AbortError'
      if (transient && attempt < retries) {
        await sleep(700 * (attempt + 1))
        continue
      }
      break
    }
  }
  // Serve the last good copy rather than an error screen; the next
  // successful poll replaces it silently.
  if (method === 'GET' && lastGood.has(path)) return lastGood.get(path) as T
  throw lastErr
}

export function adminApi<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  return api<T>(path, { ...init, headers: { 'X-Admin-Token': token, ...(init?.headers || {}) } })
}

/** Stable anonymous identity for prefs + push subscriptions. */
export function userKey(): string {
  let key = localStorage.getItem('sa_user_key')
  if (!key) {
    key = crypto.randomUUID()
    localStorage.setItem('sa_user_key', key)
  }
  return key
}
