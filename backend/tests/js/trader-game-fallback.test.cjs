// TRADER MODE: with no authorized live-score provider, the game panel is the
// unavailable fallback -- never a scoreboard, a zero score or a clock.
//
// Production today serves no score evidence (trader-mode score_feed:
// NO_SCORE_PROVIDER_EVIDENCE_RECORDED; provider authorization is an owner
// action). Two payload shapes reach the page: the Live Game State record
// (schema bettor.live_game.v1, status UNAVAILABLE with a reason) and the
// native normalizer's UNAVAILABLE game (LIVE_SCORE_SOURCE_NOT_CONNECTED /
// SCORE_EVENT_IDENTITY_UNPROVEN). Both must render the fallback with the
// reason, on the card and on the focus desk's game monitor; the device
// acceptance checks the rendered page against the snapshot (GAME_FALLBACK_WRONG,
// FOCUS_GAME_FALLBACK_WRONG); these tests pin the renderers.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '../../../frontend/public/command');
const vm = require('node:vm');
// frontend/package.json is "type":"module": evaluate the browser IIFE in a
// CommonJS sandbox so its module.exports branch runs (as trader-core.test.cjs does)
const S = (() => { const m = { exports: {} }; vm.runInNewContext(fs.readFileSync(path.join(DIR, 'trader-live-scores.js'), 'utf8'), { module: m, exports: m.exports, URL, Date, Math, Number, String, JSON, Object, Array, Map, Set, console }); return m.exports; })();
const JS = fs.readFileSync(path.join(DIR, 'trader.js'), 'utf8');
const now = 1800000000;
const pos = (game, extra) => Object.assign({ position_id: 'p', venue: 'POLYMARKET_US', event_id: 'ev', title: 'SYNTHETIC TEST', state: 'ACTIVE', game }, extra || {});

test('a Live Game State UNAVAILABLE record renders the fallback with its reason, no score, no clock', () => {
  for (const why of ['NO_SCORE_PROVIDER_EVIDENCE_RECORDED', 'PROVIDER_NOT_AUTHORIZED', 'SCORE_EVIDENCE_READ_FAILED:TimeoutError']) {
    const h = S.render(pos({ schema: S.SCHEMA, status: 'UNAVAILABLE', why, venue: 'POLYMARKET_US', event_id: 'ev', identity_verified: false }), false, now);
    assert.match(h, /class="score-unavailable live-score-unavailable"/);
    assert.match(h, /Live score unavailable/);
    assert.ok(h.includes(why));
    assert.doesNotMatch(h, /team-score|scoreboard|live-score-clock|data-live-score-number/);
  }
});

test('the native UNAVAILABLE game goes to the page renderer, which names the reason and draws no score', () => {
  assert.equal(S.render(pos({ status: 'UNAVAILABLE', why: 'LIVE_SCORE_SOURCE_NOT_CONNECTED' }), false, now), null);
  const g = JS.slice(JS.indexOf('function gameHTML('), JS.indexOf('function metrics('));
  assert.ok(g.includes("if(!['CURRENT','STALE'].includes(g.status))return`<div class=\"score-unavailable\">"));
  assert.ok(g.includes("'Live score feed unavailable'"));
  assert.ok(g.includes("g.why==='SCORE_EVENT_IDENTITY_UNPROVEN'?'Score identity not verified'"));
  assert.ok(g.includes("p.state==='SETTLEMENT_PENDING'?'Awaiting venue settlement'"));
  // the unavailable branch returns before any scoreboard markup
  const branch = g.slice(g.indexOf("if(!['CURRENT','STALE']"), g.indexOf('const fresh='));
  assert.doesNotMatch(branch, /team-score|scoreboard|game-clock/);
});

test('the focus desk game monitor uses the same renderer', () => {
  assert.ok(JS.includes("$('focus-game').innerHTML=`<div class=\"screen-eyebrow\"><span>${E(sport(p))} GAME CENTER</span><span class=\"screen-live\">${preview?'REPLAY':'SOURCE'}</span></div>${gameHTML(p,true)}`;"));
});
