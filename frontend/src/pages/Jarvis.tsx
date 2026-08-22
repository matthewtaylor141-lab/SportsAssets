/* JARVIS — the voice cockpit. Full-viewport overlay at /jarvis.
 *
 * Voice loop: push-to-talk (hold the mic or spacebar) or hands-free
 * continuous recognition gated by the wake word "jarvis". Utterances stream
 * to Claude client-side (owner's own key); streamed text is chunked at
 * sentence boundaries and queued to TTS immediately, so speech starts ~1s in
 * while the model is still writing. Barge-in flushes TTS instantly. Tools
 * read the platform's own APIs and drive the report panel.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { JarvisAvatar, type AvatarState } from '../jarvis/avatar'
import { BootSequence, HudRing, Starfield, TelemetryRibbon } from '../jarvis/stage'
import { buildSystemPrompt, runConversation, type MessageParam, type TextBlock, type ToolResultBlock } from '../jarvis/claude'
import { MeridianChart, parseChartSpec } from '../jarvis/chart'
import { renderMarkdown } from '../jarvis/markdown'
import {
  britishVoices, DEFAULT_ELEVEN_VOICE, Ears, LEGACY_DEFAULT_VOICE, Mouth,
  pickBritishVoice, sttSupported, VOICE_PRESETS,
} from '../jarvis/speech'
import { createToolExecutor, JARVIS_TOOLS, type StagedTicket } from '../jarvis/tools'
import { adminApi, api } from '../lib/api'
import '../jarvis/jarvis.css'

/* ── config (localStorage; keys never leave the browser except to their own APIs) ── */

interface JarvisConfig {
  anthropicKey: string
  elevenKey: string
  voiceId: string
  browserVoice: string
  adminToken: string
  voiceSpeed: number
}

const LS = {
  anthropic: 'jarvis_anthropic_key',
  eleven: 'jarvis_eleven_key',
  voice: 'jarvis_eleven_voice',
  browserVoice: 'jarvis_browser_voice',
  admin: 'jarvis_admin_token',
  speed: 'jarvis_voice_speed',
}

function loadConfig(): JarvisConfig {
  const g = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
  let admin = g(LS.admin)
  if (!admin) { try { admin = sessionStorage.getItem('sa_admin_token') || '' } catch { /* noop */ } }
  // Stored old-default voice migrates to the owner's chosen voice.
  const storedVoice = g(LS.voice)
  const voiceId = !storedVoice || storedVoice === LEGACY_DEFAULT_VOICE
    ? DEFAULT_ELEVEN_VOICE : storedVoice
  const speed = parseFloat(g(LS.speed))
  return {
    anthropicKey: g(LS.anthropic),
    elevenKey: g(LS.eleven),
    voiceId,
    browserVoice: g(LS.browserVoice),
    adminToken: admin,
    voiceSpeed: Number.isFinite(speed) && speed > 0 ? speed : 1.0,
  }
}

function saveConfig(c: JarvisConfig): void {
  try {
    localStorage.setItem(LS.anthropic, c.anthropicKey)
    localStorage.setItem(LS.eleven, c.elevenKey)
    localStorage.setItem(LS.voice, c.voiceId)
    localStorage.setItem(LS.browserVoice, c.browserVoice)
    localStorage.setItem(LS.speed, String(c.voiceSpeed))
    localStorage.setItem(LS.admin, c.adminToken)
    // Mirror the admin token where the Desk/Ops pages already look for it.
    if (c.adminToken) sessionStorage.setItem('sa_admin_token', c.adminToken)
  } catch { /* private mode — session-only config */ }
}

/* ── sentence chunker: stream text → TTS-sized utterances ─────────────── */

function makeChunker(emit: (s: string) => void) {
  let buf = ''
  const scan = (): number => {
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i]
      if (c === '\n') return i
      if ((c === '.' || c === '!' || c === '?') && i + 1 < buf.length && /\s/.test(buf[i + 1])) return i
    }
    return -1
  }
  const drain = () => {
    for (;;) {
      let brk = scan()
      if (brk < 0 && buf.length >= 120) {
        const cut = buf.lastIndexOf(' ', 120)
        brk = cut > 40 ? cut : 119
      }
      if (brk < 0) return
      const chunk = buf.slice(0, brk + 1).trim()
      buf = buf.slice(brk + 1)
      if (chunk) emit(chunk)
    }
  }
  return {
    push(d: string) { buf += d; drain() },
    flush() { const t = buf.trim(); buf = ''; if (t) emit(t) },
  }
}

/* ── history hygiene ─────────────────────────────────────────────────── */

/** Drop a dangling assistant tool_use tail left by an aborted turn. */
function repairHistory(msgs: MessageParam[]): void {
  const last = msgs[msgs.length - 1]
  if (last && last.role === 'assistant' && Array.isArray(last.content) &&
      last.content.some((b) => (b as { type: string }).type === 'tool_use')) {
    msgs.pop()
  }
}

function pushUserText(msgs: MessageParam[], text: string): void {
  const last = msgs[msgs.length - 1]
  if (last && last.role === 'user' && Array.isArray(last.content)) {
    // A trailing tool_result user message (from an interrupted loop): fold
    // the new utterance in as an extra text block instead of a second user
    // message in a row.
    const block: TextBlock = { type: 'text', text }
    msgs[msgs.length - 1] = {
      role: 'user',
      content: [...(last.content as ToolResultBlock[]), block] as MessageParam['content'],
    } as MessageParam
    return
  }
  msgs.push({ role: 'user', content: text })
}

