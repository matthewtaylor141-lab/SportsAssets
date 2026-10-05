/*
 * BETTOR COMMAND - THE THREE AGENT CHARACTERS (original work).
 *
 * Derek, Xavier and Audrey are ORIGINAL stylised characters built here from
 * three.js primitives (lathe, capsule, sphere, tube and extruded shapes) with
 * procedural canvas textures. No model, texture, likeness or asset from any
 * other work is used. Each is an articulated rig: hips, spine, chest, neck,
 * head (jaw, eyes, lids, brows) and two arms solved by two-bone IK to named
 * poses, so gestures, posture changes, blinking, breathing and head movement
 * are all procedural and restrained.
 *
 * THE POSE IS NEVER DECORATION. The page sets the mode through a `cc:mode`
 * event whose value comes from the agent's persisted status record:
 *   monitoring  - IDLE / DECISION_RECORDED, heartbeat current
 *   reviewing   - EVALUATING / RECOVERING (or a chat answer being prepared)
 *   waiting     - WAITING_FOR_EVIDENCE / WAITING_FOR_PROVIDER / BLOCKED
 *   speaking    - Audrey while a chat reply renders
 *   unavailable - stale heartbeat, FAILED, or the status read failed: the
 *                 character stands still, eyes lowered, lights dimmed, and
 *                 the screens behind show NO HEARTBEAT.
 *
 * ARCHER AND SCOUT (migration 217) stand at their own desks: Archer's is an
 * institutional execution desk (order-book / depth screens), Scout's a
 * research desk (information, sports-feed and weather / data panels). Their
 * screens are drawn ONLY from the desk record the page passes in
 * (`opts.desk` and the `cc:desk` event, read by the page from the agent's
 * own endpoint); this module reads no data itself, and a value the
 * record does not carry is drawn as NOT MEASURED / NO RECORD, never invented.
 *
 * prefers-reduced-motion: one static frame per change, no animation loop.
 * This module is loaded only after the page's capability checks and is
 * independent of the data: if it fails, the 2D portrait stays and every
 * record on the page is unaffected.
 */
import * as THREE from './three.module.min.js';

const V3 = THREE.Vector3;
const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const damp = (a, b, smooth, dt) => lerp(a, b, 1 - Math.exp(-dt / Math.max(1e-4, smooth)));
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

// ════════════════════════════════════════════════════════════════════
// PERSONAS
// ════════════════════════════════════════════════════════════════════
const PERSONAS = {
  derek: {
    accent: 0xf2a541, scale: 1.0, seed: 11,
    skin: 0xb3835f, hair: 0x1b1310, iris: 0x4a2f1c, lips: 0x9a5a4b, brow: 0x1b1310,
    jacket: 0x1b2a40, lapel: 0x142033, shirt: 0xf1efe8, trousers: 0x23262d, shoes: 0xe6e2da,
    top: 'open', hair_style: 'crop', beard: 'stubble', glasses: false, female: false, tie: null,
    build: {sh: 0.205, chest: 0.198, waist: 0.168, hem: 0.176},
    tempo: 1.3, smooth: 0.16, blink: [2.0, 4.2], weightEvery: [3.5, 6.5], breathHz: 0.3,
    expr: {smile: 0.55, brow: 0.004},
    poses: {
      monitoring: {L: 'HIP', R: 'PHONE'}, reviewing: {L: 'SUPPORT', R: 'CHIN'},
      waiting: {L: 'CROSS_UNDER', R: 'CROSS_OVER'}, speaking: {L: 'OPEN', R: 'EXPLAIN'},
      unavailable: {L: 'REST', R: 'REST'}},
    gestures: {
      monitoring: [{R: 'POINT', dur: 1.9, look: [0.55, 0.1]}, {L: 'POCKET', dur: 3.5}, {R: 'PHONE', dur: 2.5, look: [-0.05, -0.42]}],
      reviewing: [{look: [0.2, -0.1], dur: 1.6}],
      waiting: [{L: 'WATCH', R: 'REST', dur: 1.8, look: [0.12, -0.45]}],
      speaking: [{R: 'POINT', dur: 1.2, look: [0.1, 0.02]}]},
    gestureEvery: [3.5, 6.5],
  },
  xavier: {
    accent: 0x41d3e2, scale: 1.01, seed: 23,
    skin: 0xd6ab8b, hair: 0xb4b4ba, iris: 0x3f5563, lips: 0x9c6457, brow: 0x8f8f96,
    jacket: 0x252c3a, lapel: 0x1b212c, shirt: 0xe6edf6, trousers: 0x252c3a, shoes: 0x1a1512,
    top: 'tie', hair_style: 'swept', beard: 'full', glasses: true, female: false, tie: 0x1d6f79,
    vest: 0x222834, pinstripe: true,
    build: {sh: 0.21, chest: 0.2, waist: 0.172, hem: 0.18},
    tempo: 0.75, smooth: 0.45, blink: [3.0, 6.0], weightEvery: [7, 12], breathHz: 0.22,
    expr: {smile: 0.15, brow: -0.001},
    poses: {
      monitoring: {L: 'CLASP', R: 'CLASP'}, reviewing: {L: 'STEEPLE', R: 'STEEPLE'},
      waiting: {L: 'POCKET', R: 'REST'}, speaking: {L: 'CLASP', R: 'EXPLAIN'},
      unavailable: {L: 'REST', R: 'REST'}},
    gestures: {
      monitoring: [{R: 'GLASSES', L: 'REST', dur: 1.6, look: [0, 0.02]}, {look: [0.4, 0.05], dur: 3.0}],
      reviewing: [{look: [-0.15, -0.2], dur: 2.5}],
      waiting: [{L: 'WATCH', R: 'REST', dur: 2.0, look: [0.12, -0.45]}],
      speaking: []},
    gestureEvery: [8, 13],
  },
  audrey: {
    // fashion-forward evening styling: a fitted satin wrap dress with a V
    // neckline and an asymmetric draped sash, statement earrings, polished
    // glossy waves, defined eyes and lips. Non-explicit by construction.
    accent: 0xb39bff, scale: 0.965, seed: 37,
    skin: 0xc98d6a, hair: 0x24150f, hair2: 0x6a3d27, iris: 0x5a3417, lips: 0x9c2c44, brow: 0x24150f,
    jacket: 0x0f4a46, lapel: 0x14605a, shirt: 0x0f4a46, trousers: 0x0f4a46, shoes: 0x16151a,
    top: 'evening', hair_style: 'long', beard: null, glasses: false, female: true, tie: null,
    makeup: true, beauty: true,
    build: {sh: 0.186, chest: 0.18, waist: 0.136, hem: 0.16},
    tempo: 1.0, smooth: 0.26, blink: [2.4, 4.8], weightEvery: [5, 9], breathHz: 0.25,
    expr: {smile: 0.45, brow: 0.003},
    poses: {
      monitoring: {L: 'TABLET', R: 'REST'}, reviewing: {L: 'TABLET_UP', R: 'SWIPE'},
      waiting: {L: 'HIP', R: 'REST'}, speaking: {L: 'TABLET', R: 'EXPLAIN'},
      unavailable: {L: 'REST', R: 'REST'}},
    gestures: {
      monitoring: [{R: 'SWIPE', L: 'TABLET_UP', dur: 2.2, look: [0.05, -0.38]}, {look: [0.35, 0.05], dur: 2.5}],
      reviewing: [{look: [0.15, -0.3], dur: 1.5}],
      waiting: [{L: 'CROSS_UNDER', R: 'CROSS_OVER', dur: 4.0}],
      speaking: [{R: 'OPEN', dur: 1.4}, {L: 'OPEN', R: 'EXPLAIN', dur: 1.6}]},
    gestureEvery: [4, 7.5],
  },
  archer: {
    // institutional execution: charcoal suit, steel-blue accent, precise
    accent: 0x5fb7ff, scale: 1.0, seed: 41, desk: 'execution',
    skin: 0x8d5a3b, hair: 0x15100d, iris: 0x2b1a10, lips: 0x80493d, brow: 0x15100d,
    jacket: 0x161a21, lapel: 0x101318, shirt: 0xdfe6ee, trousers: 0x161a21, shoes: 0x0d0c0c,
    top: 'tie', hair_style: 'crop', beard: null, glasses: false, female: false, tie: 0x24425f,
    build: {sh: 0.207, chest: 0.199, waist: 0.17, hem: 0.178},
    tempo: 1.15, smooth: 0.2, blink: [2.6, 5.0], weightEvery: [6, 10], breathHz: 0.24,
    expr: {smile: 0.1, brow: -0.0005},
    poses: {
      monitoring: {L: 'POCKET', R: 'REST'}, reviewing: {L: 'SUPPORT', R: 'CHIN'},
      waiting: {L: 'CLASP', R: 'CLASP'}, speaking: {L: 'REST', R: 'EXPLAIN'},
      unavailable: {L: 'REST', R: 'REST'}},
    gestures: {
      monitoring: [{R: 'POINT', dur: 1.2, look: [0.45, 0.05]}, {L: 'WATCH', R: 'REST', dur: 1.4, look: [0.12, -0.45]}],
      reviewing: [{look: [0.3, -0.15], dur: 1.2}],
      waiting: [{look: [0.4, 0.02], dur: 2.0}],
      speaking: [{R: 'EXPLAIN', dur: 1.0}]},
    gestureEvery: [4, 7],
  },
  scout: {
    // field researcher: open collar, glasses, green accent, curious
    accent: 0x6fd39a, scale: 0.99, seed: 53, desk: 'research',
    skin: 0xe0b48f, hair: 0x6b4a2b, iris: 0x3b5a3a, lips: 0xa06455, brow: 0x5a3d22,
    jacket: 0x2f3a2c, lapel: 0x263022, shirt: 0xe9e4d6, trousers: 0x3a3f46, shoes: 0x2a1d14,
    top: 'open', hair_style: 'swept', beard: 'stubble', glasses: true, female: false, tie: null,
    build: {sh: 0.2, chest: 0.193, waist: 0.166, hem: 0.174},
    tempo: 1.05, smooth: 0.24, blink: [2.2, 4.4], weightEvery: [4, 8], breathHz: 0.27,
    expr: {smile: 0.4, brow: 0.003},
    poses: {
      monitoring: {L: 'HIP', R: 'CHIN'}, reviewing: {L: 'SUPPORT', R: 'CHIN'},
      waiting: {L: 'CROSS_UNDER', R: 'CROSS_OVER'}, speaking: {L: 'OPEN', R: 'EXPLAIN'},
      unavailable: {L: 'REST', R: 'REST'}},
    gestures: {
      monitoring: [{R: 'GLASSES', L: 'REST', dur: 1.5, look: [0, 0.02]}, {R: 'POINT', dur: 1.6, look: [-0.5, 0.08]}],
      reviewing: [{look: [-0.2, -0.25], dur: 2.0}],
      waiting: [{L: 'WATCH', R: 'REST', dur: 1.8, look: [0.12, -0.45]}],
      speaking: [{R: 'OPEN', dur: 1.3}]},
    gestureEvery: [3.5, 6.5],
  },
};

