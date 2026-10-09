// TRADER DEVICE ACCEPTANCE (read only), for frontend-preview.yml.
//
// The PM's acceptance for Trader Mode is not "the page renders": every open
// position, its offsetting standing orders, real venue quotes, Xavier's
// recorded action, game state only from an authorized source, logos, and the
// phone / tablet layout must be shown as the production API records them,
// with no simulated price and no fabricated operating state. This compares
// the rendered page with the API's own snapshot, position by position and
// order by order, on every device:
//
//   phase LIVE    the page polls /api/command/paper/trader-mode through the
//                 preview proxy (production data, GET only); every response
//                 is kept in memory (never written out) and the last one is
//                 the reference snapshot S
//   phase FROZEN  from then on the page's polls are answered with S itself
//                 (only snapshot_at re-stamped, so the page's 15 s staleness
//                 rule does not fire), so the data cannot change: the DOM is
//                 read twice 10 s apart and any price, P/L, count, score or
//                 decision-tape event that moved was produced by the page,
//                 not by the venue (a simulated quote fails here)
//   phase OUTAGE  the page's polls are refused (503): within seconds it must
//                 say Disconnected and stop claiming anything is current
//                 (no current bid, no marked P/L, no recommendation, no
//                 healthy book / packet light)
//
// Fidelity (DOM vs S; see TRADER FIDELITY for each rule): the card set equals
// the API's position_id set and the wall / order counts equal the API
// totals; each card's bid AND ask, its bid label (current vs last), the held
// side, quantity, entry and the agents that entered / manage it, the target,
// P/L, order strip, evidence-state pill and book / probability ages equal the
// snapshot values in the page's own formats; an order that is not standing
// (EXPIRED, REJECTED, CANCELLED, FILLED ...) or is past its recorded expiry is
// never shown as the standing limit / protection, and every standing order
// is one row of the orders view with its direction, role, remaining
// quantity, reference price, limit and state; the focus desk shows the
// recorded bid / ask / observation time, the held side and quantity, the
// recorded recommendation (never one the API does not hold) or "Waiting for
// evidence" with the missing evidence named, and Xavier's review id and time
// as recorded; the decision tape holds only reviews the API records and quote
// moves actually observed; the brain rail lights a book / packet / protection
// as current only when the snapshot supports it; no score is drawn for a
// position the API carries no current game for, and an unavailable game
// shows the unavailable fallback with its reason (never a scoreboard);
// images come only from the page's host or a named allowlist and load.
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2];
const OUT = process.argv[3];
const API = '/api/command/paper/trader-mode';
const PHONE = { isMobile: true, hasTouch: true };
const DEVICES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  iphone: Object.assign({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 }, PHONE),
  iphone_landscape: Object.assign({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 3 }, PHONE),
  ipad_portrait: Object.assign({ viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2 }, PHONE),
  ipad_landscape: Object.assign({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2 }, PHONE),
};
// hosts an image may come from besides the page's own (team / venue marks in
// the authored V4 package and the Live Game State provider allowlist); any
// other host is a finding
const IMAGE_HOSTS = ['a.espncdn.com', 'polymarket.com'];
const FOCUS_ALL_ON = 'desktop';     // every position's focus desk on desktop
const FOCUS_SAMPLE = 6;             // the first N elsewhere

