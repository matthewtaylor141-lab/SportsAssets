/* BETTOR HEADQUARTERS · THE 3D TRADING FLOOR (three.js, vendored r185).
 *
 * Presentation only: it is handed the floor payload (GET /api/command/floor)
 * by floor.js and draws it. Every visible behaviour maps to a recorded fact:
 *   typing        a run is in progress inside the heartbeat window
 *                 (activity_basis RUN_IN_PROGRESS) or a Slack request is being
 *                 worked (SLACK_REQUEST)
 *   reviewing     REVIEWING / CHALLENGING from a recorded output
 *   walking       ONLY when a collaboration edge newer than WALK_WINDOW_S
 *                 arrives (a real row with its evidence id); once per edge
 *   light trail   one per collaboration edge in the payload's window
 *   still / dim   STALE, NOT_DEPLOYED or no data (unknown is never animated)
 * prefers-reduced-motion: no idle motion, no walks, no moving trails; the
 * scene renders only when the data or the camera changes.
 *
 * Characters: the licensed Microsoft Rocketbox models in the repo
 * (team-demo/assets/models, MIT). EVERY agent wears its OWN model -- seven
 * distinct people; no seat reuses or recolours another agent's body (see
 * CAST and CREDITS). Eddie's headset is a desk prop. Nothing is downloaded
 * from anywhere else. */
import * as THREE from './team-demo/assets/three.module.min.js';
import {clone as cloneSkinned} from './team-demo/assets/SkeletonUtils.js';
import {AvatarController, resolveBones, resolveBlendshapes, resolveVisemes,
        buildJoints, armsDown} from './team-demo/assets/cc_avatar.js';

const MODELS = './team-demo/assets/models/';
export const CREDITS = {
  derek: 'Rocketbox Business_Male_03 (MIT, © 2020 Microsoft)',
  xavier: 'Rocketbox Business_Male_05 (MIT, © 2020 Microsoft)',
  audrey: 'Rocketbox Business_Female_04 (MIT, © 2020 Microsoft)',
  karen: 'Rocketbox Business_Female_02 (MIT, © 2020 Microsoft)',
  allocator: 'Rocketbox Business_Female_03 (MIT, © 2020 Microsoft)',
  eddie: 'Rocketbox Business_Male_04, with a headset prop (MIT, © 2020 Microsoft)',
  scout: 'Rocketbox Business_Male_06 (MIT, © 2020 Microsoft)'
};
// which licensed model each seat wears: its own, never a recoloured copy of
// another agent's (the tint shader below only dims / desaturates by state)
export const CAST = {
  derek: {model: 'derek', tint: null},
  xavier: {model: 'xavier', tint: null},
  audrey: {model: 'audrey', tint: null},
  karen: {model: 'karen', tint: null},
  allocator: {model: 'allie', tint: null},
  eddie: {model: 'eddie', tint: null, headset: true},
  scout: {model: 'scout', tint: null}
};
const WALK_WINDOW_S = 600;          // an edge newer than this may walk once
const ARC_R = 7.0, ARC_Z = 2.4, ARC_SPAN = 150 * Math.PI / 180;
const DESK_H = 1.04;

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const damp = (cur, tgt, lambda, dt) => cur + (tgt - cur) * (1 - Math.exp(-lambda * dt));
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

export function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (e) { return false; }
}

/* ── canvas text helpers ─────────────────────────────────────────── */
function wrap(ctx, text, x, y, maxW, lineH, maxLines) {
  const words = String(text || '').split(/\s+/);
  let line = '', n = 0;
  for (let i = 0; i < words.length; i++) {
    const test = line ? line + ' ' + words[i] : words[i];
    if (ctx.measureText(test).width > maxW && line) {
      if (n === maxLines - 1) { ctx.fillText(line.replace(/.{0,2}$/, '') + '…', x, y + n * lineH); return n + 1; }
      ctx.fillText(line, x, y + n * lineH); n++; line = words[i];
    } else line = test;
  }
  if (line) { ctx.fillText(line, x, y + n * lineH); n++; }
  return n;
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function fitText(ctx, text, maxW) {
  let t = String(text == null ? '' : text);
  if (ctx.measureText(t).width <= maxW) return t;
  while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
  return t + '…';
}

/* ── the suit recolour: dark, unsaturated texels move toward the tint;
 *    skin (saturated) and the white shirt (bright) are left alone ─────── */
function tintMaterial(mat, opts) {
  const m = mat.clone();
  m.userData.uniforms = {uTint: {value: new THREE.Color(0, 0, 0)}, uTintAmt: {value: 0},
                         uDim: {value: 1}, uDesat: {value: 0}};
  if (opts && opts.tint) { m.userData.uniforms.uTint.value.setRGB(opts.tint[0], opts.tint[1], opts.tint[2]); m.userData.uniforms.uTintAmt.value = opts.amt || 0.8; }
  const suit = !!(opts && opts.suit);
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, m.userData.uniforms);
    sh.fragmentShader = 'uniform vec3 uTint; uniform float uTintAmt; uniform float uDim; uniform float uDesat;\n' +
      sh.fragmentShader.replace('#include <map_fragment>', '#include <map_fragment>\n' +
        (suit ? '{ float lum = dot(diffuseColor.rgb, vec3(0.2126,0.7152,0.0722));\n' +
          ' float mx = max(max(diffuseColor.r,diffuseColor.g),diffuseColor.b); float mn = min(min(diffuseColor.r,diffuseColor.g),diffuseColor.b);\n' +
          ' float sat = (mx-mn)/(mx+1e-4);\n' +
          ' float k = smoothstep(0.42,0.12,sat) * smoothstep(0.30,0.04,lum);\n' +
          ' diffuseColor.rgb = mix(diffuseColor.rgb, uTint * (0.55 + lum * 9.0), k * uTintAmt); }\n' : '') +
        '{ float g = dot(diffuseColor.rgb, vec3(0.2126,0.7152,0.0722)); diffuseColor.rgb = mix(diffuseColor.rgb, vec3(g), uDesat) * uDim; }\n');
  };
  m.customProgramCacheKey = () => 'bt-floor-tint-' + (suit ? 1 : 0);
  return m;
}

/* ── the legs: hinge joints like cc_avatar's arms (model faces +Z) ───── */
function legJoints(root) {
  root.updateMatrixWorld(true);
  const find = (n) => { let b = null; root.traverse((o) => { if (!b && o.isBone && o.name === n) b = o; }); return b; };
  const rootInv = root.getWorldQuaternion(new THREE.Quaternion()).invert();
  const pos = (b) => root.worldToLocal(b.getWorldPosition(new THREE.Vector3()));
  const out = {};
  for (const side of ['L', 'R']) {
    const th = find('Bip01 ' + side + ' Thigh'), ca = find('Bip01 ' + side + ' Calf'), ft = find('Bip01 ' + side + ' Foot');
    if (!th || !ca || !ft) continue;
    for (const [b, a, c] of [[th, th, ca], [ca, ca, ft]]) {
      const d = pos(c).sub(pos(a)).normalize();
      const ax = d.clone().cross(new THREE.Vector3(0, 0, 1)).normalize();
      const toParent = rootInv.clone().multiply(b.parent.getWorldQuaternion(new THREE.Quaternion())).invert();
      out[b.name] = {bone: b, rest: b.quaternion.clone(), axis: ax.applyQuaternion(toParent).normalize()};
    }
    out[side] = {thigh: th.name, calf: ca.name};
  }
  return out;
}
function hinge(j, angle) {
  if (!j) return;
  j.bone.quaternion.copy(new THREE.Quaternion().setFromAxisAngle(j.axis, angle).multiply(j.rest));
}

