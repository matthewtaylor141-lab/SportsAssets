// HQ freshness strip, 'Management' tile (hq-model.js managementOpen, used by
// hq.js freshness): floor/xavier outputs are a 24 h timeline, so the tile used
// to read '1 of 1 stale' from a settled position's last assessment while the
// book held 0 open positions. The tile now counts OPEN positions only
// from floor/xavier position_book, the open PAPER book the API serves for this
// (each open position's current review, null when unreviewed), cross-checked
// with equity/live paper.open_positions.count. Synthetic reads in the served
// shapes; the model is evaluated as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq-model.js'), 'utf8')
  .replace(/^export (const|function) /gm, '$1 ');
const B = {SEATS: [], ago: () => '', esc: (s) => String(s), isFixture: () => false, read: () => new Promise(() => {}), reducedMotion: () => true};

// served shapes at command_floor.py: an outputs row (1624-1641) carries no
// group id; position_book (1451-1464, 1747-1753) is the open PAPER book
function model(paper, xavier) {
  const M = vm.runInNewContext(SRC + '; createModel', {Object, Array, Map, Set, String, Number, Math, Date, JSON, Promise, isFinite, isNaN, setTimeout: () => 0, clearTimeout: () => {}, window: {}, document: undefined})(B, {curve: false});
  M.reads.equity.status = 'OK'; M.reads.equity.data = {paper};
  if (xavier) M.reads.detail.xavier = {status: 'OK', data: xavier, lastOk: 1};
  return M;
}
const OUT_STALE = {kind: 'xavier_management_assessments', id: 77, at: 1760000000, verdict: 'HOLD', recommendation_state: 'STALE', recorded_recommendation: 'HOLD', management_state: 'WAITING_FOR_FRESH_EVIDENCE', superseded_by: null, valuation_id: null, valuation_timestamp: null, age_seconds: 9000, freshness_limit: 300, summary: 'Stale · PAPER g1 · STALE · INTACT'};
const review = (rs, ms) => ({table: 'paper_xavier_reviews', review_id: 5, reviewed_at: 1760000000, refusal: null, recommendation: rs, recommendation_state: rs, management_state: ms, superseded_by: null, recorded_recommendation: 'HOLD', recommendation_label: rs, valuation_id: null, valuation_timestamp: null, age_seconds: 9000, freshness_limit: 300, valuation_expires_at: null});
const pos = (g, cr) => ({position_kind: 'PAPER', group_id: g, current_review: cr, packet: null, protection: null, why: cr ? 'NO_PACKET_RECORDED' : 'NO_REVIEW_RECORDED'});
const book = (open, positions, truncated) => ({open, shown: positions.length, positions_truncated: !!truncated, bound: 50, positions});

test('0 open positions reads 0, whatever the 24 h timeline holds (the 16:45Z case)', () => {
  const r = model({open_positions: {count: 0, rows: []}}, {outputs: [OUT_STALE], position_book: book(0, [])}).managementOpen();
  assert.equal(r.status, 'OK');
  assert.equal(r.open, 0);
  assert.equal(r.stale.length, 0);
  assert.ok(!r.mismatch);
});

test('0 open on equity/live before floor/xavier is read still reads 0', () => {
  const r = model({open_positions: {count: 0, rows: []}}, null).managementOpen();
  assert.equal(r.status, 'OK');
  assert.equal(r.open, 0);
});

test('1 open with a stale current review reads 1 of 1 stale (outputs rows carry no group id)', () => {
  const r = model({open_positions: {count: 1, rows: [{key: 'pk-1'}]}},
    {outputs: [OUT_STALE], position_book: book(1, [pos('pk-1', review('STALE', 'WAITING_FOR_FRESH_EVIDENCE'))])}).managementOpen();
  assert.equal(r.status, 'OK');
  assert.equal(r.open, 1);
  assert.equal(r.reviewed, 1);
  assert.equal(r.stale.length, 1);
  assert.equal(r.stale[0].group_id, 'pk-1');
});

test('a null current_review is unreviewed, never fresh or stale', () => {
  const r = model({open_positions: {count: 2}},
    {outputs: [], position_book: book(2, [pos('g1', review('CURRENT', 'MANAGED')), pos('g2', null)])}).managementOpen();
  assert.equal(r.open, 2);
  assert.equal(r.rows.length, 2);
  assert.equal(r.reviewed, 1);
  assert.equal(r.stale.length, 0);
});

