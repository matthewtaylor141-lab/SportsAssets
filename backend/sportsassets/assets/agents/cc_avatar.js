/*
 * BETTOR COMMAND - THE LICENSED-CHARACTER PIPELINE (glTF 2.0 binary).
 *
 * Loads a licensed, rigged human (.glb) for Derek, Xavier or Audrey and
 * animates it from the agent's REAL status. Nothing here fetches data: the
 * page sends the mode through the `cc:mode` event (from agent_status), and
 * `cc:speak` while an Audrey chat reply renders.
 *
 * WHAT A MODEL MUST PROVIDE (see models/README.md and manifest.json):
 *   - glTF 2.0 binary (.glb); Meshopt compression is supported (the decoder
 *     is vendored). KTX2 textures are NOT: the Basis transcoder is not in
 *     this build, so textures must be PNG/JPEG/WebP inside the .glb.
 *   - one skinned humanoid skeleton. Bones are found by role through
 *     BONE_ROLES, which accepts Mixamo ("mixamorigHead"), VRM / humanoid
 *     ("Head", "J_Bip_C_Head", "head") and common exporter names;
 *     `bones` in the config overrides any role explicitly.
 *   - ARKit 52 blendshapes on the face mesh (eyeBlinkLeft, jawOpen, ...),
 *     matched by name (a "prefix.eyeBlinkLeft" namespace is accepted);
 *     `blendshapes` in the config overrides any name explicitly.
 *
 * THE CONTROLLER (AvatarController) is pure motion logic over resolved bones
 * and morph targets, so it runs under node in the tests:
 *   blinks at random 2-6 s intervals, ~150 ms each; breathing as a few
 *   milliradians on the spine and chest bones (never whole-body bobbing);
 *   saccades between look targets; slow head turns clamped to +/-12 deg;
 *   restrained posture shifts; speech blendshapes while `speaking`; and an
 *   UNAVAILABLE pose that settles and then stays still.
 */
import * as THREE from './three.module.min.js';

export const VERSION = 'CC_AVATAR_PIPELINE_V1';
export const HEAD_TURN_LIMIT_RAD = 12 * Math.PI / 180;
export const BLINK_MIN_S = 2, BLINK_MAX_S = 6, BLINK_S = 0.15;

// ── skeleton roles: Mixamo, VRM / humanoid, 3ds Max Biped (Rocketbox) ──
export const BONE_ROLES = {
  hips: ['Bip01 Pelvis', 'mixamorigHips', 'Hips', 'hips', 'J_Bip_C_Hips', 'pelvis', 'Pelvis'],
  spine: ['Bip01 Spine', 'mixamorigSpine', 'Spine', 'spine', 'J_Bip_C_Spine', 'spine_01'],
  chest: ['Bip01 Spine2', 'Bip01 Spine1', 'mixamorigSpine2', 'mixamorigSpine1', 'UpperChest', 'Chest', 'upperChest', 'chest', 'J_Bip_C_UpperChest', 'J_Bip_C_Chest', 'spine_03', 'spine_02', 'Spine2', 'Spine1'],
  neck: ['Bip01 Neck', 'mixamorigNeck', 'Neck', 'neck', 'J_Bip_C_Neck', 'neck_01'],
  head: ['Bip01 Head', 'mixamorigHead', 'Head', 'head', 'J_Bip_C_Head'],
  leftEye: ['Bip01 LEye', 'mixamorigLeftEye', 'LeftEye', 'leftEye', 'J_Adj_L_FaceEye', 'eye_L', 'Eye_L'],
  rightEye: ['Bip01 REye', 'mixamorigRightEye', 'RightEye', 'rightEye', 'J_Adj_R_FaceEye', 'eye_R', 'Eye_R'],
  leftShoulder: ['Bip01 L Clavicle', 'mixamorigLeftShoulder', 'LeftShoulder', 'leftShoulder', 'J_Bip_L_Shoulder', 'clavicle_l'],
  rightShoulder: ['Bip01 R Clavicle', 'mixamorigRightShoulder', 'RightShoulder', 'rightShoulder', 'J_Bip_R_Shoulder', 'clavicle_r'],
  leftUpperArm: ['Bip01 L UpperArm', 'mixamorigLeftArm', 'LeftUpperArm', 'LeftArm', 'leftUpperArm', 'J_Bip_L_UpperArm', 'upperarm_l'],
  rightUpperArm: ['Bip01 R UpperArm', 'mixamorigRightArm', 'RightUpperArm', 'RightArm', 'rightUpperArm', 'J_Bip_R_UpperArm', 'upperarm_r'],
  leftLowerArm: ['Bip01 L Forearm', 'mixamorigLeftForeArm', 'LeftLowerArm', 'LeftForeArm', 'leftLowerArm', 'J_Bip_L_LowerArm', 'lowerarm_l'],
  rightLowerArm: ['Bip01 R Forearm', 'mixamorigRightForeArm', 'RightLowerArm', 'RightForeArm', 'rightLowerArm', 'J_Bip_R_LowerArm', 'lowerarm_r'],
};
export const REQUIRED_ROLES = ['hips', 'spine', 'chest', 'neck', 'head'];