/* ═══════════════════════════════════════════════════════════════════ */
export async function createFloor(host, opts) {
  const o = Object.assign({phone: false, reducedMotion: false, seats: [], onPick: null, onHover: null,
                           onFrame: null, onStatus: null}, opts || {});
  const phone = !!o.phone;
  let raf = 0, last = 0, T = 0, ema = 16, slow = 0, needsOne = true, paused = false;
  const fps = []; let frames = 0, secT = performance.now();
  const canvas = document.createElement('canvas');
  canvas.className = 'fl-canvas';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Three-dimensional trading floor: seven agent desks around a central wall. Use the agent list for the same information as text.');
  host.appendChild(canvas);
  const renderer = new THREE.WebGLRenderer({canvas, antialias: !phone, powerPreference: 'high-performance', alpha: false});
  let dpr = Math.min(window.devicePixelRatio || 1, phone ? 1.25 : 1.75);
  renderer.setPixelRatio(dpr);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = !phone;
  renderer.shadowMap.type = THREE.PCFShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#05090e');
  scene.fog = new THREE.Fog('#05090e', 22, 48);

  // a soft in-scene environment for reflections (no HDR file)
  {
    const s = new THREE.Scene();
    s.background = new THREE.Color('#0a1018');
    const panel = (c, i, p, w, h) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({color: new THREE.Color(c).multiplyScalar(i), side: THREE.DoubleSide})); m.position.set(...p); m.lookAt(0, 1.5, 0); s.add(m); };
    panel('#cfe6ff', 1.6, [0, 7, 0], 10, 1.2); panel('#7fb8ff', 0.45, [-6, 4, -4], 4, 3); panel('#ffe2c4', 0.3, [6, 4, 4], 4, 3); panel('#3a6c8c', 0.5, [0, 4, -9], 14, 4);
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(s, 0.04).texture; pm.dispose();
    scene.environmentIntensity = 0.55;
  }

  /* lights */
  scene.add(new THREE.HemisphereLight('#bcd6ff', '#0b1210', phone ? 1.1 : 0.7));
  const key = new THREE.DirectionalLight('#fff1e2', 2.0);
  key.position.set(-3, 16, 15);
  if (!phone) {
    key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
    const sc = key.shadow.camera; sc.left = -14; sc.right = 14; sc.top = 14; sc.bottom = -12; sc.near = 1; sc.far = 50;
    key.shadow.bias = -0.0004; key.shadow.normalBias = 0.03;
  }
  scene.add(key);
  // no back rim light: on the glossy floor it reads as a glare, not as state

  /* the room */
  const room = new THREE.Group(); scene.add(room);
  {
    // polished floor with a fine grid
    const gc = document.createElement('canvas'); gc.width = gc.height = 512;
    const g = gc.getContext('2d'); g.fillStyle = '#0a1219'; g.fillRect(0, 0, 512, 512);
    g.strokeStyle = 'rgba(120,170,210,.10)'; g.lineWidth = 2; g.strokeRect(0, 0, 512, 512);
    g.strokeStyle = 'rgba(120,170,210,.045)'; g.lineWidth = 1;
    for (let i = 64; i < 512; i += 64) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 512); g.moveTo(0, i); g.lineTo(512, i); g.stroke(); }
    const gt = new THREE.CanvasTexture(gc); gt.wrapS = gt.wrapT = THREE.RepeatWrapping; gt.repeat.set(16, 16); gt.colorSpace = THREE.SRGBColorSpace;
    gt.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(64, 64), new THREE.MeshStandardMaterial({map: gt, color: '#ffffff', metalness: 0.55, roughness: 0.32}));
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; room.add(floor);
    // the inlaid medallion at the centre of the floor
    const mc = document.createElement('canvas'); mc.width = mc.height = 1024;
    const m = mc.getContext('2d'); m.translate(512, 512);
    for (const [r, w, a] of [[500, 3, .5], [455, 1.5, .35], [300, 1, .22]]) { m.beginPath(); m.arc(0, 0, r, 0, Math.PI * 2); m.strokeStyle = 'rgba(150,205,240,' + a + ')'; m.lineWidth = w; m.stroke(); }
    for (let i = 0; i < 72; i++) { const an = i / 72 * Math.PI * 2; m.beginPath(); m.moveTo(Math.cos(an) * 462, Math.sin(an) * 462); m.lineTo(Math.cos(an) * (i % 6 ? 475 : 492), Math.sin(an) * (i % 6 ? 475 : 492)); m.strokeStyle = 'rgba(150,205,240,.35)'; m.lineWidth = 2; m.stroke(); }
    m.fillStyle = 'rgba(205,232,250,.85)'; m.font = '600 120px Inter, system-ui, sans-serif'; m.textAlign = 'center'; m.textBaseline = 'middle';
    m.fillText('BETTOR', 0, -10); m.font = '500 34px Inter, system-ui, sans-serif'; m.fillStyle = 'rgba(160,200,225,.7)'; m.fillText('H E A D Q U A R T E R S', 0, 92);
    const mt = new THREE.CanvasTexture(mc); mt.colorSpace = THREE.SRGBColorSpace;
    const med = new THREE.Mesh(new THREE.CircleGeometry(3.1, 96), new THREE.MeshBasicMaterial({map: mt, transparent: true, opacity: 0.55, depthWrite: false}));
    med.rotation.x = -Math.PI / 2; med.position.set(0, 0.004, ARC_Z); room.add(med);

    // the central wall
    const wall = new THREE.Mesh(new THREE.BoxGeometry(26, 8.2, 0.4), new THREE.MeshStandardMaterial({color: '#0b1520', metalness: 0.15, roughness: 0.8}));
    wall.position.set(0, 4.1, -8.8); wall.receiveShadow = true; room.add(wall);
    const trim = new THREE.Mesh(new THREE.BoxGeometry(26, 0.04, 0.05), new THREE.MeshBasicMaterial({color: '#5fb4e8'}));
    trim.position.set(0, 0.6, -8.58); room.add(trim);
    const trim2 = trim.clone(); trim2.position.y = 7.7; room.add(trim2);
    // signage
    const sc = document.createElement('canvas'); sc.width = 2048; sc.height = 160;
    const s = sc.getContext('2d'); s.fillStyle = '#dbeefa'; s.font = '600 92px Inter, system-ui, sans-serif'; s.textBaseline = 'middle';
    s.fillText('BETTOR', 40, 82); const bw = s.measureText('BETTOR').width;
    s.fillStyle = '#7fb6d8'; s.font = '400 46px Inter, system-ui, sans-serif'; s.fillText('·  HEADQUARTERS  ·  TRADING FLOOR', 80 + bw, 86);
    const st = new THREE.CanvasTexture(sc); st.colorSpace = THREE.SRGBColorSpace;
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(12.8, 1), new THREE.MeshBasicMaterial({map: st, transparent: true}));
    sign.position.set(-4.6, 7.05, -8.58); room.add(sign);
    // side walls with vertical light ribs and a night skyline beyond glass
    const sky = document.createElement('canvas'); sky.width = 2048; sky.height = 512;
    const k = sky.getContext('2d'); const grd = k.createLinearGradient(0, 0, 0, 512); grd.addColorStop(0, '#04070c'); grd.addColorStop(0.65, '#0c1a2b'); grd.addColorStop(1, '#122a40'); k.fillStyle = grd; k.fillRect(0, 0, 2048, 512);
    let x = 0, seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    while (x < 2048) { const w = 30 + rnd() * 90, h = 120 + rnd() * 300; k.fillStyle = 'rgba(8,16,26,.95)'; k.fillRect(x, 512 - h, w, h);
      k.fillStyle = 'rgba(160,200,235,.16)'; for (let yy = 512 - h + 10; yy < 500; yy += 14) for (let xx = x + 5; xx < x + w - 6; xx += 10) if (rnd() > .72) k.fillRect(xx, yy, 4, 6); x += w + 4; }
    const skyT = new THREE.CanvasTexture(sky); skyT.colorSpace = THREE.SRGBColorSpace;
    const backdrop = new THREE.Mesh(new THREE.CylinderGeometry(30, 30, 16, 64, 1, true, Math.PI * 0.25, Math.PI * 1.5), new THREE.MeshBasicMaterial({map: skyT, side: THREE.BackSide, fog: false}));
    backdrop.position.set(0, 6, 2); room.add(backdrop);
    const ribMat = new THREE.MeshBasicMaterial({color: '#3f7fae'});
    for (const sx of [-1, 1]) for (let i = 0; i < 6; i++) {
      const rib = new THREE.Mesh(new THREE.BoxGeometry(0.06, 6.5, 0.06), ribMat); rib.position.set(sx * (14 + i * 0.4), 3.25, -8 + i * 4.2); room.add(rib);
      const col = new THREE.Mesh(new THREE.BoxGeometry(0.5, 7.5, 0.5), new THREE.MeshStandardMaterial({color: '#0d1822', metalness: .1, roughness: .85})); col.position.set(sx * (14.3 + i * 0.4), 3.75, -8 + i * 4.2); col.castShadow = !phone; room.add(col);
    }
  }

  /* the three wall screens: frames in 3D; their content is DOM projected
   * onto them by floor.js (crisp text, the equity component mounts there) */
  const WALL = [
    {id: 'feed', x: -8.9, y: 3.7, w: 5.0, h: 3.3},
    {id: 'equity', x: 0, y: 4.0, w: 11.6, h: 3.9},
    {id: 'health', x: 8.9, y: 3.7, w: 5.0, h: 3.3}
  ];
  const WALL_Z = -8.56;
  for (const w of WALL) {
    const frame = new THREE.Mesh(new THREE.BoxGeometry(w.w + 0.16, w.h + 0.16, 0.08), new THREE.MeshStandardMaterial({color: '#111c28', metalness: .7, roughness: .3}));
    frame.position.set(w.x, w.y, WALL_Z); room.add(frame);
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(w.w, w.h), new THREE.MeshBasicMaterial({color: '#07111b'}));
    glass.position.set(w.x, w.y, WALL_Z + 0.05); room.add(glass);
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(w.w + 0.5, 0.03), new THREE.MeshBasicMaterial({color: '#6ec3f2', transparent: true, opacity: .7}));
    glow.position.set(w.x, w.y - w.h / 2 - 0.16, WALL_Z + 0.06); room.add(glow);
  }

  /* ── desks ─────────────────────────────────────────────────────── */
  const seats = o.seats;
  // a narrow portrait screen gets a tighter arc of slightly smaller desks
  const portrait = host.clientWidth / Math.max(1, host.clientHeight) < 0.9;
  const LR = portrait ? 6.0 : ARC_R, LSPAN = portrait ? 120 * Math.PI / 180 : ARC_SPAN, LS = portrait ? 0.85 : 1;
  const desks = {};
  const pickables = [];
  const N = seats.length;
  const center = new THREE.Vector3(0, 0, ARC_Z);
  const deskMetal = new THREE.MeshStandardMaterial({color: '#1a2633', metalness: .75, roughness: .3});
  const deskTop = new THREE.MeshStandardMaterial({color: '#162230', metalness: .3, roughness: .22});
  const screenBack = new THREE.MeshStandardMaterial({color: '#0c131b', metalness: .6, roughness: .4});

  function makeCanvasPlane(w, h, pxW, pxH) {
    const c = document.createElement('canvas'); c.width = pxW; c.height = pxH;
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({map: t, toneMapped: false}));
    return {canvas: c, ctx: c.getContext('2d'), tex: t, mesh};
  }

  seats.forEach((seat, i) => {
    const th = -LSPAN / 2 + LSPAN * (i / (N - 1));
    const pos = new THREE.Vector3(Math.sin(th) * LR, 0, ARC_Z - Math.cos(th) * LR);
    const g = new THREE.Group(); g.position.copy(pos); g.lookAt(center.x, 0, center.z); g.scale.setScalar(LS); room.add(g); g.updateMatrixWorld(true);
    // +Z of the group points at the centre: the agent stands at z=-0.55 facing +z
    const accent = new THREE.Color(seat.accent);
    const top = new THREE.Mesh(new THREE.BoxGeometry(2.1, 0.06, 0.86), deskTop); top.position.set(0, DESK_H, 0.15); top.castShadow = top.receiveShadow = !phone; g.add(top);
    const edgeMat = new THREE.MeshBasicMaterial({color: accent.clone()});
    const edge = new THREE.Mesh(new THREE.BoxGeometry(2.1, 0.012, 0.012), edgeMat); edge.position.set(0, DESK_H - 0.02, 0.585); g.add(edge);
    for (const sx of [-0.95, 0.95]) { const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, DESK_H, 0.7), deskMetal); leg.position.set(sx, DESK_H / 2, 0.15); leg.castShadow = !phone; g.add(leg); }
    const modesty = new THREE.Mesh(new THREE.BoxGeometry(1.84, 0.5, 0.025), deskMetal); modesty.position.set(0, 0.62, 0.55); g.add(modesty);
    // monitors facing the agent (screens toward -z), backs toward the centre
    const monitors = [];
    const monSpecs = [[-0.6, 0.42], [0, 0], [0.6, -0.42]];
    for (const [mx, rot] of monSpecs) {
      const arm = new THREE.Group(); arm.position.set(mx, DESK_H + 0.02, 0.32); arm.rotation.y = Math.PI + rot; g.add(arm);
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.18, 8), deskMetal); stem.position.y = 0.09; arm.add(stem);
      const back = new THREE.Mesh(new THREE.BoxGeometry(0.58, 0.34, 0.02), screenBack); back.position.y = 0.33; back.castShadow = !phone; arm.add(back);
      const scr = makeCanvasPlane(0.55, 0.31, 512, 288); scr.mesh.position.set(0, 0.33, 0.0115); arm.add(scr.mesh);
      monitors.push(scr);
    }
    if (!phone) { const spot = new THREE.SpotLight('#fff4e6', 22, 7, 0.5, 0.6, 1.6); spot.position.set(0, 4.6, 1.4); spot.target.position.set(0, 1.3, -0.5); g.add(spot); g.add(spot.target); }
    const backGlow = new THREE.PointLight(accent.clone(), 0.5, 3.2, 2); backGlow.position.set(0, DESK_H + 0.45, -0.1); g.add(backGlow);
    // the floating status board behind the agent, facing the centre
    const board = makeCanvasPlane(1.9, 0.86, 1024, 464);
    board.mesh.position.set(0, 2.62, -1.15); g.add(board.mesh);
    const boardFrameMat = new THREE.MeshBasicMaterial({color: accent.clone(), transparent: true, opacity: 0.9});
    const bf = new THREE.Mesh(new THREE.PlaneGeometry(1.94, 0.012), boardFrameMat); bf.position.set(0, 2.62 - 0.445, -1.151); g.add(bf);
    const bf2 = bf.clone(); bf2.position.y = 2.62 + 0.445; g.add(bf2);
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 2.2, 8), deskMetal); pole.position.set(0, 1.1, -1.2); g.add(pole);
    // nameplate on the desk front (facing the centre)
    const plate = makeCanvasPlane(1.1, 0.2, 512, 96);
    plate.mesh.position.set(0, DESK_H - 0.16, 0.567); g.add(plate.mesh);
    // the state ring on the floor
    const ringMat = new THREE.MeshBasicMaterial({color: '#4a5566', transparent: true, opacity: 0.7, depthWrite: false});
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.45, 1.52, 96), ringMat); ring.rotation.x = -Math.PI / 2; ring.position.set(0, 0.006, -0.1); g.add(ring);
    const halo = new THREE.Mesh(new THREE.CircleGeometry(1.45, 64), new THREE.MeshBasicMaterial({color: '#4a5566', transparent: true, opacity: 0.08, depthWrite: false}));
    halo.rotation.x = -Math.PI / 2; halo.position.set(0, 0.005, -0.1); g.add(halo);
    // props
    if (seat.slug === 'karen') { const flag = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.2, 3), new THREE.MeshStandardMaterial({color: '#d4414c', emissive: '#5a0d12'})); flag.rotation.z = -Math.PI / 2; flag.position.set(0.86, DESK_H + 0.3, 0.05); g.add(flag); const fp = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.36, 6), deskMetal); fp.position.set(0.79, DESK_H + 0.2, 0.05); g.add(fp); }
    if (seat.slug === 'allocator') { const brass = new THREE.MeshStandardMaterial({color: '#c9a250', metalness: 1, roughness: .25}); const base = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.07, 0.03, 24), brass); base.position.set(-0.85, DESK_H + 0.045, 0.0); g.add(base); const beam = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.008, 0.008), brass); beam.position.set(-0.85, DESK_H + 0.24, 0); g.add(beam); const post = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.2, 8), brass); post.position.set(-0.85, DESK_H + 0.14, 0); g.add(post); for (const px of [-0.12, 0.12]) { const pan = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.03, 0.01, 20), brass); pan.position.set(-0.85 + px, DESK_H + 0.17, 0); g.add(pan); } }
    if (seat.slug === 'scout') { const globe = new THREE.Mesh(new THREE.SphereGeometry(0.1, 24, 16), new THREE.MeshStandardMaterial({color: '#2b5a6e', metalness: .2, roughness: .5, wireframe: true})); globe.position.set(0.85, DESK_H + 0.16, 0.02); g.add(globe); const gb = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.06, 0.04, 16), deskMetal); gb.position.set(0.85, DESK_H + 0.05, 0.02); g.add(gb); }
    // the hit volume (desk + agent + board)
    const hit = new THREE.Mesh(new THREE.BoxGeometry(2.3, 3.2, 2.2), new THREE.MeshBasicMaterial({visible: false}));
    hit.position.set(0, 1.6, -0.35); hit.userData.slug = seat.slug; g.add(hit); pickables.push(hit);
    const world = (x, y, z) => g.localToWorld(new THREE.Vector3(x, y, z));
    desks[seat.slug] = {seat, group: g, theta: th, monitors, board, plate, ring, ringMat, halo, edgeMat, boardFrameMat, backGlow,
      stand: world(0, 0, -0.55), visit: world(0.25, 0, 1.05), anchor: world(0, 2.3, -0.4), focusPos: world(0.2, 2.45, 3.95), focusTarget: world(0, 1.7, -0.6),
      agent: null, avatar: null, data: null};
  });

  /* ── characters ────────────────────────────────────────────────── */
  const loaded = {};
  async function loadModel(name) {
    if (!loaded[name]) loaded[name] = (async () => {
      const [{GLTFLoader}, {MeshoptDecoder}] = await Promise.all([import('./team-demo/assets/GLTFLoader.js'), import('./team-demo/assets/meshopt_decoder.module.js')]);
      const loader = new GLTFLoader(); loader.setMeshoptDecoder(MeshoptDecoder);
      const gltf = await loader.loadAsync(MODELS + name + '.glb');
      return gltf.scene;
    })();
    return loaded[name];
  }
  const avatars = [];
  async function mountAvatar(d) {
    const cast = CAST[d.seat.slug]; if (!cast) return;
    const src = await loadModel(cast.model);
    const root = cloneSkinned(src);
    const mats = [];
    root.traverse((m) => {
      if (!m.isMesh) return;
      m.castShadow = !phone; m.receiveShadow = false; m.frustumCulled = false;
      const isBody = /body/i.test(m.material && m.material.name || '');
      m.material = tintMaterial(m.material, {tint: isBody ? cast.tint : null, amt: cast.amt, suit: isBody && !!cast.tint});
      mats.push(m.material);
    });
    armsDown(root);
    root.updateMatrixWorld(true);
    const bones = resolveBones(root).bones;
    const ctl = new AvatarController(bones, resolveBlendshapes(root).shapes, {mode: 'unavailable', seed: 17 + avatars.length * 13, visemes: resolveVisemes(root), joints: buildJoints(root, bones)});
    const legs = legJoints(root);
    if (cast.headset && bones.head) {
      const hs = new THREE.Group(); const mat = new THREE.MeshStandardMaterial({color: '#1b1f24', metalness: .6, roughness: .35});
      const band = new THREE.Mesh(new THREE.TorusGeometry(0.105, 0.008, 8, 32, Math.PI), mat); hs.add(band);
      for (const sx of [-1, 1]) { const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.025, 20), mat); cup.rotation.z = Math.PI / 2; cup.position.set(sx * 0.105, 0, 0); hs.add(cup); }
      const boom = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.12, 6), mat); boom.rotation.x = Math.PI / 2.4; boom.position.set(0.1, -0.055, 0.05); hs.add(boom);
      // place it in head space: measure the head's world frame once
      const hp = bones.head.getWorldPosition(new THREE.Vector3());
      const hq = bones.head.getWorldQuaternion(new THREE.Quaternion());
      hs.position.copy(bones.head.worldToLocal(hp.clone().add(new THREE.Vector3(0, 0.08, 0.0))));
      hs.quaternion.copy(hq.clone().invert());
      bones.head.add(hs);
    }
    root.position.copy(d.stand);
    root.rotation.y = d.group.rotation.y;
    root.scale.setScalar(LS);
    room.add(root);
    const av = {slug: d.seat.slug, root, ctl, legs, mats, pose: {type: 0, fore: 0.24, up: 0.04}, home: d.stand.clone(), homeYaw: d.group.rotation.y,
                walk: null, phase: Math.random() * 6, dim: 1, desat: 0, mode: 'unavailable'};
    d.avatar = av; avatars.push(av);
    applyLook(d);
    requestRender();
  }

  /* ── board / monitor drawing (REAL fields only) ────────────────── */
  const B = window.BTFloor;
  let payload = null, readMeta = {status: 'LOADING'};
  function kpi(a) { const m = (a && a.monitor || []).find((x) => x && x.value != null) || (a && a.monitor || [])[0]; return m || null; }
  function alerts(a) {
    const out = [];
    if (!a) return out;
    if (a.challenges && a.challenges.open_against) out.push(a.challenges.open_against + ' open challenge' + (a.challenges.open_against > 1 ? 's' : ''));
    if (a.state === 'STALE') out.push('Heartbeat stale');
    if (a.state === 'WAITING') out.push('Waiting');
    if (a.status_row && a.status_row.last_error && a.status_row.state === 'FAILED') out.push('Last run failed');
    return out;
  }
  function drawBoard(d) {
    const a = d.data, seat = d.seat, c = d.board.ctx, W = 1024, H = 464, now = Date.now() / 1000;
    const st = B.stateOf(a), meta = B.STATES[st];
    c.clearRect(0, 0, W, H);
    const bg = c.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, 'rgba(14,26,38,.96)'); bg.addColorStop(1, 'rgba(7,14,22,.96)');
    c.fillStyle = bg; roundRect(c, 0, 0, W, H, 22); c.fill();
    c.fillStyle = seat.accent; c.fillRect(0, 0, 10, H);
    c.fillStyle = '#e9f2fa'; c.font = '600 54px Inter, system-ui, sans-serif'; c.textBaseline = 'alphabetic';
    c.fillText(fitText(c, seat.name, 560), 44, 74);
    c.fillStyle = '#8fa7ba'; c.font = '500 25px Inter, system-ui, sans-serif'; c.fillText(fitText(c, seat.short.toUpperCase(), 560), 46, 112);
    // the state chip
    c.font = '700 26px Inter, system-ui, sans-serif';
    const label = (readMeta.stale ? 'STALE READ · ' : '') + meta.label.toUpperCase();
    const cw = c.measureText(label).width + 56;
    c.fillStyle = meta.color + '26'; roundRect(c, W - cw - 36, 36, cw, 52, 26); c.fill();
    c.strokeStyle = meta.color; c.lineWidth = 2; roundRect(c, W - cw - 36, 36, cw, 52, 26); c.stroke();
    c.fillStyle = meta.color; c.beginPath(); c.arc(W - cw - 12, 62, 7, 0, Math.PI * 2); c.fill();
    c.fillText(label, W - cw - 36 + 40, 72);
    // detail
    c.fillStyle = '#c8d6e2'; c.font = '400 28px Inter, system-ui, sans-serif';
    const detail = !a ? (readMeta.why || 'No floor read yet') : (a.state_detail || '');
    wrap(c, detail, 46, 168, W - 92, 36, 2);
    // KPI
    const k = kpi(a);
    c.fillStyle = 'rgba(255,255,255,.06)'; c.fillRect(46, 250, W - 92, 2);
    if (k) {
      c.fillStyle = '#8fa7ba'; c.font = '500 22px Inter, system-ui, sans-serif'; c.fillText(fitText(c, k.label.toUpperCase(), 520), 46, 296);
      c.fillStyle = k.value == null ? '#e9be74' : '#f1f7fc'; c.font = (k.value == null ? '600 34px' : '600 64px') + ' Inter, system-ui, sans-serif';
      c.fillText(fitText(c, k.value == null ? 'UNAVAILABLE' : String(k.value), 560), 46, k.value == null ? 348 : 366);
      c.fillStyle = '#6f8597'; c.font = '400 19px Inter, system-ui, sans-serif';
      c.fillText(fitText(c, 'source ' + k.source + (k.as_of ? ' · ' + B.ago(k.as_of, now) : '') + (k.value == null && k.why ? ' · ' + k.why : ''), 560), 46, 404);
    } else if (a && a.state === 'NOT_DEPLOYED') {
      c.fillStyle = '#e9be74'; c.font = '600 36px Inter, system-ui, sans-serif'; c.fillText('NOT YET DEPLOYED', 46, 340);
      c.fillStyle = '#6f8597'; c.font = '400 20px Inter, system-ui, sans-serif'; c.fillText(fitText(c, a.deploy_why || '', 560), 46, 380);
    }
    // heartbeat + alerts
    const hb = a && a.heartbeat;
    c.textAlign = 'right';
    c.fillStyle = '#8fa7ba'; c.font = '500 22px Inter, system-ui, sans-serif'; c.fillText('HEARTBEAT', W - 46, 296);
    c.fillStyle = !hb || hb.age_s == null ? '#e9be74' : (a.state === 'STALE' ? '#ff8395' : '#e9f2fa');
    c.font = '600 44px Inter, system-ui, sans-serif';
    c.fillText(!a ? '—' : (!hb || hb.at == null ? 'NONE' : B.age(now - hb.at).trim()), W - 46, 352);
    const al = alerts(a);
    c.font = '600 21px Inter, system-ui, sans-serif'; c.fillStyle = al.length ? '#ffb1bd' : '#6f8597';
    c.fillText(al.length ? '⚠ ' + al.slice(0, 2).join(' · ') : 'No alerts', W - 46, 404);
    c.textAlign = 'left';
    if (B.isFixture(payload)) { c.fillStyle = '#e9be74'; c.font = '700 18px Inter, system-ui, sans-serif'; c.fillText('FIXTURE DATA · NOT PRODUCTION', 46, H - 22); }
    d.board.tex.needsUpdate = true;
  }
  function drawMonitors(d) {
    const a = d.data, now = Date.now() / 1000, st = B.stateOf(a), meta = B.STATES[st];
    const off = !a || st === 'NOT_DEPLOYED' || st === 'UNKNOWN';
    d.monitors.forEach((m, i) => {
      const c = m.ctx, W = 512, H = 288;
      c.fillStyle = off ? '#05080c' : '#0a1724'; c.fillRect(0, 0, W, H);
      if (off) { c.fillStyle = '#3d4859'; c.font = '600 26px Inter, system-ui, sans-serif'; c.fillText(st === 'NOT_DEPLOYED' ? 'NOT YET DEPLOYED' : 'NO DATA', 28, 150); m.tex.needsUpdate = true; return; }
      c.fillStyle = meta.color; c.fillRect(0, 0, W, 6);
      c.fillStyle = '#d8e6f1'; c.font = '600 26px Inter, system-ui, sans-serif';
      if (i === 1) {
        c.fillText(fitText(c, meta.label.toUpperCase(), 460), 28, 50);
        c.fillStyle = '#9db3c4'; c.font = '400 21px Inter, system-ui, sans-serif'; wrap(c, a.state_detail || '', 28, 92, 456, 28, 4);
      } else {
        const mon = (a.monitor || [])[i === 0 ? 0 : 1] || (a.monitor || [])[0];
        if (mon) {
          c.fillStyle = '#8fa7ba'; c.font = '500 18px Inter, system-ui, sans-serif'; c.fillText(fitText(c, mon.label.toUpperCase(), 456), 28, 46);
          c.fillStyle = mon.value == null ? '#e9be74' : '#f1f7fc'; c.font = '600 44px Inter, system-ui, sans-serif'; c.fillText(fitText(c, mon.value == null ? 'UNAVAILABLE' : String(mon.value), 456), 28, 110);
          c.fillStyle = '#6f8597'; c.font = '400 16px Inter, system-ui, sans-serif'; c.fillText(fitText(c, mon.source + (mon.as_of ? ' · ' + B.ago(mon.as_of, now) : ''), 456), 28, 150);
        }
        if (a.last_output && i === 0) { c.fillStyle = '#9db3c4'; c.font = '400 17px Inter, system-ui, sans-serif'; wrap(c, 'Last: ' + a.last_output.summary, 28, 196, 456, 24, 3); }
      }
      m.tex.needsUpdate = true;
    });
  }
  function drawPlate(d) {
    const c = d.plate.ctx, W = 512, H = 96, seat = d.seat;
    c.clearRect(0, 0, W, H); c.fillStyle = 'rgba(8,14,20,.92)'; roundRect(c, 0, 0, W, H, 14); c.fill();
    c.fillStyle = seat.accent; c.fillRect(20, 26, 6, 44);
    c.fillStyle = '#eef5fb'; c.font = '600 34px Inter, system-ui, sans-serif'; c.fillText(fitText(c, seat.name, 300), 42, 58);
    c.fillStyle = '#8fa7ba'; c.font = '500 18px Inter, system-ui, sans-serif'; c.textAlign = 'right'; c.fillText(fitText(c, seat.short.toUpperCase(), 170), W - 20, 58); c.textAlign = 'left';
    d.plate.tex.needsUpdate = true;
  }

  function applyLook(d) {
    const a = d.data, st = B.stateOf(a), meta = B.STATES[st];
    const color = new THREE.Color(meta.color);
    d.ringMat.color.copy(color); d.halo.material.color.copy(color);
    d.ringMat.opacity = st === 'NOT_DEPLOYED' || st === 'UNKNOWN' ? 0.25 : st === 'STALE' ? 0.35 : 0.8;
    d.halo.material.opacity = meta.motion === 'work' ? 0.12 : meta.motion === 'review' ? 0.1 : 0.05;
    const lit = !(st === 'STALE' || st === 'NOT_DEPLOYED' || st === 'UNKNOWN');
    d.edgeMat.color.copy(new THREE.Color(d.seat.accent)).multiplyScalar(lit ? 1 : 0.25);
    d.boardFrameMat.color.copy(color);
    d.backGlow.intensity = lit ? 0.6 : 0.08;
    d.board.mesh.material.color.setScalar(lit ? 1 : 0.55);
    const av = d.avatar;
    if (av) {
      const mode = st === 'STALE' || st === 'NOT_DEPLOYED' || st === 'UNKNOWN' ? 'unavailable'
        : st === 'WAITING' ? 'waiting' : meta.motion === 'review' ? 'reviewing' : meta.motion === 'work' ? 'speaking' : 'monitoring';
      // 'speaking' would open the mouth only with speech audio; here it just
      // narrows the gaze -- no audio is attached on the floor
      av.mode = mode === 'speaking' ? 'reviewing' : mode;
      av.ctl.setMode(av.mode);
      const typing = a && (a.activity_basis === 'RUN_IN_PROGRESS' || a.activity_basis === 'SLACK_REQUEST');
      av.pose.target = typing ? 1 : (meta.motion === 'review' || meta.motion === 'work') ? 0.45 : 0;
      av.dim = st === 'NOT_DEPLOYED' ? 0.5 : st === 'STALE' ? 0.6 : st === 'UNKNOWN' ? 0.7 : 1;
      av.desat = st === 'NOT_DEPLOYED' ? 0.85 : st === 'STALE' ? 0.5 : 0;
      for (const m of av.mats) { m.userData.uniforms.uDim.value = av.dim; m.userData.uniforms.uDesat.value = av.desat; }
    }
  }

  /* ── light trails (one per real edge) ──────────────────────────── */
  const trailGroup = new THREE.Group(); scene.add(trailGroup);
  const trailMat = (color) => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {uTime: {value: 0}, uColor: {value: new THREE.Color(color)}, uAlpha: {value: 1}, uSpeed: {value: o.reducedMotion ? 0 : 1}},
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: 'uniform float uTime; uniform vec3 uColor; uniform float uAlpha; uniform float uSpeed; varying vec2 vUv;' +
      'void main(){ float along = vUv.x; float pulse = fract(along*3.0 - uTime*0.45*uSpeed); float head = smoothstep(0.0,0.08,pulse)*smoothstep(0.35,0.08,pulse);' +
      ' float edge = 1.0 - abs(vUv.y-0.5)*2.0; float base = 0.28 + 0.72*head*step(0.001,uSpeed) + (1.0-step(0.001,uSpeed))*0.35;' +
      ' float fade = smoothstep(0.0,0.06,along)*smoothstep(1.0,0.94,along); gl_FragColor = vec4(uColor, base*edge*fade*uAlpha); }'
  });
  let trails = [];
  function buildTrails(edges) {
    for (const t of trails) { trailGroup.remove(t.mesh); t.mesh.geometry.dispose(); t.mesh.material.dispose(); }
    trails = [];
    const now = Date.now() / 1000;
    for (const e of edges || []) {
      const fs = B.BY_AGENT[e.from], ts = B.BY_AGENT[e.to];
      if (!fs || !ts || !desks[fs.slug] || !desks[ts.slug]) continue;
      const a = desks[fs.slug].anchor.clone(), b = desks[ts.slug].anchor.clone();
      const mid = a.clone().add(b).multiplyScalar(0.5); mid.y += 1.2 + a.distanceTo(b) * 0.12;
      mid.lerp(new THREE.Vector3(0, mid.y, ARC_Z), 0.18);
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const geo = new THREE.TubeGeometry(curve, 64, 0.035 + Math.min(4, e.count || 1) * 0.008, 8, false);
      const mat = trailMat(fs.accent);
      const ageS = e.at ? now - e.at : (payload && payload.window_s) || 3600;
      mat.uniforms.uAlpha.value = clamp(1.15 - ageS / ((payload && payload.window_s) || 3600), 0.3, 1);
      const mesh = new THREE.Mesh(geo, mat); mesh.userData.edge = e; mesh.renderOrder = 5; trailGroup.add(mesh);
      trails.push({mesh, edge: e});
    }
  }

  /* ── walks (once per new edge, only for recent real edges) ─────── */
  const walked = new Set();
  const walkQueue = [];
  function considerWalks(edges) {
    if (o.reducedMotion) return;
    const now = Date.now() / 1000;
    for (const e of edges || []) {
      if (!e.at || now - e.at > WALK_WINDOW_S) continue;
      const id = e.from + '>' + e.to + '>' + e.kind + '>' + e.at;
      if (walked.has(id)) continue;
      walked.add(id);
      const fs = B.BY_AGENT[e.from], ts = B.BY_AGENT[e.to];
      if (!fs || !ts || !desks[fs.slug] || !desks[ts.slug]) continue;
      // a desk that is stale / not deployed / unknown does not get up
      const st = B.stateOf(desks[fs.slug].data);
      if (st === 'STALE' || st === 'NOT_DEPLOYED' || st === 'UNKNOWN') continue;
      walkQueue.push({from: fs.slug, to: ts.slug, edge: e});
    }
  }
  function startWalk(av, job) {
    const td = desks[job.to];
    const target = td.visit.clone();
    av.walk = {stage: 'out', from: av.root.position.clone(), to: target, t: 0, dur: av.root.position.distanceTo(target) / 1.15,
               hold: 7, faceTo: td.stand.clone(), edge: job.edge};
  }
  function stepWalk(av, dt) {
    const w = av.walk; if (!w) return false;
    if (w.stage === 'out' || w.stage === 'back') {
      w.t += dt / Math.max(0.5, w.dur);
      const u = clamp(w.t, 0, 1), e = ease(u);
      // a gentle curve through the open floor, not through desks
      const p = w.from.clone().lerp(w.to, e);
      const pull = Math.sin(u * Math.PI) * 1.4;
      const inward = new THREE.Vector3(0, 0, ARC_Z).sub(p).setY(0).normalize().multiplyScalar(pull);
      p.add(inward);
      const prev = av.root.position.clone();
      av.root.position.copy(p);
      const v = p.clone().sub(prev); v.y = 0;
      if (v.lengthSq() > 1e-7) av.root.rotation.y = damp(av.root.rotation.y, Math.atan2(v.x, v.z), 10, dt);
      av.phase += dt * 6.2;
      const s = Math.sin(av.phase), sw = 0.42 * Math.min(1, Math.sin(u * Math.PI) * 3);
      hinge(av.legs['Bip01 L Thigh'], s * sw); hinge(av.legs['Bip01 R Thigh'], -s * sw);
      hinge(av.legs['Bip01 L Calf'], -Math.max(0, -s) * sw * 1.3); hinge(av.legs['Bip01 R Calf'], -Math.max(0, s) * sw * 1.3);
      av.root.position.y = Math.abs(Math.cos(av.phase)) * 0.015 * Math.min(1, sw * 3);
      if (u >= 1) {
        for (const k of ['Bip01 L Thigh', 'Bip01 R Thigh', 'Bip01 L Calf', 'Bip01 R Calf']) hinge(av.legs[k], 0);
        av.root.position.y = 0;
        if (w.stage === 'out') { w.stage = 'hold'; w.t = 0; }
        else { av.walk = null; av.root.rotation.y = av.homeYaw; }
      }
      return true;
    }
    if (w.stage === 'hold') {
      const f = w.faceTo.clone().sub(av.root.position);
      av.root.rotation.y = damp(av.root.rotation.y, Math.atan2(f.x, f.z), 4, dt);
      w.t += dt;
      if (w.t >= w.hold) { w.stage = 'back'; w.t = 0; w.from = av.root.position.clone(); w.to = av.home.clone(); w.dur = w.from.distanceTo(w.to) / 1.15; }
      return true;
    }
    return false;
  }

  /* ── arms: typing / reviewing pose over the controller's motion ─── */
  function stepArms(av, dt, T) {
    const p = av.pose; const tgt = av.walk ? 0 : (p.target || 0);
    p.type = damp(p.type, tgt, 3, dt);
    if (p.type < 0.01 && !av.walk) return;
    const typing = p.type;
    for (const side of [1, -1]) {
      const up = side === 1 ? 'leftUpperArm' : 'rightUpperArm', lo = side === 1 ? 'leftLowerArm' : 'rightLowerArm';
      const tap = (tgt >= 1 && !o.reducedMotion) ? Math.sin(T * 9 + side * 1.7) * 0.035 + Math.sin(T * 13.3 + side) * 0.02 : 0;
      av.ctl._hinge(up, lerp(0.04, 0.42, typing));
      av.ctl._hinge(lo, lerp(0.24, 1.32, typing) + tap * typing);
    }
    if (av.walk) { const s = Math.sin(av.phase); av.ctl._hinge('leftUpperArm', -s * 0.22); av.ctl._hinge('rightUpperArm', s * 0.22); }
  }

  /* ── camera: overview orbit + focus transitions ────────────────── */
  const camera = new THREE.PerspectiveCamera(phone ? 52 : 40, 1, 0.1, 120);
  const view = phone ? {target: new THREE.Vector3(0, 0.6, -1.0), az: 0, pol: 0.72, r: 15.5} : {target: new THREE.Vector3(0, 2.1, -1.2), az: 0, pol: 1.2, r: 13.8};
  const base = {target: view.target.clone(), az: 0, pol: view.pol, r: view.r};
  let flight = null, focused = null, cameraDirty = true;
  function setCamFromView() {
    const sp = new THREE.Vector3(Math.sin(view.az) * Math.sin(view.pol), Math.cos(view.pol), Math.cos(view.az) * Math.sin(view.pol)).multiplyScalar(view.r);
    camera.position.copy(view.target).add(sp); camera.lookAt(view.target); cameraDirty = true;
  }
  setCamFromView();
  function flyTo(pos, target, dur) {
    const fromPos = camera.position.clone(), fromT = view.target.clone();
    if (o.reducedMotion) dur = 0.001;
    flight = {fromPos, fromT, toPos: pos.clone(), toT: target.clone(), t0: performance.now(), dur: dur || 1.4};
    requestRender();
  }
  let insetRight = 0;
  function applyInset() {
    const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
    if (insetRight > 0 && focused && !String(focused).startsWith('wall:')) camera.setViewOffset(w, h, insetRight / 2, 0, w, h); else camera.clearViewOffset();
    camera.updateProjectionMatrix(); cameraDirty = true;
  }
  function focus(slug) {
    const d = desks[slug]; if (!d) return;
    focused = slug; applyInset();
    flyTo(d.focusPos, d.focusTarget, 1.5);
  }
  function focusWall(id) {
    const w = WALL.find((x) => x.id === id); if (!w) return;
    focused = 'wall:' + id; applyInset();
    const dist = Math.max(w.w / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect), w.h / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)))) * 1.18;
    flyTo(new THREE.Vector3(w.x * 0.92, w.y + 0.1, WALL_Z + dist), new THREE.Vector3(w.x, w.y, WALL_Z), 1.4);
  }
  function resetView() {
    focused = null; applyInset();
    const sp = new THREE.Vector3(Math.sin(base.az) * Math.sin(base.pol), Math.cos(base.pol), Math.cos(base.az) * Math.sin(base.pol)).multiplyScalar(base.r);
    view.az = base.az; view.pol = base.pol; view.r = base.r;
    flyTo(base.target.clone().add(sp), base.target, 1.3);
  }

  /* pointer: drag to orbit (overview), wheel / pinch to zoom, tap to pick */
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let drag = null, pinch = null, hoverSlug = null;
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    return hit ? hit.object.userData.slug : null;
  }
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    drag = {x: e.clientX, y: e.clientY, moved: 0, id: e.pointerId, az: view.az, pol: view.pol};
  });
  canvas.addEventListener('pointermove', (e) => {
    if (drag && drag.id === e.pointerId && !pinch) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.moved = Math.max(drag.moved, Math.abs(dx) + Math.abs(dy));
      if (drag.moved > 6 && !focused && !flight) {
        view.az = clamp(drag.az - dx * 0.004, -0.75, 0.75);
        view.pol = clamp(drag.pol - dy * 0.003, 0.62, 1.32);
        setCamFromView(); requestRender();
      }
      return;
    }
    if (e.pointerType === 'mouse') {
      const s = pick(e.clientX, e.clientY);
      if (s !== hoverSlug) { hoverSlug = s; canvas.style.cursor = s ? 'pointer' : 'grab'; }
      if (o.onHover) o.onHover(s, e.clientX, e.clientY);
    }
  });
  canvas.addEventListener('pointerup', (e) => {
    if (drag && drag.id === e.pointerId && drag.moved <= 6) {
      const s = pick(e.clientX, e.clientY);
      if (o.onPick) o.onPick(s, {again: s && s === focused});
    }
    drag = null;
  });
  canvas.addEventListener('pointerleave', () => { if (o.onHover) o.onHover(null); });
  canvas.addEventListener('wheel', (e) => {
    if (focused) return;
    e.preventDefault();
    view.r = clamp(view.r * (1 + Math.sign(e.deltaY) * 0.08), 9, 30); setCamFromView(); requestRender();
  }, {passive: false});
  canvas.addEventListener('touchstart', (e) => { if (e.touches.length === 2) { pinch = {d: Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY), r: view.r}; } }, {passive: true});
  canvas.addEventListener('touchmove', (e) => {
    if (pinch && e.touches.length === 2 && !focused) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      view.r = clamp(pinch.r * pinch.d / Math.max(20, d), 9, 32); setCamFromView(); requestRender();
    }
  }, {passive: true});
  canvas.addEventListener('touchend', () => { pinch = null; }, {passive: true});

  /* ── sizing, render loop, quality ──────────────────────────────── */
  function resize() {
    const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
    renderer.setSize(w, h, false); camera.aspect = w / h;
    camera.fov = (w / h < 0.8 ? 62 : phone ? 52 : 40); applyInset();
    cameraDirty = true; requestRender();
  }
  const ro = window.ResizeObserver ? new ResizeObserver(resize) : null;
  if (ro) ro.observe(host); else window.addEventListener('resize', resize);
  resize();

  function requestRender() { needsOne = true; if (!raf && !paused && !document.hidden) raf = requestAnimationFrame(tick); }
  function animating() {
    if (flight) return true;
    if (o.reducedMotion) return walkQueue.length > 0 && false;
    return true;   // idle breathing / typing / trails while visible
  }
  function tick(now) {
    raf = 0;
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60; last = now; T += dt;
    if (flight) {
      flight.t = (now - flight.t0) / 1000 / flight.dur; const u = ease(clamp(flight.t, 0, 1));
      camera.position.lerpVectors(flight.fromPos, flight.toPos, u);
      view.target.lerpVectors(flight.fromT, flight.toT, u); camera.lookAt(view.target); cameraDirty = true;
      if (flight.t >= 1) flight = null;
    }
    // walks: one at a time per walker
    if (walkQueue.length) {
      for (let i = 0; i < walkQueue.length; i++) {
        const job = walkQueue[i], d = desks[job.from];
        if (d && d.avatar && !d.avatar.walk) { startWalk(d.avatar, job); walkQueue.splice(i, 1); i--; }
      }
    }
    for (const av of avatars) {
      const moving = stepWalk(av, dt);
      if (!o.reducedMotion || moving) av.ctl.update(dt); else if (needsOne) av.ctl.update(1 / 60);
      stepArms(av, dt, T);
    }
    for (const t of trails) t.mesh.material.uniforms.uTime.value = T;
    // the state rings breathe only while that desk is genuinely working
    for (const s in desks) {
      const d = desks[s], m = B.STATES[B.stateOf(d.data)].motion;
      if (!o.reducedMotion && (m === 'work' || m === 'review')) d.halo.material.opacity = 0.07 + 0.06 * (0.5 + 0.5 * Math.sin(T * (m === 'work' ? 2.4 : 1.4)));
    }
    renderer.render(scene, camera);
    if (o.onFrame) o.onFrame({camera, cameraDirty, focused});
    cameraDirty = false; needsOne = false;
    // adaptive quality
    ema = ema * 0.92 + dt * 1000 * 0.08;
    if (ema > 24 && !window.__floorFixedQuality) { slow += dt; if (slow > 2 && dpr > 0.8) { dpr = Math.max(0.8, dpr - 0.25); renderer.setPixelRatio(dpr); renderer.shadowMap.enabled = false; resize(); slow = 0; ema = 16; host.setAttribute('data-dpr', String(dpr)); } } else slow = 0;
    frames++; if (now - secT >= 1000) { fps.push(Math.round(frames * 1000 / (now - secT))); if (fps.length > 30) fps.shift(); host.setAttribute('data-fps', String(fps[fps.length - 1])); frames = 0; secT = now; }
    if (!paused && !document.hidden && (animating() || walkQueue.length || avatars.some((a) => a.walk))) raf = requestAnimationFrame(tick);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { last = 0; requestRender(); } });

  /* ── data in ───────────────────────────────────────────────────── */
  function setData(p, meta) {
    payload = p; readMeta = meta || {status: p ? 'OK' : 'NONE'};
    const by = {};
    for (const a of (p && p.agents) || []) by[a.slug] = a;
    for (const s in desks) {
      const d = desks[s];
      d.data = by[s] || null;
      drawBoard(d); drawMonitors(d); drawPlate(d); applyLook(d);
    }
    buildTrails(p && p.edges);
    considerWalks(p && p.edges);
    requestRender();
  }
  function tickClocks() { for (const s in desks) { drawBoard(desks[s]); drawMonitors(desks[s]); } requestRender(); }

  /* the wall screens' projected corners for floor.js's DOM overlay */
  const tmp = new THREE.Vector3();
  function wallQuads() {
    const W = canvas.clientWidth, H = canvas.clientHeight, out = {};
    for (const w of WALL) {
      const pts = [[-1, 1], [1, 1], [1, -1], [-1, -1]].map(([sx, sy]) => {
        tmp.set(w.x + sx * w.w / 2, w.y + sy * w.h / 2, WALL_Z + 0.06).project(camera);
        return {x: (tmp.x * 0.5 + 0.5) * W, y: (-tmp.y * 0.5 + 0.5) * H, behind: tmp.z > 1};
      });
      out[w.id] = pts;
    }
    return out;
  }
  function anchorOf(slug) {
    const d = desks[slug]; if (!d) return null;
    tmp.copy(d.anchor).setY(3.2).project(camera);
    return {x: (tmp.x * 0.5 + 0.5) * canvas.clientWidth, y: (-tmp.y * 0.5 + 0.5) * canvas.clientHeight, behind: tmp.z > 1};
  }

  // characters load after the room is drawn: the floor is usable at once
  for (const s in desks) { drawBoard(desks[s]); drawMonitors(desks[s]); drawPlate(desks[s]); }
  requestRender();
  const mounting = (async () => {
    let failed = 0;
    for (const s of Object.keys(desks)) {
      try { await mountAvatar(desks[s]); } catch (e) { failed++; if (o.onStatus) o.onStatus('character-failed', s, e); }
    }
    if (o.onStatus) o.onStatus('characters-ready', failed);
  })();

  return {
    setData, focus, focusWall, resetView, tickClocks, wallQuads, anchorOf, requestRender, mounting,
    get focused() { return focused; },
    setInsetRight(px) { insetRight = px || 0; applyInset(); requestRender(); },
    setPaused(v) { paused = !!v; if (!paused) requestRender(); },
    stats() { return {fps: fps.slice(), dpr, shadows: renderer.shadowMap.enabled, avatars: avatars.length, trails: trails.length, walking: avatars.filter((a) => a.walk).map((a) => a.slug)}; },
    dispose() { cancelAnimationFrame(raf); if (ro) ro.disconnect(); renderer.dispose(); }
  };
}
