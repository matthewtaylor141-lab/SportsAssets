export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
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