// ════════════════════════════════════════════════════════════════════
// MATERIALS AND PROCEDURAL TEXTURES
// ════════════════════════════════════════════════════════════════════
function canvasTex(w, h, draw, repeat) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat[0], repeat[1]); }
  return t;
}
function weaveTex(stripe) {
  return canvasTex(128, 128, (g, w, h) => {
    g.fillStyle = '#ffffff'; g.fillRect(0, 0, w, h);
    const r = rng(7);
    for (let i = 0; i < 1400; i++) { g.fillStyle = 'rgba(0,0,0,' + (0.02 + r() * 0.04) + ')'; g.fillRect(r() * w, r() * h, 1 + r() * 2, 1); }
    if (stripe) { g.fillStyle = 'rgba(255,255,255,0.9)'; for (let x = 6; x < w; x += 16) g.fillRect(x, 0, 1, h); g.fillStyle = 'rgba(0,0,0,0.06)'; for (let x = 7; x < w; x += 16) g.fillRect(x, 0, 1, h); }
  }, [6, 6]);
}
function fabric(color, opts = {}) {
  return new THREE.MeshPhysicalMaterial({color, roughness: opts.rough ?? 0.74, metalness: 0,
    sheen: opts.sheen ?? 0.7, sheenRoughness: 0.65, sheenColor: new THREE.Color(color).lerp(new THREE.Color(0xffffff), 0.35),
    map: opts.map || null, bumpMap: opts.bump || null, bumpScale: 0.6});
}
function skinMat(color) {
  return new THREE.MeshPhysicalMaterial({color, roughness: 0.5, metalness: 0, sheen: 0.35,
    sheenRoughness: 0.5, sheenColor: new THREE.Color(0xff8a70), clearcoat: 0.06, clearcoatRoughness: 0.6});
}
const std = (color, rough = 0.6, metal = 0, extra = {}) => new THREE.MeshStandardMaterial(Object.assign({color, roughness: rough, metalness: metal}, extra));

// ════════════════════════════════════════════════════════════════════
// GEOMETRY HELPERS
// ════════════════════════════════════════════════════════════════════
const G = {};
function geo() {
  if (G.sphere) return G;
  G.sphere = new THREE.SphereGeometry(1, 36, 26);
  G.sphereLo = new THREE.SphereGeometry(1, 18, 12);
  G.cyl = new THREE.CylinderGeometry(1, 1, 1, 28, 1);
  G.box = new THREE.BoxGeometry(1, 1, 1);
  return G;
}
function add(parent, geometry, material, p, r, s, shadow = true) {
  const m = new THREE.Mesh(geometry, material);
  if (p) m.position.set(p[0], p[1], p[2]);
  if (r) m.rotation.set(r[0], r[1], r[2]);
  if (s) (typeof s === 'number') ? m.scale.setScalar(s) : m.scale.set(s[0], s[1], s[2]);
  m.castShadow = shadow; m.receiveShadow = shadow;
  parent.add(m);
  return m;
}
function group(parent, p) { const g = new THREE.Group(); if (p) g.position.set(p[0], p[1], p[2]); parent.add(g); return g; }
function capsule(r, len) { return new THREE.CapsuleGeometry(r, len, 6, 16); }
function tube(points, radius, seg = 24, radial = 8) {
  return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map((p) => new V3(p[0], p[1], p[2]))), seg, radius, radial, false);
}
function lathe(profile, seg = 48) {
  // LatheGeometry faces outward only when the profile runs bottom -> top;
  // sort by height so every profile (skirt, torso, vest) renders its outside
  const pts = profile[0][1] > profile[profile.length - 1][1] ? profile.slice().reverse() : profile;
  return new THREE.LatheGeometry(pts.map((p) => new THREE.Vector2(p[0], p[1])), seg);
}

