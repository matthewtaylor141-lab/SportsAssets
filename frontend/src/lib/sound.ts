import { useCallback, useEffect, useState } from 'react'

/* Sound design (owner order 2026-08-29): synthesized, not sampled — a
 * sonar blip per whale detection and a soft ping per fill, built on a
 * lazily-created AudioContext so nothing loads or plays until the user
 * arms it. DEFAULT OFF, one tap to arm, persisted; browsers block
 * autoplay anyway, so arming from a click is also what makes the
 * context legally resumable. Volume is deliberately low: this is
 * ambience for a desk, not a slot machine. */

const KEY = 'sa_sound'
let ctx: AudioContext | null = null
let lastAt = 0

function ac(): AudioContext | null {
  try {
    if (!ctx) {
      const AC = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AC) return null
      ctx = new AC()
    }
    if (ctx.state === 'suspended') void ctx.resume()
    return ctx
  } catch { return null }
}

export function soundArmed(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

function throttle(): boolean {
  // A burst of 20 detections must not machine-gun the speaker.
  const now = Date.now()
  if (now - lastAt < 180) return true
  lastAt = now
  return false
}

function tone(freq: number, dur: number, type: OscillatorType,
              gain: number, sweepTo?: number) {
  const c = ac()
  if (!c) return
  const o = c.createOscillator()
  const g = c.createGain()
  o.type = type
  o.frequency.setValueAtTime(freq, c.currentTime)
  if (sweepTo) o.frequency.exponentialRampToValueAtTime(sweepTo, c.currentTime + dur)
  g.gain.setValueAtTime(gain, c.currentTime)
  g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur)
  o.connect(g).connect(c.destination)
  o.start()
  o.stop(c.currentTime + dur)
}

/** Whale detection: a sonar blip — high ping with a falling tail. */
export function sonar() {
  if (!soundArmed() || throttle()) return
  tone(1160, 0.28, 'sine', 0.045, 620)
  setTimeout(() => tone(1160, 0.18, 'sine', 0.014, 620), 210) // echo
}

/** Our copy filled: a warm two-note confirmation. */
export function fillPing() {
  if (!soundArmed() || throttle()) return
  tone(523.25, 0.12, 'triangle', 0.05)
  setTimeout(() => tone(783.99, 0.22, 'triangle', 0.05), 110)
}

/** Rejection / error: single low knock, quieter than the good news. */
export function knock() {
  if (!soundArmed() || throttle()) return
  tone(196, 0.16, 'square', 0.02)
}

export function useSound() {
  const [armed, setArmed] = useState(soundArmed)
  useEffect(() => {
    const sync = (e: StorageEvent) => {
      if (e.key === KEY) setArmed(soundArmed())
    }
    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])
  const toggle = useCallback(() => {
    const next = !soundArmed()
    try { localStorage.setItem(KEY, next ? '1' : '0') } catch { /* pref only */ }
    setArmed(next)
    if (next) { ac(); fillPing() } // audible proof it armed, from the tap
  }, [])
  return { armed, toggle }
}