// ── the 52 ARKit face blendshapes ─────────────────────────────────────
export const ARKIT_52 = [
  'browDownLeft', 'browDownRight', 'browInnerUp', 'browOuterUpLeft', 'browOuterUpRight',
  'cheekPuff', 'cheekSquintLeft', 'cheekSquintRight',
  'eyeBlinkLeft', 'eyeBlinkRight', 'eyeLookDownLeft', 'eyeLookDownRight', 'eyeLookInLeft', 'eyeLookInRight',
  'eyeLookOutLeft', 'eyeLookOutRight', 'eyeLookUpLeft', 'eyeLookUpRight', 'eyeSquintLeft', 'eyeSquintRight',
  'eyeWideLeft', 'eyeWideRight',
  'jawForward', 'jawLeft', 'jawOpen', 'jawRight',
  'mouthClose', 'mouthDimpleLeft', 'mouthDimpleRight', 'mouthFrownLeft', 'mouthFrownRight', 'mouthFunnel',
  'mouthLeft', 'mouthLowerDownLeft', 'mouthLowerDownRight', 'mouthPressLeft', 'mouthPressRight', 'mouthPucker',
  'mouthRight', 'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper', 'mouthSmileLeft',
  'mouthSmileRight', 'mouthStretchLeft', 'mouthStretchRight', 'mouthUpperUpLeft', 'mouthUpperUpRight',
  'noseSneerLeft', 'noseSneerRight', 'tongueOut'];

// the Oculus/Meta 15-viseme set (Rocketbox "AA_VI_10_aa", VRM "aa", ...)
export const VISEMES = ['sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS', 'nn', 'RR', 'aa', 'E', 'I', 'O', 'U'];

/** Viseme morph targets by viseme id. */
export function resolveVisemes(root) {
  const found = {};
  root.traverse((o) => {
    const dict = o.morphTargetDictionary;
    if (!dict || !o.morphTargetInfluences) return;
    for (const k of Object.keys(dict)) {
      const m = /(?:^|[._])(?:VI_\d\d_|viseme_?)(sil|pp|ff|th|dd|kk|ch|ss|nn|rr|aa|e|i|o|u)$/i.exec(k.split('.').pop());
      if (m) { const id = VISEMES.find((v) => v.toLowerCase() === m[1].toLowerCase()); (found[id] = found[id] || []).push({mesh: o, index: dict[k]}); }
    }
  });
  return found;
}

const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
// a vendor's ordering code before the ARKit name ("AK_09_EyeBlinkLeft") is not part of the name
const bare = (s) => norm(String(s || '').split('.').pop()).replace(/^[a-z]{2}\d{2}/, '');

/** Bones by role. `overrides` maps role -> exact bone name. */
export function resolveBones(root, overrides = {}) {
  const byName = new Map();
  root.traverse((o) => { if (o.isBone || o.type === 'Bone' || o.isObject3D) { const k = norm(o.name); if (k && !byName.has(k)) byName.set(k, o); } });
  const bones = {}, source = {};
  for (const [role, names] of Object.entries(BONE_ROLES)) {
    const want = overrides[role] ? [overrides[role]] : names;
    for (const n of want) { const o = byName.get(norm(n)); if (o) { bones[role] = o; source[role] = o.name; break; } }
  }
  return {bones, source, missing: REQUIRED_ROLES.filter((r) => !bones[r])};
}

