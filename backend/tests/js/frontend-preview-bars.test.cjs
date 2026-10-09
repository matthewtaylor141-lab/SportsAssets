// FRONTEND PREVIEW HARNESS: fixed-bar pairing (.github/frontend-preview/shots.js).
//
// Production device run 37834226533 failed no_fixed_bar_overlap on desktop,
// iPad portrait and iPad landscape Command: every pair named #hq-stage (the
// WebGL headquarters, 100 % of the viewport, z-index 0) or #hq-tags (its desk
// label overlay, 100 %, z-index 8, pointer-events none) against a HUD bar at
// z-index 9-22 painted above it, plus #hq-alert x #hq-hint.gone (a product
// defect, fixed in hq.js). The harness now lists a box that shows on >= 90 %
// of the viewport and paints BENEATH the other box as a background layer
// instead of an overlap. These tests pin that it still flags every real
// overlap: two smaller bars, a full-viewport box painted ABOVE a bar, a layer
// just under the 90 % line, and stacking contexts that put a "background"
// above the bar.
//
// The block between the BAR PAIRING markers is evaluated exactly as written,
// with fake elements standing in for the DOM.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/shots.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> BAR PAIRING'), SRC.indexOf('// <<< BAR PAIRING'));

const DEFAULTS = { position: 'static', zIndex: 'auto', opacity: '1', transform: 'none', filter: 'none', backdropFilter: 'none',
  perspective: 'none', clipPath: 'none', maskImage: 'none', isolation: 'auto', mixBlendMode: 'normal', willChange: 'auto',
  contain: 'none', display: 'block' };

function harness() {
  let order = 0;
  const root = { css: {}, parentElement: null, order: order++ };
  const body = { css: {}, parentElement: root, order: order++ };
  const el = (css, parent) => ({ css, parentElement: parent || body, order: order++,
    compareDocumentPosition(o) { return o.order > this.order ? 4 : 2; } });
  const ctx = { getComputedStyle: (e) => Object.assign({}, DEFAULTS, e.css), Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    document: { documentElement: root }, Math, Number, Object, parseInt };
  const api = vm.runInNewContext('(() => {' + BLOCK + '; return { pairBars, paintsBeneath, BG_SHARE }; })()', ctx);
  // results cross the sandbox boundary as plain data (deepStrictEqual compares prototypes)
  const pairBars = (...a) => JSON.parse(JSON.stringify(api.pairBars(...a)));
  return { el, body, pairBars, paintsBeneath: api.paintsBeneath, BG_SHARE: api.BG_SHARE };
}

// a box as measure() builds it: rect, share of the viewport, z-index, pointer-events
const box = (id, [left, top, w, h], share, z, pe = 'auto', node = null) => ({ id, r: { left, top, right: left + w, bottom: top + h }, share, z, pe, node });
const run = (H, bars) => H.pairBars(bars, () => false, (i, j) => H.paintsBeneath(bars[i].node, bars[j].node));

test('the block under test is the harness pairing code', () => {
  assert.ok(BLOCK.includes('const BG_SHARE = 0.9;'));
  assert.ok(SRC.includes('const paired = pairBars(fixed,'));
  assert.ok(SRC.includes('fixed_overlaps: overlaps.slice(0, 12), background_layers: paired.background_layers,'));
});

test('production desktop Command: the stage and the tag overlay are background layers; the HUD has no overlap', () => {
  // geometry and z-index of the 1440 x 900 production view (local mock reproduces every area_px of run 37834226533)
  const H = harness();
  const fixed = (z, pe) => H.el({ position: 'fixed', zIndex: String(z), pointerEvents: pe || 'auto' });
  const bars = [
    box('#hq-stage', [0, 0, 1440, 900], 1, '0', 'auto', fixed(0)),
    box('#hq-top', [0, 0, 1440, 60], 0.067, '20', 'auto', fixed(20)),
    box('#hq-alert', [352, 66, 718, 55], 0.031, '22', 'auto', fixed(22)),
    box('#hq-fresh', [352, 136, 718, 77], 0.043, '11', 'auto', fixed(11)),
    box('#hq-tags.compact-all', [0, 0, 1440, 900], 1, '8', 'none', fixed(8, 'none')),
    box('div.hud-col.left', [18, 68, 300, 714], 0.165, '10', 'auto', fixed(10)),
    box('div.hud-col.right', [1104, 68, 318, 714], 0.175, '10', 'auto', fixed(10)),
    box('div.hud-bottom', [18, 782, 1404, 104], 0.113, '10', 'auto', fixed(10)),
  ];
  const got = run(H, bars);
  assert.deepEqual(got.overlaps, []);
  const layers = Object.fromEntries(got.background_layers.map((l) => [l.id, l]));
  assert.deepEqual(Object.keys(layers).sort(), ['#hq-stage', '#hq-tags.compact-all']);
  assert.deepEqual(layers['#hq-stage'].beneath, ['#hq-top', '#hq-alert', '#hq-fresh', '#hq-tags.compact-all', 'div.hud-col.left', 'div.hud-col.right', 'div.hud-bottom']);
  assert.deepEqual(layers['#hq-tags.compact-all'].beneath, ['#hq-top', '#hq-alert', '#hq-fresh', 'div.hud-col.left', 'div.hud-col.right', 'div.hud-bottom']);
  assert.equal(layers['#hq-tags.compact-all'].pointer_events, 'none');
  assert.equal(layers['#hq-stage'].z_index, '0');
});