// >>> TRADER FIDELITY (pinned by backend/tests/js/trader-accept-fidelity.test.cjs)
// The page's own display rules (trader-core.js / trader.js /
// trader-cinematic.js / trader-live-scores.js), restated so the check does
// not run the code it is checking. now is the reading instant (seconds);
// a rule that ages with time is checked at each of the instants the page may
// have last drawn (nows: the read time and the 3 s before it).
const STANDING = ['RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'PENDING_SIMULATION'];
const finite = v => typeof v === 'number' && Number.isFinite(v);
const fmt = (n, d = 0) => finite(n) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }).format(n) : '—';
const cents = p => finite(p) ? `${fmt(p * 100, 1)}¢` : '—';
const money = (v, signed = false) => finite(v) ? `${signed ? (v < 0 ? '−' : v > 0 ? '+' : '') : v < 0 ? '−' : ''}$${fmt(Math.abs(v), 2)}` : '—';
const age = (at, now) => finite(at) && finite(now) ? now - at : null;
const fresh = (at, now, limit) => finite(at) && finite(now) && 0 <= now - at && now - at <= limit;
const duration = s => !finite(s) ? 'unavailable' : s < 0 ? 'clock invalid' : s < 60 ? `${Math.floor(s)}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h`;
const utc = at => finite(at) ? new Date(at * 1000).toISOString().slice(11, 19) + 'Z' : '—';
const standing = p => (p.orders || []).filter(o => STANDING.includes(o.state));
// an order past its recorded expiry is not protection even while the ledger
// still reads RESTING (the server's packet refuses it the same way)
const pastExpiry = (o, now) => !!o && finite(o.expires_at) && finite(now) && o.expires_at <= now;
const target = (p, now) => { const os = standing(p).filter(o => !pastExpiry(o, now)); return os.find(o => o.direction === 'SELL' && ['RESTING', 'PARTIALLY_FILLED'].includes(o.state)) || os.find(o => o.direction === 'SELL') || os[0] || p.proposal || null; };
const gameCurrent = g => !!(g && g.status === 'CURRENT');
const bookCurrent = (q, now) => !!q && q.current === true && fresh(q.at, now, 300);
function missingAt(p, now) {
  const k = p.packet || {}, q = p.quote || {}, m = [...(k.missing || [])];
  if (!fresh(k.probability_at, now, 30) || k.probability_current !== true) m.push('NO_FRESH_PROBABILITY');
  if (!bookCurrent(q, now)) m.push('NO_CURRENT_EXECUTABLE_BOOK');
  return [...new Set(m)];
}
const packetComplete = (p, now) => (p.packet || {}).complete === true && missingAt(p, now).length === 0;
// what one card must read at instant now
function cardExpect(p, now) {
  const q = p.quote || {}, t = target(p, now), n = missingAt(p, now).length;
  const lapsed = standing(p).filter(o => pastExpiry(o, now));
  return {
    bid: cents(q.bid), bid_label: bookCurrent(q, now) ? 'Current bid' : 'Last bid', ask_line: `Ask ${cents(q.ask)}`,
    holding: `${p.entry_agent || 'ENTRY AGENT NOT RECORDED'} → ${p.management_agent || 'MANAGER NOT RECORDED'} · ${fmt(p.qty, 2)} ${p.holding_side} · ENTRY ${cents(p.entry_price)}`,
    target_label: t && t.order_id ? 'Standing limit' : 'Recorded target', target: cents(t && t.limit_price), pnl: money(p.unrealized_usd, true),
    strip: t ? `${t.order_id ? 'Recorded' : 'Proposed'} ${t.direction} ${cents(t.limit_price)}` : lapsed.length ? `Recorded ${lapsed[0].direction} ${cents(lapsed[0].limit_price)}` : 'No standing order or proposed target recorded',
    strip_tail: t ? (t.order_id ? `${fmt(t.remaining_qty, 2)} remain` : 'NOT PLACED') : lapsed.length ? 'PAST EXPIRY · NOT PROTECTION' : null,
    pill: p.state === 'SETTLEMENT_PENDING' ? 'Settlement pending' : packetComplete(p, now) ? 'Packet complete' : `${n} evidence gap${n === 1 ? '' : 's'}`,
    ages: `BOOK ${duration(age(q.at, now))} · P ${duration(age((p.packet || {}).probability_at, now))}`,
    target_order_id: t && t.order_id ? t.order_id : null,
  };
}
const anyNow = (nows, f) => nows.some(f);
// the game panel: a game the API does not serve (UNAVAILABLE, or a Live Game
// State record whose identity is not verified) shows the unavailable
// fallback with the reason the API gives, never a scoreboard; a CURRENT game
// shows its scoreboard; a STALE one is left to SCORE_WITHOUT_CURRENT_GAME
function gameExpect(p) {
  const g = p.game || {};
  if (g.schema === 'bettor.live_game.v1') {
    const served = ['CURRENT', 'STALE'].includes(g.status) && g.identity_verified === true && g.event_id === p.event_id && g.venue === p.venue;
    if (!served) return { scoreboard: false, says: ['Live score unavailable', g.why || 'Score identity not verified'] };
    return g.status === 'CURRENT' ? { scoreboard: true } : { scoreboard: null };
  }
  if (g.status === 'CURRENT') return { scoreboard: true };
  if (g.status === 'STALE') return { scoreboard: null };
  return { scoreboard: false, says: [p.state === 'SETTLEMENT_PENDING' ? 'Awaiting venue settlement' : g.why === 'SCORE_EVENT_IDENTITY_UNPROVEN' ? 'Score identity not verified' : 'Live score feed unavailable'] };
}
function compareCards(S, dom, nows) {
  const byId = new Map(S.positions.map(p => [p.position_id, p]));
  const domIds = new Set(dom.cards.map(c => c.id));
  const out = { missing_cards: [...byId.keys()].filter(id => !domIds.has(id)), extra_cards: [...domIds].filter(id => !byId.has(id)),
    bid_mismatch: [], ask_mismatch: [], bid_label_mismatch: [], held_side_mismatch: [], target_mismatch: [], pnl_mismatch: [], strip_mismatch: [],
    evidence_state_mismatch: [], age_mismatch: [], expired_shown_as_protection: [], score_without_current_game: [], score_mismatch: [], game_fallback_wrong: [],
    bid_hidden: 0, ask_in_api: 0, ask_shown: 0 };
  for (const c of dom.cards) {
    const p = byId.get(c.id); if (!p) continue;
    const q = p.quote || {}, e0 = cardExpect(p, nows[0]);
    if (c.bid !== e0.bid) out.bid_mismatch.push({ id: c.id, dom: c.bid, api: e0.bid });
    if (!c.bid_visible) out.bid_hidden++;
    if (c.ask_line !== e0.ask_line) out.ask_mismatch.push({ id: c.id, dom: c.ask_line, api: e0.ask_line });
    if (finite(q.ask)) { out.ask_in_api++; if (c.ask_line === e0.ask_line) out.ask_shown++; }
    if (!anyNow(nows, n => c.bid_label === cardExpect(p, n).bid_label)) out.bid_label_mismatch.push({ id: c.id, dom: c.bid_label, api: e0.bid_label });
    if (c.holding !== e0.holding) out.held_side_mismatch.push({ id: c.id, dom: c.holding, api: e0.holding });
    if (!anyNow(nows, n => { const e = cardExpect(p, n); return c.target === e.target && c.target_label === e.target_label; })) out.target_mismatch.push({ id: c.id, dom: [c.target_label, c.target], api: [e0.target_label, e0.target] });
    if (c.pnl !== e0.pnl) out.pnl_mismatch.push({ id: c.id, dom: c.pnl, api: e0.pnl });
    if (!anyNow(nows, n => { const e = cardExpect(p, n); return (c.strip || '').includes(e.strip) && (!e.strip_tail || (c.strip || '').includes(e.strip_tail)); }))
      out.strip_mismatch.push({ id: c.id, dom: c.strip, want: [e0.strip, e0.strip_tail] });
    if (!anyNow(nows, n => c.pill === cardExpect(p, n).pill)) out.evidence_state_mismatch.push({ id: c.id, dom: c.pill, api: e0.pill });
    if (!anyNow(nows, n => c.ages === cardExpect(p, n).ages)) out.age_mismatch.push({ id: c.id, dom: c.ages, api: e0.ages });
    // never protection: an order that is not standing, or past its expiry, is
    // not the standing limit the card names
    for (const o of p.orders || []) {
      const dead = !STANDING.includes(o.state) || nows.every(n => pastExpiry(o, n));
      if (dead && c.target_label === 'Standing limit' && c.target === cents(o.limit_price) && !standing(p).some(x => !pastExpiry(x, nows[0]) && cents(x.limit_price) === c.target))
        out.expired_shown_as_protection.push({ id: c.id, order: String(o.order_id || '').slice(-10), state: o.state });
    }
    if (!gameCurrent(p.game) && c.score_numbers.length) out.score_without_current_game.push({ id: c.id, shown: c.score_numbers });
    if (gameCurrent(p.game) && c.score_numbers.length) {
      const want2 = [p.game.away_score, p.game.home_score].filter(finite).map(v => fmt(v));
      if (want2.some(v => !c.score_numbers.includes(v))) out.score_mismatch.push({ id: c.id, shown: c.score_numbers, api: want2 });
    }
    const g = gameExpect(p);
    if (g.scoreboard === true ? !c.scoreboard : g.scoreboard === false && (c.scoreboard || !c.unavailable || g.says.some(s => !(c.unavailable_text || '').includes(s))))
      out.game_fallback_wrong.push({ id: c.id, scoreboard: c.scoreboard, unavailable: c.unavailable_text, want: g });
  }
  return out;
}
// the orders view: exactly the standing orders, one row each
function compareOrderRows(S, rows, nows) {
  const want = S.positions.flatMap(p => standing(p).map(o => ({ p, o })));
  const key = r => [r.direction, r.role, r.remaining, r.limit, r.state].join(' | ');
  const multiset = a => a.reduce((m, x) => (m[x] = (m[x] || 0) + 1, m), {});
  const wantRows = want.map(({ p, o }) => ({ direction: o.direction, role: o.role, remaining: fmt(o.remaining_qty, 2),
    reference: cents(o.direction === 'SELL' ? (p.quote || {}).bid : (p.quote || {}).ask), limit: cents(o.limit_price), state: o.state,
    lapsed: nows.every(n => pastExpiry(o, n)), lapsedAny: nows.some(n => pastExpiry(o, n)) }));
  const a = multiset(wantRows.map(key)), b = multiset(rows.map(key));
  const nonStanding = S.positions.flatMap(p => (p.orders || []).filter(o => !STANDING.includes(o.state)).map(o => o.state));
  return { rows: rows.length, api_standing: want.length, non_standing_in_api: nonStanding.length,
    limit_state_multiset_equal: JSON.stringify(Object.entries(multiset(wantRows.map(r => r.limit + ' ' + r.state))).sort()) === JSON.stringify(Object.entries(multiset(rows.map(r => r.limit + ' ' + r.state))).sort()),
    row_multiset_equal: JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort()),
    reference_multiset_equal: JSON.stringify(Object.entries(multiset(wantRows.map(r => r.reference))).sort()) === JSON.stringify(Object.entries(multiset(rows.map(r => r.reference))).sort()),
    // a lapsed order is listed (the ledger still holds it) and labelled as no protection
    lapsed_rows_unlabelled: rows.filter(r => r.lapsed_label).length < wantRows.filter(r => r.lapsed).length,
    lapsed_label_without_lapse: rows.filter(r => r.lapsed_label).length > wantRows.filter(r => r.lapsedAny).length,
    every_row_labelled_paper_simulated: rows.every(r => r.paper) };
}
// the focus desk for one position
function compareFocus(p, f, nows) {
  const issues = [], q = p.quote || {};
  if (f.bid !== cents(q.bid)) issues.push('FOCUS_BID_DIFFERS');
  if (!(f.ask_line || '').startsWith(`Ask ${cents(q.ask)} · observed ${finite(q.at) ? f.observed_want : '—'}`)) issues.push('FOCUS_ASK_OR_TIME_DIFFERS');
  if (!(f.market_eyebrow || '').includes(`${p.holding_side} · ${fmt(p.qty)} CONTRACTS`)) issues.push('FOCUS_HELD_SIDE_DIFFERS');
  if (!anyNow(nows, n => (f.market_eyebrow || '').startsWith(bookCurrent(q, n) ? 'CURRENT EXIT BID' : 'LAST OBSERVED BID'))) issues.push('FOCUS_BOOK_STATE_DIFFERS');
  const recorded = [p.packet && p.packet.current_recommendation, p.review && p.review.recommendation].filter(Boolean);
  if (f.action === 'Waiting for evidence') { if (!/Missing:/.test(f.note || '')) issues.push('BLOCKED_WITHOUT_REASON'); if (nows.every(n => packetComplete(p, n))) issues.push('WAITING_WITH_COMPLETE_PACKET'); }
  else if (f.action === 'REVIEW RECORDED' ? !(p.review && p.review.review_id) : !recorded.includes(f.action)) issues.push('RECOMMENDATION_NOT_RECORDED');
  else if (nows.every(n => !packetComplete(p, n))) issues.push('RECOMMENDATION_WITHOUT_COMPLETE_PACKET');
  const r = p.review || {};
  const wantReview = r.review_id && finite(r.reviewed_at) ? `Xavier review recorded ${utc(r.reviewed_at)} · …${String(r.review_id).slice(-10)}` : 'No Xavier review recorded for this position';
  if (f.review !== wantReview) issues.push('REVIEW_TIME_DIFFERS');
  for (const o of standing(p)) if (nows.every(n => pastExpiry(o, n)) && !(f.note || '').includes(`${o.direction} limit at ${cents(o.limit_price)} is past its recorded expiry`)) issues.push('LAPSED_ORDER_NOT_NAMED');
  // the game monitor follows the same rule as the card
  const g = gameExpect(p);
  if (g.scoreboard === true ? !f.game_scoreboard : g.scoreboard === false && (f.game_scoreboard || !f.game_unavailable || g.says.some(x => !(f.game_unavailable || '').includes(x)))) issues.push('FOCUS_GAME_FALLBACK_WRONG');
  return issues;
}
// the brain rail for the position in focus: a light is 'current' only with evidence
function compareBrain(p, nodes, nows) {
  const issues = [], by = Object.fromEntries((nodes || []).map(n => [n.label, n]));
  const q = p.quote || {};
  const book = by['VENUE BOOK'], xav = by['XAVIER / MANAGEMENT'], prot = by['PROTECTION'];
  if (book && book.state === 'current' && !nows.some(n => bookCurrent(q, n))) issues.push('BOOK_LIT_WITHOUT_CURRENT_QUOTE');
  if (xav && xav.state === 'current' && !nows.some(n => packetComplete(p, n))) issues.push('PACKET_LIT_WITHOUT_EVIDENCE');
  const protMissing = nows.every(n => missingAt(p, n).some(x => String(x).includes('PROTECTION')));
  if (prot && prot.state === 'current' && (protMissing || !standing(p).length)) issues.push('PROTECTION_LIT_WITHOUT_EVIDENCE');
  return issues;
}
// the decision tape: every review event is one the API records (id, time,
// recommendation); every quote event is a move between two observations this
// run actually received; nothing else
function compareTape(S, tape, observed) {
  const reviews = new Map(S.positions.filter(p => p.review && p.review.review_id).map(p => [String(p.review.review_id).slice(-10), p.review]));
  const out = { events: tape.length, unrecorded: [], reviews_in_api: reviews.size, reviews_shown: 0 };
  for (const e of tape) {
    if (e.kind === 'review') {
      const r = reviews.get(e.ref);
      if (!r || e.head !== `Xavier · ${r.recommendation || 'review recorded'}` || e.time !== e.time_want) out.unrecorded.push({ ref: e.ref, head: e.head });
      else out.reviews_shown++;
    } else {
      const m = /^Bid (\S+) → (\S+)$/.exec(e.head || '');
      if (!m || !observed.some(o => o.ref === e.ref && o.bid === m[2])) out.unrecorded.push({ ref: e.ref, head: e.head });
    }
  }
  return out;
}
// <<< TRADER FIDELITY

