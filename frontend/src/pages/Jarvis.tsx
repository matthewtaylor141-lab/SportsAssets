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
import { BootSequence, GridFloor, HudRing, Starfield, TelemetryRibbon } from '../jarvis/stage'
import { buildSystemPrompt, liveModel, MODEL_CHAIN, runConversation, type MessageParam, type TextBlock, type ToolResultBlock } from '../jarvis/claude'
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

/* ── pocket mode ─────────────────────────────────────────────────────── */

/** POCKET MODE: the installed-PWA ("Add to Home Screen") or small-phone
 * experience — the primary one. Standalone display OR a <720px viewport
 * flips the whole page into the immersive pocket layout. */
function detectPocket(): boolean {
  try {
    const standalone = window.matchMedia('(display-mode: standalone)').matches
      || (navigator as unknown as { standalone?: boolean }).standalone === true
    return standalone || window.innerWidth < 720
  } catch { return false }
}

/* ── "on my mind": the page notices things by itself ─────────────────── */

interface MindItem { line: string; ask: string }

/** After each pills poll, pick the SINGLE most notable thing from the
 * data the page already holds — pure local heuristic, zero extra API
 * calls. Priority: engine health, paused switch, a position under
 * water, the day's first settle, the biggest mover, an outsized day. */
function pickOnMyMind(a: {
  pnl: number | null
  settled: number | null
  prevSettled: number
  paused: boolean
  engineAgeS: number | null
  positions: { label: string; unrealized: number }[]
}): MindItem | null {
  if (a.engineAgeS != null && a.engineAgeS >= 900) {
    return {
      line: `the engine heartbeat is ${Math.round(a.engineAgeS / 60)} minutes old — that's red`,
      ask: 'The engine heartbeat looks stale — run the diagnostics and tell me what is wrong.',
    }
  }
  if (a.engineAgeS != null && a.engineAgeS >= 180) {
    return {
      line: `the engine heartbeat is running amber — ${Math.round(a.engineAgeS / 60)}m since the last beat`,
      ask: 'The engine heartbeat is amber — check the engine diagnostics.',
    }
  }
  if (a.paused) {
    return {
      line: 'the copy engine is sitting paused',
      ask: 'Why is the copy engine paused — and should it be?',
    }
  }
  if (a.positions.length > 0) {
    const worst = a.positions.reduce((b, p) => (p.unrealized < b.unrealized ? p : b))
    if (worst.unrealized <= -40) {
      return {
        line: `${worst.label} is $${Math.abs(Math.round(worst.unrealized))} under water`,
        ask: `How is our ${worst.label} position doing — should we cash out or hold?`,
      }
    }
  }
  if (a.prevSettled === 0 && (a.settled ?? 0) > 0) {
    return {
      line: `first settle of the day just landed${a.pnl != null
        ? ` — ${a.pnl >= 0 ? 'up' : 'down'} $${Math.abs(Math.round(a.pnl))} so far` : ''}`,
      ask: "What just settled? Walk me through today's fills.",
    }
  }
  if (a.positions.length > 0) {
    const big = a.positions.reduce((b, p) =>
      (Math.abs(p.unrealized) > Math.abs(b.unrealized) ? p : b))
    if (Math.abs(big.unrealized) >= 30) {
      return {
        line: `${big.label} is the biggest mover on the book, ${big.unrealized >= 0 ? '+' : '−'}$${Math.abs(Math.round(big.unrealized))} unrealized`,
        ask: `Tell me about the ${big.label} position — what's moving it?`,
      }
    }
  }
  if (a.pnl != null && Math.abs(a.pnl) >= 250) {
    return {
      line: `we're ${a.pnl >= 0 ? 'up' : 'down'} $${Math.abs(Math.round(a.pnl))} on the day`,
      ask: a.pnl >= 0 ? "What's driving today's gain?" : "What's driving today's drawdown?",
    }
  }
  return null
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
interface PanelState { id: number; title: string; kind: 'md' | 'pdf' | 'chart'; body: string }
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
  get_engine_diagnostics: 'Reading the engine vitals…',
  get_whale: 'Looking that whale up…',
  whale_deep_dive: 'Going deep on that whale…',
  show_markdown: 'Putting it on screen…',
  leave_note_for_engine_session: 'Leaving the note…',
  get_daily_series: 'Pulling the daily numbers…',
  show_chart: 'Drawing the chart…',
  price_history: 'Pulling the price history…',
  request_code_change: 'Filing the work order…',
  search_desk_markets: 'Searching the desk…',
  get_accounts: 'Opening the book…',
  get_desk_blotter: 'Pulling the blotter…',
  stage_desk_order: 'Staging the ticket…',
  stage_cash_out: 'Staging the cash-out…',
  confirm_staged_order: 'Placing the order…',
  cancel_staged_order: 'Tearing the ticket up…',
  cancel_desk_order: 'Cancelling that order…',
  write_journal: 'Writing the journal…',
}

