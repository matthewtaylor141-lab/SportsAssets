/* BETTOR HEADQUARTERS · THE 3D COMMAND CENTER (three.js r185, vendored).
 *
 * The primary experience of COMMAND: one cinematic headquarters in which the
 * eight desks (seven agents and Adriana's Arbitrage Desk) stand in a horseshoe
 * around the capital core, under the BETTOR wall, with the live opportunity
 * wall, the capital wall and the risk / status wall behind. Ported from the
 * cinematic floor (claude/ui-floor-int 545e4a9) onto the accepted production
 * foundation, with the camera closer, the room lit brighter, Adriana's desk and
 * one camera move per COMMAND section (hq.js drives it).
 *
 * Every visible behaviour maps to a recorded fact (hq-model.js):
 *   desk light, zone edge, sign, monitors   GET /api/command/floor agents[]
 *                                           (work_state / state, detail, monitor,
 *                                           last_output, heartbeat)
 *   avatar posture                          the same desk state: an active state
 *                                           leans in to the keyboard; waiting
 *                                           leans back; no task sits upright;
 *                                           stale sits still; offline stands away
 *   light paths between desks               floor edges[] (real hand-offs,
 *                                           challenges, review steps); the newer
 *                                           the row, the brighter
 *   capital core                            /equity/live paper and
 *                                           small_live_bettor, side by side, never
 *                                           summed; a pulse only when the server's
 *                                           seq says the content genuinely changed
 *   opportunity wall                        floor feed[] + opportunities[]
 *   capital wall, ticker ring, markets      equity/live paper (real marks)
 *   risk / status wall                      release, equity, coverage, floor
 *   Adriana's desk                          NOT DEPLOYED · UNVERIFIED until the
 *                                           floor API serves an ADRIANA seat; dark,
 *                                           still, no avatar, no figure
 *
 * Characters: the licensed Microsoft Rocketbox models in the repo
 * (team-demo/assets/models, MIT). EVERY agent wears its OWN model; no seat
 * reuses or recolours another agent's body (CAST). There is no random number
 * anywhere in this file: decor textures use a fixed seed, idle motion is the
 * avatar controller's seeded blink and breath, and work motion runs only while
 * the desk's recorded state is an active one.
 * prefers-reduced-motion: no drift, no scrolling, no flowing light, instant
 * camera cuts; the scene renders only when data or the camera changes. */
import * as THREE from './team-demo/assets/three.module.min.js';
import {mergeGeometries} from './team-demo/assets/BufferGeometryUtils.js';
import {AvatarController, resolveBones, resolveBlendshapes, resolveVisemes, buildJoints, armsDown} from './team-demo/assets/cc_avatar.js';
import * as S from './hq-screens.js';

// BettorToken brand images (frontend/public/command/brand/) for canvas
// textures: local files only; a texture keeps its plain state if one fails.
function brandImage(src, draw) {
  const im = new Image(); im.decoding = 'async';
  im.onload = () => { try { draw(im); } catch (e) { /* keep the plain texture */ } };
  im.src = src;
}


const MODELS = new URL('./team-demo/assets/models/', import.meta.url).href;
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ease = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const damp = (a, b, k, dt) => a + (b - a) * (1 - Math.exp(-k * dt));

/* THE FLOOR PLAN. Angle from north (the BETTOR wall), clockwise; the eight
 * desks form a horseshoe that opens toward the establishing camera. */
const SEAT_ANGLE = {derek: -126, karen: -90, scout: -54, allocator: -18, adriana: 18, eddie: 54, audrey: 90, xavier: 126};
const R_DESK = 9.3;
const DESK_TOP = 0.74;
const SEAT_TOP = 0.47;
const LAYER_NO_REFLECT = 1;

export const CREDITS = {
  derek: 'Rocketbox Business_Male_03 (MIT, © 2020 Microsoft)',
  xavier: 'Rocketbox Business_Male_05 (MIT, © 2020 Microsoft)',
  audrey: 'Rocketbox Business_Female_04 (MIT, © 2020 Microsoft)',
  karen: 'Rocketbox Business_Female_02 (MIT, © 2020 Microsoft)',
  allocator: 'Rocketbox Business_Female_03 (MIT, © 2020 Microsoft)',
  eddie: 'Rocketbox Business_Male_04, with a headset prop (MIT, © 2020 Microsoft)',
  scout: 'Rocketbox Business_Male_06 (MIT, © 2020 Microsoft)',
  adriana: 'Rocketbox Business_Female_01 (MIT, © 2020 Microsoft)'
};
// which licensed model each seat wears: its OWN file, never another agent's
// body, never a tint (models/manifest.json entries, when present, win)
export const CAST = {
  derek: {model: 'derek', tint: null}, xavier: {model: 'xavier', tint: null}, audrey: {model: 'audrey', tint: null},
  karen: {model: 'karen', tint: null}, allocator: {model: 'allie', tint: null}, eddie: {model: 'eddie', tint: null},
  scout: {model: 'scout', tint: null}, adriana: {model: 'adriana', tint: null}
};

export function webglAvailable() {
  try { const c = document.createElement('canvas'); return !!(window.WebGL2RenderingContext && c.getContext('webgl2')) || !!(window.WebGLRenderingContext && c.getContext('webgl')); } catch (e) { return false; }
}

