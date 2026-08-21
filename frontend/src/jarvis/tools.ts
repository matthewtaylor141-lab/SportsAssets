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
  /** Show/replace the staged-ticket confirmation card (null clears it). */
  stageTicket: (t: StagedTicket | null) => void
}

export interface StagedTicket {
  venue: 'polymarket' | 'kalshi'
  title: string
  outcome: string
  usd: number
  price: number | null
  /** venue payload: PM asset/us_slug; Kalshi ticker + yes/no side. */
  asset?: string
  usSlug?: string
  ticker?: string
  kalshiSide?: 'yes' | 'no'
  stagedAt: number
}

export interface ToolContext {
  adminToken: string
  ui: JarvisUI
  /** The currently staged (unconfirmed) desk ticket, if any. */
  getStaged: () => StagedTicket | null
  /** THE MONEY GATE: true only when Matt's most recent utterance was an
   * explicit confirmation ("yes" / "confirm" / "place it" / "send it" /
   * "do it") spoken within the last 90s. The model cannot fabricate
   * this — it is read from the actual speech transcript. */
  lastUtteranceConfirms: () => boolean
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
    name: 'search_desk_markets',
    description:
      'Search the over-the-counter desk for tradeable markets. venue "polymarket" searches sports markets by team/player/market text; venue "kalshi" searches Kalshi sports events. Returns candidate markets with live prices. Use this FIRST when Matt wants to place a trade, then read him the best match and its price before staging anything.',
    input_schema: {
      type: 'object',
      properties: {
        venue: { type: 'string', enum: ['polymarket', 'kalshi'] },
        query: { type: 'string', description: 'team, player, or market text, e.g. "braves ml" or "sinner"' },
      },
      required: ['venue', 'query'],
    },
  },
  {
    name: 'stage_desk_order',
    description:
      'Stage a REAL-MONEY desk order for confirmation. This does NOT place the trade — it puts the exact ticket on screen and returns the read-back script. Protocol (mandatory): only stage after Matt has named the market, the side, and the dollar amount; then READ THE TICKET BACK to him word for word ("Confirm: fifty dollars on Atlanta Braves moneyline at fifty-eight cents on Polymarket — yes or no?") and WAIT for his answer. Never assume the amount; never stage more than one ticket at a time.',
    input_schema: {
      type: 'object',
      properties: {
        venue: { type: 'string', enum: ['polymarket', 'kalshi'] },
        title: { type: 'string', description: 'human market title for the read-back' },
        outcome: { type: 'string', description: 'the outcome being bought, e.g. "Atlanta Braves"' },
        usd: { type: 'number', description: 'dollar amount Matt explicitly named' },
        asset: { type: 'string', description: 'polymarket: the outcome asset/token id from search' },
        us_slug: { type: 'string', description: 'polymarket: the market slug from search' },
        ticker: { type: 'string', description: 'kalshi: the market ticker from search' },
        kalshi_side: { type: 'string', enum: ['yes', 'no'], description: 'kalshi only' },
        price: { type: 'number', description: 'the live ask from search, for the read-back' },
      },
      required: ['venue', 'title', 'outcome', 'usd'],
    },
  },
  {
    name: 'confirm_staged_order',
    description:
      'Execute the staged desk order. HARD GATE: this only works if Matt\'s most recent spoken words were an explicit confirmation (yes / confirm / place it / send it). Call it ONLY immediately after he confirms the read-back. If it refuses, tell him what it said — never retry without a fresh confirmation. Returns the fill result to speak.',
    input_schema: none,
  },
  {
    name: 'cancel_staged_order',
    description: 'Discard the staged desk ticket (Matt said no, changed his mind, or wants different terms).',
    input_schema: none,
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
/* ── the over-the-counter desk, by voice ─────────────────────────── */

const NEED_TOKEN = JSON.stringify({
  error: 'The desk needs the platform admin token — Matt can add it in MERIDIAN settings (gear icon).',
})

async function searchDeskMarkets(input: Dict, ctx: ToolContext): Promise<string> {
  if (!ctx.adminToken) return NEED_TOKEN
  const venue = String(input.venue ?? 'polymarket')
  const q = String(input.query ?? '').trim()
  if (q.length < 2) return JSON.stringify({ error: 'query too short' })
  try {
    if (venue === 'kalshi') {
      const r = await adminApi<{ markets: {
        ticker: string; title: string; sub_title: string
        yes_ask: number | null; no_ask: number | null
      }[] }>(`/api/admin/kalshi-markets?q=${encodeURIComponent(q)}`, ctx.adminToken)
      return pack({ venue, markets: (r.markets || []).slice(0, 8).map((m) => ({
        ticker: m.ticker, title: m.title, sub: m.sub_title,
        yes_ask: m.yes_ask, no_ask: m.no_ask,
      })) })
    }
    const r = await adminApi<{ markets: {
      slug: string; title: string; event_title: string | null
      outcomes: { outcome: string; asset: string; ask: number | null; bid: number | null }[]
    }[] }>(`/api/admin/market-search?q=${encodeURIComponent(q)}`, ctx.adminToken)
    return pack({ venue, markets: (r.markets || []).slice(0, 6).map((m) => ({
      slug: m.slug, title: m.title, event: m.event_title,
      outcomes: (m.outcomes || []).slice(0, 4).map((o) => ({
        outcome: o.outcome, asset: o.asset, ask: o.ask,
      })),
    })) })
  } catch (e) {
    return fail('the desk market search', e)
  }
}

async function stageDeskOrder(input: Dict, ctx: ToolContext): Promise<string> {
  if (!ctx.adminToken) return NEED_TOKEN
  const venue = input.venue === 'kalshi' ? 'kalshi' as const : 'polymarket' as const
  const usd = Number(input.usd)
  if (!(usd > 0) || usd > 1000) {
    return JSON.stringify({ error: 'usd must be a positive amount Matt explicitly named (max $1000 by voice)' })
  }
  const t: StagedTicket = {
    venue,
    title: String(input.title ?? ''),
    outcome: String(input.outcome ?? ''),
    usd: Math.round(usd * 100) / 100,
    price: input.price != null ? Number(input.price) : null,
    asset: input.asset != null ? String(input.asset) : undefined,
    usSlug: input.us_slug != null ? String(input.us_slug) : undefined,
    ticker: input.ticker != null ? String(input.ticker) : undefined,
    kalshiSide: input.kalshi_side === 'no' ? 'no' : 'yes',
    stagedAt: Date.now(),
  }
  if (venue === 'polymarket' && !t.asset && !t.usSlug) {
    return JSON.stringify({ error: 'polymarket ticket needs asset or us_slug from search_desk_markets' })
  }
  if (venue === 'kalshi' && !t.ticker) {
    return JSON.stringify({ error: 'kalshi ticket needs a ticker from search_desk_markets' })
  }
  ctx.ui.stageTicket(t)
  return JSON.stringify({
    staged: true,
    read_back: `Confirm: $${t.usd} on ${t.outcome} — ${t.title}` +
      (t.price != null ? ` at about ${Math.round(t.price * 100)} cents` : '') +
      ` on ${venue === 'kalshi' ? 'Kalshi' : 'Polymarket'}. Yes or no?`,
    note: 'Read this back to Matt WORD FOR WORD and wait for his answer. Only call confirm_staged_order after an explicit yes.',
  })
}

async function confirmStagedOrder(ctx: ToolContext): Promise<string> {
  if (!ctx.adminToken) return NEED_TOKEN
  const t = ctx.getStaged()
  if (!t) return JSON.stringify({ error: 'No staged ticket. Stage one first and read it back.' })
  if (Date.now() - t.stagedAt > 180000) {
    ctx.ui.stageTicket(null)
    return JSON.stringify({ error: 'The staged ticket expired (3 minutes). Stage it again and re-confirm.' })
  }
  if (!ctx.lastUtteranceConfirms()) {
    return JSON.stringify({
      refused: "Money gate: Matt's most recent spoken words were not an explicit confirmation. Read the ticket back and wait for his yes.",
    })
  }
  try {
    const body = t.venue === 'kalshi'
      ? { venue: 'kalshi', ticker: t.ticker, side: t.kalshiSide || 'yes',
          title: `${t.title} — ${t.outcome}`, usd: t.usd, note: 'via MERIDIAN voice' }
      : { venue: 'polymarket-us', asset: t.asset || '', us_slug: t.usSlug || '',
          ask: t.price ?? undefined, title: `${t.title} — ${t.outcome}`,
          usd: t.usd, note: 'via MERIDIAN voice' }
    const r = await adminApi<Dict>('/api/admin/manual-trade', ctx.adminToken, {
      method: 'POST', body: JSON.stringify(body),
    })
    ctx.ui.stageTicket(null)
    if (r.ok === false) return pack({ placed: false, error: r.error })
    if (r.queued && r.row_id) {
      // Kalshi relays through the engine — poll the row briefly so the
      // spoken answer is the real outcome, not "probably".
      for (let i = 0; i < 12; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        const st = await adminApi<Dict>(
          `/api/admin/manual-order?id=${r.row_id}`, ctx.adminToken).catch(() => null)
        if (st && st.found && st.terminal) return pack({ placed: true, result: st })
      }
      return pack({ placed: true, result: 'queued — the engine is relaying it; check the blotter in a moment' })
    }
    return pack({ placed: true, result: {
      status: r.status ?? (r.filled_shares ? 'filled' : 'unknown'),
      filled_shares: r.filled_shares, fill_price: r.fill_price, error: r.error,
    } })
  } catch (e) {
    ctx.ui.stageTicket(null)
    return fail('the desk order', e)
  }
}

async function cancelStagedOrder(ctx: ToolContext): Promise<string> {
  ctx.ui.stageTicket(null)
  return JSON.stringify({ cancelled: true })
}

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
      case 'search_desk_markets': return searchDeskMarkets(input, ctx)
      case 'stage_desk_order': return stageDeskOrder(input, ctx)
      case 'confirm_staged_order': return confirmStagedOrder(ctx)
      case 'cancel_staged_order': return cancelStagedOrder(ctx)
      case 'leave_note_for_engine_session': return leaveNote(input, ctx)
      default: return JSON.stringify({ error: `Unknown tool: ${name}` })
    }
  }
}
