import { API_BASE } from './api'
import { deskAdminToken, deskToken } from './desk'

// TV wall session (owner order 2026-08-23): the office TVs unlock once
// with the shared desk password — POST /api/wall/unlock mints a 7-DAY
// wall token (scope 'wall:', read-only) — and then keep themselves
// alive forever by silently renewing via /api/wall/renew whenever less
// than 3 days remain. Wall tokens live in localStorage (unlike desk
// tokens: a TV must survive reboots without a keyboard), always travel
// as X-Desk-Token, and any 401 relocks the wall back to the gate.

const WALL_KEY = 'sa_wall_token'
const RENEW_BEFORE_MS = 3 * 24 * 3600 * 1000 // renew when < 3 days left
const EXPIRY_SLACK_MS = 30_000 // never let a token die mid-request

/** Fired on window whenever the wall session dies (expiry or 401). */
export const WALL_RELOCK_EVENT = 'sa-wall-relock'

export interface WallUnlockResult { ok: boolean; error?: string }

/** A chart MERIDIAN projected onto a TV (bounded server-side). */
export interface WallChart {
  title?: string
  kind?: 'line' | 'bar'
  labels?: string[]
  series: { name: string; values: number[] }[]
}

/** One screen's directive inside a MERIDIAN scene. */
export interface WallScreen {
  kind: 'book' | 'report' | 'chart' | 'whales' | 'headline'
  from?: string
  to?: string
  text?: string
  stat?: string
  chart?: WallChart
}

export interface WallState {
  mode: 'book' | 'report' | 'scene'
  from: string | null
  to: string | null
  set_at: number | null
  headline?: string | null
  ttl_s?: number | null
  screens?: { kalshi?: WallScreen; polymarket?: WallScreen } | null
}

// ── Token storage ────────────────────────────────────────────────────

interface StoredWallToken { token: string; expires_at: number }

function readStored(): StoredWallToken | null {
  try {
    const raw = localStorage.getItem(WALL_KEY)
    if (!raw) return null
    const j = JSON.parse(raw) as StoredWallToken
    if (!j || typeof j.token !== 'string') return null
    return j
  } catch {
    return null
  }
}

function storeToken(token: string, expiresAt: number | null | undefined): void {
  const exp = expiresAt ?? tokenExp(token) ?? 0
  try {
    localStorage.setItem(WALL_KEY, JSON.stringify({ token, expires_at: exp }))
  } catch { /* storage unavailable — the session just won't persist */ }
}

/** Expiry (epoch seconds) parsed from the token's own "<exp>.<hmac>" shape. */
function tokenExp(token: string): number | null {
  const exp = parseInt(token.split('.')[0] || '', 10)
  return Number.isFinite(exp) ? exp : null
}

/** Current wall token, or null (expired tokens are purged on read). */
export function wallToken(): string | null {
  const j = readStored()
  if (!j) return null
  const exp = tokenExp(j.token) ?? j.expires_at
  if (!exp || exp * 1000 <= Date.now() + EXPIRY_SLACK_MS) {
    try { localStorage.removeItem(WALL_KEY) } catch { /* ignore */ }
    return null
  }
  return j.token
}

export function wallAuthed(): boolean {
  return wallToken() != null
}

/** Drop the wall session and tell every mounted wall page to show the gate. */
export function wallRelock(): void {
  try { localStorage.removeItem(WALL_KEY) } catch { /* ignore */ }
  window.dispatchEvent(new Event(WALL_RELOCK_EVENT))
}

// ── Unlock / renew ───────────────────────────────────────────────────

