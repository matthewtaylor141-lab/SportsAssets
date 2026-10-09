// TRADER MODE: an order past its recorded expiry is not protection
// (frontend/public/command/trader-core.js, trader.js).
//
// The server's packet already refuses a STANDING_PROTECTION order whose
// expires_at has passed (trader_mode.packet_state: NO_VALID_ACTIVE_PROTECTION)
// even while the ledger still reads RESTING; the page drew that order as the
// card's "Standing limit" / "Recorded SELL ... remain" (Trader device
// acceptance, synthetic snapshot from the backend's own build_snapshot,
// frontend f90dafdd). target() now skips such an order; the card says
// "PAST EXPIRY · NOT PROTECTION", the orders view lists it labelled the same,
// the focus desk names it. Xavier's review id and time are on the focus desk,
// in UTC; the card names the agents the record names, not a fixed pair.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIR = path.join(__dirname, '../../../frontend/public/command');
const C = (() => { const m = { exports: {} }; vm.runInNewContext(fs.readFileSync(path.join(DIR, 'trader-core.js'), 'utf8'), { module: m, exports: m.exports, Intl, Date, Math, Number, String, Set, Array, Object, JSON, console }); return m.exports; })();
const JS = fs.readFileSync(path.join(DIR, 'trader.js'), 'utf8');
const NOW = 1800000000;
const order = (o) => Object.assign({ position_id: 'p', order_id: 'o', direction: 'SELL', role: 'STANDING_PROTECTION', state: 'RESTING', limit_price: 0.3, remaining_qty: 50 }, o);
const pos = (orders, proposal) => ({ position_id: 'p', orders, proposal: proposal || null });

test('an order past its recorded expiry is not the target; one before it is', () => {
  assert.equal(C.pastExpiry(order({ expires_at: NOW - 1 }), NOW), true);
  assert.equal(C.pastExpiry(order({ expires_at: NOW }), NOW), true);
  assert.equal(C.pastExpiry(order({ expires_at: NOW + 1 }), NOW), false);
  assert.equal(C.pastExpiry(order({ expires_at: null }), NOW), false);
  assert.equal(C.target(pos([order({ expires_at: NOW - 120 })]), NOW), null);
  const live = order({ order_id: 'o2', limit_price: 0.41, expires_at: NOW + 3600 });
  assert.equal(C.target(pos([order({ expires_at: NOW - 120 }), live]), NOW).order_id, 'o2');
  // a recorded proposal still stands in when no live order exists
  assert.equal(C.target(pos([order({ expires_at: NOW - 120 })], { kind: 'RECORDED_PROPOSAL_NOT_AN_ORDER', limit_price: 0.44, direction: 'SELL' }), NOW).limit_price, 0.44);
  // not standing at all: never the target (unchanged)
  assert.equal(C.target(pos([order({ state: 'EXPIRED', expires_at: NOW + 10 }), order({ state: 'REJECTED' })]), NOW), null);
});

test('the standing count still follows the ledger state (the API counts the same way)', () => {
  assert.equal(C.standing(pos([order({ expires_at: NOW - 120 }), order({ state: 'REJECTED' })])).length, 1);
});

test('the near-target filter judges the live target only', () => {
  const p = Object.assign(pos([order({ expires_at: NOW - 120, limit_price: 0.35 })]), { state: 'ACTIVE', quote: { bid: 0.35, ask: 0.37, at: NOW - 10, current: true }, packet: {} });
  assert.equal(C.matches(p, 'near', '', NOW), false);
});

test('recorded instants are shown in UTC, never invented', () => {
  assert.equal(C.utc(NOW), new Date(NOW * 1000).toISOString().slice(11, 19) + 'Z');
  assert.equal(C.utc(null), '—');
  assert.equal(C.utc(NaN), '—');
});

test('the page draws the lapsed order as no protection, the review instant, and the recorded agents', () => {
  assert.ok(JS.includes("t=C.target(p,now)"));
  assert.ok(!JS.includes("t=C.target(p),"));
  assert.ok(JS.includes('<span class="order-qty order-expired">PAST EXPIRY · NOT PROTECTION</span>'));
  assert.ok(JS.includes("${C.pastExpiry(o,now)?'<small class=\"order-expired\">PAST EXPIRY · NOT PROTECTION</small>':''}"));
  assert.ok(JS.includes("is past its recorded expiry: not protection."));
  assert.ok(JS.includes("`Xavier review recorded ${C.utc(rev.reviewed_at)} · …${E(String(rev.review_id).slice(-10))}`:'No Xavier review recorded for this position'"));
  assert.ok(JS.includes("${E(p.entry_agent||'ENTRY AGENT NOT RECORDED')} → ${E(p.management_agent||'MANAGER NOT RECORDED')}"));
  assert.ok(!JS.includes('DEREK → XAVIER ·'));
  // no new data path: the ask still comes only from the snapshot's quote (pinned elsewhere too)
  assert.equal(JS.split('C.cents(q.ask)').length - 1, 2);
});
