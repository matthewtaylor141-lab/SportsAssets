// FRONTEND PREVIEW HARNESS: the notch and the home indicator (.github/frontend-preview/shots.js).
//
// Every touch view now runs with the device's env(safe-area-inset-*) applied
// (DevTools Emulation.setSafeAreaInsetsOverride): iPhone 47 / 34 px portrait,
// 47 px sides / 21 px landscape, iPad 24 / 20 px, as the installed app sees
// them. Found (local preview, synthetic reads, frontend f90dafdd): the shell's
// phone tab bar 32 px into the home indicator, its side rail up to 37 px under
// the landscape notch, the page bar's title / Company Pulse under the status
// bar. Text and controls inside a fixed / sticky bar must be inside the safe
// rectangle; in-flow content must keep clear of the side insets and, at rest,
// of the top inset; in-flow content under the bottom inset is passed by
// scrolling. The block between the SAFE AREA markers is evaluated as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/shots.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> SAFE AREA'), SRC.indexOf('// <<< SAFE AREA'));
const H = vm.runInNewContext('(() => {' + BLOCK + '; return { safeAreaViolations }; })()', { Math });
const R = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
const PORTRAIT = { top: 47, right: 0, bottom: 34, left: 0 }, LANDSCAPE = { top: 0, right: 47, bottom: 21, left: 47 };
const plain = (x) => JSON.parse(JSON.stringify(x));

test('the insets are the documented device values and are applied before the page loads', () => {
  assert.ok(SRC.includes("iphone: { top: 47, right: 0, bottom: 34, left: 0 },"));
  assert.ok(SRC.includes("iphone_landscape: { top: 0, right: 47, bottom: 21, left: 47 },"));
  assert.ok(SRC.includes("ipad_portrait: { top: 24, right: 0, bottom: 20, left: 0 },"));
  assert.ok(SRC.includes("ipad_landscape: { top: 24, right: 0, bottom: 20, left: 0 },"));
  assert.ok(SRC.indexOf("await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets });") < SRC.indexOf('await page.goto(BASE + target'));
  // the page's own env() reading is recorded as proof the override took
  assert.ok(SRC.includes('safeArea = { insets, env_read: { top: Math.round(pr.top), left: Math.round(pr.left) }, checked: boxes.length, violations: v.length, first: v.slice(0, 12) };'));
});

test('the production defect: a phone tab bar link on the home indicator', () => {
  const v = plain(H.safeAreaViolations([{ id: 'aside.bt-hq2-shell > a "HQ"', r: R(80, 781, 50, 61), fixed: true }], PORTRAIT, 390, 844));
  assert.deepEqual(v, [{ id: 'aside.bt-hq2-shell > a "HQ"', edge: 'bottom', px: 32 }]);
});

test('a fixed bar title under the status bar; the same bar moved down by the inset is clear', () => {
  assert.equal(H.safeAreaViolations([{ id: 'title', r: R(100, 20, 80, 20), fixed: true }], PORTRAIT, 390, 844)[0].edge, 'top');
  assert.equal(H.safeAreaViolations([{ id: 'title', r: R(100, 67, 80, 20), fixed: true }], PORTRAIT, 390, 844).length, 0);
});

test('in-flow content: the top inset at rest and the side insets count, the bottom inset does not', () => {
  const S = PORTRAIT;
  assert.equal(H.safeAreaViolations([{ id: 'a', r: R(16, 10, 100, 30), fixed: false }], S, 390, 844).length, 1);
  assert.equal(H.safeAreaViolations([{ id: 'b', r: R(16, 820, 100, 30), fixed: false }], S, 390, 844).length, 0);
  const L = LANDSCAPE;
  assert.deepEqual(plain(H.safeAreaViolations([{ id: 'c', r: R(16, 100, 100, 30), fixed: false }], L, 844, 390)), [{ id: 'c', edge: 'left', px: 31 }]);
  assert.deepEqual(plain(H.safeAreaViolations([{ id: 'd', r: R(760, 100, 70, 30), fixed: false }], L, 844, 390)), [{ id: 'd', edge: 'right', px: 33 }]);
});

test('1 px is tolerated, boxes off screen are ignored, no insets mean no findings', () => {
  assert.equal(H.safeAreaViolations([{ id: 'e', r: R(100, 46, 50, 20), fixed: true }], PORTRAIT, 390, 844).length, 0);
  assert.equal(H.safeAreaViolations([{ id: 'f', r: R(100, -60, 50, 20), fixed: true }], PORTRAIT, 390, 844).length, 0);
  assert.equal(H.safeAreaViolations([{ id: 'g', r: R(0, 0, 390, 64), fixed: true }], { top: 0, right: 0, bottom: 0, left: 0 }, 390, 844).length, 0);
});
