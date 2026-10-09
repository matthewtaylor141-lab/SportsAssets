// COMMAND: a read reaches the screen whether or not the page paints frames.
//
// hq.js schedule() rendered a read on the next animation frame only. An
// animation frame runs when the page paints; Command's WebGL room starves the
// page of frames under software GL (production device runs: 0 fps on desktop
// and iPad Command), and every read waited for one. Measured locally (iPad
// portrait, synthetic reads, f90dafdd): the coverage read that raises a
// CRITICAL item arrived at 7.8 s and the CRITICAL bar appeared at 33.4 s; the
// HUD then moved 68 px under the viewer's finger. Now whichever comes first
// renders: the frame, or a 250 ms timer (bar at 7.5 s).
//
// The scheduler is evaluated exactly as written, with a page that never
// paints (or paints first) around it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
const START = SRC.indexOf('let pending = false;');
const BLOCK = SRC.slice(START, SRC.indexOf('\nfunction wire()', START));

function page() {
  const frames = [], timers = [];
  const ctx = { renders: 0, requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; }, setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; } };
  vm.runInNewContext(BLOCK + '\nfunction render() { renders++; }\nthis.schedule = schedule;', ctx);
  return { ctx, frames, timers };
}

test('the block under test is hq.js schedule()', () => {
  assert.ok(START > 0);
  assert.ok(BLOCK.includes('function schedule() {'));
});

test('without a painted frame a read still renders, once, within 250 ms', () => {
  const { ctx, frames, timers } = page();
  ctx.schedule(); ctx.schedule(); ctx.schedule();
  assert.equal(frames.length, 1);
  assert.equal(timers.length, 1);
  assert.ok(timers[0].ms <= 250);
  // the page never paints: the timer renders
  timers[0].fn();
  assert.equal(ctx.renders, 1);
  // a frame that comes later renders nothing twice
  frames[0]();
  assert.equal(ctx.renders, 1);
});

test('with a painted frame first, the timer renders nothing twice; the next read schedules again', () => {
  const { ctx, frames, timers } = page();
  ctx.schedule();
  frames[0]();
  assert.equal(ctx.renders, 1);
  timers[0].fn();
  assert.equal(ctx.renders, 1);
  ctx.schedule();
  assert.equal(frames.length, 2);
  timers[1].fn();
  assert.equal(ctx.renders, 2);
});
