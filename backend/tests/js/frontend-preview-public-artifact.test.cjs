// FRONTEND PREVIEW HARNESS: nothing the paper book holds reaches the public
// artifact (.github/frontend-preview/public-artifact.js, shots.js,
// trader_accept.js).
//
// frontend-preview.yml uploads preview.json and trader_accept.json in the
// clear from a public repository, and pm-acceptance copies them into the
// evidence packet; only the screenshots are encrypted. Review finding on the
// lane's room view: the page reads /api/command/positions/room/<key>
// (position.js) and shots.js counted every API response by its path with only
// hex segments masked, so a room key (<BOOK_CODE>:<EVT|MKT>:<event slug>,
// position_rooms.group_key) was written as an api_by_path_status key; the
// verdict's "got <navs>" note printed /position?g=<key>. The RC5 production
// device run (pm-acceptance 37836393458) also wrote a focused Trader card's
// market slug as current_workspace text, and trader_accept.js names a failing
// position by its id (paperpos:<account>:<group>:<market slug>:<side>).
// These tests feed synthetic keys, slugs and ids through the code that writes
// the artifact and assert none of them survives, in any spelling, while every
// count, verdict and failure class stays exactly as measured.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const DIR = path.join(__dirname, '../../../.github/frontend-preview');
const PA = require(path.join(DIR, 'public-artifact.js'));
const SHOTS = fs.readFileSync(path.join(DIR, 'shots.js'), 'utf8');
const TRADER = fs.readFileSync(path.join(DIR, 'trader_accept.js'), 'utf8');
const block = (src, name) => src.slice(src.indexOf('// >>> ' + name), src.indexOf('// <<< ' + name));
const CONTROLS = vm.runInNewContext('(() => {' + block(SHOTS, 'CONTROLS') + '; return { CONTROLS, verdict }; })()',
  { AGENTS: ['derek', 'xavier', 'audrey', 'karen', 'allocator', 'archer', 'scout'], Object, JSON, Array, String, Math, window: {} });
const FID = vm.runInNewContext('(() => {' + block(TRADER, 'TRADER FIDELITY') + '; return { compareCards }; })()',
  { Intl, Number, Math, Date, String, JSON, Object, Array, Set, Map });

// synthetic stand-ins with the production shapes
const SLUG = 'tst-leakcheck-event-2026-10-09';
// position_rooms.BOOK_CODES / position.js KEY_RE: PAPER, ACTUAL-POLYMARKET, ACTUAL-KALSHI
const KEY = 'PAPER:EVT:' + SLUG;
const KEY_MKT = 'ACTUAL-KALSHI:MKT:tst-leakcheck-market-2026-10-09-yes';
const ENC = encodeURIComponent(KEY);
const sha12 = (s) => crypto.createHash('sha256').update(s).digest('hex').slice(0, 12);
const P1 = 'paperpos:paper_leakcheck_acct:grp-leak-0001:tst-leakcheck-market-1:LONG';
const P2 = 'paperpos:paper_leakcheck_acct:grp-leak-0002:tst-leakcheck-market-2:SHORT';
const ORDER = 'paperord:leakcheck-order-0001';
const REVIEW = 'xrev_leakcheck_0001_abcdef1';
// every spelling that must never be written
// an event title is a phrase without digits or punctuation (a real one: 'Away Team at Home Team')
const TITLE = 'Leakcheck Away at Leakcheck Home';
const FORBIDDEN = [KEY, ENC, ENC.toLowerCase(), SLUG, KEY_MKT, encodeURIComponent(KEY_MKT), 'tst-leakcheck-market', 'EVT%3A', 'MKT%3A', 'EVT:', 'MKT:',
  'paperpos', 'paper_leakcheck_acct', 'grp-leak-000', ORDER, 'leakcheck-order', REVIEW, 'xrev_leakcheck', TITLE, encodeURIComponent(TITLE)];
const clean = (x) => { const s = JSON.stringify(x); return FORBIDDEN.filter((f) => s.includes(f)); };

