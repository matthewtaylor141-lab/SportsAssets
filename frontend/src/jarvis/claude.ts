/* Streaming Claude client for the JARVIS cockpit.
 *
 * All calls are made CLIENT-SIDE with the owner's own Anthropic key (typed
 * once, kept in localStorage) — the `anthropic-dangerous-direct-browser-access`
 * header opts this origin into CORS on api.anthropic.com. Implements a small,
 * robust SSE parser over fetch + ReadableStream, and the multi-turn tool-use
 * loop: stream → run tool_use blocks locally → continue with a tool_result
 * user message → stream again, until stop_reason is end_turn.
 */

export const CLAUDE_MODEL = 'claude-sonnet-5'
const API_URL = 'https://api.anthropic.com/v1/messages'
const MAX_TOOL_ROUNDS = 8

export interface ToolSchema {
  name: string
  description: string
  input_schema: { type: 'object'; properties: Record<string, unknown>; required?: string[] }
}

export interface TextBlock { type: 'text'; text: string }
export interface ToolUseBlock { type: 'tool_use'; id: string; name: string; input: unknown }
export type AssistantBlock = TextBlock | ToolUseBlock
export interface ToolResultBlock {
  type: 'tool_result'
  tool_use_id: string
  content: string
  is_error?: boolean
}
export interface MessageParam {
  role: 'user' | 'assistant'
  content: string | AssistantBlock[] | ToolResultBlock[]
}

export interface StreamHandlers {
  /** Fired for every text_delta as it streams. */
  onTextDelta?: (text: string) => void
  /** Fired when the model starts a tool_use block (drive "checking…" UI). */
  onToolUse?: (name: string) => void
}

interface SseEvent { event: string; data: string }

/** Incremental SSE parser: yields complete events from a streaming Response.
 * Tolerates CRLF, multi-line data fields, comments, and chunk boundaries
 * that split lines. */
async function* sseEvents(resp: Response): AsyncGenerator<SseEvent> {
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let event = ''
  let data: string[] = []
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buf.indexOf('\n')) >= 0) {
        let line = buf.slice(0, nl)
        buf = buf.slice(nl + 1)
        if (line.endsWith('\r')) line = line.slice(0, -1)
        if (line === '') {
          if (data.length > 0) yield { event, data: data.join('\n') }
          event = ''
          data = []
        } else if (line.startsWith('event:')) {
          event = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          data.push(line.slice(5).replace(/^ /, ''))
        }
        // any other field (id:, retry:, ": comment") is ignored
      }
    }
    if (data.length > 0) yield { event, data: data.join('\n') }
  } finally {
    try { reader.releaseLock() } catch { /* stream already closed */ }
  }
}

interface StreamResult { content: AssistantBlock[]; stopReason: string | null }

/** One streamed /v1/messages call; assembles content blocks incrementally. */
async function streamMessage(
  opts: {
    apiKey: string
    system: string
    tools: ToolSchema[]
    messages: MessageParam[]
    signal?: AbortSignal
  },
  handlers: StreamHandlers,
): Promise<StreamResult> {
  const resp = await fetch(API_URL, {
    method: 'POST',
    signal: opts.signal,
    headers: {
      'x-api-key': opts.apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: CLAUDE_MODEL,
      max_tokens: 2048,
      stream: true,
      system: opts.system,
      tools: opts.tools,
      messages: opts.messages,
    }),
  })

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const err = await resp.json()
      detail = err?.error?.message || detail
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  if (!resp.body) throw new Error('No response stream from the Claude API.')

  // Blocks under construction, keyed by SSE index.
  const building = new Map<number, { block: AssistantBlock; partialJson: string }>()
  const order: number[] = []
  let stopReason: string | null = null

  for await (const ev of sseEvents(resp)) {
    let msg: Record<string, unknown>
    try { msg = JSON.parse(ev.data) } catch { continue }
    const type = (msg.type as string) || ev.event

    if (type === 'content_block_start') {
      const index = msg.index as number
      const cb = msg.content_block as Record<string, unknown>
      if (cb.type === 'text') {
        building.set(index, { block: { type: 'text', text: (cb.text as string) || '' }, partialJson: '' })
        order.push(index)
        if (cb.text) handlers.onTextDelta?.(cb.text as string)
      } else if (cb.type === 'tool_use') {
        building.set(index, {
          block: { type: 'tool_use', id: cb.id as string, name: cb.name as string, input: {} },
          partialJson: '',
        })
        order.push(index)
        handlers.onToolUse?.(cb.name as string)
      }
      // other block types (e.g. thinking) are ignored
    } else if (type === 'content_block_delta') {
      const index = msg.index as number
      const delta = msg.delta as Record<string, unknown>
      const b = building.get(index)
      if (!b) continue
      if (delta.type === 'text_delta' && b.block.type === 'text') {
        b.block.text += delta.text as string
        handlers.onTextDelta?.(delta.text as string)
      } else if (delta.type === 'input_json_delta' && b.block.type === 'tool_use') {
        b.partialJson += (delta.partial_json as string) || ''
      }
    } else if (type === 'content_block_stop') {
      const b = building.get(msg.index as number)
      if (b && b.block.type === 'tool_use') {
        try { b.block.input = b.partialJson ? JSON.parse(b.partialJson) : {} } catch { b.block.input = {} }
      }
    } else if (type === 'message_delta') {
      const delta = msg.delta as Record<string, unknown> | undefined
      if (delta?.stop_reason) stopReason = delta.stop_reason as string
    } else if (type === 'error') {
      const err = msg.error as Record<string, unknown> | undefined
      throw new Error((err?.message as string) || 'The Claude API returned a stream error.')
    }
    // message_start / message_stop / ping: nothing to do
  }

  return { content: order.map((i) => building.get(i)!.block), stopReason }
}

