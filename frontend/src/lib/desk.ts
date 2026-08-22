import { api } from './api'

// Desk session (owner order 2026-08-22): the team unlocks the desk with
// the shared desk password — POST /api/desk/unlock mints a 12h token —
// while the admin token keeps working everywhere as a superset. Tokens
// live in sessionStorage only (never localStorage, never logged) and the
// token's own "<exp>.<hmac>" shape lets the client relock the UI the
// moment it expires instead of surfacing raw 401s.

const DESK_KEY = 'sa_desk_token'
const ADMIN_KEY = 'sa_admin_token'

/** Fired on window whenever the desk session dies (expiry or 401). */
export const DESK_RELOCK_EVENT = 'sa-desk-relock'

export interface UnlockResult { ok: boolean; error?: string }

// ── GET /api/desk/accounts payload (shared by Accounts + Trade Desk) ──
export interface PMPosition {
  market_slug: string; title: string; outcome: string; qty: number
  cost: number | null; value: number | null; unrealized: number | null
}
export interface KPosition {
  ticker: string; qty: number; cost_usd: number | null
  mark_bid: number | null; value_usd: number | null; unrealized: number | null
}
export interface DeskAccounts {
  as_of: string | number
  polymarket: {
    configured: boolean; account_value: number | null; cash: number | null
    buying_power: number | null; open_value: number | null
    unsettled_funds: number | null; realized_pnl: number | null
    positions: PMPosition[]; recent_trades: Record<string, unknown>[] | null
  }
  kalshi: {
    configured: boolean; balance_usd: number | null; at: number | null
    stale_s: number | null; exposure_usd: number | null; resting: number | null
    positions: KPosition[]; degraded?: boolean
  }
  totals: { value: number | null; cash: number | null; unrealized: number | null }
}

/** Current desk token, or null (expired tokens are purged on read). */
export function deskToken(): string | null {
  const tok = sessionStorage.getItem(DESK_KEY)
  if (!tok) return null
  const exp = parseInt(tok.split('.')[0] || '', 10)
  // 30s of slack so a token never dies mid-request.
  if (!Number.isFinite(exp) || exp * 1000 <= Date.now() + 30_000) {
    sessionStorage.removeItem(DESK_KEY)
    return null
  }
  return tok
}

/** Admin token, if this browser also holds one (superset credential). */
export function deskAdminToken(): string | null {
  return sessionStorage.getItem(ADMIN_KEY) || null
}

/** True when either credential is present (desk token pre-checked for expiry). */
export function deskAuthed(): boolean {
  return deskToken() != null || deskAdminToken() != null
}

/** Drop the desk session and tell every mounted desk page to show the gate. */
export function deskRelock(): void {
  sessionStorage.removeItem(DESK_KEY)
  window.dispatchEvent(new Event(DESK_RELOCK_EVENT))
}

/** Exchange the desk password for a session token. Never throws. */
export async function deskUnlock(password: string): Promise<UnlockResult> {
  try {
    const r = await api<{ ok: boolean; token?: string; error?: string }>(
      '/api/desk/unlock',
      { method: 'POST', body: JSON.stringify({ password }) },
    )
    if (r.ok && r.token) {
      sessionStorage.setItem(DESK_KEY, r.token)
      return { ok: true }
    }
    return { ok: false, error: r.error || 'Wrong password.' }
  } catch (e: unknown) {
    const msg = (e as { message?: string })?.message || ''
    if (/HTTP (401|403)/.test(msg)) return { ok: false, error: 'Wrong password.' }
    if (/HTTP 429/.test(msg)) return { ok: false, error: 'Too many attempts — wait a minute.' }
    return { ok: false, error: 'API unreachable — check the service status.' }
  }
}

/**
 * Fetch wrapper for every desk-page call. Sends X-Desk-Token when a live
 * desk session exists and X-Admin-Token when the browser holds one (both
 * when both — the server accepts either), so the same code path serves
 * team members and the owner. A 401 means the session died server-side:
 * relock the UI and rethrow.
 */
export function deskApi<T>(path: string, init?: RequestInit): Promise<T> {
  const dtok = deskToken()
  const atok = deskAdminToken()
  if (!dtok && !atok) {
    deskRelock()
    return Promise.reject(new Error('desk session expired'))
  }
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) || {}),
  }
  if (dtok) headers['X-Desk-Token'] = dtok
  if (atok) headers['X-Admin-Token'] = atok
  return api<T>(path, { ...init, headers }).catch((e: unknown) => {
    if (/HTTP 401/.test((e as { message?: string })?.message || '')) deskRelock()
    throw e
  })
}