// ════════════════════════════════════════════════════════════════════
// THE CHARACTER
// ════════════════════════════════════════════════════════════════════
function buildCharacter(P) {
  geo();
  const skin = skinMat(P.skin);
  const skinShade = skinMat(new THREE.Color(P.skin).multiplyScalar(0.86).getHex());
  const hairM = new THREE.MeshPhysicalMaterial({color: P.hair, roughness: P.makeup ? 0.4 : 0.62, metalness: 0, sheen: 1.0, sheenRoughness: 0.35, sheenColor: new THREE.Color(P.hair2 || P.hair).lerp(new THREE.Color(0xffffff), 0.25), clearcoat: P.makeup ? 0.5 : 0, clearcoatRoughness: 0.35});
  const weave = weaveTex(!!P.pinstripe);
  const jacketM = P.top === 'evening'
    ? new THREE.MeshPhysicalMaterial({color: P.jacket, roughness: 0.32, metalness: 0.0, sheen: 1.0, sheenRoughness: 0.35,
        sheenColor: new THREE.Color(P.lapel).lerp(new THREE.Color(0xffffff), 0.4), clearcoat: 0.35, clearcoatRoughness: 0.4})
    : fabric(P.jacket, {map: P.pinstripe ? weave : null, bump: weave});
  const lapelM = fabric(P.lapel, {rough: 0.45, sheen: 0.9});
  const shirtM = fabric(P.shirt, {rough: P.female ? 0.42 : 0.7, sheen: P.female ? 1.0 : 0.4});
  const trouserM = fabric(P.trousers, {bump: weave});
  const shoeM = std(P.shoes, P.female ? 0.25 : 0.45, 0.05);
  const metalM = std(P.female ? 0xd4a64a : 0xc9ccd2, 0.25, 0.95);
  const darkM = std(0x0b0c0f, 0.5, 0.1);

  const rig = {P};
  const root = new THREE.Group();
  root.scale.setScalar(P.scale);
  rig.root = root;
  const B = P.build;

  // ── hips and legs ────────────────────────────────────────────────
  const hips = group(root, [0, 0.98, 0]); rig.hips = hips;
  add(hips, G.sphere, trouserM, [0, -0.02, 0], null, [B.waist + 0.005, 0.11, 0.115]);
  rig.legs = [];
  for (const s of [1, -1]) {
    const thigh = group(hips, [0.092 * s, -0.03, 0]);
    if (!P.female) add(thigh, capsule(0.068, 0.34), trouserM, [0, -0.2, 0]);
    else add(thigh, capsule(0.058, 0.34), skinShade, [0, -0.2, 0]);
    const knee = group(thigh, [0, -0.42, 0]);
    if (!P.female) add(knee, capsule(0.055, 0.36), trouserM, [0, -0.21, -0.005]);
    else add(knee, capsule(0.043, 0.36), skinShade, [0, -0.21, -0.005]);
    if (P.female) {
      add(knee, G.box, shoeM, [0, -0.455, 0.045], [0.2, 0, 0], [0.075, 0.05, 0.2]);
      add(knee, G.cyl, shoeM, [0, -0.46, -0.04], null, [0.012, 0.07, 0.012]);
    } else {
      add(knee, G.box, shoeM, [0, -0.46, 0.05], null, [0.1, 0.07, 0.27]);
      if (P.shoes > 0xa00000) add(knee, G.box, std(0xffffff, 0.8), [0, -0.49, 0.05], null, [0.104, 0.02, 0.274]);
    }
    rig.legs.push({thigh, knee, s});
  }
  if (P.top === 'evening') {
    // the dress continues as a fitted midi skirt
    add(hips, lathe([[0.001, 0.08], [0.146, 0.08], [0.166, -0.04], [0.164, -0.2], [0.15, -0.4], [0.132, -0.56], [0.128, -0.6], [0.001, -0.6]]), jacketM, [0, 0, 0], null, [1, 1, 0.76]);
  } else if (P.female) {
    // a tailored pencil skirt to the knee
    add(hips, lathe([[0.001, 0.06], [0.15, 0.06], [0.168, -0.05], [0.165, -0.2], [0.152, -0.38], [0.138, -0.47], [0.001, -0.47]]), trouserM, [0, 0, 0], null, [1, 1, 0.74]);
  }

  // ── spine and chest ──────────────────────────────────────────────
  const spine = group(hips, [0, 0.1, 0]); rig.spine = spine;
  const chest = group(spine, [0, 0.24, 0]); rig.chest = chest;
  const torso = group(chest, [0, 0, 0]); rig.torso = torso;
  // THE BODY IS LAYERED LATHES (skin, shirt, vest, jacket or dress) on one
  // smooth profile; the open front is a V pushed into the outer layer so the
  // layer beneath shows through it, following the curve of the chest.
  const shell = group(torso, [0, 0, 0]); shell.scale.set(1, 1, 0.63);
  const yHem = -0.37, yTop = 0.21;
  const interior = [[B.hem, yHem], [B.hem - 0.006, -0.3], [B.waist, -0.22], [B.waist + 0.012, -0.1], [B.chest, 0.02], [B.chest + 0.004, 0.1], [B.sh - 0.03, 0.15], [0.12, 0.185], [0.07, 0.203]];
  const spl = new THREE.SplineCurve(interior.map((q) => new THREE.Vector2(q[0], q[1]))).getPoints(44);
  const prof = [new THREE.Vector2(0.001, yHem)].concat(spl, [new THREE.Vector2(0.001, yTop)]);
  const rAt = (y) => { for (let i = 1; i < spl.length; i++) { const p0 = spl[i - 1], p1 = spl[i]; if ((y - p0.y) * (y - p1.y) <= 0) { const t = (y - p0.y) / ((p1.y - p0.y) || 1); return p0.x + (p1.x - p0.x) * t; } } return 0.05; };
  const surf = (x, y, k = 1) => Math.sqrt(Math.max(0, (rAt(y) * k) ** 2 - x * x));
  const layer = (k, mat, wFn, push) => {
    const g = new THREE.LatheGeometry(prof.map((v) => new THREE.Vector2(v.x * k, v.y)), 96);
    if (wFn) {
      const pos = g.attributes.position;
      for (let i = 0; i < pos.count; i++) { const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i); if (z > 0 && Math.abs(x) < wFn(y)) { pos.setX(i, x * push); pos.setZ(i, z * push); } }
      g.computeVertexNormals();
    }
    return add(shell, g, mat, [0, 0, 0]);
  };
  const vee = (yb, yt, w0, w1) => (y) => y < yb ? 0 : w0 + (w1 - w0) * clamp((y - yb) / (yt - yb), 0, 1);
  const eve = P.top === 'evening';
  const yb = eve ? 0.03 : P.female ? -0.03 : -0.09;
  const wJ = vee(yb, 0.205, 0.004, eve ? 0.072 : 0.086);
  layer(0.93, skin, null);                                                     // skin beneath
  if (!eve) layer(0.962, shirtM, P.top === 'open' ? vee(0.11, 0.205, 0.004, 0.036) : null, 0.9);
  if (P.vest) layer(0.98, fabric(P.vest), vee(0.035, 0.205, 0.004, 0.06), 0.93);
  layer(1.0, jacketM, wJ, 0.9);                                                 // jacket, or the dress
  const edge = (w, k, dx) => { const pts = []; for (let i = 0; i <= 14; i++) { const y = yb + (0.205 - yb) * i / 14; const x = w(y) + dx; pts.push([x, y, surf(x, y, k) + 0.002]); } return pts; };
  for (const sd of [1, -1]) {
    const e = edge(wJ, 1.0, 0.006).map((q) => [q[0] * sd, q[1], q[2]]);
    if (eve) add(shell, tube(e, 0.006, 40, 6), new THREE.MeshPhysicalMaterial({color: P.lapel, roughness: 0.28, sheen: 1, sheenRoughness: 0.3, sheenColor: new THREE.Color(0xbfe8e0), clearcoat: 0.4}));
    else {
      add(shell, tube(e, 0.0075, 40, 6), lapelM);                                                           // the lapel roll
      add(shell, tube(edge(wJ, 1.0, 0.02).map((q) => [q[0] * sd, q[1], q[2]]).slice(4), 0.0075, 30, 6), lapelM);   // its width
    }
  }
  if (eve) {
    // an asymmetric satin sash from shoulder to hip, and a fine pendant
    const sash = []; for (let i = 0; i <= 16; i++) { const t = i / 16; const x = -0.15 + 0.29 * t, y = 0.16 - 0.46 * t; sash.push([x, y, surf(x, y) + 0.012]); }
    add(shell, tube(sash, 0.017, 48, 8), new THREE.MeshPhysicalMaterial({color: P.lapel, roughness: 0.28, sheen: 1, sheenRoughness: 0.3, sheenColor: new THREE.Color(0xbfe8e0), clearcoat: 0.4}));
    add(shell, G.sphere, std(P.accent, 0.1, 0.2, {emissive: P.accent, emissiveIntensity: 0.3}), [0, yb + 0.05, surf(0, yb + 0.05, 0.93) + 0.006], null, [0.006, 0.008, 0.008], false);
    const ch = []; for (let i = 0; i <= 12; i++) { const t = -1 + 2 * i / 12; const x = 0.05 * t, y = yb + 0.05 + 0.13 * t * t; ch.push([x, y, surf(x, y, 0.93) + 0.003]); }
    add(shell, tube(ch, 0.0011, 30, 4), metalM, null, null, null, false);
  }
  if (P.top === 'tie') {
    const tp = []; for (let i = 0; i <= 12; i++) { const y = 0.185 - 0.23 * i / 12; tp.push([0, y, surf(0, y, 0.962) + 0.006]); }
    const tieM = fabric(P.tie, {rough: 0.4, sheen: 1});
    const t = add(shell, tube(tp, 0.012, 24, 8), tieM); t.scale.x = 1.9;
    add(shell, G.sphere, tieM, [0, 0.188, surf(0, 0.188, 0.962) + 0.008], null, [0.016, 0.013, 0.012]);
    add(shell, G.box, fabric(P.accent), [0.105, 0.065, surf(0.105, 0.065) + 0.004], [0, 0, 0.12], [0.034, 0.014, 0.01]);   // pocket square
  }
  if (P.top === 'open') for (const sd of [1, -1]) {
    const cp = new THREE.Shape(); cp.moveTo(0, 0); cp.lineTo(0.045 * sd, 0.012); cp.lineTo(0.02 * sd, -0.045); cp.closePath();
    add(shell, new THREE.ShapeGeometry(cp), shirtM, [0.03 * sd, 0.19, surf(0.03, 0.19, 0.962) + 0.006], [-0.4, 0.25 * sd, 0], null, false);
  }
  if (!eve) add(shell, G.sphere, darkM, [0, P.female ? -0.06 : -0.13, surf(0, P.female ? -0.06 : -0.13) + 0.002], null, [0.008, 0.008, 0.006]);
  // the AI pin on the left chest (identifies the character as an AI agent)
  const pinTex = canvasTex(64, 64, (g) => { g.fillStyle = '#0b0d12'; g.beginPath(); g.arc(32, 32, 30, 0, TAU); g.fill(); g.lineWidth = 5; g.strokeStyle = '#' + P.accent.toString(16).padStart(6, '0'); g.stroke(); g.fillStyle = '#fff'; g.font = 'bold 26px monospace'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('AI', 32, 34); });
  const pinX = eve ? 0.1 : 0.115, pinY = eve ? -0.02 : 0.09;
  add(shell, new THREE.CircleGeometry(0.012, 24), new THREE.MeshStandardMaterial({map: pinTex, emissive: 0xffffff, emissiveMap: pinTex, emissiveIntensity: 0.6, roughness: 0.3}), [pinX, pinY, surf(pinX, pinY) + 0.012], [0, Math.atan2(pinX, surf(pinX, pinY)) * 0.8, 0], null, false);
  for (const sd of [1, -1]) add(torso, G.sphere, jacketM, [(B.sh - 0.03) * sd, 0.138, -0.005], null, eve ? [0.05, 0.038, 0.056] : [0.066, 0.05, 0.07]);

  // ── neck and head ────────────────────────────────────────────────
  const neck = group(chest, [0, 0.19, -0.008]); rig.neck = neck;
  add(neck, G.cyl, skin, [0, 0.05, -0.004], null, [P.female ? 0.039 : 0.046, 0.12, P.female ? 0.036 : 0.043]);
  if (P.top === 'tie') add(neck, G.cyl, shirtM, [0, 0.02, 0.0], null, [0.052, 0.038, 0.05]);
  const head = group(neck, [0, 0.082, 0.006]); rig.head = head;
  const skull = group(head, [0, 0, 0]);
  const HW = P.female ? 0.93 : 1.0, HD = 1.12, HZ = -0.008;
  // THE HEAD: one smooth lathe (cranium, temples, cheek line, jaw), deeper
  // than it is wide, with the features placed on its surface.
  const HP = [[0.024, 0.0], [0.044, 0.012], [0.059, 0.031], [0.07, 0.054], [0.079, 0.08], [0.087, 0.106], [0.091, 0.13], [0.089, 0.156], [0.079, 0.184], [0.056, 0.206]];
  const hspl = new THREE.SplineCurve(HP.map((q) => new THREE.Vector2(q[0], q[1]))).getPoints(40);
  const hprof = [new THREE.Vector2(0.001, -0.004)].concat(hspl, [new THREE.Vector2(0.001, 0.219)]);
  const hr = (y) => { for (let i = 1; i < hspl.length; i++) { const p0 = hspl[i - 1], p1 = hspl[i]; if ((y - p0.y) * (y - p1.y) <= 0) { const t = (y - p0.y) / ((p1.y - p0.y) || 1); return p0.x + (p1.x - p0.x) * t; } } return 0.03; };
  const hz = (x, y) => Math.sqrt(Math.max(0, (hr(y) * HW) ** 2 - x * x)) / HW * HD + HZ;
  add(skull, new THREE.LatheGeometry(hprof, 72), skin, [0, 0, HZ], null, [HW, 1, HD]);
  add(skull, G.sphere, skin, [0, 0.02, hz(0, 0.02) - 0.0165], null, [P.female ? 0.022 : 0.027, 0.016, 0.0185]);        // chin
  for (const sd of [1, -1]) {
    add(skull, G.sphere, skin, [0.049 * sd * HW, 0.088, hz(0.049 * HW, 0.088) - 0.0165], null, [0.021, 0.014, 0.0155]);  // cheekbones
    add(skull, G.sphere, skinShade, [(hr(0.104) * HW - 0.002) * sd, 0.103, -0.012], [0, 0.35 * sd, 0], [0.01, 0.025, 0.016]);   // ears
  }
  // nose: bridge, tip and wings
  const nb = [[0, 0.12, hz(0, 0.12) - 0.002], [0, 0.104, hz(0, 0.104) + 0.004], [0, 0.088, hz(0, 0.088) + 0.009]];
  const nose = add(skull, tube(nb, P.female ? 0.005 : 0.0058, 12, 8), skin); nose.scale.x = 1.25;
  add(skull, G.sphere, skin, [0, 0.082, hz(0, 0.082) + 0.0085], null, [P.female ? 0.0085 : 0.0102, 0.0085, 0.0092]);
  for (const sd of [1, -1]) add(skull, G.sphere, skinShade, [0.0098 * sd, 0.0785, hz(0.0098, 0.0785) + 0.0032], null, P.female ? 0.005 : 0.006);
  // eyes, lids, lashes
  const scleraM = new THREE.MeshPhysicalMaterial({color: 0xf4f1ec, roughness: 0.15, clearcoat: 1, clearcoatRoughness: 0.05});
  const irisM = std(P.iris, 0.35, 0);
  const pupilM = std(0x050505, 0.2, 0);
  const lashM = std(0x120c0a, 0.7, 0);
  const lidShadow = skinMat(new THREE.Color(P.skin).lerp(new THREE.Color(0x6b4458), 0.3).getHex());
  rig.eyes = []; rig.lids = [];
  const EX = 0.033 * HW, EY = 0.112, ER = P.female ? 0.0128 : 0.0124;
  for (const sd of [1, -1]) {
    const eg = group(skull, [EX * sd, EY, hz(EX, EY) - ER + 0.0022]);
    const look = group(eg, [0, 0, 0]);
    add(look, G.sphere, scleraM, [0, 0, 0], null, ER, false);
    add(look, new THREE.CircleGeometry(ER * 0.52, 28), irisM, [0, 0, ER * 0.995], null, null, false);
    add(look, new THREE.CircleGeometry(ER * 0.22, 20), pupilM, [0, 0, ER * 0.998], null, null, false);
    add(look, new THREE.CircleGeometry(ER * 0.1, 10), new THREE.MeshBasicMaterial({color: 0xffffff}), [ER * 0.17, ER * 0.19, ER * 1.004], null, null, false);
    const lid = group(eg, [0, 0, 0]);
    add(lid, new THREE.SphereGeometry(ER * 1.1, 24, 10, 0, TAU, 0, Math.PI / 2), P.makeup ? lidShadow : skin, [0, 0, 0], null, null, false);
    add(lid, new THREE.TorusGeometry(ER * 1.1, P.makeup ? 0.0017 : P.female ? 0.0012 : 0.0008, 6, 26, Math.PI), lashM, [0, 0, 0], [Math.PI / 2, 0, 0], null, false);
    add(eg, new THREE.SphereGeometry(ER * 1.08, 24, 8, 0, TAU, Math.PI * 0.64, Math.PI * 0.36), skin, [0, 0, 0], [-0.08, 0, 0], null, false);
    if (P.makeup) add(eg, tube([[0.9 * ER * sd, 0.1 * ER, 0.35 * ER], [1.45 * ER * sd, 0.5 * ER, 0.1 * ER], [1.85 * ER * sd, 0.85 * ER, -0.2 * ER]], 0.0009, 8, 4), lashM, null, null, null, false);   // winged liner
    rig.eyes.push(look); rig.lids.push(lid);
  }
  // brows
  rig.brows = [];
  const browM = new THREE.MeshStandardMaterial({color: P.brow, roughness: 0.9});
  for (const sd of [1, -1]) {
    const bx = 0.035 * HW, by = 0.135 + P.expr.brow;
    const bg = group(skull, [bx * sd, by, hz(bx, by) + 0.0015]);
    const pts = P.female ? [[-0.017 * sd, -0.004, -0.001], [-0.004 * sd, 0.003, 0.001], [0.011 * sd, 0.0045, -0.001], [0.019 * sd, -0.002, -0.006]]
                         : [[-0.018 * sd, -0.001, -0.001], [0.0, 0.003, 0.001], [0.019 * sd, 0.001, -0.005]];
    add(bg, tube(pts, P.female ? 0.0019 : 0.003, 16, 6), browM, null, null, null, false);
    rig.brows.push({g: bg, s: sd, y0: bg.position.y});
  }
  if (P.makeup) add(skull, G.sphere, std(0x3a2016, 0.8), [-0.03, 0.068, hz(0.03, 0.068) + 0.0008], null, 0.0016, false);   // a beauty mark
  // mouth: interior, upper lip, and the jaw (lower lip + chin) that opens
  const lipM = new THREE.MeshPhysicalMaterial({color: P.lips, roughness: P.makeup ? 0.22 : 0.4, clearcoat: P.makeup ? 0.9 : P.female ? 0.5 : 0.1, clearcoatRoughness: 0.2});
  const MY = 0.051, sm = P.expr.smile * 0.003, lipW = P.female ? 0.019 : 0.021;
  add(skull, new THREE.CircleGeometry(0.017, 24), std(0x2a0d0d, 0.9), [0, MY - 0.002, hz(0, MY) - 0.001], null, [1, 0.35, 1], false);
  const ul = [-1, -0.5, 0, 0.5, 1].map((t) => { const x = lipW * t; return [x, MY + 0.0015 + (Math.abs(t) > 0.9 ? sm : 0.0015 * (1 - Math.abs(t))), hz(x, MY) + 0.0028 - 0.002 * Math.abs(t)]; });
  add(skull, tube(ul, P.female ? 0.0042 : 0.0034, 20, 8), lipM, null, null, null, false);
  const jaw = group(skull, [0, 0.075, 0.0]); rig.jaw = jaw;
  const ll = [-0.9, -0.45, 0, 0.45, 0.9].map((t) => { const x = lipW * t; return [x, MY - 0.0045 - 0.075 - 0.002 * (1 - Math.abs(t)) + (Math.abs(t) > 0.8 ? sm : 0), hz(x, MY - 0.005) + 0.003 - 0.002 * Math.abs(t)]; });
  add(jaw, tube(ll, P.female ? 0.0052 : 0.0042, 20, 8), lipM, null, null, null, false);
  // facial hair: a partial lathe of the lower head, just outside the skin
  const lowerHead = (k, yMax) => new THREE.LatheGeometry(hprof.filter((v) => v.y <= yMax).map((v) => new THREE.Vector2(v.x * k, v.y)).concat([new THREE.Vector2(hr(yMax) * k, yMax)]), 64, -0.62 * Math.PI, 1.24 * Math.PI);
  if (P.beard === 'full') {
    const bm = new THREE.MeshStandardMaterial({color: P.hair, roughness: 1.0, side: THREE.DoubleSide});
    add(skull, lowerHead(1.055, 0.078), bm, [0, 0, HZ], null, [HW, 1, HD]);
    add(skull, tube([[-0.02, MY + 0.009, hz(0.02, MY + 0.009) + 0.004], [-0.007, MY + 0.012, hz(0, MY) + 0.007], [0.007, MY + 0.012, hz(0, MY) + 0.007], [0.02, MY + 0.009, hz(0.02, MY + 0.009) + 0.004]], 0.0045, 12, 6), bm, null, null, null, false);
  } else if (P.beard === 'stubble') {
    const st = new THREE.MeshStandardMaterial({color: 0x2a1c14, roughness: 1, transparent: true, opacity: 0.15, side: THREE.DoubleSide, depthWrite: false});
    add(skull, lowerHead(1.02, 0.038), st, [0, 0, HZ], null, [HW, 1, HD], false);
  }
  // hair
  buildHair(rig, skull, P, hairM, {HW, HD, HZ});
  // glasses
  if (P.glasses) {
    const gm = std(0x3a414c, 0.3, 0.9);
    const lens = new THREE.MeshPhysicalMaterial({color: 0xdfe8f0, roughness: 0.05, transparent: true, opacity: 0.12, metalness: 0});
    const gz = hz(EX, EY) + 0.012;
    for (const sd of [1, -1]) {
      add(skull, new THREE.TorusGeometry(0.0165, 0.0013, 8, 32), gm, [EX * sd, EY, gz], null, [1, 0.78, 1], false);
      add(skull, new THREE.CircleGeometry(0.0162, 28), lens, [EX * sd, EY, gz - 0.0005], null, [1, 0.78, 1], false);
      add(skull, tube([[0.049 * sd, EY + 0.002, gz - 0.002], [hr(EY) * HW * sd, EY + 0.003, hz(hr(EY) * HW * 0.8, EY) - 0.01], [(hr(EY) * HW + 0.002) * sd, EY - 0.004, -0.04]], 0.0012, 12, 5), gm, null, null, null, false);
    }
    add(skull, tube([[-0.017, EY + 0.001, gz], [0, EY + 0.004, gz + 0.002], [0.017, EY + 0.001, gz]], 0.0012, 8, 5), gm, null, null, null, false);
  }
  if (P.female) for (const sd of [1, -1]) {
    const ex = (hr(0.08) * HW + 0.004) * sd;
    add(skull, G.sphere, metalM, [ex, 0.078, -0.01], null, 0.0042);
    if (P.makeup) {
      add(skull, G.cyl, metalM, [ex, 0.06, -0.01], null, [0.0011, 0.03, 0.0011]);
      add(skull, new THREE.OctahedronGeometry(1, 0), new THREE.MeshPhysicalMaterial({color: P.accent, roughness: 0.05, metalness: 0.1, clearcoat: 1, emissive: P.accent, emissiveIntensity: 0.15}), [ex, 0.039, -0.01], null, [0.0065, 0.011, 0.0065]);
    }
  }

  // ── arms ─────────────────────────────────────────────────────────
  rig.arms = {};
  const L1 = P.female ? 0.265 : 0.28, L2 = P.female ? 0.245 : 0.26;
  for (const s of [1, -1]) {
    const S = new V3((B.sh - 0.03) * s, 0.135, -0.012);
    const upper = group(chest, [S.x, S.y, S.z]);
    const eve = P.top === 'evening';
    add(upper, capsule(eve ? 0.036 : P.female ? 0.043 : 0.05, L1 - 0.04), eve ? skin : jacketM, [0, -L1 / 2, 0]);
    if (eve) add(upper, G.sphere, jacketM, [0, -0.03, 0], null, [0.041, 0.05, 0.043]);
    const fore = group(upper, [0, -L1, 0]);
    add(fore, capsule(eve ? 0.03 : P.female ? 0.037 : 0.043, L2 - 0.05), eve ? skin : jacketM, [0, -L2 / 2 + 0.01, 0]);
    if (eve) add(fore, new THREE.TorusGeometry(0.031, 0.0035, 8, 28), metalM, [0, -L2 + 0.03, 0], [Math.PI / 2, 0, 0]);
    else add(fore, G.cyl, shirtM, [0, -L2 + 0.018, 0], null, [P.female ? 0.034 : 0.039, 0.028, P.female ? 0.034 : 0.039]);
    if (s === 1 && !eve) {
      add(fore, new THREE.TorusGeometry(P.female ? 0.034 : 0.04, 0.005, 8, 28), metalM, [0, -L2 + 0.04, 0], [Math.PI / 2, 0, 0]);
      add(fore, G.cyl, std(0x0e1116, 0.2, 0.3), [0, -L2 + 0.04, P.female ? 0.035 : 0.042], [Math.PI / 2, 0, 0], [0.014, 0.006, 0.014]);
    }
    const hand = group(fore, [0, -L2, 0]);
    const fingers = buildHand(hand, s, skin, P.female);
    rig.arms[s] = {s, S, L1, L2, upper, fore, hand, fingers,
      cur: {H: new V3(0.215 * s, -0.4, 0.03), pole: new V3(0.3 * s, 0, -1), b: new V3(s, 0, 0), curl: 0.3, index: 0},
      prop: null};
  }
  // props
  const screenTex = (w, h, draw) => canvasTex(w, h, draw);
  const acc = '#' + P.accent.toString(16).padStart(6, '0');
  if (P.poses.monitoring.R === 'PHONE' || P.gestures.monitoring.some((g) => g.R === 'PHONE')) {
    const ph = new THREE.Group();
    add(ph, G.box, std(0x111318, 0.3, 0.6), [0, 0, 0], null, [0.07, 0.145, 0.008]);
    const t = screenTex(128, 256, (g, w, h) => { g.fillStyle = '#05070b'; g.fillRect(0, 0, w, h); g.strokeStyle = acc; g.lineWidth = 3; g.beginPath(); let y = h * 0.6; g.moveTo(8, y); for (let x = 8; x < w - 8; x += 8) { y += (Math.sin(x * 0.13) + Math.cos(x * 0.05)) * 6 - 1.5; g.lineTo(x, y); } g.stroke(); g.fillStyle = '#9aa3b1'; g.font = '14px monospace'; g.fillText('EDGE  +', 10, 24); g.fillStyle = acc; g.fillRect(10, 34, 70, 6); });
    add(ph, new THREE.PlaneGeometry(0.064, 0.136), new THREE.MeshStandardMaterial({map: t, emissive: 0xffffff, emissiveMap: t, emissiveIntensity: 0.8}), [0, 0, -0.0045], [0, Math.PI, 0], null, false);
    ph.position.set(0, -0.06, -0.022); ph.rotation.set(0, 0, 0);
    rig.arms[-1].hand.add(ph); ph.visible = false; rig.arms[-1].prop = {obj: ph, pose: 'PHONE'};
  }
  if (P.female) {
    const tb = new THREE.Group();
    add(tb, G.box, std(0x16181d, 0.3, 0.5), [0, 0, 0], null, [0.24, 0.17, 0.009]);
    const t = screenTex(320, 224, (g, w, h) => { g.fillStyle = '#060811'; g.fillRect(0, 0, w, h); g.fillStyle = '#b8c0cc'; g.font = '15px monospace'; g.fillText('DAILY AUDIT', 14, 26); for (let i = 0; i < 6; i++) { g.fillStyle = i % 2 ? '#2a3140' : '#1c2230'; g.fillRect(14, 42 + i * 26, w - 28, 20); g.fillStyle = i === 2 ? acc : '#6f7a8c'; g.fillRect(20, 48 + i * 26, 40 + i * 25, 8); } });
    add(tb, new THREE.PlaneGeometry(0.225, 0.155), new THREE.MeshStandardMaterial({map: t, emissive: 0xffffff, emissiveMap: t, emissiveIntensity: 0.75}), [0, 0, -0.0048], [0, Math.PI, 0], null, false);
    tb.position.set(0.0, -0.085, -0.02); tb.rotation.set(0, 0, Math.PI / 2);
    rig.arms[1].hand.add(tb); tb.visible = false; rig.arms[1].prop = {obj: tb, pose: 'TABLET'};
  }
  root.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
  return rig;
}

