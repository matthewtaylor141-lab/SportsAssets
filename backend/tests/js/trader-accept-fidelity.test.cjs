// TRADER DEVICE ACCEPTANCE: data on the screen equals the API, order by order
// (.github/frontend-preview/trader_accept.js, block TRADER FIDELITY).
//
// The acceptance compared bid, target, P/L and the order strip. It now also
// compares, for every open position and every order: the ask, the bid label
// (current vs last), the held side / quantity / entry and the agents that
// entered and manage it, the evidence-state pill and the book / probability
// ages, the orders view row by row (direction, role, remaining quantity,
// reference price, limit, state), Xavier's recorded recommendation and review
// id / time on the focus desk, the decision tape (only recorded reviews and
// observed quote moves), the brain rail's healthy lights, and the game panel's
// unavailable fallback. Found with it on the production frontend (f90dafdd,
// synthetic snapshot built by the backend's own trader_mode.build_snapshot):
// a RESTING order past its recorded expiry -- which the server's packet
// already refuses as protection (NO_VALID_ACTIVE_PROTECTION) -- was drawn as
// the card's "Standing limit" and "Recorded SELL ... remain"; the review's
// time was nowhere on the page. These tests pin the rules with fixed inputs.
// The block between the TRADER FIDELITY markers is evaluated exactly as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/trader_accept.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> TRADER FIDELITY'), SRC.indexOf('// <<< TRADER FIDELITY'));
const F = vm.runInNewContext('(() => {' + BLOCK + '; return { compareCards, compareOrderRows, compareFocus, compareBrain, compareTape, cardExpect, target, pastExpiry, gameExpect }; })()',
  { Intl, Number, Math, Date, String, JSON, Object, Array, Set, Map });
const plain = (x) => JSON.parse(JSON.stringify(x));

const NOW = 1800000000;
const nows = [NOW, NOW - 1, NOW - 2, NOW - 3];
const P1 = 'paperpos:paper_test_fixture:g1:test-fixture-market-1:LONG';
const P2 = 'paperpos:paper_test_fixture:g2:test-fixture-market-2:SHORT';
function snapshot() {
  return { schema: 'bettor.trader.v1', mode: 'PAPER', snapshot_at: NOW, total_position_count: 2, positions: [
    { position_id: P1, venue: 'POLYMARKET_US', event_id: 'ev1', holding_side: 'LONG', qty: 100, entry_price: 0.40, entry_agent: 'DEREK', management_agent: 'XAVIER',
      state: 'ACTIVE', unrealized_usd: 6, quote: { bid: 0.46, ask: 0.47, at: NOW - 20, current: true, observation_id: 1001 },
      orders: [{ position_id: P1, order_id: 'paperord:fixture1', role: 'STANDING_PROTECTION', state: 'RESTING', direction: 'SELL', limit_price: 0.41, qty: 100, remaining_qty: 100, expires_at: NOW + 3600 },
               { position_id: P1, order_id: 'paperord:fixture2', role: 'STANDING_PROTECTION', state: 'EXPIRED', direction: 'SELL', limit_price: 0.55, qty: 100, remaining_qty: 100, expires_at: NOW - 600 }],
      packet: { complete: true, missing: [], probability_at: NOW - 5, probability_current: true, current_recommendation: 'HOLD' },
      review: { review_id: 'xrev_fixture_0001_abcdef1', reviewed_at: NOW - 41, recommendation: 'HOLD' }, proposal: null,
      game: { schema: 'bettor.live_game.v1', status: 'UNAVAILABLE', why: 'NO_SCORE_PROVIDER_EVIDENCE_RECORDED', venue: 'POLYMARKET_US', event_id: 'ev1', identity_verified: false } },
    { position_id: P2, venue: 'POLYMARKET_US', event_id: 'ev2', holding_side: 'SHORT', qty: 50, entry_price: 0.62, entry_agent: 'DEREK', management_agent: 'XAVIER',
      state: 'ACTIVE', unrealized_usd: -13.5, quote: { bid: 0.35, ask: 0.37, at: NOW - 30, current: true, observation_id: 1002 },
      orders: [{ position_id: P2, order_id: 'paperord:fixture3', role: 'STANDING_PROTECTION', state: 'RESTING', direction: 'SELL', limit_price: 0.30, qty: 50, remaining_qty: 50, expires_at: NOW - 120 },
               { position_id: P2, order_id: 'paperord:fixture4', role: 'STANDING_PROTECTION', state: 'REJECTED', direction: 'SELL', limit_price: 0.33, qty: 50, remaining_qty: 50, expires_at: NOW + 3600 }],
      packet: { complete: false, missing: ['NO_FRESH_PROBABILITY', 'NO_VALID_ACTIVE_PROTECTION'], probability_at: NOW - 400, probability_current: false, current_recommendation: null },
      review: { review_id: 'xrev_fixture_0002_abcdef2', reviewed_at: NOW - 42, recommendation: 'EXIT' }, proposal: null,
      game: { status: 'UNAVAILABLE', why: 'LIVE_SCORE_SOURCE_NOT_CONNECTED' } },
  ] };
}
// the cards as the page now draws them
function cardsFixed() {
  return [
    { id: P1, bid: '46.0¢', bid_label: 'Current bid', ask_line: 'Ask 47.0¢', bid_visible: true, holding: 'DEREK → XAVIER · 100.00 LONG · ENTRY 40.0¢',
      target_label: 'Standing limit', target: '41.0¢', pnl: '+$6.00', strip: 'Recorded SELL 41.0¢100.00 remain', pill: 'Packet complete', ages: 'BOOK 20s · P 5s',
      scoreboard: false, unavailable: true, unavailable_text: 'Fixture Live score unavailable NO_SCORE_PROVIDER_EVIDENCE_RECORDED Market and position evidence remain separate.', score_numbers: [], clocks: [] },
    { id: P2, bid: '35.0¢', bid_label: 'Current bid', ask_line: 'Ask 37.0¢', bid_visible: true, holding: 'DEREK → XAVIER · 50.00 SHORT · ENTRY 62.0¢',
      target_label: 'Recorded target', target: '—', pnl: '−$13.50', strip: 'Recorded SELL 30.0¢PAST EXPIRY · NOT PROTECTION', pill: '2 evidence gaps', ages: 'BOOK 30s · P 6m',
      scoreboard: false, unavailable: true, unavailable_text: 'SPORT · GAME STATE Fixture Live score feed unavailable', score_numbers: [], clocks: [] },
  ];
}
const dom = (cards) => ({ cards });
const issues = (out) => Object.entries(out).filter(([k, v]) => Array.isArray(v) && v.length).map(([k]) => k).sort();