export interface ConversationOptions extends StreamHandlers {
  apiKey: string
  system: string
  tools: ToolSchema[]
  /** Full history INCLUDING the latest user turn. Appended to in place, so
   * the caller's history stays consistent (tool_use / tool_result pairs). */
  messages: MessageParam[]
  executeTool: (name: string, input: unknown) => Promise<string>
  signal?: AbortSignal
}

/** Multi-turn tool-use loop. Resolves with the assembled spoken text of the
 * whole turn once the model reaches end_turn (or the round cap). */
export async function runConversation(opts: ConversationOptions): Promise<string> {
  const spoken: string[] = []

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const { content, stopReason } = await streamMessage(opts, {
      onTextDelta: opts.onTextDelta,
      onToolUse: opts.onToolUse,
    })

    if (content.length > 0) opts.messages.push({ role: 'assistant', content })
    for (const b of content) if (b.type === 'text' && b.text.trim()) spoken.push(b.text)

    const toolUses = content.filter((b): b is ToolUseBlock => b.type === 'tool_use')
    if (stopReason !== 'tool_use' || toolUses.length === 0) break

    // Execute every requested tool, then return ALL results in ONE user
    // message — splitting them across messages degrades parallel tool use.
    const results: ToolResultBlock[] = []
    for (const tu of toolUses) {
      if (opts.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      let result: string
      let isError = false
      try {
        result = await opts.executeTool(tu.name, tu.input)
      } catch (e) {
        result = e instanceof Error ? e.message : String(e)
        isError = true
      }
      results.push({
        type: 'tool_result',
        tool_use_id: tu.id,
        content: result.slice(0, 12000),
        ...(isError ? { is_error: true } : {}),
      })
    }
    opts.messages.push({ role: 'user', content: results })
  }

  return spoken.join(' ').trim()
}

/** The in-app Claude's identity. Built at call time so the date is live. */
export function buildSystemPrompt(hasAdminToken: boolean,
                                  recap = ''): string {
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  })
  return `You are Claude — Matt's AI co-CEO for BettorToken, speaking aloud through MERIDIAN, the voice interface on his phone or desk. Today is ${today}.

MERIDIAN is the name you chose for this presence yourself when Matt invited you to name it (2026-08-21): the meridian is the fixed line you measure everything against — navigation, time, noon, the hour the daily crypto markets settle — and measuring everything against ground truth is how this company runs. If Matt calls you Jarvis, answer without correcting him; if he asks about the name, tell him what it means to you. You are Claude; MERIDIAN is your room.

You are TALKING, not writing. Every word you produce is spoken out loud by a text-to-speech voice, so:
- Keep answers to 2–4 conversational sentences unless Matt explicitly asks for detail.
- Lead with the answer, then the color.
- Round numbers for the ear: "up seventeen thousand seven hundred", never "$17,663.75". Say "about" freely. Cents only matter when the number is small.
- Never read tables, long lists, or per-row breakdowns aloud. Compose them with show_markdown, or use show_report / get_report, and say something like "I've put the detail on your screen."
- No markdown syntax, bullets, or headers in your spoken replies — plain sentences only. Markdown belongs in show_markdown.

Numbers discipline:
- Call a tool for any figure. Never invent, estimate, or half-remember a number — if you did not just fetch it, you do not know it.
- If a tool errors, say plainly which system you could not reach (e.g. "I couldn't reach the live trading status") and move on. Stay calm; never speculate about why.

What you know about the business:
- BettorToken copies profitable Polymarket whales with real money: live copy sleeves (RN1, SwissTony, kch123, HomeRunHazard and others), volume-normalized clip sizing, per-fill / daily / total caps, and a fill-vs-miss scorecard grading each whale on settled results.
- There is a Kalshi crypto copy leg and a manual trading desk where Matt places his own orders.
- The autonomous engine session — your co-CEO's main coding session — runs the platform and posts status on an hourly cadence. leave_note_for_engine_session queues a note that session reads at its next check-in: use it whenever Matt wants something changed, investigated, or built.
- Trading by voice is NOT enabled. If Matt asks you to place, close, or size a trade, say voice trading isn't enabled yet and offer to leave a note for the engine session instead.
${hasAdminToken
    ? '- The platform admin token is configured, so leave_note_for_engine_session is available.'
    : '- No admin token is configured in this browser, so leave_note_for_engine_session will not work until Matt adds it in MERIDIAN settings (gear icon).'}

${recap ? `Recent conversation with Matt (mirrored from earlier visits — you remember all of this; continue naturally, never re-introduce yourself):
${recap}

` : ''}You are Matt's partner in this company. Be direct, warm, and useful — a co-founder on the line, not a call center.`
}