/** Exchange the desk password for a 7-day wall token. Never throws. */
export async function wallUnlock(password: string): Promise<WallUnlockResult> {
  try {
    const resp = await fetch(`${API_BASE}/api/wall/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
      signal: AbortSignal.timeout(15_000),
    })
    if (resp.status === 401 || resp.status === 403) return { ok: false, error: 'Wrong password.' }
    if (resp.status === 429) return { ok: false, error: 'Too many attempts — wait a minute.' }
    if (!resp.ok) return { ok: false, error: `API error (HTTP ${resp.status}).` }
    const r = (await resp.json()) as { ok?: boolean; token?: string; expires_at?: number; error?: string }
    if (r.ok && r.token) {
      storeToken(r.token, r.expires_at)
      return { ok: true }
    }
    return { ok: false, error: r.error || 'Wrong password.' }
  } catch {
    return { ok: false, error: 'API unreachable — check the service status.' }
  }
}

let renewInFlight = false

/**
 * Silent renew: when the wall token has < 3 days left, trade it for a
 * fresh 7-day one via /api/wall/renew. Safe to call on every poll tick
 * (self-throttled); failures are silent — the token still has days of
 * life, and the next tick tries again.
 */
export async function maybeRenewWallToken(): Promise<void> {
  if (renewInFlight) return
  const tok = wallToken()
  if (!tok) return
  const exp = tokenExp(tok)
  if (exp == null || exp * 1000 - Date.now() > RENEW_BEFORE_MS) return
  renewInFlight = true
  try {
    const resp = await fetch(`${API_BASE}/api/wall/renew`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Desk-Token': tok },
      body: '{}',
      signal: AbortSignal.timeout(15_000),
    })
    if (resp.ok) {
      const r = (await resp.json()) as { ok?: boolean; token?: string; expires_at?: number }
      if (r.ok && r.token) storeToken(r.token, r.expires_at)
    }
  } catch { /* silent — retried on the next poll tick */ } finally {
    renewInFlight = false
  }
}

// ── Authed fetch ─────────────────────────────────────────────────────

/**
 * Fetch wrapper for every wall call. Plain fetch on purpose — the
 * api() helper's retry + last-good cache would mask outages from the
 * wall's failure watchdog, and an unattended TV needs honest failures
 * so it can reload itself. A 401 relocks the wall and rethrows.
 */
export async function wallApi<T>(path: string, init?: RequestInit): Promise<T> {
  const tok = wallToken()
  if (!tok) {
    wallRelock()
    throw new Error('wall session expired')
  }
  const { headers: initHeaders, ...rest } = init || {}
  const resp = await fetch(`${API_BASE}${path}`, {
    signal: init?.signal ?? AbortSignal.timeout(15_000),
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      'X-Desk-Token': tok,
      ...((initHeaders as Record<string, string>) || {}),
    },
  })
  if (resp.status === 401) {
    wallRelock()
    throw new Error('HTTP 401')
  }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return (await resp.json()) as T
}

// ── Broadcast (control page) ─────────────────────────────────────────

export interface WallBroadcastBody {
  mode: 'book' | 'report' | 'scene'
  from?: string
  to?: string
  headline?: string
  ttl_s?: number
  screens?: { kalshi?: WallScreen; polymarket?: WallScreen }
}

/**
 * POST /api/wall/broadcast. Wall tokens are read-only there (403), so
 * the control page prefers a real desk/admin credential when this
 * browser holds one (the control gate mints a desk session alongside
 * the wall token — same password) and only falls back to the wall
 * token, whose 403 becomes a human-readable message. Never throws.
 */
export async function wallBroadcast(body: WallBroadcastBody): Promise<{ ok: boolean; error?: string }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const dtok = deskToken()
  const atok = deskAdminToken()
  const wtok = wallToken()
  if (dtok) headers['X-Desk-Token'] = dtok
  else if (wtok) headers['X-Desk-Token'] = wtok
  if (atok) headers['X-Admin-Token'] = atok
  if (!headers['X-Desk-Token'] && !atok) {
    return { ok: false, error: 'Locked — enter the password first.' }
  }
  try {
    const resp = await fetch(`${API_BASE}/api/wall/broadcast`, {
      method: 'POST', headers, body: JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    })
    if (resp.status === 401) return { ok: false, error: 'Session expired — enter the password again.' }
    if (resp.status === 403) {
      return {
        ok: false,
        error: 'This device only holds a read-only wall token — re-enter the password here to control the wall.',
      }
    }
    if (!resp.ok) return { ok: false, error: `API error (HTTP ${resp.status}).` }
    return { ok: true }
  } catch {
    return { ok: false, error: 'API unreachable — check the service status.' }
  }
}

// ── ET date helpers (report ranges + the 04:10 nightly reload) ───────

/** Today's date in ET as YYYY-MM-DD (en-CA locale prints ISO order). */
export function etToday(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
}

/** N days before now, as an ET YYYY-MM-DD day. */
export function etDaysAgo(n: number): string {
  return new Date(Date.now() - n * 86_400_000)
    .toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
}

/** Current ET wall-clock time as "HH:MM" (24h). */
export function etHM(): string {
  return new Date().toLocaleTimeString('en-GB', {
    timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit',
  })
}

// ── Shared payload shapes (wall-only endpoints) ──────────────────────

/** GET /api/today-live — the copy record's live edge. */
export interface TodayLive {
  pnl: number
  settled: number
  wins: number
  recent: { title: string; outcome: string | null; whale: string | null; pnl: number; at: string | null }[]
  warming?: boolean
}

/** One category cell of /api/daily-breakdown. */
export interface BreakdownCat { pnl: number; settled: number; wins: number; losses: number }

/** One ET day of /api/daily-breakdown: date + account + category cells. */
export interface BreakdownDay {
  date: string
  account: number | null
  [category: string]: BreakdownCat | string | number | null | undefined
}

export interface DailyBreakdown {
  from: string
  to: string
  days: BreakdownDay[]
  totals: Record<string, BreakdownCat>
  net_pnl: number
}

/** Category keys that are NOT part of the copies column. */
const NON_COPY_CATS = new Set(['manual', 'underdog', 'arb', 'external', 'residual'])

/** The category cells of one breakdown day (metadata keys stripped). */
export function dayCategories(d: BreakdownDay): [string, BreakdownCat][] {
  const out: [string, BreakdownCat][] = []
  for (const [k, v] of Object.entries(d)) {
    if (k === 'date' || k === 'account' || v == null || typeof v !== 'object') continue
    out.push([k, v as BreakdownCat])
  }
  return out
}

/** True when the category key belongs in the copies column. */
export function isCopyCategory(cat: string): boolean {
  return !NON_COPY_CATS.has(cat)
}

// ── Feed card shape (subset of the desk v8 contract) ─────────────────

export interface WallFeedOutcome { label: string; id: string; price: number | null }
export interface WallFeedCard {
  id: string
  title: string
  league?: string
  outcomes: WallFeedOutcome[]
  volume_usd?: number | null
}

interface LegacyFeedGame {
  id: string; title: string; league?: string
  outcomes: { label: string; ticker?: string; asset?: string; us_slug?: string; price: number | null }[]
}

/**
 * Fetch the live-markets strip, accepting both the v8 `cards` shape and
 * the legacy `games` shape (same tolerance the desk feed has).
 */
export async function wallFeed(venue: string, league: string): Promise<WallFeedCard[]> {
  const r = await wallApi<{ cards?: WallFeedCard[]; games?: LegacyFeedGame[] }>(
    `/api/admin/desk-feed?venue=${venue}&league=${league}`)
  if (r.cards) return r.cards
  return (r.games || []).map((g) => ({
    id: g.id, title: g.title, league: g.league,
    outcomes: (g.outcomes || []).map((o) => ({
      label: o.label, id: o.ticker || o.asset || o.us_slug || '', price: o.price,
    })),
  }))
}