test('a hint left visible under the CRITICAL bar is still an overlap (the production defect)', () => {
  const H = harness();
  const fixed = (z) => H.el({ position: 'fixed', zIndex: String(z) });
  const bars = [
    box('#hq-stage', [0, 0, 1440, 900], 1, '0', 'auto', fixed(0)),
    box('#hq-alert', [352, 66, 718, 55], 0.031, '22', 'auto', fixed(22)),
    box('#hq-hint.gone', [487, 72, 467, 27], 0.01, '9', 'none', fixed(9)),
  ];
  const got = run(H, bars);
  assert.deepEqual(got.overlaps, [{ a: '#hq-alert', b: '#hq-hint.gone', area_px: 12609 }]);
  assert.deepEqual(got.background_layers.map((l) => [l.id, l.beneath]), [['#hq-stage', ['#hq-alert', '#hq-hint.gone']]]);
});

test('two real bars overlapping are flagged with or without a background layer', () => {
  const H = harness();
  const fixed = (z) => H.el({ position: 'fixed', zIndex: String(z) });
  const bars = [
    box('#hq5-floorbar', [102, 102, 628, 41], 0.027, '9200', 'auto', fixed(9200)),
    box('#hq5-floor-summary', [594, 102, 206, 29], 0.006, '9190', 'auto', fixed(9190)),
  ];
  assert.deepEqual(run(H, bars).overlaps, [{ a: '#hq5-floorbar', b: '#hq5-floor-summary', area_px: 3944 }]);
  const withLayer = [box('#stage', [0, 0, 820, 1180], 1, '0', 'auto', fixed(0))].concat(bars);
  const got = run(H, withLayer);
  assert.deepEqual(got.overlaps, [{ a: '#hq5-floorbar', b: '#hq5-floor-summary', area_px: 3944 }]);
  assert.equal(got.background_layers.length, 1);
});

test('a full-viewport box painted ABOVE a bar is an overlap, not a background', () => {
  const H = harness();
  const fixed = (z) => H.el({ position: 'fixed', zIndex: String(z) });
  const bars = [
    box('#top', [0, 0, 1440, 60], 0.067, '20', 'auto', fixed(20)),
    box('#sheet', [0, 0, 1440, 900], 1, '50', 'auto', fixed(50)),
  ];
  const got = run(H, bars);
  assert.deepEqual(got.overlaps, [{ a: '#top', b: '#sheet', area_px: 86400 }]);
  assert.deepEqual(got.background_layers, []);
});

test('a layer under 90 % of the viewport is a bar like any other', () => {
  const H = harness();
  const fixed = (z) => H.el({ position: 'fixed', zIndex: String(z) });
  assert.equal(H.BG_SHARE, 0.9);
  const bars = [
    box('#big', [0, 0, 1440, 800], 0.889, '0', 'auto', fixed(0)),
    box('#top', [0, 0, 1440, 60], 0.067, '20', 'auto', fixed(20)),
  ];
  assert.deepEqual(run(H, bars).overlaps, [{ a: '#big', b: '#top', area_px: 86400 }]);
  bars[0].share = 0.9;
  assert.deepEqual(run(H, bars).overlaps, []);
});

test('pairs that nest, or touch by two pixels or less, are not overlaps (unchanged)', () => {
  const H = harness();
  const fixed = (z) => H.el({ position: 'fixed', zIndex: String(z) });
  const bars = [box('#a', [0, 0, 100, 50], 0.01, '1', 'auto', fixed(1)), box('#b', [98, 0, 100, 50], 0.01, '2', 'auto', fixed(2))];
  assert.deepEqual(run(H, bars).overlaps, []);
  bars[1].r.left = 90; bars[1].r.right = 190;
  assert.equal(H.pairBars(bars, () => true, () => true).overlaps.length, 0);
  assert.equal(run(H, bars).overlaps.length, 1);
});

test('stacking order follows stacking contexts, not the raw z-index of the box', () => {
  const H = harness();
  const layer = H.el({ position: 'fixed', zIndex: '50' });
  // a bar inside a z-index 100 container paints above the z-index 50 layer...
  const high = H.el({ position: 'relative', zIndex: '100' });
  const barHigh = H.el({ position: 'fixed', zIndex: '1' }, high);
  assert.equal(H.paintsBeneath(layer, barHigh), true);
  // ...and inside a z-index 1 container it paints beneath it, whatever its own z-index
  const low = H.el({ position: 'relative', zIndex: '1' });
  const barLow = H.el({ position: 'fixed', zIndex: '999' }, low);
  assert.equal(H.paintsBeneath(layer, barLow), false);
  assert.equal(H.paintsBeneath(barLow, layer), true);
  const bars = [box('#layer', [0, 0, 1440, 900], 1, '50', 'auto', layer), box('#bar', [0, 0, 1440, 60], 0.067, '999', 'auto', barLow)];
  assert.deepEqual(run(H, bars).overlaps, [{ a: '#layer', b: '#bar', area_px: 86400 }]);
  // an opacity < 1 wrapper is a context too (z auto = 0 within the parent)
  const zero = H.el({ position: 'fixed', zIndex: '0' });
  const faded = H.el({ opacity: '0.9' });
  const barFaded = H.el({ position: 'fixed', zIndex: '5' }, faded);
  assert.equal(H.paintsBeneath(zero, barFaded), true); // tie at z 0: the later box (the faded wrapper) paints on top
  assert.equal(H.paintsBeneath(barFaded, zero), false); // its own z-index 5 counts only inside the wrapper
});

test('equal z-index: the earlier box in the document paints beneath', () => {
  const H = harness();
  const a = H.el({ position: 'fixed', zIndex: '10' });
  const b = H.el({ position: 'fixed', zIndex: '10' });
  assert.equal(H.paintsBeneath(a, b), true);
  assert.equal(H.paintsBeneath(b, a), false);
  const auto1 = H.el({ position: 'fixed' });
  const neg = H.el({ position: 'fixed', zIndex: '-1' });
  assert.equal(H.paintsBeneath(neg, auto1), true);
});
