/* The JARVIS avatar: an abstract luminous orb on canvas.
 *
 * Slow-breathing gradient sphere + an orbiting particle ring (cyan with gold
 * accents). While speaking it ripples with the TTS amplitude fed in through
 * getLevel(); while listening it shows a soft outer ring; while thinking the
 * ring spins faster and shimmers. Runs its own rAF loop at 60fps, honors
 * prefers-reduced-motion by rendering a static frame instead.
 */

import { useEffect, useRef } from 'react'

export type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking'

interface Props {
  state: AvatarState
  /** 0..1 speech amplitude (analyser or boundary pulse). */
  getLevel: () => number
}

interface Particle { angle: number; speed: number; radius: number; size: number; gold: boolean; drift: number }

const CYAN = { r: 105, g: 224, b: 255 }
const GOLD = { r: 232, g: 200, b: 119 }

function makeParticles(n: number): Particle[] {
  const out: Particle[] = []
  for (let i = 0; i < n; i++) {
    out.push({
      angle: (i / n) * Math.PI * 2 + Math.random() * 0.12,
      speed: 0.25 + Math.random() * 0.35,
      radius: 0.92 + Math.random() * 0.22,
      size: 0.8 + Math.random() * 1.6,
      gold: i % 9 === 0,
      drift: Math.random() * Math.PI * 2,
    })
  }
  return out
}

