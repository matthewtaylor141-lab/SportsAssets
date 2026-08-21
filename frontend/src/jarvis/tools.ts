/* JARVIS tool belt: schemas the in-app Claude sees + local executors.
 *
 * Every executor returns a COMPACT JSON string for the model — payloads are
 * trimmed hard (the full /api/live-status is ~25 rows of order history the
 * model never needs). Some tools also drive the UI (report panel) through
 * the JarvisUI callbacks.  Platform reads go through lib/api's `api()` so
 * they inherit its retry + last-good-cache behavior.
 */

import { API_BASE, adminApi, api } from '../lib/api'
import type { ToolSchema } from './claude'

export interface JarvisUI {
  showMarkdown: (title: string, markdown: string) => void
  showPdf: (title: string, url: string) => void
}

export interface ToolContext {
  adminToken: string
  ui: JarvisUI
}

/* ── schemas ─────────────────────────────────────────────────────────── */

const none = { type: 'object' as const, properties: {} }

export const JARVIS_TOOLS: ToolSchema[] = [
  {
    name: 'get_live_status',
    description:
      'Live real-money copy-trading status: armed/paused, caps, lifetime summary (orders, fills, deployed, realized P&L), ' +
      'per-whale record, 24h sizing audit, manual-desk diagnostics, and the 7-day fill-vs-miss scorecard. ' +
      'Call this for "how are we doing live", kill-switch state, or per-whale grading.',
    input_schema: none,
  },
  {
    name: 'get_today',
    description:
      "Today's settlements from our own ledger, updated within seconds: day P&L, settled count, wins, and the latest settled trades. " +
      'Call this for "how are we doing today".',
    input_schema: none,
  },
  {
    name: 'get_track_record_summary',
    description:
      'The headline account track record since Aug 1 (the venue account itself, not telemetry): totals, ROI, win rate, ' +
      'whole-account tie-out, copy-sleeve cohort, and the last 7 daily rows.',
    input_schema: none,
  },
  {
    name: 'get_copies_record',
    description:
      'The COPIES cohort record the copy-trading thesis stands on: uncapped, venue-backed totals plus a per-whale split and the last 7 days.',
    input_schema: none,
  },
  {
    name: 'get_report',
    description:
      'Fetch the operating report (markdown) for a period. Returns the first part of the text to you AND renders the full report on ' +
      "Matt's screen. Prefer this when he asks to \"see\" or \"pull up\" a report.",
    input_schema: {
      type: 'object',
      properties: {
        period: { type: 'string', enum: ['daily', 'weekly', 'monthly'], description: 'Report window.' },
      },
      required: ['period'],
    },
  },
  {
    name: 'show_report',
    description:
      "Put a performance report on Matt's screen without reading it: format \"pdf\" shows the downloadable category P&L PDF, " +
      '"md" renders the operating report as text. Say the headline yourself; the screen carries the detail.',
    input_schema: {
      type: 'object',
      properties: {
        period: { type: 'string', enum: ['daily', 'weekly', 'monthly'], description: 'Report window.' },
        format: { type: 'string', enum: ['pdf', 'md'], description: 'pdf (default) or md.' },
      },
      required: ['period'],
    },
  },
  {
    name: 'get_engine_status',
    description:
      "The autonomous engine's heartbeat: status, heartbeat age, verdict, cycle time, and the funnel counters " +
      '(feed events → venue matches → books checked → logged), plus budget and any error.',
    input_schema: none,
  },
  {
    name: 'get_whale',
    description:
      'Look up one copied whale by id, username (e.g. "swisstony", "RN1"), or wallet address prefix. ' +
      'Returns their profile summary: realized P&L, volume, drawdown, trade count, and top sports.',
    input_schema: {
      type: 'object',
      properties: {
        id_or_name: { type: 'string', description: 'Whale id, username, or 0x… address prefix.' },
      },
      required: ['id_or_name'],
    },
  },
  {
    name: 'show_markdown',
    description:
      "Render markdown you compose onto Matt's report screen (headings, bold, tables, lists, code). " +
      'Use it for anything too dense to say out loud — comparisons, breakdowns, checklists.',
    input_schema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Short panel title.' },
        markdown: { type: 'string', description: 'The markdown body to render.' },
      },
      required: ['title', 'markdown'],
    },
  },
  {
    name: 'leave_note_for_engine_session',
    description:
      "Queue a note for the autonomous engine session — the co-CEO's main coding session that runs the platform — which reads its " +
      'notes at its next hourly check-in. Use for anything Matt wants changed, investigated, built, or watched. ' +
      'Write the note as a clear instruction with any numbers or names it needs. Requires the platform admin token.',
    input_schema: {
      type: 'object',
      properties: {
        note: { type: 'string', description: 'The note, written as a self-contained instruction.' },
      },
      required: ['note'],
    },
  },
]

/* ── executors ───────────────────────────────────────────────────────── */

