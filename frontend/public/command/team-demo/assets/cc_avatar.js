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
 *
 * WORLD-AXIS JOINTS (V2). A 3ds Max Biped (Rocketbox) bone's local X runs
 * ALONG the bone, so an Euler "x" on its head bone twists the head instead
 * of nodding it. mountAvatar therefore hands the controller `joints`: for
 * each driven bone its rest quaternion and the model's own right / up /
 * forward axes expressed in the bone's parent space, so pitch, yaw and roll
 * mean the same thing on every rig. Without joints (the node tests' bare
 * bones) the controller falls back to Euler offsets as before.
 *
 * RESTRAINED GESTURES (V2): elbows rest slightly bent; while the agent is
 * working a forearm occasionally drifts up and settles, and while REAL
 * speech audio plays one hand lifts into a small beat gesture that follows
 * the speech envelope. Shoulders rise a little with each breath.
 *
 * SPEECH FROM AUDIO (V2): attachAudio() measures the playing element's RMS
 * and three frequency bands; the controller turns the envelope into jaw
 * opening and the bands into viseme weights (open vowels, rounded vowels,
 * spread vowels, sibilants), with onsets nodding the head and lifting the
 * brows. The page's audio is found automatically (any same-origin <audio>
 * or Audio() that plays while a model is mounted); the 'cc:speech' event
 * remains the explicit hook.
 *
 * LABELS STAY OFF THE CHARACTER (V2): layoutStage() measures the stage's own
 * name block, controls and status line and frames the character into the
 * space they leave (beside the name block on a wide stage, below it on a
 * narrow one); the canvas ends above the status line and fades out there.
 */
import * as THREE from './three.module.min.js';

