/* The JARVIS presence — "the core".
 *
 * A real-time WebGL fragment shader, not a sprite: a turbulent plasma
 * sphere (five-octave fbm interior with a hot pole and limb darkening),
 * a fresnel rim, and a filamented corona that flares with the actual
 * TTS amplitude. State is expressed physically, not with labels:
 *   idle       slow interior convection, faint corona breathing
 *   listening  the rim tightens and a bright sweep orbits it; a focus
 *              ring condenses just outside the surface
 *   thinking   the interior churns ~2.6x faster and the corona quickens
 *   speaking   gold ignites through the plasma and the corona throws
 *              flares that track the voice's live amplitude
 * A tilted particle ring (2D canvas overlay, depth-shaded) orbits the
 * core. On boot the core irises up from a point over ~1.6s.
 *
 * No dependencies. If WebGL is unavailable the 2D fallback renders a
 * simpler orb; prefers-reduced-motion renders a single still frame.
 */

import { useEffect, useRef } from 'react'

export type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking'

interface Props {
  state: AvatarState
  /** 0..1 speech amplitude (analyser or boundary pulse). */
  getLevel: () => number
  /** 0..1 journal-mood tint: 0 steady (cool), 1 alert (warm, restless). */
  tone?: number
  /** Increment to fire a settle shockwave — a ring detonates outward
   * from the core when real money lands. Money speaks physically. */
  pulseSeq?: number
}

/* ── shaders ─────────────────────────────────────────────────────── */