test('a truncated position book is reported, and equity/live is cross-checked', () => {
  const r = model({open_positions: {count: 3}}, {outputs: [], position_book: book(4, [pos('g1', review('INVALID', 'MANAGED'))], true)}).managementOpen();
  assert.equal(r.open, 4);
  assert.equal(r.shown, 1);
  assert.equal(r.truncated, true);
  assert.equal(r.mismatch, true);
  assert.equal(r.equityOpen, 3);
  assert.equal(r.stale.length, 1);
});

test('open positions with no position_book served is UNAVAILABLE, never matched from outputs', () => {
  const r = model({open_positions: {count: 1}}, {outputs: [OUT_STALE], position_book: null}).managementOpen();
  assert.equal(r.status, 'UNAVAILABLE');
  assert.equal(r.open, 1);
});

test('open positions with floor/xavier not read yet report the read state', () => {
  const r = model({open_positions: {count: 1, rows: [{key: 'g1'}]}}, null).managementOpen();
  assert.equal(r.status, 'LOADING');
  assert.equal(r.open, 1);
});

test('hq.js draws the tile from managementOpen (position_book), not from the raw timeline', () => {
  const HQJS = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
  const fresh = HQJS.slice(HQJS.indexOf('function freshness()'), HQJS.indexOf('function freshHTML()'));
  assert.ok(fresh.includes('HQ.managementOpen()'));
  assert.ok(!fresh.includes('HQ.xavierDecisions()'));
  assert.ok(fresh.includes("v: '0 open'"));
  assert.ok(!fresh.includes('no current Xavier review matched'));
  const MODEL = SRC.slice(SRC.indexOf('function managementOpen()'), SRC.indexOf('function curve()'));
  assert.ok(MODEL.includes('position_book'));
  assert.ok(!MODEL.includes('GROUP_KEYS'));
});

// the tile block of hq.js freshness(), evaluated as written over a model run
const HQJS_SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
const TILE = HQJS_SRC.slice(HQJS_SRC.indexOf('  // OPEN positions only (HQ.managementOpen'), HQJS_SRC.indexOf('  const ds = HQ.desks().filter((d) => !d.planned)'));
function tile(M) {
  const ctx = {HQ: M, cells: []};
  vm.runInNewContext(TILE, ctx);
  return ctx.cells.find((c) => c.k === 'Management');
}

test('tile: 1 open with a stale current review renders 1 of 1 open stale in bad tone (the reviewer repro)', () => {
  const c = tile(model({open_positions: {count: 1, rows: [{key: 'pk-1'}]}},
    {outputs: [OUT_STALE], position_book: book(1, [pos('pk-1', review('STALE', 'WAITING_FOR_FRESH_EVIDENCE'))])}));
  assert.equal(c.v, '1 of 1 open stale');
  assert.equal(c.tone, 'bad');
});

test('tile: 0 open renders 0 open in good tone', () => {
  const c = tile(model({open_positions: {count: 0, rows: []}}, {outputs: [OUT_STALE], position_book: book(0, [])}));
  assert.equal(c.v, '0 open');
  assert.equal(c.tone, 'good');
});

test('tile: an unreviewed open position is named and never reads good', () => {
  const c = tile(model({open_positions: {count: 2}}, {outputs: [], position_book: book(2, [pos('g1', review('CURRENT', 'MANAGED')), pos('g2', null)])}));
  assert.equal(c.v, '0 of 2 open stale');
  assert.equal(c.tone, 'warn');
  assert.ok(c.s.includes('1 of 2 reviewed · 1 unreviewed'));
});

test('tile: a truncated book says so and counts over the shown positions', () => {
  const c = tile(model({open_positions: {count: 4}}, {outputs: [], position_book: book(4, [pos('g1', review('CURRENT', 'MANAGED'))], true)}));
  assert.equal(c.v, '0 of 1 shown stale');
  assert.equal(c.tone, 'warn');
  assert.ok(c.s.includes('only 1 of 4 shown (position book truncated)'));
});