export const VERSION = 'CC_AVATAR_PIPELINE_V2';
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
    // world-axis joints by role (see WORLD-AXIS JOINTS above); optional
    this.joints = opts.joints || {};
    this.mode = opts.mode || 'unavailable';
    this.t = 0; this.speaking = false;
    this.nextBlink = this._blinkGap(); this.blinkLeft = 0;
    this.look = {yaw: 0, pitch: 0}; this.lookAt = 0;
    this.head = {yaw: 0, pitch: 0}; this.eye = {yaw: 0, pitch: 0};
    this.posture = 0; this.postureTarget = 0; this.nextPosture = 4 + 4 * this.rand();
    this.still = false;
    this.stats = {blinks: 0, saccades: 0, maxHeadYaw: 0, maxBreath: 0, speechFrames: 0, postureShifts: 0, gestures: 0, onsets: 0, maxViseme: 0};
    this.visemes = opts.visemes || {};
    this.speech = null;             // {amplitude 0..1, bands?} or {viseme, weight}, from audio only
    // speech envelope (fast attack, slower release) and its slow follower
    this.env = 0; this.envSlow = 0; this.nod = 0; this.browKick = 0; this.lastOnset = -1;
    this.vis = {aa: 0, O: 0, E: 0, I: 0, SS: 0, FF: 0, PP: 0};
    // arms: per side, the gesture's target and current flexion
    this.arm = {1: {up: 0, fore: 0, upT: 0, foreT: 0}, '-1': {up: 0, fore: 0, upT: 0, foreT: 0}};
    this.gesture = null; this.nextGesture = 3 + 4 * this.rand();
    this.eyeShapes = !!(this.shapes.eyeLookOutLeft || this.shapes.eyeLookInLeft);
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
  /** pitch (+ = look up / lean back), yaw (+ = toward the model's left),
   *  roll, in radians about the MODEL's axes; Euler fallback without joints. */
  _turn(role, pitch, yaw, roll) {
    const j = this.joints[role];
    if (!j) return this._rot(role, pitch, yaw, roll);
    let q = j.rest;
    if (roll) q = qmul(qaxis(j.axes.z, roll), q);
    if (pitch) q = qmul(qaxis(j.axes.x, -pitch), q);
    if (yaw) q = qmul(qaxis(j.axes.y, yaw), q);
    j.bone.quaternion.set(q[0], q[1], q[2], q[3]);
  }
  /** a hinge about the joint's own precomputed axis (arms) */
  _hinge(name, angle) { const j = this.joints[name]; if (!j) return; const q = qmul(qaxis(j.axis, angle), j.rest); j.bone.quaternion.set(q[0], q[1], q[2], q[3]); }
  _gestures(T, dt, off) {
    const talking = !!(this.speech && typeof this.speech.amplitude === 'number');
    if (off) this.gesture = null;
    else if (this.gesture && T >= this.gesture.end) { this.gesture = null; this.nextGesture = T + (talking ? 1.5 + 2.5 * this.rand() : 7 + 7 * this.rand()); }
    else if (!this.gesture && T >= this.nextGesture && this.mode !== 'waiting') {
      // one hand at a time; the right hand more often; a beat while speaking,
      // a small drift while working
      const side = this.rand() < 0.62 ? -1 : 1;
      this.gesture = talking ? {side, up: 0.14 + 0.08 * this.rand(), fore: 0.8 + 0.3 * this.rand(), beat: true, end: T + 1.4 + 1.4 * this.rand()}
                             : {side, up: 0.05 + 0.05 * this.rand(), fore: 0.35 + 0.25 * this.rand(), beat: false, end: T + 2 + 1.5 * this.rand()};
      this.stats.gestures++;
    }
    for (const s of [1, -1]) {
      const a = this.arm[s], g = this.gesture && this.gesture.side === s ? this.gesture : null;
      // elbows rest a little bent, never locked straight
      a.upT = off ? 0.02 : g ? g.up : 0.04;
      a.foreT = off ? 0.14 : g ? g.fore + (g.beat ? 0.22 * (this.env - 0.35) : 0) : 0.24;
      a.up = damp(a.up, a.upT, 0.45, dt); a.fore = damp(a.fore, a.foreT, g && g.beat ? 0.18 : 0.4, dt);
      this._hinge(s === 1 ? 'leftUpperArm' : 'rightUpperArm', a.up);
      this._hinge(s === 1 ? 'leftLowerArm' : 'rightLowerArm', a.fore);
    }
  }
  _speech(T, dt, off) {
    const sp = off ? null : this.speech;
    const amp = sp && typeof sp.amplitude === 'number' ? clamp(sp.amplitude, 0, 1) : 0;
    if (!sp) { this.env = 0; this.envSlow = 0; }
    else {
      this.env = amp > this.env ? damp(this.env, amp, 0.03, dt) : damp(this.env, amp, 0.08, dt);
      this.envSlow = damp(this.envSlow, this.env, 0.35, dt);
      // a syllable onset nods the head a touch and lifts the brows
      if (this.env - this.envSlow > 0.18 && T - this.lastOnset > 0.45) { this.lastOnset = T; this.nod = 1; this.browKick = 1; this.stats.onsets++; }
    }
    this.nod = Math.max(0, this.nod - dt / 0.35); this.browKick = Math.max(0, this.browKick - dt / 0.6);
    const b = sp && sp.bands, e = this.env;
    // visemes from the spectrum when the page measured it: low energy = open
    // or rounded vowels, mid = spread vowels, high = sibilants and fricatives
    const tgt = {aa: 0, O: 0, E: 0, I: 0, SS: 0, FF: 0, PP: 0};
    if (b) {
      const sum = (b.low || 0) + (b.mid || 0) + (b.high || 0) + 1e-6, l = b.low / sum, m = b.mid / sum, h = b.high / sum;
      // conversational, not theatrical: no viseme is driven past ~0.55
      tgt.aa = e * clamp(1.5 * l - 0.25, 0, 1) * 0.5;
      tgt.O = e * clamp(2.2 * (l - m) - 0.3, 0, 1) * 0.35;
      tgt.E = e * clamp(1.8 * m - 0.2, 0, 1) * 0.45;
      tgt.I = e * clamp(2 * m - 0.6, 0, 1) * 0.3;
      tgt.SS = clamp(2.4 * h - 0.5, 0, 1) * clamp(e * 4, 0, 1) * 0.55;
      tgt.FF = clamp(2.4 * h - 0.9, 0, 1) * clamp(e * 4, 0, 1) * 0.3;
      tgt.PP = sp && e < 0.08 && this.envSlow > 0.15 ? 0.5 : 0;     // a closure between syllables
    }
    for (const k of Object.keys(tgt)) { this.vis[k] = damp(this.vis[k], tgt[k], 0.045, dt); this.stats.maxViseme = Math.max(this.stats.maxViseme, this.vis[k]); }
    const ids = {aa: 'aa', O: 'O', E: 'E', I: 'I', SS: 'SS', FF: 'FF', PP: 'PP'};
    for (const id of Object.keys(this.visemes)) {
      const w = sp && sp.viseme ? (sp.viseme === id ? clamp(sp.weight ?? 1, 0, 1) : 0) : (ids[id] ? this.vis[id] : 0);
      for (const v of this.visemes[id]) v.mesh.morphTargetInfluences[v.index] = w;
    }
    const talk = !!sp;
    if (talk) this.stats.speechFrames++;
    // with visemes doing the lip shapes the jaw opens less on its own
    this._shape('jawOpen', (b && Object.keys(this.visemes).length ? 0.16 : 0.6) * e);
    this._shape('mouthFunnel', 0);
    this._shape('mouthClose', talk ? 0 : 0.05);
    this._shape('mouthSmileLeft', off ? 0 : 0.12); this._shape('mouthSmileRight', off ? 0 : 0.12);
    this._shape('browInnerUp', (this.mode === 'reviewing' ? 0.25 : (this.mode === 'speaking' || this.speaking) ? 0.12 : 0) + 0.18 * this.browKick);
    return talk;
  }
  update(dt) {
    if (this.still) return false;
    // wall-clock timing up to a quarter second per frame, so a slow device
    // still blinks and breathes on schedule (longer gaps are a stall)
    dt = clamp(dt, 0, 0.25);
    const T = (this.t += dt), off = this.mode === 'unavailable';
    const talking = !!(this.speech && !off);
    // look targets (saccades) and slow head turns, clamped to +/-12 deg
    if (!off && T >= this.lookAt) {
      const wide = this.mode === 'monitoring' ? 1 : this.mode === 'reviewing' ? 0.5 : 0.35;
      this.look.yaw = (this.rand() * 2 - 1) * 0.3 * wide;
      this.look.pitch = this.mode === 'reviewing' ? -0.12 - 0.08 * this.rand() : (this.rand() * 2 - 1) * 0.06;
      if (this.mode === 'speaking' || this.speaking || talking) { this.look.yaw *= 0.3; this.look.pitch = 0.02; }
      this.lookAt = T + 0.8 + 2.4 * this.rand();
      this.stats.saccades++;
    }
    if (off) { this.look.yaw = 0; this.look.pitch = -0.08; }
    this.eye.yaw = damp(this.eye.yaw, this.look.yaw, 0.05, dt);
    this.eye.pitch = damp(this.eye.pitch, this.look.pitch, 0.05, dt);
    this.head.yaw = clamp(damp(this.head.yaw, this.look.yaw * 0.6, 0.9, dt), -HEAD_TURN_LIMIT_RAD, HEAD_TURN_LIMIT_RAD);
    this.head.pitch = clamp(damp(this.head.pitch, this.look.pitch * 0.6, 0.9, dt), -HEAD_TURN_LIMIT_RAD, HEAD_TURN_LIMIT_RAD);
    this.stats.maxHeadYaw = Math.max(this.stats.maxHeadYaw, Math.abs(this.head.yaw));
    const nod = -0.035 * Math.sin(this.nod * Math.PI);           // an onset dips the head ~2 deg
    const roll = talking ? Math.sin(T * 0.7) * 0.025 : 0;
    this._turn('neck', this.head.pitch * 0.4 + nod * 0.3, this.head.yaw * 0.4, roll * 0.4);
    this._turn('head', this.head.pitch * 0.6 + nod * 0.7, this.head.yaw * 0.6, roll * 0.6);
    const ey = this.eye.yaw - this.head.yaw, ep = this.eye.pitch - this.head.pitch;
    // eyes: the look blendshapes when the face has them (they move the eyeball
    // geometry), the eye bones otherwise -- never both
    if (!this.eyeShapes) { this._turn('leftEye', ep, ey, 0); this._turn('rightEye', ep, ey, 0); }
    // a look shape at 1.0 is the eye's full travel; a glance is a fraction
    const g = this.eyeShapes ? 1.6 : 3, gm = this.eyeShapes ? 0.45 : 1;
    this._shape('eyeLookOutLeft', clamp(ey * g, 0, gm)); this._shape('eyeLookInRight', clamp(ey * g, 0, gm));
    this._shape('eyeLookInLeft', clamp(-ey * g, 0, gm)); this._shape('eyeLookOutRight', clamp(-ey * g, 0, gm));
    this._shape('eyeLookDownLeft', clamp(-ep * g, 0, gm)); this._shape('eyeLookDownRight', clamp(-ep * g, 0, gm));
    this._shape('eyeLookUpLeft', clamp(ep * g, 0, gm)); this._shape('eyeLookUpRight', clamp(ep * g, 0, gm));
    // blinks: random 2-6 s, ~150 ms (an offline agent keeps its eyes lowered)
    let blink = 0;
    if (!off) {
      if (T >= this.nextBlink && this.blinkLeft <= 0) { this.blinkLeft = BLINK_S; this.stats.blinks++; }
      if (this.blinkLeft > 0) { this.blinkLeft -= dt; blink = Math.sin(clamp(1 - this.blinkLeft / BLINK_S, 0, 1) * Math.PI); if (this.blinkLeft <= 0) this.nextBlink = T + this._blinkGap(); }
    } else blink = 0.45;
    this._shape('eyeBlinkLeft', blink); this._shape('eyeBlinkRight', blink);
    // relaxed lids: a light squint so a model's neutral face does not stare
    this._shape('eyeSquintLeft', off ? 0 : 0.22); this._shape('eyeSquintRight', off ? 0 : 0.22);
    // breathing: a few milliradians on spine and chest only
    const breath = off ? 0 : Math.sin(T * 2 * Math.PI * 0.24) * 0.006;
    this.stats.maxBreath = Math.max(this.stats.maxBreath, Math.abs(breath));
    // restrained posture shifts (<= 1.5 deg)
    if (!off && T >= this.nextPosture) { this.postureTarget = (this.rand() * 2 - 1) * 0.026; this.nextPosture = T + 5 + 6 * this.rand(); this.stats.postureShifts++; }
    if (off) this.postureTarget = 0;
    this.posture = damp(this.posture, this.postureTarget, 1.2, dt);
    this._turn('spine', breath * 0.5, 0, this.posture * 0.5);
    this._turn('chest', breath, 0, -this.posture * 0.3);
    this._turn('hips', 0, 0, this.posture * 0.4);
    // the shoulders rise a little on each in-breath (joints only)
    this._turn('leftShoulder', 0, 0, breath * 1.2);
    this._turn('rightShoulder', 0, 0, -breath * 1.2);
    this._gestures(T, dt, off);
    // speech: the mouth follows REAL audio (amplitude or visemes) only
    this._speech(T, dt, off);
    // an offline agent settles, then stays still
    if (off && Math.abs(this.head.yaw) < 1e-3 && Math.abs(this.head.pitch - this.look.pitch * 0.6) < 1e-3 && Math.abs(this.posture) < 1e-3
        && Math.abs(this.arm[1].fore - this.arm[1].foreT) < 1e-3 && Math.abs(this.arm[-1].fore - this.arm[-1].foreT) < 1e-3) this.still = true;
    return !this.still;
  }
}