/** Morph targets by ARKit name. `overrides` maps ARKit name -> target name. */
export function resolveBlendshapes(root, overrides = {}) {
  const found = {};
  root.traverse((o) => {
    const dict = o.morphTargetDictionary;
    if (!dict || !o.morphTargetInfluences) return;
    const keys = Object.keys(dict);
    for (const a of ARKIT_52) {
      const want = overrides[a] !== undefined ? [String(overrides[a])] : [a];
      for (const w of want) {
        const k = keys.find((x) => x === w || norm(x) === norm(w) || norm(x.split('.').pop()) === norm(w) || (overrides[a] === undefined && bare(x) === norm(w)));
        if (k !== undefined) (found[a] = found[a] || []).push({mesh: o, index: dict[k]});
      }
    }
  });
  return {shapes: found, names: Object.keys(found), missing: ARKIT_52.filter((a) => !found[a])};
}

function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const damp = (a, b, tau, dt) => a + (b - a) * (1 - Math.exp(-dt / Math.max(1e-4, tau)));

// ════════════════════════════════════════════════════════════════════
// THE CONTROLLER (pure: bones need only .rotation {x,y,z}; meshes only
// morphTargetInfluences)
// ════════════════════════════════════════════════════════════════════
export class AvatarController {
  constructor(bones, shapes, opts = {}) {
    this.bones = bones; this.shapes = shapes || {};
    this.rand = rng(opts.seed || 7);
    this.rest = {};
    for (const [role, b] of Object.entries(bones)) this.rest[role] = {x: b.rotation.x, y: b.rotation.y, z: b.rotation.z};
    this.mode = opts.mode || 'unavailable';
    this.t = 0; this.speaking = false;
    this.nextBlink = this._blinkGap(); this.blinkLeft = 0;
    this.look = {yaw: 0, pitch: 0}; this.lookAt = 0;
    this.head = {yaw: 0, pitch: 0}; this.eye = {yaw: 0, pitch: 0};
    this.posture = 0; this.postureTarget = 0; this.nextPosture = 4 + 4 * this.rand();
    this.still = false;
    this.stats = {blinks: 0, saccades: 0, maxHeadYaw: 0, maxBreath: 0, speechFrames: 0, postureShifts: 0};
    this.visemes = opts.visemes || {};
    this.speech = null;             // {amplitude 0..1} or {viseme, weight}, from audio only
  }
  /** SPEECH COMES FROM AUDIO, NEVER FROM RENDERED TEXT. The page's audio
   *  hook (attachAudio, or a 'cc:speech' event carrying amplitude or a
   *  viseme) sets this each frame; with none, the mouth stays closed. */
  setSpeech(s) { this.speech = s && (typeof s.amplitude === 'number' || s.viseme) ? s : null; if (this.speech) this.still = false; }
  _blinkGap() { return BLINK_MIN_S + (BLINK_MAX_S - BLINK_MIN_S) * this.rand(); }
  setMode(m) { if (m && m !== this.mode) { this.mode = m; this.still = false; this.lookAt = 0; } }
  setSpeaking(on) { this.speaking = !!on; this.still = false; }
  _shape(name, v) { for (const s of this.shapes[name] || []) s.mesh.morphTargetInfluences[s.index] = v; }
  _rot(role, dx, dy, dz) { const b = this.bones[role], r = this.rest[role]; if (!b || !r) return; b.rotation.x = r.x + (dx || 0); b.rotation.y = r.y + (dy || 0); b.rotation.z = r.z + (dz || 0); }
  update(dt) {
    if (this.still) return false;
    // wall-clock timing up to a quarter second per frame, so a slow device
    // still blinks and breathes on schedule (longer gaps are a stall)
    dt = clamp(dt, 0, 0.25);
    const T = (this.t += dt), off = this.mode === 'unavailable';
    // look targets (saccades) and slow head turns, clamped to +/-12 deg
    if (!off && T >= this.lookAt) {
      const wide = this.mode === 'monitoring' ? 1 : this.mode === 'reviewing' ? 0.5 : 0.35;
      this.look.yaw = (this.rand() * 2 - 1) * 0.3 * wide;
      this.look.pitch = this.mode === 'reviewing' ? -0.12 - 0.08 * this.rand() : (this.rand() * 2 - 1) * 0.06;
      if (this.mode === 'speaking' || this.speaking) { this.look.yaw *= 0.3; this.look.pitch = 0.02; }
      this.lookAt = T + 0.8 + 2.4 * this.rand();
      this.stats.saccades++;
    }
    if (off) { this.look.yaw = 0; this.look.pitch = -0.08; }
    this.eye.yaw = damp(this.eye.yaw, this.look.yaw, 0.05, dt);
    this.eye.pitch = damp(this.eye.pitch, this.look.pitch, 0.05, dt);
    this.head.yaw = clamp(damp(this.head.yaw, this.look.yaw * 0.6, 0.9, dt), -HEAD_TURN_LIMIT_RAD, HEAD_TURN_LIMIT_RAD);
    this.head.pitch = clamp(damp(this.head.pitch, this.look.pitch * 0.6, 0.9, dt), -HEAD_TURN_LIMIT_RAD, HEAD_TURN_LIMIT_RAD);
    this.stats.maxHeadYaw = Math.max(this.stats.maxHeadYaw, Math.abs(this.head.yaw));
    this._rot('neck', this.head.pitch * 0.4, this.head.yaw * 0.4, 0);
    this._rot('head', this.head.pitch * 0.6, this.head.yaw * 0.6, 0);
    const ey = this.eye.yaw - this.head.yaw, ep = this.eye.pitch - this.head.pitch;
    this._rot('leftEye', -ep, ey, 0); this._rot('rightEye', -ep, ey, 0);
    this._shape('eyeLookOutLeft', clamp(ey * 3, 0, 1)); this._shape('eyeLookInRight', clamp(ey * 3, 0, 1));
    this._shape('eyeLookInLeft', clamp(-ey * 3, 0, 1)); this._shape('eyeLookOutRight', clamp(-ey * 3, 0, 1));
    this._shape('eyeLookDownLeft', clamp(-ep * 3, 0, 1)); this._shape('eyeLookDownRight', clamp(-ep * 3, 0, 1));
    this._shape('eyeLookUpLeft', clamp(ep * 3, 0, 1)); this._shape('eyeLookUpRight', clamp(ep * 3, 0, 1));
    // blinks: random 2-6 s, ~150 ms (an offline agent keeps its eyes lowered)
    let blink = 0;
    if (!off) {
      if (T >= this.nextBlink && this.blinkLeft <= 0) { this.blinkLeft = BLINK_S; this.stats.blinks++; }
      if (this.blinkLeft > 0) { this.blinkLeft -= dt; blink = Math.sin(clamp(1 - this.blinkLeft / BLINK_S, 0, 1) * Math.PI); if (this.blinkLeft <= 0) this.nextBlink = T + this._blinkGap(); }
    } else blink = 0.45;
    this._shape('eyeBlinkLeft', blink); this._shape('eyeBlinkRight', blink);
    // breathing: a few milliradians on spine and chest only
    const breath = off ? 0 : Math.sin(T * 2 * Math.PI * 0.24) * 0.006;
    this.stats.maxBreath = Math.max(this.stats.maxBreath, Math.abs(breath));
    // restrained posture shifts (<= 1.5 deg)
    if (!off && T >= this.nextPosture) { this.postureTarget = (this.rand() * 2 - 1) * 0.026; this.nextPosture = T + 5 + 6 * this.rand(); this.stats.postureShifts++; }
    if (off) this.postureTarget = 0;
    this.posture = damp(this.posture, this.postureTarget, 1.2, dt);
    this._rot('spine', breath * 0.5, 0, this.posture * 0.5);
    this._rot('chest', breath, 0, -this.posture * 0.3);
    this._rot('hips', 0, 0, this.posture * 0.4);
    // speech: the mouth follows REAL audio (amplitude or visemes) only
    const sp = off ? null : this.speech;
    const amp = sp && typeof sp.amplitude === 'number' ? clamp(sp.amplitude, 0, 1) : 0;
    for (const id of Object.keys(this.visemes)) for (const v of this.visemes[id]) v.mesh.morphTargetInfluences[v.index] = sp && sp.viseme === id ? clamp(sp.weight ?? 1, 0, 1) : 0;
    const talk = !!sp;
    if (talk) this.stats.speechFrames++;
    this._shape('jawOpen', amp * 0.6); this._shape('mouthFunnel', 0);
    this._shape('mouthClose', talk ? 0 : 0.05);
    this._shape('mouthSmileLeft', off ? 0 : 0.12); this._shape('mouthSmileRight', off ? 0 : 0.12);
    this._shape('browInnerUp', this.mode === 'reviewing' ? 0.25 : (this.mode === 'speaking' || this.speaking) ? 0.12 : 0);
    // an offline agent settles, then stays still
    if (off && Math.abs(this.head.yaw) < 1e-3 && Math.abs(this.head.pitch - this.look.pitch * 0.6) < 1e-3 && Math.abs(this.posture) < 1e-3) this.still = true;
    return !this.still;
  }
}

