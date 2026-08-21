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
import { renderMarkdown } from '../jarvis/markdown'
import {
  britishVoices, DEFAULT_ELEVEN_VOICE, Ears, Mouth, pickBritishVoice, sttSupported,
} from '../jarvis/speech'
import { createToolExecutor, JARVIS_TOOLS } from '../jarvis/tools'
import { api } from '../lib/api'
import '../jarvis/jarvis.css'

/* ── config (localStorage; keys never leave the browser except to their own APIs) ── */

interface JarvisConfig {
  anthropicKey: string
  elevenKey: string
  voiceId: string
  browserVoice: string
  adminToken: string
}

const LS = {
  anthropic: 'jarvis_anthropic_key',
  eleven: 'jarvis_eleven_key',
  voice: 'jarvis_eleven_voice',
  browserVoice: 'jarvis_browser_voice',
  admin: 'jarvis_admin_token',
}

function loadConfig(): JarvisConfig {
  const g = (k: string) => { try { return localStorage.getItem(k) || '' } catch { return '' } }
  let admin = g(LS.admin)
  if (!admin) { try { admin = sessionStorage.getItem('sa_admin_token') || '' } catch { /* noop */ } }
  return {
    anthropicKey: g(LS.anthropic),
    elevenKey: g(LS.eleven),
    voiceId: g(LS.voice) || DEFAULT_ELEVEN_VOICE,
    browserVoice: g(LS.browserVoice),
    adminToken: admin,
  }
}

function saveConfig(c: JarvisConfig): void {
  try {
    localStorage.setItem(LS.anthropic, c.anthropicKey)
    localStorage.setItem(LS.eleven, c.elevenKey)
    localStorage.setItem(LS.voice, c.voiceId)
    localStorage.setItem(LS.browserVoice, c.browserVoice)
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
interface PanelState { title: string; kind: 'md' | 'pdf'; body: string }
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
}

const WAKE = /\bjarvis\b/i

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

  const [sttOk] = useState(() => sttSupported())

  const mouthRef = useRef<Mouth | null>(null)
  const earsRef = useRef<Ears | null>(null)
  const historyRef = useRef<MessageParam[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const keyHeld = useRef(false)
  /** Set on barge-in: the interrupted turn keeps streaming captions but must
   * not enqueue any further speech. Cleared when the next turn starts. */
  const mutedRef = useRef(false)

  // Latest-callback refs so long-lived Ears/keyboard handlers never go stale.
  const onInterimRef = useRef<(t: string) => void>(() => {})
  const onFinalRef = useRef<(t: string) => void>(() => {})

  if (!mouthRef.current) {
    mouthRef.current = new Mouth(() => ({
      elevenKey: cfgRef.current.elevenKey,
      voiceId: cfgRef.current.voiceId,
      browserVoiceURI: cfgRef.current.browserVoice,
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
    mouth.onSpeakingChange = setSpeaking
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
    pushUserText(msgs, text)
    trimHistory(msgs)

    setLog((l) => [...l, { role: 'user', text, at: Date.now() }])
    setLiveText('')
    setHint('')
    setBusy(true)
    mutedRef.current = false

    const chunker = makeChunker((s) => { if (!mutedRef.current) mouth.speak(s) })
    try {
      const finalText = await runConversation({
        apiKey: config.anthropicKey,
        system: buildSystemPrompt(!!config.adminToken),
        tools: JARVIS_TOOLS,
        messages: msgs,
        executeTool: createToolExecutor({
          adminToken: config.adminToken,
          ui: {
            showMarkdown: (title, md) => setPanel({ title, kind: 'md', body: md }),
            showPdf: (title, url) => setPanel({ title, kind: 'pdf', body: url }),
          },
        }),
        signal: ctrl.signal,
        onTextDelta: (d) => { setLiveText((p) => p + d); chunker.push(d) },
        onToolUse: (name) => setHint(TOOL_NOTES[name] || `Running ${name}…`),
      })
      chunker.flush()
      setHint('')
      setLiveText('')
      if (finalText) setLog((l) => [...l, { role: 'assistant', text: finalText, at: Date.now() }])
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
      // Barge-in. In hands-free mode require the wake word so the mic
      // picking up JARVIS's own voice on open speakers can't self-interrupt.
      if (!handsFreeRef.current || WAKE.test(t)) {
        mouth.flush()
        mutedRef.current = true // captions continue; speech stays stopped
      }
    }
  }

  onFinalRef.current = (t: string) => {
    setUserInterim('')
    let text = t.trim()
    if (handsFreeRef.current) {
      const m = WAKE.exec(text)
      if (!m) return // not addressed to JARVIS — ignore
      text = text.slice(m.index + m[0].length).replace(/^[\s,.!?:;-]+/, '').trim()
      const mouth = mouthRef.current!
      if (!text) { mouth.flush(); mouth.ensureAudio(); mouth.speak('Yes?'); return }
    }
    void send(text)
  }

  const pttStart = useCallback(() => {
    if (handsFreeRef.current || !sttOk) return
    const mouth = mouthRef.current!
    mouth.ensureAudio()
    if (mouth.speaking) { mouth.flush(); mutedRef.current = true }
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
      setHint('Hands-free on — say "JARVIS", then your question.')
    } else {
      earsRef.current!.stop()
      setHint('')
    }
  }

  /* ── status strip polling ── */

  useEffect(() => {
    let dead = false
    const load = async () => {
      const [today, live, engine] = await Promise.all([
        api<{ pnl: number; settled: number; wins: number }>('/api/today-live').catch(() => null),
        api<{ enabled: boolean; paused: boolean }>('/api/live-status').catch(() => null),
        api<{ beat_at?: string }>('/api/engine/status').catch(() => null),
      ])
      if (dead) return
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

  const stateLabel =
    speaking ? 'Speaking' : busy ? 'Thinking' : listening
      ? (handsFree ? 'Listening — say "JARVIS…"' : 'Listening')
      : handsFree ? 'Standing by — say "JARVIS…"' : 'Standing by'

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
        <span className="jv-brand">J.A.R.V.I.S.<em>BettorToken</em></span>
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
          <JarvisAvatar state={avatarState} getLevel={() => mouthRef.current?.level() ?? 0} />
          <div className={`jv-state jv-state-${avatarState}`}>{stateLabel}</div>
          <BootSequence />
        </div>
        <TelemetryRibbon items={ribbon} />

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
        <div className="jv-dock">
          <form className="jv-typed" onSubmit={submitTyped}>
            <input
              className="jv-typed-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Type to JARVIS…"
              aria-label="Type to JARVIS"
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
                <span className="jv-turn-who">{t.role === 'user' ? 'MATT' : 'JARVIS'}</span>
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
  })

  return (
    <div className="jv-setup-scrim">
      <div className="jv-setup" role="dialog" aria-label="JARVIS setup">
        <h2>Bring JARVIS online</h2>
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
          <label className="jv-field">
            <span>ElevenLabs voice id <em>default: Daniel — deep, calm, British</em></span>
            <input type="text" autoComplete="off"
                   value={voiceId} onChange={(e) => setVoiceId(e.target.value)} />
          </label>
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
          <span>Platform admin token <em>optional — unlocks engine notes</em></span>
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
