import { useEffect, useRef } from 'react'
import { subscribe } from '../lib/sse'

/** The brand's hexagonal lattice as a living instrument surface.
 *
 * A canvas of hex cells in brand blue drifts almost imperceptibly;
 * every REAL whale detection off the wire strikes a ripple through
 * the lattice — the room visibly reacts to the machine seeing a
 * trade. Pointer moves add a soft parallax. Pauses when the tab is
 * hidden; renders one static frame under prefers-reduced-motion.
 * Pure decoration layer: pointer-events none, data never occluded.
 */
export function HexField({ height = 260 }: { height?: number }) {
  const ref = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    let w = 0
    let h = 0
    const fit = () => {
      const r = canvas.parentElement?.getBoundingClientRect()
      w = Math.max(300, r?.width ?? 800)
      h = height
      canvas.width = w * dpr
      canvas.height = h * dpr
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    fit()
    const ro = new ResizeObserver(fit)
    if (canvas.parentElement) ro.observe(canvas.parentElement)

    const R = 26                       // hex radius
    const ripples: { x: number; y: number; t0: number }[] = []
    let px = 0.5
    let py = 0.35
    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      px = (e.clientX - r.left) / Math.max(1, r.width)
      py = (e.clientY - r.top) / Math.max(1, r.height)
    }
    canvas.parentElement?.addEventListener('pointermove', onMove)

    const offTrade = subscribe((_t, fresh) => {
      if (!fresh) return
      ripples.push({
        x: (0.15 + Math.abs(Math.sin(performance.now() / 700)) * 0.7) * w,
        y: (0.2 + Math.abs(Math.cos(performance.now() / 900)) * 0.6) * h,
        t0: performance.now(),
      })
      if (ripples.length > 6) ripples.shift()
    })

    const hex = (cx: number, cy: number, r: number) => {
      ctx.beginPath()
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i + Math.PI / 6
        const x = cx + r * Math.cos(a)
        const y = cy + r * Math.sin(a)
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
    }

    const draw = (now: number) => {
      ctx.clearRect(0, 0, w, h)
      const drift = reduced ? 0 : (now / 12000) % (R * 3)
      const ox = (px - 0.5) * 14 - drift * 0.4
      const oy = (py - 0.5) * 8
      const stepX = R * 1.732
      const stepY = R * 1.5
      for (let row = -1; row * stepY < h + R; row++) {
        for (let col = -2; col * stepX < w + R * 2; col++) {
          const cx = col * stepX + (row % 2 ? stepX / 2 : 0) + ox + drift
          const cy = row * stepY + oy
          // base lattice: faintest at center-bottom, stronger at top
          const fade = 0.02 + 0.09 * (1 - cy / h)
          let alpha = Math.max(0, fade)
          let width = 1
          for (const rp of ripples) {
            const age = (now - rp.t0) / 1400
            if (age > 1) continue
            const d = Math.hypot(cx - rp.x, cy - rp.y)
            const ring = Math.abs(d - age * 340)
            if (ring < 44) {
              const k = (1 - ring / 44) * (1 - age)
              alpha += k * 0.5
              width = Math.max(width, 1 + k * 0.8)
            }
          }
          ctx.strokeStyle = `rgba(0, 102, 255, ${Math.min(0.6, alpha)})`
          ctx.lineWidth = width
          hex(cx, cy, R - 3)
          ctx.stroke()
        }
      }
      // prune dead ripples
      for (let i = ripples.length - 1; i >= 0; i--) {
        if (now - ripples[i].t0 > 1400) ripples.splice(i, 1)
      }
    }

    if (reduced) {
      draw(0)
      return () => {
        ro.disconnect()
        offTrade()
        canvas.parentElement?.removeEventListener('pointermove', onMove)
      }
    }
    let raf = 0
    const loop = (now: number) => {
      if (!document.hidden) draw(now)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      offTrade()
      canvas.parentElement?.removeEventListener('pointermove', onMove)
    }
  }, [height])

  return <canvas className="nd-hexfield" ref={ref} aria-hidden />
}