// quaternions as [x, y, z, w] arrays (pure: the controller runs under node)
function qaxis(a, ang) { const s = Math.sin(ang / 2); return [a[0] * s, a[1] * s, a[2] * s, Math.cos(ang / 2)]; }
function qmul(a, b) {
  return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1], a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
          a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3], a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
}

// ════════════════════════════════════════════════════════════════════
// LIGHTING PRESETS AND FRAMING
// ════════════════════════════════════════════════════════════════════
export const LIGHTING = {
  cinematic_warm: {key: [0xffe4cc, 2.4, [-1.6, 2.4, 2.2]], fill: [0xbfd0ff, 0.7, [1.8, 1.6, 1.8]], rim: [0xffffff, 1.6, [1.2, 2.2, -2.0]], env: 0.6, exposure: 1.0},
  cinematic_cool: {key: [0xeaf0ff, 2.2, [-1.5, 2.5, 2.2]], fill: [0xffe0cc, 0.5, [1.8, 1.4, 1.8]], rim: [0x9fe8f0, 1.8, [1.3, 2.2, -2.0]], env: 0.55, exposure: 1.0},
  cinematic_violet: {key: [0xffe8dc, 2.3, [-1.4, 2.3, 2.3]], fill: [0xd8ccff, 0.8, [1.7, 1.5, 1.9]], rim: [0xc8b4ff, 1.9, [1.2, 2.3, -2.0]], env: 0.6, exposure: 1.05},
};
export const FRAMING = {face: 0.34, bust: 0.52, chest: 0.72, waist: 1.05};

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
const AX = {x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1)};