function readDom(args) {
  const T = el => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
  const vis = el => { if (!el) return false; const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };
  const cards = [...document.querySelectorAll('#positions .position-card')].map(c => {
    const blocks = [...c.querySelectorAll('.card-prices .price-block')];
    const bidB = blocks.find(b => !b.classList.contains('target') && !b.classList.contains('pnl'));
    const tgtB = blocks.find(b => b.classList.contains('target'));
    const pnlB = blocks.find(b => b.classList.contains('pnl'));
    const score = c.querySelector('.card-score');
    const scoreNums = [...c.querySelectorAll('.team-score,[data-live-score-number]')].map(T).filter(t => t && /\d/.test(t));
    const clocks = [...c.querySelectorAll('[data-game-clock],.live-score-clock,.game-clock')].map(T).filter(Boolean);
    const un = score && score.querySelector('.score-unavailable');
    return { id: c.dataset.id, bid: T(bidB && bidB.querySelector('strong')), bid_label: T(bidB && bidB.querySelector('small')),
      ask_line: T(bidB && bidB.querySelector('.ask-line')), bid_visible: vis(bidB && bidB.querySelector('strong')),
      target: T(tgtB && tgtB.querySelector('strong')), target_label: T(tgtB && tgtB.querySelector('small')),
      pnl: T(pnlB && pnlB.querySelector('strong')), strip: T(c.querySelector('.order-strip')), holding: T(c.querySelector('.holding-line')),
      pill: T(c.querySelector('.packet-pill')), ages: T(c.querySelector('.card-footer .age-label')),
      scoreboard: !!(score && score.querySelector('.scoreboard')), unavailable: !!un, unavailable_text: T(un),
      score_numbers: scoreNums, clocks, text: T(c).slice(0, 2000) };
  });
  const tfmt = at => new Date(at * 1000).toLocaleTimeString('en-US', { hour12: false });
  const tape = [...document.querySelectorAll('#activity .tape-event')].map(e => {
    const time = T(e.querySelector('time')) || ''; const parts = time.split(' · ');
    const kind = T(e.querySelector('.event-dot')) === '↗' ? 'quote' : 'review';
    const ref = parts.slice(1).join(' · ');
    const want = (args.reviewTimes || {})[ref];
    return { kind, head: T(e.querySelector('b')), time: parts[0], ref, time_want: finite(want) ? tfmt(want) : null };
    function finite(v) { return typeof v === 'number' && Number.isFinite(v); }
  });
  const brain = [...document.querySelectorAll('#brain-rail .brain-node')].map(n => ({ label: T(n.querySelector('small')), value: T(n.querySelector('b')), state: n.dataset.state }));
  const imgs = [...document.querySelectorAll('img')].map(i => { let host = null; try { host = new URL(i.currentSrc || i.src, location.href).host; } catch (e) {}
    return { host, same_origin: host === location.host, complete: i.complete, natural_w: i.naturalWidth, visible: vis(i), alt: i.getAttribute('alt') }; });
  return {
    now: Date.now() / 1000,
    preview_flag: window.__TRADER_PREVIEW__ === true,
    connection: T(document.getElementById('connection')), footer: T(document.getElementById('footer-proof')),
    error_shown: (() => { const e = document.getElementById('error'); return !!(e && !e.hidden && vis(e)); })(),
    wall_count: T(document.getElementById('wall-count')), order_count: T(document.getElementById('order-count')),
    metrics: T(document.getElementById('metrics')), truncate_banner: !!document.querySelector('#positions .truncate-banner'),
    focused: (() => { const fc = document.querySelector('#positions .position-card.focused'); return fc ? fc.dataset.id : null; })(),
    // against the initial containing block: a phone widens its layout viewport
    // (innerWidth) to fit a too-wide page, so scrollWidth - innerWidth hides it
    cards, tape, brain, imgs, overflow_px: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    tilde_clocks: [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && /^~\d+:\d\d$/.test((e.textContent || '').trim())).length,
  };
}