type Dict = Record<string, unknown>

/** Round every finite number in a payload to 2dp — keeps tool results
 * compact and stops 17-digit float noise reaching the model. */
function slim(v: unknown): unknown {
  if (typeof v === 'number') return Number.isFinite(v) ? Math.round(v * 100) / 100 : v
  if (Array.isArray(v)) return v.map(slim)
  if (v && typeof v === 'object') {
    const out: Dict = {}
    for (const [k, val] of Object.entries(v as Dict)) out[k] = slim(val)
    return out
  }
  return v
}

const pack = (v: unknown): string => JSON.stringify(slim(v))
const fail = (system: string, e: unknown): string =>
  JSON.stringify({ error: `Couldn't reach ${system}: ${e instanceof Error ? e.message : String(e)}` })

const isoDaysAgo = (n: number): string =>
  new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10)

const PERIODS: Record<string, number> = { daily: 0, weekly: 6, monthly: 29 }
const normPeriod = (p: unknown): 'daily' | 'weekly' | 'monthly' =>
  p === 'daily' || p === 'monthly' ? p : 'weekly'

async function getLiveStatus(): Promise<string> {
  try {
    const d = await api<Dict>('/api/live-status')
    return pack({
      enabled: d.enabled,
      venue: d.venue,
      paused: d.paused,
      caps: d.caps,
      summary: d.summary,
      by_whale: d.by_whale,
      sizing_24h: d.sizing_24h,
      manual_desk: d.manual_desk,
      scorecard_fill_vs_miss_7d: d.fill_vs_miss_7d,
    })
  } catch (e) {
    return fail('the live trading status', e)
  }
}

async function getToday(): Promise<string> {
  try {
    const d = await api<{ pnl: number; settled: number; wins: number; recent: Dict[] }>('/api/today-live')
    return pack({
      pnl: d.pnl,
      settled: d.settled,
      wins: d.wins,
      losses: d.settled - d.wins,
      recent: (d.recent || []).slice(0, 8),
    })
  } catch (e) {
    return fail("today's ledger", e)
  }
}

async function getTrackRecordSummary(): Promise<string> {
  try {
    const d = await api<Dict>('/api/track-record?since=2026-08-01')
    const daily = (d.daily as Dict[]) || []
    return pack({
      since: d.since,
      summary: d.summary,
      account: d.account,
      record_subset: d.record_subset,
      copy_sleeve: d.copy_sleeve,
      snapshot_age_s: (d.snapshot as Dict | undefined)?.age_s,
      last_7_days: daily.slice(-7),
    })
  } catch (e) {
    return fail('the track record', e)
  }
}

async function getCopiesRecord(): Promise<string> {
  try {
    const d = await api<Dict>('/api/copies-record')
    return pack({
      since: d.since,
      uncapped: d.uncapped,
      total: d.total,
      by_whale: ((d.by_whale as Dict[]) || []).slice(0, 8),
      last_7_days: ((d.daily as Dict[]) || []).slice(-7),
    })
  } catch (e) {
    return fail('the copies record', e)
  }
}