// ════════════════════════════════════════════════════════════════════
// LIGHTING PRESETS AND FRAMING
// ════════════════════════════════════════════════════════════════════
export const LIGHTING = {
  cinematic_warm: {key: [0xffe4cc, 2.4, [-1.6, 2.4, 2.2]], fill: [0xbfd0ff, 0.7, [1.8, 1.6, 1.8]], rim: [0xffffff, 1.6, [1.2, 2.2, -2.0]], env: 0.6, exposure: 1.0},
  cinematic_cool: {key: [0xeaf0ff, 2.2, [-1.5, 2.5, 2.2]], fill: [0xffe0cc, 0.5, [1.8, 1.4, 1.8]], rim: [0x9fe8f0, 1.8, [1.3, 2.2, -2.0]], env: 0.55, exposure: 1.0},
  cinematic_violet: {key: [0xffe8dc, 2.3, [-1.4, 2.3, 2.3]], fill: [0xd8ccff, 0.8, [1.7, 1.5, 1.9]], rim: [0xc8b4ff, 1.9, [1.2, 2.3, -2.0]], env: 0.6, exposure: 1.05},
};
export const FRAMING = {face: 0.34, chest: 0.72, waist: 1.05};

/** A soft studio environment built in-scene (no HDR file). */
function studioEnvironment(renderer) {
  const s = new THREE.Scene();
  s.background = new THREE.Color(0x0a0c10);
  const panel = (c, i, p, w, h) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({color: new THREE.Color(c).multiplyScalar(i), side: THREE.DoubleSide})); m.position.set(p[0], p[1], p[2]); m.lookAt(0, 1.5, 0); s.add(m); };
  panel(0xfff1e6, 3, [-2.5, 3, 2.5], 2.5, 2.5); panel(0xdfe8ff, 1.2, [2.5, 2, 2], 2, 2); panel(0xffffff, 1.5, [0, 3, -3], 3, 1.2); panel(0x303540, 1, [0, -1, 0], 8, 8);
  const pm = new THREE.PMREMGenerator(renderer);
  const t = pm.fromScene(s, 0.04).texture; pm.dispose();
  return t;
}

