/* BETTOR COMMAND · THE AGENT PORTRAIT (agent pages, HQ2 hero).
 *
 * The agent's own licensed 3D character, rendered live in the hero so the
 * person is visible on arrival (the full office stays in "Desk &
 * conversation"). It reuses the character pipeline as is: cc_avatar.js
 * mountAvatar (bones, ARKit blendshapes, arms down, framing, cinematic
 * lighting) and its AvatarController (blinks, breathing, saccades), driven by
 * the agent's REAL floor state through `cc:mode` (hq2-agent.js sets it).
 *
 * A character is drawn ONLY when models/manifest.json has a licensed entry
 * for this agent (model, licence file, SPDX id, licensor, test_asset false).
 * Otherwise the stage shows the agent's initial and name, labelled
 * "3D PORTRAIT PENDING" -- never a stand-in figure and never another agent's
 * body.
 *
 * Lazy: nothing loads until the stage is on screen; an off-screen stage stops
 * drawing; leaving the page disposes the renderer, geometry and textures.
 * prefers-reduced-motion: one still frame, re-rendered only on a state change. */
const MODELS = './team-demo/assets/models/';
const REQUIRED = ['model', 'license_file', 'license_spdx', 'licensed_from'];

const stage = document.querySelector('.bt-portrait');
if (stage && !stage.dataset.mounted) { stage.dataset.mounted = '1'; start(stage); }

function card(stage, label, why) {
  stage.classList.remove('is-loading'); stage.classList.add('is-card');
  stage.setAttribute('data-portrait', label === '3D PORTRAIT PENDING' ? 'pending' : 'unavailable');
  const tag = stage.querySelector('.bt-portrait-tag');
  if (tag) { tag.textContent = label; tag.title = why || ''; }
  const c = stage.querySelector('canvas'); if (c) c.hidden = true;
}

function webgl() {
  try { const c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl'))); }
  catch (e) { return false; }
}

/** The licensed entry for this agent, or null with the reason. */
export function licensedEntry(manifest, slug) {
  const e = manifest && manifest.characters && manifest.characters[slug];
  if (!e) return {entry: null, why: 'no character entry for ' + slug};
  const miss = REQUIRED.filter((k) => !e[k]);
  if (miss.length) return {entry: null, why: 'manifest entry incomplete: ' + miss.join(', ')};
  if (e.test_asset !== false) return {entry: null, why: 'test asset (never shown as a character)'};
  return {entry: e, why: null};
}

async function start(stage) {
  const slug = stage.dataset.slug;
  let manifest = null;
  try {
    const r = await fetch(MODELS + 'manifest.json', {cache: 'no-cache'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    manifest = await r.json();
  } catch (e) { return card(stage, '3D PORTRAIT PENDING', 'the character manifest could not be read (' + e.message + ')'); }
  const {entry, why} = licensedEntry(manifest, slug);
  if (!entry) return card(stage, '3D PORTRAIT PENDING', why);
  if (!webgl()) return card(stage, '3D PORTRAIT UNAVAILABLE', 'this browser has no WebGL');
  const credit = stage.querySelector('.bt-portrait-credit');
  if (credit && entry.credit) credit.textContent = entry.credit.replace(/^Character model:\s*/, '3D · ');

  let handle = null, visible = false, mounting = null;
  const reduced = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const mount = () => {
    if (mounting) return mounting;
    mounting = (async () => {
      const {mountAvatar} = await import('./team-demo/assets/cc_avatar.js');
      const cfg = Object.assign({}, entry, {model: MODELS + entry.model, framing: entry.portrait_framing || 'bust'});
      handle = await mountAvatar(stage, cfg, {reducedMotion: reduced, compact: true});
      stage.classList.remove('is-loading'); stage.classList.add('is-live');
      stage.setAttribute('data-portrait', 'live');
      if (!visible) handle.setPaused(true);
    })().catch((e) => { card(stage, '3D PORTRAIT UNAVAILABLE', 'the model could not be drawn (' + (e && e.message || e) + ')'); });
    return mounting;
  };
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((es) => {
      visible = es.some((x) => x.isIntersecting);
      if (visible) mount();
      if (handle) handle.setPaused(!visible);
    }, {rootMargin: '120px'}).observe(stage);
  } else { visible = true; mount(); }
  window.addEventListener('pagehide', () => { if (handle) { handle.dispose(); handle = null; } });
}
