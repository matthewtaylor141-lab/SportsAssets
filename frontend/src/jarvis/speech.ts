/* Voice I/O for JARVIS.
 *
 * Ears  — Web Speech API recognition (push-to-talk one-shots or continuous
 *         hands-free), with auto-restart and interim results.
 * Mouth — sentence-queued TTS: ElevenLabs streaming (blob → shared <audio>
 *         through an AnalyserNode so the avatar rides real amplitude), with
 *         browser speechSynthesis as the fallback (utterance boundary events
 *         drive a synthetic pulse instead). Sentences never overlap; flush()
 *         is the barge-in path and kills everything instantly.
 */

/* ── minimal Web Speech typings (not in lib.dom) ─────────────────────── */

interface SRAlternative { transcript: string; confidence: number }
interface SRResult { isFinal: boolean; length: number; [i: number]: SRAlternative }
interface SRResultList { length: number; [i: number]: SRResult }
interface SREvent { resultIndex: number; results: SRResultList }
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  onresult: ((e: SREvent) => void) | null
  onend: (() => void) | null
  onerror: ((e: { error: string }) => void) | null
  start(): void
  stop(): void
  abort(): void
}
type SRCtor = new () => SpeechRecognitionLike

function recognitionCtor(): SRCtor | null {
  const w = window as unknown as { SpeechRecognition?: SRCtor; webkitSpeechRecognition?: SRCtor }
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export function sttSupported(): boolean {
  return recognitionCtor() !== null
}

export interface EarsHandlers {
  onInterim: (text: string) => void
  onFinal: (text: string) => void
  onState: (listening: boolean) => void
  onError: (message: string) => void
}

export class Ears {
  private rec: SpeechRecognitionLike | null = null
  /** User intent: keep listening (hands-free) until stop() is called. */
  private wantActive = false
  private continuous = false

  constructor(private handlers: EarsHandlers) {}

  get listening(): boolean { return this.rec !== null }

  start(continuous: boolean): void {
    const Ctor = recognitionCtor()
    if (!Ctor) {
      this.handlers.onError('Speech recognition is not supported in this browser.')
      return
    }
    this.stopInternal()
    this.wantActive = true
    this.continuous = continuous
    this.spinUp(Ctor)
  }

  private spinUp(Ctor: SRCtor): void {
    const rec = new Ctor()
    rec.lang = 'en-GB'
    rec.continuous = this.continuous
    rec.interimResults = true
    rec.maxAlternatives = 1
    rec.onresult = (e) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i]
        const text = (res[0]?.transcript || '').trim()
        if (!text) continue
        if (res.isFinal) this.handlers.onFinal(text)
        else interim += (interim ? ' ' : '') + text
      }
      if (interim) this.handlers.onInterim(interim)
    }
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        this.wantActive = false
        this.handlers.onError('Microphone access was denied — allow it in the browser to talk to JARVIS.')
      } else if (e.error !== 'no-speech' && e.error !== 'aborted') {
        this.handlers.onError(`Speech recognition error: ${e.error}`)
      }
    }
    rec.onend = () => {
      this.rec = null
      // Chrome ends continuous sessions after silence — restart while the
      // hands-free switch is still on.
      if (this.wantActive && this.continuous) {
        const C = recognitionCtor()
        if (C) { setTimeout(() => { if (this.wantActive && !this.rec) this.spinUp(C) }, 250); return }
      }
      this.wantActive = false
      this.handlers.onState(false)
    }
    this.rec = rec
    try {
      rec.start()
      this.handlers.onState(true)
    } catch {
      this.rec = null
      this.handlers.onState(false)
    }
  }

  /** Graceful stop: pending audio still produces a final result. */
  stop(): void {
    this.wantActive = false
    try { this.rec?.stop() } catch { /* already stopped */ }
  }

  /** Hard stop: discard anything in flight. */
  private stopInternal(): void {
    this.wantActive = false
    const rec = this.rec
    this.rec = null
    if (rec) {
      rec.onresult = null
      rec.onend = null
      rec.onerror = null
      try { rec.abort() } catch { /* already stopped */ }
    }
  }

  destroy(): void { this.stopInternal() }
}