const ROOMS = { venues: { POLYMARKET_US: { rooms: [{ group_key: KEY, kind: 'EVT', legs: [{ slug: 'tst-leakcheck-market-1', group_id: 'grp-leak-0001' }] }, { group_key: KEY_MKT, kind: 'MKT' }] } } };
const SNAP = { positions: [
  { position_id: P1, account_id: 'paper_leakcheck_acct', group_id: 'grp-leak-0001', us_market_slug: 'tst-leakcheck-market-1', event_id: 'tst-leakcheck-event-1', title: TITLE, holding_side: 'LONG', qty: 100,
    entry_price: 0.4, entry_agent: 'DEREK', management_agent: 'XAVIER', state: 'ACTIVE', unrealized_usd: 6, quote: { bid: 0.46, ask: 0.47, at: 1800000000 - 20, current: true, observation_id: 1001 },
    orders: [{ position_id: P1, order_id: ORDER, role: 'STANDING_PROTECTION', state: 'EXPIRED', direction: 'SELL', limit_price: 0.55, qty: 100, remaining_qty: 100, expires_at: 1800000000 - 600 }],
    packet: { complete: true, missing: [], probability_at: 1800000000 - 5, probability_current: true, current_recommendation: 'HOLD' },
    review: { review_id: REVIEW, reviewed_at: 1800000000 - 41, recommendation: 'HOLD' }, game: { status: 'UNAVAILABLE', why: 'LIVE_SCORE_SOURCE_NOT_CONNECTED' } },
  { position_id: P2, account_id: 'paper_leakcheck_acct', group_id: 'grp-leak-0002', us_market_slug: 'tst-leakcheck-market-2', holding_side: 'SHORT', qty: 50, entry_price: 0.62,
    entry_agent: 'DEREK', management_agent: 'XAVIER', state: 'ACTIVE', unrealized_usd: -13.5, quote: { bid: 0.35, ask: 0.37, at: 1800000000 - 30, current: true }, orders: [],
    packet: { complete: false, missing: ['NO_FRESH_PROBABILITY'] }, game: { status: 'UNAVAILABLE', why: 'LIVE_SCORE_SOURCE_NOT_CONNECTED' } },
] };