/** World-axis joints (see WORLD-AXIS JOINTS above): for each driven bone its
 *  rest quaternion and the MODEL's right / up / forward axes in the bone's
 *  parent space; the arms get one hinge axis each (the upper arm swings
 *  forward; the forearm folds forward and a little across the body). Taken
 *  once, after armsDown(), with the model facing +Z. */
export function buildJoints(root, bones) {
  root.updateMatrixWorld(true);
  const rootInv = root.getWorldQuaternion(new THREE.Quaternion()).invert();
  const toParent = (b) => rootInv.clone().multiply(b.parent.getWorldQuaternion(new THREE.Quaternion())).invert();
  const inParent = (v, b) => v.clone().applyQuaternion(toParent(b)).normalize().toArray();
  const pos = (b) => root.worldToLocal(b.getWorldPosition(new THREE.Vector3()));
  const joints = {};
  for (const role of ['hips', 'spine', 'chest', 'neck', 'head', 'leftEye', 'rightEye', 'leftShoulder', 'rightShoulder']) {
    const b = bones[role];
    if (b && b.parent) joints[role] = {bone: b, rest: b.quaternion.toArray(), axes: {x: inParent(AX.x, b), y: inParent(AX.y, b), z: inParent(AX.z, b)}};
  }
  for (const [up, lo] of [['leftUpperArm', 'leftLowerArm'], ['rightUpperArm', 'rightLowerArm']]) {
    const a = bones[up], f = bones[lo];
    if (!a || !f || !a.parent) continue;
    const hand = f.children.find((c) => c.isBone);
    const pa = pos(a), pf = pos(f), ph = hand ? pos(hand) : pf.clone().multiplyScalar(2).sub(pa);
    const side = Math.sign(pa.x) || 1;
    const d1 = pf.clone().sub(pa).normalize(), d2 = ph.clone().sub(pf).normalize();
    const ax1 = d1.clone().cross(AX.z).normalize();
    const ax2 = d2.clone().cross(new THREE.Vector3(-0.42 * side, 0.12, 1).normalize()).normalize();
    joints[up] = {bone: a, rest: a.quaternion.toArray(), axis: inParent(ax1, a)};
    joints[lo] = {bone: f, rest: f.quaternion.toArray(), axis: inParent(ax2, f)};
  }
  return joints;
}