/** A bind pose (T or A) brought to a natural standing pose: each upper arm
 *  is turned so the arm hangs down beside the body. Done once, before the
 *  controller records its rest pose. */
export function armsDown(root, overrides = {}) {
  const {bones} = resolveBones(root, overrides);
  root.updateMatrixWorld(true);
  const turned = [];
  for (const [up, lo, side] of [['leftUpperArm', 'leftLowerArm', 1], ['rightUpperArm', 'rightLowerArm', -1]]) {
    const a = bones[up], b = bones[lo];
    if (!a || !b || !a.parent) continue;
    const pa = a.getWorldPosition(new THREE.Vector3()), pb = b.getWorldPosition(new THREE.Vector3());
    const d = pb.clone().sub(pa).normalize();
    if (d.y < -0.8) continue;                                   // already hanging
    const sideSign = Math.sign(d.x) || side;
    const want = new THREE.Vector3(0.16 * sideSign, -1, 0.04).normalize();
    const qw = new THREE.Quaternion().setFromUnitVectors(d, want);
    const aw = a.getWorldQuaternion(new THREE.Quaternion());
    const pw = a.parent.getWorldQuaternion(new THREE.Quaternion());
    a.quaternion.copy(pw.clone().invert().multiply(qw).multiply(aw));
    a.updateMatrixWorld(true);
    turned.push(up);
  }
  return turned;
}

