/* Stage furniture around the core: a parallax starfield with occasional
 * meteors, the rotating HUD ring, a boot sequence, and the telemetry
 * ribbon that breathes live platform numbers through the room.
 * All dependency-free; starfield honors prefers-reduced-motion. */

import { useEffect, useRef, useState } from 'react'

/* ── starfield ───────────────────────────────────────────────────── */

interface Star { x: number; y: number; z: number; tw: number }
interface Meteor { x: number; y: number; vx: number; vy: number; life: number }

export function Starfield() {
  const ref = useRef<HTMLCanvasElement | null>(null)

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

    const frame = () => {
      if (!running) return
      const now = performance.now()
      const t = (now - t0) / 1000
      const w = canvas.width, h = canvas.height
      ctx.clearRect(0, 0, w, h)
      for (const s of stars) {
        s.x -= s.z * 0.06                        // slow drift
        if (s.x < -2) { s.x = w + 2; s.y = Math.random() * h }
        const a = 0.25 + 0.5 * s.z + 0.22 * Math.sin(t * (0.6 + s.z) + s.tw)
        ctx.fillStyle = `rgba(160, 215, 255, ${Math.max(0.05, a * 0.5)})`
        const r = s.z * 1.25
        ctx.fillRect(s.x, s.y, r, r)
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
