// COMMAND: the desk tags on the 3D stage never lie across a HUD panel.
//
// hq.js placeTags() showed a desk's tag wherever its anchor fell inside fixed
// bounds (x 350 .. width-370, y 170/240 .. height-130). Those bounds assume one
// layout; the panels move with the screen. On iPad landscape (1180 x 820, 24 px
// status bar) the freshness strip runs 124-201 px and the harness (LAYER
// CHILDREN, synthetic reads, f90dafdd + the inset fix) found Allie's and
// Adriana's tags across it (1,136 / 1,103 px2). Each shown tag is now checked
// against the HUD panels on screen and hidden where it meets one.
//
// The block from HUD_PANELS to the end of placeTags is evaluated as written,
// with a minimal page around it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
const START = SRC.indexOf('// the HUD panels a desk tag must stay clear of (placeTags)');
const BLOCK = SRC.slice(START, SRC.indexOf('\nfunction floorCountsHTML', START));

function page({ anchors, panels, view = 'command', critical = false }) {
  const tags = {};
  const mkTag = (slug) => ({ slug, hidden: true, style: {}, classList: { toggle() {} },
    // the compact tag: 104 x 61 px, centred over its anchor (hq.css .tag translate(-50%, -100%))
    getBoundingClientRect() { const l = parseFloat(this.style.left) - 52, t = parseFloat(this.style.top) - 61; return { left: l, top: t, right: l + 104, bottom: t + 61, width: 104, height: 61 }; } });
  Object.keys(anchors).forEach((s) => { tags[s] = mkTag(s); });
  const host = { classList: { toggle() {} }, querySelector: (sel) => tags[/data-desk="([^"]+)"/.exec(sel)[1]] || null };
  const panelEls = panels.map((p) => ({ visibility: p.visibility || 'visible',
    getBoundingClientRect: () => ({ left: p.box[0], top: p.box[1], right: p.box[2], bottom: p.box[3], width: p.box[2] - p.box[0], height: p.box[3] - p.box[1] }) }));
  let panelQuery = null;
  const ctx = {
    innerWidth: 1180, innerHeight: 820,
    doc: { body: { classList: { contains: (c) => c === 'has-critical' && critical } } },
    S: { scene: { anchorOf: (slug) => anchors[slug] }, view, desk: null, watch: false },
    HQ: { SEATS: Object.keys(anchors).map((slug) => ({ slug })) },
    $: (sel) => (sel === '#hq-tags' ? host : null),
    $$: (sel) => { if (sel === '#hq-tags .tag') return Object.values(tags); panelQuery = sel; return panelEls; },
    getComputedStyle: (e) => ({ visibility: e.visibility }),
  };
  vm.runInNewContext(BLOCK + '\nplaceTags();', ctx);
  return { tags, panelQuery };
}

// iPad landscape, the inset applied: the freshness strip and the two HUD columns
const FRESH = { box: [316, 124, 844, 201] };
const COLS = [{ box: [18, 124, 288, 682] }, { box: [876, 124, 1162, 682] }];

test('the block under test is hq.js placeTags', () => {
  assert.ok(START > 0, 'HUD_PANELS marker');
  assert.ok(BLOCK.includes('function placeTags() {'));
  assert.ok(BLOCK.includes("const HUD_PANELS = '#hq-top, #hq-alert, #hq-fresh, #hq-hint, #hq-caption, #hq-tabbar, #hq-hud .hud-col, #hq-hud .hud-bottom, #hq-hud .drawer, #hq-hud .markets-left, #hq-hud .floor-legend';"));
});

test('a tag that meets a HUD panel is hidden; one on open stage stays', () => {
  const { tags, panelQuery } = page({ anchors: { allocator: { x: 480, y: 190 }, adriana: { x: 700, y: 196 }, derek: { x: 368, y: 410 } }, panels: [FRESH].concat(COLS) });
  assert.equal(tags.allocator.hidden, true);
  assert.equal(tags.adriana.hidden, true);
  assert.equal(tags.derek.hidden, false);
  assert.equal(tags.derek.style.left, '368.0px');
  assert.ok(/#hq-fresh/.test(panelQuery) && /\.hud-col/.test(panelQuery));
});

test('the fixed bounds still apply first; a hidden or empty panel hides nothing', () => {
  // x < 350 stays hidden whatever the panels say
  let r = page({ anchors: { scout: { x: 340, y: 400 } }, panels: [] });
  assert.equal(r.tags.scout.hidden, true);
  // a panel with visibility:hidden (the right column while a desk is open) or no box
  r = page({ anchors: { allocator: { x: 480, y: 190 } }, panels: [{ box: [316, 124, 844, 201], visibility: 'hidden' }, { box: [0, 0, 0, 0] }] });
  assert.equal(r.tags.allocator.hidden, false);
  // the Floor view has no x / y bounds: the panels alone decide
  r = page({ view: 'floor', anchors: { karen: { x: 120, y: 300 }, audrey: { x: 600, y: 400 } }, panels: [{ box: [18, 124, 288, 682] }] });
  assert.equal(r.tags.karen.hidden, true);
  assert.equal(r.tags.audrey.hidden, false);
});