export function JarvisAvatar({ state, getLevel }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stateRef = useRef<AvatarState>(state)
  const levelRef = useRef<() => number>(getLevel)
  stateRef.current = state
  levelRef.current = getLevel

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const particles = makeParticles(84)
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    let raf = 0
    let running = true
    // Smoothed values so state changes glide instead of snapping.
    let level = 0
    let listenGlow = 0
    let thinkSpin = 0
    let last = performance.now()

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      const t = now / 1000
      const st = stateRef.current
      const anim = !reduced.matches

      const rect = canvas.getBoundingClientRect()
      const w = rect.width
      const h = rect.height
      const cx = w / 2
      const cy = h / 2
      const R = Math.min(w, h) * 0.26 // core radius

      // smooth inputs
      const targetLevel = st === 'speaking' ? levelRef.current() : 0
      level += (targetLevel - level) * (anim ? 0.35 : 1)
      listenGlow += ((st === 'listening' ? 1 : 0) - listenGlow) * 0.12
      thinkSpin += ((st === 'thinking' ? 1 : 0) - thinkSpin) * 0.08

      ctx.clearRect(0, 0, w, h)

      const breathe = anim ? 1 + 0.028 * Math.sin(t * 0.9) : 1
      const coreR = R * breathe * (1 + level * 0.14)

      // ambient halo
      const halo = ctx.createRadialGradient(cx, cy, coreR * 0.2, cx, cy, coreR * 3.1)
      halo.addColorStop(0, `rgba(${CYAN.r},${CYAN.g},${CYAN.b},${0.13 + level * 0.1 + thinkSpin * 0.04})`)
      halo.addColorStop(0.55, `rgba(${CYAN.r},${CYAN.g},${CYAN.b},0.035)`)
      halo.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = halo
      ctx.fillRect(0, 0, w, h)

      // speech ripples: expanding rings whose brightness rides the amplitude
      if ((st === 'speaking' || level > 0.02) && anim) {
        for (let i = 0; i < 3; i++) {
          const phase = ((t * 0.55 + i / 3) % 1)
          const rr = coreR * (1.15 + phase * 1.9)
          const alpha = (1 - phase) * 0.28 * (0.25 + level)
          ctx.beginPath()
          ctx.arc(cx, cy, rr, 0, Math.PI * 2)
          ctx.strokeStyle = `rgba(${CYAN.r},${CYAN.g},${CYAN.b},${alpha.toFixed(3)})`
          ctx.lineWidth = 1.4
          ctx.stroke()
        }
      }

      // listening ring: steady soft ring + slow sweep arc
      if (listenGlow > 0.02) {
        const lr = coreR * 1.55
        ctx.beginPath()
        ctx.arc(cx, cy, lr, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${CYAN.r},${CYAN.g},${CYAN.b},${(0.22 * listenGlow).toFixed(3)})`
        ctx.lineWidth = 1.2
        ctx.stroke()
        if (anim) {
          const sweep = t * 1.6
          ctx.beginPath()
          ctx.arc(cx, cy, lr, sweep, sweep + Math.PI * 0.45)
          ctx.strokeStyle = `rgba(${GOLD.r},${GOLD.g},${GOLD.b},${(0.55 * listenGlow).toFixed(3)})`
          ctx.lineWidth = 2
          ctx.lineCap = 'round'
          ctx.stroke()
        }
      }

      // orbiting particle ring (tilted ellipse)
      const tilt = 0.42
      const orbitR = coreR * 1.62
      const speedMul = 1 + thinkSpin * 2.6 + level * 1.4
      for (const p of particles) {
        if (anim) p.angle += p.speed * speedMul * dt
        const wob = anim ? Math.sin(t * 1.3 + p.drift) * 0.05 : 0
        const pr = orbitR * (p.radius + wob + level * 0.06)
        const x = cx + Math.cos(p.angle) * pr
        const y = cy + Math.sin(p.angle) * pr * tilt
        const behind = Math.sin(p.angle) < 0
        const c = p.gold ? GOLD : CYAN
        const alpha = (behind ? 0.28 : 0.75) * (0.5 + 0.5 * Math.abs(Math.cos(p.angle))) * (0.6 + thinkSpin * 0.4 + level * 0.4)
        if (behind) {
          ctx.beginPath()
          ctx.arc(x, y, p.size, 0, Math.PI * 2)
          ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${alpha.toFixed(3)})`
          ctx.fill()
        }
      }

      // the core orb
      const grad = ctx.createRadialGradient(
        cx - coreR * 0.28, cy - coreR * 0.32, coreR * 0.08,
        cx, cy, coreR,
      )
      grad.addColorStop(0, 'rgba(235, 252, 255, 0.95)')
      grad.addColorStop(0.22, `rgba(${CYAN.r},${CYAN.g},${CYAN.b},0.82)`)
      grad.addColorStop(0.62, 'rgba(24, 108, 148, 0.55)')
      grad.addColorStop(1, 'rgba(6, 18, 30, 0.05)')
      ctx.beginPath()
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2)
      ctx.fillStyle = grad
      ctx.fill()

      // inner shimmer while thinking
      if (thinkSpin > 0.02 && anim) {
        for (let i = 0; i < 3; i++) {
          const a = t * (2.2 + i * 0.6) + (i * Math.PI * 2) / 3
          ctx.beginPath()
          ctx.ellipse(cx, cy, coreR * 0.82, coreR * (0.3 + i * 0.16), a, 0, Math.PI * 2)
          ctx.strokeStyle = `rgba(${GOLD.r},${GOLD.g},${GOLD.b},${(0.16 * thinkSpin).toFixed(3)})`
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }

      // rim light
      ctx.beginPath()
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(${CYAN.r},${CYAN.g},${CYAN.b},${(0.35 + level * 0.4).toFixed(3)})`
      ctx.lineWidth = 1.1
      ctx.stroke()

      // foreground particles (in front of the orb)
      for (const p of particles) {
        if (Math.sin(p.angle) < 0) continue
        const wob = anim ? Math.sin(t * 1.3 + p.drift) * 0.05 : 0
        const pr = orbitR * (p.radius + wob + level * 0.06)
        const x = cx + Math.cos(p.angle) * pr
        const y = cy + Math.sin(p.angle) * pr * tilt
        const c = p.gold ? GOLD : CYAN
        const alpha = 0.8 * (0.5 + 0.5 * Math.abs(Math.cos(p.angle))) * (0.65 + thinkSpin * 0.35 + level * 0.35)
        ctx.beginPath()
        ctx.arc(x, y, p.size, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${alpha.toFixed(3)})`
        ctx.fill()
      }
    }

    const loop = (now: number) => {
      if (!running) return
      draw(now)
      if (reduced.matches) {
        // static-ish: repaint at 2fps so state changes still show
        setTimeout(() => { if (running) raf = requestAnimationFrame(loop) }, 500)
      } else {
        raf = requestAnimationFrame(loop)
      }
    }
    raf = requestAnimationFrame(loop)

    return () => {
      running = false
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [])

  return <canvas ref={canvasRef} className="jv-avatar" aria-hidden />
}