test('the block under test is the acceptance code and each rule is a named failure', () => {
  for (const f of ['ASK_DIFFERS', 'BID_LABEL_DIFFERS', 'HELD_SIDE_DIFFERS', 'EVIDENCE_STATE_DIFFERS', 'AGE_DIFFERS', 'EXPIRED_ORDER_SHOWN_AS_PROTECTION', 'GAME_FALLBACK_WRONG',
    'ACTIVITY_WITHOUT_DATA', 'TAPE_EVENT_NOT_RECORDED', 'REVIEW_NOT_ON_TAPE', 'HEALTHY_INDICATOR_WITHOUT_EVIDENCE', 'ORDER_ROWS_DIFFER', 'LAPSED_ORDER_LABEL_DIFFERS',
    'OUTAGE_NOT_DISCLOSED', 'HEALTHY_INDICATOR_DURING_OUTAGE'])
    assert.ok(SRC.includes("fails.push('" + f + "')"), f);
  // the earlier checks are all still there
  for (const f of ['NOT_THE_NATIVE_READBACK', 'POSITION_SET_DIFFERS', 'WALL_COUNT_DIFFERS', 'ORDER_COUNT_DIFFERS', 'BID_DIFFERS', 'TARGET_DIFFERS', 'PNL_DIFFERS', 'ORDER_STRIP_DIFFERS',
    'SCORE_WITHOUT_CURRENT_GAME', 'SCORE_DIFFERS', 'MOVED_WITHOUT_DATA', 'ESTIMATED_CLOCK_SHOWN', 'IMAGE_FROM_UNLISTED_HOST', 'FOCUS_BID_DIFFERS', 'RECOMMENDATION_NOT_RECORDED',
    'BLOCKED_WITHOUT_REASON', 'ORDERS_VIEW_DIFFERS', 'HORIZONTAL_OVERFLOW', 'ASK_NOT_SHOWN', 'FROZEN_PHASE_NOT_REACHED'])
    assert.ok(SRC.includes("fails.push('" + f + "')"), f);
});