function skinTune(root) {
  // PHYSICALLY BASED SKIN, no plastic sheen: roughness about 0.5 on skin
  // materials (by name), no clearcoat or sheen
  root.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m || !/skin|body|face|head/i.test(m.name || '')) continue;
      // a roughness map carries the skin's own variation; only an untextured
      // material is clamped into the skin range
      if ('roughness' in m && !m.roughnessMap) m.roughness = clamp(m.roughness, 0.45, 0.6);
      if ('clearcoat' in m) m.clearcoat = 0;
      if ('sheen' in m) m.sheen = 0;
      if ('metalness' in m) m.metalness = 0;
    }
  });
}

// ════════════════════════════════════════════════════════════════════
// MOUNT
// ════════════════════════════════════════════════════════════════════
export async function mountAvatar(stage, cfg, opts = {}) {
  const [{GLTFLoader}, {MeshoptDecoder}] = await Promise.all([import('./GLTFLoader.js'), import('./meshopt_decoder.module.js')]);
  const reduced = !!opts.reducedMotion;
  const canvas = stage.querySelector('canvas');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, powerPreference: 'high-performance'});
  let dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  renderer.setPixelRatio(dpr);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const L = LIGHTING[cfg.lighting] || LIGHTING.cinematic_warm;
  renderer.toneMappingExposure = L.exposure;
  renderer.shadowMap.enabled = true;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(cfg.background || 0x07090d);
  scene.environment = studioEnvironment(renderer);
  scene.environmentIntensity = L.env;
  for (const [kind, spec] of Object.entries({key: L.key, fill: L.fill, rim: L.rim})) {
    const l = kind === 'key' ? new THREE.DirectionalLight(spec[0], spec[1]) : new THREE.SpotLight(spec[0], spec[1] * 8, 10, 0.7, 0.8, 1.5);
    l.position.set(spec[2][0], spec[2][1], spec[2][2]);
    if (kind === 'key') { l.castShadow = true; l.shadow.mapSize.set(1024, 1024); l.shadow.bias = -0.0003; }
    scene.add(l); if (l.target) { l.target.position.set(0, 1.4, 0); scene.add(l.target); }
  }
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  const gltf = await loader.loadAsync(cfg.model);
  const root = gltf.scene;
  root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false; } });
  skinTune(root);
  if (cfg.arms_down !== false) armsDown(root, cfg.bones || {});
  // materials the config names as unusable (e.g. alpha-card hair whose
  // opacity map was not supplied) are hidden rather than drawn opaque
  const hide = new Set(cfg.hide_materials || []);
  if (hide.size) root.traverse((o) => { if (o.isMesh && [].concat(o.material).some((m) => hide.has(m.name))) o.visible = false; });
  if (cfg.yaw) root.rotation.y = cfg.yaw;
  if (cfg.scale) root.scale.setScalar(cfg.scale);
  scene.add(root);
  root.updateMatrixWorld(true);
  const {bones, source, missing} = resolveBones(root, cfg.bones || {});
  const bs = resolveBlendshapes(root, cfg.blendshapes || {});
  const vis = resolveVisemes(root);
  const ctl = new AvatarController(bones, bs.shapes, {mode: document.body.getAttribute('data-cc-mode') || 'unavailable', seed: 11, visemes: vis});
  // framing from the head bone (or the model bounds)
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const headPos = bones.head ? bones.head.getWorldPosition(new THREE.Vector3()) : new THREE.Vector3(box.getCenter(new THREE.Vector3()).x, box.max.y - size.y * 0.08, 0);
  const unit = size.y / 1.75;
  const span = (FRAMING[cfg.framing] || FRAMING.chest) * unit;
  // the head bone sits at the top of the neck: a face shot centres ~9 cm
  // above it, chest and waist shots lower
  const lift = {face: 0.09, chest: -0.12, waist: -0.3}[cfg.framing] ?? -0.12;
  const target = headPos.clone().add(new THREE.Vector3(0, lift * unit, 0));
  const camera = new THREE.PerspectiveCamera(24, 1, 0.01, 100);
  const dist = span / (2 * Math.tan(THREE.MathUtils.degToRad(12)));
  const view = cfg.view || 'front';
  const ang = view === 'three_quarter' ? THREE.MathUtils.degToRad(35) : 0;
  camera.position.set(target.x + Math.sin(ang) * dist, target.y + span * 0.05, target.z + Math.cos(ang) * dist);
  camera.lookAt(target);
  const resize = () => { const w = Math.max(1, stage.clientWidth), h = Math.max(1, stage.clientHeight); renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  resize();
  // render loop: paused when hidden, adaptive quality over 20 ms frames
  const fps = window.__ccFps = []; let frames = 0, secT = performance.now(), ema = 16, last = 0, raf = 0, paused = false;
  const tick = (now) => {
    raf = 0;
    const dt = last ? (now - last) / 1000 : 1 / 60; last = now;
    const moving = ctl.update(dt);
    renderer.render(scene, camera);
    ema = ema * 0.9 + Math.min(100, dt * 1000) * 0.1;
    if (ema > 20 && dpr > 0.75) { dpr = Math.max(0.75, dpr - 0.25); renderer.setPixelRatio(dpr); renderer.shadowMap.enabled = dpr > 1; resize(); ema = 16; stage.setAttribute('data-cc-dpr', String(dpr)); }
    frames++;
    if (now - secT >= 1000) { fps.push(Math.round(frames * 1000 / (now - secT))); frames = 0; secT = now; stage.setAttribute('data-cc-fps', String(fps[fps.length - 1])); }
    if (moving && !reduced && !paused && !document.hidden) raf = requestAnimationFrame(tick);
  };
  const go = () => { if (!raf && !reduced && !paused && !document.hidden) { last = 0; raf = requestAnimationFrame(tick); } };
  window.addEventListener('cc:mode', (e) => { ctl.setMode(e.detail && e.detail.mode); if (reduced) { ctl.update(1 / 60); renderer.render(scene, camera); } else go(); });
  window.addEventListener('cc:speak', (e) => { ctl.setSpeaking(e.detail && e.detail.on); go(); });
  // THE AUDIO HOOK: {amplitude} or {viseme, weight} per frame from a TTS or
  // audio pipeline; attachAudio() derives amplitude from a media element
  window.addEventListener('cc:speech', (e) => { ctl.setSpeech(e.detail); go(); });
  window.addEventListener('cc:pause', (e) => { paused = !!(e.detail && e.detail.paused); if (!paused) go(); });
  document.addEventListener('visibilitychange', go);
  if (window.ResizeObserver) new ResizeObserver(() => { resize(); if (reduced) renderer.render(scene, camera); }).observe(stage);
  ctl.update(1 / 60); renderer.render(scene, camera);
  stage.classList.add('cc-3d-on', 'cc-real-model');
  const tag = stage.querySelector('[data-placeholder]');
  if (cfg.candidate_label && tag) { tag.textContent = cfg.candidate_label; stage.classList.add('cc-candidate'); }
  stage.setAttribute('data-cc-3d', reduced ? 'static' : 'on');
  stage.setAttribute('data-cc-model', cfg.model);
  window.__ccAvatar = {controller: ctl, bones: source, missing, blendshapes: bs.names, blendshapesMissing: bs.missing.length,
    visemes: Object.keys(vis), camera, root, renderer, scene, render: () => renderer.render(scene, camera)};
  go();
  return window.__ccAvatar;
}

/** Drive the mouth from a playing audio element (WebAudio RMS amplitude). */
export function attachAudio(mediaElement) {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC(), src = ctx.createMediaElementSource(mediaElement), an = ctx.createAnalyser();
  an.fftSize = 1024; src.connect(an); an.connect(ctx.destination);
  const buf = new Float32Array(an.fftSize); let on = true;
  const step = () => {
    if (!on) return;
    an.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v;
    const amp = Math.min(1, Math.sqrt(s / buf.length) * 6);
    window.dispatchEvent(new CustomEvent('cc:speech', {detail: mediaElement.paused ? null : {amplitude: amp}}));
    requestAnimationFrame(step);
  };
  step();
  return () => { on = false; window.dispatchEvent(new CustomEvent('cc:speech', {detail: null})); ctx.close(); };
}