function buildHand(hand, s, skin, female) {
  const k = female ? 0.92 : 1.0;
  add(hand, G.sphere, skin, [0, -0.042 * k, 0], null, [0.036 * k, 0.046 * k, 0.017 * k]);
  const fingers = [];
  const xs = [-0.021, -0.007, 0.007, 0.021];
  const lens = [0.03, 0.033, 0.031, 0.025];
  xs.forEach((x, i) => {
    const prox = group(hand, [-s * x * k, -0.082 * k, 0.0]);
    add(prox, capsule(0.0077 * k, lens[i] * k), skin, [0, -lens[i] * k / 2, 0]);
    const dist = group(prox, [0, -lens[i] * k - 0.004, 0]);
    add(dist, capsule(0.0069 * k, 0.022 * k), skin, [0, -0.013 * k, 0]);
    fingers.push({prox, dist, index: i === 0});
  });
  const th = group(hand, [-s * 0.03 * k, -0.03 * k, -0.006]);
  th.rotation.set(0.35, 0, s * 0.75);
  add(th, capsule(0.0088 * k, 0.03 * k), skin, [0, -0.02 * k, 0]);
  fingers.thumb = th;
  return fingers;
}

function buildHair(rig, skull, P, m, H) {
  const hw = H.HW, hz0 = H.HZ;
  const cap = (theta, tilt, grow = 1.03) => add(skull, new THREE.SphereGeometry(1, 40, 22, 0, TAU, 0, Math.PI * theta), m,
    [0, 0.112, hz0 - 0.004], [tilt, 0, 0], [0.097 * hw * grow, 0.121 * grow, 0.108 * grow]);
  if (P.hair_style === 'crop') {
    cap(0.44, -0.3);
    const r = rng(P.seed);
    for (let i = 0; i < 34; i++) {
      const a = (r() - 0.5) * 2.0, bb = r();
      add(skull, G.sphereLo, m, [Math.sin(a) * 0.07 * hw, 0.205 + 0.012 * (1 - Math.abs(a)) - bb * 0.012, hz0 + 0.03 + Math.cos(a) * 0.04 - bb * 0.08], [0, a, 0], [0.02, 0.011, 0.018]);
    }
  } else if (P.hair_style === 'swept') {
    cap(0.48, -0.42);
    for (let i = -3; i <= 3; i++) add(skull, tube([[i * 0.018, 0.2, hz0 + 0.08], [i * 0.022, 0.226, hz0 + 0.03], [i * 0.024, 0.222, hz0 - 0.04], [i * 0.022, 0.18, hz0 - 0.1]], 0.011, 16, 8), m);
    add(skull, G.sphere, m, [0, 0.208, hz0 + 0.07], [0.35, 0, 0], [0.07 * hw, 0.022, 0.036]);
  } else if (P.hair_style === 'long') {
    cap(0.52, -0.36, 1.045);
    // a polished side part sweeping across the brow
    add(skull, G.sphere, m, [-0.016, 0.19, hz0 + 0.074], [0.45, 0.2, 0.4], [0.056, 0.014, 0.026]);
    const back = group(skull, [0, 0.105, 0]); rig.hairBack = back;
    // the volume of waves behind the shoulders; strands add texture on top
    const curtain = add(back, new THREE.CylinderGeometry(0.1, 0.142, 0.36, 44, 8, true, Math.PI * 0.5, Math.PI * 1.0), m.clone(), [0, -0.1, hz0 - 0.004]);
    curtain.material.side = THREE.DoubleSide;
    const pos = curtain.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) { const y = pos.getY(i), x = pos.getX(i), z = pos.getZ(i); const k = 1 + 0.05 * Math.sin(y * 34 + Math.atan2(x, z) * 6); pos.setX(i, x * k); pos.setZ(i, z * k); }
    curtain.geometry.computeVertexNormals();
    const r = rng(P.seed);
    const m2 = new THREE.MeshPhysicalMaterial({color: P.hair2, roughness: 0.5, sheen: 1, sheenRoughness: 0.35, sheenColor: new THREE.Color(0xc9a080), clearcoat: 0.4});
    const N = 40;
    for (let i = 0; i < N; i++) {
      const a = -1.9 + (i / (N - 1)) * 3.8 + (r() - 0.5) * 0.08;
      const sx = Math.sin(a) * hw, cz = -Math.cos(a);
      const front = Math.abs(a) > 1.4;
      const len = front ? 0.22 : 0.29 + r() * 0.04;
      const w = 0.004 + r() * 0.006;
      const wv = 0.012 * Math.sin(i * 1.7);
      const pts = [[sx * 0.086, 0.07, cz * 0.094 + hz0], [sx * 0.112, 0.0, cz * 0.106 + hz0], [sx * 0.13 + w + wv, -0.09, cz * 0.1 + hz0 - 0.01], [sx * 0.126 - w - wv, -0.18, cz * 0.09 + hz0 - 0.018], [sx * 0.132 + wv, -0.25, cz * 0.078 + hz0 - 0.024], [sx * 0.12, -len, cz * 0.068 + hz0 - 0.028]];
      if (front) pts.forEach((q, j) => { if (j > 0) q[2] = Math.min(Math.max(q[2], -0.03), 0.01); });
      add(back, tube(pts, 0.02 + r() * 0.008, 22, 8), i % 4 === 1 ? m2 : m);
    }
  }
}