test('the page as it now draws: every card field equals the snapshot', () => {
  const out = F.compareCards(snapshot(), dom(cardsFixed()), nows);
  assert.deepEqual(issues(out), []);
  assert.equal(out.ask_shown, 2);
  assert.equal(out.ask_in_api, 2);
});

test('the production defect: an order past its expiry drawn as the standing limit', () => {
  // f90dafdd drew the lapsed RESTING order of P2 as protection
  const base = cardsFixed();
  base[1].target_label = 'Standing limit'; base[1].target = '30.0¢'; base[1].strip = 'Recorded SELL 30.0¢50.00 remain';
  const out = F.compareCards(snapshot(), dom(base), nows);
  assert.deepEqual(issues(out), ['expired_shown_as_protection', 'strip_mismatch', 'target_mismatch']);
  assert.equal(out.expired_shown_as_protection[0].state, 'RESTING');
  // the restated rule: a standing order is protection only before its recorded expiry
  const S = snapshot();
  assert.equal(F.target(S.positions[1], NOW), null);
  assert.equal(F.target(S.positions[0], NOW).order_id, 'paperord:fixture1');
  assert.equal(F.pastExpiry(S.positions[1].orders[0], NOW), true);
  assert.equal(F.pastExpiry({ expires_at: null }, NOW), false);
});

test('a REJECTED / EXPIRED order is never the standing limit either', () => {
  const S = snapshot();
  S.positions[1].orders = [S.positions[1].orders[1]];   // only the REJECTED order
  const base = cardsFixed();
  base[1].target_label = 'Standing limit'; base[1].target = '33.0¢'; base[1].strip = 'Recorded SELL 33.0¢50.00 remain';
  assert.ok(F.compareCards(S, dom(base), nows).expired_shown_as_protection.length === 1);
});

test('ask, held side, bid label, evidence pill and ages are compared exactly', () => {
  const S = snapshot();
  const c = () => cardsFixed();
  let x = c(); x[0].ask_line = 'Ask 46.0¢'; assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['ask_mismatch']);
  x = c(); x[1].holding = 'DEREK → XAVIER · 50.00 LONG · ENTRY 62.0¢'; assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['held_side_mismatch']);
  // a hard-coded agent pair is a fabrication when the record names another
  const S2 = snapshot(); S2.positions[0].entry_agent = 'ARCHER';
  assert.deepEqual(issues(F.compareCards(S2, dom(c()), nows)), ['held_side_mismatch']);
  x = c(); x[1].bid_label = 'Last bid'; assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['bid_label_mismatch']);
  x = c(); x[1].pill = 'Packet complete'; assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['evidence_state_mismatch']);
  x = c(); x[0].ages = 'BOOK 2s · P 5s'; assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['age_mismatch']);
  // a book that aged past 300 s reads "Last bid" and adds an evidence gap
  const S3 = snapshot(); S3.positions[0].quote.at = NOW - 400;
  x = c(); x[0].bid_label = 'Last bid'; x[0].pill = '1 evidence gap'; x[0].ages = 'BOOK 6m · P 5s';
  assert.deepEqual(issues(F.compareCards(S3, dom(x), nows)), []);
});

test('an unavailable game shows the fallback with the API reason, never a scoreboard', () => {
  const S = snapshot();
  let x = cardsFixed(); x[0].scoreboard = true; x[0].unavailable = false;
  assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['game_fallback_wrong']);
  x = cardsFixed(); x[0].unavailable_text = 'Fixture Live score unavailable';
  assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['game_fallback_wrong']);
  x = cardsFixed(); x[1].unavailable_text = 'Score identity not verified';
  assert.deepEqual(issues(F.compareCards(S, dom(x), nows)), ['game_fallback_wrong']);
  assert.deepEqual(plain(F.gameExpect(S.positions[0])), { scoreboard: false, says: ['Live score unavailable', 'NO_SCORE_PROVIDER_EVIDENCE_RECORDED'] });
  assert.deepEqual(plain(F.gameExpect({ game: { status: 'CURRENT' } })), { scoreboard: true });
  // a score drawn for a game that is not CURRENT is still SCORE_WITHOUT_CURRENT_GAME
  x = cardsFixed(); x[1].score_numbers = ['3', '2'];
  assert.ok(F.compareCards(S, dom(x), nows).score_without_current_game.length === 1);
});

