// PUBLIC ARTIFACT REDACTION, for shots.js and trader_accept.js
// (pinned by backend/tests/js/frontend-preview-public-artifact.test.cjs).
//
// frontend-preview.yml uploads preview.json and trader_accept.json IN THE
// CLEAR from a PUBLIC repository (30 days), and pm-acceptance copies them into
// the evidence packet; only the screenshots are encrypted. What the paper book
// holds is private and is never written there:
//   a position room key   <BOOK_CODE>:<EVT|MKT>:<event or market slug>
//                         (position_rooms.group_key); position.js reads the
//                         room at /api/command/positions/room/<key> and the
//                         page is /position?g=<key>
//   a Trader position id  paperpos:<account>:<group>:<market slug>:<side>
//                         (trader_readmodel), and order / review ids
//   the text a page draws from them (a focused card's market slug, a room
//                         card's event, a game panel's event title)
// Three layers keep them out:
//   1. at the source: an API path is counted with the room key masked
//      (apiPathKey); a page path names its search values only for the page's
//      public UI parameters (publicPath / navPaths); a control is named by its
//      structure, never its text (shots.js PUBLIC LABEL); a Trader record
//      names a position / order / tape event by a sha256 prefix
//      (publicTraderResult)
//   2. every query value in any string the harness writes, other than a
//      public parameter's, becomes its sha256 prefix (scrubText)
//   3. every private identifier the harness itself read (the room list of
//      both books, the Trader snapshot: collectIdentifiers) is replaced
//      wherever it still appears -- as is, URL-encoded either way -- in a
//      value or a key (scrubDeep)
// Counts, verdicts and failure classes are never touched: no measurement
// changes, only what names a private object.
'use strict';
const crypto = require('crypto');

const sha12 = (s) => crypto.createHash('sha256').update(String(s)).digest('hex').slice(0, 12);
const ref = (s) => 'sha256:' + sha12(s);
const decode = (s) => { try { return decodeURIComponent(String(s).replace(/\+/g, ' ')); } catch (e) { return String(s); } };

