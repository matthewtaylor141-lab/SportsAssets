/* Stage furniture around the core: a parallax starfield with occasional
 * meteors, the rotating HUD ring, a boot sequence, and the telemetry
 * ribbon that breathes live platform numbers through the room.
 * All dependency-free; starfield honors prefers-reduced-motion. */

import { useEffect, useRef, useState } from 'react'

/* ── starfield ───────────────────────────────────────────────────── */

interface Star { x: number; y: number; z: number; tw: number }
interface Meteor { x: number; y: number; vx: number; vy: number; life: number }

export function Starfield({ getWarp }: { getWarp?: () => number } = {}) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const warpRef = useRef<(() => number) | undefined>(getWarp)
  warpRef.current = getWarp

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')

    let stars: Star[] = []
    const seed = () => {
      stars = []
      const n = Math.round((canvas.width * canvas.height) / 16000)
      for (let i = 0; i < n; i++)
        stars.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height,
          z: 0.25 + Math.random() * 0.75,       // depth → speed + size
          tw: Math.random() * Math.PI * 2,
        })
    }
    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const rect = canvas.getBoundingClientRect()
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
      seed()
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(canvas)

    let meteors: Meteor[] = []
    let nextMeteor = performance.now() + 9000 + Math.random() * 14000
    let raf = 0
    let running = true
    const t0 = performance.now()

    // WARP: while MERIDIAN thinks, the room computes — the drift eases
    // toward ~5x and stars stretch into short streaks, then settles.
    let warp = 1
    const frame = () => {
      if (!running) return
      const now = performance.now()
      const t = (now - t0) / 1000
      const w = canvas.width, h = canvas.height
      const wantWarp = Math.max(1, Math.min(6, warpRef.current?.() ?? 1))
      warp += (wantWarp - warp) * 0.04
      ctx.clearRect(0, 0, w, h)
      for (const s of stars) {
        s.x -= s.z * 0.06 * warp                 // slow drift (or warp)
        if (s.x < -2) { s.x = w + 2; s.y = Math.random() * h }
        const a = 0.25 + 0.5 * s.z + 0.22 * Math.sin(t * (0.6 + s.z) + s.tw)
        ctx.fillStyle = `rgba(160, 215, 255, ${Math.max(0.05, a * 0.5)})`
        const r = s.z * 1.25
        if (warp > 1.25) {
          ctx.fillRect(s.x, s.y, r + s.z * 2.2 * (warp - 1), Math.max(0.8, r * 0.8))
        } else {
          ctx.fillRect(s.x, s.y, r, r)
        }
      }
      if (now > nextMeteor) {
        nextMeteor = now + 12000 + Math.random() * 20000
        const fromTop = Math.random() < 0.7
        meteors.push({
          x: Math.random() * w * 0.8 + w * 0.1,
          y: fromTop ? -10 : Math.random() * h * 0.3,
          vx: -(3 + Math.random() * 3),
          vy: 2 + Math.random() * 2,
          life: 1,
        })
      }
      meteors = meteors.filter((m) => m.life > 0)
      for (const m of meteors) {
        m.x += m.vx; m.y += m.vy; m.life -= 0.016
        const grad = ctx.createLinearGradient(
          m.x, m.y, m.x - m.vx * 9, m.y - m.vy * 9)
        grad.addColorStop(0, `rgba(200, 235, 255, ${0.65 * m.life})`)
        grad.addColorStop(1, 'rgba(200, 235, 255, 0)')
        ctx.strokeStyle = grad
        ctx.lineWidth = 1.2
        ctx.beginPath()
        ctx.moveTo(m.x, m.y)
        ctx.lineTo(m.x - m.vx * 9, m.y - m.vy * 9)
        ctx.stroke()
      }
      if (reduced.matches && t > 0.2) return     // one still frame
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { running = false; cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={ref} className="jv-stars" aria-hidden />
}

/* ── HUD ring: counter-rotating dashed arcs + tick marks ─────────── */

export function HudRing({ state }: { state: string }) {
  return (
    <svg className={`jv-hud jv-hud-${state}`} viewBox="0 0 200 200" aria-hidden>
      <g className="jv-hud-slow">
        <circle cx="100" cy="100" r="88" fill="none"
                strokeDasharray="2 9" strokeWidth="0.7" />
        <path d="M 100 6 A 94 94 0 0 1 194 100" fill="none" strokeWidth="1.1" />
      </g>
      <g className="jv-hud-fast">
        <circle cx="100" cy="100" r="79" fill="none"
                strokeDasharray="24 40" strokeWidth="0.55" />
        <path d="M 21 100 A 79 79 0 0 1 100 21" fill="none"
              strokeWidth="1.4" className="jv-hud-arc" />
      </g>
      {[0, 90, 180, 270].map((a) => (
        <line key={a} x1="100" y1="4" x2="100" y2="10"
              strokeWidth="1" transform={`rotate(${a} 100 100)`} />
      ))}
    </svg>
  )
}

/* ── orbital megastructure: the machine around the mind ──────────── */
/* Three segmented arcs + a degree dial + orbit paths, all counter-
 * rotating at geological speeds behind the HUD ring. Pure SVG. */

export function OrbitalFrame({ state }: { state: string }) {
  const segs = (r: number, n: number, fill: number, key: string) => {
    const out = []
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2
      const a1 = a0 + (fill / n) * Math.PI * 2
      out.push(
        <path key={`${key}-${i}`} fill="none" strokeWidth={r > 130 ? 2.4 : 1.2}
          d={`M ${200 + r * Math.cos(a0)} ${200 + r * Math.sin(a0)} A ${r} ${r} 0 0 1 ${200 + r * Math.cos(a1)} ${200 + r * Math.sin(a1)}`} />,
      )
    }
    return out
  }
  return (
    <svg className={`jv-orbit jv-orbit-${state}`} viewBox="0 0 400 400" aria-hidden>
      <g className="jv-orbit-a">{segs(186, 36, 0.5, 'a')}</g>
      <g className="jv-orbit-b">{segs(172, 8, 0.72, 'b')}
        <circle cx="200" cy="372" r="3.4" className="jv-orbit-sat" />
      </g>
      <g className="jv-orbit-c">
        {segs(158, 96, 0.28, 'c')}
        {[0, 45, 90, 135, 180, 225, 270, 315].map((d) => (
          <text key={d} x="200" y="24" textAnchor="middle" fontSize="7.5"
            transform={`rotate(${d} 200 200)`} className="jv-orbit-deg">
            {String(d).padStart(3, '0')}
          </text>
        ))}
      </g>
      <ellipse cx="200" cy="200" rx="196" ry="66" className="jv-orbit-path" />
    </svg>
  )
}