/** Where the character may be drawn: the stage minus its own name block,
 *  controls and status line. Returns the canvas height and the band (in
 *  canvas pixels) the framed region must fill, beside the name block on a
 *  wide stage and below it on a narrow one. Pure DOM measurement. */
export function layoutStage(stage, region) {
  const S = stage.getBoundingClientRect(), W = S.width, H = S.height;
  const rel = (r) => (r && r.width > 0 && r.height > 0) ? {l: r.left - S.left, t: r.top - S.top, r: r.right - S.left, b: r.bottom - S.top} : null;
  const textOf = (el) => { const g = document.createRange(); g.selectNodeContents(el); return rel(g.getBoundingClientRect()); };
  const shown = (el) => el && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
  const name = [...stage.querySelectorAll('.cc-ov > *')].filter(shown).map(textOf).filter(Boolean);
  const ctl = [...stage.querySelectorAll('.cc-ctl, .cc-placeholder')].filter(shown).map((e) => rel(e.getBoundingClientRect())).filter(Boolean);
  const low = [...stage.querySelectorAll('.cc-st > *')].filter(shown).map((e) => rel(e.getBoundingClientRect())).filter(Boolean);
  const union = (rs) => rs.length ? {l: Math.min(...rs.map((r) => r.l)), t: Math.min(...rs.map((r) => r.t)), r: Math.max(...rs.map((r) => r.r)), b: Math.max(...rs.map((r) => r.b))} : null;
  const N = union(name), M = 8, TOP = 16;
  const statusTop = low.length ? Math.min(...low.map((r) => r.t)) : H;
  const Hc = Math.max(80, Math.round(statusTop - 6));
  const fit = (top, cx0) => {
    let s = (Hc - top - 4) / region.h;
    s = Math.min(s, (W - 2 * M) / region.w);
    const w = region.w * s, cx = Math.max(cx0, w / 2 + M);
    return {s, top, cx, w};
  };
  // the head must clear the controls (top right) wherever it ends up
  const clearCtl = (f) => { const hw = region.head * f.s / 2; const hit = ctl.filter((r) => r.r > f.cx - hw && r.l < f.cx + hw && r.b > f.top); return hit.length ? Math.max(...hit.map((r) => r.b)) + 6 : null; };
  let beside = null;
  if (N) {
    let f = fit(TOP, Math.max(W / 2, N.r + 16));
    f = fit(TOP, Math.max(W / 2, N.r + 16 + f.w / 2));
    const t = clearCtl(f); if (t !== null) f = fit(t, Math.max(W / 2, N.r + 16 + f.w / 2));
    if (f.cx + f.w / 2 <= W - M && f.cx - f.w / 2 >= N.r + 10) beside = f;
  }
  let below = fit(N ? N.b + 10 : M, W / 2);
  const t2 = clearCtl(below); if (t2 !== null) below = fit(Math.max(below.top, t2), W / 2);
  // beside wins when it leaves the character at least as large
  const pick = beside && beside.s >= below.s * 0.9 ? Object.assign(beside, {how: 'beside'}) : Object.assign(below, {how: 'below'});
  return Object.assign(pick, {W, H, Hc, statusTop});
}