// an API path as preview.json counts it (api_by_path_status): the room key is
// the last segment of a room read and is never written; a hex / uuid segment
// is masked as it always was
const maskRoom = (p) => String(p).replace(/\/positions\/room\/[^/?#]*/g, '/positions/room/:key');
function apiPathKey(pathname) {
  return maskRoom(pathname).replace(/\/[0-9a-f-]{8,}/g, '/:id');
}

// the page's public UI parameters: their values are UI state (which book is
// listed), never a private identifier. Every other value (g=<room key>) is
// written as the sha256 prefix of its decoded value -- for a room key the
// same prefix as room_key_sha256_12
const PUBLIC_PARAMS = ['book'];
const QUERY_VALUE = /([?&])([A-Za-z_][\w.-]*)=([^&\s"'#<>,]*)/g;
function scrubQuery(s) {
  return String(s).replace(QUERY_VALUE, (m, sep, k, v) => (PUBLIC_PARAMS.includes(k) || /^sha256:[0-9a-f]{12}$/.test(v)) ? m : sep + k + '=' + ref(decode(v)));
}
// a page path (pathname + search) as a failure note names it
function publicPath(p) {
  const s = String(p == null ? '' : p);
  const q = s.indexOf('?');
  const head = maskRoom(q < 0 ? s : s.slice(0, q));
  return q < 0 ? head : head + scrubQuery(s.slice(q));
}
// the navigations a control started, as the verdict compares and names them.
// No expectation names a private value (pinned by the suite), so the verdict
// on these paths is the verdict on the raw ones
function navPaths(urls) {
  return (urls || []).map((u) => { try { const x = new URL(u); return publicPath(x.pathname + x.search); } catch (e) { return publicPath(u); } });
}

// a private string worth replacing everywhere: 8+ characters with a digit,
// ':', '%', '_' or '-' (keys, slugs, ids, uuids), or a phrase of 6+
// characters with a space (an event, market or team name: the Trader game
// panel's unavailable fallback prints the position's title). A single plain
// word (an agent's name, a side) is never one: replacing it would rewrite the
// harness's own vocabulary and hide nothing
const distinctive = (s) => typeof s === 'string' && ((s.length >= 8 && /[0-9:%_-]/.test(s)) || (s.trim().length >= 6 && /\S\s+\S/.test(s)));
// the fields of the API's reads that carry one (room list, room, Trader
// snapshot); a composite key also yields its parts
const ID_FIELDS = /^(group_key|position_id|position_key|leg_key|order_id|review_id|account_id|group_id|slug|slugs|market_slug|us_market_slug|event_slug|event_id|market_id|ticker|condition_id|instrument_id|token_id|asset_id|title|market_title|event_title|home|away|home_team|away_team)$/;
const COMPOSITE = /^(group_key|position_id|position_key|leg_key)$/;
function collectIdentifiers(json, into) {
  const out = into || new Set();
  const add = (k, v) => {
    if (typeof v === 'number') v = String(v);
    if (typeof v !== 'string') return;
    out.add(v);
    if (COMPOSITE.test(k)) { const parts = v.split(':'); for (let i = 1; i < parts.length; i++) { out.add(parts[i]); out.add(parts.slice(i).join(':')); } }
  };
  const walk = (v, depth) => {
    if (depth > 40 || v == null) return;
    if (Array.isArray(v)) { for (const x of v) walk(x, depth + 1); return; }
    if (typeof v !== 'object') return;
    for (const [k, x] of Object.entries(v)) {
      if (ID_FIELDS.test(k)) { if (Array.isArray(x)) x.forEach((y) => add(k, y)); else add(k, x); }
      walk(x, depth + 1);
    }
  };
  walk(json, 0);
  for (const s of [...out]) if (!distinctive(s)) out.delete(s);
  return out;
}
// a room key read from the room list: the key and its parts
function roomIdentifiers(key, into) { return collectIdentifiers({ group_key: key }, into); }

// every spelling a page, a URL or an error message may carry
function variants(s) {
  const out = new Set([s]);
  try { const c = encodeURIComponent(s); out.add(c); out.add(c.replace(/%[0-9A-F]{2}/g, (m) => m.toLowerCase())); out.add(encodeURI(s)); } catch (e) { /* lone surrogate: as is only */ }
  return [...out];
}
function compile(secrets) {
  const pairs = [];
  for (const s of secrets || []) if (distinctive(s)) for (const v of variants(s)) pairs.push([v, 'redacted:' + sha12(s)]);
  // the longest first: a key is replaced whole before its own parts
  return pairs.sort((a, b) => b[0].length - a[0].length);
}
function scrubText(s, pairs) {
  let out = scrubQuery(s);
  for (const [v, r] of pairs || []) if (out.includes(v)) out = out.split(v).join(r);
  return out;
}
// a record as it may be written: every string value AND every key
function scrubDeep(value, secrets) {
  const pairs = Array.isArray(secrets) && secrets.length && Array.isArray(secrets[0]) ? secrets : compile(secrets);
  const walk = (v) => {
    if (typeof v === 'string') return scrubText(v, pairs);
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') { const o = {}; for (const [k, x] of Object.entries(v)) o[scrubText(k, pairs)] = walk(x); return o; }
    return v;
  };
  return walk(value);
}

// a Trader acceptance record as it is written: a position (id,
// missing_cards, extra_cards), an order (order) and a tape event (ref) are
// named by their sha256 prefix; a tape event's head is kept only in the two
// shapes the page draws (a recorded review, an observed bid move), which
// carry no identifier; then layer 2 and 3 as above
const TRADER_ID_KEYS = ['id', 'order', 'ref'];
const TRADER_ID_LISTS = ['missing_cards', 'extra_cards'];
const TAPE_HEAD = /^(Xavier · [A-Za-z][A-Za-z _-]{0,39}|Bid \S{1,12} → \S{1,12})$/;
function publicTraderResult(rec, secrets) {
  const walk = (v, k) => {
    if (Array.isArray(v)) return TRADER_ID_LISTS.includes(k) ? v.map((x) => ref(x)) : v.map((x) => walk(x, null));
    if (v && typeof v === 'object') {
      const o = {};
      for (const [kk, x] of Object.entries(v)) {
        if (TRADER_ID_KEYS.includes(kk) && x != null && typeof x !== 'object') o[kk] = ref(x);
        else if (kk === 'head' && typeof x === 'string') o[kk] = TAPE_HEAD.test(x) ? x : ref(x);
        else o[kk] = walk(x, kk);
      }
      return o;
    }
    return v;
  };
  return scrubDeep(walk(rec, null), secrets);
}

module.exports = { sha12, ref, maskRoom, apiPathKey,PUBLIC_PARAMS, scrubQuery, publicPath, navPaths, distinctive, collectIdentifiers,
  roomIdentifiers, variants, compile, scrubText, scrubDeep, publicTraderResult, TAPE_HEAD };