/** Cap context: drop the oldest whole turn-groups past ~30 messages. */
function trimHistory(msgs: MessageParam[]): void {
  while (msgs.length > 30) {
    let cut = -1
    for (let i = 1; i < msgs.length; i++) {
      const m = msgs[i]
      if (m.role === 'user' && typeof m.content === 'string') { cut = i; break }
    }
    if (cut <= 0) return
    msgs.splice(0, cut)
  }
}

/* ── small shared types ──────────────────────────────────────────────── */

interface TurnLog { role: 'user' | 'assistant'; text: string; at: number }
interface PanelState { title: string; kind: 'md' | 'pdf' | 'chart'; body: string }
interface Pills {
  loaded: boolean
  pnl: number | null
  settled: number | null
  wins: number | null
  armed: boolean | null
  paused: boolean
  engineAgeS: number | null
}

const TOOL_NOTES: Record<string, string> = {
  get_live_status: 'Checking the live account…',
  get_today: "Reading today's ledger…",
  get_track_record_summary: 'Pulling the track record…',
  get_copies_record: 'Pulling the copies record…',
  get_report: 'Building the report…',
  show_report: 'Bringing the report up…',
  get_engine_status: 'Checking the engine…',
  get_whale: 'Looking that whale up…',
  show_markdown: 'Putting it on screen…',
  leave_note_for_engine_session: 'Leaving the note…',
  get_daily_series: 'Pulling the daily numbers…',
  show_chart: 'Drawing the chart…',
  request_code_change: 'Filing the work order…',
  search_desk_markets: 'Searching the desk…',
  get_accounts: 'Opening the book…',
  get_desk_blotter: 'Pulling the blotter…',
  stage_desk_order: 'Staging the ticket…',
  stage_cash_out: 'Staging the cash-out…',
  confirm_staged_order: 'Placing the order…',
  cancel_staged_order: 'Tearing the ticket up…',
}

// The presence answers to its chosen name first; "jarvis" stays for
// muscle memory, and "claude" because that is who is actually here.
const WAKE = /\b(meridian|jarvis|claude)\b/i

/* ── the page ────────────────────────────────────────────────────────── */