const REGION = {face: 0.36, bust: 0.56, chest: 0.66, waist: 0.98};

export async function mountAvatar(stage, cfg, opts = {}) {
  const [{GLTFLoader}, {MeshoptDecoder}] = await Promise.all([import('./GLTFLoader.js'), import('./meshopt_decoder.module.js')]);
  const reduced = !!opts.reducedMotion;
  const canvas = stage.querySelector('canvas');
  // transparent: the stage's own backdrop shows through, so the canvas edge
  // above the status line is invisible
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true, powerPreference: 'high-performance'});
  renderer.setClearColor(0x000000, 0);
  let dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  renderer.setPixelRatio(dpr);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const L = LIGHTING[cfg.lighting] || LIGHTING.cinematic_warm;
  renderer.toneMappingExposure = L.exposure;
  renderer.shadowMap.enabled = true;
  const scene = new THREE.Scene();
  scene.background = cfg.background ? new THREE.Color(cfg.background) : null;
  scene.environment = studioEnvironment(renderer);
  scene.environmentIntensity = L.env;
  for (const [kind, spec] of Object.entries({key: L.key, fill: L.fill, rim: L.rim})) {
    const l = kind === 'key' ? new THREE.DirectionalLight(spec[0], spec[1]) : new THREE.SpotLight(spec[0], spec[1] * 8, 10, 0.7, 0.8, 1.5);
    l.position.set(spec[2][0], spec[2][1], spec[2][2]);
    if (kind === 'key') { l.castShadow = true; l.shadow.mapSize.set(1024, 1024); l.shadow.bias = -0.0003; l.shadow.normalBias = 0.02; }
    scene.add(l); if (l.target) { l.target.position.set(0, 1.4, 0); scene.add(l.target); }
  }
  // a soft sky/ground fill so the shadow side of the face keeps its colour
  scene.add(new THREE.HemisphereLight(0xe8eefc, 0x3a2c24, L.hemi ?? 0.45));
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
  const joints = buildJoints(root, bones);
  const ctl = new AvatarController(bones, bs.shapes, {mode: document.body.getAttribute('data-cc-mode') || 'unavailable', seed: 11, visemes: vis, joints});
  // the framed region, from the top of the head down (model units)
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const unit = size.y / 1.75;
  const headPos = bones.head ? bones.head.getWorldPosition(new THREE.Vector3()) : new THREE.Vector3(box.getCenter(new THREE.Vector3()).x, box.max.y - size.y * 0.08, 0);
  const sh = bones.leftUpperArm && bones.rightUpperArm ? bones.leftUpperArm.getWorldPosition(new THREE.Vector3()).distanceTo(bones.rightUpperArm.getWorldPosition(new THREE.Vector3())) : 0.36 * unit;
  const framing = cfg.framing in REGION ? cfg.framing : 'chest';
  const region = {h: REGION[framing] * unit, w: framing === 'face' ? 0.32 * unit : framing === 'bust' ? sh + 0.06 * unit : sh + 0.2 * unit, head: 0.26 * unit};
  const top = box.max.y + 0.012 * unit;
  const camera = new THREE.PerspectiveCamera(20, 1, 0.01, 100);
  const half = Math.tan(THREE.MathUtils.degToRad(10));
  // (opts.compact: a fixed-size portrait stage, e.g. the agent-page hero, keeps its own height)
  if (!opts.compact && stage.clientWidth < 560 && stage.clientHeight < 500) stage.style.minHeight = '520px';   // room for the face between the name and the status line
  let lay = null;
  const resize = () => {
    lay = layoutStage(stage, region);
    const W = Math.max(1, Math.round(lay.W)), Hc = Math.max(1, lay.Hc);
    canvas.style.height = Hc + 'px'; canvas.style.bottom = 'auto';
    const fade = 'linear-gradient(to bottom, #000 calc(100% - 42px), transparent)';
    canvas.style.maskImage = fade; canvas.style.webkitMaskImage = fade;
    renderer.setSize(W, Hc, false);
    // the region (top of head .. framing line) fills the band at lay.s px per
    // unit; the camera sits square to it, and a view offset puts it in place
    const d = (Hc / lay.s) / (2 * half);
    const cy = lay.top + region.h * lay.s / 2;
    const target = new THREE.Vector3(headPos.x, top - region.h / 2, headPos.z);
    camera.position.set(target.x, target.y + 0.03 * d, target.z + d);
    camera.lookAt(target.x, target.y, target.z);
    camera.aspect = W / Hc;
    camera.setViewOffset(W, Hc, W / 2 - lay.cx, Hc / 2 - cy, W, Hc);
    camera.updateProjectionMatrix();
    stage.setAttribute('data-cc-layout', lay.how);
  };
  resize();
  // render loop: paused when hidden, adaptive quality over 20 ms frames
  const fps = window.__ccFps = []; let frames = 0, secT = performance.now(), ema = 16, last = 0, raf = 0, paused = false;
  const tick = (now) => {
    raf = 0;
    const dt = last ? (now - last) / 1000 : 1 / 60; last = now;
    const moving = ctl.update(dt);
    renderer.render(scene, camera);
    ema = ema * 0.9 + Math.min(100, dt * 1000) * 0.1;
    // (window.__ccFixedDpr: a fixed-quality inspection render, e.g. the software-GL proof videos)
    if (ema > 20 && dpr > 0.75 && !window.__ccFixedDpr) { dpr = Math.max(0.75, dpr - 0.25); renderer.setPixelRatio(dpr); renderer.shadowMap.enabled = dpr > 1; resize(); ema = 16; stage.setAttribute('data-cc-dpr', String(dpr)); }
    frames++;
    if (now - secT >= 1000) { fps.push(Math.round(frames * 1000 / (now - secT))); frames = 0; secT = now; stage.setAttribute('data-cc-fps', String(fps[fps.length - 1])); }
    if (moving && !reduced && !paused && !document.hidden) raf = requestAnimationFrame(tick);
  };
  const go = () => { if (!raf && !reduced && !paused && !document.hidden) { last = 0; raf = requestAnimationFrame(tick); } };
  const onMode = (e) => { ctl.setMode(e.detail && e.detail.mode); if (reduced) { ctl.update(1 / 60); renderer.render(scene, camera); } else go(); };
  const onSpeak = (e) => { ctl.setSpeaking(e.detail && e.detail.on); go(); };
  const onSpeech = (e) => { ctl.setSpeech(e.detail); go(); };
  const onPause = (e) => { paused = !!(e.detail && e.detail.paused); if (!paused) go(); };
  window.addEventListener('cc:mode', onMode);
  window.addEventListener('cc:speak', onSpeak);
  // THE AUDIO HOOK: {amplitude, bands} or {viseme, weight} per frame from a
  // TTS or audio pipeline; attachAudio() derives both from a media element,
  // and autoAudio() attaches it to whatever audio the page plays
  window.addEventListener('cc:speech', onSpeech);
  autoAudio();
  window.addEventListener('cc:pause', onPause);
  document.addEventListener('visibilitychange', go);
  // the labels change size as records arrive: frame again whenever they do
  let ro = null;
  if (window.ResizeObserver) { ro = new ResizeObserver(() => { resize(); renderer.render(scene, camera); }); ro.observe(stage); stage.querySelectorAll('.cc-ov, .cc-st, .cc-ctl').forEach((el) => ro.observe(el)); }
  // DISPOSE: stop the loop, drop every listener and free the GPU (geometry,
  // materials, textures, the environment map and the context itself)
  let disposed = false;
  const dispose = () => {
    if (disposed) return; disposed = true; paused = true;
    if (raf) cancelAnimationFrame(raf); raf = 0;
    window.removeEventListener('cc:mode', onMode); window.removeEventListener('cc:speak', onSpeak);
    window.removeEventListener('cc:speech', onSpeech); window.removeEventListener('cc:pause', onPause);
    document.removeEventListener('visibilitychange', go);
    if (ro) ro.disconnect();
    scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      for (const m of o.material ? [].concat(o.material) : []) {
        for (const v of Object.values(m)) if (v && v.isTexture) v.dispose();
        m.dispose();
      }
    });
    if (scene.environment) scene.environment.dispose();
    renderer.dispose(); renderer.forceContextLoss();
    stage.classList.remove('cc-3d-on', 'cc-real-model'); stage.setAttribute('data-cc-3d', 'disposed');
    if (window.__ccAvatar && window.__ccAvatar.dispose === dispose) window.__ccAvatar = null;
  };
  // per-stage pause (an off-screen portrait stops drawing); cc:pause still pauses every stage
  const setPaused = (p) => { paused = !!p; if (!paused) go(); };
  ctl.update(1 / 60); renderer.render(scene, camera);
  stage.classList.add('cc-3d-on', 'cc-real-model');
  const tag = stage.querySelector('[data-placeholder]');
  if (cfg.candidate_label && tag) { tag.textContent = cfg.candidate_label; stage.classList.add('cc-candidate'); }
  // the text alternative now describes the licensed model, not the placeholder
  const who = (stage.querySelector('#cc-name') || {}).textContent || 'the agent';
  const alt = who + ', an AI agent, shown as a licensed 3D character' + (cfg.credit ? ' (' + cfg.credit + ')' : '') + '. The pose follows the agent_status record; the mouth moves only with real speech audio.';
  canvas.setAttribute('aria-label', alt);
  const altP = stage.querySelector('#cc-alt'); if (altP) altP.textContent = alt;
  stage.setAttribute('data-cc-3d', reduced ? 'static' : 'on');
  stage.setAttribute('data-cc-model', cfg.model);
  window.__ccAvatar = {controller: ctl, bones: source, missing, blendshapes: bs.names, blendshapesMissing: bs.missing.length,
    visemes: Object.keys(vis), joints: Object.keys(joints), layout: () => lay, camera, root, renderer, scene, render: () => renderer.render(scene, camera),
    dispose, setPaused};
  go();
  return window.__ccAvatar;
}