test('the review finding: a room read is counted with its key masked', () => {
  for (const k of [KEY, KEY_MKT]) {
    for (const p of ['/api/command/positions/room/' + encodeURIComponent(k), '/api/command/positions/room/' + k, '/api/command/positions/room/' + encodeURIComponent(k).toLowerCase()]) {
      const got = PA.apiPathKey(p) + ' 200';
      assert.equal(got, '/api/command/positions/room/:key 200');
      assert.deepEqual(clean(got), []);
    }
  }
  // the hex / uuid mask is unchanged; a plain path is counted as it is
  assert.equal(PA.apiPathKey('/api/command/agents/derek/persona/0123abcd-4567'), '/api/command/agents/derek/persona/:id');
  assert.equal(PA.apiPathKey('/api/command/floor/xavier'), '/api/command/floor/xavier');
  assert.equal(PA.apiPathKey('/api/command/positions/rooms'), '/api/command/positions/rooms');
  // the harness counts every response through it, and through nothing else
  assert.ok(SHOTS.includes("const k = PA.apiPathKey(u.pathname) + ' ' + r.status(); api[k] = (api[k] || 0) + 1;"));
  assert.ok(!/u\.pathname\.replace\(/.test(SHOTS));
});

test('the review finding: a navigation to a room is compared and named with its key hashed', () => {
  const navs = PA.navPaths(['http://127.0.0.1:8787/position?g=' + ENC]);
  assert.deepEqual(navs, ['/position?g=sha256:' + sha12(KEY)]);
  // the same prefix the record carries as room_key_sha256_12
  assert.ok(SHOTS.includes("key_sha256_12: got.key ? crypto.createHash('sha256').update(got.key).digest('hex').slice(0, 12) : null"));
  // a control that lands elsewhere names the destination without the key
  const why = CONTROLS.verdict({ nav: '/positions' }, {}, navs, []);
  assert.equal(why.length, 1);
  assert.match(why[0], /^NO_NAVIGATION_TO:\/positions got \/position\?g=sha256:[0-9a-f]{12}$/);
  assert.deepEqual(clean(why), []);
  // and a control that lands where expected still passes on the public paths
  assert.deepEqual(JSON.parse(JSON.stringify(CONTROLS.verdict({ nav: '/position' }, {}, navs, []))), []);
  assert.deepEqual(PA.navPaths(['http://127.0.0.1:8787/positions?book=ACTUAL']), ['/positions?book=ACTUAL']);
  assert.deepEqual(JSON.parse(JSON.stringify(CONTROLS.verdict({ nav: '/positions?book=ACTUAL' }, {}, PA.navPaths(['http://127.0.0.1:8787/positions?book=ACTUAL']), []))), []);
  assert.ok(SHOTS.includes('why = verdict(expect, obs, PA.navPaths(capture.navs), capture.requests);'));
  // the page left outside a tap is named the same way
  assert.equal(PA.publicPath('/position?g=' + ENC), '/position?g=sha256:' + sha12(KEY));
  assert.ok(SHOTS.includes("why: ['PAGE_LEFT_TO ' + PA.publicPath(here)]"));
});

test('no declared navigation expects a private value: the verdict on public paths is the verdict on raw ones', () => {
  const vps = [{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 844, height: 390 }, { width: 820, height: 1180 }, { width: 1180, height: 820 }];
  let seen = 0;
  for (const [pg, list] of Object.entries(CONTROLS.CONTROLS)) for (const c of list) for (const vp of vps) {
    const e = typeof c.expect === 'function' ? c.expect(vp) : c.expect;
    for (const w of [].concat((e && e.nav) || [])) {
      seen++;
      if (!w.includes('?')) continue;
      for (const kv of w.split('?')[1].split('&')) assert.ok(PA.PUBLIC_PARAMS.includes(kv.split('=')[0]), pg + ' ' + c.name + ' ' + w);
      assert.equal(PA.publicPath(w), w);
    }
  }
  assert.ok(seen > 100);
});

test('every record is scrubbed of every identifier the harness read, in every spelling', () => {
  const ids = PA.collectIdentifiers([ROOMS, SNAP]);
  PA.roomIdentifiers(KEY, ids);
  const S = PA.compile(ids);
  // a record as the reviewed harness wrote it, with every channel a key reached
  const raw = {
    view: 'iphone_room', device: 'iphone', page: 'room', status: 200, signed_in: true, overflow_px: 0, console_errors: 2, touch_targets_under_44: 1,
    first_errors: ['goto: page.goto: Timeout 60000ms exceeded.\nCall log:\n  - navigating to "http://127.0.0.1:8787/position?g=' + ENC + '", waiting until "load"',
      'room ' + KEY + ' failed for ' + P1],
    api_by_path_status: { ['/api/command/positions/room/' + ENC + ' 200']: 3, '/api/command/release 200': 1 },
    current_workspace: [SLUG], touch_targets_under_44_first: [{ el: 'a.pr-card', w: 40, h: 40, where: 'page' }],
    controls: { declared: 3, checked: 3, passed: 2, failed: [{ name: 'open a room', why: ['NO_NAVIGATION_TO:/positions got /position?g=' + ENC] }] },
  };
  const out = PA.scrubDeep(raw, S);
  assert.deepEqual(clean(out), []);
  // nothing measured moves: counts, verdict inputs and names are as they were
  for (const k of ['view', 'device', 'page', 'status', 'signed_in', 'overflow_px', 'console_errors', 'touch_targets_under_44']) assert.equal(out[k], raw[k]);
  assert.deepEqual(out.controls.declared + out.controls.checked + out.controls.passed, 8);
  assert.equal(out.controls.failed[0].name, 'open a room');
  assert.equal(out.api_by_path_status['/api/command/release 200'], 1);
  assert.equal(Object.values(out.api_by_path_status).reduce((a, b) => a + b, 0), 4);
  // the same value always reads the same, and a second pass changes nothing
  assert.deepEqual(PA.scrubDeep(out, S), out);
  // shots.js writes every record, measured or UNMEASURED, through the scrub,
  // and reads the identifiers on every run (not only with the room view)
  assert.equal(SHOTS.split('records.push(PA.scrubDeep(').length - 1, 2);
  assert.equal((SHOTS.match(/records\.push\(/g) || []).length, 2);
  assert.ok(SHOTS.includes('const room = await findRoom(browser);\n  const SECRETS = PA.compile(room.identifiers);'));
  assert.ok(SHOTS.includes("const t = await read('/api/command/paper/trader-mode'); if (t.ok) out.reads.push(t.j);"));
});

test('identifiers: keys, their parts, slugs and ids; never a plain word', () => {
  const ids = PA.collectIdentifiers([ROOMS, SNAP]);
  for (const x of [KEY, SLUG, KEY_MKT, P1, 'paper_leakcheck_acct', 'grp-leak-0001', 'tst-leakcheck-market-1', ORDER, REVIEW, 'tst-leakcheck-event-1', TITLE]) assert.ok(ids.has(x), x);
  for (const x of ['PAPER', 'ACTUAL-KALSHI', 'EVT', 'MKT', 'LONG', 'SHORT', 'DEREK', 'XAVIER', 'xavier', 'paperpos', '1001']) assert.ok(!ids.has(x), x);
  // a plain word is never replaced: the harness's own vocabulary stays intact
  assert.deepEqual(PA.scrubDeep({ view: 'desktop_xavier', a: '/xavier' }, ['xavier', 'desktop']), { view: 'desktop_xavier', a: '/xavier' });
  // a query value is hashed wherever it appears, a public one is kept
  assert.equal(PA.scrubText('x /position?g=abc&book=PAPER y', []), 'x /position?g=sha256:' + sha12('abc') + '&book=PAPER y');
});

test('PUBLIC LABEL: a control is named by its structure, never its text', () => {
  const L = vm.runInNewContext('(() => {' + block(SHOTS, 'PUBLIC LABEL') + '; return { labelOf }; })()', { location: { href: 'http://127.0.0.1:8787/trader' }, URL, String });
  const el = (tagName, o) => Object.assign({ tagName, id: '', className: '', attrs: {}, textContent: '', hasAttribute(a) { return a in this.attrs; }, getAttribute(a) { return a in this.attrs ? this.attrs[a] : null; } }, o);
  // the RC5 leak: the focused Trader ribbon item (aria-current) reads the market title
  const ribbon = el('BUTTON', { className: 'bt-v4-ribbon-item', attrs: { 'data-v4-ribbon': P1, 'aria-current': 'true' }, textContent: SLUG });
  assert.equal(L.labelOf(ribbon), 'button.bt-v4-ribbon-item');
  // a room card: the link's path, never its search or its text
  const card = el('A', { className: 'pr-card', attrs: { href: '/position?g=' + ENC }, textContent: 'Fixture Away at Fixture Home ' + SLUG });
  assert.equal(L.labelOf(card), 'a.pr-card[path=/position]');
  assert.equal(L.labelOf(el('A', { attrs: { href: '/api/command/positions/room/' + ENC } })), 'a[path=/api/command/positions/room/:key]');
  // the page's UI enums name a control; anything else is not read
  assert.equal(L.labelOf(el('BUTTON', { id: 'x', className: 'hq5-view on extra', attrs: { 'data-v': 'map', role: 'tab' } })), '#x.hq5-view.on[data-v=map][role=tab]');
  assert.deepEqual(clean([ribbon, card].map(L.labelOf)), []);
  // every list preview.json writes names its boxes this way; none carries text
  for (const line of ["small.push({ el: labelOf(el), w: Math.round(r.width), h: Math.round(r.height), where });",
    "unreachable.push({ el: labelOf(el), by: h ? idOf(h) : null,",
    "boxes.push({ id: (bar ? idOf(bar) + ' > ' : '') + labelOf(el), r: part.v, fixed: !!bar });",
    "const cur = [...document.querySelectorAll('[aria-current]')].filter(vis).map(labelOf);"]) assert.ok(SHOTS.includes(line), line);
  assert.ok(!/(small|unreachable)\.push\(\{ t:/.test(SHOTS));
});

test('Trader: a position, an order and a tape event are named by hash; verdict, failures and counts as measured', () => {
  const NOW = 1800000000, nows = [NOW, NOW - 1, NOW - 2, NOW - 3];
  // a page that draws P2 wrong, misses P1 and draws a card the API does not hold
  const cards = [{ id: P2, bid: '34.0¢', bid_label: 'Current bid', ask_line: 'Ask 37.0¢', holding: 'DEREK → XAVIER · 50.00 SHORT · ENTRY 62.0¢', target: '—', target_label: 'Recorded target',
    pnl: '−$13.50', strip: 'No standing order or proposed target recorded', pill: '1 evidence gap', ages: 'BOOK 30s · P unavailable', scoreboard: false, unavailable: true,
    unavailable_text: 'Live score feed unavailable', score_numbers: [], clocks: [] },
  { id: 'paperpos:paper_leakcheck_acct:grp-leak-0009:tst-leakcheck-market-9:LONG', bid: '—', score_numbers: [], clocks: [] }];
  const fidelity = FID.compareCards(SNAP, { cards }, nows);
  assert.ok(fidelity.missing_cards.length === 1 && fidelity.extra_cards.length === 1 && fidelity.bid_mismatch.length === 1);
  const rec = { device: 'iphone', verdict: 'FAIL', failures: ['POSITION_SET_DIFFERS', 'BID_DIFFERS'], api: { returned: 2, total_position_count: 2 }, fidelity,
    moved_while_frozen: [{ id: P2, before: 'a', after: 'b' }], focus: { checked: 1, issues: [{ id: P1, issue: 'REVIEW_TIME_DIFFERS' }] },
    tape: { events: 3, reviews_in_api: 1, reviews_shown: 1, unrecorded: [{ ref: REVIEW.slice(-10), head: 'Xavier · HOLD' }, { ref: '1001', head: 'Bid 45.0¢ → 46.0¢' }, { ref: 'zz', head: 'tst-leakcheck-market-1 surged' }] },
    expired: [{ id: P1, order: ORDER.slice(-10), state: 'EXPIRED' }],
    outage: { polls_refused: 3, claims: ['CURRENT_BID:' + PA.ref(P2)] }, first_errors: ['card ' + P1 + ' at ' + ORDER, 'GET /x?title=' + encodeURIComponent(TITLE)],
    // the game panel's fallback prints the position's title (trader.js / trader-live-scores.js)
    game: [{ id: P1, scoreboard: true, unavailable: 'MLB · GAME STATE ' + TITLE + ' Live score feed unavailable', want: { scoreboard: false } }] };
  const ids = PA.collectIdentifiers(SNAP);
  const out = PA.publicTraderResult(rec, PA.compile(ids));
  assert.deepEqual(clean(out), []);
  // the extra card the API never carried is hashed too (it is in no snapshot)
  assert.ok(!JSON.stringify(out).includes('grp-leak-0009') && !JSON.stringify(out).includes('tst-leakcheck-market-9'));
  assert.equal(out.fidelity.missing_cards[0], 'sha256:' + sha12(P1));
  assert.equal(out.fidelity.bid_mismatch[0].id, 'sha256:' + sha12(P2));
  assert.equal(out.fidelity.bid_mismatch[0].dom, '34.0¢');
  assert.equal(out.expired[0].order, 'sha256:' + sha12(ORDER.slice(-10)));
  // the page's two tape shapes carry no identifier and are kept; anything else is hashed
  assert.deepEqual(out.tape.unrecorded.map((e) => e.head), ['Xavier · HOLD', 'Bid 45.0¢ → 46.0¢', 'sha256:' + sha12('tst-leakcheck-market-1 surged')]);
  // what the scorecard reads is untouched
  assert.equal(out.verdict, 'FAIL');
  assert.deepEqual(out.failures, rec.failures);
  assert.deepEqual(out.api, rec.api);
  assert.equal(out.tape.events, 3);
  assert.equal(out.outage.polls_refused, 3);
  assert.match(out.game[0].unavailable, /^MLB · GAME STATE redacted:[0-9a-f]{12} Live score feed unavailable$/);
  // trader_accept.js writes only the public form, with the identifiers of every snapshot it read
  assert.ok(TRADER.includes('const published = results.map(r => PA.publicTraderResult(r, SECRETS));'));
  assert.ok(TRADER.includes('redaction: { identifiers_known: identifiers.size }, results: published }, null, 1));'));
  assert.ok(TRADER.includes('if (j && Array.isArray(j.positions)) { S = j; live++; PA.collectIdentifiers(j, identifiers);'));
  assert.ok(!TRADER.includes('c.id.slice(-12)'));
  assert.equal((TRADER.match(/writeFileSync\(/g) || []).length, 1);
});
