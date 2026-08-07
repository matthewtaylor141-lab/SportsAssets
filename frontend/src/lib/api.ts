export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Headers must merge AFTER the init spread: spreading init last let a
  // caller's headers object REPLACE the merged one, silently dropping
  // Content-Type — every admin POST with a JSON body then went out as
  // text/plain and the server 422'd it (trade desk, 2026-08-07).
  const { headers: initHeaders, ...rest } = init || {}
  const resp = await fetch(`${API_BASE}${path}`, {
    // 20s ceiling: a hung request fails fast instead of freezing the UI
    // (callers catch and show their own placeholder/error states).
    signal: init?.signal ?? AbortSignal.timeout(20000),
    ...rest,
    headers: { 'Content-Type': 'application/json', ...(initHeaders || {}) },
  })
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
  return resp.json() as Promise<T>
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