// ════════════════════════════════════════════════════════════════════
// AUDIO: amplitude and bands from whatever the page plays
// ════════════════════════════════════════════════════════════════════
let _actx = null;
const _attached = new WeakMap();

/** Drive the mouth from a playing audio element: RMS amplitude plus low
 *  (100-900 Hz), mid (900-2600 Hz) and high (2600-8000 Hz) band energy,
 *  sent as 'cc:speech' every frame while it plays and null when it stops.
 *  One AudioContext for the page; an element is attached once (WebAudio
 *  allows one source per element). Returns a detach function. */
export function attachAudio(mediaElement) {
  if (_attached.has(mediaElement)) return _attached.get(mediaElement);
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return () => {};
  _actx = _actx || new AC();
  if (_actx.state === 'suspended' && _actx.resume) _actx.resume().catch(() => {});
  const src = _actx.createMediaElementSource(mediaElement), an = _actx.createAnalyser();
  an.fftSize = 1024; an.smoothingTimeConstant = 0.35; src.connect(an); an.connect(_actx.destination);
  const buf = new Float32Array(an.fftSize), spec = new Float32Array(an.frequencyBinCount);
  const hz = _actx.sampleRate / an.fftSize;
  const band = (a, b) => { let s = 0; for (let i = Math.max(1, Math.floor(a / hz)); i < Math.min(spec.length, Math.ceil(b / hz)); i++) s += Math.pow(10, spec[i] / 10); return s; };
  let on = true, raf = 0;
  const send = (d) => window.dispatchEvent(new CustomEvent('cc:speech', {detail: d}));
  const step = () => {
    raf = 0;
    if (!on || mediaElement.paused || mediaElement.ended) { send(null); return; }
    an.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v;
    an.getFloatFrequencyData(spec);
    send({amplitude: Math.min(1, Math.sqrt(s / buf.length) * 6), bands: {low: band(100, 900), mid: band(900, 2600), high: band(2600, 8000)}});
    raf = requestAnimationFrame(step);
  };
  const start = () => { if (_actx.state === 'suspended' && _actx.resume) _actx.resume().catch(() => {}); if (!raf) raf = requestAnimationFrame(step); };
  mediaElement.addEventListener('play', start); mediaElement.addEventListener('playing', start);
  mediaElement.addEventListener('pause', () => send(null)); mediaElement.addEventListener('ended', () => send(null));
  if (!mediaElement.paused) start();
  const detach = () => { on = false; send(null); };
  _attached.set(mediaElement, detach);
  return detach;
}

/** The page's own audio, found without the page wiring anything: an
 *  <audio> element in the document (the capture-phase 'play' event) or a
 *  detached Audio() (its play() call). Same-origin, blob: and data: audio
 *  only -- cross-origin audio without CORS would be silenced by WebAudio. */
let _autoOn = false;
export function autoAudio() {
  if (_autoOn || typeof HTMLMediaElement === 'undefined') return;
  _autoOn = true;
  const ours = (el) => {
    if (!(el instanceof HTMLMediaElement) || (typeof HTMLVideoElement !== 'undefined' && el instanceof HTMLVideoElement)) return false;
    const u = el.currentSrc || el.src || '';
    if (/^(blob|data):/.test(u)) return true;
    try { return !!u && new URL(u, location.href).origin === location.origin; } catch (_) { return false; }
  };
  const hook = (el) => { try { if (ours(el)) attachAudio(el); } catch (_) { /* never break the page's audio */ } };
  document.addEventListener('play', (e) => hook(e.target), true);
  const P = HTMLMediaElement.prototype, play = P.play;
  if (!play.__ccHooked) { P.play = function () { hook(this); return play.apply(this, arguments); }; P.play.__ccHooked = true; }
}