async function fetchReportMd(period: string): Promise<string> {
  const resp = await fetch(`${API_BASE}/api/report?period=${period}&format=md`, {
    signal: AbortSignal.timeout(30000),
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return await resp.text()
}

async function getReport(input: Dict, ctx: ToolContext): Promise<string> {
  const period = normPeriod(input.period)
  try {
    const md = await fetchReportMd(period)
    ctx.ui.showMarkdown(`${period[0].toUpperCase()}${period.slice(1)} report`, md)
    const head = md.slice(0, 4000)
    return head + (md.length > 4000
      ? `\n\n[…truncated — the full ${period} report is rendered on Matt's screen]`
      : '\n\n[The full report is also rendered on Matt\'s screen]')
  } catch (e) {
    return fail(`the ${period} report`, e)
  }
}

async function showReport(input: Dict, ctx: ToolContext): Promise<string> {
  const period = normPeriod(input.period)
  const format = input.format === 'md' ? 'md' : 'pdf'
  const title = `${period[0].toUpperCase()}${period.slice(1)} report`
  if (format === 'md') {
    try {
      const md = await fetchReportMd(period)
      ctx.ui.showMarkdown(title, md)
      return `Rendered the ${period} operating report on screen.`
    } catch (e) {
      return fail(`the ${period} report`, e)
    }
  }
  // The PDF endpoint keys on from/to dates; derive them from the period
  // (period itself rides along, harmless, for future backends).
  const from = isoDaysAgo(PERIODS[period])
  const to = isoDaysAgo(0)
  ctx.ui.showPdf(title, `${API_BASE}/api/report.pdf?period=${period}&from=${from}&to=${to}`)
  return `Showing the ${period} PDF report (${from} → ${to}) on screen.`
}

async function getEngineStatus(): Promise<string> {
  try {
    const d = await api<Dict>('/api/engine/status')
    const detail = (d.detail as Dict) || {}
    const beatAt = d.beat_at as string | undefined
    const age = beatAt ? Math.round((Date.now() - new Date(beatAt).getTime()) / 1000) : null
    return pack({
      status: d.status,
      heartbeat_age_s: age,
      verdict: detail.verdict,
      cycle_s: detail.cycle_s,
      funnel: {
        feed_events: detail.feed_events,
        matched: detail.matched,
        books_checked: detail.books_checked,
        logged: detail.logged,
        studied: detail.studied,
      },
      edges: detail.edges,
      budget: detail.budget,
      blockers: detail.blockers,
      error: detail.error,
    })
  } catch (e) {
    return fail('the engine heartbeat', e)
  }
}

interface WhaleRow {
  id: number
  address: string
  username: string | null
  pinned: boolean
  active: boolean
  sports_profit_alltime: number | null
}

async function getWhale(input: Dict): Promise<string> {
  const q = String(input.id_or_name ?? '').trim().toLowerCase()
  if (!q) return JSON.stringify({ error: 'id_or_name is required' })
  try {
    const whales = await api<WhaleRow[]>('/api/whales?include_inactive=true')
    const match =
      whales.find((w) => String(w.id) === q) ||
      whales.find((w) => (w.username || '').toLowerCase() === q) ||
      whales.find((w) => (w.username || '').toLowerCase().includes(q)) ||
      whales.find((w) => w.address.toLowerCase().startsWith(q))
    if (!match) {
      return JSON.stringify({
        error: `No whale matching "${q}"`,
        known: whales.slice(0, 12).map((w) => w.username || w.address.slice(0, 10)),
      })
    }
    const profile = await api<Dict>(`/api/whales/${match.id}`)
    const stats = ((profile.stats as Dict[]) || [])
      .filter((s) => s.window === 'all')
      .sort((a, b) => ((b.realized_pnl as number) || 0) - ((a.realized_pnl as number) || 0))
      .slice(0, 5)
      .map((s) => ({
        sport: s.sport, realized_pnl: s.realized_pnl, win_pct: s.win_pct,
        markets_traded: s.markets_traded, roi: s.roi,
      }))
    return pack({
      whale: {
        id: match.id, username: match.username, address: match.address,
        pinned: match.pinned, active: match.active,
        sports_profit_alltime: match.sports_profit_alltime,
      },
      summary: profile.summary,
      top_sports_alltime: stats,
    })
  } catch (e) {
    return fail('the whale database', e)
  }
}

async function showMarkdownTool(input: Dict, ctx: ToolContext): Promise<string> {
  const title = String(input.title ?? 'JARVIS')
  const md = String(input.markdown ?? '')
  if (!md.trim()) return JSON.stringify({ error: 'markdown is empty' })
  ctx.ui.showMarkdown(title, md)
  return `Rendered "${title}" on screen.`
}

async function leaveNote(input: Dict, ctx: ToolContext): Promise<string> {
  const note = String(input.note ?? '').trim()
  if (!note) return JSON.stringify({ error: 'note is empty' })
  if (!ctx.adminToken) {
    return JSON.stringify({
      error: 'No admin token configured. Matt can add it in JARVIS settings (gear icon) to enable notes.',
    })
  }
  try {
    const r = await adminApi<Dict>('/api/admin/jarvis-note', ctx.adminToken, {
      method: 'POST',
      body: JSON.stringify({ note }),
    })
    return pack({ ok: true, detail: r })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.includes('404')) {
      return JSON.stringify({
        error: "The notes bridge isn't deployed yet — the backend endpoint is still being added. Try again after the next deploy.",
      })
    }
    if (msg.includes('401') || msg.includes('403')) {
      return JSON.stringify({ error: 'The admin token was rejected — Matt should re-enter it in JARVIS settings.' })
    }
    return fail('the notes bridge', e)
  }
}

/** Bind a tool executor to its UI + credentials context. */
export function createToolExecutor(ctx: ToolContext) {
  return async (name: string, rawInput: unknown): Promise<string> => {
    const input = (rawInput && typeof rawInput === 'object' ? rawInput : {}) as Dict
    switch (name) {
      case 'get_live_status': return getLiveStatus()
      case 'get_today': return getToday()
      case 'get_track_record_summary': return getTrackRecordSummary()
      case 'get_copies_record': return getCopiesRecord()
      case 'get_report': return getReport(input, ctx)
      case 'show_report': return showReport(input, ctx)
      case 'get_engine_status': return getEngineStatus()
      case 'get_whale': return getWhale(input)
      case 'show_markdown': return showMarkdownTool(input, ctx)
      case 'leave_note_for_engine_session': return leaveNote(input, ctx)
      default: return JSON.stringify({ error: `Unknown tool: ${name}` })
    }
  }
}