// ════════════════════════════════════════════════════════════════════
// POSES (chest space; x mirrored by side)
// ════════════════════════════════════════════════════════════════════
function pose(name, s) {
  const P = (H, pole, b, curl, extra) => Object.assign({H: new V3(H[0], H[1], H[2]), pole: new V3(pole[0], pole[1], pole[2]).normalize(), b: new V3(b[0], b[1], b[2]).normalize(), curl, index: 0}, extra || {});
  switch (name) {
    case 'POCKET': return P([0.168 * s, -0.36, 0.055], [s, -0.2, -0.5], [s, 0, 0.3], 0.55);
    case 'HIP': return P([0.2 * s, -0.25, -0.005], [s, 0.15, -0.35], [s, 0.25, -0.3], 0.45);
    case 'CROSS_UNDER': return P([-0.125 * s, -0.135, 0.155], [s, -0.6, -0.1], [0, 0.2, 1], 0.5);
    case 'CROSS_OVER': return P([-0.14 * s, -0.07, 0.195], [s, -0.6, -0.1], [0, 0.35, 1], 0.45);
    case 'CHIN': return P([-0.04 * s, 0.225, 0.155], [s * 0.3, -1, 0.5], [0, 0, 1], 0.8);
    case 'SUPPORT': return P([-0.07 * s, -0.1, 0.175], [s, -0.7, -0.2], [0, -0.3, 1], 0.4);
    case 'PHONE': return P([0.07 * s, -0.035, 0.27], [s * 0.7, -1, -0.3], [0, -1, 0.35], 0.45, {prop: 'PHONE'});
    case 'POINT': return P([0.33 * s, 0.13, 0.3], [s, -0.5, -0.3], [0, 1, 0], 0.92, {index: 1});
    case 'WATCH': return P([0.03 * s, 0.035, 0.28], [s, -1, -0.2], [0, 1, 0.25], 0.45);
    case 'STEEPLE': return P([0.046 * s, -0.075, 0.22], [s, -0.8, -0.4], [s, 0.2, 0.4], 0.12);
    case 'CLASP': return P([0.032 * s, -0.3, 0.15], [s * 0.6, -0.5, -0.5], [s * 0.5, 0, 1], 0.62);
    case 'GLASSES': return P([0.025 * s, 0.27, 0.19], [s * 0.4, -1, 0.1], [0, 0, 1], 0.85, {index: 1});
    case 'TABLET': return P([0.09 * s, -0.14, 0.225], [s, -1, -0.3], [0, -1, 0.25], 0.35, {prop: 'TABLET'});
    case 'TABLET_UP': return P([0.08 * s, -0.06, 0.26], [s, -1, -0.3], [0, -1, 0.45], 0.35, {prop: 'TABLET'});
    case 'SWIPE': return P([0.0, -0.045, 0.31], [s, -0.8, -0.3], [0, 1, 0.2], 0.85, {index: 1, swipe: 1});
    case 'OPEN': return P([0.24 * s, -0.12, 0.27], [s, -0.6, -0.4], [0, -1, 0.2], 0.12, {beat: 1});
    case 'EXPLAIN': return P([0.13 * s, -0.02, 0.3], [s, -0.8, -0.3], [s, 0.1, 0.2], 0.25, {beat: 1});
    default: return P([0.215 * s, -0.4, 0.03], [0.3 * s, 0, -1], [s, 0, 0], 0.3);
  }
}

// two-bone IK in chest space; sets the three joint quaternions
const _m = new THREE.Matrix4(), _x = new V3(), _y = new V3(), _z = new V3(), _q1 = new THREE.Quaternion(), _q2 = new THREE.Quaternion(), _q3 = new THREE.Quaternion(), _qi = new THREE.Quaternion();
function basisQuat(yDir, zHint, out) {
  _y.copy(yDir).normalize();
  _z.copy(zHint).addScaledVector(_y, -zHint.dot(_y));
  if (_z.lengthSq() < 1e-6) { _z.set(0, 0, 1).addScaledVector(_y, -_y.z); if (_z.lengthSq() < 1e-6) _z.set(1, 0, 0); }
  _z.normalize();
  _x.crossVectors(_y, _z).normalize();
  _z.crossVectors(_x, _y).normalize();
  _m.makeBasis(_x, _y, _z);
  return out.setFromRotationMatrix(_m);
}
const _d = new V3(), _e = new V3(), _f = new V3(), _pp = new V3();
function solveArm(arm, H, pole, b) {
  const {S, L1, L2} = arm;
  _d.copy(H).sub(S);
  let dist = _d.length();
  const maxR = L1 + L2 - 0.002, minR = Math.abs(L1 - L2) + 0.01;
  dist = clamp(dist, minR, maxR);
  _d.normalize();
  const cosA = clamp((L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist), -1, 1);
  const a = Math.acos(cosA);
  _pp.copy(pole).addScaledVector(_d, -pole.dot(_d));
  if (_pp.lengthSq() < 1e-6) _pp.set(0, -1, 0);
  _pp.normalize();
  _e.copy(_d).multiplyScalar(Math.cos(a)).addScaledVector(_pp, Math.sin(a)).multiplyScalar(L1).add(S);   // elbow
  const Hc = _f.copy(_d).multiplyScalar(dist).add(S);                                                     // reachable wrist
  basisQuat(_y.copy(S).sub(_e), _pp.clone().negate(), _q1);                                               // upper: bone along -y
  const foreDir = new V3().copy(_e).sub(Hc);
  basisQuat(foreDir, b, _q2);                                                                              // forearm twist follows the hand
  arm.upper.quaternion.copy(_q1);
  _qi.copy(_q1).invert();
  arm.fore.quaternion.copy(_qi).multiply(_q2);
  arm.hand.quaternion.identity();
}

// ════════════════════════════════════════════════════════════════════
// THE ROOM
// ════════════════════════════════════════════════════════════════════
function chartTex(accent, seed, kind) {
  return canvasTex(512, 288, (g, w, h) => {
    const r = rng(seed);
    g.fillStyle = '#04070c'; g.fillRect(0, 0, w, h);
    g.strokeStyle = 'rgba(120,140,170,0.12)'; g.lineWidth = 1;
    for (let x = 0; x < w; x += 32) { g.beginPath(); g.moveTo(x, 30); g.lineTo(x, h); g.stroke(); }
    for (let y = 30; y < h; y += 26) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
    g.fillStyle = '#8391a6'; g.font = '15px monospace';
    g.fillText(['MKT-A  0.612', 'SPRD +2.5  0.404', 'BOOK DEPTH', 'EDGE MONITOR'][kind % 4], 12, 20);
    g.fillStyle = accent; g.fillText(['▲ 1.3', '▼ 0.4', '▲ 0.8', '▲ 2.1'][kind % 4], w - 70, 20);
    if (kind % 4 === 2) {
      for (let i = 0; i < 9; i++) { const bw = 40 + r() * 170; g.fillStyle = 'rgba(79,209,151,0.55)'; g.fillRect(w / 2 - bw, 40 + i * 26, bw, 18); const aw = 40 + r() * 170; g.fillStyle = 'rgba(255,125,115,0.5)'; g.fillRect(w / 2 + 4, 40 + i * 26, aw, 18); }
      return;
    }
    for (let k = 0; k < 2; k++) {
      g.strokeStyle = k ? 'rgba(138,168,255,0.7)' : accent; g.lineWidth = k ? 1.5 : 2.5;
      g.beginPath(); let y = h * (0.5 + r() * 0.2);
      for (let x = 0; x <= w; x += 6) { y = clamp(y + (r() - 0.5) * 14, 40, h - 10); x ? g.lineTo(x, y) : g.moveTo(x, y); }
      g.stroke();
    }
  }, [1, 1]);
}
function offlineTex() {
  return canvasTex(512, 288, (g, w, h) => {
    g.fillStyle = '#07080a'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#5b6270'; g.font = 'bold 30px monospace'; g.textAlign = 'center';
    g.fillText('NO HEARTBEAT', w / 2, h / 2); g.font = '16px monospace'; g.fillText('status unavailable', w / 2, h / 2 + 30);
  });
}
function skylineTex() {
  return canvasTex(1024, 320, (g, w, h) => {
    const sky = g.createLinearGradient(0, 0, 0, h);
    sky.addColorStop(0, '#070b16'); sky.addColorStop(0.55, '#16203a'); sky.addColorStop(0.85, '#3b2c3c'); sky.addColorStop(1, '#5a3a3a');
    g.fillStyle = sky; g.fillRect(0, 0, w, h);
    const r = rng(99);
    for (let layer = 0; layer < 3; layer++) {
      let x = 0;
      while (x < w) {
        const bw = 20 + r() * 60, bh = (0.25 + r() * 0.55) * h * (1 - layer * 0.18);
        const shade = 8 + layer * 7;
        g.fillStyle = 'rgb(' + shade + ',' + (shade + 3) + ',' + (shade + 10) + ')';
        g.fillRect(x, h - bh, bw, bh);
        for (let wy = h - bh + 6; wy < h - 4; wy += 7) for (let wx = x + 3; wx < x + bw - 3; wx += 6) if (r() < 0.22 - layer * 0.05) { g.fillStyle = r() < 0.7 ? 'rgba(255,214,150,0.8)' : 'rgba(170,200,255,0.75)'; g.fillRect(wx, wy, 2, 3); }
        x += bw + r() * 6;
      }
    }
  });
}
function tickerTex(accent) {
  const t = canvasTex(2048, 64, (g, w, h) => {
    g.fillStyle = '#030406'; g.fillRect(0, 0, w, h);
    g.font = 'bold 30px monospace'; g.textBaseline = 'middle';
    const items = ['MKT-A 0.612 ▲', 'SPRD+2.5 0.404 ▼', 'EDGE 5.1pp', 'DEPTH 2,000', 'FAIR 0.590', 'MKT-C 0.338 ▲', 'HEDGE 0.412', 'VOL 18k'];
    let x = 10; let i = 0;
    while (x < w) { const s = items[i++ % items.length]; g.fillStyle = i % 3 ? '#9aa6b8' : accent; g.fillText(s, x, h / 2); x += g.measureText(s).width + 60; }
  }, [1, 1]);
  return t;
}