export async function createHQ(host, opts) {
  const o = Object.assign({phone: false, reducedMotion: false, model: null, view: 'command', onPick: null, onHover: null, onFrame: null, onStatus: null}, opts || {});
  const stage = host;
  const phone = !!o.phone;
  const reduced = !!o.reducedMotion;
  const HQ = o.model;
  // loading progress for the seven portraits (the room itself is drawn at once)
  const prog = document.createElement('div'); prog.className = 'fl-av-progress'; prog.setAttribute('role', 'status');
  prog.style.cssText = 'position:absolute;left:50%;top:14px;transform:translateX(-50%);z-index:5;display:flex;align-items:center;gap:10px;padding:7px 12px;border-radius:999px;background:rgba(4,12,25,.78);border:1px solid rgba(151,180,214,.18);backdrop-filter:blur(10px);font:600 10px/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:#9fb1c4;pointer-events:none;transition:opacity .8s';
  prog.innerHTML = '<b style="display:block;width:120px;height:3px;border-radius:3px;background:rgba(150,180,220,.18);overflow:hidden"><i style="display:block;height:100%;transform-origin:left;transform:scaleX(.02);background:linear-gradient(90deg,#3157ff,#8fb0ff);transition:transform .4s"></i></b><span>Opening the headquarters…</span>'; host.appendChild(prog);
  HQ.progress = (frac, label) => {
    prog.querySelector('i').style.transform = 'scaleX(' + Math.max(0.02, Math.min(1, frac)) + ')';
    if (label) prog.querySelector('span').textContent = label;
    if (frac >= 1) { prog.style.opacity = '0'; setTimeout(() => { prog.hidden = true; }, 900); }
  };
  const TEX = phone ? 0.5 : 1;
  HQ.progress(0.04, 'Opening the headquarters…');

  /* ── renderer ──────────────────────────────────────────────────── */
  const renderer = new THREE.WebGLRenderer({antialias: phone || !(window.WebGL2RenderingContext), powerPreference: 'high-performance', alpha: false});
  const HIGH = !phone && renderer.capabilities.isWebGL2;
  let dpr = Math.min(window.devicePixelRatio || 1, phone ? 1.5 : 1.75);
  renderer.setPixelRatio(dpr);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.22;
  renderer.shadowMap.enabled = HIGH;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  const canvas = renderer.domElement;
  canvas.className = 'hq-canvas';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Three-dimensional BETTOR headquarters: eight desks around the capital core under the BETTOR wall, with the opportunity, capital and risk walls. The HUD gives the same information as text.');
  host.insertBefore(canvas, host.firstChild);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#081325');
  scene.fog = new THREE.FogExp2('#0a1628', phone ? 0.011 : 0.0085);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 220);
  camera.layers.enable(LAYER_NO_REFLECT);

  /* ── materials ─────────────────────────────────────────────────── */
  const std = (o) => new THREE.MeshStandardMaterial(o);
  const MAT = {
    arch: std({color: '#1b2638', roughness: 0.78, metalness: 0.12}),
    panel: std({color: '#152034', roughness: 0.46, metalness: 0.38}),
    alu: std({color: '#9aa7b6', roughness: 0.3, metalness: 1}),
    darkMetal: std({color: '#1b222b', roughness: 0.35, metalness: 0.9}),
    brass: std({color: '#c7a467', roughness: 0.26, metalness: 1}),
    bezel: std({color: '#07090c', roughness: 0.22, metalness: 0.4}),
    deskTop: std({color: '#14181d', roughness: 0.38, metalness: 0.08}),
    walnut: std({color: '#2b1d14', roughness: 0.42, metalness: 0.05}),
    leather: std({color: '#101215', roughness: 0.5, metalness: 0.05}),
    mesh: std({color: '#16191e', roughness: 0.85, metalness: 0.1}),
    carpet: std({color: '#121c2b', roughness: 0.94, metalness: 0}),
    stone: std({color: '#25303f', roughness: 0.28, metalness: 0.12}),
    glass: new THREE.MeshStandardMaterial({color: '#a9c2dc', roughness: 0.04, metalness: 0.9, transparent: true, opacity: 0.1, depthWrite: false}),
    black: new THREE.MeshBasicMaterial({color: '#020306'})
  };
  const glow = (hex, k) => new THREE.MeshBasicMaterial({color: new THREE.Color(hex).multiplyScalar(k), toneMapped: true});
  MAT.strip = glow('#9cc2ff', 3.2);
  MAT.stripWarm = glow('#ffe2bd', 3.0);
  MAT.stripDim = glow('#5f86c9', 1.4);

  /* ── batching: static geometry merged per material ─────────────── */
  const batches = new Map();
  function addStatic(geom, mat, matrix, opts) {
    const g = geom.index ? geom.toNonIndexed() : geom.clone();
    if (matrix) g.applyMatrix4(matrix);
    for (const k of Object.keys(g.attributes)) if (!['position', 'normal', 'uv'].includes(k)) g.deleteAttribute(k);
    if (!g.attributes.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
    const key = mat.uuid + (opts && opts.noReflect ? ':nr' : '') + (opts && opts.noShadow ? ':ns' : '');
    if (!batches.has(key)) batches.set(key, {mat, list: [], opts: opts || {}});
    batches.get(key).list.push(g);
  }
  function flushStatic() {
    for (const {mat, list, opts} of batches.values()) {
      const merged = mergeGeometries(list, false);
      list.forEach((g) => g.dispose());
      const m = new THREE.Mesh(merged, mat);
      m.castShadow = HIGH && !opts.noShadow && !(mat.isMeshBasicMaterial);
      m.receiveShadow = HIGH && !(mat.isMeshBasicMaterial);
      m.matrixAutoUpdate = false;
      if (opts.noReflect) { m.layers.set(LAYER_NO_REFLECT); }
      scene.add(m);
    }
    batches.clear();
  }
  const M4 = (x, y, z, rx, ry, rz, sx, sy, sz) => new THREE.Matrix4().compose(V(x, y, z), new THREE.Quaternion().setFromEuler(new THREE.Euler(rx || 0, ry || 0, rz || 0)), V(sx || 1, sy || 1, sz || 1));
  function roundedBox(w, h, d, r, seg) {
    const s = new THREE.Shape(), x = -w / 2 + r, y = -d / 2 + r, ww = w - 2 * r, dd = d - 2 * r;
    s.moveTo(x, y); s.lineTo(x + ww, y); s.absarc(x + ww, y, r, -Math.PI / 2, 0); s.lineTo(x + ww + r, y + dd); s.absarc(x + ww, y + dd, r, 0, Math.PI / 2);
    s.lineTo(x, y + dd + r); s.absarc(x, y + dd, r, Math.PI / 2, Math.PI); s.lineTo(x - r, y); s.absarc(x, y, r, Math.PI, Math.PI * 1.5);
    const g = new THREE.ExtrudeGeometry(s, {depth: h, bevelEnabled: false, curveSegments: seg || 6});
    g.rotateX(-Math.PI / 2); g.translate(0, -h / 2, 0);
    return g;
  }
  function curvedPanel(w, h, R, segs) {
    const g = new THREE.PlaneGeometry(w, h, segs || 32, 1), p = g.attributes.position;
    for (let i = 0; i < p.count; i++) { const a = p.getX(i) / R; p.setXYZ(i, R * Math.sin(a), p.getY(i), R - R * Math.cos(a)); }
    g.computeVertexNormals();
    return g;
  }
  /* a canvas-backed screen surface */
  const screens = [];
  function screen(geom, pxW, pxH, paint, tags, opts) {
    const c = document.createElement('canvas'); c.width = Math.round(pxW * TEX); c.height = Math.round(pxH * TEX);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = HIGH ? 8 : 2;
    const o = opts || {};
    if (o.repeatX) { tex.wrapS = THREE.RepeatWrapping; tex.repeat.x = o.repeatX; }
    const mat = new THREE.MeshBasicMaterial({map: tex, toneMapped: true, transparent: !!o.transparent, depthWrite: !o.transparent, side: o.side || THREE.FrontSide,
      blending: o.additive ? THREE.AdditiveBlending : THREE.NormalBlending});
    mat.color.setScalar(o.gain || 1.15);
    const mesh = new THREE.Mesh(geom, mat);
    const sc = {canvas: c, ctx: c.getContext('2d'), tex, mat, mesh, paint, tags: new Set(tags)};
    screens.push(sc);
    return sc;
  }
  function redraw(tag) {
    for (const sc of screens) if (!tag || sc.tags.has(tag) || sc.tags.has('*')) {
      try { sc.paint(sc.ctx, sc.canvas.width, sc.canvas.height); } catch (e) { if (window.console) console.warn('BETTOR floor: screen paint', e); }
      sc.tex.needsUpdate = true;
    }
    requestRender();
  }

  /* ── environment (reflections without an HDR file) ────────────── */
  {
    const env = new THREE.Scene();
    env.background = new THREE.Color('#0b1424');
    const em = (c, k) => new THREE.MeshBasicMaterial({color: new THREE.Color(c).multiplyScalar(k), side: THREE.DoubleSide});
    const ring = new THREE.Mesh(new THREE.TorusGeometry(7, 0.5, 8, 64), em('#dbe8ff', 5)); ring.rotation.x = Math.PI / 2; ring.position.y = 12; env.add(ring);
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(24, 6), em('#6d8dff', 1.6)); wall.position.set(0, 6.5, -17); env.add(wall);
    for (const sx of [-1, 1]) { const win = new THREE.Mesh(new THREE.PlaneGeometry(30, 10), em('#1b2a44', 0.9)); win.position.set(sx * 21, 6, 2); win.rotation.y = -sx * Math.PI / 2; env.add(win); }
    const warm = new THREE.Mesh(new THREE.PlaneGeometry(10, 1), em('#ffd9a8', 1.4)); warm.position.set(0, 4, 12); env.add(warm);
    const fl = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), em('#0a0d12', 1)); fl.rotation.x = -Math.PI / 2; env.add(fl);
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(env, 0.02).texture; pm.dispose();
    scene.environmentIntensity = 1.05;
  }

  /* ── lights ────────────────────────────────────────────────────── */
  scene.add(new THREE.HemisphereLight('#6c88b8', '#0c121c', phone ? 1.2 : 0.85));
  const key = new THREE.DirectionalLight('#d6e4ff', HIGH ? 1.1 : 1.6);
  key.position.set(8, 22, 6);
  if (HIGH) {
    key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
    const sc = key.shadow.camera; sc.left = -15; sc.right = 15; sc.top = 15; sc.bottom = -15; sc.near = 4; sc.far = 45;
    key.shadow.bias = -0.0004; key.shadow.normalBias = 0.025; key.shadow.radius = 4;
  }
  scene.add(key);
  const wallSpill = new THREE.SpotLight('#7a96ff', HIGH ? 110 : 90, 40, 0.8, 0.9, 1.6);
  wallSpill.position.set(0, 8, -15); wallSpill.target.position.set(0, 0, -2); scene.add(wallSpill, wallSpill.target);
  const coreLight = new THREE.PointLight('#6f9bff', 12, 12, 1.8); coreLight.position.set(0, 2.6, 0); scene.add(coreLight);
  // the ceiling wash: a broad, cool fill so the room reads as architecture, not a cave
  const wash = new THREE.PointLight('#b9cdf0', HIGH ? 60 : 45, 38, 1.4); wash.position.set(0, 12.5, 1); scene.add(wash);
  const front = new THREE.SpotLight('#cfe0ff', HIGH ? 70 : 55, 46, 0.75, 0.8, 1.2); front.position.set(0, 13, 16); front.target.position.set(0, 0.5, -3); scene.add(front, front.target);

  /* ── the floor: polished stone with real-time reflection (desktop) ─ */
  const floorTex = (() => {
    const c = document.createElement('canvas'); c.width = c.height = 1024 * (phone ? 0.5 : 1);
    const g = c.getContext('2d'), n = 4, t = c.width / n;
    let seed = 1337; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);   // deterministic tile variation (decor, not data)
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
      const v = 15 + Math.floor(rnd() * 6);
      g.fillStyle = 'rgb(' + v + ',' + (v + 3) + ',' + (v + 8) + ')'; g.fillRect(i * t, j * t, t, t);
      for (let k = 0; k < 6; k++) { g.strokeStyle = 'rgba(160,180,210,' + (0.012 + rnd() * 0.02) + ')'; g.lineWidth = 1 + rnd() * 2; g.beginPath(); const y0 = j * t + rnd() * t; g.moveTo(i * t, y0); g.bezierCurveTo(i * t + t * 0.3, y0 + (rnd() - 0.5) * t * 0.4, i * t + t * 0.7, y0 + (rnd() - 0.5) * t * 0.4, i * t + t, y0 + (rnd() - 0.5) * t * 0.3); g.stroke(); }
    }
    g.strokeStyle = 'rgba(0,0,0,.55)'; g.lineWidth = 3;
    for (let i = 0; i <= n; i++) { g.beginPath(); g.moveTo(i * t, 0); g.lineTo(i * t, c.height); g.moveTo(0, i * t); g.lineTo(c.width, i * t); g.stroke(); }
    const tx = new THREE.CanvasTexture(c); tx.wrapS = tx.wrapT = THREE.RepeatWrapping; tx.repeat.set(22, 22); tx.colorSpace = THREE.SRGBColorSpace; tx.anisotropy = HIGH ? 8 : 2;
    return tx;
  })();
  const floorMat = new THREE.MeshStandardMaterial({map: floorTex, color: '#dfe5ee', roughness: 0.26, metalness: 0.0, envMapIntensity: 0.45});
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), floorMat);
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = HIGH; scene.add(floor);
  let refl = null;
  if (HIGH) {
    const rt = new THREE.WebGLRenderTarget(512, 512, {type: THREE.HalfFloatType});
    const texMat = new THREE.Matrix4(), rcam = new THREE.PerspectiveCamera();
    rcam.layers.set(0);
    refl = {rt, texMat, rcam, strength: 0.5};
    floorMat.onBeforeCompile = (sh) => {
      sh.uniforms.tRefl = {value: rt.texture}; sh.uniforms.uTexMat = {value: texMat}; sh.uniforms.uReflStr = refl.uniform = {value: refl.strength};
      sh.uniforms.uTexel = {value: new THREE.Vector2(1 / 512, 1 / 512)}; refl.texel = sh.uniforms.uTexel;
      sh.vertexShader = 'uniform mat4 uTexMat;\nvarying vec4 vReflUv;\n' + sh.vertexShader.replace('#include <project_vertex>', '#include <project_vertex>\n vReflUv = uTexMat * vec4(transformed, 1.0);');
      sh.fragmentShader = 'uniform sampler2D tRefl; uniform float uReflStr; uniform vec2 uTexel;\nvarying vec4 vReflUv;\n' + sh.fragmentShader.replace('#include <tonemapping_fragment>',
        '{ vec2 ruv = vReflUv.xy / vReflUv.w; vec2 o = uTexel * 2.5;\n' +
        '  vec3 r = texture2D(tRefl, ruv).rgb * 0.28 + (texture2D(tRefl, ruv + vec2(o.x, 0.0)).rgb + texture2D(tRefl, ruv - vec2(o.x, 0.0)).rgb + texture2D(tRefl, ruv + vec2(0.0, o.y)).rgb + texture2D(tRefl, ruv - vec2(0.0, o.y)).rgb) * 0.13\n' +
        '    + (texture2D(tRefl, ruv + o * 2.0).rgb + texture2D(tRefl, ruv - o * 2.0).rgb + texture2D(tRefl, ruv + vec2(o.x, -o.y) * 2.0).rgb + texture2D(tRefl, ruv - vec2(o.x, -o.y) * 2.0).rgb) * 0.05;\n' +
        '  float fres = 0.45 + 0.55 * pow(1.0 - clamp(dot(normalize(vViewPosition), normal), 0.0, 1.0), 4.0);\n' +
        '  float tile = smoothstep(0.02, 0.09, dot(diffuseColor.rgb, vec3(0.333)));\n' +
        '  gl_FragColor.rgb += r * uReflStr * fres * tile; }\n#include <tonemapping_fragment>');
    };
  }
  function updateReflection() {
    const {rcam, texMat, rt} = refl;
    const n = V(0, 1, 0), camPos = camera.getWorldPosition(V(0, 0, 0));
    if (camPos.y < 0.05) return false;
    const rot = new THREE.Matrix4().extractRotation(camera.matrixWorld);
    const view = camPos.clone(); view.y = -view.y;
    const look = V(0, 0, -1).applyMatrix4(rot).add(camPos); look.y = -look.y;
    rcam.position.copy(view);
    rcam.up.set(0, 1, 0).applyMatrix4(rot).reflect(n);
    rcam.lookAt(look);
    rcam.far = camera.far; rcam.near = camera.near; rcam.fov = camera.fov; rcam.aspect = camera.aspect;
    rcam.updateMatrixWorld(); rcam.projectionMatrix.copy(camera.projectionMatrix);
    texMat.set(0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1);
    texMat.multiply(rcam.projectionMatrix).multiply(rcam.matrixWorldInverse).multiply(floor.matrixWorld);
    // oblique near plane on the floor
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(n, V(0, 0, 0)).applyMatrix4(rcam.matrixWorldInverse);
    const cp = new THREE.Vector4(plane.normal.x, plane.normal.y, plane.normal.z, plane.constant), pm = rcam.projectionMatrix.elements;
    const q = new THREE.Vector4((Math.sign(cp.x) + pm[8]) / pm[0], (Math.sign(cp.y) + pm[9]) / pm[5], -1, (1 + pm[10]) / pm[14]);
    cp.multiplyScalar(2 / cp.dot(q));
    pm[2] = cp.x; pm[6] = cp.y; pm[10] = cp.z + 1 - 0.003; pm[14] = cp.w;
    floor.visible = false;
    const fog = scene.fog; scene.fog = null;
    renderer.setRenderTarget(rt); renderer.clear(); renderer.render(scene, rcam);
    renderer.setRenderTarget(null);
    scene.fog = fog; floor.visible = true;
    return true;
  }

  /* ── architecture ──────────────────────────────────────────────── */
  {
    // the north wall that carries the LED wall
    addStatic(curvedPanel(46, 15, 19.2, 48), MAT.arch, M4(0, 7.5, -19.2));
    for (let i = -11; i <= 11; i++) {   // vertical fins
      const a = i * 0.055;
      addStatic(new THREE.BoxGeometry(0.16, 15, 0.5), MAT.panel, M4(19 * Math.sin(a), 7.5, -19 * Math.cos(a), 0, -a, 0));
    }
    addStatic(curvedPanel(46, 0.06, 18.9, 48), MAT.strip, M4(0, 3.55, -18.9), {noShadow: true});
    // THE BETTOR WALL: the house lockup, backlit, above the LED walls
    {
      const lc = document.createElement('canvas'); lc.width = 2048 * TEX; lc.height = 420 * TEX;
      const lg = lc.getContext('2d'), k = lc.width / 2048;
      const lt = new THREE.CanvasTexture(lc); lt.colorSpace = THREE.SRGBColorSpace; lt.anisotropy = HIGH ? 8 : 2;
      brandImage('brand/bettortoken-logo-white.png', (im) => {
        const hh = 250 * k, ww = im.naturalWidth * hh / im.naturalHeight;
        lg.shadowColor = 'rgba(120,170,255,.85)'; lg.shadowBlur = 40 * k;
        lg.drawImage(im, 1024 * k - ww / 2, 30 * k, ww, hh);
        lg.shadowBlur = 0; lg.font = '600 ' + (40 * k) + 'px "IBM Plex Sans Condensed", "IBM Plex Sans", sans-serif'; lg.textAlign = 'center';
        lg.fillStyle = 'rgba(170,200,240,.85)'; lg.fillText('A U T O N O M O U S   I N V E S T M E N T   H E A D Q U A R T E R S', 1024 * k, 360 * k);
        lt.needsUpdate = true; requestRender(); });
      const logo = new THREE.Mesh(new THREE.PlaneGeometry(13.6, 2.79), new THREE.MeshBasicMaterial({map: lt, transparent: true, depthWrite: false, toneMapped: false, color: new THREE.Color(1.15, 1.18, 1.25)}));
      logo.position.set(0, 10.05, -17.75); scene.add(logo);
      const halo = new THREE.Mesh(new THREE.PlaneGeometry(17, 0.035), MAT.strip); halo.position.set(0, 8.45, -17.7); scene.add(halo);
      const lw = new THREE.PointLight('#7fa6ff', HIGH ? 30 : 22, 16, 1.6); lw.position.set(0, 10, -15.5); scene.add(lw);
    }
    addStatic(curvedPanel(46, 0.05, 18.9, 48), MAT.stripDim, M4(0, 11.4, -18.9), {noShadow: true});
    // ceiling
    const ceil = new THREE.Mesh(new THREE.CircleGeometry(40, 64), new THREE.MeshStandardMaterial({color: '#05070a', roughness: 0.9}));
    ceil.rotation.x = Math.PI / 2; ceil.position.y = 14; scene.add(ceil);
    addStatic(new THREE.TorusGeometry(7.2, 0.07, 8, 128), MAT.strip, M4(0, 12.9, 0, Math.PI / 2), {noShadow: true});
    addStatic(new THREE.TorusGeometry(5.2, 0.045, 8, 128), MAT.stripDim, M4(0, 13.1, 0, Math.PI / 2), {noShadow: true});
    addStatic(new THREE.CylinderGeometry(7.6, 7.6, 0.9, 96, 1, true), MAT.panel, M4(0, 13.45, 0), {noShadow: true});
    for (let i = 0; i < 24; i++) {
      const a = i / 24 * Math.PI * 2, r0 = 8.3, len = 9;
      addStatic(new THREE.BoxGeometry(0.07, 0.035, len), i % 2 ? MAT.stripDim : MAT.strip, M4(Math.sin(a) * (r0 + len / 2), 13.5, Math.cos(a) * (r0 + len / 2), 0, a, 0), {noShadow: true});
    }
    // structural columns with light reveals
    for (const deg of [-52, -88, 52, 88]) {
      const a = deg * Math.PI / 180, r = 20.2, x = r * Math.sin(a), z = -r * Math.cos(a);
      addStatic(new THREE.CylinderGeometry(0.55, 0.55, 14, 24), MAT.stone, M4(x, 7, z));
      addStatic(new THREE.BoxGeometry(0.06, 12.5, 0.06), MAT.stripDim, M4(x * 0.972, 7, z * 0.972, 0, -a, 0), {noShadow: true});
    }
    // glass curtain walls with mullions and transoms, east and west
    for (const side of [-1, 1]) {
      for (let k = 0; k < 7; k++) {
        const a = side * (62 + k * 12) * Math.PI / 180, r = 23;
        const seg = new THREE.Mesh(new THREE.PlaneGeometry(4.8, 14), MAT.glass);
        seg.position.set(r * Math.sin(a), 7, -r * Math.cos(a)); seg.lookAt(0, 7, 0); scene.add(seg);
        addStatic(new THREE.BoxGeometry(0.12, 14, 0.25), MAT.darkMetal, M4(r * Math.sin(a + side * 0.105), 7, -r * Math.cos(a + side * 0.105), 0, -a, 0));
        for (const y of [0.15, 4.6, 9.2, 13.8]) addStatic(new THREE.BoxGeometry(4.9, 0.1, 0.2), MAT.darkMetal, M4(r * Math.sin(a), y, -r * Math.cos(a), 0, -a, 0), {noShadow: true});
      }
    }
    // the night skyline beyond the glass (painted once; deterministic)
    const sk = document.createElement('canvas'); sk.width = 4096 * TEX; sk.height = 1024 * TEX;
    const k = sk.getContext('2d'), W = sk.width, H = sk.height;
    const gr = k.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, '#020409'); gr.addColorStop(0.55, '#0a1527'); gr.addColorStop(1, '#16253c');
    k.fillStyle = gr; k.fillRect(0, 0, W, H);
    let seed = 77; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let layer = 0; layer < 2; layer++) {
      let x = 0;
      while (x < W) {
        const w = (40 + rnd() * 110) * TEX, h = (layer ? 180 + rnd() * 520 : 100 + rnd() * 260) * TEX;
        k.fillStyle = layer ? '#060b14' : '#0a1220'; k.fillRect(x, H - h, w, h);
        if (layer) for (let yy = H - h + 12 * TEX; yy < H - 8; yy += 16 * TEX) for (let xx = x + 6 * TEX; xx < x + w - 8 * TEX; xx += 11 * TEX) if (rnd() > 0.78) { k.fillStyle = rnd() > 0.85 ? 'rgba(255,214,160,.55)' : 'rgba(170,205,255,.35)'; k.fillRect(xx, yy, 5 * TEX, 7 * TEX); }
        x += w + (layer ? 6 : 2) * TEX;
      }
    }
    const skyT = new THREE.CanvasTexture(sk); skyT.colorSpace = THREE.SRGBColorSpace;
    const sky = new THREE.Mesh(new THREE.CylinderGeometry(70, 70, 46, 64, 1, true), new THREE.MeshBasicMaterial({map: skyT, side: THREE.BackSide, fog: false, color: new THREE.Color(0.7, 0.7, 0.75)}));
    sky.position.y = 14; scene.add(sky);
  }

  /* ── the capital core ──────────────────────────────────────────── */
  const core = {};
  {
    addStatic(new THREE.CylinderGeometry(3.3, 3.4, 0.2, 96), MAT.stone, M4(0, 0.1, 0));
    addStatic(new THREE.CylinderGeometry(2.45, 2.5, 0.16, 96), MAT.stone, M4(0, 0.28, 0));
    addStatic(new THREE.CylinderGeometry(0.95, 1.15, 0.55, 48), MAT.darkMetal, M4(0, 0.63, 0));
    addStatic(new THREE.CylinderGeometry(0.62, 0.95, 0.12, 48), MAT.alu, M4(0, 0.96, 0));
    core.rimMat = glow('#7fa6ff', 3);
    const rim1 = new THREE.Mesh(new THREE.TorusGeometry(3.36, 0.022, 6, 160), core.rimMat); rim1.rotation.x = Math.PI / 2; rim1.position.y = 0.205; scene.add(rim1);
    const rim2 = new THREE.Mesh(new THREE.TorusGeometry(2.49, 0.02, 6, 160), core.rimMat); rim2.rotation.x = Math.PI / 2; rim2.position.y = 0.365; scene.add(rim2);
    // the medallion inlaid in the dais
    const mc = document.createElement('canvas'); mc.width = mc.height = 1024 * TEX;
    const m = mc.getContext('2d'), c = mc.width / 2; m.translate(c, c); const s = mc.width / 1024;
    for (const [r, w, a] of [[500, 3, .55], [462, 1.5, .3], [300, 1.2, .25], [150, 1, .2]]) { m.beginPath(); m.arc(0, 0, r * s, 0, Math.PI * 2); m.strokeStyle = 'rgba(150,190,255,' + a + ')'; m.lineWidth = w * s; m.stroke(); }
    for (let i = 0; i < 96; i++) { const an = i / 96 * Math.PI * 2; m.beginPath(); m.moveTo(Math.cos(an) * 466 * s, Math.sin(an) * 466 * s); m.lineTo(Math.cos(an) * (i % 8 ? 478 : 496) * s, Math.sin(an) * (i % 8 ? 478 : 496) * s); m.strokeStyle = 'rgba(150,190,255,.4)'; m.lineWidth = 2 * s; m.stroke(); }
    m.fillStyle = 'rgba(200,220,255,.7)'; m.font = '600 ' + (44 * s) + 'px Inter, system-ui, sans-serif'; m.textAlign = 'center';
    for (let i = 0; i < 2; i++) { m.save(); m.rotate(i * Math.PI); m.fillText('B E T T O R T O K E N   ·   C A P I T A L   C O R E', 0, -390 * s); m.restore(); }
    const mt = new THREE.CanvasTexture(mc); mt.colorSpace = THREE.SRGBColorSpace;
    // the BettorToken mark (brand/) at the centre of the dais
    brandImage('brand/bettortoken-mark-white.png', (im) => { m.globalAlpha = 0.85; m.drawImage(im, -250 * s, -250 * s, 500 * s, 500 * s); m.globalAlpha = 1; mt.needsUpdate = true; });
    const med = new THREE.Mesh(new THREE.CircleGeometry(2.42, 96), new THREE.MeshBasicMaterial({map: mt, transparent: true, opacity: 0.9, depthWrite: false}));
    med.rotation.x = -Math.PI / 2; med.position.y = 0.365; scene.add(med);
    // energy column (a static vertical gradient; colour = paper status)
    core.beamMat = new THREE.ShaderMaterial({transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: {uColor: {value: new THREE.Color('#6f9bff')}, uK: {value: 1}},
      vertexShader: 'varying vec2 vUv; varying vec3 vN; varying vec3 vV; void main(){ vUv = uv; vN = normalize(normalMatrix*normal); vec4 mv = modelViewMatrix*vec4(position,1.); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }',
      fragmentShader: 'uniform vec3 uColor; uniform float uK; varying vec2 vUv; varying vec3 vN; varying vec3 vV; void main(){ float rim = pow(1.0 - abs(dot(vN, vV)), 1.8); float h = smoothstep(0.0, 0.15, vUv.y) * (1.0 - smoothstep(0.55, 1.0, vUv.y)); gl_FragColor = vec4(uColor * (0.05 + rim * 0.42) * h * uK, 1.0); }'});
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.32, 3.4, 32, 1, true), core.beamMat); beam.position.y = 2.6; scene.add(beam);
    // the orb
    core.orbMat = new THREE.MeshStandardMaterial({color: '#0b1430', emissive: new THREE.Color('#5d8cff'), emissiveIntensity: 2.4, roughness: 0.2, metalness: 0.4, flatShading: true});
    core.orb = new THREE.Mesh(new THREE.IcosahedronGeometry(0.36, 1), core.orbMat); core.orb.position.y = 2.95; scene.add(core.orb);
    core.cage = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.56, 1)), new THREE.LineBasicMaterial({color: new THREE.Color('#9fbcff').multiplyScalar(1.6), transparent: true, opacity: 0.6}));
    core.cage.position.y = 2.95; scene.add(core.cage);
    // the holographic band: PAPER | SMALL LIVE | PAPER | SMALL LIVE (never one figure)
    core.band = screen(new THREE.CylinderGeometry(1.7, 1.7, 0.95, 96, 1, true), 4096, 366, (ctx, w, h) => S.coreBand(ctx, w, h, HQ), ['equity'], {transparent: true, additive: true, gain: 0.82});
    core.band.mesh.position.y = 1.72; scene.add(core.band.mesh);
    for (const y of [1.22, 2.22]) { const t = new THREE.Mesh(new THREE.TorusGeometry(1.7, 0.012, 6, 128), core.rimMat); t.rotation.x = Math.PI / 2; t.position.y = y; scene.add(t); }
    // equity pulse ring (fires only when /equity/live seq changes)
    core.pulse = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 128), new THREE.MeshBasicMaterial({color: new THREE.Color('#8fb0ff').multiplyScalar(2), transparent: true, opacity: 0, depthWrite: false}));
    core.pulse.rotation.x = -Math.PI / 2; core.pulse.position.y = 0.02; scene.add(core.pulse);
    core.pulseT = 1; core.lastChange = null;
    // the house wordmark inlaid in the floor on the visitors' side of the core
    {
      const wc = document.createElement('canvas'); wc.width = 2048 * TEX; wc.height = 320 * TEX;
      const w = wc.getContext('2d'), k = wc.width / 2048;
      w.textAlign = 'center';
      const wt = new THREE.CanvasTexture(wc); wt.colorSpace = THREE.SRGBColorSpace; wt.anisotropy = HIGH ? 8 : 2;
      // the full BettorToken lockup (brand/), inlaid; nothing is drawn until it loads
      brandImage('brand/bettortoken-logo-white.png', (im) => {
        const hh = 230 * k, ww = im.naturalWidth * hh / im.naturalHeight; w.drawImage(im, 1024 * k - ww / 2, 20 * k, ww, hh);
        w.font = '600 ' + (36 * k) + 'px Inter, system-ui, sans-serif'; w.fillStyle = 'rgba(150,180,220,.8)'; w.fillText('C A P I T A L   ·   H E A D Q U A R T E R S', 1024 * k, 296 * k);
        wt.needsUpdate = true; });
      const mark = new THREE.Mesh(new THREE.PlaneGeometry(8.4, 1.31), new THREE.MeshBasicMaterial({map: wt, transparent: true, opacity: 0.16, depthWrite: false}));
      mark.rotation.x = -Math.PI / 2; mark.position.set(0, 0.006, 6.4); scene.add(mark);
    }
    // floor rings
    for (const [r, mat] of [[3.9, MAT.stripDim], [6.1, MAT.stripDim]]) { const g = new THREE.RingGeometry(r - 0.012, r + 0.012, 192); g.rotateX(-Math.PI / 2); addStatic(g, mat, M4(0, 0.004, 0), {noShadow: true}); }
  }

  /* ── the suspended ticker ring (real marks, scrolling) ─────────── */
  const ring = {};
  {
    ring.sc = screen(new THREE.CylinderGeometry(4.7, 4.7, 0.72, 128, 1, true), 4096, 200, (ctx, w, h) => S.ticker(ctx, w, h, HQ), ['equity', 'floor'], {repeatX: 2, gain: 1.4});
    ring.sc.mesh.position.y = 9.2; ring.sc.mesh.layers.set(0); scene.add(ring.sc.mesh);
    const inner = new THREE.Mesh(new THREE.CylinderGeometry(4.66, 4.66, 0.72, 96, 1, true), new THREE.MeshStandardMaterial({color: '#07090d', roughness: 0.5, metalness: 0.6, side: THREE.BackSide}));
    inner.position.y = 9.2; scene.add(inner);
    for (const y of [8.82, 9.58]) addStatic(new THREE.TorusGeometry(4.72, 0.045, 8, 160), MAT.alu, M4(0, y, 0, Math.PI / 2), {noShadow: true});
    for (let i = 0; i < 6; i++) { const a = i / 6 * Math.PI * 2; addStatic(new THREE.CylinderGeometry(0.012, 0.012, 4.4, 6), MAT.alu, M4(Math.sin(a) * 4.7, 11.8, Math.cos(a) * 4.7), {noShadow: true, noReflect: true}); }
  }

  /* ── the giant LED wall (floor.js projects its DOM wall screens onto
   *    these three panels: feed, equity, health) + side walls ────────── */
  const WALL = [];
  {
    const Rw = 17.6;
    const mk = (id, deg, w, h, y, pxW, pxH, paint, tags) => {
      const a = deg * Math.PI / 180;
      const sc = screen(new THREE.PlaneGeometry(w, h), pxW, pxH, paint, tags, {gain: 1.25});
      sc.mesh.position.set(Rw * Math.sin(a), y, -Rw * Math.cos(a)); sc.mesh.lookAt(0, y, 0); scene.add(sc.mesh);
      const fr = new THREE.Mesh(new THREE.BoxGeometry(w + 0.22, h + 0.22, 0.1), MAT.bezel);
      fr.position.copy(sc.mesh.position).multiplyScalar(1.0035); fr.position.y = y; fr.lookAt(0, y, 0); scene.add(fr);
      const glowLine = new THREE.Mesh(new THREE.PlaneGeometry(w + 0.4, 0.025), MAT.strip);
      glowLine.position.copy(sc.mesh.position); glowLine.position.y = y - h / 2 - 0.2; glowLine.lookAt(0, glowLine.position.y, 0); scene.add(glowLine);
      sc.mesh.updateMatrixWorld(true);
      WALL.push({id, mesh: sc.mesh, w, h});
      return sc;
    };
    mk('opps', -29, 5.4, 3.56, 5.95, 1552, 1024, (c, w, h) => S.opportunities(c, w, h, HQ), ['floor']);
    mk('equity', 0, 11.2, 3.75, 6.15, 2048, 689, (c, w, h) => S.capital(c, w, h, HQ), ['equity']);
    mk('risk', 29, 5.4, 3.56, 5.95, 1552, 1024, (c, w, h) => S.riskWall(c, w, h, HQ), ['release', 'equity', 'coverage', 'floor']);
    // side walls on pylons
    const side = (deg, paint, tags) => {
      const a = deg * Math.PI / 180, r = 16.2, y = 3.7;
      const sc = screen(curvedPanel(5.6, 3.15, 16, 12), 1138, 640, paint, tags, {gain: 1.2});
      sc.mesh.position.set(r * Math.sin(a), y, -r * Math.cos(a)); sc.mesh.lookAt(0, y, 0); scene.add(sc.mesh);
      const m = new THREE.Matrix4().compose(V(r * 1.006 * Math.sin(a), y, -r * 1.006 * Math.cos(a)), new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(V(0, y, 0), V(r * Math.sin(a), y, -r * Math.cos(a)), V(0, 1, 0))), V(1, 1, 1));
      addStatic(new THREE.BoxGeometry(5.9, 3.45, 0.14), MAT.bezel, m);
      addStatic(new THREE.BoxGeometry(0.5, 2.1, 0.5), MAT.darkMetal, M4(r * 1.012 * Math.sin(a), 1.05, -r * 1.012 * Math.cos(a), 0, -a, 0));
      addStatic(new THREE.BoxGeometry(5.9, 0.03, 0.03), MAT.strip, new THREE.Matrix4().multiplyMatrices(m, M4(0, -1.8, 0.08)), {noShadow: true});
    };
    side(-64, (c, w, h) => S.markets(c, w, h, HQ), ['equity']);
    side(64, (c, w, h) => S.coverageWall(c, w, h, HQ), ['coverage']);
  }

  /* ── the seven desk zones ──────────────────────────────────────── */
  const desks = {}, pickables = [];
  function buildDesk(seat) {
    const slug = seat.slug, th = SEAT_ANGLE[slug] * Math.PI / 180, planned = !!seat.planned;
    const g = new THREE.Group();
    g.position.set(R_DESK * Math.sin(th), 0, -R_DESK * Math.cos(th));
    g.lookAt(0, 0, 0); g.updateMatrixWorld(true);
    scene.add(g);
    const L = (x, y, z, rx, ry, rz, sx, sy, sz) => new THREE.Matrix4().multiplyMatrices(g.matrixWorld, M4(x, y, z, rx, ry, rz, sx, sy, sz));
    const accent = new THREE.Color(planned ? '#6d7c90' : seat.accent);
    const wide = slug === 'allocator' ? 2.9 : slug === 'eddie' ? 2.7 : 2.3;
    // zone plate + its lit edge (state colour)
    addStatic(roundedBox(5.6, 0.03, 4.6, 0.35, 6), MAT.carpet, L(0, 0.015, -0.25), {noShadow: true});
    const edgeMat = new THREE.MeshBasicMaterial({color: accent.clone().multiplyScalar(2)});
    const eg = [];
    for (const [w, d, x, z] of [[5.6, 0.025, 0, 2.0], [5.6, 0.025, 0, -2.5], [0.025, 4.6, 2.8, -0.25], [0.025, 4.6, -2.8, -0.25]]) {
      const b = new THREE.BoxGeometry(w, 0.012, d); b.applyMatrix4(M4(x, 0.034, z)); eg.push(b);
    }
    const edge = new THREE.Mesh(mergeGeometries(eg), edgeMat); g.add(edge);
    // floor light path toward the core (brightness = desk state)
    const pathMat = new THREE.MeshBasicMaterial({color: accent.clone().multiplyScalar(1.2), transparent: true, opacity: 0.6, depthWrite: false});
    const path = new THREE.Mesh(new THREE.PlaneGeometry(0.035, R_DESK - 6.3), pathMat);
    path.rotation.x = -Math.PI / 2; path.position.set(0, 0.006, 2.0 + (R_DESK - 6.3) / 2 - 0.0); g.add(path);
    // the desk
    const deskTopMat = slug === 'allocator' ? MAT.walnut : MAT.deskTop;
    addStatic(roundedBox(wide, 0.045, 0.82, 0.04, 4), deskTopMat, L(0, DESK_TOP - 0.022, 0.8));
    addStatic(new THREE.BoxGeometry(wide, 0.012, 0.012), slug === 'allocator' ? MAT.brass : MAT.alu, L(0, DESK_TOP - 0.03, 0.39), {noShadow: true});
    for (const sx of [-1, 1]) addStatic(new THREE.BoxGeometry(0.05, DESK_TOP - 0.045, 0.72), MAT.darkMetal, L(sx * (wide / 2 - 0.12), (DESK_TOP - 0.045) / 2, 0.8));
    addStatic(new THREE.BoxGeometry(wide - 0.3, 0.32, 0.02), MAT.darkMetal, L(0, DESK_TOP - 0.22, 1.16));
    // keyboard + mouse (small: not reflected)
    addStatic(roundedBox(0.44, 0.018, 0.14, 0.01, 2), MAT.bezel, L(0, DESK_TOP + 0.009, 0.56), {noReflect: true, noShadow: true});
    addStatic(roundedBox(0.06, 0.02, 0.1, 0.025, 3), MAT.bezel, L(0.34, DESK_TOP + 0.01, 0.56), {noReflect: true, noShadow: true});
    // monitors (screens face the agent: -z)
    const kinds = S.DESK_SCREENS[slug] || ['status'];
    const mons = [];
    const monAt = (kind, x, y, w, h, yaw, curved) => {
      const geom = curved ? curvedPanel(w, h, 1.6, 16) : new THREE.PlaneGeometry(w, h);
      const sc = screen(geom, Math.round(320 * w / h), 320, (c, cw, ch) => S.monitor(c, cw, ch, HQ, slug, kind), ['floor', 'equity', 'coverage', 'xavier', 'clock']);
      const holder = new THREE.Group(); holder.position.set(x, y, 1.02); holder.rotation.y = Math.PI + yaw; g.add(holder);
      sc.mesh.position.z = 0.012; holder.add(sc.mesh);
      const back = new THREE.Mesh(curved ? curvedPanel(w + 0.03, h + 0.03, 1.62, 16) : new THREE.BoxGeometry(w + 0.03, h + 0.03, 0.02), MAT.bezel);
      holder.add(back);
      // the back of every monitor carries a faint accent line (seen from the core)
      const line = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.6, 0.008), new THREE.MeshBasicMaterial({color: accent.clone().multiplyScalar(1.5)}));
      line.position.set(0, -h / 2 + 0.03, -0.012); line.rotation.y = Math.PI; holder.add(line);
      sc.mesh.layers.set(0);
      mons.push(sc);
      return holder;
    };
    const mw = 0.6, mh = 0.35, y0 = DESK_TOP + 0.36;
    if (slug === 'eddie') {
      [-0.62, 0, 0.62].forEach((x, i) => { monAt(kinds[i], x, y0, mw, mh, [-0.32, 0, 0.32][i]); monAt(kinds[i + 3], x, y0 + 0.385, mw, mh, [-0.32, 0, 0.32][i]); });
    } else if (slug === 'xavier') {
      monAt(kinds[0], 0, y0 + 0.02, 1.15, 0.38, 0, true); monAt(kinds[1], -0.88, y0, mw * 0.85, mh * 0.85, -0.5); monAt(kinds[2], 0.88, y0, mw * 0.85, mh * 0.85, 0.5);
    } else if (slug === 'audrey') {
      [-0.63, 0, 0.63].forEach((x, i) => monAt(kinds[i], x, y0, mw, mh, [-0.36, 0, 0.36][i])); monAt(kinds[3], 0, y0 + 0.385, mw, mh, 0);
    } else {
      [-0.63, 0, 0.63].forEach((x, i) => monAt(kinds[i], x, y0, mw, mh, [-0.36, 0, 0.36][i]));
    }
    // monitor stand pole + base
    addStatic(new THREE.CylinderGeometry(0.018, 0.018, slug === 'eddie' ? 0.82 : 0.42, 10), MAT.alu, L(0, DESK_TOP + 0.21 * (slug === 'eddie' ? 1.95 : 1), 1.06), {noReflect: true});
    addStatic(new THREE.BoxGeometry(0.26, 0.012, 0.16), MAT.alu, L(0, DESK_TOP + 0.006, 1.06), {noReflect: true, noShadow: true});
    // the executive chair (base static; the seat reads as leather)
    {
      const cz = -0.06;
      addStatic(roundedBox(0.52, 0.08, 0.5, 0.08, 5), MAT.leather, L(0, SEAT_TOP - 0.04, cz + 0.02));
      addStatic(roundedBox(0.5, 0.07, 0.66, 0.1, 5), slug === 'allocator' ? MAT.leather : MAT.mesh, L(0, SEAT_TOP + 0.4, cz - 0.2, Math.PI / 2 - 0.12, 0, 0));
      if (slug === 'allocator' || slug === 'xavier') addStatic(roundedBox(0.32, 0.07, 0.16, 0.06, 4), MAT.leather, L(0, SEAT_TOP + 0.84, cz - 0.25, Math.PI / 2 - 0.12, 0, 0));
      for (const sx of [-1, 1]) {
        addStatic(new THREE.BoxGeometry(0.05, 0.03, 0.3), MAT.leather, L(sx * 0.29, SEAT_TOP + 0.2, cz), {noReflect: true});
        addStatic(new THREE.BoxGeometry(0.025, 0.2, 0.03), MAT.darkMetal, L(sx * 0.29, SEAT_TOP + 0.08, cz - 0.04), {noReflect: true});
      }
      addStatic(new THREE.CylinderGeometry(0.03, 0.035, SEAT_TOP - 0.16, 12), MAT.alu, L(0, (SEAT_TOP - 0.16) / 2 + 0.08, cz));
      for (let i = 0; i < 5; i++) {
        const a = i / 5 * Math.PI * 2;
        addStatic(new THREE.BoxGeometry(0.035, 0.03, 0.32), MAT.alu, L(Math.sin(a) * 0.16, 0.08, cz + Math.cos(a) * 0.16, 0, a, 0), {noReflect: true});
        addStatic(new THREE.SphereGeometry(0.03, 8, 6), MAT.bezel, L(Math.sin(a) * 0.31, 0.03, cz + Math.cos(a) * 0.31), {noReflect: true, noShadow: true});
      }
    }
    // backdrop: a glass partition carrying the zone board, and the lit sign
    addStatic(new THREE.BoxGeometry(5.4, 2.9, 0.06), MAT.panel, L(0, 1.45, -2.42));
    for (const sx of [-1, 1]) addStatic(new THREE.BoxGeometry(0.06, 2.95, 0.12), MAT.alu, L(sx * 2.72, 1.475, -2.42));
    addStatic(new THREE.BoxGeometry(5.5, 0.05, 0.14), MAT.alu, L(0, 2.93, -2.42));
    const glassP = new THREE.Mesh(new THREE.PlaneGeometry(5.4, 1.1), MAT.glass); glassP.position.set(0, 3.5, -2.42); g.add(glassP);
    const board = screen(new THREE.PlaneGeometry(3.1, 1.74), 1536, 864, (c, w, h) => S.zoneBoard(c, w, h, HQ, slug), ['floor', 'clock'], {gain: 1.2});
    board.mesh.position.set(0, 1.62, -2.385); g.add(board.mesh);
    const sign = screen(new THREE.PlaneGeometry(4.2, 0.66), 1024, 160, (c, w, h) => S.zoneSign(c, w, h, HQ, slug), ['floor'], {transparent: true, gain: 1.5});
    sign.mesh.scale.setScalar(0.62); sign.mesh.position.set(-0.25, 2.7, -2.38); g.add(sign.mesh);
    const accentBar = new THREE.Mesh(new THREE.BoxGeometry(5.4, 0.025, 0.03), new THREE.MeshBasicMaterial({color: accent.clone().multiplyScalar(2.4)}));
    accentBar.position.set(0, 0.05, -2.38); g.add(accentBar);
    // nameplate on the desk front (faces the core)
    const plate = screen(new THREE.PlaneGeometry(0.96, 0.18), 512, 96, (c, w, h) => S.nameplate(c, w, h, HQ, slug), ['floor'], {gain: 1.2});
    plate.mesh.position.set(0, DESK_TOP - 0.15, 1.175); g.add(plate.mesh);
    // the pendant luminaire + its desk light (Allie: a brass ring over the allocation desk)
    if (slug === 'allocator') {
      addStatic(new THREE.TorusGeometry(1.25, 0.05, 10, 96), MAT.brass, L(0, 3.4, 0.35, Math.PI / 2), {noShadow: true});
      const halo = new THREE.Mesh(new THREE.TorusGeometry(1.25, 0.018, 8, 128), glow('#ffd9b0', 3.2)); halo.rotation.x = Math.PI / 2; halo.position.set(0, 3.36, 0.35); g.add(halo);
      for (let i = 0; i < 3; i++) { const a = i / 3 * Math.PI * 2; addStatic(new THREE.CylinderGeometry(0.004, 0.004, 10.6, 4), MAT.alu, L(Math.sin(a) * 1.25, 8.7, 0.35 + Math.cos(a) * 1.25), {noShadow: true, noReflect: true}); }
      // her line to the capital core is drawn in brass, wider than the others
      const brassPath = new THREE.Mesh(new THREE.PlaneGeometry(0.11, R_DESK - 6.3), new THREE.MeshBasicMaterial({color: new THREE.Color('#e7c27c').multiplyScalar(0.9), transparent: true, opacity: 0.5, depthWrite: false}));
      brassPath.rotation.x = -Math.PI / 2; brassPath.position.set(0, 0.005, 2.0 + (R_DESK - 6.3) / 2); g.add(brassPath);
    } else {
      addStatic(new THREE.BoxGeometry(2.6, 0.05, 0.22), MAT.darkMetal, L(0, 3.35, 0.45), {noShadow: true});
      const lum = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 0.14), MAT.stripWarm); lum.rotation.x = Math.PI / 2; lum.position.set(0, 3.322, 0.45); g.add(lum);
      for (const sx of [-1, 1]) addStatic(new THREE.CylinderGeometry(0.004, 0.004, 10.6, 4), MAT.alu, L(sx * 1.1, 8.65, 0.45), {noShadow: true, noReflect: true});
    }
    let spot = null;
    if (HIGH && !planned) {
      spot = new THREE.SpotLight('#ffe7c8', 40, 9, 0.62, 0.7, 1.4);
      spot.position.copy(g.localToWorld(V(0, 3.3, 0.35))); spot.target.position.copy(g.localToWorld(V(0, 0.6, 0.2)));
      scene.add(spot, spot.target);
    }
    let accentLight = null;
    if (HIGH && !planned) { accentLight = new THREE.PointLight(accent.clone(), 3, 4.5, 1.8); accentLight.position.copy(g.localToWorld(V(0, 1.6, -1.9))); scene.add(accentLight); }
    // zone character props
    if (slug === 'allocator') {   // brass balance on the executive desk
      addStatic(new THREE.CylinderGeometry(0.05, 0.07, 0.025, 24), MAT.brass, L(-1.15, DESK_TOP + 0.012, 0.7), {noReflect: true});
      addStatic(new THREE.CylinderGeometry(0.006, 0.006, 0.24, 8), MAT.brass, L(-1.15, DESK_TOP + 0.13, 0.7), {noReflect: true});
      addStatic(new THREE.BoxGeometry(0.3, 0.007, 0.007), MAT.brass, L(-1.15, DESK_TOP + 0.25, 0.7), {noReflect: true});
      for (const px of [-0.14, 0.14]) addStatic(new THREE.CylinderGeometry(0.05, 0.035, 0.012, 20), MAT.brass, L(-1.15 + px, DESK_TOP + 0.18, 0.7), {noReflect: true});
    }
    if (slug === 'eddie') {   // headset on the desk
      const hs = new THREE.TorusGeometry(0.09, 0.008, 8, 24, Math.PI); addStatic(hs, MAT.bezel, L(1.05, DESK_TOP + 0.09, 0.62, 0, 0.6, 0), {noReflect: true});
      for (const sx of [-1, 1]) addStatic(new THREE.CylinderGeometry(0.035, 0.035, 0.03, 16), MAT.bezel, L(1.05 + sx * 0.074, DESK_TOP + 0.02, 0.62 + sx * 0.05, Math.PI / 2, 0.6, 0), {noReflect: true});
    }
    if (slug === 'audrey') {  // ledger binders on a low credenza behind
      addStatic(new THREE.BoxGeometry(1.6, 0.6, 0.4), MAT.panel, L(1.7, 0.3, -1.95));
      const cols = ['#3d2f55', '#2c3a4f', '#4a3a24', '#2f4a3e', '#55303a', '#28303c', '#3d2f55', '#4a3a24'];
      cols.forEach((c, i) => addStatic(new THREE.BoxGeometry(0.07, 0.32, 0.26), std({color: c, roughness: 0.7}), L(1.08 + i * 0.09, 0.76, -1.95), {noReflect: true}));
    }
    if (slug === 'scout') {   // the research globe
      addStatic(new THREE.CylinderGeometry(0.16, 0.22, 0.9, 24), MAT.darkMetal, L(1.85, 0.45, 0.6));
      const globe = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.3, 2)), new THREE.LineBasicMaterial({color: accent.clone().multiplyScalar(1.6), transparent: true, opacity: 0.75}));
      globe.position.copy(g.localToWorld(V(1.85, 1.25, 0.6))); scene.add(globe);
      const gc = new THREE.Mesh(new THREE.SphereGeometry(0.26, 24, 16), new THREE.MeshStandardMaterial({color: '#0b1520', emissive: accent.clone(), emissiveIntensity: 0.35, roughness: 0.3, metalness: 0.5}));
      gc.position.copy(globe.position); scene.add(gc);
      desksExtra[slug] = {spin: globe};
    }
    if (slug === 'karen') {   // the red-team glass fin
      const fin = new THREE.Mesh(new THREE.PlaneGeometry(0.04, 2.2), new THREE.MeshBasicMaterial({color: accent.clone().multiplyScalar(2.2)}));
      fin.position.set(-2.55, 1.1, -0.8); fin.rotation.y = Math.PI / 2; g.add(fin);
      const glassFin = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 2.3), new THREE.MeshStandardMaterial({color: '#ff8a90', roughness: 0.05, metalness: 0.6, transparent: true, opacity: 0.08, depthWrite: false, side: THREE.DoubleSide}));
      glassFin.position.set(-2.55, 1.15, -0.95); glassFin.rotation.y = Math.PI / 2; g.add(glassFin);
    }
    if (slug === 'derek') {   // the discovery pipeline: a large screen on a floor stand beside the desk
      const sc2 = screen(new THREE.PlaneGeometry(1.1, 0.62), 568, 320, (c, w, h) => S.monitor(c, w, h, HQ, slug, 'pipeline'), ['floor', 'equity']);
      sc2.mesh.position.set(-1.75, 1.35, 0.7); sc2.mesh.rotation.y = Math.PI - 0.55; g.add(sc2.mesh);
      addStatic(new THREE.BoxGeometry(1.14, 0.66, 0.035), MAT.bezel, L(-1.75 + 0.012, 1.35, 0.7 + 0.016, 0, -0.55, 0));
      addStatic(new THREE.CylinderGeometry(0.025, 0.025, 1.05, 10), MAT.alu, L(-1.75, 0.52, 0.72));
      addStatic(new THREE.CylinderGeometry(0.22, 0.24, 0.03, 24), MAT.darkMetal, L(-1.75, 0.015, 0.72));
    }
    if (planned) {   // the cordon: a low rail and a NOT DEPLOYED floor band in front of the desk
      for (const sx of [-1, 1]) addStatic(new THREE.CylinderGeometry(0.03, 0.03, 0.9, 10), MAT.alu, L(sx * 2.3, 0.45, 2.15));
      addStatic(new THREE.BoxGeometry(4.6, 0.05, 0.05), MAT.darkMetal, L(0, 0.88, 2.15), {noShadow: true});
      const tape = screen(new THREE.PlaneGeometry(4.4, 0.42), 1024, 98, (c, w, h) => S.cordon(c, w, h, HQ, slug), ['floor'], {transparent: true, gain: 1.3});
      tape.mesh.rotation.x = -Math.PI / 2; tape.mesh.rotation.z = Math.PI; tape.mesh.position.set(0, 0.04, 2.6); g.add(tape.mesh);
    }
    // PORTRAIT ARRIVING plate (shown until this agent's own model is present)
    const arr = screen(new THREE.PlaneGeometry(0.66, 0.66), 512, 512, (c, w, h) => S.arriving(c, w, h, HQ, slug), ['floor'], {transparent: true, gain: 1.35});
    arr.mesh.position.set(0, 1.34, -0.12); g.add(arr.mesh);
    // hit volume
    const hit = new THREE.Mesh(new THREE.BoxGeometry(5.4, 3.2, 4.6), new THREE.MeshBasicMaterial({visible: false}));
    hit.position.set(0, 1.6, -0.25); hit.userData.slug = slug; g.add(hit); pickables.push(hit);
    const W = (x, y, z) => g.localToWorld(V(x, y, z));
    desks[slug] = {seat, group: g, edgeMat, pathMat, accent, spot, accentLight, mons, board, sign, plate, arr, avatar: null,
      head: W(0, 1.2, 0.05), top: W(0, 3.2, 0.2), center: W(0, 1, 0)};
  }
  const desksExtra = {};
  HQ.SEATS.forEach(buildDesk);
  flushStatic();
  HQ.progress(0.2, 'Lighting the floor…');

  /* ── collaboration light paths (floor edges) ───────────────────── */
  const edgeGroup = new THREE.Group(); scene.add(edgeGroup);
  const edgeMats = [];
  function buildEdges() {
    while (edgeGroup.children.length) { const m = edgeGroup.children.pop(); m.geometry.dispose(); m.material.dispose(); }
    edgeMats.length = 0;
    // the newest four recorded hand-offs lead; older ones fade to a thin trace
    const list = HQ.edges().slice().sort((a, b) => (b.at || 0) - (a.at || 0)), nowS = Date.now() / 1000, win = (HQ.reads.floor.data && HQ.reads.floor.data.window_s) || 3600;
    list.forEach((e, i) => {
      const A = desks[e.from], Bd = desks[e.to]; if (!A || !Bd) return;
      const p0 = A.top.clone(), p3 = Bd.top.clone();
      const mid = p0.clone().add(p3).multiplyScalar(0.5);
      const pull = mid.clone().multiplyScalar(0.35); pull.y = 5.2 + (i % 3) * 0.45;
      const c1 = p0.clone().lerp(pull, 0.7), c2 = p3.clone().lerp(pull, 0.7);
      const curve = new THREE.CubicBezierCurve3(p0, c1, c2, p3);
      const age = e.at ? Math.max(0, nowS - e.at) : win;
      const fresh = clamp(1 - age / win, 0.15, 1);
      const mat = new THREE.ShaderMaterial({transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: {uA: {value: new THREE.Color(A.seat.accent)}, uB: {value: new THREE.Color(Bd.seat.accent)}, uT: {value: 0}, uK: {value: i < 4 ? 0.9 + fresh * 0.8 : 0.07 + fresh * 0.08}, uFlow: {value: reduced || i >= 4 ? 0 : 1}},
        vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.); }',
        fragmentShader: 'uniform vec3 uA; uniform vec3 uB; uniform float uT; uniform float uK; uniform float uFlow; varying vec2 vUv; void main(){ vec3 c = mix(uA, uB, vUv.x); float base = 0.28; float p = fract(vUv.x * 2.0 - uT * 0.35); float pulse = uFlow * smoothstep(0.0, 0.08, p) * (1.0 - smoothstep(0.08, 0.22, p)); float edge = 1.0 - abs(vUv.y - 0.5) * 2.0; gl_FragColor = vec4(c * (base + pulse * 1.8) * uK * edge, 1.0); }'});
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, i < 4 ? 0.026 + Math.min(4, e.count) * 0.004 : 0.008, 8, false), mat);
      tube.layers.set(LAYER_NO_REFLECT);
      edgeGroup.add(tube); edgeMats.push(mat);
    });
  }

  /* ── avatars: each agent's own licensed model ──────────────────── */
  const avatars = [];
  const THIGH = ['Bip01 L Thigh', 'mixamorigLeftUpLeg', 'LeftUpperLeg', 'LeftUpLeg', 'J_Bip_L_UpperLeg', 'thigh_l'];
  const CALF = ['Bip01 L Calf', 'mixamorigLeftLeg', 'LeftLowerLeg', 'LeftLeg', 'J_Bip_L_LowerLeg', 'calf_l'];
  const FOOT = ['Bip01 L Foot', 'mixamorigLeftFoot', 'LeftFoot', 'J_Bip_L_Foot', 'foot_l'];
  const HAND = ['Bip01 L Hand', 'mixamorigLeftHand', 'LeftHand', 'J_Bip_L_Hand', 'hand_l'];
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  function findBone(root, names, side) {
    const want = names.map((n) => norm(side === 'R' ? n.replace(/\bL\b/, 'R').replace('Left', 'Right').replace('_L_', '_R_').replace(/_l$/, '_r') : n));
    let hit = null;
    root.traverse((o) => { if (!hit && o.isBone && want.includes(norm(o.name))) hit = o; });
    return hit;
  }
  function aim(bone, child, want) {
    bone.updateMatrixWorld(true);
    const pa = bone.getWorldPosition(V(0, 0, 0)), pb = child.getWorldPosition(V(0, 0, 0));
    const d = pb.sub(pa).normalize();
    const qw = new THREE.Quaternion().setFromUnitVectors(d, want.clone().normalize());
    const bw = bone.getWorldQuaternion(new THREE.Quaternion()), pw = bone.parent.getWorldQuaternion(new THREE.Quaternion());
    bone.quaternion.copy(pw.invert().multiply(qw).multiply(bw));
    bone.updateMatrixWorld(true);
  }
  const qaxisArr = (a, ang) => { const s = Math.sin(ang / 2); return new THREE.Quaternion(a[0] * s, a[1] * s, a[2] * s, Math.cos(ang / 2)); };
  function skinTune(root) {
    root.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of [].concat(o.material)) {
        if (!m || !/skin|body|face|head/i.test(m.name || '')) continue;
        if ('roughness' in m && !m.roughnessMap) m.roughness = clamp(m.roughness, 0.45, 0.6);
        if ('metalness' in m) m.metalness = 0;
      }
    });
  }
  /* posture from the desk's REAL state */
  const POSTURE_OF = {WORKING_ON: 'work', WORKING: 'work', REVIEWING: 'work', CHALLENGING: 'work', HANDOFF_PENDING: 'back', WAITING_FOR_FRESH_EVIDENCE: 'back', BLOCKED_ON_MARKET_DATA: 'back',
    WAITING: 'back', BLOCKED: 'back', IDLE_NO_OPEN_WORK: 'upright', IDLE: 'upright', STALE: 'still', NOT_DEPLOYED: 'away', UNAVAILABLE: 'away'};
  const MODE_OF = {work: 'reviewing', back: 'waiting', upright: 'monitoring', still: 'unavailable', away: 'unavailable'};

  async function loadAvatar(slug, entry, loader, onBytes) {
    const gltf = await new Promise((res, rej) => loader.load(MODELS + entry.model, res, (ev) => onBytes(ev.loaded || 0, ev.total || 0), rej));
    const root = gltf.scene;
    root.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = HIGH; o.receiveShadow = false; o.frustumCulled = false;
      // alpha-tested hair cards resolve through MSAA instead of stair-stepping
      for (const m of [].concat(o.material)) if (m && m.alphaTest > 0) { m.alphaToCoverage = true; m.needsUpdate = true; }
    });
    skinTune(root);
    const hide = new Set(entry.hide_materials || []);
    if (hide.size) root.traverse((o) => { if (o.isMesh && [].concat(o.material).some((m) => hide.has(m.name))) o.visible = false; });
    if (entry.yaw) root.rotation.y = entry.yaw;
    if (entry.scale) root.scale.setScalar(entry.scale);
    const pivot = new THREE.Group(); pivot.add(root); pivot.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root), hgt = box.max.y - box.min.y;
    if (hgt < 1.3 || hgt > 2.2) { root.scale.multiplyScalar(1.76 / hgt); pivot.updateMatrixWorld(true); }
    if (entry.arms_down !== false) armsDown(root, entry.bones || {});
    pivot.updateMatrixWorld(true);
    const {bones} = resolveBones(root, entry.bones || {});
    const legs = {};
    for (const sd of ['L', 'R']) legs[sd] = {thigh: findBone(root, THIGH, sd), calf: findBone(root, CALF, sd), foot: findBone(root, FOOT, sd), hand: findBone(root, HAND, sd)};
    const joints = buildJoints(root, bones);
    // record the base (standing, arms down) pose
    const driven = [];
    for (const sd of ['L', 'R']) for (const k of ['thigh', 'calf']) if (legs[sd][k]) driven.push(legs[sd][k]);
    for (const r of ['leftUpperArm', 'leftLowerArm', 'rightUpperArm', 'rightLowerArm']) if (bones[r]) driven.push(bones[r]);
    const base = new Map(driven.map((b) => [b, b.quaternion.clone()]));
    const spineRest = {spine: joints.spine ? joints.spine.rest.slice() : null, chest: joints.chest ? joints.chest.rest.slice() : null};
    const restore = () => { for (const [b, q] of base) b.quaternion.copy(q); for (const k of ['spine', 'chest']) if (joints[k]) { joints[k].bone.quaternion.fromArray(spineRest[k]); joints[k].rest = spineRest[k].slice(); } pivot.updateMatrixWorld(true); };
    const wp = (b) => b.getWorldPosition(V(0, 0, 0));
    const ankle0 = legs.L.foot ? wp(legs.L.foot).y : 0.08;
    function lean(ang) {
      for (const [k, f] of [['spine', 0.55], ['chest', 0.45]]) {
        const j = joints[k]; if (!j) continue;
        const q = qaxisArr(j.axes.x, ang * f).multiply(new THREE.Quaternion().fromArray(spineRest[k]));
        j.bone.quaternion.copy(q);
      }
      pivot.updateMatrixWorld(true);
    }
    function seatLegs() {
      for (const sd of ['L', 'R']) {
        const lg = legs[sd]; if (!lg.thigh || !lg.calf || !lg.foot) continue;
        const sx = Math.sign(wp(lg.thigh).x) || (sd === 'L' ? 1 : -1);
        aim(lg.thigh, lg.calf, V(sx * 0.1, -0.12, 1));
      }
      // drop so the pelvis sits on the seat, then let the calves reach the floor
      const pel = bones.hips ? wp(bones.hips) : V(0, 0.95, 0);
      const dy = (SEAT_TOP + 0.085) - pel.y;
      for (const sd of ['L', 'R']) {
        const lg = legs[sd]; if (!lg.thigh || !lg.calf || !lg.foot) continue;
        const knee = wp(lg.calf), ank = wp(lg.foot), len = knee.distanceTo(ank);
        const drop = (knee.y + dy) - ankle0;
        const a = drop >= len ? 0 : Math.acos(clamp(drop / len, -1, 1));
        aim(lg.calf, lg.foot, V(0, -Math.cos(a), Math.sin(a) + 0.05));
      }
      return dy;
    }
    function armsTo(side, upDir, handTarget) {
      const up = bones[side + 'UpperArm'], lo = bones[side + 'LowerArm'], hand = legs[side === 'left' ? 'L' : 'R'].hand || (lo && lo.children.find((c) => c.isBone));
      if (!up || !lo || !hand) return;
      const sx = Math.sign(wp(up).x) || 1;
      aim(up, lo, V(sx * upDir.x, upDir.y, upDir.z));
      const el = wp(lo), tgt = V(sx * Math.abs(handTarget.x), handTarget.y, handTarget.z);
      aim(lo, hand, tgt.sub(el));
    }
    const postures = {};
    function capture(name, f) {
      restore();
      const out = f() || {};
      const q = new Map(driven.map((b) => [b, b.quaternion.clone()]));
      const rest = {spine: joints.spine ? joints.spine.bone.quaternion.toArray() : null, chest: joints.chest ? joints.chest.bone.quaternion.toArray() : null};
      postures[name] = {q, rest, pos: out.pos || V(0, 0, 0), yaw: out.yaw || 0};
    }
    // all positions are in the desk's local frame: agent at the origin, desk ahead (+z)
    const KB = 0.56;
    capture('work', () => { lean(0.16); const dy = seatLegs(); const pz = 0.0; armsTo('left', V(0.1, -0.78, 0.6), V(0.2, DESK_TOP + 0.045 - dy, KB - pz)); armsTo('right', V(0.1, -0.78, 0.6), V(0.2, DESK_TOP + 0.045 - dy, KB - pz)); return {pos: V(0, dy, pz)}; });
    capture('upright', () => { lean(0.02); const dy = seatLegs(); armsTo('left', V(0.14, -0.92, 0.32), V(0.2, SEAT_TOP + 0.2 - dy, 0.34)); armsTo('right', V(0.14, -0.92, 0.32), V(0.2, SEAT_TOP + 0.2 - dy, 0.34)); return {pos: V(0, dy, 0)}; });
    capture('back', () => { lean(-0.1); const dy = seatLegs(); armsTo('left', V(0.18, -0.95, 0.12), V(0.27, SEAT_TOP + 0.22 - dy, 0.18)); armsTo('right', V(0.18, -0.95, 0.12), V(0.27, SEAT_TOP + 0.22 - dy, 0.18)); return {pos: V(0, dy, -0.04)}; });
    capture('still', () => { lean(0.1); const dy = seatLegs(); armsTo('left', V(0.13, -0.93, 0.3), V(0.18, SEAT_TOP + 0.18 - dy, 0.32)); armsTo('right', V(0.13, -0.93, 0.3), V(0.18, SEAT_TOP + 0.18 - dy, 0.32)); return {pos: V(0, dy, 0)}; });
    capture('away', () => ({pos: V(1.75, 0, 0.15), yaw: -0.55}));
    restore();
    const ctl = new AvatarController(bones, resolveBlendshapes(root, entry.blendshapes || {}).shapes, {mode: 'unavailable', seed: 23 + avatars.length * 17, visemes: resolveVisemes(root), joints});
    const d = desks[slug];
    d.group.add(pivot);
    const av = {slug, pivot, root, ctl, joints, driven, postures, cur: null, from: null, to: null, t: 1, phase: avatars.length * 1.7, bones, legs, posture: null};
    avatars.push(av); d.avatar = av;
    d.arr.mesh.visible = false;
    setPosture(av, POSTURE_OF[HQ.desk(slug).code] || 'away', true);
    return av;
  }
  function snapshot(av) {
    return {q: new Map(av.driven.map((b) => [b, b.quaternion.clone()])),
            rest: {spine: av.joints.spine ? av.joints.spine.rest.slice() : null, chest: av.joints.chest ? av.joints.chest.rest.slice() : null},
            pos: av.pivot.position.clone(), yaw: av.pivot.rotation.y};
  }
  function setPosture(av, name, instant) {
    if (!av.postures[name] || av.posture === name) return;
    av.posture = name;
    av.from = snapshot(av); av.to = av.postures[name]; av.t = instant || reduced ? 1 : 0;
    av.ctl.setMode(MODE_OF[name]);
    applyPosture(av, av.t);
    shadowDirty = true;
  }
  const _q = new THREE.Quaternion(), _q2 = new THREE.Quaternion(), _axis = new THREE.Vector3();
  function applyPosture(av, t) {
    const e = ease(clamp(t, 0, 1)), A = av.from, Bp = av.to;
    for (const b of av.driven) { _q.copy(A.q.get(b)).slerp(Bp.q.get(b), e); b.quaternion.copy(_q); }
    for (const k of ['spine', 'chest']) {
      const j = av.joints[k]; if (!j || !A.rest[k] || !Bp.rest[k]) continue;
      _q.fromArray(A.rest[k]); _q2.fromArray(Bp.rest[k]); _q.slerp(_q2, e); j.rest = _q.toArray();
    }
    av.pivot.position.lerpVectors(A.pos, Bp.pos, e);
    av.pivot.rotation.y = A.yaw + (Bp.yaw - A.yaw) * e;
  }
  // after the controller's frame: the posture's arms and legs, plus a light
  // typing rhythm ONLY while the desk's recorded state is an active one
  function holdPosture(av, T) {
    const Bp = av.to; if (!Bp) return;
    if (av.t >= 1) for (const b of av.driven) b.quaternion.copy(Bp.q.get(b));
    else applyPosture(av, av.t);
    const d = HQ.desk(av.slug);
    if (av.posture === 'work' && d.active && !reduced) {
      for (const [r, ph] of [['leftLowerArm', 0], ['rightLowerArm', 1.9]]) {
        const j = av.joints[r]; if (!j) continue;
        const k = 0.05 * Math.max(0, Math.sin(T * 7.3 + ph + av.phase)) * (0.55 + 0.45 * Math.sin(T * 0.63 + av.phase));
        j.bone.quaternion.premultiply(_q.setFromAxisAngle(_axis.fromArray(j.axis), k));
      }
    }
  }

  async function loadAvatars() {
    let manifest = null;
    try { const r = await fetch(MODELS + 'manifest.json', {cache: 'no-cache'}); if (r.ok) manifest = await r.json(); } catch (e) { manifest = null; }
    const chars = manifest && manifest.characters || {};
    // each desk's OWN model: its manifest entry (by its own slug; the Chief
    // Allocator may be listed as 'allie'); without a manifest, its own file (CAST)
    const plan = HQ.SEATS.map((s) => {
      if (s.planned) return null;   // Adriana: seated only once the floor API serves her seat
      const e = chars[s.slug] || (s.slug === 'allocator' ? (chars.allie || chars.chief_allocator) : null);
      if (e) return e.model && e.test_asset === false ? {slug: s.slug, entry: e} : null;
      return !manifest && CAST[s.slug] ? {slug: s.slug, entry: {model: CAST[s.slug].model + '.glb'}} : null;
    }).filter(Boolean);
    if (!plan.length) { HQ.progress(1, 'Headquarters open'); if (o.onStatus) o.onStatus('characters-ready', HQ.SEATS.length); return; }
    const [{GLTFLoader}, {MeshoptDecoder}] = await Promise.all([import('./team-demo/assets/GLTFLoader.js'), import('./team-demo/assets/meshopt_decoder.module.js')]);
    const loader = new GLTFLoader(); loader.setMeshoptDecoder(MeshoptDecoder);
    let done = 0;
    for (const p of plan) {
      const name = HQ.BY_SLUG[p.slug].name;
      try {
        await loadAvatar(p.slug, p.entry, loader, (l, t) => HQ.progress(0.25 + 0.75 * (done + (t ? l / t : 0.5)) / plan.length, 'Seating ' + name + ' · ' + (done + 1) + ' of ' + plan.length));
      } catch (e) { if (o.onStatus) o.onStatus('character-failed', p.slug, e); }
      done++;
      HQ.progress(0.25 + 0.75 * done / plan.length, done < plan.length ? 'Seating the desks · ' + done + ' of ' + plan.length : 'Headquarters open');
      requestRender();
    }
    canvas.setAttribute('data-avatars', String(avatars.length));
    if (o.onStatus) o.onStatus('characters-ready', HQ.SEATS.filter((s) => !s.planned).length - avatars.length);
  }

  /* ── state → visuals ───────────────────────────────────────────── */
  const TONE_K = {work: 2.6, wait: 1.5, idle: 1.1, stale: 0.45, off: 0.14};
  let hoverSlug = null;
  function applyStates() {
    for (const slug of Object.keys(desks)) {
      const d = desks[slug], st = HQ.desk(slug), k = st.planned ? 0.25 : (TONE_K[st.tone] || 0.3), hov = hoverSlug === slug || HQ.selected() === slug;
      d.edgeMat.color.set(st.color).multiplyScalar((0.2 + k * 0.32) * (hov ? 1.8 : 1));
      d.pathMat.color.copy(d.accent).multiplyScalar(0.3 + k * 0.3);
      d.pathMat.opacity = st.tone === 'off' ? 0.15 : 0.55;
      if (d.spot) d.spot.intensity = st.tone === 'off' ? 5 : st.tone === 'stale' ? 16 : 40;
      if (d.accentLight) d.accentLight.intensity = (st.tone === 'off' ? 0.6 : 3) * (hov ? 1.8 : 1);
      if (d.avatar) setPosture(d.avatar, POSTURE_OF[st.code] || 'away');
    }
    const q = HQ.equity(), p = q.paper;
    const col = !p ? '#7a8496' : p.status === 'OK' ? '#6f9bff' : p.status === 'STALE' ? '#e8b25e' : '#7a8496';
    core.beamMat.uniforms.uColor.value.set(col);
    core.beamMat.uniforms.uK.value = !p ? 0.25 : p.status === 'OK' ? 0.55 : 0.4;
    core.orbMat.emissive.set(col); core.orbMat.emissiveIntensity = !p ? 0.25 : p.status === 'OK' ? 0.5 : 0.35;
    core.rimMat.color.set(col).multiplyScalar(p && p.status === 'OK' ? 1.35 : 0.9);
    coreLight.color.set(col); coreLight.intensity = p && p.status === 'OK' ? 3.5 : 2.5;
    if (q.changedAt && q.changedAt !== core.lastChange) { core.lastChange = q.changedAt; if (!reduced) core.pulseT = 0; core.pulse.material.color.set(col).multiplyScalar(2); }
    requestRender();
  }

  /* ── camera ────────────────────────────────────────────────────── */
  const camState = {pos: V(0, 24, 44), tgt: V(0, 1.2, -2), from: null, fromT: null, t: 1, dur: 1.8, lift: 0, mode: 'command', slug: null,
    userYaw: 0, userPitch: 0, zoom: 1, offX: 0, offY: 0, offXT: 0, offYT: 0};
  /* ONE CAMERA POSE PER COMMAND SECTION. Each is an orbit (target, distance,
   * elevation, yaw) so the user's drag and wheel act on every view alike. The
   * only automatic motion is a slow deterministic sway (off under reduced motion). */
  const POSES = {
    command:  {C: [0, 2.0, -3.4], dist: 21.5, el: 0.30, yaw: 0, portrait: {C: [0, 2.2, -2.5], dist: 26, el: 0.30}},
    floor:    {C: [0, 0.6, -0.8], dist: 23.5, el: 0.58, yaw: 0, portrait: {C: [0, 0.6, -1.2], dist: 30, el: 0.62}},
    capital:  {C: [0, 2.0, -0.6], dist: 8.6, el: 0.16, yaw: 0, portrait: {C: [0, 2.0, 0], dist: 13, el: 0.2}},
    reports:  {C: [0, 4.5, -10], dist: 14, el: 0.10, yaw: 0, portrait: {C: [0, 4.5, -8], dist: 22, el: 0.12}}
  };
  function orbit(name, T) {
    const portrait = camera.aspect < 1;
    const P = (portrait && POSES[name].portrait) ? Object.assign({}, POSES[name], POSES[name].portrait) : POSES[name];
    const C = V(P.C[0], P.C[1], P.C[2]);
    const dist = P.dist * camState.zoom, el = P.el + camState.userPitch + (reduced ? 0 : 0.008 * Math.sin(T * 0.07));
    const yaw = (P.yaw || 0) + camState.userYaw + (reduced ? 0 : 0.05 * Math.sin(T * 0.043));
    return {pos: V(C.x + dist * Math.sin(yaw) * Math.cos(el), C.y + dist * Math.sin(el), C.z + dist * Math.cos(yaw) * Math.cos(el)), tgt: C};
  }
  function focusPose(slug, T) {
    const d = desks[slug], g = d.group;
    const side = g.position.x >= -0.5 ? 1 : -1;
    const sway = reduced ? 0 : 0.04 * Math.sin(T * 0.11);
    // a three-quarter view from the open side, close enough to read the person and the desk
    // in front of the desk and to its open side: the person, the desk and the
    // zone board behind them (their recorded work) in one frame
    const yaw = (camState.userYaw * 0.6) + side * 0.5 + sway;
    const dist = (camera.aspect < 1 ? 6.2 : 5.1) * camState.zoom;
    const local = V(Math.sin(yaw) * dist, 2.35 + camState.userPitch * 3, 0.4 + Math.cos(yaw) * dist);
    return {pos: g.localToWorld(local), tgt: g.localToWorld(V(0, 1.3, -0.95))};
  }
  function wallPose(id) {
    const w = WALL.find((x) => x.id === id) || WALL[1];
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    // fit the wall into the part of the screen the HUD leaves clear
    const freeA = Math.max(0.3, (size.w - insets.left - insets.right) / Math.max(1, size.h - insets.top - insets.bottom));
    const freeH = Math.max(0.3, (size.h - insets.top - insets.bottom) / Math.max(1, size.h));
    const dist = Math.max(w.w / (2 * half * freeA * freeH), w.h / (2 * half * freeH)) * 1.08;
    const c = w.mesh.getWorldPosition(V(0, 0, 0)), n = V(0, 0, 1).applyQuaternion(w.mesh.getWorldQuaternion(new THREE.Quaternion()));
    return {pos: c.clone().addScaledVector(n, dist), tgt: c};
  }
  function desired(T) {
    if (camState.debug) return camState.debug;
    if (camState.mode === 'wall') return wallPose(camState.slug);
    if (camState.mode === 'focus' && camState.slug) return focusPose(camState.slug, T);
    return orbit(POSES[camState.mode] ? camState.mode : 'command', T);
  }
  function goTo(mode, slug, dur, lift) {
    camState.from = {pos: camState.pos.clone(), tgt: camState.tgt.clone()};
    camState.mode = mode; camState.slug = slug; camState.t = reduced ? 1 : 0; camState.dur = dur; camState.lift = lift || 0;
    camState.userYaw = 0; camState.userPitch = 0; camState.zoom = 1;
    requestRender();
  }
  function stepCamera(dt, T) {
    const want = desired(T);
    if (camState.t < 1) {
      camState.t = Math.min(1, camState.t + dt / camState.dur);
      const e = ease(camState.t);
      camState.pos.lerpVectors(camState.from.pos, want.pos, e); camState.pos.y += Math.sin(Math.PI * e) * camState.lift;
      camState.tgt.lerpVectors(camState.from.tgt, want.tgt, e);
    } else { camState.pos.copy(want.pos); camState.tgt.copy(want.tgt); }
    camera.position.copy(camState.pos); camera.lookAt(camState.tgt);
    // keep the focused desk clear of the detail panel
    camState.offX = damp(camState.offX, camState.offXT, 4, dt); camState.offY = damp(camState.offY, camState.offYT, 4, dt);
    const w = size.w, h = size.h;
    if (Math.abs(camState.offX) > 0.5 || Math.abs(camState.offY) > 0.5) camera.setViewOffset(w, h, camState.offX, camState.offY, w, h);
    else if (camera.view && camera.view.enabled) camera.clearViewOffset();
    camera.updateMatrixWorld();
  }
  // keep the subject clear of the HUD: hq.js reports how many pixels each
  // overlay covers on the left / right / top / bottom, and the camera's view
  // is offset so the subject sits in the clear part of the screen
  const insets = {left: 0, right: 0, top: 0, bottom: 0};
  function panelOffsets() {
    camState.offXT = (insets.right - insets.left) / 2;
    camState.offYT = (insets.bottom - insets.top) / 2;
  }

  /* ── post-processing (desktop): HDR bloom + vignette, ACES at the end ─ */
  let post = null;
  if (HIGH) {
    const vs = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }';
    const qScene = new THREE.Scene(), qCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1), quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2)); quad.frustumCulled = false; qScene.add(quad);
    const down = new THREE.ShaderMaterial({uniforms: {src: {value: null}, texel: {value: new THREE.Vector2()}, pre: {value: 0}, thr: {value: 1.4}}, vertexShader: vs, depthTest: false, depthWrite: false,
      fragmentShader: 'uniform sampler2D src; uniform vec2 texel; uniform float pre; uniform float thr; varying vec2 vUv;\n' +
        'vec3 s(vec2 uv){ vec3 c = texture2D(src, uv).rgb; if (pre > 0.5) { float b = max(c.r, max(c.g, c.b)); float k = 0.5; float soft = clamp(b - thr + k, 0.0, 2.0 * k); soft = soft * soft / (4.0 * k + 1e-4); c *= max(soft, b - thr) / max(b, 1e-4); c = min(c, vec3(24.0)); } return c; }\n' +
        'void main(){ vec2 o = texel; vec3 c = s(vUv) * 4.0 + s(vUv - o) + s(vUv + o) + s(vUv + vec2(o.x, -o.y)) + s(vUv - vec2(o.x, -o.y)); gl_FragColor = vec4(c / 8.0, 1.0); }'});
    const up = new THREE.ShaderMaterial({uniforms: {src: {value: null}, base: {value: null}, texel: {value: new THREE.Vector2()}}, vertexShader: vs, depthTest: false, depthWrite: false,
      fragmentShader: 'uniform sampler2D src; uniform sampler2D base; uniform vec2 texel; varying vec2 vUv;\n' +
        'void main(){ vec2 o = texel; vec3 c = texture2D(src, vUv + vec2(-o.x * 2.0, 0.0)).rgb + texture2D(src, vUv + vec2(-o.x, o.y)).rgb * 2.0 + texture2D(src, vUv + vec2(0.0, o.y * 2.0)).rgb + texture2D(src, vUv + vec2(o.x, o.y)).rgb * 2.0 + texture2D(src, vUv + vec2(o.x * 2.0, 0.0)).rgb + texture2D(src, vUv + vec2(o.x, -o.y)).rgb * 2.0 + texture2D(src, vUv + vec2(0.0, -o.y * 2.0)).rgb + texture2D(src, vUv + vec2(-o.x, -o.y)).rgb * 2.0; gl_FragColor = vec4(c / 12.0 + texture2D(base, vUv).rgb, 1.0); }'});
    const comp = new THREE.ShaderMaterial({uniforms: {tScene: {value: null}, tBloom: {value: null}, k: {value: 0.16}}, vertexShader: vs, depthTest: false, depthWrite: false,
      fragmentShader: 'uniform sampler2D tScene; uniform sampler2D tBloom; uniform float k; varying vec2 vUv;\n' +
        'void main(){ vec3 c = texture2D(tScene, vUv).rgb + texture2D(tBloom, vUv).rgb * k; vec2 d = vUv - 0.5; float v = 1.0 - smoothstep(0.35, 0.95, length(d * vec2(1.15, 1.0))); c *= mix(0.62, 1.0, v); gl_FragColor = vec4(c, 1.0);\n#include <tonemapping_fragment>\n#include <colorspace_fragment>\n}'});
    const LV = 5, rtOpt = {type: THREE.HalfFloatType, depthBuffer: false};
    post = {main: new THREE.WebGLRenderTarget(4, 4, {type: THREE.HalfFloatType, samples: 4}), down: [], up: [], comp,
      setSize(w, h) {
        this.main.setSize(w, h);
        for (let i = 0; i < LV; i++) {
          const ww = Math.max(1, w >> (i + 1)), hh = Math.max(1, h >> (i + 1));
          if (!this.down[i]) { this.down[i] = new THREE.WebGLRenderTarget(ww, hh, rtOpt); this.up[i] = new THREE.WebGLRenderTarget(ww, hh, rtOpt); }
          this.down[i].setSize(ww, hh); this.up[i].setSize(ww, hh);
        }
      },
      render() {
        renderer.setRenderTarget(this.main); renderer.render(scene, camera);
        let src = this.main;
        quad.material = down;
        for (let i = 0; i < LV; i++) {
          down.uniforms.src.value = src.texture; down.uniforms.texel.value.set(1 / src.width, 1 / src.height); down.uniforms.pre.value = i === 0 ? 1 : 0;
          renderer.setRenderTarget(this.down[i]); renderer.render(qScene, qCam); src = this.down[i];
        }
        quad.material = up;
        let prev = this.down[LV - 1];
        for (let i = LV - 2; i >= 0; i--) {
          up.uniforms.src.value = prev.texture; up.uniforms.base.value = this.down[i].texture; up.uniforms.texel.value.set(1 / prev.width, 1 / prev.height);
          renderer.setRenderTarget(this.up[i]); renderer.render(qScene, qCam); prev = this.up[i];
        }
        quad.material = comp; comp.uniforms.tScene.value = this.main.texture; comp.uniforms.tBloom.value = prev.texture;
        renderer.setRenderTarget(null); renderer.render(qScene, qCam);
      }};
  }

  /* ── size ──────────────────────────────────────────────────────── */
  const size = {w: 1, h: 1};
  function resize() {
    const r = stage.getBoundingClientRect(), w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    size.w = w; size.h = h;
    renderer.setPixelRatio(dpr); renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.fov = w / h < 1 ? 52 : 40; camera.updateProjectionMatrix();
    const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (post) post.setSize(pw, ph);
    if (refl) { const rw = Math.max(256, Math.round(pw * 0.5)), rh = Math.max(256, Math.round(ph * 0.5)); refl.rt.setSize(rw, rh); if (refl.texel) refl.texel.value.set(1 / rw, 1 / rh); }
    panelOffsets();
    requestRender();
  }
  new ResizeObserver(resize).observe(stage);

  /* ── interaction ───────────────────────────────────────────────── */
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  function pick(cx, cy) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    return hit ? hit.object.userData.slug : null;
  }
  let drag = null;
  canvas.addEventListener('pointerdown', (e) => { drag = {x: e.clientX, y: e.clientY, moved: 0, id: e.pointerId}; });
  canvas.addEventListener('pointermove', (e) => {
    if (drag && e.buttons) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy); drag.x = e.clientX; drag.y = e.clientY;
      if (drag.moved > 6) { camState.userYaw = clamp(camState.userYaw - dx * 0.003, -0.75, 0.75); camState.userPitch = clamp(camState.userPitch + dy * 0.0022, -0.22, 0.3); hideHint(); requestRender(); }
      return;
    }
    if (e.pointerType !== 'mouse') return;
    const slug = pick(e.clientX, e.clientY);
    if (slug !== hoverSlug) { hoverSlug = slug; applyStates(); canvas.style.cursor = slug ? 'pointer' : 'grab'; }
    if (o.onHover) o.onHover(slug, e.clientX, e.clientY);
  });
  canvas.addEventListener('pointerleave', () => { if (hoverSlug) { hoverSlug = null; applyStates(); } if (o.onHover) o.onHover(null); });
  canvas.addEventListener('pointerup', (e) => {
    const wasDrag = drag && drag.moved > 6; drag = null;
    if (wasDrag) return;
    const slug = pick(e.clientX, e.clientY);
    if (slug) hideHint();
    // floor.js decides: a first click focuses, a second opens the workspace,
    // a click on empty floor returns to the overview
    if (o.onPick) o.onPick(slug, {again: !!slug && slug === focused});
  });
  canvas.addEventListener('wheel', (e) => { camState.zoom = clamp(camState.zoom + e.deltaY * 0.0008, 0.72, 1.3); requestRender(); }, {passive: true});
  function hideHint() { const h = document.getElementById('hq-hint'); if (h) h.classList.add('gone'); }

  /* ── data subscriptions ────────────────────────────────────────── */
  let paused = false;
  HQ.subscribe((kind) => {
    if (kind === 'floor') { redraw('floor'); buildEdges(); applyStates(); }
    else if (kind === 'equity') { redraw('equity'); applyStates(); }
    else if (kind === 'coverage') redraw('coverage');
    else if (kind === 'release') redraw('release');
    else if (kind === 'derek') redraw('derek');
    else if (kind === 'detail:xavier') redraw('xavier');
  });

  /* ── the loop ──────────────────────────────────────────────────── */
  let raf = 0, last = 0, T = 0, needs = true, ema = 16, slowFor = 0, frames = 0, fpsT = performance.now();
  let shadowDirty = true, lastCam = new Array(16).fill(0), lastProj = new Array(16).fill(0), forceDirty = true, focused = null;
  const perf = {fps: [], drawCalls: 0, triangles: 0, tier: HIGH ? 'high' : 'phone', dpr, reflections: !!refl, bloom: !!post};
  function requestRender() { needs = true; if (!raf && !paused && !document.hidden) raf = requestAnimationFrame(frame); }
  function frame(now) {
    raf = 0;
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60; last = now; T += dt;
    stepCamera(dt, T);
    // avatars: blink / breathe / glance (cc_avatar), then the recorded posture
    for (const av of avatars) {
      if (av.t < 1) av.t = Math.min(1, av.t + dt / 1.3);
      av.ctl.update(dt); holdPosture(av, T);
    }
    edgeGroup.visible = camState.mode === 'command' || camState.mode === 'floor' || camState.t < 0.5;
    if (!reduced) {
      ring.sc.tex.offset.x = (ring.sc.tex.offset.x + dt * 0.0065) % 1;
      edgeMats.forEach((m) => { m.uniforms.uT.value = T; });
      core.cage.rotation.y += dt * 0.12; core.orb.rotation.y -= dt * 0.08;
      core.band.mesh.rotation.y += dt * 0.035;
      if (desksExtra.scout) desksExtra.scout.spin.rotation.y += dt * 0.15;
    }
    if (core.pulseT < 1) {
      core.pulseT = Math.min(1, core.pulseT + dt / 2.6);
      const s = 3.4 + core.pulseT * 6.5; core.pulse.scale.setScalar(s); core.pulse.material.opacity = (1 - core.pulseT) * 0.45;
    }
    if (shadowDirty && HIGH) { renderer.shadowMap.needsUpdate = true; shadowDirty = false; }
    renderer.info.autoReset = false; renderer.info.reset();
    if (refl) updateReflection();
    if (post) post.render(); else renderer.render(scene, camera);
    let camDirty = false;
    for (let k = 0; k < 16; k++) if (Math.abs(camera.matrixWorld.elements[k] - lastCam[k]) > 1e-6 || Math.abs(camera.projectionMatrix.elements[k] - lastProj[k]) > 1e-9) { camDirty = true; break; }
    if (camDirty) { lastCam = camera.matrixWorld.elements.slice(); lastProj = camera.projectionMatrix.elements.slice(); }
    if (o.onFrame) o.onFrame({camera, cameraDirty: camDirty || forceDirty, focused});
    forceDirty = false;
    perf.drawCalls = renderer.info.render.calls; perf.triangles = renderer.info.render.triangles;
    // adaptive quality: sustained slow frames step down reflections, bloom, then resolution
    ema = ema * 0.92 + dt * 1000 * 0.08;
    if (!window.__hqFixedQuality) {
      slowFor = ema > 26 ? slowFor + dt : 0;
      if (slowFor > 2.5) {
        slowFor = 0; ema = 16;
        if (refl && refl.strength > 0) { refl.strength = 0; if (refl.uniform) refl.uniform.value = 0; refl = null; perf.reflections = false; }
        else if (dpr > 1) { dpr = Math.max(1, dpr - 0.25); perf.dpr = dpr; resize(); }
        else if (post) { post = null; perf.bloom = false; }
      }
    }
    frames++;
    if (now - fpsT >= 1000) { perf.fps.push(Math.round(frames * 1000 / (now - fpsT))); if (perf.fps.length > 30) perf.fps.shift(); frames = 0; fpsT = now; perf.frameMs = Math.round(ema * 10) / 10; }
    needs = false;
    const animating = !reduced || camState.t < 1 || avatars.some((a) => a.t < 1) || core.pulseT < 1;
    if (animating && !paused && !document.hidden) raf = requestAnimationFrame(frame);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { last = 0; requestRender(); } });

  // first paint: screens, states, then the establishing move
  resize();
  redraw();
  applyStates();
  buildEdges();
  canvas.setAttribute('data-ready', '1');
  const startMode = POSES[o.view] ? o.view : 'command';
  if (!reduced) { camState.pos.set(0, 19, 40); camState.tgt.set(0, 2.5, -4); goTo(startMode, null, 3.4, 0); }
  else { camState.mode = startMode; const ov = orbit(startMode, 0); camState.pos.copy(ov.pos); camState.tgt.copy(ov.tgt); }
  requestRender();
  HQ.progress(0.25, 'Seating the desks…');
  const mounting = loadAvatars().then(() => { shadowDirty = true; requestRender(); }, (e) => { if (o.onStatus) o.onStatus('character-failed', null, e); });

  /* ── the API floor.js drives ───────────────────────────────────── */
  const tmpV = new THREE.Vector3();
  const toScreen = (v) => { tmpV.copy(v).project(camera); return {x: (tmpV.x * 0.5 + 0.5) * size.w, y: (-tmpV.y * 0.5 + 0.5) * size.h, behind: tmpV.z > 1}; };
  const api = {
    setData(p, meta) { HQ.setFloor(p, meta); },
    focus(slug) {
      if (!desks[slug]) return;
      focused = slug; HQ.setSelected(slug);
      goTo('focus', slug, 1.7, camState.mode === 'focus' ? 0.8 : 0.4);
      panelOffsets(); applyStates();
    },
    focusWall(id) { focused = 'wall:' + id; HQ.setSelected(null); goTo('wall', id, 1.5, 0.2); panelOffsets(); applyStates(); },
    view(name) { if (!POSES[name]) return; focused = null; HQ.setSelected(null); goTo(name, null, 1.9, camState.mode === 'focus' ? 0.5 : 0.2); panelOffsets(); applyStates(); },
    resetView() { focused = null; HQ.setSelected(null); goTo('command', null, 1.9, 0.6); panelOffsets(); applyStates(); },
    get mode() { return camState.mode; },
    tickClocks() { redraw('clock'); },
    wallQuads() {
      const out = {};
      for (const w of WALL) out[w.id] = [[-1, 1], [1, 1], [1, -1], [-1, -1]].map(([sx, sy]) => toScreen(w.mesh.localToWorld(V(sx * w.w / 2, sy * w.h / 2, 0.02))));
      return out;
    },
    anchorOf(slug) { const d = desks[slug]; return d ? toScreen(d.top) : null; },
    requestRender, mounting,
    get focused() { return focused; },
    setInsets(v) { Object.assign(insets, v || {}); panelOffsets(); requestRender(); },
    setPaused(v) { paused = !!v; if (!paused) { forceDirty = true; resize(); requestRender(); } },
    stats() { return Object.assign({avatars: avatars.length, edges: edgeMats.length}, perf); },
    dispose() { cancelAnimationFrame(raf); renderer.dispose(); }
  };
  // inspection hooks (software-GL renders): complete running transitions now / place the camera
  const settle = () => { panelOffsets(); camState.offX = camState.offXT; camState.offY = camState.offYT; camState.t = 1; for (const av of avatars) { av.t = 1; applyPosture(av, 1); } forceDirty = true; requestRender(); };
  const look = (slug, lp, lt) => { const g = desks[slug].group; camState.debug = lp ? {pos: g.localToWorld(V(...lp)), tgt: g.localToWorld(V(...lt))} : null; camState.t = 1; requestRender(); };
  window.__hqScene = {camState, look, settle, scene, camera, renderer, desks, avatars, perf, model: HQ};
  return api;
}
