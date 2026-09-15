import { useEffect, useRef } from 'react'

/** The P&L landscape as a holographic isometric terrain.
 *
 * Each day is an extruded bar on a glowing grid floor rendered in
 * true isometric projection — green rises, red sinks below the
 * plane — with a slow breathing camera sway, a scanline sweep that
 * plays across the field, and glow pooled under every bar. Pure
 * canvas, zero deps, DPR-aware; one static frame under
 * prefers-reduced-motion. Numbers are never invented: the terrain
 * IS the daily ledger.
 */
export function HoloTerrain({ days, height = 240 }: {
  days: { date: string; pnl: number }[]
  height?: number
}) {
  const ref = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas || days.length < 2) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    let w = 0
    const fit = () => {
      const r = canvas.parentElement?.getBoundingClientRect()
      w = Math.max(320, r?.width ?? 800)
      canvas.width = w * dpr
      canvas.height = height * dpr
      canvas.style.width = `${w}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    fit()
    const ro = new ResizeObserver(fit)
    if (canvas.parentElement) ro.observe(canvas.parentElement)

    const maxAbs = Math.max(0.01, ...days.map((d) => Math.abs(d.pnl)))

    const draw = (t: number) => {
      ctx.clearRect(0, 0, w, height)
      const sway = reduced ? 0 : Math.sin(t / 3800) * 0.06
      const n = days.length
      const originX = w * 0.5
      const originY = height * 0.62
      const span = Math.min(w * 0.86, n * 34)
      const stepX = span / n
      const ix = Math.cos(0.523 + sway)      // ~30° isometric axes
      const iy = Math.sin(0.523 + sway) * 0.55
      const H = height * 0.30                // max bar height

      // the floor: receding grid lines with depth fade
      ctx.lineWidth = 1
      for (let g = -3; g <= 3; g++) {
        ctx.strokeStyle = `rgba(0, 102, 255, ${0.16 - Math.abs(g) * 0.035})`
        ctx.beginPath()
        ctx.moveTo(originX - span / 2 * ix + g * 26, originY + span / 2 * iy + g * 10)
        ctx.lineTo(originX + span / 2 * ix + g * 26, originY - span / 2 * iy + g * 10)
        ctx.stroke()
      }

      // scanline position (sweeps the field every ~5s)
      const scan = reduced ? -1 : ((t / 5200) % 1) * n

      for (let i = 0; i < n; i++) {
        const d = days[i]
        const fx = i - n / 2
        const x = originX + fx * stepX * ix
        const y = originY - fx * stepX * iy
        const hgt = (Math.abs(d.pnl) / maxAbs) * H
        const up = d.pnl >= 0
        const col = up ? '52, 209, 123' : '255, 92, 92'
        const near = scan >= 0 ? Math.abs(i - scan) : 9
        const boost = near < 2 ? (2 - near) * 0.45 : 0

        // glow pool on the floor
        const pool = ctx.createRadialGradient(x, y, 0, x, y, stepX * 1.4)
        pool.addColorStop(0, `rgba(${col}, ${0.20 + boost * 0.3})`)
        pool.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.fillStyle = pool
        ctx.beginPath()
        ctx.ellipse(x, y, stepX * 1.4, stepX * 0.5, 0, 0, Math.PI * 2)
        ctx.fill()

        // the extruded bar: front face + top lozenge, holographic
        const bw = Math.max(3, stepX * 0.42)
        const top = up ? y - hgt : y + hgt
        const grad = ctx.createLinearGradient(0, Math.min(y, top), 0, Math.max(y, top))
        grad.addColorStop(0, `rgba(${col}, ${0.85 + boost})`)
        grad.addColorStop(1, `rgba(${col}, 0.10)`)
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.roundRect(x - bw / 2, Math.min(y, top), bw, Math.abs(y - top) || 1, 2)
        ctx.fill()
        ctx.fillStyle = `rgba(${col}, ${Math.min(1, 0.9 + boost)})`
        ctx.beginPath()
        ctx.ellipse(x, top, bw * 0.72, bw * 0.30, 0, 0, Math.PI * 2)
        ctx.fill()
      }

      // hologram frame text
      ctx.font = '600 9px "IBM Plex Mono", monospace'
      ctx.fillStyle = 'rgba(107, 118, 113, 0.9)'
      ctx.fillText(days[0].date.slice(5), 10, height - 8)
      const last = days[n - 1].date.slice(5)
      ctx.fillText(last, w - ctx.measureText(last).width - 10, height - 8)
    }

    if (reduced) {
      draw(0)
      return () => ro.disconnect()
    }
    let raf = 0
    const loop = (t: number) => {
      if (!document.hidden) draw(t)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [days, height])

  if (days.length < 2) return null
  return <canvas className="nd-terrain" ref={ref} aria-hidden />
}
