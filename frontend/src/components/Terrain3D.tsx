import { useEffect, useRef } from 'react'
import * as THREE from 'three'

/* TRUE-3D P&L TERRAIN (owner order 2026-08-29): the daily ledger as an
 * orbitable WebGL cityscape — one glowing column per settled day,
 * green rising above the ground plane, red sinking below it, laid out
 * as a week-grid calendar. Drag orbits, wheel/pinch zooms, and it
 * slow-rotates on its own until touched. This module (and three.js
 * with it) loads ONLY when the 3D toggle is flipped — the lazy import
 * keeps the main bundle exactly as heavy as before. Reduced-motion
 * disables the idle rotation; the columns still render. */

export type TerrainDay = { date: string; pnl: number }

const POS = new THREE.Color('#34d17b')
const NEG = new THREE.Color('#ff5c5c')
const GRID = new THREE.Color('#1a3550')

export default function Terrain3D({ days, height = 300 }: {
  days: TerrainDay[]
  height?: number
}) {
  const mountRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !days.length) return
    let dead = false
    const still = (() => {
      try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches }
      catch { return false }
    })()

    const scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(0x050607, 0.016)
    const w = mount.clientWidth || 600
    const camera = new THREE.PerspectiveCamera(42, w / height, 0.1, 400)
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, height)
    mount.appendChild(renderer.domElement)

    // Calendar layout: columns = weeks, rows = weekday. Height maps
    // |pnl| through a sqrt so one monster day cannot flatten the rest.
    const maxAbs = Math.max(...days.map((d) => Math.abs(d.pnl)), 1)
    const weeks = Math.ceil(days.length / 7)
    const unit = 1.15
    const group = new THREE.Group()
    const cols: { mesh: THREE.Mesh; h: number }[] = []
    days.forEach((d, i) => {
      const week = Math.floor(i / 7)
      const dow = i % 7
      const h = Math.max(0.08, Math.sqrt(Math.abs(d.pnl) / maxAbs) * 7)
      const up = d.pnl >= 0
      const geo = new THREE.BoxGeometry(0.72, h, 0.72)
      const col = up ? POS : NEG
      const mat = new THREE.MeshStandardMaterial({
        color: col, emissive: col, emissiveIntensity: 0.35,
        transparent: true, opacity: 0.92, roughness: 0.35, metalness: 0.15,
      })
      const mesh = new THREE.Mesh(geo, mat)
      mesh.position.set(
        (week - weeks / 2) * unit,
        up ? h / 2 : -h / 2,
        (dow - 3) * unit)
      group.add(mesh)
      cols.push({ mesh, h })
      // glow cap on real green days — the fake bloom
      if (up && Math.abs(d.pnl) > maxAbs * 0.35) {
        const cap = new THREE.Sprite(new THREE.SpriteMaterial({
          map: glowTexture(), color: col, transparent: true,
          opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
        }))
        cap.scale.setScalar(2.2)
        cap.position.set(mesh.position.x, h + 0.4, mesh.position.z)
        group.add(cap)
      }
    })
    scene.add(group)

    const grid = new THREE.GridHelper(Math.max(weeks * unit + 6, 16), 24, GRID, GRID)
    ;(grid.material as THREE.Material).transparent = true
    ;(grid.material as THREE.Material).opacity = 0.35
    scene.add(grid)

    scene.add(new THREE.AmbientLight(0x8899aa, 0.7))
    const key = new THREE.DirectionalLight(0xcfe8ff, 1.1)
    key.position.set(6, 14, 8)
    scene.add(key)

    // Orbit: hand-rolled (drag = yaw/pitch, wheel = dolly) — the whole
    // examples controls module is not worth its bytes for one gesture.
    let yaw = 0.7, pitch = 0.48, dist = Math.max(weeks * 2.1, 24)
    let dragging = false, px = 0, py = 0, touched = still
    const apply = () => {
      camera.position.set(
        Math.sin(yaw) * Math.cos(pitch) * dist,
        Math.sin(pitch) * dist,
        Math.cos(yaw) * Math.cos(pitch) * dist)
      camera.lookAt(0, 0.5, 0)
    }
    const down = (e: PointerEvent) => {
      dragging = true; touched = true; px = e.clientX; py = e.clientY
      mount.setPointerCapture?.(e.pointerId)
    }
    const move = (e: PointerEvent) => {
      if (!dragging) return
      yaw -= (e.clientX - px) * 0.006
      pitch = Math.min(1.35, Math.max(0.08, pitch + (e.clientY - py) * 0.005))
      px = e.clientX; py = e.clientY
    }
    const up = () => { dragging = false }
    const wheel = (e: WheelEvent) => {
      e.preventDefault(); touched = true
      dist = Math.min(80, Math.max(8, dist + e.deltaY * 0.03))
    }
    mount.addEventListener('pointerdown', down)
    mount.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    mount.addEventListener('wheel', wheel, { passive: false })

    let raf = 0
    const tick = () => {
      if (dead) return
      if (!touched) yaw += 0.0022 // idle rotate until first touch
      apply()
      renderer.render(scene, camera)
      raf = requestAnimationFrame(tick)
    }
    tick()

    const ro = new ResizeObserver(() => {
      const nw = mount.clientWidth || 600
      camera.aspect = nw / height
      camera.updateProjectionMatrix()
      renderer.setSize(nw, height)
    })
    ro.observe(mount)

    return () => {
      dead = true
      cancelAnimationFrame(raf)
      ro.disconnect()
      mount.removeEventListener('pointerdown', down)
      mount.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      mount.removeEventListener('wheel', wheel)
      scene.traverse((o) => {
        const m = o as THREE.Mesh
        if (m.geometry) m.geometry.dispose()
        const mat = m.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
        else mat?.dispose()
      })
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [days, height])

  return (
    <div ref={mountRef} style={{ width: '100%', height, cursor: 'grab',
      touchAction: 'none' }}
      aria-label="3D P&L terrain — drag to orbit, scroll to zoom" role="img" />
  )
}

let _glow: THREE.Texture | null = null
function glowTexture(): THREE.Texture {
  if (_glow) return _glow
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  grad.addColorStop(0, 'rgba(255,255,255,0.9)')
  grad.addColorStop(0.4, 'rgba(255,255,255,0.25)')
  grad.addColorStop(1, 'rgba(255,255,255,0)')
  g.fillStyle = grad
  g.fillRect(0, 0, 64, 64)
  _glow = new THREE.CanvasTexture(c)
  return _glow
}