const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`

const FRAG = `
precision highp float;
uniform vec2  u_res;
uniform float u_time;
uniform float u_level;   // live speech amplitude 0..1
uniform float u_listen;  // eased state weights 0..1
uniform float u_think;
uniform float u_speak;
uniform float u_boot;    // 0..1 iris-in
uniform float u_tone;    // journal mood: 0 steady .. 1 alert
uniform float u_pulse;   // settle shockwave energy 1->0 (decaying)

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
mat2 rot(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
float fbm(vec2 p) {
  float v = 0.0, amp = 0.55;
  for (int i = 0; i < 5; i++) {
    v += amp * noise(p);
    p = rot(0.6) * p * 2.02 + 11.7;
    amp *= 0.55;
  }
  return v;
}

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / min(u_res.x, u_res.y);
  float t = u_time;
  float r = length(uv);
  float ang = atan(uv.y, uv.x);

  float boot  = smoothstep(0.0, 1.0, u_boot);
  float R     = 0.285 * boot + 0.015;
  float churn = 1.0 + u_think * 1.6 + u_speak * 0.6 + u_tone * 0.35;

  vec3 col = vec3(0.0);

  // faint interior nebula so the square never reads as a dead field
  float neb = fbm(uv * 2.2 + vec2(t * 0.008, -t * 0.005));
  col += vec3(0.012, 0.026, 0.045) * neb * 1.15 * boot;

  // AURORA DEPTH: two slow counter-drifting bands of color behind the
  // stage — teal and violet trading dominance over ~2 minutes — so the
  // room reads as a place, not a black rectangle.
  float aur1 = fbm(uv * vec2(1.3, 2.8) + vec2(t * 0.012, -t * 0.004));
  float aur2 = fbm(uv * vec2(2.1, 1.2) - vec2(t * 0.007, t * 0.009) + 31.7);
  float sway = 0.5 + 0.5 * sin(t * 0.05);
  vec3 aurA = mix(vec3(0.010, 0.055, 0.075), vec3(0.045, 0.020, 0.085), sway);
  vec3 aurB = mix(vec3(0.030, 0.015, 0.070), vec3(0.008, 0.060, 0.065), sway);
  float far = smoothstep(0.42, 0.95, r);
  col += (aurA * smoothstep(0.52, 0.95, aur1)
        + aurB * smoothstep(0.55, 0.98, aur2)) * far * boot;

  // ── plasma interior ──
  if (r < R) {
    vec2 p = uv / R;
    float z = sqrt(max(0.0, 1.0 - dot(p, p)));
    vec2 q = rot(t * 0.05 * churn) * (p * 1.6);
    float n1 = fbm(q * 2.4 + vec2(t * 0.16 * churn, t * 0.11 * churn));
    float n2 = fbm(q * 4.4 - vec2(t * 0.09 * churn, t * 0.14 * churn) + n1 * 1.8);
    float plasma = n1 * 0.65 + n2 * 0.6;

    vec3 deep = vec3(0.006, 0.04, 0.1);
    vec3 blue = vec3(0.03, 0.24, 0.58);
    vec3 cyan = vec3(0.16, 0.75, 1.0);
    vec3 hot  = vec3(0.93, 0.99, 1.0);
    // Deep body, luminous veins: heat lives only in the noise ridges
    // (and rises with the voice) — never a blown-out white ball.
    float heat = smoothstep(0.62, 1.3, plasma * 0.95 + z * 0.2
                            + u_level * 0.55 + u_speak * 0.12);
    col = mix(deep, blue, smoothstep(0.18, 0.62, plasma));
    col = mix(col, cyan, smoothstep(0.55, 0.98, plasma));
    col = mix(col, hot, heat * heat);

    // gold ignites through the convection while speaking
    float fleck = smoothstep(0.76, 0.9, fbm(q * 6.0 + vec2(t * 0.2, -t * 0.15)));
    col += vec3(0.95, 0.78, 0.38) * fleck
           * (0.1 + u_tone * 0.28 + u_speak * 0.95 + u_level * 0.85);

    // limb darkening + a specular pole up-left
    col *= 0.3 + 0.8 * z;
    float spec = pow(max(0.0, dot(normalize(vec3(p, z)),
                                  normalize(vec3(-0.5, 0.6, 0.62)))), 9.0);
    col += hot * spec * 0.35;
  }

  // ── fresnel rim, with an orbiting sweep while listening ──
  float rim = exp(-pow((r - R) * (46.0 + u_listen * 26.0), 2.0));
  float sweep = 0.6 + 0.4 * sin(ang - t * (0.7 + u_listen * 2.4));
  col += mix(vec3(0.35, 0.9, 1.0), vec3(0.95, 0.75, 0.4), u_tone * 0.4) * rim
         * (0.85 + u_listen * 1.5 * sweep + u_level * 1.3) * boot;

  // ── corona filaments ──
  if (r > R * 0.98) {
    float fil = fbm(vec2(ang * 2.6 + t * 0.12 * churn,
                         (r - R) * 5.0 - t * (0.35 + u_speak * 0.9)));
    float flame = pow(max(0.0, fil - 0.36), 1.6);
    float fall = exp(-(r - R) * (6.8 - u_level * 2.6 - u_speak * 1.2));
    col += mix(vec3(0.2, 0.75, 1.0), vec3(0.95, 0.8, 0.45), u_speak * 0.55 * fil)
           * flame * fall * (0.8 + u_level * 2.3 + u_think * 0.5) * boot;
  }

  // listening: a focus ring condenses just outside the surface
  float ring2 = exp(-pow((r - (R + 0.055 + 0.012 * sin(t * 2.2))) * 120.0, 2.0));
  col += vec3(0.5, 0.95, 1.0) * ring2 * u_listen * 0.8;

  // GOD RAYS: angular light shafts streaming off the core, alive in
  // the noise field — brighter while speaking/thinking, never static.
  float shaft = pow(max(0.0, fbm(vec2(ang * 3.2 + t * 0.045,
                                      t * 0.02)) - 0.34), 2.2);
  float reach = exp(-(r - R) * (2.6 - u_speak * 0.7)) * step(R, r);
  col += mix(vec3(0.16, 0.55, 0.85), vec3(0.75, 0.62, 0.35), u_speak * 0.5)
         * shaft * reach * (0.5 + u_think * 0.5 + u_level * 0.9) * boot;

  // SETTLE SHOCKWAVE: real money landing detonates a ring outward —
  // bright leading edge, faint chromatic trail, gone in ~3 seconds.
  if (u_pulse > 0.003) {
    float prog = 1.0 - u_pulse;
    float pr = R + prog * 0.85;
    float ring3 = exp(-pow((r - pr) * 30.0, 2.0));
    float trail = exp(-pow((r - pr + 0.05) * 22.0, 2.0)) * 0.4;
    col += (vec3(0.40, 0.95, 1.0) * ring3
          + vec3(0.85, 0.70, 0.35) * trail) * u_pulse * 1.35;
  }

  // vignette + dither (kills gradient banding)
  col *= 1.0 - smoothstep(0.55, 1.0, r) * 0.72;
  col += hash(uv * vec2(917.3, 533.7) + t) * 0.03 - 0.015;
  col = max(col, 0.0);

  float alpha = clamp(max(col.r, max(col.g, col.b)) * 1.6, 0.0, 1.0);
  gl_FragColor = vec4(col * alpha, alpha);   // premultiplied over the stage
}
`

/* ── tilted particle ring (2D overlay, depth-shaded) ─────────────── */

interface Particle {
  angle: number; speed: number; radius: number; size: number
  gold: boolean; wobble: number
}

function makeParticles(n: number): Particle[] {
  const out: Particle[] = []
  for (let i = 0; i < n; i++) {
    out.push({
      angle: (i / n) * Math.PI * 2 + Math.random() * 0.2,
      speed: 0.22 + Math.random() * 0.3,
      radius: 1.06 + Math.random() * 0.3,
      size: 0.7 + Math.random() * 1.5,
      gold: i % 8 === 0,
      wobble: Math.random() * Math.PI * 2,
    })
  }
  return out
}

function drawRing(
  ctx: CanvasRenderingContext2D, w: number, h: number, t: number,
  particles: Particle[], spinMul: number, level: number,
  clearFirst = true,
) {
  if (clearFirst) ctx.clearRect(0, 0, w, h)
  const cx = w / 2, cy = h / 2
  const R = Math.min(w, h) * 0.285
  const tiltY = 0.34                       // ring plane squash
  const planeRot = t * 0.05                // the whole plane precesses
  for (const p of particles) {
    const a = p.angle + t * p.speed * spinMul
    const rr = p.radius * R * (1 + 0.03 * Math.sin(t * 0.7 + p.wobble))
    let x = Math.cos(a) * rr
    let y = Math.sin(a) * rr * tiltY
    const depth = (Math.sin(a) + 1) / 2     // 0 far → 1 near
    const xr = x * Math.cos(planeRot) - y * Math.sin(planeRot)
    const yr = x * Math.sin(planeRot) + y * Math.cos(planeRot)
    // occluded behind the core: far-side particles inside the disc hide
    const px = cx + xr, py = cy + yr
    const insideCore = Math.hypot(xr, yr) < R * 0.96
    if (depth < 0.5 && insideCore) continue
    const s = p.size * (0.55 + depth * 0.9) * (1 + level * 0.5)
    const alpha = (0.14 + depth * 0.5) * (p.gold ? 1.0 : 0.85)
    ctx.beginPath()
    ctx.arc(px, py, s, 0, Math.PI * 2)
    ctx.fillStyle = p.gold
      ? `rgba(232, 200, 119, ${alpha})`
      : `rgba(120, 220, 255, ${alpha})`
    ctx.shadowBlur = 6 * depth
    ctx.shadowColor = p.gold ? 'rgba(232,200,119,.8)' : 'rgba(105,224,255,.8)'
    ctx.fill()
    ctx.shadowBlur = 0
  }
}

/* ── 2D fallback core (no WebGL) ─────────────────────────────────── */

function drawFallbackCore(
  ctx: CanvasRenderingContext2D, w: number, h: number, t: number,
  level: number, listen: number,
) {
  const cx = w / 2, cy = h / 2
  const R = Math.min(w, h) * 0.285 * (1 + level * 0.05)
  const g = ctx.createRadialGradient(
    cx - R * 0.35, cy - R * 0.35, R * 0.05, cx, cy, R * 1.05)
  g.addColorStop(0, 'rgba(235,250,255,.95)')
  g.addColorStop(0.35, 'rgba(105,224,255,.85)')
  g.addColorStop(0.8, 'rgba(10,40,80,.9)')
  g.addColorStop(1, 'rgba(2,10,24,0)')
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(cx, cy, R * 1.05, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = `rgba(105,224,255,${0.35 + listen * 0.5 + level * 0.4})`
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.arc(cx, cy, R * (1.12 + 0.02 * Math.sin(t * 2)), 0, Math.PI * 2)
  ctx.stroke()
}

/* ── component ───────────────────────────────────────────────────── */

export function JarvisAvatar({ state, getLevel, tone = 0, pulseSeq = 0 }: Props) {
  const glRef = useRef<HTMLCanvasElement | null>(null)
  const ringRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef<AvatarState>(state)
  const levelRef = useRef<() => number>(getLevel)
  const toneRef = useRef(tone)
  const pulseSeqRef = useRef(pulseSeq)
  stateRef.current = state
  levelRef.current = getLevel
  toneRef.current = tone
  pulseSeqRef.current = pulseSeq

  useEffect(() => {
    const glCanvas = glRef.current
    const ringCanvas = ringRef.current
    if (!glCanvas || !ringCanvas) return
    const ring2d = ringCanvas.getContext('2d')
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    const particles = makeParticles(72)

    /* WebGL setup (fallback to 2D if unavailable) */
    const gl = glCanvas.getContext('webgl', {
      alpha: true, premultipliedAlpha: true, antialias: true,
    }) as WebGLRenderingContext | null
    let program: WebGLProgram | null = null
    let uni: Record<string, WebGLUniformLocation | null> = {}
    if (gl) {
      const mk = (type: number, src: string) => {
        const s = gl.createShader(type)!
        gl.shaderSource(s, src)
        gl.compileShader(s)
        return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null
      }
      const vs = mk(gl.VERTEX_SHADER, VERT)
      const fs = mk(gl.FRAGMENT_SHADER, FRAG)
      if (vs && fs) {
        program = gl.createProgram()!
        gl.attachShader(program, vs)
        gl.attachShader(program, fs)
        gl.linkProgram(program)
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) program = null
      }
      if (program) {
        gl.useProgram(program)
        const buf = gl.createBuffer()
        gl.bindBuffer(gl.ARRAY_BUFFER, buf)
        gl.bufferData(gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
        const loc = gl.getAttribLocation(program, 'a_pos')
        gl.enableVertexAttribArray(loc)
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
        for (const n of ['u_res', 'u_time', 'u_level', 'u_listen',
                         'u_think', 'u_speak', 'u_boot', 'u_tone',
                         'u_pulse'])
          uni[n] = gl.getUniformLocation(program, n)
      }
    }

    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const rect = glCanvas.getBoundingClientRect()
      for (const c of [glCanvas, ringCanvas]) {
        c.width = Math.round(rect.width * dpr)
        c.height = Math.round(rect.height * dpr)
      }
      gl?.viewport(0, 0, glCanvas.width, glCanvas.height)
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(glCanvas)

    /* eased state weights + smoothed amplitude */
    let listen = 0, think = 0, speak = 0, level = 0
    /* choreography clocks: the moment speech began (anticipatory
     * contraction) and the moment a think/tool phase settled (flare). */
    let prevSt: AvatarState = stateRef.current
    let speakAt = -10
    let settleAt = -10
    let prevPulseSeq = pulseSeqRef.current
    let pulseAt = -10                      // settle-shockwave fire time
    const t0 = performance.now()
    let raf = 0
    let running = true

    const frame = () => {
      if (!running) return
      const t = (performance.now() - t0) / 1000
      const st = stateRef.current
      if (st !== prevSt) {
        if (st === 'speaking') speakAt = t        // breath drawn before the first word
        if (prevSt === 'thinking') settleAt = t   // a tool round just completed
        prevSt = st
      }
      if (pulseSeqRef.current !== prevPulseSeq) { // money landed: detonate
        prevPulseSeq = pulseSeqRef.current
        pulseAt = t
      }
      const pAge = t - pulseAt
      const pulse = pAge >= 0 && pAge < 3.2 ? Math.exp(-pAge * 1.5) : 0
      const ease = (v: number, target: number) => v + (target - v) * 0.08
      listen = ease(listen, st === 'listening' ? 1 : 0)
      think = ease(think, st === 'thinking' ? 1 : 0)
      speak = ease(speak, st === 'speaking' ? 1 : 0)
      const raw = Math.max(0, Math.min(1, levelRef.current()))
      level = raw > level ? level + (raw - level) * 0.5    // fast attack
                          : level + (raw - level) * 0.12   // slow decay
      const boot = Math.min(1, t / 1.6)

      /* STATE CHOREOGRAPHY — a scale envelope on the wrapper (composited
       * transform, no shader change) plus a flare riding u_level:
       *   breath   idle only: 1±0.01 on a ~5s cycle, alive but calm
       *   contract 300ms anticipatory dip as speech begins — the inhale
       *   flare    settle-pulse when a tool round completes: fast
       *            attack, eased exponential decay */
      const idleW = Math.max(0, 1 - Math.max(listen, Math.max(think, speak)))
      const breath = 0.01 * Math.sin((t * Math.PI * 2) / 5) * idleW
      const cAge = t - speakAt
      const contract = cAge >= 0 && cAge < 0.3
        ? 0.014 * Math.sin((cAge / 0.3) * Math.PI) : 0
      const fAge = t - settleAt
      const flare = fAge >= 0 && fAge < 0.9
        ? Math.min(1, fAge / 0.12) * Math.exp(-fAge * 4.2) : 0
      if (wrapRef.current) {
        wrapRef.current.style.transform =
          `scale(${(1 + breath - contract + flare * 0.012).toFixed(4)})`
      }
      const lvl = Math.min(1, level + flare * 0.3)

      if (gl && program) {
        gl.useProgram(program)
        gl.uniform2f(uni.u_res, glCanvas.width, glCanvas.height)
        gl.uniform1f(uni.u_time, t)
        gl.uniform1f(uni.u_level, lvl)
        gl.uniform1f(uni.u_listen, listen)
        gl.uniform1f(uni.u_think, think)
        gl.uniform1f(uni.u_speak, speak)
        gl.uniform1f(uni.u_boot, boot)
        gl.uniform1f(uni.u_tone, Math.max(0, Math.min(1, toneRef.current)))
        gl.uniform1f(uni.u_pulse, pulse)
        gl.clearColor(0, 0, 0, 0)
        gl.clear(gl.COLOR_BUFFER_BIT)
        gl.drawArrays(gl.TRIANGLES, 0, 3)
        if (ring2d)
          drawRing(ring2d, ringCanvas.width, ringCanvas.height, t,
                   particles, 1 + think * 2.2 + speak * 0.6, lvl)
      } else if (ring2d) {
        // no WebGL: clear once, 2D core as base layer, ring on top
        ring2d.clearRect(0, 0, ringCanvas.width, ringCanvas.height)
        drawFallbackCore(ring2d, ringCanvas.width, ringCanvas.height,
                         t, lvl, listen)
        drawRing(ring2d, ringCanvas.width, ringCanvas.height, t,
                 particles, 1, lvl, false)
      }

      if (reduced.matches && t > 1.7) return   // settle into a still frame
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      running = false
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [])

  return (
    <div className="jv-core" ref={wrapRef}>
      <canvas ref={glRef} className="jv-core-gl" aria-hidden />
      <canvas ref={ringRef} className="jv-core-ring" aria-hidden />
    </div>
  )
}
