// FRONTEND PREVIEW HARNESS: controls a tap cannot reach (.github/frontend-preview/shots.js).
//
// A control can be on screen, 44 px and still take no tap: another box sits
// over it. Found this way (local preview, synthetic reads, frontend f90dafdd):
// agent.html's navigation under the shell's page bar, status strip and rail on
// every device (9 controls per view), /positions' bar links and Refresh under
// the strip. A cover that belongs to a fixed bar at or above the control's
// upper half can never be scrolled away at rest; a bottom tab bar over content
// beneath the fold can (not counted); a control mostly scrolled out of a
// scrolling strip is reached by scrolling it (not counted). These tests pin
// those rules. The block between the REACHABILITY markers is evaluated as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/shots.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> REACHABILITY'), SRC.indexOf('// <<< REACHABILITY'));
const H = vm.runInNewContext('(() => {' + BLOCK + '; return { visiblePart, reachVerdict }; })()', { Math });
const R = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
const VW = 390, VH = 844;

test('the block under test is the harness reachability code, and it is reported', () => {
  assert.ok(BLOCK.includes('const reachVerdict = (r, part, hit, cover, room, vh) =>'));
  assert.ok(SRC.includes("if (v === 'COVERED') unreachable.push("));
  assert.ok(SRC.includes('unreachable_controls: unreachable.length, unreachable_controls_first: unreachable.slice(0, 12),'));
});

test('a control whose centre hits itself is reached', () => {
  const r = R(20, 300, 120, 44);
  assert.equal(H.reachVerdict(r, H.visiblePart(r, [], VW, VH), 'self', null, 0, VH), 'REACHED');
});

test('the production defect: an agent link under the fixed page bar is covered', () => {
  // agent.html, iPhone: "Derek" link at y 30-68 under the 64 px page bar (top 0)
  const r = R(220, 30, 100, 38);
  const v = H.reachVerdict(r, H.visiblePart(r, [], VW, VH), 'cover', { fixed: true, top: 0, bottom: 64 }, 3000, VH);
  assert.equal(v, 'COVERED');
});

test('a bottom tab bar over content beneath the fold is passed by scrolling, unless the page cannot scroll', () => {
  // the shell's phone tab bar: 66 px at the bottom of an 844 px screen
  const r = R(20, 800, 120, 44), part = H.visiblePart(r, [], VW, VH);
  const tabbar = { fixed: true, top: 778, bottom: 844 };
  assert.equal(H.reachVerdict(r, part, 'cover', tabbar, 500, VH), 'SCROLL_REVEALS');
  assert.equal(H.reachVerdict(r, part, 'cover', tabbar, 10, VH), 'COVERED');
  // a bar in the upper half is never scrolled away, however much page is left
  const strip = { fixed: true, top: 64, bottom: 89 };
  assert.equal(H.reachVerdict(R(20, 70, 120, 44), H.visiblePart(R(20, 70, 120, 44), [], VW, VH), 'cover', strip, 5000, VH), 'COVERED');
});

test('an in-flow box over a control covers it', () => {
  const r = R(20, 300, 120, 44);
  assert.equal(H.reachVerdict(r, H.visiblePart(r, [], VW, VH), 'cover', { fixed: false, top: -1, bottom: -1 }, 5000, VH), 'COVERED');
});

test('a control mostly scrolled out of a scrolling strip is not judged at rest; one merely clipped is', () => {
  // Floor view bar on an iPhone: "Active Desk" at x 361-442 in a strip ending at 382
  const r = R(361, 116, 81, 44);
  const strip = { left: 8, top: 110, right: 382, bottom: 166, scrolls: true };
  const part = H.visiblePart(r, [strip], VW, VH);
  assert.ok(part.scroller && part.share < 0.5);
  assert.equal(H.reachVerdict(r, part, 'cover', { fixed: false, top: -1, bottom: -1 }, 0, VH), 'OFFSCREEN');
  // more than half shown: judged like any other control
  const r2 = R(300, 116, 81, 44), part2 = H.visiblePart(r2, [strip], VW, VH);
  assert.equal(H.reachVerdict(r2, part2, 'cover', { fixed: true, top: 0, bottom: 64 }, 0, VH), 'COVERED');
  // a clip that does not scroll (overflow hidden) never excuses a covered control
  const hidden = { left: 8, top: 110, right: 382, bottom: 166, scrolls: false };
  assert.equal(H.reachVerdict(r, H.visiblePart(r, [hidden], VW, VH), 'cover', { fixed: true, top: 0, bottom: 64 }, 0, VH), 'COVERED');
});

test('the visible part is cut by the viewport and every clipping ancestor', () => {
  const p = H.visiblePart(R(-20, -10, 100, 40), [{ left: 0, top: 0, right: 50, bottom: 100, scrolls: false }], VW, VH);
  assert.deepEqual(JSON.parse(JSON.stringify(p.v)), { left: 0, top: 0, right: 50, bottom: 30 });
  assert.equal(H.visiblePart(R(500, 10, 40, 40), [], VW, VH).empty, true);
});