/** MICRO-ACKS: one short canned line spoken when a tool round leaves
 * >1.2s of dead air — the silence otherwise reads as a hang. At most
 * one per turn, never over real speech, never after a barge-in. */
const ACK_LINES = ['Pulling that now.', 'One second.', 'Checking the book.']
const ACK_DELAY_MS = 1200

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
  /** Panel STACK: chart + markdown + pdf coexist (one of each kind, max
   * 3), newest on top, each dismissible. show_chart/show_report PUSH —
   * a new chart no longer wipes the table beside it. */
  const [panels, setPanels] = useState<PanelState[]>([])
  const panelSeqRef = useRef(0)
  const pushPanel = useCallback((p: Omit<PanelState, 'id'>) => {
    setPanels((prev) =>
      [...prev.filter((q) => q.kind !== p.kind),
       { ...p, id: ++panelSeqRef.current }].slice(-3))
  }, [])
  const closePanel = useCallback((id: number) => {
    setPanels((prev) => prev.filter((p) => p.id !== id))
  }, [])
  const [drawerOpen, setDrawerOpen] = useState(false)
  const drawerBodyRef = useRef<HTMLDivElement | null>(null)
  const [typed, setTyped] = useState('')
  const [pills, setPills] = useState<Pills>({
    loaded: false, pnl: null, settled: null, wins: null, armed: null, paused: false, engineAgeS: null,
  })
  /** Open positions with unrealized P&L, cycled by the exposure ribbon. */
  const [exposure, setExposure] = useState<string[]>([])
  const [journal, setJournal] = useState<{ entry: string; mood: string; at: string } | null>(null)
  /** POCKET MODE: standalone PWA or small viewport → immersive layout. */
  const [pocket, setPocket] = useState(detectPocket)
  /** SCENES (owner 2026-08-23 "office should look unreal"): Reactor is
   * the classic core; Hologram adds the grid deck; Minimal strips the
   * stage bare for screen-shares. Cycled from the top bar, persisted. */
  const [scene, setScene] = useState<'reactor' | 'hologram' | 'minimal'>(() => {
    try {
      const s = localStorage.getItem('jarvis_scene')
      return s === 'hologram' || s === 'minimal' ? s : 'reactor'
    } catch { return 'reactor' }
  })
  const cycleScene = () => {
    const next = scene === 'reactor' ? 'hologram'
      : scene === 'hologram' ? 'minimal' : 'reactor'
    setScene(next)
    try { localStorage.setItem('jarvis_scene', next) } catch { /* noop */ }
  }
  /** AMBIENT: no input for 90s → chrome fades, the core takes the room.
   * Any pointer/key/speech brings the cockpit back. The office-presence
   * mode: MERIDIAN alive on the screen without asking for attention. */
  const [ambient, setAmbient] = useState(false)
  const ambientTimerRef = useRef<number>(0)
  /** Settle shockwave: bumping this fires a ring off the core. */
  const [pulseSeq, setPulseSeq] = useState(0)
  /** ON MY MIND: the one thing the page noticed for itself this poll. */
  const [onMind, setOnMind] = useState<MindItem | null>(null)
  /** The last mind-line Matt tapped to ask — don't resurface it. */
  const mindAskedRef = useRef('')
  /** Last few journal entries, folded into the system prompt — the
   * presence references its own written continuity. */
  const journalRecapRef = useRef('')

  useEffect(() => {
    // Ambient arm/disarm: activity resets a 90s timer; speaking or an
    // open panel/drawer/setup keeps the cockpit awake.
    const wake = () => {
      setAmbient(false)
      window.clearTimeout(ambientTimerRef.current)
      ambientTimerRef.current = window.setTimeout(() => setAmbient(true), 90_000)
    }
    wake()
    const evs: (keyof WindowEventMap)[] = ['pointerdown', 'pointermove', 'keydown', 'touchstart']
    for (const e of evs) window.addEventListener(e, wake, { passive: true })
    return () => {
      window.clearTimeout(ambientTimerRef.current)
      for (const e of evs) window.removeEventListener(e, wake)
    }
  }, [])

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

  // Pocket detection tracks rotation/resize and display-mode changes.
  useEffect(() => {
    const onChange = () => setPocket(detectPocket())
    window.addEventListener('resize', onChange)
    let mq: MediaQueryList | null = null
    try {
      mq = window.matchMedia('(display-mode: standalone)')
      mq.addEventListener('change', onChange)
    } catch { mq = null }
    return () => {
      window.removeEventListener('resize', onChange)
      try { mq?.removeEventListener('change', onChange) } catch { /* noop */ }
    }
  }, [])

  // The transcript sheet follows the conversation to the newest bubble.
  useEffect(() => {
    const el = drawerBodyRef.current
    if (el && drawerOpen) el.scrollTop = el.scrollHeight
  }, [log, liveText, drawerOpen])

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
      pushPanel({ title: 'MERIDIAN — journal', kind: 'md',
                  body: md || '_No entries yet._' })
    } catch { /* the card itself still shows the latest */ }
  }, [pushPanel])

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
  /** Micro-ack bookkeeping: the pending dead-air timer and whether this
   * turn already spoke its single ack. */
  const ackTimerRef = useRef<number | null>(null)
  const ackSpokenRef = useRef(false)
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
    ackSpokenRef.current = false
    if (ackTimerRef.current != null) {
      window.clearTimeout(ackTimerRef.current)
      ackTimerRef.current = null
    }

    const chunker = makeChunker((s) => { if (!mutedRef.current) mouth.speak(s) })
    const baseExecute = createToolExecutor({
      adminToken: config.adminToken,
      ui: {
        showMarkdown: (title, md) => pushPanel({ title, kind: 'md', body: md }),
        showChart: (spec) => {
          const parsed = parseChartSpec(spec)
          if (!parsed) return false
          pushPanel({ title: parsed.title || 'Chart', kind: 'chart',
                      body: JSON.stringify(parsed) })
          return true
        },
        showPdf: (title, url) => pushPanel({ title, kind: 'pdf', body: url }),
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
    })
    // BRIEFING CINEMA: during the walk-in brief the P&L chart rises
    // while the words land over it — the daily series is already inside
    // the copies/track-record payload the brief fetches, so the page
    // pushes the chart itself from the passing tool result. Zero extra
    // API calls; a read-only observer, never touching the tool's result.
    const wantsBrief = /walk[- ]?in brief/i.test(text)
    let briefChartUp = false
    const executeTool = !wantsBrief ? baseExecute
      : async (name: string, input: unknown): Promise<string> => {
          const out = await baseExecute(name, input)
          if (!briefChartUp && (name === 'get_copies_record'
              || name === 'get_track_record_summary' || name === 'get_daily_series')) {
            try {
              const d = JSON.parse(out) as { last_7_days?: unknown; days?: unknown }
              const rows = (Array.isArray(d.last_7_days) ? d.last_7_days : d.days) as
                { date?: unknown; pnl?: unknown }[] | undefined
              const values = (Array.isArray(rows) ? rows : [])
                .filter((r) => r && r.pnl != null && Number.isFinite(Number(r.pnl)))
                .map((r) => ({ x: String(r.date ?? '').slice(-5), y: Number(r.pnl) }))
              if (values.length >= 2) {
                briefChartUp = true
                pushPanel({
                  title: 'Daily P&L', kind: 'chart',
                  body: JSON.stringify({ kind: 'bars', title: 'Daily P&L — the brief',
                    unit: '$', series: [{ name: 'P&L', values }] }),
                })
              }
            } catch { /* the chart is garnish — the brief never fails on it */ }
          }
          return out
        }
    try {
      const finalText = await runConversation({
        apiKey: config.anthropicKey,
        system: buildSystemPrompt(!!config.adminToken, recapRef.current,
                                  snapshotRef.current, journalRecapRef.current),
        tools: JARVIS_TOOLS,
        messages: msgs,
        executeTool,
        signal: ctrl.signal,
        onTextDelta: (d) => { setLiveText((p) => p + d); chunker.push(d) },
        onToolUse: (name) => {
          setHint(TOOL_NOTES[name] || `Running ${name}…`)
          // MICRO-ACK: if the tool round leaves >1.2s of dead air, say
          // one short canned line through the normal mouth queue. Fires
          // only while THIS turn is live, never over real speech, never
          // after a barge-in, and at most once per turn.
          if (!ackSpokenRef.current && ackTimerRef.current == null) {
            ackTimerRef.current = window.setTimeout(() => {
              ackTimerRef.current = null
              if (ackSpokenRef.current || mutedRef.current) return
              if (abortRef.current !== ctrl) return // turn ended or superseded
              if (mouth.speaking) return           // real speech covers the gap
              ackSpokenRef.current = true
              mouth.speak(ACK_LINES[Math.floor(Math.random() * ACK_LINES.length)])
            }, ACK_DELAY_MS)
          }
        },
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
      if (abortRef.current === ctrl) {
        setBusy(false)
        if (ackTimerRef.current != null) {
          window.clearTimeout(ackTimerRef.current)
          ackTimerRef.current = null
        }
      }
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

  /* ── screen wake lock while hands-free (pocket mode's long sits) ── */

  // Feature-detected: request while hands-free listening is on, release
  // when the page blurs or hides, re-acquire when it returns. Never an
  // error state — a refusal (battery saver) just means normal dimming.
  useEffect(() => {
    if (!handsFree) return
    type Sentinel = { release?: () => Promise<void> }
    const wl = (navigator as unknown as {
      wakeLock?: { request: (type: 'screen') => Promise<Sentinel> }
    }).wakeLock
    if (!wl || typeof wl.request !== 'function') return
    let lock: Sentinel | null = null
    let dead = false
    const acquire = () => {
      if (dead || document.visibilityState !== 'visible') return
      wl.request('screen').then((s) => {
        if (dead) void s.release?.().catch(() => {})
        else lock = s
      }).catch(() => { /* denied — hands-free still works, screen may dim */ })
    }
    const drop = () => { void lock?.release?.().catch(() => {}); lock = null }
    const onVis = () => {
      if (document.visibilityState === 'visible') acquire()
      else drop()
    }
    acquire()
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('blur', drop)
    return () => {
      dead = true
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('blur', drop)
      drop()
    }
  }, [handsFree])

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
          ? adminApi<{
              totals: { value: number; cash: number; unrealized: number }
              polymarket?: { positions?: {
                title: string | null; outcome: string | null; unrealized: number | null
              }[] }
              kalshi?: { positions?: {
                ticker: string | null; cost_usd: number | null; unrealized: number | null
              }[] }
            }>('/api/desk/accounts', token).catch(() => null)
          : Promise.resolve(null),
      ])
      if (dead) return
      // Baseline BEFORE this poll updates it — the on-my-mind heuristic
      // uses it to spot the day's first settle.
      const settledBefore = prevSettledRef.current
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
        setPulseSeq((s) => s + 1)   // the core detonates a settle ring
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
      // EXPOSURE RIBBON: every open position with its unrealized P&L
      // cycles under the core — the book stays in the room even when
      // nobody is asking about it.
      const held: string[] = []
      /** Numeric positions for the on-my-mind heuristic (same poll). */
      const posNum: { label: string; unrealized: number }[] = []
      for (const p of accounts?.polymarket?.positions || []) {
        const label = (p.outcome || p.title || '').slice(0, 26)
        if (!label) continue
        if (p.unrealized != null) posNum.push({ label, unrealized: p.unrealized })
        held.push(p.unrealized != null
          ? `${label} ${p.unrealized >= 0 ? '+' : '−'}$${Math.abs(p.unrealized).toFixed(0)}`
          : `${label} · open`)
      }
      for (const p of accounts?.kalshi?.positions || []) {
        if (!p.ticker) continue
        const label = p.ticker.slice(0, 26)
        if (p.unrealized != null) posNum.push({ label, unrealized: p.unrealized })
        held.push(p.unrealized != null
          ? `${label} ${p.unrealized >= 0 ? '+' : '−'}$${Math.abs(p.unrealized).toFixed(0)}`
          : p.cost_usd != null
            ? `${label} · $${Math.round(p.cost_usd)} in`
            : `${label} · open`)
      }
      setExposure(held.slice(0, 12))
      // ON MY MIND: one most-notable thing, picked locally from this
      // same poll — never resurfacing the line Matt already tapped.
      const mind = pickOnMyMind({
        pnl: today ? today.pnl : null,
        settled: today ? today.settled : null,
        prevSettled: settledBefore,
        paused: !!live?.paused,
        engineAgeS: engine?.beat_at
          ? Math.max(0, Math.round((Date.now() - new Date(engine.beat_at).getTime()) / 1000))
          : null,
        positions: posNum,
      })
      setOnMind(mind && mind.line !== mindAskedRef.current ? mind : null)
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

  // Ambient never swallows an active exchange or an open surface.
  const ambientLive = ambient && !speaking && !busy && !listening
    && !setupOpen && !drawerOpen && panels.length === 0 && !stagedView

  return (
    <div className={`jv-root jv-scene-${scene}${pocket ? ' jv-pocket' : ''}${ambientLive ? ' jv-ambient' : ''}`}>
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
          {!pocket && (
            <button className="jv-iconbtn" aria-label="Cycle scene"
                    title={`Scene: ${scene} — tap to cycle`}
                    onClick={cycleScene}>✦</button>
          )}
          {/* pocket mode moves transcript + settings into the thumb bar */}
          {!pocket && (
            <button className="jv-iconbtn" title="Transcript" aria-label="Transcript"
                    onClick={() => setDrawerOpen((v) => !v)}>≡</button>
          )}
          {!pocket && (
            <button className="jv-iconbtn" title="Settings" aria-label="Settings"
                    onClick={() => setSetupOpen(true)}>⚙</button>
          )}
          <Link className="jv-iconbtn" title="Exit to site" aria-label="Exit" to="/">✕</Link>
        </div>
      </header>

      {/* ── stage ── */}
      <div className={`jv-stage${panels.length > 0 ? ' jv-with-panel' : ''}`}>
        <Starfield />
        {scene === 'hologram' && <GridFloor />}
        <div className="jv-orb-wrap">
          <HudRing state={avatarState} />
          <JarvisAvatar state={avatarState} tone={tone} pulseSeq={pulseSeq}
                        getLevel={() => mouthRef.current?.level() ?? 0} />
          <div className={`jv-state jv-state-${avatarState}`}>{stateLabel}</div>
          <BootSequence />
        </div>
        <TelemetryRibbon items={ribbon} />
        {exposure.length > 0 && (
          <div className="jv-exposure" aria-hidden>
            <TelemetryRibbon items={exposure} />
          </div>
        )}
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
        {onMind && (
          <aside className="jv-mind" role="button" tabIndex={0}
                 aria-label={`On my mind: ${onMind.line}. Tap to ask.`}
                 onClick={() => {
                   mindAskedRef.current = onMind.line
                   setOnMind(null)
                   void send(onMind.ask)
                 }}
                 onKeyDown={(e) => {
                   if (e.key !== 'Enter') return
                   mindAskedRef.current = onMind.line
                   setOnMind(null)
                   void send(onMind.ask)
                 }}>
            <span className="jv-mind-dot" />
            <div className="jv-mind-body">
              <span className="jv-mind-head">on my mind</span>
              <p>{onMind.line}</p>
            </div>
            <span className="jv-mind-ask">ask ›</span>
          </aside>
        )}

        <div className="jv-captions" aria-live="polite">
          {userInterim && <p className="jv-cap-user">{userInterim}</p>}
          {liveText && (
            <p className="jv-cap-ai">{liveText}<span className="jv-caret" aria-hidden /></p>
          )}
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
          {pocket && (
            <button className="jv-iconbtn jv-dock-side jv-dock-left"
                    title="Transcript" aria-label="Transcript"
                    onClick={() => setDrawerOpen((v) => !v)}>≡</button>
          )}
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
          {pocket && (
            <button className="jv-iconbtn jv-dock-side jv-dock-right"
                    title="Settings" aria-label="Settings"
                    onClick={() => setSetupOpen(true)}>⚙</button>
          )}
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

      {/* ── report panels (stack: newest on top, each dismissible) ── */}
      {panels.length > 0 && (
        <aside className="jv-panels" data-count={panels.length}>
          {[...panels].reverse().map((p) => (
            <article className="jv-panel" key={p.id}>
              <header className="jv-panel-head">
                <span className="jv-panel-title">{p.title}</span>
                {p.kind === 'pdf' && (
                  <a className="jv-panel-open" href={p.body} target="_blank" rel="noopener noreferrer">
                    open ↗
                  </a>
                )}
                <button className="jv-iconbtn" aria-label={`Close ${p.title}`}
                        onClick={() => closePanel(p.id)}>✕</button>
              </header>
              {p.kind === 'md' ? (
                <div className="jv-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(p.body) }} />
              ) : p.kind === 'chart' ? (
                <div className="jv-panel-chart">
                  {(() => { const sp = parseChartSpec(p.body); return sp ? <MeridianChart spec={sp} /> : null })()}
                </div>
              ) : (
                <PdfFrame url={p.body} title={p.title} />
              )}
            </article>
          ))}
        </aside>
      )}

      {/* ── transcript drawer ── */}
      {drawerOpen && (
        <aside className="jv-drawer">
          <header className="jv-panel-head">
            <span className="jv-panel-title">Session transcript</span>
            <button className="jv-iconbtn" aria-label="Close transcript" onClick={() => setDrawerOpen(false)}>✕</button>
          </header>
          <div className="jv-drawer-body" ref={drawerBodyRef}>
            {log.length === 0 && !liveText &&
              <p className="jv-dim-text">Nothing yet — say something.</p>}
            {log.map((t, i) => (
              <div key={`${t.at}-${i}`} className={`jv-turn jv-turn-${t.role}`} tabIndex={0}>
                <span className="jv-turn-who">
                  {t.role === 'user' ? 'MATT' : 'MERIDIAN'}
                  <time className="jv-turn-time">
                    {new Date(t.at).toLocaleTimeString('en-US',
                      { hour: 'numeric', minute: '2-digit' })}
                  </time>
                </span>
                <p>{t.text}</p>
              </div>
            ))}
            {liveText && (
              <div className="jv-turn jv-turn-assistant jv-turn-live">
                <span className="jv-turn-who">MERIDIAN</span>
                <p>{liveText}<span className="jv-caret" aria-hidden /></p>
              </div>
            )}
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
        <p className="jv-setup-brain">
          {liveModel()
            ? <>brain live this session: <strong>{liveModel()}</strong></>
            : <>brain: <strong>{MODEL_CHAIN[0]}</strong> — first turn pending</>}
          {' '}· fallback {MODEL_CHAIN.join(' → ')}
        </p>

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
