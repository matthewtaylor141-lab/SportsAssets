// HQ management-epoch KPIs (frontend/public/command/hq.js mgKpis): the epoch's
// equity and cash are RE-BASED management figures, so they are labelled as
// such, the ledger's own available cash is shown beside them, and the served
// reconciliation.differences_to_ledger is drawn as a bridge under Current
// equity. Every figure is an API field as served; nothing is summed here.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
const from = SRC.indexOf('function ledgerAvail(m)'), to = SRC.indexOf('function mgHistoryHTML(');
assert.ok(from > 0 && to > from, 'the mgKpis block is where the test expects it');
const BLOCK = SRC.slice(from, to);

function load(paper) {
  const fin = (v) => typeof v === 'number' && isFinite(v);
  const usd = (v, d) => { if (!fin(v)) return null; const s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d}); return (v < 0 ? '−$' : '$') + s; };
  const U = {usd, signedUsd: (v, d) => { const s = usd(v, d); return s == null ? null : (v > 0 ? '+' : '') + s; }, words: (s) => String(s).replace(/_/g, ' ')};
  const ctx = {HQ: {equity: () => ({paper})}, U, fin, esc: (s) => String(s), tone: () => '', pct: (v) => (fin(v) ? v.toFixed(2) + '%' : null), Array, Math, String, Object};
  return vm.runInNewContext('(() => {' + BLOCK + '; return { mgKpis, mgBridgeHTML, ledgerAvail }; })()', ctx);
}
const kv = (k, v, s) => '<div><div class="k">' + k + '</div><div class="v">' + (v != null ? v : 'N/A') + '</div>' + (s ? '<span class="s">' + s + '</span>' : '') + '</div>';
// the shape bettor_paper_epoch serves: equity, cash and ledger totals as in the
// audit's 13:03Z replay; the per-item split below is SYNTHETIC test data
const M = {
  status: 'OK', opening_equity_usd: 500000, equity_usd: 458633.67, cash_usd: 458633.67, reserved_usd: 0,
  exposure: {open_positions: 0, basis_usd: 0}, marked_open_position_value_usd: 0,
  pre_management_history: {ledger_available_usd: 455310.79, ledger_cash_usd: 455310.79},
  reconciliation: {
    management_equity_usd: 458633.67, ledger_equity_usd: 455310.7938, differences_total_usd: -3322.8762, ledger_gap_usd: 0,
    differences_to_ledger: [
      {item: 'LEDGER_FUNDING_MINUS_OPENING_EQUITY', amount_usd: 0},
      {item: 'PRE_EPOCH_REALIZED_RESULT_OF_POSITIONS_FLAT_AT_THE_EPOCH', amount_usd: -3000.5},
      {item: 'PRE_EPOCH_MARK_TO_MARKET_RESULT_OF_CARRIED_POSITIONS', amount_usd: -322.3762},
      {item: 'LEDGER_CASH_NOT_ATTRIBUTED_TO_ANY_POSITION', amount_usd: 0}]}};

test('epoch equity and cash are labelled re-based; the ledger available cash is shown beside them', () => {
  const H = load({available_usd: 1});
  const html = H.mgKpis(M, kv);
  assert.ok(!html.includes('reconciles to the ledger'), 'no claim that the re-based equity is the ledger');
  assert.ok(!/>Cash<\/div><div class="v">\$458,634<\/div><span class="s">available/.test(html), 'epoch cash is not called available');
  assert.ok(html.includes('Current equity (re-based)'));
  assert.ok(html.includes('Cash (re-based)'));
  assert.ok(html.includes('Ledger cash available</div><div class="v">$455,310.79'), 'ledger available from pre_management_history');
});

test('the served differences_to_ledger render as a bridge directly under Current equity', () => {
  const html = load(null).mgKpis(M, kv);
  const ce = html.indexOf('Current equity (re-based)'), br = html.indexOf('Bridge to the ledger'), rz = html.indexOf('Realized P&amp;L') >= 0 ? html.indexOf('Realized P&amp;L') : html.indexOf('Realized P&L');
  assert.ok(ce >= 0 && br > ce && rz > br, 'bridge sits between Current equity and the next KPI');
  assert.ok(html.includes('management equity (re-based)</span><b class="num">$458,633.67'));
  assert.ok(html.includes('pre-epoch realized (flat at the epoch)</span><b class="num">−$3,000.50'));
  assert.ok(html.includes('pre-epoch result of carried positions</span><b class="num">−$322.38'));
  assert.ok(html.includes('= ledger equity</span><b class="num">$455,310.79'));
  assert.ok(!html.includes('bridge gap'), 'no gap line when the served gap is zero');
});

test('falls back to equity/live paper.available_usd; absent everywhere it is N/A, never invented', () => {
  const noHist = Object.assign({}, M, {pre_management_history: {}});
  assert.equal(load({available_usd: 455310.79}).ledgerAvail(noHist), 455310.79);
  assert.equal(load(null).ledgerAvail(noHist), null);
  assert.ok(load(null).mgKpis(noHist, kv).includes('Ledger cash available</div><div class="v">N/A'));
});

test('a long bridge is cut with a count and the served total; an absent bridge says so', () => {
  const many = Object.assign({}, M, {reconciliation: Object.assign({}, M.reconciliation, {
    differences_to_ledger: Array.from({length: 11}, (_, i) => ({item: 'POST_EPOCH_CASH_ON_A_POSITION_FLAT_AT_THE_EPOCH', market: 'm' + i, amount_usd: -1}))})});
  const html = load(null).mgBridgeHTML(many);
  assert.equal((html.match(/post-epoch cash, position flat at the epoch/g) || []).length, 8);
  assert.ok(html.includes('+ 3 more items (all differences −$3,322.88)'));
  const none = load(null).mgBridgeHTML(Object.assign({}, M, {reconciliation: null}));
  assert.ok(none.includes('bridge to the ledger not served'));
});
