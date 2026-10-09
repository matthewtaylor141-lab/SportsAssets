// FRONTEND PREVIEW HARNESS: the controls of a background layer (.github/frontend-preview/shots.js).
//
// RC6 lists a full-viewport box painted beneath a bar (#hq-stage, the WebGL
// headquarters; #hq-tags, its desk-label overlay) as a background layer
// instead of an overlap (frontend-preview-bars.test.cjs). The layer is scene;
// its CONTROLS are not: #hq-tags is pointer-events none, but every desk tag in
// it is a button (pointer-events auto) that must take a tap. A tag that runs
// under a HUD bar painted above the layer is half hidden and its covered part
// takes no tap, so each child control intersecting a bar the layer sits
// beneath is an overlap (no_fixed_bar_overlap), and the layer record says
// whether it holds the canvas and how many controls it has. The block between
// the LAYER CHILDREN markers is evaluated as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/shots.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> LAYER CHILDREN'), SRC.indexOf('// <<< LAYER CHILDREN'));
const H = vm.runInNewContext('(() => {' + BLOCK + '; return { layerChildOverlaps }; })()', { Math });
const R = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
const plain = (x) => JSON.parse(JSON.stringify(x));

test('the block under test is the harness code, and its findings are overlaps', () => {
  assert.ok(BLOCK.includes('const layerChildOverlaps = (children, above) =>'));
  assert.ok(SRC.includes('const overlaps = paired.overlaps.concat(layerOverlaps);'));
  assert.ok(SRC.includes("fixed_overlaps: overlaps.slice(0, 12), background_layers: paired.background_layers,"));
  assert.ok(SRC.includes('background_layer_evidence: layerEvidence, layer_child_overlaps: layerOverlaps.length,'));
  // only controls that take pointer events count; the layer's own box never does
  assert.ok(SRC.includes(".filter(e => vis(e) && getComputedStyle(e).pointerEvents !== 'none')"));
});

test('a desk tag under a HUD column is an overlap; a tag clear of every bar is not', () => {
  const hud = [{ id: 'div.hud-col.left', r: R(18, 68, 300, 714) }, { id: '#hq-top', r: R(0, 0, 1440, 60) }];
  const under = [{ id: '#hq-tags > button.tag[data-desk=derek]', r: R(250, 300, 140, 60) }];
  assert.deepEqual(plain(H.layerChildOverlaps(under, hud)), [{ a: '#hq-tags > button.tag[data-desk=derek]', b: 'div.hud-col.left', area_px: 68 * 60 }]);
  const clear = [{ id: '#hq-tags > button.tag[data-desk=xavier]', r: R(600, 300, 140, 60) }];
  assert.deepEqual(plain(H.layerChildOverlaps(clear, hud)), []);
});

test('touching by two pixels or less is not an overlap (the bar pairing rule)', () => {
  const hud = [{ id: 'bar', r: R(0, 0, 100, 100) }];
  assert.equal(H.layerChildOverlaps([{ id: 't', r: R(98, 10, 50, 50) }], hud).length, 0);
  assert.equal(H.layerChildOverlaps([{ id: 't', r: R(97, 10, 50, 50) }], hud).length, 1);
});