test('the orders view: one row per standing order, lapsed ones labelled, nothing else', () => {
  const S = snapshot();
  const row = (direction, role, remaining, reference, limit, state, lapsed) => ({ direction, role, remaining, reference, limit, state, paper: true, lapsed_label: !!lapsed });
  const fixed = [row('SELL', 'STANDING_PROTECTION', '100.00', '46.0¢', '41.0¢', 'RESTING'), row('SELL', 'STANDING_PROTECTION', '50.00', '35.0¢', '30.0¢', 'RESTING', true)];
  const ok = F.compareOrderRows(S, fixed, nows);
  assert.equal(ok.rows, 2); assert.equal(ok.api_standing, 2); assert.equal(ok.non_standing_in_api, 2);
  assert.ok(ok.row_multiset_equal && ok.reference_multiset_equal && ok.limit_state_multiset_equal && ok.every_row_labelled_paper_simulated);
  assert.equal(ok.lapsed_rows_unlabelled, false); assert.equal(ok.lapsed_label_without_lapse, false);
  // f90dafdd: the lapsed order listed as a live one
  const base = [fixed[0], row('SELL', 'STANDING_PROTECTION', '50.00', '35.0¢', '30.0¢', 'RESTING', false)];
  assert.equal(F.compareOrderRows(S, base, nows).lapsed_rows_unlabelled, true);
  // a rejected order listed, or a wrong remaining quantity
  const extra = fixed.concat([row('SELL', 'STANDING_PROTECTION', '50.00', '35.0¢', '33.0¢', 'REJECTED')]);
  assert.equal(F.compareOrderRows(S, extra, nows).row_multiset_equal, false);
  const qty = [row('SELL', 'STANDING_PROTECTION', '90.00', '46.0¢', '41.0¢', 'RESTING'), fixed[1]];
  assert.equal(F.compareOrderRows(S, qty, nows).row_multiset_equal, false);
  // a valid order wrongly labelled lapsed
  assert.equal(F.compareOrderRows(S, [row('SELL', 'STANDING_PROTECTION', '100.00', '46.0¢', '41.0¢', 'RESTING', true), fixed[1]], nows).lapsed_label_without_lapse, true);
});

test('the focus desk: recorded recommendation, review id and time, held side, lapsed order named', () => {
  const S = snapshot(), p1 = S.positions[0], p2 = S.positions[1];
  const t1 = new Date((NOW - 20) * 1000).toLocaleTimeString('en-GB', { hour12: false });
  const f1 = { bid: '46.0¢', ask_line: 'Ask 47.0¢ · observed ' + t1, observed_want: t1, market_eyebrow: 'CURRENT EXIT BID · POLYMARKET_US LONG · 100 CONTRACTS',
    action: 'HOLD', note: 'SELL limit recorded at 41.0¢. Watching the bid rise 5.0¢.', game_scoreboard: false, game_unavailable: 'Fixture Live score unavailable NO_SCORE_PROVIDER_EVIDENCE_RECORDED', review: 'Xavier review recorded ' + new Date((NOW - 41) * 1000).toISOString().slice(11, 19) + 'Z · …' + 'xrev_fixture_0001_abcdef1'.slice(-10) };
  assert.deepEqual(plain(F.compareFocus(p1, f1, nows)), []);
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { action: 'EXIT' }), nows)), ['RECOMMENDATION_NOT_RECORDED']);
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { review: null }), nows)), ['REVIEW_TIME_DIFFERS']);
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { market_eyebrow: 'CURRENT EXIT BID · POLYMARKET_US SHORT · 100 CONTRACTS' }), nows)), ['FOCUS_HELD_SIDE_DIFFERS']);
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { market_eyebrow: 'LAST OBSERVED BID · POLYMARKET_US LONG · 100 CONTRACTS' }), nows)), ['FOCUS_BOOK_STATE_DIFFERS']);
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { ask_line: 'Ask 47.0¢ · observed 00:00:00' }), nows)), ['FOCUS_ASK_OR_TIME_DIFFERS']);
  // the game monitor: the unavailable fallback with the API reason, never a scoreboard
  assert.deepEqual(plain(F.compareFocus(p1, Object.assign({}, f1, { game_scoreboard: true, game_unavailable: null }), nows)), ['FOCUS_GAME_FALLBACK_WRONG']);
  const t2 = new Date((NOW - 30) * 1000).toLocaleTimeString('en-GB', { hour12: false });
  const f2 = { bid: '35.0¢', ask_line: 'Ask 37.0¢ · observed ' + t2, observed_want: t2, market_eyebrow: 'CURRENT EXIT BID · POLYMARKET_US SHORT · 50 CONTRACTS',
    action: 'Waiting for evidence', note: 'SELL limit at 30.0¢ is past its recorded expiry: not protection. Missing: fresh probability, valid active protection.', game_scoreboard: false, game_unavailable: 'NFL · GAME STATE Fixture Live score feed unavailable',
    review: 'Xavier review recorded ' + new Date((NOW - 42) * 1000).toISOString().slice(11, 19) + 'Z · …' + 'xrev_fixture_0002_abcdef2'.slice(-10) };
  assert.deepEqual(plain(F.compareFocus(p2, f2, nows)), []);
  // a recorded recommendation shown while the packet is incomplete
  assert.deepEqual(plain(F.compareFocus(p2, Object.assign({}, f2, { action: 'EXIT' }), nows)), ['RECOMMENDATION_WITHOUT_COMPLETE_PACKET']);
  assert.deepEqual(plain(F.compareFocus(p2, Object.assign({}, f2, { note: 'Missing: fresh probability.' }), nows)), ['LAPSED_ORDER_NOT_NAMED']);
  assert.deepEqual(plain(F.compareFocus(p2, Object.assign({}, f2, { note: 'SELL limit at 30.0¢ is past its recorded expiry: not protection.' }), nows)), ['BLOCKED_WITHOUT_REASON']);
});