/* ── TTS ─────────────────────────────────────────────────────────────── */

// Owner's pick 2026-08-21 ("use this voice id from eleven labs").
export const DEFAULT_ELEVEN_VOICE = 'vBKc2FfBKJfcZNyEt1n6'
/** The previous default — stored configs carrying it are migrated to
 * the owner's pick on load. */
export const LEGACY_DEFAULT_VOICE = 'onwK4e9ZLuTAKqWW03F9'

/** Curated ElevenLabs voices — the owner's pick first, then premade
 * voices picked for clarity (owner request 2026-08-21: "change your
 * voice for easy communication"). */
export const VOICE_PRESETS: { id: string; name: string; blurb: string }[] = [
  { id: 'vBKc2FfBKJfcZNyEt1n6', name: "Matt's pick", blurb: 'the voice Matt chose' },
  { id: 'ErXwobaYiN019PkySvjV', name: 'Antoni', blurb: 'clear, friendly American' },
  { id: 'nPczCjzI2devNBz1zQrb', name: 'Brian', blurb: 'smooth, easy American' },
  { id: 'pNInz6obpgDQGcFmaJgB', name: 'Adam', blurb: 'deep American' },
  { id: '21m00Tcm4TlvDq8ikWAM', name: 'Rachel', blurb: 'clear American female' },
  { id: 'onwK4e9ZLuTAKqWW03F9', name: 'Daniel', blurb: 'deep, calm British' },
  { id: 'JBFqnCBsd6RMkjVDRZzb', name: 'George', blurb: 'warm British' },
  { id: 'XB0fDUnXU5powFXDhCwa', name: 'Charlotte', blurb: 'warm European female' },
]

export interface VoiceConfig {
  elevenKey: string
  voiceId: string
  /** speechSynthesis voiceURI for the fallback path. */
  browserVoiceURI: string
  /** 0.8–1.2 speaking speed (both engines). */
  speed?: number
}

interface QueueItem {
  text: string
  /** ElevenLabs synthesis kicked off at enqueue time so audio for sentence
   * N+1 is ready the moment sentence N finishes. null → use fallback. */
  audio: Promise<Blob | null>
  epoch: number
}