function buildRoom(scene, P, renderer) {
  const acc = '#' + P.accent.toString(16).padStart(6, '0');
  const room = new THREE.Group(); scene.add(room);
  const floor = add(room, new THREE.PlaneGeometry(30, 30), std(0x0a0c10, 0.32, 0.25), [0, 0, 0], [-Math.PI / 2, 0, 0], null, false);
  floor.receiveShadow = true;
  add(room, new THREE.PlaneGeometry(18, 7), std(0x0b0e14, 0.9), [0, 3.5, -4.4], null, null, false);
  const sky = skylineTex();
  add(room, new THREE.PlaneGeometry(11, 3.3), new THREE.MeshBasicMaterial({map: sky, toneMapped: true}), [0, 2.05, -4.35], null, null, false);
  for (let i = -5; i <= 5; i++) add(room, G.box, std(0x111419, 0.6, 0.4), [i * 1.1, 2.05, -4.3], null, [0.05, 3.3, 0.05], false);
  add(room, G.box, std(0x111419, 0.6, 0.4), [0, 3.72, -4.3], null, [11, 0.06, 0.06], false);
  const ticker = tickerTex(acc);
  const tick = add(room, new THREE.PlaneGeometry(9, 0.28), new THREE.MeshBasicMaterial({map: ticker}), [0, 3.95, -4.34], null, null, false);
  // ceiling light strips
  for (const x of [-2.4, 0, 2.4]) add(room, G.box, new THREE.MeshBasicMaterial({color: 0xbfd2ff}), [x, 3.4, -1.5], null, [0.05, 0.02, 7], false);
  // desks and monitors
  const deskM = std(0x14171d, 0.45, 0.3);
  const frameM = std(0x07080a, 0.35, 0.5);
  const textures = [0, 1, 2, 3].map((k) => chartTex(k === 1 ? '#8aa8ff' : acc, 5 + k * 13, k));
  const off = offlineTex();
  const screens = [];
  const rows = [{z: -1.35, xs: [-2.05, 2.25], y: 0.74}, {z: -2.5, xs: [-3.1, -0.9, 1.3, 3.5], y: 0.74}, {z: -3.5, xs: [-2.2, 0.1, 2.4], y: 0.74}];
  rows.forEach((row, ri) => row.xs.forEach((x, xi) => {
    add(room, G.box, deskM, [x, row.y, row.z], null, [1.7, 0.04, 0.7]);
    add(room, G.box, deskM, [x, row.y / 2, row.z - 0.3], null, [1.6, row.y, 0.04], false);
    for (let m = -1; m <= 1; m++) {
      const mx = x + m * 0.56, my = row.y + 0.36, mz = row.z - 0.15 + Math.abs(m) * 0.06;
      const rotY = -m * 0.22;
      const fr = add(room, G.box, frameM, [mx, my, mz], [0, rotY, 0], [0.56, 0.34, 0.025], false);
      const tex = textures[(ri * 3 + xi + m + 4) % 4];
      const sm = new THREE.MeshBasicMaterial({map: tex, toneMapped: true});
      const sc = add(room, new THREE.PlaneGeometry(0.53, 0.31), sm, [mx + Math.sin(rotY) * 0.014, my, mz + Math.cos(rotY) * 0.014], [0, rotY, 0], null, false);
      screens.push({mesh: sc, tex, on: sm});
      add(room, G.cyl, frameM, [mx, row.y + 0.1, mz - 0.03], null, [0.012, 0.18, 0.012], false);
      void fr;
    }
  }));
  // environment for reflections (skin, fabric sheen, glasses, metal)
  const envScene = new THREE.Scene();
  envScene.background = new THREE.Color(0x05070b);
  const eb = (c, p, s) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({color: c, side: THREE.DoubleSide})); m.position.set(p[0], p[1], p[2]); m.scale.set(s[0], s[1], 1); m.lookAt(0, 1.4, 0); envScene.add(m); };
  eb(0xfff0dd, [-3, 3, 3], [3, 2]); eb(0x9fb8ff, [0, 5, 0], [6, 1]); eb(P.accent, [3, 2, -3], [3, 3]); eb(0x404a60, [0, 1.5, -5], [10, 3]); eb(0x2a1e1a, [0, -1, 2], [8, 2]);
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(envScene, 0.04).texture;
  scene.environmentIntensity = 0.55;
  pm.dispose();
  return {room, screens, off, ticker, textures, tick};
}

// ════════════════════════════════════════════════════════════════════
// THE DESKS (Archer, Scout): original procedural geometry; every screen is
// redrawn from the desk RECORD the page passes in -- nothing is invented
// ════════════════════════════════════════════════════════════════════
const NM = 'NOT MEASURED';
function dv(v, d) { return v === null || v === undefined ? NM : (typeof v === 'number' ? (Math.abs(v) < 1 && v !== 0 ? v.toFixed(d === undefined ? 4 : d) : String(Math.round(v * 100) / 100)) : String(v)); }
function txt(g, s, x, y, col, size, max) {
  g.fillStyle = col; g.font = (size || 15) + 'px monospace';
  let t = String(s === null || s === undefined ? '' : s);
  if (max) while (t.length > 3 && g.measureText(t).width > max) t = t.slice(0, -2);
  g.fillText(t, x, y);
}
function screenHead(g, w, title, acc) {
  g.fillStyle = '#03060a'; g.fillRect(0, 0, w, 1000);
  g.fillStyle = 'rgba(255,255,255,0.04)'; g.fillRect(0, 0, w, 30);
  txt(g, title, 12, 21, acc, 15);
}
const DESK_SCREENS = {
  execution: [
    ['DEPTH AT DECISION', (g, w, h, d, acc) => {
      const a = d && d.current_analysis, b = a && a.book;
      screenHead(g, w, 'DEPTH AT DECISION', acc);
      if (!b || b.mid === null || b.mid === undefined) { txt(g, 'NO RECORDED BOOK', 12, h / 2, '#6b7686', 22); return; }
      const rows = [['BEST ASK (ACQ)', b.best_acquisition, '#ff7d73'], ['MID', b.mid, '#c9d3e0'], ['BEST BID (EXIT)', b.best_exit, '#4fd197']];
      rows.forEach((r, i) => { txt(g, r[0], 16, 74 + i * 52, '#8391a6', 16); txt(g, dv(r[1], 3), w - 150, 74 + i * 52, r[2], 26); });
      const wk = b.walk || {};
      txt(g, 'WALK ' + dv(wk.filled_qty, 0) + ' @ VWAP ' + dv(wk.vwap, 3), 16, h - 22, acc, 16, w - 24);
    }],
    ['EXECUTION ANALYSIS', (g, w, h, d, acc) => {
      const a = d && d.current_analysis;
      screenHead(g, w, 'EXECUTION ANALYSIS · SHADOW', acc);
      if (!a) { txt(g, 'NO ESTIMATE RECORDED', 12, h / 2, '#6b7686', 22); return; }
      txt(g, a.recommendation, 16, 78, a.recommendation === 'SKIP_EXECUTION' ? '#ff7d73' : a.recommendation === 'WAIT' ? '#f0b54f' : '#4fd197', 34, w - 24);
      const rows = [['THEORETICAL EDGE', a.theoretical_edge_pp], ['NET EXECUTABLE EDGE', a.expected_net_executable_edge_pp], ['FILL PROBABILITY', a.expected_fill_probability], ['EXP. SLIPPAGE', a.expected_slippage_pp]];
      rows.forEach((r, i) => { txt(g, r[0], 16, 122 + i * 36, '#8391a6', 15); txt(g, dv(r[1]), w - 190, 122 + i * 36, '#e8eef6', 18); });
    }],
    ['CAPITAL & OUTCOME', (g, w, h, d, acc) => {
      screenHead(g, w, 'CAPITAL · PREDICTED vs REALIZED', acc);
      const a = d && d.current_analysis, pv = d && d.predicted_vs_realized, ec = d && d.economic_score;
      txt(g, 'CAPITAL-HOURS', 16, 70, '#8391a6', 15); txt(g, dv(a && a.expected_capital_hours, 2), w - 190, 70, '#e8eef6', 18);
      txt(g, 'PREDICTED LOSS', 16, 110, '#8391a6', 15); txt(g, dv(pv && pv.predicted_execution_loss_pp), w - 190, 110, '#e8eef6', 18);
      txt(g, 'REALIZED LOSS', 16, 150, '#8391a6', 15); txt(g, dv(pv && pv.realized_execution_loss_pp), w - 190, 150, '#e8eef6', 18);
      txt(g, 'VS NAIVE (USD)', 16, 190, '#8391a6', 15); txt(g, ec && ec.value !== null && ec.value !== undefined ? dv(ec.value, 2) : NM, w - 190, 190, acc, 18);
    }],
  ],
  research: [
    ['SPORTS FEED', (g, w, h, d, acc) => {
      screenHead(g, w, 'SPORTS FEED · COMPLIANT SOURCES', acc);
      const s = (d && d.data_sources) || [];
      if (!s.length) { txt(g, 'NO SOURCE DECLARED', 12, h / 2, '#6b7686', 22); return; }
      s.slice(0, 4).forEach((x, i) => { txt(g, x.compliance_passed ? 'PASS' : 'REFUSED', 16, 70 + i * 50, x.compliance_passed ? '#4fd197' : '#ff7d73', 18); txt(g, x.name, 120, 70 + i * 50, '#c9d3e0', 14, w - 132); txt(g, x.licensing_class, 120, 90 + i * 50, '#6b7686', 12, w - 132); });
    }],
    ['FEATURES UNDER TEST', (g, w, h, d, acc) => {
      screenHead(g, w, 'FEATURES UNDER TEST', acc);
      const f = (d && d.features_under_test) || [];
      if (!f.length) { txt(g, 'NO FEATURE UNDER TEST', 12, h / 2, '#6b7686', 22); return; }
      f.slice(0, 4).forEach((x, i) => {
        const n = Number(x.samples_settled) || 0, m = Number(x.min_sample) || 1;
        txt(g, x.feature, 16, 66 + i * 52, '#c9d3e0', 14, w - 32);
        g.fillStyle = '#1b2430'; g.fillRect(16, 74 + i * 52, w - 32, 10);
        g.fillStyle = acc; g.fillRect(16, 74 + i * 52, Math.min(1, n / m) * (w - 32), 10);
        txt(g, n + '/' + m + ' settled · ' + (x.samples_frozen || 0) + ' frozen', 16, 100 + i * 52, '#6b7686', 12);
      });
    }],
    ['WEATHER / DATA', (g, w, h, d, acc) => {
      screenHead(g, w, 'WEATHER · DATA', acc);
      const s = ((d && d.data_sources) || []).filter((x) => /weather/i.test(x.name || x.source_id || ''));
      txt(g, s.length ? (s[0].compliance_passed ? 'LICENSED' : 'NO LICENSED SOURCE') : 'NOT DECLARED', 16, 78, s.length && !s[0].compliance_passed ? '#f0b54f' : '#c9d3e0', 22, w - 32);
      txt(g, 'NOTHING INGESTED WITHOUT A LICENSE', 16, 112, '#6b7686', 13, w - 32);
      const ip = d && d.incremental_predictive_value;
      txt(g, 'INCREMENTAL VALUE', 16, 160, '#8391a6', 15); txt(g, ip && ip.value !== null && ip.value !== undefined ? dv(ip.value) : NM, w - 190, 160, acc, 18);
    }],
  ],
};
function buildDesk(room, P) {
  const acc = '#' + P.accent.toString(16).padStart(6, '0');
  const kind = P.desk;
  const deskM = std(kind === 'execution' ? 0x0f1319 : 0x1b1712, kind === 'execution' ? 0.35 : 0.7, kind === 'execution' ? 0.5 : 0.05);
  const frameM = std(0x06070a, 0.3, 0.6);
  const g = group(room, [0.12, 0, -0.95]);
  // the desk: a long top, a modesty panel, and (execution) a brushed rail /
  // (research) a lower shelf of reference binders
  add(g, G.box, deskM, [0, 0.74, 0], null, [2.6, 0.045, 0.62]);
  add(g, G.box, deskM, [0, 0.37, -0.28], null, [2.5, 0.74, 0.03], false);
  if (kind === 'execution') add(g, G.box, std(0x8a97a8, 0.25, 0.9), [0, 0.765, 0.3], null, [2.6, 0.012, 0.012], false);
  else for (let i = 0; i < 9; i++) add(g, G.box, std([0x3d5a40, 0x6a4e2d, 0x2f4a5a][i % 3], 0.8), [-1.0 + i * 0.07, 0.86, -0.22], null, [0.05, 0.22, 0.16], false);
  const screens = [], defs = DESK_SCREENS[kind];
  defs.forEach((def, i) => {
    // two desk monitors either side of the agent, the third wall-mounted
    // above him, so none is hidden behind the character
    const mid = i === 1, sc_ = mid ? 1.18 : 1.0;
    const x = (i - 1) * 0.8, rotY = -(i - 1) * 0.36, y = mid ? 1.98 : 1.22, z = mid ? -0.3 : -0.05;
    add(g, G.box, frameM, [x, y, z], [0, rotY, 0], [0.8 * sc_, 0.47 * sc_, 0.025], false);
    const c = document.createElement('canvas'); c.width = 512; c.height = 300;
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const mat = new THREE.MeshBasicMaterial({map: tex, toneMapped: true});
    const mesh = add(g, new THREE.PlaneGeometry(0.76 * sc_, 0.44 * sc_), mat, [x + Math.sin(rotY) * 0.014, y, z + Math.cos(rotY) * 0.014], [0, rotY, 0], null, false);
    if (mid) add(g, G.box, frameM, [x, y + 0.45, z - 0.02], null, [0.03, 0.42, 0.03], false);
    else add(g, G.cyl, frameM, [x, 0.92, z - 0.03], null, [0.014, 0.34, 0.014], false);
    screens.push({mesh, tex, on: mat, canvas: c, draw: def[1], title: def[0]});
  });
  function update(desk) {
    for (const sc of screens) {
      const ctx = sc.canvas.getContext('2d');
      ctx.clearRect(0, 0, sc.canvas.width, sc.canvas.height);
      try { sc.draw(ctx, sc.canvas.width, sc.canvas.height, desk || null, acc); }
      catch (_) { screenHead(ctx, sc.canvas.width, sc.title, acc); txt(ctx, 'RECORD UNREADABLE', 12, 150, '#6b7686', 20); }
      sc.tex.needsUpdate = true;
    }
  }
  update(null);
  return {screens, update};
}