test('the brain rail lights a book, a packet or protection as current only with evidence', () => {
  const S = snapshot(), p1 = S.positions[0], p2 = S.positions[1];
  const rail = (b, x, pr) => [{ label: 'VENUE BOOK', state: b }, { label: 'XAVIER / MANAGEMENT', state: x }, { label: 'PROTECTION', state: pr }];
  assert.deepEqual(plain(F.compareBrain(p1, rail('current', 'current', 'current'), nows)), []);
  assert.deepEqual(plain(F.compareBrain(p2, rail('current', 'blocked', 'blocked'), nows)), []);
  assert.deepEqual(plain(F.compareBrain(p2, rail('current', 'current', 'current'), nows)), ['PACKET_LIT_WITHOUT_EVIDENCE', 'PROTECTION_LIT_WITHOUT_EVIDENCE']);
  const stale = snapshot().positions[0]; stale.quote.at = NOW - 900;
  assert.deepEqual(plain(F.compareBrain(stale, rail('current', 'blocked', 'current'), nows)), ['BOOK_LIT_WITHOUT_CURRENT_QUOTE']);
});

test('the decision tape holds only recorded reviews and observed quote moves', () => {
  const S = snapshot();
  const tfmt = at => new Date(at * 1000).toLocaleTimeString('en-US', { hour12: false });
  const ev = (ref, head, at) => ({ kind: 'review', ref, head, time: tfmt(at), time_want: tfmt(at) });
  const tape = [ev('0001_abcdef1'.slice(-10), 'Xavier · HOLD', NOW - 41), ev('0002_abcdef2'.slice(-10), 'Xavier · EXIT', NOW - 42)];
  let out = F.compareTape(S, tape, []);
  assert.equal(out.reviews_shown, 2); assert.deepEqual(plain(out.unrecorded), []);
  // an invented review, a wrong recommendation, a wrong time
  out = F.compareTape(S, tape.concat([ev('ZZZZZZZZZZ', 'Xavier · EXIT', NOW)]), []);
  assert.equal(out.unrecorded.length, 1);
  out = F.compareTape(S, [Object.assign({}, tape[0], { head: 'Xavier · EXIT' })], []);
  assert.equal(out.unrecorded.length, 1);
  out = F.compareTape(S, [Object.assign({}, tape[0], { time: '00:00:00' })], []);
  assert.equal(out.unrecorded.length, 1);
  // a quote move is shown only when that observation was received
  const q = { kind: 'quote', ref: '1003', head: 'Bid 46.0¢ → 47.0¢' };
  assert.equal(F.compareTape(S, [q], []).unrecorded.length, 1);
  assert.equal(F.compareTape(S, [q], [{ ref: '1003', bid: '47.0¢' }]).unrecorded.length, 0);
  assert.equal(F.compareTape(S, [q], [{ ref: '1003', bid: '48.0¢' }]).unrecorded.length, 1);
});