/** Strip markdown & symbols the voice should never pronounce. */
export function speakable(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' code block on screen ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*|__|[*_#>|]/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

export class Mouth {
  private queue: QueueItem[] = []
  private epoch = 0
  private pumping = false
  private _speaking = false

  private ctx: AudioContext | null = null
  private el: HTMLAudioElement | null = null
  private analyser: AnalyserNode | null = null
  private freqData: Uint8Array<ArrayBuffer> | null = null
  private analyserLive = false
  private pulseAt = 0

  /** Holds the active utterance so Chrome cannot GC it before onend. */
  // eslint-disable-next-line no-unused-private-class-members
  private gcGuard: SpeechSynthesisUtterance | null = null

  /** Read by nothing — existing is the point (GC anchor). */
  hasPendingUtterance(): boolean { return this.gcGuard !== null }
  onSpeakingChange: ((speaking: boolean) => void) | null = null
  onError: ((message: string) => void) | null = null

  constructor(private getConfig: () => VoiceConfig) {}

  get speaking(): boolean { return this._speaking }

  /** Must be called from a user gesture once, so audio can autoplay. */
  ensureAudio(): void {
    if (!this.ctx) {
      try {
        const Ctor = window.AudioContext
          || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        if (Ctor) {
          this.ctx = new Ctor()
          this.el = new Audio()
          const src = this.ctx.createMediaElementSource(this.el)
          this.analyser = this.ctx.createAnalyser()
          this.analyser.fftSize = 256
          this.analyser.smoothingTimeConstant = 0.7
          this.freqData = new Uint8Array(this.analyser.frequencyBinCount)
          src.connect(this.analyser)
          this.analyser.connect(this.ctx.destination)
        }
      } catch { /* audio graph unavailable — fallback path still works */ }
    }
    if (this.ctx?.state === 'suspended') void this.ctx.resume().catch(() => {})
  }

  /** 0..1 amplitude for the avatar. Real analyser data when ElevenLabs audio
   * is playing; a decaying boundary pulse for the speechSynthesis fallback. */
  level(): number {
    if (this.analyserLive && this.analyser && this.freqData) {
      this.analyser.getByteFrequencyData(this.freqData)
      let sum = 0
      for (let i = 0; i < this.freqData.length; i++) sum += this.freqData[i]
      return Math.min(1, (sum / this.freqData.length / 255) * 2.2)
    }
    if (this._speaking) {
      const dt = performance.now() - this.pulseAt
      const pulse = Math.max(0, 1 - dt / 420)
      // gentle idle oscillation so the orb never freezes mid-utterance
      const wobble = 0.22 + 0.1 * Math.sin(performance.now() / 170)
      return Math.min(1, Math.max(pulse * 0.85, wobble))
    }
    return 0
  }

  speak(rawText: string): void {
    const text = speakable(rawText)
    if (!text) return
    const epoch = this.epoch
    const cfg = this.getConfig()
    const audio = cfg.elevenKey
      ? this.synthEleven(text, cfg).catch((e) => {
          this.onError?.(e instanceof Error ? e.message : String(e))
          return null
        })
      : Promise.resolve<Blob | null>(null)
    this.queue.push({ text, audio, epoch })
    void this.pump()
  }

  /** Barge-in: stop the current sentence and drop everything queued. */
  flush(): void {
    this.epoch++
    this.queue = []
    if (this.el) {
      try { this.el.pause(); this.el.removeAttribute('src'); this.el.load() } catch { /* noop */ }
    }
    try { window.speechSynthesis?.cancel() } catch { /* noop */ }
    this.analyserLive = false
    this.setSpeaking(false)
  }

  destroy(): void {
    this.flush()
    void this.ctx?.close().catch(() => {})
    this.ctx = null
  }

  private setSpeaking(v: boolean): void {
    if (this._speaking !== v) {
      this._speaking = v
      this.onSpeakingChange?.(v)
    }
  }

  /** ElevenLabs free tier allows 2 concurrent requests; enqueueing a long
   * reply fired one per sentence at once — everything past the first 429'd
   * and fell to the fallback path. Gate to 2 in flight, retry a 429 once. */
  private elevenInFlight = 0
  private async synthEleven(text: string, cfg: VoiceConfig): Promise<Blob | null> {
    while (this.elevenInFlight >= 2) {
      await new Promise((r) => setTimeout(r, 120))
    }
    this.elevenInFlight++
    try {
      let resp = await this.elevenFetch(text, cfg)
      if (resp.status === 429) {
        await new Promise((r) => setTimeout(r, 450))
        resp = await this.elevenFetch(text, cfg)
      }
      if (!resp.ok) throw new Error(`ElevenLabs ${resp.status} — falling back to the browser voice`)
      return await resp.blob()
    } finally {
      this.elevenInFlight--
    }
  }

  private async elevenFetch(text: string, cfg: VoiceConfig): Promise<Response> {
    const voice = cfg.voiceId || DEFAULT_ELEVEN_VOICE
    return await fetch(
      `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voice)}/stream`,
      {
        method: 'POST',
        headers: { 'xi-api-key': cfg.elevenKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          model_id: 'eleven_turbo_v2_5',
          voice_settings: {
            stability: 0.5, similarity_boost: 0.75,
            speed: Math.min(1.2, Math.max(0.8, cfg.speed ?? 1.0)),
          },
        }),
        signal: AbortSignal.timeout(30000),
      },
    )
  }

  private async pump(): Promise<void> {
    if (this.pumping) return
    this.pumping = true
    try {
      while (this.queue.length > 0) {
        const item = this.queue.shift()!
        if (item.epoch !== this.epoch) continue
        this.setSpeaking(true)
        let blob: Blob | null = null
        try { blob = await item.audio } catch { blob = null }
        if (item.epoch !== this.epoch) continue
        // Watchdog: a lost 'ended'/'onend' event must never wedge the
        // queue (owner report 2026-08-21: replies died after the first
        // sentence). Generous bound scaled to the sentence length.
        const maxMs = Math.min(30000, 6000 + item.text.length * 110)
        const play = blob && this.el
          ? this.playBlob(blob, item.epoch)
          : this.playSynthesis(item.text, item.epoch)
        await Promise.race([
          play, new Promise<void>((r) => setTimeout(r, maxMs)),
        ])
      }
    } finally {
      this.pumping = false
      if (this.queue.length === 0) this.setSpeaking(false)
    }
  }

  private playBlob(blob: Blob, epoch: number): Promise<void> {
    return new Promise((resolve) => {
      const el = this.el!
      const url = URL.createObjectURL(blob)
      let settled = false
      const done = () => {
        if (settled) return
        settled = true
        el.onended = null
        el.onerror = null
        el.onpause = null
        URL.revokeObjectURL(url)
        this.analyserLive = false
        resolve()
      }
      el.onended = done
      el.onerror = done
      // flush() pauses the element mid-sentence; 'ended' never fires then,
      // so 'pause' must also settle this promise or the queue deadlocks.
      el.onpause = done
      el.src = url
      this.analyserLive = true
      el.play().catch(() => {
        // Autoplay refused (no gesture yet) — degrade to captions only.
        if (epoch === this.epoch) this.onError?.('Audio playback was blocked — tap the mic once to enable sound.')
        done()
      })
    })
  }

  private playSynthesis(text: string, epoch: number): Promise<void> {
    return new Promise((resolve) => {
      const synth = window.speechSynthesis
      if (!synth) { resolve(); return }
      const utter = new SpeechSynthesisUtterance(text)
      const uri = this.getConfig().browserVoiceURI
      const voices = synth.getVoices()
      const chosen = voices.find((v) => v.voiceURI === uri) || pickBritishVoice(voices)
      if (chosen) utter.voice = chosen
      utter.lang = chosen?.lang || 'en-GB'
      utter.rate = 1.02 * Math.min(1.2, Math.max(0.8,
        this.getConfig().speed ?? 1.0))
      utter.pitch = 0.92
      utter.onboundary = () => { if (epoch === this.epoch) this.pulseAt = performance.now() }
      utter.onstart = () => { this.pulseAt = performance.now() }
      // Chrome quirks: a cancel() can leave the synth paused (speak()
      // then queues silently forever), and an unreferenced utterance
      // can be garbage-collected before onend fires.
      this.gcGuard = utter
      utter.onend = () => { this.gcGuard = null; resolve() }
      utter.onerror = () => { this.gcGuard = null; resolve() }
      try { synth.resume() } catch { /* noop */ }
      try { synth.speak(utter) } catch { resolve() }
    })
  }
}

/* ── browser voice helpers ───────────────────────────────────────────── */

export function britishVoices(): SpeechSynthesisVoice[] {
  try {
    const all = window.speechSynthesis?.getVoices() || []
    const gb = all.filter((v) => /en[-_]GB/i.test(v.lang))
    return gb.length > 0 ? gb : all.filter((v) => /^en/i.test(v.lang))
  } catch {
    return []
  }
}

/** Prefer a male-sounding en-GB voice for the default. */
export function pickBritishVoice(voices?: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const pool = voices?.filter((v) => /en[-_]GB/i.test(v.lang)) ?? britishVoices()
  if (pool.length === 0) return null
  return (
    pool.find((v) => /daniel|arthur|george|oliver|brian|male/i.test(v.name)) ||
    pool.find((v) => !/female|kate|serena|stephanie|susan|martha|sonia|libby|hazel/i.test(v.name)) ||
    pool[0]
  )
}