// ════════════════════════════════════════════════════════════════════
// MOUNT
// ════════════════════════════════════════════════════════════════════
export function mount(stage, opts) {
  const agent = (opts && opts.agent) || stage.getAttribute('data-agent');
  const P = PERSONAS[agent];
  if (!P) throw new Error('unknown agent ' + agent);
  const reduced = !!(opts && opts.reducedMotion);
  const canvas = stage.querySelector('canvas');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, powerPreference: 'low-power', preserveDrawingBuffer: false});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.3;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x05070b);
  scene.fog = new THREE.FogExp2(0x05070b, 0.11);
  const camera = new THREE.PerspectiveCamera(26, 1, 0.05, 40);
  const cam = (opts && opts.camera) || null;       // a fixed camera (close-up inspection only)
  const camBase = cam ? new V3(cam[0], cam[1], cam[2]) : new V3(0.12, 1.47, 2.62);
  camera.position.copy(camBase);
  const lookAt = cam ? new V3(cam[3], cam[4], cam[5]) : new V3(0.12, 1.43, 0);

  // cinematic lighting: warm key, accent rim, cool fill, screen glow
  const hemi = new THREE.HemisphereLight(0x9fb2dc, 0x2a1c16, 0.7); scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffe2c4, 2.8); key.position.set(-1.8, 3.2, 2.6); key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024); key.shadow.camera.left = -1.2; key.shadow.camera.right = 1.2; key.shadow.camera.top = 2.2; key.shadow.camera.bottom = -0.2; key.shadow.bias = -0.0004; key.shadow.normalBias = 0.02;
  key.target.position.set(0, 1.1, 0); scene.add(key); scene.add(key.target);
  const rim = new THREE.SpotLight(P.accent, 26, 8, 0.6, 0.6, 1.6); rim.position.set(1.6, 2.6, -1.8); rim.target.position.set(0, 1.4, 0); scene.add(rim); scene.add(rim.target);
  const rim2 = new THREE.SpotLight(0x9bb4ff, 10, 8, 0.7, 0.7, 1.6); rim2.position.set(-1.8, 2.2, -1.6); rim2.target.position.set(0, 1.5, 0); scene.add(rim2); scene.add(rim2.target);
  const fill = new THREE.PointLight(0x9fb4ff, 1.3, 6, 1.6); fill.position.set(1.8, 1.4, 2.2); scene.add(fill);
  const glow = new THREE.PointLight(P.accent, 1.4, 4, 2); glow.position.set(0, 1.2, -1.2); scene.add(glow);
  const lights = [[hemi, hemi.intensity], [key, key.intensity], [rim, rim.intensity], [rim2, rim2.intensity], [fill, fill.intensity], [glow, glow.intensity]];
  if (P.beauty) {
    // flattering cinematic light: a soft warm frontal key near the lens and a stronger accent rim
    const beauty = new THREE.PointLight(0xffe6d6, 2.2, 5, 1.8); beauty.position.set(0.25, 1.75, 2.1); scene.add(beauty);
    lights.push([beauty, beauty.intensity]);
    rim.intensity *= 1.35; lights[2][1] = rim.intensity;
  }

  const env = buildRoom(scene, P, renderer);
  // Archer's / Scout's own desk, its screens drawn from the desk record only
  const desk = P.desk ? buildDesk(env.room, P) : null;
  if (desk) { for (const sc of desk.screens) env.screens.push(sc); desk.update((opts && opts.desk) || null); }
  const rig = buildCharacter(P);
  rig.root.position.set(0.12, 0, 0);
  scene.add(rig.root);

  // ── animation state ─────────────────────────────────────────────
  const R = rng(P.seed * 7 + 3);
  const rand = (a, b) => a + (b - a) * R();
  const S = {mode: document.body.getAttribute('data-cc-mode') || 'unavailable', t: 0, paused: false, frames: 0,
    blinkAt: rand(P.blink[0], P.blink[1]), blink: 0, lid: -0.46,
    head: {yaw: 0, pitch: 0, roll: 0}, look: {yaw: 0, pitch: 0}, lookHold: 0, eyes: {yaw: 0, pitch: 0},
    gesture: null, gestureEnd: 0, nextGesture: rand(P.gestureEvery[0], P.gestureEvery[1]),
    weight: 0, weightTgt: 0, nextWeight: rand(P.weightEvery[0], P.weightEvery[1]),
    lean: 0, slump: 0, jaw: 0, light: 1, brow: 0, visible: true, lastT: 0, screensOff: null};

  function modeTargets() {
    const m = S.mode;
    const base = P.poses[m] || P.poses.monitoring;
    let L = base.L, Rr = base.R;
    if (S.gesture) { if (S.gesture.L) L = S.gesture.L; if (S.gesture.R) Rr = S.gesture.R; }
    return {1: pose(L, 1), '-1': pose(Rr, -1)};
  }
  function pickLook() {
    const m = S.mode;
    if (S.gesture && S.gesture.look) return {yaw: S.gesture.look[0], pitch: S.gesture.look[1], hold: S.gesture.dur};
    if (m === 'monitoring') { const r = R(); if (r < 0.15) return {yaw: 0, pitch: 0.02, hold: rand(1.2, 2.2)}; return {yaw: rand(-0.55, 0.55), pitch: rand(-0.04, 0.1), hold: rand(1.2, 3.8) / P.tempo}; }
    if (m === 'reviewing') return {yaw: rand(-0.12, 0.2), pitch: rand(-0.42, -0.26), hold: rand(0.8, 2.2)};
    if (m === 'waiting') return {yaw: rand(-0.18, 0.18), pitch: rand(-0.05, 0.05), hold: rand(2.5, 5)};
    if (m === 'speaking') return {yaw: rand(-0.06, 0.06), pitch: rand(0.0, 0.05), hold: rand(1.0, 2.2)};
    return {yaw: 0, pitch: -0.24, hold: 10};
  }

  function update(dt, instant) {
    const T = (S.t += dt);
    const m = S.mode;
    const off = m === 'unavailable';
    const sm = instant ? 1e-4 : P.smooth;
    // gestures (only when working: never animate an offline agent)
    if (!off && !instant) {
      if (S.gesture && T > S.gestureEnd) { S.gesture = null; S.nextGesture = T + rand(P.gestureEvery[0], P.gestureEvery[1]); S.lookHold = 0; }
      if (!S.gesture && T > S.nextGesture) {
        const pool = P.gestures[m] || [];
        if (pool.length) { S.gesture = pool[Math.floor(R() * pool.length)]; S.gestureEnd = T + S.gesture.dur; S.lookHold = 0; }
        else S.nextGesture = T + rand(P.gestureEvery[0], P.gestureEvery[1]);
      }
    } else if (off) S.gesture = null;
    // weight shifts and posture
    if (!off && !instant && T > S.nextWeight) { S.weightTgt = rand(-1, 1) * (m === 'waiting' ? 1 : 0.7); S.nextWeight = T + rand(P.weightEvery[0], P.weightEvery[1]); }
    if (off) S.weightTgt = 0;
    S.weight = instant ? S.weightTgt : damp(S.weight, S.weightTgt, 0.9 / P.tempo, dt);
    const leanT = {monitoring: 0.0, reviewing: 0.07, waiting: -0.02, speaking: 0.03, unavailable: 0.1}[m] || 0;
    S.lean = instant ? leanT : damp(S.lean, leanT, 0.6, dt);
    const w = S.weight;
    rig.hips.position.x = w * 0.016;
    rig.hips.rotation.z = w * 0.028;
    rig.spine.rotation.z = -w * 0.034;
    rig.spine.rotation.x = S.lean;
    rig.chest.rotation.z = -w * 0.01;
    for (const lg of rig.legs) { lg.thigh.rotation.z = -w * 0.028; lg.knee.rotation.x = (lg.s * w > 0 ? 0.0 : 0.06 * Math.abs(w)); lg.thigh.rotation.x = -(lg.s * w > 0 ? 0 : 0.03 * Math.abs(w)); }
    // breathing
    const bh = (off ? 0.14 : m === 'waiting' ? P.breathHz * 0.85 : P.breathHz);
    const br = (instant || off) ? 0 : Math.sin(T * TAU * bh);
    rig.torso.scale.set(1 + br * 0.006, 1 + br * 0.004, 1 + br * 0.012);
    rig.chest.position.y = 0.24 + br * 0.0025;
    // look target
    if (!instant) { S.lookHold -= dt; if (S.lookHold <= 0) { const l = pickLook(); S.look.yaw = l.yaw; S.look.pitch = l.pitch; S.lookHold = l.hold; } }
    else { const l = pickLook(); S.look.yaw = l.yaw; S.look.pitch = l.pitch; }
    const micro = (off || instant) ? 0 : 1;
    const ny = micro * (Math.sin(T * 0.9 * P.tempo) * 0.02 + Math.sin(T * 2.3) * 0.008);
    const np = micro * (Math.sin(T * 0.7 * P.tempo + 1) * 0.012);
    const nod = (m === 'speaking' && !instant) ? Math.max(0, Math.sin(T * 2.4)) * 0.05 : 0;
    const hsm = instant ? 1e-4 : 0.42 / P.tempo;
    S.head.yaw = damp(S.head.yaw, S.look.yaw * 0.7 + ny, hsm, dt);
    S.head.pitch = damp(S.head.pitch, S.look.pitch * 0.75 + np - nod, hsm, dt);
    S.head.roll = damp(S.head.roll, (m === 'speaking' ? Math.sin(T * 0.8) * 0.04 : 0) + w * 0.02, 0.8, dt);
    rig.neck.rotation.set(S.head.pitch * 0.35, S.head.yaw * 0.4, S.head.roll * 0.4);
    rig.head.rotation.set(S.head.pitch * 0.65, S.head.yaw * 0.6, S.head.roll * 0.6);
    // eyes lead the head
    const ey = clamp((S.look.yaw - S.head.yaw) * 1.4 + (micro ? Math.sin(T * 7.1) * 0.01 : 0), -0.35, 0.35);
    const ep = clamp((S.look.pitch - S.head.pitch) * 1.2, -0.25, 0.25) + (off ? -0.15 : 0);
    S.eyes.yaw = damp(S.eyes.yaw, ey, instant ? 1e-4 : 0.06, dt);
    S.eyes.pitch = damp(S.eyes.pitch, ep, instant ? 1e-4 : 0.06, dt);
    for (const e of rig.eyes) e.rotation.set(-S.eyes.pitch, S.eyes.yaw, 0);
    // blinking
    let lidT = off ? 0.9 : (m === 'reviewing' ? -0.3 : m === 'speaking' ? -0.52 : -0.46) - S.eyes.pitch * 0.7;
    if (!off && !instant) {
      if (T > S.blinkAt) { S.blink = 1; S.blinkAt = T + rand(P.blink[0], P.blink[1]) * (m === 'speaking' ? 0.7 : 1); if (R() < 0.12) S.blinkAt = T + 0.28; }
      if (S.blink > 0) { S.blink = Math.max(0, S.blink - dt / 0.17); }
      const bp = S.blink > 0 ? Math.sin((1 - S.blink) * Math.PI) : 0;
      lidT = lerp(lidT, 1.45, bp);
    }
    S.lid = instant ? lidT : damp(S.lid, lidT, 0.025, dt);
    for (const l of rig.lids) l.rotation.x = S.lid;
    // brows
    const browT = {monitoring: 0.0, reviewing: -0.0025, waiting: 0.0015, speaking: 0.002, unavailable: -0.002}[m] || 0;
    const emph = (m === 'speaking' && !instant) ? Math.max(0, Math.sin(T * 1.7)) * 0.003 : 0;
    S.brow = instant ? browT : damp(S.brow, browT + emph, 0.2, dt);
    for (const b of rig.brows) { b.g.position.y = b.y0 + S.brow; b.g.rotation.z = (m === 'reviewing' ? 0.12 : 0.0) * b.s * -1; }
    // mouth: speaking only while a reply renders
    // RENDERED TEXT IS NOT SPEECH: the mouth moves only with real audio
    // (the 'cc:speech' amplitude hook); a chat reply alone keeps it closed
    const jawT = (S.speech && !off && !instant) ? clamp(S.speech, 0, 1) * 0.085 : 0.0;
    S.jaw = instant ? jawT : damp(S.jaw, jawT, 0.04, dt);
    rig.jaw.rotation.x = S.jaw;
    // hair sway (restrained)
    if (rig.hairBack) rig.hairBack.rotation.set(micro * Math.sin(T * 1.1) * 0.02 - S.head.pitch * 0.2, -S.head.yaw * 0.15, micro * Math.sin(T * 0.8) * 0.015);
    // arms
    const tg = modeTargets();
    for (const s of [1, -1]) {
      const arm = rig.arms[s], t = tg[s], c = arm.cur;
      const H = t.H.clone();
      if (t.beat && !instant) H.y += Math.sin(T * 3.1 + s) * 0.02;
      if (t.swipe && !instant) H.x += Math.sin(T * 2.4) * 0.035;
      if (!off && !instant) H.y += br * 0.004;
      if (instant) { c.H.copy(H); c.pole.copy(t.pole); c.b.copy(t.b); c.curl = t.curl; c.index = t.index; }
      else {
        const k = 1 - Math.exp(-dt / sm);
        c.H.lerp(H, k); c.pole.lerp(t.pole, k).normalize(); c.b.lerp(t.b, k).normalize();
        c.curl = lerp(c.curl, t.curl, k); c.index = lerp(c.index, t.index, k);
      }
      solveArm(arm, c.H, c.pole, c.b);
      arm.fingers.forEach((f) => { const cu = f.index ? c.curl * (1 - c.index) : c.curl; f.prox.rotation.x = cu * 1.2; f.dist.rotation.x = cu * 1.1; });
      arm.fingers.thumb.rotation.x = 0.35 + c.curl * 0.5;
      if (arm.prop) arm.prop.obj.visible = !off && t.prop === arm.prop.pose && c.H.distanceTo(t.H) < 0.12;
    }
    // lights and screens follow availability
    const lt = off ? 0.38 : 1;
    S.light = instant ? lt : damp(S.light, lt, 0.5, dt);
    for (const [l, i0] of lights) l.intensity = i0 * (l === rim || l === glow ? S.light * S.light : S.light);
    const wantOff = off;
    if (S.screensOff !== wantOff) {
      S.screensOff = wantOff;
      for (const sc of env.screens) { sc.mesh.material.map = wantOff ? env.off : sc.tex; sc.mesh.material.color.setScalar(wantOff ? 0.35 : 1); sc.mesh.material.needsUpdate = true; }
      env.tick.material.color.setScalar(wantOff ? 0.25 : 1);
    }
    if (!off && !instant) { env.ticker.offset.x = (T * 0.012) % 1; for (let i = 0; i < env.textures.length; i++) env.textures[i].offset.x = Math.sin(T * 0.05 + i) * 0.004; }
    // camera: a slow, small drift
    const still_ = instant || off;
    camera.position.set(camBase.x + (still_ ? 0 : Math.sin(T * 0.07) * 0.035), camBase.y + (still_ ? 0 : Math.sin(T * 0.05) * 0.012), camBase.z);
    camera.lookAt(lookAt);
    // AN OFFLINE AGENT IS NOT ANIMATED: once the offline pose and the dimmed
    // lights have settled, the loop stops until the mode changes.
    S.offSettled = off && Math.abs(S.light - lt) < 0.01 && Math.abs(S.lean - leanT) < 0.002 && Math.abs(S.head.pitch - S.look.pitch * 0.75) < 0.004;
  }

  function resize() {
    const w = Math.max(1, stage.clientWidth), h = Math.max(1, stage.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = cam ? (cam[6] || 20) : (w / h < 1.05 ? 32 : 26);
    if (!cam) camBase.z = w / h < 0.8 ? 3.3 : 2.62;
    camera.updateProjectionMatrix();
  }
  resize();

  let raf = 0, dead = false;
  function frame(now) {
    raf = 0;
    if (dead) return;
    const t = now / 1000;
    const dt = S.lastT ? clamp(t - S.lastT, 0, 0.05) : 1 / 60;
    S.lastT = t;
    update(dt, false);
    renderer.render(scene, camera);
    S.frames++;
    if (S.frames % 30 === 0) stage.setAttribute('data-cc-frames', String(S.frames));
    schedule();
  }
  function schedule() {
    if (reduced || dead || raf || S.paused || document.hidden || !S.visible) return;
    if (S.mode === 'unavailable' && S.offSettled) { stage.setAttribute('data-cc-frames', 'offline-still'); return; }
    raf = requestAnimationFrame(frame);
  }
  function still() { update(1 / 60, true); renderer.render(scene, camera); stage.setAttribute('data-cc-frames', 'static'); }

  function setMode(m) {
    if (!m || m === S.mode) return;
    S.mode = m; S.gesture = null; S.lookHold = 0; S.nextGesture = S.t + rand(1.5, 3); S.offSettled = false;
    stage.setAttribute('data-cc-pose', m);
    if (reduced || S.paused) still(); else { S.lastT = 0; schedule(); }
  }
  const onMode = (e) => setMode(e.detail && e.detail.mode);
  const onSpeech = (e) => { S.speech = e.detail && typeof e.detail.amplitude === 'number' ? e.detail.amplitude : 0; if (S.speech) schedule(); };
  window.addEventListener('cc:speech', onSpeech);
  const onDesk = (e) => { if (!desk) return; desk.update((e.detail && e.detail.desk) || null); if (reduced || S.paused) still(); else schedule(); };
  window.addEventListener('cc:desk', onDesk);
  const onPause = (e) => { S.paused = !!(e.detail && e.detail.paused); if (S.paused) { if (raf) cancelAnimationFrame(raf); raf = 0; } else { S.lastT = 0; schedule(); } };
  const onVis = () => { S.lastT = 0; schedule(); };
  window.addEventListener('cc:mode', onMode);
  window.addEventListener('cc:pause', onPause);
  document.addEventListener('visibilitychange', onVis);
  let ro = null, io = null;
  if (window.ResizeObserver) { ro = new ResizeObserver(() => { resize(); if (reduced || S.paused) still(); }); ro.observe(stage); }
  if (window.IntersectionObserver) { io = new IntersectionObserver((es) => { S.visible = es.some((e) => e.isIntersecting); S.lastT = 0; schedule(); }); io.observe(stage); }
  canvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); dead = true; stage.classList.remove('cc-3d-on'); stage.setAttribute('data-cc-3d', 'off'); stage.setAttribute('data-cc-why', 'WebGL context lost'); });

  stage.setAttribute('data-cc-pose', S.mode);
  still();
  stage.classList.add('cc-3d-on');
  stage.setAttribute('data-cc-3d', reduced ? 'static' : 'on');
  try { window.dispatchEvent(new CustomEvent('cc:ready')); } catch (_) { /* ignore */ }
  schedule();
  return {setMode, dispose() { dead = true; if (raf) cancelAnimationFrame(raf); window.removeEventListener('cc:mode', onMode); window.removeEventListener('cc:desk', onDesk); window.removeEventListener('cc:pause', onPause); document.removeEventListener('visibilitychange', onVis); if (ro) ro.disconnect(); if (io) io.disconnect(); renderer.dispose(); }};
}

export const DESCRIBE = {characters: Object.keys(PERSONAS), modes: ['monitoring', 'reviewing', 'waiting', 'speaking', 'unavailable'], original: true, three: '0.185.1'};