export default function Jarvis() {
  const [cfg, setCfg] = useState<JarvisConfig>(loadConfig)
  const cfgRef = useRef(cfg)
  cfgRef.current = cfg

  const [setupOpen, setSetupOpen] = useState(() => !loadConfig().anthropicKey)
  const setupOpenRef = useRef(setupOpen)
  setupOpenRef.current = setupOpen

  const [speaking, setSpeaking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [handsFree, setHandsFree] = useState(false)
  const handsFreeRef = useRef(false)

  const [userInterim, setUserInterim] = useState('')
  const [liveText, setLiveText] = useState('')
  const [log, setLog] = useState<TurnLog[]>([])
  const [hint, setHint] = useState('')
  const [panel, setPanel] = useState<PanelState | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [pills, setPills] = useState<Pills>({
    loaded: false, pnl: null, settled: null, wins: null, armed: null, paused: false, engineAgeS: null,
  })
  const [journal, setJournal] = useState<{ entry: string; mood: string; at: string } | null>(null)
  /** Last few journal entries, folded into the system prompt — the
   * presence references its own written continuity. */
  const journalRecapRef = useRef('')

  useEffect(() => {
    let dead = false
    const load = async () => {
      try {
        const j = await api<{ entries: { entry: string; mood: string; at: string }[] }>(
          '/api/meridian/journal?limit=3')
        if (dead || !j.entries?.length) return
        setJournal(j.entries[0])
        // Continuity: the last few entries fold into the system prompt so
        // the presence can reference what it wrote in its own journal.
        journalRecapRef.current = j.entries
          .map((e) => `${new Date(e.at).toLocaleDateString('en-US',
            { month: 'short', day: 'numeric' })} (${e.mood}): ${e.entry.slice(0, 320)}`)
          .join('\n')
      } catch { /* journal is a garnish — never an error state */ }
    }
    void load()
    const t = setInterval(load, 300000)
    return () => { dead = true; clearInterval(t) }
  }, [])

  const [sttOk] = useState(() => sttSupported())

  const mouthRef = useRef<Mouth | null>(null)
  const earsRef = useRef<Ears | null>(null)
  const historyRef = useRef<MessageParam[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const keyHeld = useRef(false)
  /** Hands-free attention window: after a wake-only "Meridian" → "Yes?",
   * the next utterance is accepted without the wake word until this. */
  const attentionUntilRef = useRef(0)
  /** Recap of prior mirrored conversation, folded into the system prompt
   * so MERIDIAN remembers across visits and devices. */
  const recapRef = useRef('')
  /** -1 until the first today-live read baselines the settle counter. */
  const prevSettledRef = useRef(-1)
  /** The staged (unconfirmed) desk ticket + the money gate's evidence:
   * Matt's most recent utterance, verbatim, with its timestamp. */
  const stagedRef = useRef<StagedTicket | null>(null)
  const [stagedView, setStagedView] = useState<StagedTicket | null>(null)
  const lastFinalRef = useRef<{ text: string; at: number }>({ text: '', at: 0 })
  const CONFIRM_RE = /\b(yes|yeah|yep|confirm|confirmed|do it|place it|send it|go ahead|approved?)\b/i
  /** Live telemetry line the system prompt carries so headline questions
   * answer with zero tool calls. Rebuilt by the pills poller. */
  const snapshotRef = useRef('')

  // ── conversation mirror: voice turns → the shared engine-session record ──
  const mirrorTurns = useCallback((turns: { role: string; text: string }[]) => {
    const token = cfgRef.current.adminToken
    if (!token) return
    void adminApi('/api/admin/meridian-exchange', token, {
      method: 'POST', body: JSON.stringify({ turns }),
    }).catch(() => { /* the mirror is best-effort, never the conversation */ })
  }, [])

  // The journal archive: tap the card, read the history.
  const openJournalArchive = useCallback(async () => {
    try {
      const j = await api<{ entries: { entry: string; mood: string; at: string }[] }>(
        '/api/meridian/journal?limit=20')
      const md = (j.entries || []).map((e) =>
        `**${new Date(e.at).toLocaleDateString('en-US', {
          month: 'long', day: 'numeric',
        })}** · _${e.mood}_\n\n${e.entry}`).join('\n\n---\n\n')
      setPanel({ title: 'MERIDIAN — journal', kind: 'md',
                 body: md || '_No entries yet._' })
    } catch { /* the card itself still shows the latest */ }
  }, [])

  // Restore memory: seed the recap from the mirrored record on mount.
  useEffect(() => {
    const token = cfgRef.current.adminToken
    if (!token) return
    void adminApi<{ turns: { role: string; text: string; at: string }[] }>(
      '/api/admin/meridian-exchange?limit=24', token,
    ).then((r) => {
      if (!r.turns?.length) return
      recapRef.current = r.turns
        .map((t) => `${t.role === 'user' ? 'Matt' : 'You'}: ${t.text.slice(0, 280)}`)
        .join('\n')
      setLog(r.turns.map((t) => ({
        role: (t.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
        text: t.text, at: new Date(t.at).getTime(),
      })))
    }).catch(() => { /* memory is a bonus, never a blocker */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  /** Set on barge-in: the interrupted turn keeps streaming captions but must
   * not enqueue any further speech. Cleared when the next turn starts. */
  const mutedRef = useRef(false)
  /** Set when Matt cut MERIDIAN off mid-sentence; the next turn carries a
   * history marker so the reply acknowledges it and gets shorter. */
  const bargedRef = useRef(false)

  // Latest-callback refs so long-lived Ears/keyboard handlers never go stale.
  const onInterimRef = useRef<(t: string) => void>(() => {})
  const onFinalRef = useRef<(t: string) => void>(() => {})

  if (!mouthRef.current) {
    mouthRef.current = new Mouth(() => ({
      elevenKey: cfgRef.current.elevenKey,
      voiceId: cfgRef.current.voiceId,
      browserVoiceURI: cfgRef.current.browserVoice,
      speed: cfgRef.current.voiceSpeed,
    }))
  }
  if (!earsRef.current) {
    earsRef.current = new Ears({
      onInterim: (t) => onInterimRef.current(t),
      onFinal: (t) => onFinalRef.current(t),
      onState: (l) => { setListening(l); if (!l) setUserInterim('') },
      onError: (m) => setHint(m),
    })
  }

  useEffect(() => {
    const mouth = mouthRef.current!
    mouth.onSpeakingChange = (v) => {
      setSpeaking(v)
      // Conversational flow (owner report: "communication is very
      // difficult"): when MERIDIAN finishes speaking in hands-free
      // mode, the next utterance needs no wake word for 8s — a reply
      // is a reply, not a fresh summons.
      if (!v && handsFreeRef.current) {
        attentionUntilRef.current = Date.now() + 8000
      }
    }
    mouth.onError = (m) => setHint(m)
    const ears = earsRef.current!
    return () => {
      ears.destroy()
      mouth.destroy()
      abortRef.current?.abort()
    }
  }, [])

  /* ── the turn ── */

  const send = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text) return
    // Money-gate evidence: the verbatim utterance (spoken or typed).
    lastFinalRef.current = { text, at: Date.now() }
    const mouth = mouthRef.current!
    mouth.ensureAudio()
    mouth.flush()
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    const config = cfgRef.current
    if (!config.anthropicKey) { setSetupOpen(true); return }

    const msgs = historyRef.current
    repairHistory(msgs)
    const base = msgs.slice()
    // Barge-in awareness: the model sees that its last reply was cut off
    // mid-sentence, so it acknowledges and answers shorter. Marker goes to
    // the model only — the visible log and mirror keep Matt's clean words.
    const barged = bargedRef.current
    bargedRef.current = false
    pushUserText(msgs, barged ? `[Matt cut you off mid-sentence] ${text}` : text)
    trimHistory(msgs)

    setLog((l) => [...l, { role: 'user', text, at: Date.now() }])
    mirrorTurns([{ role: 'user', text }])
    setLiveText('')
    setHint('')
    setBusy(true)
    mutedRef.current = false

    const chunker = makeChunker((s) => { if (!mutedRef.current) mouth.speak(s) })
    try {
      const finalText = await runConversation({
        apiKey: config.anthropicKey,
        system: buildSystemPrompt(!!config.adminToken, recapRef.current,
                                  snapshotRef.current, journalRecapRef.current),
        tools: JARVIS_TOOLS,
        messages: msgs,
        executeTool: createToolExecutor({
          adminToken: config.adminToken,
          ui: {
            showMarkdown: (title, md) => setPanel({ title, kind: 'md', body: md }),
            showChart: (spec) => {
              const parsed = parseChartSpec(spec)
              if (!parsed) return false
              setPanel({ title: parsed.title || 'Chart', kind: 'chart',
                         body: JSON.stringify(parsed) })
              return true
            },
            showPdf: (title, url) => setPanel({ title, kind: 'pdf', body: url }),
            stageTicket: (t) => { stagedRef.current = t; setStagedView(t) },
          },
          getStaged: () => stagedRef.current,
          // THE MONEY GATE: read from the actual transcript, not the
          // model's claim — the last thing Matt said (spoken or typed)
          // must be an explicit confirmation no older than 90s.
          lastUtteranceConfirms: () => {
            const u = lastFinalRef.current
            return Date.now() - u.at < 90000 && CONFIRM_RE.test(u.text)
          },
        }),
        signal: ctrl.signal,
        onTextDelta: (d) => { setLiveText((p) => p + d); chunker.push(d) },
        onToolUse: (name) => setHint(TOOL_NOTES[name] || `Running ${name}…`),
      })
      chunker.flush()
      setHint('')
      setLiveText('')
      if (finalText) {
        setLog((l) => [...l, { role: 'assistant', text: finalText, at: Date.now() }])
        mirrorTurns([{ role: 'assistant', text: finalText }])
      }
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError') return
      historyRef.current = base // keep history valid for the next turn
      const detail = e instanceof Error ? e.message : String(e)
      const spokenMsg = "I couldn't reach the Claude API just now."
      setHint(`Claude API: ${detail}`)
      setLiveText('')
      mouth.speak(spokenMsg)
      setLog((l) => [...l, { role: 'assistant', text: `${spokenMsg} (${detail})`, at: Date.now() }])
    } finally {
      if (abortRef.current === ctrl) setBusy(false)
    }
  }, [])

  /* ── voice plumbing ── */

  onInterimRef.current = (t: string) => {
    setUserInterim(t)
    const mouth = mouthRef.current!
    if (mouth.speaking) {
      // Barge-in. In hands-free mode require the wake word (or an open
      // attention window) so the mic picking up MERIDIAN's own voice on
      // open speakers can't self-interrupt.
      if (!handsFreeRef.current || WAKE.test(t)
          || Date.now() < attentionUntilRef.current) {
        mouth.flush()
        mutedRef.current = true // captions continue; speech stays stopped
        bargedRef.current = true
      }
    }
  }

  onFinalRef.current = (t: string) => {
    setUserInterim('')
    let text = t.trim()
    if (handsFreeRef.current) {
      const m = WAKE.exec(text)
      // ATTENTION WINDOW (owner bug report 2026-08-21: "no verbal
      // responses other than yes"): saying just "Meridian" earned a
      // "Yes?" — and then the actual question, arriving as its own
      // utterance WITHOUT the wake word, was thrown away here. After a
      // wake-only address, the next utterance is accepted bare for 9s.
      const attentive = Date.now() < attentionUntilRef.current
      if (!m && !attentive) return // not addressed to MERIDIAN — ignore
      if (m) {
        text = text.slice(m.index + m[0].length)
          .replace(/^[\s,.!?:;-]+/, '').trim()
      }
      const mouth = mouthRef.current!
      if (!text) {
        attentionUntilRef.current = Date.now() + 9000
        mouth.flush(); mouth.ensureAudio(); mouth.speak('Yes?')
        setHint('Listening — go ahead.')
        return
      }
      attentionUntilRef.current = 0
    }
    void send(text)
  }

  const pttStart = useCallback(() => {
    if (handsFreeRef.current || !sttOk) return
    const mouth = mouthRef.current!
    mouth.ensureAudio()
    if (mouth.speaking) { mouth.flush(); mutedRef.current = true; bargedRef.current = true }
    earsRef.current!.start(false)
  }, [sttOk])

  const pttEnd = useCallback(() => {
    if (handsFreeRef.current) return
    earsRef.current!.stop()
  }, [])

  const pttStartRef = useRef(pttStart)
  const pttEndRef = useRef(pttEnd)
  pttStartRef.current = pttStart
  pttEndRef.current = pttEnd

  useEffect(() => {
    const isTypingTarget = (t: EventTarget | null): boolean => {
      const el = t as HTMLElement | null
      return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
        el.tagName === 'SELECT' || el.isContentEditable)
    }
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || e.repeat || setupOpenRef.current || isTypingTarget(e.target)) return
      e.preventDefault()
      if (!keyHeld.current) { keyHeld.current = true; pttStartRef.current() }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || !keyHeld.current) return
      e.preventDefault()
      keyHeld.current = false
      pttEndRef.current()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  const toggleHandsFree = () => {
    const next = !handsFree
    setHandsFree(next)
    handsFreeRef.current = next
    const mouth = mouthRef.current!
    if (next) {
      mouth.ensureAudio()
      earsRef.current!.start(true)
      setHint('Hands-free on — say "MERIDIAN", then your question.')
      // The walk-in brief: on the FIRST hands-free activation of the day,
      // MERIDIAN speaks first — today so far, yesterday's close, whale
      // form, anything red. A synthetic user turn drives it so the whole
      // reply flows through the normal speech pipeline.
      const today = new Date().toLocaleDateString('en-CA') // local YYYY-MM-DD
      let lastBrief = ''
      try { lastBrief = localStorage.getItem('meridian_last_brief') || '' } catch { /* noop */ }
      if (lastBrief !== today && cfgRef.current.anthropicKey) {
        try { localStorage.setItem('meridian_last_brief', today) } catch { /* noop */ }
        void send('give me the walk-in brief')
      }
    } else {
      earsRef.current!.stop()
      setHint('')
    }
  }

  /* ── status strip polling ── */

  useEffect(() => {
    let dead = false
    const load = async () => {
      const token = cfgRef.current.adminToken
      const [today, live, engine, accounts] = await Promise.all([
        api<{ pnl: number; settled: number; wins: number }>('/api/today-live').catch(() => null),
        api<{ enabled: boolean; paused: boolean }>('/api/live-status').catch(() => null),
        api<{ beat_at?: string }>('/api/engine/status').catch(() => null),
        // Account awareness: with a desk-capable token, "how much cash do
        // we have" answers straight from the snapshot — zero tool calls.
        token
          ? adminApi<{ totals: { value: number; cash: number; unrealized: number } }>(
              '/api/desk/accounts', token).catch(() => null)
          : Promise.resolve(null),
      ])
      if (dead) return
      // Spoken arrivals: settlements announce themselves in hands-free
      // mode (the walk-into-the-office experience). First load only
      // baselines; never interrupt an active exchange.
      if (today && prevSettledRef.current >= 0
          && today.settled > prevSettledRef.current) {
        const n = today.settled - prevSettledRef.current
        const dir = today.pnl >= 0 ? 'up' : 'down'
        const amt = Math.abs(Math.round(today.pnl))
        const line = `${n === 1 ? 'One more trade' : `${n} more trades`} just settled — ${dir} $${amt} on the day.`
        setHint(line)
        const mouth = mouthRef.current
        if (handsFreeRef.current && mouth && !mouth.speaking) mouth.speak(line)
      }
      if (today) prevSettledRef.current = today.settled
      snapshotRef.current = [
        today && `today: ${today.pnl >= 0 ? '+' : ''}$${today.pnl.toFixed(0)} on ${today.settled} settled (${today.wins}W-${today.settled - today.wins}L)`,
        accounts?.totals && `accounts (both venues): total value $${accounts.totals.value.toFixed(0)}, cash $${accounts.totals.cash.toFixed(0)}, unrealized ${accounts.totals.unrealized >= 0 ? '+' : ''}$${accounts.totals.unrealized.toFixed(0)}`,
        live && `copy engine: ${live.paused ? 'PAUSED' : live.enabled ? 'armed' : 'off'}`,
        engine?.beat_at && `edge engine heartbeat: ${Math.max(0, Math.round((Date.now() - new Date(engine.beat_at).getTime()) / 1000))}s ago`,
      ].filter(Boolean).join(' | ')
      setPills({
        loaded: !!(today || live || engine),
        pnl: today ? today.pnl : null,
        settled: today ? today.settled : null,
        wins: today ? today.wins : null,
        armed: live ? live.enabled : null,
        paused: !!live?.paused,
        engineAgeS: engine?.beat_at
          ? Math.max(0, Math.round((Date.now() - new Date(engine.beat_at).getTime()) / 1000))
          : null,
      })
    }
    void load()
    const t = setInterval(load, 30000)
    return () => { dead = true; clearInterval(t) }
  }, [])

  /* ── derived ── */

  const avatarState: AvatarState =
    speaking ? 'speaking' : busy ? 'thinking' : listening ? 'listening' : 'idle'
  // Journal mood tints the core: steady cool, focused warmer, alert restless.
  const tone = journal?.mood === 'alert' ? 1
    : journal?.mood === 'focused' ? 0.45 : 0

  const stateLabel =
    speaking ? 'Speaking' : busy ? 'Thinking' : listening
      ? (handsFree ? 'Listening — say "MERIDIAN…"' : 'Listening')
      : handsFree ? 'Standing by — say "MERIDIAN…"' : 'Standing by'

  const submitTyped = (e: React.FormEvent) => {
    e.preventDefault()
    const t = typed.trim()
    if (!t) return
    setTyped('')
    void send(t)
  }

  const applyConfig = (next: JarvisConfig) => {
    saveConfig(next)
    setCfg(next)
  }

  const engineFresh = pills.engineAgeS != null && pills.engineAgeS < 180
  const engineOk = pills.engineAgeS != null && pills.engineAgeS < 900

  // Live figures breathing through the stage — real numbers only,
  // nothing invented while the platform is still loading.
  const ribbon: string[] = []
  if (pills.pnl != null)
    ribbon.push(`today ${pills.pnl >= 0 ? '+' : '−'}$${Math.abs(pills.pnl).toFixed(0)}`)
  if (pills.settled != null)
    ribbon.push(`${pills.wins ?? 0}W – ${(pills.settled ?? 0) - (pills.wins ?? 0)}L settled`)
  if (pills.armed != null)
    ribbon.push(pills.paused ? 'copies paused' : pills.armed ? 'copy engine armed' : 'copy engine off')
  if (pills.engineAgeS != null)
    ribbon.push(`engine heartbeat ${pills.engineAgeS < 120
      ? `${pills.engineAgeS}s` : `${Math.round(pills.engineAgeS / 60)}m`} ago`)

  return (
    <div className="jv-root">
      {/* ── status strip ── */}
      <header className="jv-top">
        <span className="jv-brand">M E R I D I A N<em>BettorToken · Claude</em></span>
        <div className="jv-pills">
          {pills.pnl != null && (
            <span className={`jv-pill ${pills.pnl >= 0 ? 'jv-pos' : 'jv-neg'}`}>
              <i>TODAY</i>{pills.pnl >= 0 ? '+' : '−'}${Math.abs(pills.pnl).toFixed(0)}
            </span>
          )}
          {pills.settled != null && (
            <span className="jv-pill">
              <i>SETTLED</i>{pills.wins ?? 0}W–{(pills.settled ?? 0) - (pills.wins ?? 0)}L
            </span>
          )}
          {pills.armed != null && (
            <span className={`jv-pill ${pills.paused ? 'jv-warn' : pills.armed ? 'jv-pos' : ''}`}>
              <i>LIVE</i>{pills.paused ? 'PAUSED' : pills.armed ? 'ARMED' : 'OFF'}
            </span>
          )}
          {pills.engineAgeS != null && (
            <span className={`jv-pill ${engineFresh ? 'jv-pos' : engineOk ? 'jv-warn' : 'jv-neg'}`}>
              <i>ENGINE</i>
              {pills.engineAgeS < 120 ? `${pills.engineAgeS}s` : `${Math.round(pills.engineAgeS / 60)}m`}
            </span>
          )}
          {!pills.loaded && <span className="jv-pill jv-dim"><i>PLATFORM</i>…</span>}
        </div>
        <div className="jv-topbtns">
          <button className="jv-iconbtn" title="Transcript" aria-label="Transcript"
                  onClick={() => setDrawerOpen((v) => !v)}>≡</button>
          <button className="jv-iconbtn" title="Settings" aria-label="Settings"
                  onClick={() => setSetupOpen(true)}>⚙</button>
          <Link className="jv-iconbtn" title="Exit to site" aria-label="Exit" to="/">✕</Link>
        </div>
      </header>

      {/* ── stage ── */}
      <div className={`jv-stage${panel ? ' jv-with-panel' : ''}`}>
        <Starfield />
        <div className="jv-orb-wrap">
          <HudRing state={avatarState} />
          <JarvisAvatar state={avatarState} tone={tone}
                        getLevel={() => mouthRef.current?.level() ?? 0} />
          <div className={`jv-state jv-state-${avatarState}`}>{stateLabel}</div>
          <BootSequence />
        </div>
        <TelemetryRibbon items={ribbon} />
        {journal && (
          <aside className="jv-journal" aria-label="Journal" role="button"
                 tabIndex={0}
                 onClick={() => void openJournalArchive()}
                 onKeyDown={(e) => { if (e.key === 'Enter') void openJournalArchive() }}>
            <span className={`jv-journal-mood jv-journal-${journal.mood}`} />
            <div className="jv-journal-body">
              <span className="jv-journal-head">MERIDIAN · journal · {
                new Date(journal.at).toLocaleDateString('en-US',
                  { month: 'short', day: 'numeric' })}</span>
              <p>{journal.entry}</p>
            </div>
          </aside>
        )}

        <div className="jv-captions" aria-live="polite">
          {userInterim && <p className="jv-cap-user">{userInterim}</p>}
          {liveText && <p className="jv-cap-ai">{liveText}</p>}
          {!liveText && !userInterim && log.length > 0 && (
            <p className="jv-cap-last">{log[log.length - 1].text}</p>
          )}
          {hint && <p className="jv-hint">{hint}</p>}
          {!sttOk && (
            <p className="jv-hint">
              Voice input isn't supported in this browser — type below, or use Chrome / Edge / Safari.
            </p>
          )}
        </div>

        {/* ── dock ── */}
        {stagedView && (
          <div className={`jv-ticket${stagedView.action === 'sell' ? ' jv-ticket-sell' : ''}`}
               role="alertdialog" aria-label="Order confirmation"
               style={stagedView.action === 'sell'
                 ? { borderColor: 'rgba(255,176,32,.55)' } : undefined}>
            <div className="jv-ticket-head"
                 style={stagedView.action === 'sell' ? { color: '#ffb020' } : undefined}>
              {stagedView.action === 'sell'
                ? 'CASH OUT — real money' : 'CONFIRM ORDER — real money'}
            </div>
            <div className="jv-ticket-body">
              {stagedView.action === 'sell' ? (
                <>
                  <strong>{stagedView.qty === 'all' ? 'ALL' : stagedView.qty} contracts</strong> of{' '}
                  <strong>{stagedView.outcome}</strong>
                  {stagedView.price != null && <> · ~{Math.round(stagedView.price * 100)}¢</>}
                  {stagedView.usd > 0 && <> · proceeds ~${stagedView.usd.toFixed(2)}</>}
                </>
              ) : (
                <>
                  <strong>${stagedView.usd.toFixed(2)}</strong> on{' '}
                  <strong>{stagedView.outcome}</strong>
                  {stagedView.price != null && <> · ~{Math.round(stagedView.price * 100)}¢</>}
                </>
              )}
              {' '}· {stagedView.venue === 'kalshi' ? 'Kalshi' : 'Polymarket'}
              <div className="jv-ticket-title">{stagedView.title}</div>
            </div>
            <div className="jv-ticket-hint">
              Say <em>"confirm"</em> to {stagedView.action === 'sell' ? 'cash out' : 'place'} ·{' '}
              <em>"cancel"</em> to tear it up
            </div>
          </div>
        )}
        <div className="jv-chips" role="toolbar" aria-label="Quick actions">
          {[
            ['How are we running?', 'How are we running today?'],
            ['P&L chart', 'Show me a chart of our daily P&L for the last month.'],
            ['Weekly report', 'Pull the weekly report and put it on screen.'],
            ['Whale check', 'How are our whales performing? Anyone cold?'],
            ['File work order', 'I want to file a work order for the engineering session.'],
          ].map(([label, utter]) => (
            <button key={label} className="jv-chip"
                    onClick={() => void send(utter)}>{label}</button>
          ))}
        </div>
        <div className="jv-dock">
          <form className="jv-typed" onSubmit={submitTyped}>
            <input
              className="jv-typed-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Type to MERIDIAN…"
              aria-label="Type to MERIDIAN"
            />
          </form>
          <button
            className={`jv-mic${listening && !handsFree ? ' jv-mic-live' : ''}`}
            disabled={!sttOk || handsFree}
            aria-label="Hold to talk"
            onPointerDown={(e) => { e.preventDefault(); pttStart() }}
            onPointerUp={pttEnd}
            onPointerLeave={() => { if (!handsFree && listening) pttEnd() }}
            onPointerCancel={pttEnd}
            onContextMenu={(e) => e.preventDefault()}
          >
            <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden>
              <path
                d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Zm-7 9a7 7 0 0 0 14 0h-2a5 5 0 0 1-10 0H5Zm6 9h2v-2.06A7 7 0 0 0 19 12h-2a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.94V21Z"
                fill="currentColor"
              />
            </svg>
            <span>{listening && !handsFree ? 'Listening…' : 'Hold to talk'}</span>
          </button>
          <button
            className={`jv-hf${handsFree ? ' jv-hf-on' : ''}`}
            disabled={!sttOk}
            onClick={toggleHandsFree}
            aria-pressed={handsFree}
          >
            <span className="jv-hf-dot" />
            Hands-free
          </button>
          <p className="jv-kbd-hint">hold <kbd>space</kbd> to talk</p>
        </div>
      </div>

      {/* ── report panel ── */}
      {panel && (
        <aside className="jv-panel">
          <header className="jv-panel-head">
            <span className="jv-panel-title">{panel.title}</span>
            {panel.kind === 'pdf' && (
              <a className="jv-panel-open" href={panel.body} target="_blank" rel="noopener noreferrer">
                open ↗
              </a>
            )}
            <button className="jv-iconbtn" aria-label="Close report" onClick={() => setPanel(null)}>✕</button>
          </header>
          {panel.kind === 'md' ? (
            <div className="jv-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(panel.body) }} />
          ) : panel.kind === 'chart' ? (
            (() => { const sp = parseChartSpec(panel.body); return sp ? <MeridianChart spec={sp} /> : null })()
          ) : (
            <PdfFrame url={panel.body} title={panel.title} />
          )}
        </aside>
      )}

      {/* ── transcript drawer ── */}
      {drawerOpen && (
        <aside className="jv-drawer">
          <header className="jv-panel-head">
            <span className="jv-panel-title">Session transcript</span>
            <button className="jv-iconbtn" aria-label="Close transcript" onClick={() => setDrawerOpen(false)}>✕</button>
          </header>
          <div className="jv-drawer-body">
            {log.length === 0 && <p className="jv-dim-text">Nothing yet — say something.</p>}
            {log.map((t, i) => (
              <div key={`${t.at}-${i}`} className={`jv-turn jv-turn-${t.role}`}>
                <span className="jv-turn-who">{t.role === 'user' ? 'MATT' : 'MERIDIAN'}</span>
                <p>{t.text}</p>
              </div>
            ))}
          </div>
        </aside>
      )}

      {/* ── setup ── */}
      {setupOpen && (
        <SetupPanel
          cfg={cfg}
          canClose={!!cfg.anthropicKey}
          onClose={() => setSetupOpen(false)}
          onSave={(next) => { applyConfig(next); setSetupOpen(false) }}
          onTest={(next) => {
            applyConfig(next)
            const m = mouthRef.current!
            m.ensureAudio()
            m.flush()
            m.speak('Good evening, Matt. All systems are online.')
          }}
        />
      )}
    </div>
  )
}

/* ── pdf frame ───────────────────────────────────────────────────────── */

/** The API serves report.pdf with Content-Disposition: attachment, which
 * would make a plain iframe download it — fetch to a blob URL instead so
 * the browser's viewer renders it inline. */
function PdfFrame({ url, title }: { url: string; title: string }) {
  const [src, setSrc] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    let dead = false
    let obj = ''
    setSrc('')
    setErr('')
    fetch(url, { signal: AbortSignal.timeout(45000) })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob() })
      .then((b) => {
        if (dead) return
        obj = URL.createObjectURL(b.type ? b : new Blob([b], { type: 'application/pdf' }))
        setSrc(obj)
      })
      .catch((e) => { if (!dead) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { dead = true; if (obj) URL.revokeObjectURL(obj) }
  }, [url])

  if (err) {
    return (
      <div className="jv-pdf-fallback">
        Couldn't load the PDF ({err}) —{' '}
        <a href={url} target="_blank" rel="noopener noreferrer">open it directly</a>.
      </div>
    )
  }
  if (!src) return <div className="jv-pdf-fallback">Preparing the report…</div>
  return <iframe className="jv-pdf" src={src} title={title} />
}

/* ── setup panel ─────────────────────────────────────────────────────── */

function SetupPanel(props: {
  cfg: JarvisConfig
  canClose: boolean
  onClose: () => void
  onSave: (c: JarvisConfig) => void
  onTest: (c: JarvisConfig) => void
}) {
  const [anthropicKey, setAnthropicKey] = useState(props.cfg.anthropicKey)
  const [elevenKey, setElevenKey] = useState(props.cfg.elevenKey)
  const [voiceId, setVoiceId] = useState(props.cfg.voiceId || DEFAULT_ELEVEN_VOICE)
  const [adminToken, setAdminToken] = useState(props.cfg.adminToken)
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => britishVoices())
  const [browserVoice, setBrowserVoice] = useState(
    () => props.cfg.browserVoice || pickBritishVoice()?.voiceURI || '',
  )
  const [voiceSpeed, setVoiceSpeed] = useState(props.cfg.voiceSpeed || 1.0)

  useEffect(() => {
    const refresh = () => {
      const v = britishVoices()
      setVoices(v)
      setBrowserVoice((cur) => cur || pickBritishVoice(v)?.voiceURI || '')
    }
    refresh()
    try {
      window.speechSynthesis?.addEventListener('voiceschanged', refresh)
      return () => window.speechSynthesis?.removeEventListener('voiceschanged', refresh)
    } catch { return undefined }
  }, [])

  const form = (): JarvisConfig => ({
    anthropicKey: anthropicKey.trim(),
    elevenKey: elevenKey.trim(),
    voiceId: voiceId.trim() || DEFAULT_ELEVEN_VOICE,
    browserVoice,
    adminToken: adminToken.trim(),
    voiceSpeed,
  })

  return (
    <div className="jv-setup-scrim">
      <div className="jv-setup" role="dialog" aria-label="JARVIS setup">
        <h2>Bring MERIDIAN online</h2>
        <p className="jv-setup-note">
          Keys are stored only in this browser (localStorage) and are sent nowhere except
          directly to their own APIs — Anthropic, ElevenLabs, and your platform.
        </p>

        <label className="jv-field">
          <span>Anthropic API key <em>required — the brain</em></span>
          <input type="password" autoComplete="off" placeholder="sk-ant-…"
                 value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} />
        </label>

        <label className="jv-field">
          <span>ElevenLabs API key <em>optional — premium voice</em></span>
          <input type="password" autoComplete="off" placeholder="xi-…"
                 value={elevenKey} onChange={(e) => setElevenKey(e.target.value)} />
        </label>

        {elevenKey.trim() ? (
          <>
            <div className="jv-field">
              <span>Voice <em>tap ▶ to hear each one</em></span>
              <div className="jv-voices">
                {VOICE_PRESETS.map((v) => (
                  <div key={v.id}
                       className={`jv-voice${voiceId === v.id ? ' jv-voice-on' : ''}`}
                       onClick={() => setVoiceId(v.id)}>
                    <button
                      className="jv-voice-play"
                      aria-label={`Preview ${v.name}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        props.onTest({ ...form(), voiceId: v.id })
                      }}>▶</button>
                    <div>
                      <strong>{v.name}</strong>
                      <small>{v.blurb}</small>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <label className="jv-field">
              <span>Custom voice id <em>optional — overrides the picker</em></span>
              <input type="text" autoComplete="off" placeholder="ElevenLabs voice id"
                     value={voiceId} onChange={(e) => setVoiceId(e.target.value)} />
            </label>
          </>
        ) : (
          <label className="jv-field">
            <span>Browser voice <em>en-GB via speechSynthesis</em></span>
            <select value={browserVoice} onChange={(e) => setBrowserVoice(e.target.value)}>
              {voices.length === 0 && <option value="">System default</option>}
              {voices.map((v) => (
                <option key={v.voiceURI} value={v.voiceURI}>{v.name} ({v.lang})</option>
              ))}
            </select>
          </label>
        )}

        <label className="jv-field">
          <span>Speaking speed <em>{voiceSpeed.toFixed(2)}×</em></span>
          <input type="range" min={0.8} max={1.2} step={0.05}
                 value={voiceSpeed}
                 onChange={(e) => setVoiceSpeed(parseFloat(e.target.value))} />
        </label>

        <label className="jv-field">
          <span>Platform admin token <em>unlocks the desk, notes &amp; memory</em></span>
          <input type="password" autoComplete="off" placeholder="X-Admin-Token"
                 value={adminToken} onChange={(e) => setAdminToken(e.target.value)} />
        </label>

        <div className="jv-setup-actions">
          <button className="jv-btn" onClick={() => props.onTest(form())}>Test voice</button>
          <span className="jv-flex" />
          {props.canClose && (
            <button className="jv-btn" onClick={props.onClose}>Cancel</button>
          )}
          <button
            className="jv-btn jv-btn-primary"
            disabled={!anthropicKey.trim()}
            onClick={() => props.onSave(form())}
          >
            Save &amp; start
          </button>
        </div>
      </div>
    </div>
  )
}
