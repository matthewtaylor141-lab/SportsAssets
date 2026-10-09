// HQ freshness strip, 'Management' tile (hq-model.js managementOpen, used by
// hq.js freshness): floor/xavier outputs are a 24 h timeline, so the tile used
// to read '1 of 1 stale' from a settled position's last assessment while the
// book held 0 open positions. The tile now counts OPEN positions only
// (equity/live paper.open_positions) and an assessment only when its group is
// one of them. Synthetic reads; the model is evaluated as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq-model.js'), 'utf8')
  .replace(/^export (const|function) /gm, '$1 ');
const B = {SEATS: [], ago: () => '', esc: (s) => String(s), isFixture: () => false, read: () => new Promise(() => {}), reducedMotion: () => true};

function model(paper, xavierOutputs) {
  const M = vm.runInNewContext(SRC + '; createModel', {Object, Array, Map, Set, String, Number, Math, Date, JSON, Promise, isFinite, isNaN, setTimeout: () => 0, clearTimeout: () => {}, window: {}, document: undefined})(B, {curve: false});
  M.reads.equity.status = 'OK'; M.reads.equity.data = {paper};
  if (xavierOutputs) M.reads.detail.xavier = {status: 'OK', data: {outputs: xavierOutputs}, lastOk: 1};
  return M;
}
const STALE = {kind: 'xavier_management_assessments', management_state: 'WAITING_FOR_FRESH_EVIDENCE', recommendation_state: 'STALE'};
const FRESH = {kind: 'xavier_management_assessments', management_state: 'MANAGED', recommendation_state: 'CURRENT'};

test('0 open positions reads 0, whatever the 24 h timeline holds (the 16:45Z case)', () => {
  const r = model({open_positions: {count: 0, rows: []}}, [Object.assign({group_id: 'g-settled', at: '2026-10-09T10:46:07Z'}, STALE)]).managementOpen();
  assert.equal(r.status, 'OK');
  assert.equal(r.open, 0);
  assert.equal(r.rows.length, 0);
});

test('only assessments of currently open groups count, newest per group', () => {
  const r = model({open_positions: {count: 2, rows: [{group_id: 'g1'}, {position_key: 'g2'}]}}, [
    Object.assign({group_id: 'g1', at: 100}, STALE),
    Object.assign({group_id: 'g1', at: 200}, FRESH),
    Object.assign({group_id: 'g2', at: 150}, STALE),
    Object.assign({group_id: 'g-settled', at: 300}, STALE),
  ]).managementOpen();
  assert.equal(r.open, 2);
  assert.equal(r.rows.length, 2);
  const byGroup = Object.fromEntries(r.rows.map((x) => [x.group_id, x.recommendation_state]));
  assert.deepEqual(byGroup, {g1: 'CURRENT', g2: 'STALE'});
});

test('no open-position count on equity/live is UNAVAILABLE, never a default', () => {
  const r = model({}, [Object.assign({group_id: 'g1'}, STALE)]).managementOpen();
  assert.equal(r.status, 'UNAVAILABLE');
  assert.equal(r.open, null);
});

test('open positions with floor/xavier not read yet report the read state', () => {
  const r = model({open_positions: {count: 1, rows: [{group_id: 'g1'}]}}, null).managementOpen();
  assert.equal(r.status, 'LOADING');
  assert.equal(r.open, 1);
});

test('hq.js draws the tile from managementOpen, not from the raw timeline', () => {
  const HQJS = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
  const fresh = HQJS.slice(HQJS.indexOf('function freshness()'), HQJS.indexOf('function freshHTML()'));
  assert.ok(fresh.includes('HQ.managementOpen()'));
  assert.ok(!fresh.includes('HQ.xavierDecisions()'));
  assert.ok(fresh.includes("v: '0 open'"));
});