/* ── data streams: the platform's own numbers raining through the
 * room's edges. Real ribbon strings feed the glyphs — the movement IS
 * the telemetry, never decoration. ──────────────────────────────── */

interface StreamGlyph { y: number; v: number; text: string; a: number }

export function DataStreams({ items }: { items: string[] }) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const itemsRef = useRef(items)
  itemsRef.current = items

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduced.matches) return                  // stillness is fine

    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const rect = canvas.getBoundingClientRect()
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(canvas)

    const FALLBACK = ['0x076daa87', 'RN1', 'KXBTC15M', 'settle', 'copy',
      'edge', 'armed', 'bid', 'ask', 'fill']
    const cols: { x: number; glyphs: StreamGlyph[]; next: number }[] = []
    let raf = 0
    let running = true
    let last = performance.now()

    const frame = () => {
      if (!running) return
      const now = performance.now()
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      const w = canvas.width, h = canvas.height
      // Two thin gutters: 0-7% and 93-100% of the width.
      if (cols.length === 0 && w > 0) {
        for (const fx of [0.015, 0.045, 0.955, 0.985]) {
          cols.push({ x: fx * w, glyphs: [], next: Math.random() * 1.4 })
        }
      }
      ctx.clearRect(0, 0, w, h)
      ctx.font = `${Math.max(9, Math.round(h / 92))}px 'JetBrains Mono', monospace`
      const pool = itemsRef.current.length ? itemsRef.current : FALLBACK
      for (const c of cols) {
        c.next -= dt
        if (c.next <= 0 && c.glyphs.length < 7) {
          const src = pool[Math.floor(Math.random() * pool.length)] || ''
          const frag = src.length > 14
            ? src.slice(Math.floor(Math.random() * Math.max(1, src.length - 14)))
              .slice(0, 14)
            : src
          c.glyphs.push({ y: h + 20, v: (26 + Math.random() * 34) * (h / 900),
            text: frag.toUpperCase(), a: 0.05 + Math.random() * 0.13 })
          c.next = 0.9 + Math.random() * 2.4
        }
        for (const g of c.glyphs) {
          g.y -= g.v * dt
          const fade = Math.min(1, Math.max(0, g.y / h))
          ctx.fillStyle = `rgba(105, 224, 255, ${(g.a * fade).toFixed(3)})`
          ctx.save()
          ctx.translate(c.x, g.y)
          ctx.rotate(-Math.PI / 2)
          ctx.fillText(g.text, 0, 0)
          ctx.restore()
        }
        c.glyphs = c.glyphs.filter((g) => g.y > -160)
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { running = false; cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={ref} className="jv-streams" aria-hidden />
}

/* ── voice arc: MERIDIAN's voice made visible — a ring segment of
 * bars under the core breathing with the live TTS amplitude. ─────── */

export function VoiceArc({ getLevel, active }: {
  getLevel: () => number
  active: boolean
}) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const levelRef = useRef(getLevel)
  const activeRef = useRef(active)
  levelRef.current = getLevel
  activeRef.current = active

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduced.matches) return

    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const rect = canvas.getBoundingClientRect()
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(canvas)

    const N = 56
    const bars = new Array(N).fill(0)
    let vis = 0                                  // eased visibility
    let raf = 0
    let running = true
    const t0 = performance.now()

    const frame = () => {
      if (!running) return
      const t = (performance.now() - t0) / 1000
      const w = canvas.width, h = canvas.height
      ctx.clearRect(0, 0, w, h)
      vis += ((activeRef.current ? 1 : 0) - vis) * 0.07
      if (vis > 0.01) {
        const lvl = Math.max(0, Math.min(1, levelRef.current()))
        const cx = w / 2
        const cy = h * 0.02                       // arc centre above box
        const R = Math.min(w, h * 6) * 0.34
        for (let i = 0; i < N; i++) {
          const ph = (i / N) * Math.PI * 7 + t * 5.2
          const want = lvl * (0.35 + 0.65 * Math.abs(Math.sin(ph)))
          bars[i] += (want - bars[i]) * 0.35
          const a = Math.PI * (0.18 + 0.64 * (i / (N - 1)))   // lower arc
          const len = (4 + bars[i] * h * 0.72) * vis
          const x0 = cx + R * Math.cos(a)
          const y0 = cy + R * Math.sin(a) * 0.62
          const x1 = cx + (R + len) * Math.cos(a)
          const y1 = cy + (R + len) * Math.sin(a) * 0.62
          ctx.strokeStyle = `rgba(232, 200, 119, ${(0.12 + bars[i] * 0.5) * vis})`
          ctx.lineWidth = Math.max(1.5, w / 640)
          ctx.beginPath()
          ctx.moveTo(x0, y0)
          ctx.lineTo(x1, y1)
          ctx.stroke()
        }
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { running = false; cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={ref} className="jv-voicearc" aria-hidden />
}

/* ── holographic grid floor (Hologram scene) ─────────────────────── */
/* A perspective deck sliding slowly toward the viewer: horizontal
 * scanlines with exponential spacing off a horizon, converging
 * verticals, everything fading with distance. Pure 2D canvas. */

export function GridFloor() {
  const ref = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')

    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const rect = canvas.getBoundingClientRect()
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(canvas)

    let raf = 0
    let running = true
    const t0 = performance.now()

    const frame = () => {
      if (!running) return
      const t = (performance.now() - t0) / 1000
      const w = canvas.width, h = canvas.height
      ctx.clearRect(0, 0, w, h)
      const horizon = h * 0.66            // deck occupies the lower third
      const cx = w / 2
      // horizontal scanlines: constant world spacing, projected — the
      // scroll phase loops so the deck glides forever
      const phase = (t * 0.12) % 1
      for (let i = 0; i < 24; i++) {
        const world = i + 1 - phase       // distance from viewer, 0=near
        const y = horizon + (h - horizon) * (1.6 / (world * 0.55 + 0.9))
        if (y < horizon || y > h + 4) continue
        const a = Math.max(0, 0.30 - world * 0.013)
        ctx.strokeStyle = `rgba(105, 224, 255, ${a})`
        ctx.lineWidth = world < 3 ? 1.4 : 1
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(w, y)
        ctx.stroke()
      }
      // converging verticals
      for (let i = -14; i <= 14; i++) {
        const xNear = cx + i * (w / 14)
        const xFar = cx + i * (w / 90)
        ctx.strokeStyle = `rgba(105, 224, 255, ${0.16 - Math.abs(i) * 0.007})`
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(xFar, horizon)
        ctx.lineTo(xNear, h)
        ctx.stroke()
      }
      // horizon glow line
      const grad = ctx.createLinearGradient(0, horizon - 14, 0, horizon + 6)
      grad.addColorStop(0, 'rgba(105, 224, 255, 0)')
      grad.addColorStop(1, 'rgba(105, 224, 255, 0.28)')
      ctx.fillStyle = grad
      ctx.fillRect(0, horizon - 14, w, 20)
      if (reduced.matches && t > 0.2) return   // one still frame
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { running = false; cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={ref} className="jv-floor" aria-hidden />
}

/* ── boot sequence ───────────────────────────────────────────────── */

const BOOT_LINES = [
  'BETTORTOKEN INTELLIGENCE',
  'LINK ESTABLISHED — ENGINE / LEDGER / DESK',
  'TELEMETRY STREAMING',
  'MERIDIAN ONLINE',
]

export function BootSequence() {
  const [done, setDone] = useState(false)
  const [shown, setShown] = useState(0)
  useEffect(() => {
    const timers = BOOT_LINES.map((_, i) =>
      window.setTimeout(() => setShown(i + 1), 260 + i * 340))
    const end = window.setTimeout(() => setDone(true), 260 + 4 * 340 + 900)
    return () => { timers.forEach(clearTimeout); clearTimeout(end) }
  }, [])
  if (done) return null
  return (
    <div className="jv-boot" onClick={() => setDone(true)} aria-hidden>
      {BOOT_LINES.slice(0, shown).map((l, i) => (
        <p key={l} className={i === shown - 1 ? 'jv-boot-new' : ''}>
          <span className="jv-boot-tick">▸</span> {l}
        </p>
      ))}
    </div>
  )
}

/* ── telemetry ribbon ────────────────────────────────────────────── */

export function TelemetryRibbon({ items }: { items: string[] }) {
  const [i, setI] = useState(0)
  const [visible, setVisible] = useState(true)
  useEffect(() => {
    if (items.length < 2) return
    const t = setInterval(() => {
      setVisible(false)
      setTimeout(() => { setI((v) => (v + 1) % items.length); setVisible(true) }, 380)
    }, 6200)
    return () => clearInterval(t)
  }, [items.length])
  if (!items.length) return null
  return (
    <div className="jv-ribbon" aria-hidden>
      <span className="jv-ribbon-rule" />
      <span className={`jv-ribbon-text${visible ? '' : ' jv-ribbon-out'}`}>
        {items[i % items.length]}
      </span>
      <span className="jv-ribbon-rule" />
    </div>
  )
}

/* ── whale constellation (v9, owner order 2026-08-28) ─────────────────
 * The roster as a slow orbital system around the core: each copied
 * whale is a named node on its own orbit — node size follows open
 * stake, tint follows the record (gold when the book is up on them,
 * dim ember when down, cyan when flat/unknown). Real numbers only;
 * the canvas renders nothing until data arrives. Pure 2D canvas,
 * device-pixel aware, paused when the tab is hidden. */

export interface WhaleNode {
  name: string
  pnl: number | null
  openStake: number
}

export function WhaleConstellation({ whales }: { whales: WhaleNode[] }) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const dataRef = useRef<WhaleNode[]>(whales)
  dataRef.current = whales

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let raf = 0
    let dead = false
    const dpr = Math.min(2, window.devicePixelRatio || 1)

    const size = () => {
      const r = canvas.getBoundingClientRect()
      canvas.width = Math.max(1, Math.round(r.width * dpr))
      canvas.height = Math.max(1, Math.round(r.height * dpr))
    }
    size()
    const ro = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(size) : null
    ro?.observe(canvas)

    const t0 = performance.now()
    const draw = (now: number) => {
      if (dead) return
      raf = requestAnimationFrame(draw)
      if (document.visibilityState !== 'visible') return
      const w = canvas.width, h = canvas.height
      ctx.clearRect(0, 0, w, h)
      const nodes = dataRef.current
      if (!nodes.length) return
      const cx = w / 2, cy = h / 2
      const base = Math.min(w, h) * 0.36
      const spread = Math.min(w, h) * 0.115
      const maxStake = Math.max(1, ...nodes.map((n) => n.openStake))
      const t = (now - t0) / 1000
      nodes.forEach((n, i) => {
        const orbitR = base + spread * (i / Math.max(1, nodes.length - 1))
        // Deterministic phase per node; geological angular speed.
        const phase = (i * 2.399963) % (Math.PI * 2)   // golden angle
        const speed = 0.017 + 0.006 * ((i * 7919) % 5) / 5
        const a = phase + t * speed
        const x = cx + Math.cos(a) * orbitR
        const y = cy + Math.sin(a) * orbitR * 0.62      // ellipse: depth
        // Orbit path, barely there.
        ctx.beginPath()
        ctx.ellipse(cx, cy, orbitR, orbitR * 0.62, 0, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(120, 150, 180, 0.045)'
        ctx.lineWidth = 1
        ctx.stroke()
        const up = (n.pnl ?? 0) > 0.5
        const down = (n.pnl ?? 0) < -0.5
        const tint = up ? '232, 200, 119' : down ? '224, 112, 96'
          : '105, 224, 255'
        const r = (2.2 + 3.4 * Math.sqrt(n.openStake / maxStake)) * dpr
        const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 3.4)
        glow.addColorStop(0, `rgba(${tint}, 0.5)`)
        glow.addColorStop(1, `rgba(${tint}, 0)`)
        ctx.beginPath(); ctx.arc(x, y, r * 3.4, 0, Math.PI * 2)
        ctx.fillStyle = glow; ctx.fill()
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${tint}, 0.95)`; ctx.fill()
        ctx.font = `${10.5 * dpr}px 'JetBrains Mono', monospace`
        ctx.fillStyle = `rgba(${tint}, 0.6)`
        ctx.textAlign = 'center'
        ctx.fillText(n.name, x, y - r - 6 * dpr)
      })
    }
    raf = requestAnimationFrame(draw)
    return () => { dead = true; cancelAnimationFrame(raf); ro?.disconnect() }
  }, [])

  if (!whales.length) return null
  return <canvas className="jv-constellation" ref={ref} aria-hidden />
}