function readFocus(observedAt) {
  const T = el => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : null);
  const v = document.querySelector('#focus-market .screen-value');
  const eyebrow = document.querySelector('#focus-market .screen-eyebrow');
  return { bid: v && v.firstChild ? (v.firstChild.textContent || '').trim() : null, ask_line: T(document.querySelector('#focus-market .ask-line')),
    observed_want: typeof observedAt === 'number' && Number.isFinite(observedAt) ? new Date(observedAt * 1000).toLocaleTimeString('en-GB', { hour12: false }) : null,
    market_eyebrow: T(eyebrow), action: T(document.querySelector('#focus-decision .screen-decision')),
    note: T(document.querySelector('#focus-decision .screen-decision-note')), review: T(document.querySelector('#focus-decision .screen-review')),
    game_scoreboard: !!document.querySelector('#focus-game .scoreboard'), game_unavailable: T(document.querySelector('#focus-game .score-unavailable')) };
}

const motionKeys = c => [c.bid, c.ask_line, c.target, c.pnl, c.strip, c.holding, (c.score_numbers || []).join('/')].join(' | ');
const nowsOf = t => [t, t - 1, t - 2, t - 3];

(async () => {
  fs.mkdirSync(path.join(OUT, 'trader'), { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const results = [];
  for (const [dev, d] of Object.entries(DEVICES)) {
    const ctx = await browser.newContext(Object.assign({}, d));
    let frozenBody = null, outage = false, live = 0, frozenServed = 0, refused = 0, aborted = 0; let S = null; const errors = [];
    const observed = [];   // every quote observation received while live: {ref, bid} (page formats)
    await ctx.route('**/*', async route => {
      const req = route.request(); const m = req.method();
      if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }
      const isApi = new URL(req.url()).pathname === API;
      if (outage && isApi) { refused++; return route.fulfill({ status: 503, contentType: 'application/json', body: '{"refused":"PREVIEW_OUTAGE_PHASE"}' }); }
      // prices, orders and records stay exactly S; only snapshot_at moves to the
      // moment of answering, so the page's own 15 s staleness rule does not
      // (correctly) declare the feed stale while the data is held still
      if (frozenBody && isApi) { frozenServed++; const b = JSON.parse(frozenBody); b.snapshot_at = Date.now() / 1000; return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) }); }
      return route.continue();
    });
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    page.on('console', m => { if (m.type() === 'error' && !outage) errors.push(m.text().slice(0, 200)); });
    page.on('response', async r => {
      if (frozenBody || outage || new URL(r.url()).pathname !== API || r.status() !== 200) return;
      try { const txt = await r.text(); const j = JSON.parse(txt); if (j && Array.isArray(j.positions)) { S = j; live++;
        for (const p of j.positions) { const q = p.quote || {}; observed.push({ ref: String(q.observation_id || 'quote'), bid: cents(q.bid) }); } } } catch (e) {}
    });
    const rec = { device: dev, viewport: d.viewport };
    try { await page.goto(BASE + '/trader', { waitUntil: 'load', timeout: 60000 }); } catch (e) { errors.push('goto: ' + String(e).slice(0, 160)); }
    await page.waitForTimeout(12000);
    rec.live_snapshots = live;
    if (!S) {
      rec.verdict = 'NO_API_SNAPSHOT'; rec.errors = errors.slice(0, 8);
      await page.screenshot({ path: path.join(OUT, 'trader', dev + '_no_snapshot.png') }).catch(() => {});
      results.push(rec); await ctx.close(); continue;
    }
    // FROZEN: answer every further poll with S, wait for the page to render one
    frozenBody = JSON.stringify(S);
    const t0 = Date.now(); while (frozenServed < 1 && Date.now() - t0 < 30000) await page.waitForTimeout(500);
    await page.waitForTimeout(1500);
    const reviewTimes = Object.fromEntries(S.positions.filter(p => p.review && p.review.review_id && finite(p.review.reviewed_at)).map(p => [String(p.review.review_id).slice(-10), p.review.reviewed_at]));
    const d1 = await page.evaluate(readDom, { reviewTimes });
    await page.waitForTimeout(10000);
    const d2 = await page.evaluate(readDom, { reviewTimes });
    rec.frozen_polls_served = frozenServed;
    const totalStanding = S.positions.reduce((n, p) => n + standing(p).length, 0);
    const allOrders = S.positions.reduce((n, p) => n + (p.orders || []).length, 0);
    rec.api = { total_position_count: S.total_position_count, returned: S.positions.length, truncated: S.truncated,
      standing_orders: totalStanding, orders_all_states: allOrders, with_current_game: S.positions.filter(p => gameCurrent(p.game)).length,
      with_review: Object.keys(reviewTimes).length,
      score_feed: S.score_feed || null, source_sha: S.source_sha || null, mode: S.mode, authority: S.authority };
    rec.page = { preview_flag: d1.preview_flag, connection: d1.connection, footer_prefix: (d1.footer || '').split('·')[0].trim(),
      error_shown: d1.error_shown, wall_count: d1.wall_count, order_count: d1.order_count, cards: d1.cards.length,
      truncate_banner: d1.truncate_banner, overflow_px: d1.overflow_px, tilde_clocks: d1.tilde_clocks };
    rec.fidelity = compareCards(S, d1, nowsOf(d1.now));
    // MOTION while the data is frozen: prices, P/L, orders and scores, and the tape
    const m1 = new Map(d1.cards.map(c => [c.id, motionKeys(c)]));
    rec.moved_while_frozen = d2.cards.filter(c => m1.has(c.id) && m1.get(c.id) !== motionKeys(c)).map(c => ({ id: c.id, before: m1.get(c.id), after: motionKeys(c) })).slice(0, 20);
    rec.counts_moved_while_frozen = d1.wall_count !== d2.wall_count || d1.order_count !== d2.order_count || d1.cards.length !== d2.cards.length;
    rec.tape_moved_while_frozen = d2.tape.length !== d1.tape.length || d2.tape.some((e, i) => !d1.tape[i] || d1.tape[i].ref !== e.ref || d1.tape[i].head !== e.head);
    rec.tape = compareTape(S, d2.tape, observed);
    rec.brain = { focused: !!d1.focused, issues: (() => { const p = S.positions.find(x => x.position_id === d1.focused) || S.positions[0]; return p ? compareBrain(p, d1.brain, nowsOf(d1.now)) : []; })() };
    // images
    rec.images = { total: d1.imgs.length,
      foreign_hosts: [...new Set(d1.imgs.filter(i => i.host && !i.same_origin && !IMAGE_HOSTS.includes(i.host)).map(i => i.host))],
      hosts: [...new Set(d1.imgs.map(i => i.same_origin ? 'self' : i.host))],
      broken_visible: d1.imgs.filter(i => i.visible && i.complete && i.natural_w === 0).length };
    await page.screenshot({ path: path.join(OUT, 'trader', dev + '_wall.png') }).catch(() => {});
    await page.screenshot({ path: path.join(OUT, 'trader', dev + '_wall_full.png'), fullPage: true }).catch(() => {});
    // FOCUS DESK: recorded bid / ask / time, held side, recorded management, the review
    const ids = d1.cards.map(c => c.id);
    const pick = dev === FOCUS_ALL_ON ? ids : ids.slice(0, FOCUS_SAMPLE);
    const focus = { checked: 0, bid_mismatch: [], fabricated_recommendation: [], blocked_without_missing: [], issues: [], recommendation_shown: 0, waiting: 0, brain_issues: [] };
    const byId = new Map(S.positions.map(p => [p.position_id, p]));
    for (const id of pick) {
      const ok = await page.evaluate(id => { const b = [...document.querySelectorAll('#positions .card-focus')].find(e => e.dataset.focus === id); if (!b) return false; b.click(); return true; }, id);
      if (!ok) continue;
      await page.waitForTimeout(650);   // the brain rail redraws on its 500 ms observer
      const p = byId.get(id);
      const f = await page.evaluate(readFocus, (p.quote || {}).at);
      const nowF = await page.evaluate(() => Date.now() / 1000);
      focus.checked++;
      const issues = compareFocus(p, f, nowsOf(nowF));
      if (issues.includes('FOCUS_BID_DIFFERS')) focus.bid_mismatch.push({ id, dom: f.bid, api: cents((p.quote || {}).bid) });
      if (issues.includes('RECOMMENDATION_NOT_RECORDED')) focus.fabricated_recommendation.push({ id, shown: f.action });
      if (issues.includes('BLOCKED_WITHOUT_REASON')) focus.blocked_without_missing.push({ id, note: f.note });
      issues.filter(x => !['FOCUS_BID_DIFFERS', 'RECOMMENDATION_NOT_RECORDED', 'BLOCKED_WITHOUT_REASON'].includes(x)).forEach(x => focus.issues.push({ id, issue: x }));
      if (f.action === 'Waiting for evidence') focus.waiting++; else focus.recommendation_shown++;
      const brain = await page.evaluate(() => [...document.querySelectorAll('#brain-rail .brain-node')].map(n => ({ label: (n.querySelector('small') || {}).textContent, value: (n.querySelector('b') || {}).textContent, state: n.dataset.state })));
      compareBrain(p, brain, nowsOf(nowF)).forEach(x => focus.brain_issues.push({ id, issue: x }));
      if (focus.checked === 1) await page.screenshot({ path: path.join(OUT, 'trader', dev + '_focus.png') }).catch(() => {});
    }
    rec.focus = focus;
    // ORDERS VIEW: exactly the standing orders, each with its quantity, prices and state
    const ov = await page.evaluate(() => { const b = document.querySelector('[data-view="orders"]'); if (!b) return null; b.click(); return true; });
    if (ov) {
      await page.waitForTimeout(400);
      const rows = await page.evaluate(() => [...document.querySelectorAll('#positions .orders-view tbody tr')].filter(tr => tr.children.length >= 7).map(tr => {
        const td = [...tr.children], T = el => (el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '');
        const order = td[1], st = td[6];
        return { direction: order && order.firstChild ? (order.firstChild.textContent || '').trim() : '', role: T(order && order.querySelector('small')),
          remaining: T(td[2]), reference: T(td[3]), limit: T(td[4]), state: T(st && st.querySelector('.order-state')),
          paper: /PAPER SIMULATED/.test(T(st)), lapsed_label: /PAST EXPIRY · NOT PROTECTION/.test(T(st)) };
      }));
      const nowO = await page.evaluate(() => Date.now() / 1000);
      rec.orders_view = compareOrderRows(S, rows, nowsOf(nowO));
      await page.screenshot({ path: path.join(OUT, 'trader', dev + '_orders.png') }).catch(() => {});
      await page.evaluate(() => { const b = document.querySelector('[data-view="positions"]'); if (b) b.click(); });
    } else rec.orders_view = null;
    // OUTAGE: the feed refuses; the page must stop claiming anything is current
    outage = true;
    const tO = Date.now(); let dx = null;
    while (Date.now() - tO < 20000) { await page.waitForTimeout(1000); dx = await page.evaluate(readDom, { reviewTimes }); if (/Disconnected/.test(dx.connection || '')) break; }
    const fx = await page.evaluate(readFocus, null).catch(() => ({}));
    rec.outage = { polls_refused: refused, connection: dx && dx.connection, error_shown: dx && dx.error_shown,
      claims: dx ? [
        ...(/Readback connected/.test(dx.connection || '') ? ['CONNECTED_WHILE_REFUSED'] : []),
        ...dx.cards.filter(c => c.bid_label === 'Current bid').map(c => 'CURRENT_BID:' + c.id.slice(-12)),
        ...dx.cards.filter(c => c.pnl && c.pnl !== '—').map(c => 'MARKED_PNL:' + c.id.slice(-12)),
        ...dx.cards.filter(c => c.pill === 'Packet complete').map(c => 'PACKET_COMPLETE:' + c.id.slice(-12)),
        ...(dx.cards.length && fx.action && fx.action !== 'Waiting for evidence' ? ['RECOMMENDATION:' + fx.action] : []),
        ...dx.brain.filter(n => ['VENUE BOOK', 'XAVIER / MANAGEMENT'].includes(n.label) && n.state === 'current').map(n => 'LIT:' + n.label),
      ] : ['NO_READ'] };
    await page.screenshot({ path: path.join(OUT, 'trader', dev + '_outage.png') }).catch(() => {});
    rec.evidence = { positions: S.positions.length, orders_all_states: allOrders, standing_orders: totalStanding,
      non_standing_orders: allOrders - totalStanding, reviews: Object.keys(reviewTimes).length, focus_checked: focus.checked };
    rec.non_get_aborted = aborted; rec.console_errors = errors.length; rec.first_errors = errors.slice(0, 6);
    const F = rec.fidelity;
    const fails = [];
    if (rec.page.preview_flag || rec.page.footer_prefix !== 'READ ONLY' || !/Readback connected/.test(rec.page.connection || '')) fails.push('NOT_THE_NATIVE_READBACK');
    if (rec.page.error_shown) fails.push('ERROR_BANNER_SHOWN');
    if (F.missing_cards.length || F.extra_cards.length) fails.push('POSITION_SET_DIFFERS');
    if (String(S.total_position_count) !== rec.page.wall_count) fails.push('WALL_COUNT_DIFFERS');
    if (String(totalStanding) !== rec.page.order_count) fails.push('ORDER_COUNT_DIFFERS');
    if (!!S.truncated !== rec.page.truncate_banner) fails.push('TRUNCATION_NOT_DISCLOSED');
    if (F.bid_mismatch.length) fails.push('BID_DIFFERS');
    if (F.ask_mismatch.length) fails.push('ASK_DIFFERS');
    if (F.bid_label_mismatch.length) fails.push('BID_LABEL_DIFFERS');
    if (F.held_side_mismatch.length) fails.push('HELD_SIDE_DIFFERS');
    if (F.target_mismatch.length) fails.push('TARGET_DIFFERS');
    if (F.pnl_mismatch.length) fails.push('PNL_DIFFERS');
    if (F.strip_mismatch.length) fails.push('ORDER_STRIP_DIFFERS');
    if (F.evidence_state_mismatch.length) fails.push('EVIDENCE_STATE_DIFFERS');
    if (F.age_mismatch.length) fails.push('AGE_DIFFERS');
    if (F.expired_shown_as_protection.length) fails.push('EXPIRED_ORDER_SHOWN_AS_PROTECTION');
    if (F.score_without_current_game.length) fails.push('SCORE_WITHOUT_CURRENT_GAME');
    if (F.score_mismatch.length) fails.push('SCORE_DIFFERS');
    if (F.game_fallback_wrong.length) fails.push('GAME_FALLBACK_WRONG');
    if (F.bid_hidden) fails.push('BID_NOT_VISIBLE');
    if (rec.moved_while_frozen.length || rec.counts_moved_while_frozen) fails.push('MOVED_WITHOUT_DATA');
    if (rec.tape_moved_while_frozen) fails.push('ACTIVITY_WITHOUT_DATA');
    if (rec.tape.unrecorded.length) fails.push('TAPE_EVENT_NOT_RECORDED');
    // the tape holds the newest 30 events: below that every recorded review must be on it
    if (rec.tape.events < 30 && rec.tape.reviews_shown < rec.tape.reviews_in_api) fails.push('REVIEW_NOT_ON_TAPE');
    if (rec.page.tilde_clocks) fails.push('ESTIMATED_CLOCK_SHOWN');
    if (rec.images.foreign_hosts.length) fails.push('IMAGE_FROM_UNLISTED_HOST');
    if (rec.images.broken_visible) fails.push('BROKEN_IMAGE_VISIBLE');
    if (focus.bid_mismatch.length) fails.push('FOCUS_BID_DIFFERS');
    if (focus.fabricated_recommendation.length) fails.push('RECOMMENDATION_NOT_RECORDED');
    if (focus.blocked_without_missing.length) fails.push('BLOCKED_WITHOUT_REASON');
    for (const x of [...new Set(focus.issues.map(i => i.issue))]) fails.push(x);
    if (focus.brain_issues.length || rec.brain.issues.length) fails.push('HEALTHY_INDICATOR_WITHOUT_EVIDENCE');
    const O = rec.orders_view;
    if (!O || O.rows !== O.api_standing || !O.limit_state_multiset_equal || !O.every_row_labelled_paper_simulated) fails.push('ORDERS_VIEW_DIFFERS');
    if (O && (!O.row_multiset_equal || !O.reference_multiset_equal)) fails.push('ORDER_ROWS_DIFFER');
    if (O && (O.lapsed_rows_unlabelled || O.lapsed_label_without_lapse)) fails.push('LAPSED_ORDER_LABEL_DIFFERS');
    if (rec.page.overflow_px > 0) fails.push('HORIZONTAL_OVERFLOW');
    if (F.ask_in_api && F.ask_shown < F.ask_in_api) fails.push('ASK_NOT_SHOWN');
    if (rec.frozen_polls_served < 1) fails.push('FROZEN_PHASE_NOT_REACHED');
    if (rec.outage.polls_refused < 1 || !/Disconnected/.test(rec.outage.connection || '') || !rec.outage.error_shown) fails.push('OUTAGE_NOT_DISCLOSED');
    if (rec.outage.claims.length) fails.push('HEALTHY_INDICATOR_DURING_OUTAGE');
    rec.failures = [...new Set(fails)]; rec.verdict = fails.length ? 'FAIL' : 'PASS';
    results.push(rec);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'trader_accept.json'), JSON.stringify({ base: BASE, at: new Date().toISOString(), results }, null, 1));
  for (const r of results) {
    const a = r.api || {}, p = r.page || {}, F = r.fidelity || {};
    console.log(`TRADER ${r.device.padEnd(17)} ${r.verdict} api=${a.total_position_count}/${a.returned} orders=${a.standing_orders}/${a.orders_all_states} games=${a.with_current_game} reviews=${a.with_review} | page cards=${p.cards} wall=${p.wall_count} orders=${p.order_count} | missing=${(F.missing_cards || []).length} bid!=${(F.bid_mismatch || []).length} ask!=${(F.ask_mismatch || []).length} side!=${(F.held_side_mismatch || []).length} tgt!=${(F.target_mismatch || []).length} pnl!=${(F.pnl_mismatch || []).length} strip!=${(F.strip_mismatch || []).length} pill!=${(F.evidence_state_mismatch || []).length} expired=${(F.expired_shown_as_protection || []).length} game!=${(F.game_fallback_wrong || []).length} ask=${F.ask_shown}/${F.ask_in_api} moved=${(r.moved_while_frozen || []).length} tape=${r.tape ? r.tape.reviews_shown + '/' + r.tape.reviews_in_api + ' unrec=' + r.tape.unrecorded.length : '-'} focus=${r.focus ? r.focus.checked : 0} ov=${r.orders_view ? r.orders_view.rows + '/' + r.orders_view.api_standing : 'none'} outage=${r.outage ? r.outage.claims.length : '-'} imgs=${JSON.stringify((r.images || {}).hosts)} | ${JSON.stringify(r.failures || [])}`);
  }
})();
