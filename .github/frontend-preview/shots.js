// FRONTEND PREVIEW DEVICE MATRIX (read only), for frontend-preview.yml.
//
// Loads every Command Center page from the preview server at desktop,
// iPhone and iPad sizes (portrait and landscape) plus reduced-motion runs:
// Command (/), Floor (/floor), Trader (/trader), the agent pages (/derek,
// /xavier, /audrey, /karen, /allocator, /archer, /scout, and /eddie, Archer's
// historical address; Adriana is DESK_ONLY, see below), the operations
// desk (/ops), the position rooms (/positions) and one position room
// (/position?g=<the first room the production read returns>). Per view it
// records: HTTP status, sign-in state, horizontal overflow, WebGL canvases
// (is the 3D scene genuinely there and how much of the screen it holds),
// overlapping fixed/sticky bars (a full-viewport background layer painted
// beneath a bar is listed apart, see pairBars, and its own controls are
// still checked against the bars above it, see LAYER CHILDREN), touch
// targets under 44 px on touch devices (same-origin frames included),
// controls another box covers so a tap cannot reach them (REACHABILITY),
// text and controls under the notch / home indicator with the device's
// safe-area insets applied (SAFE AREA), PAPER/SHADOW label counts, words
// that would betray example/demo data, the current-workspace marker,
// console errors and API statuses. Then WORKING CONTROLS: every declared
// navigation link and primary control is tapped (clicked on desktop) and
// must land on its expected state; a click that starts a navigation is
// answered 204 here, so the page stays and the destination is recorded
// (the destination is measured by its own view), and every overlay opened
// is measured and closed again (CONTROLS). Every non-GET is aborted in the
// browser as well as refused by the server. Screenshots go to <out>/shots/.
// preview.json is uploaded in the clear (public repository): nothing in it
// names what the paper book holds -- no room key, position id, slug or page
// text (public-artifact.js; PUBLIC LABEL).
'use strict';
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const PA = require('./public-artifact');

const BASE = process.argv[2];
const OUT = process.argv[3];
const PHONE = { isMobile: true, hasTouch: true };
const DEVICES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  iphone: Object.assign({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 }, PHONE),
  iphone_landscape: Object.assign({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 3 }, PHONE),
  ipad_portrait: Object.assign({ viewport: { width: 820, height: 1180 }, deviceScaleFactor: 2 }, PHONE),
  ipad_landscape: Object.assign({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2 }, PHONE),
};
// THE NOTCH AND THE HOME INDICATOR: env(safe-area-inset-*) as the installed
// app (Add to Home Screen, black-translucent status bar: index.html) sees
// them. An iPhone 14-class phone: 47 px status bar / notch and a 34 px home
// indicator in portrait; in landscape the notch is a 47 px inset on each
// side and the indicator 21 px. An iPad with Face ID: a 24 px status bar and
// a 20 px indicator. Applied through the DevTools protocol before the page
// loads (Emulation.setSafeAreaInsetsOverride) on every touch view.
const SAFE_AREA = {
  iphone: { top: 47, right: 0, bottom: 34, left: 0 },
  iphone_landscape: { top: 0, right: 47, bottom: 21, left: 47 },
  ipad_portrait: { top: 24, right: 0, bottom: 20, left: 0 },
  ipad_landscape: { top: 24, right: 0, bottom: 20, left: 0 },
};
const PAGES = { command: '/', floor: '/floor', trader: '/trader',
  derek: '/derek', xavier: '/xavier', audrey: '/audrey', karen: '/karen', allocator: '/allocator', archer: '/archer', scout: '/scout',
  eddie: '/eddie',
  ops: '/ops', positions: '/positions', room: '/position' };
const AGENTS = ['derek', 'xavier', 'audrey', 'karen', 'allocator', 'archer', 'scout'];
// agent workspace addresses that are another agent's page: netlify.toml
// rewrites /eddie to agent.html, which serves Archer (migration 266 alias)
const ALIAS_AGENTS = { eddie: 'archer' };
// seats with no workspace page, named in preview.json so their absence is
// stated, never a silent gap: netlify.toml has no /adriana rewrite and the
// API serves no /api/command/agents/adriana/page (agent_pages.PAGE_PATHS),
// so /adriana would fall to the catch-all; Adriana is a floor desk only
// (/floor, GET /api/command/floor/adriana) and is not linked from here
const DESK_ONLY = { adriana: 'NO_WORKSPACE_PAGE: no /adriana rewrite and no /api/command/agents/adriana/page; floor desk only (/floor, /api/command/floor/adriana)' };
const DEVICE_ORDER = ['desktop', 'iphone', 'iphone_landscape', 'ipad_portrait', 'ipad_landscape'];
// the nine approval views first, then the extra states, then Command on the
// fifth profile (iPhone landscape) and every other page on all five
const VIEWS = [
  ['desktop', 'command'], ['desktop', 'floor'], ['desktop', 'trader'],
  ['iphone', 'command'], ['iphone', 'floor'], ['iphone', 'trader'],
  ['ipad_portrait', 'floor'], ['ipad_portrait', 'trader'], ['ipad_landscape', 'floor'],
  ['ipad_portrait', 'command'], ['ipad_landscape', 'trader'], ['ipad_landscape', 'command'],
  ['iphone_landscape', 'floor'], ['iphone_landscape', 'trader'],
  ['iphone', 'floor', 'reduce'], ['desktop', 'trader', 'reduce'], ['iphone', 'command', 'reduce'],
  ['iphone_landscape', 'command'],
].concat(AGENTS.concat(Object.keys(ALIAS_AGENTS), ['ops', 'positions', 'room']).reduce((a, pg) => a.concat(DEVICE_ORDER.map(dev => [dev, pg])), []));
const SUSPECT = /\b(lorem|ipsum|example data|sample data|demo data|placeholder|mock data|fake)\b/i;
// a LOCAL subset (HARNESS_PAGES=ops,positions) is named in the output, so a
// partial matrix can never pass for the full one
const ONLY = (process.env.HARNESS_PAGES || '').split(',').map(s => s.trim()).filter(Boolean);

// >>> CONTROLS (pinned by backend/tests/js/frontend-preview-controls.test.cjs)
// A control is { name, sel, expect, optional?, when?, close?, action? }.
//   sel      CSS; the first VISIBLE match is the control (a phone and a
//            desktop layout may each show their own copy)
//   expect   what the tap must produce; every key named must hold:
//            nav: path | [paths]   a navigation to that path started (search
//                                  too when the path holds a '?')
//            hash: '#x'            the document's hash after the tap
//            inView: sel           that box is on screen after the tap
//            visible / hidden: sel that box is shown / not shown
//            attr: [sel, name, v]  an attribute of that box
//            self: [name, v]       an attribute of the control itself
//            selfClass: c          a class of the control itself
//            bodyAttr: [name, v] / bodyClass / notBodyClass: c
//            dialogOpen / dialogClosed: sel   a <dialog>'s open state
//            fullscreen: true      the document is in full screen
//            request: '/api/...'   a GET to that path was sent after the tap
//            any: [expect, ...]    one of these holds
//   optional the control exists only with data (a desk to open, a position
//            to inspect): absent, it is UNMEASURED and listed as such --
//            never counted as passed
//   when     (viewport) => bool: the layout the control belongs to (a
//            desktop-only logo); declared from the page's own CSS
//            breakpoints, never from what passed
//   a control inside a closed <details> is reached through its summary
//   (tapped first); on touch every tapped control must be 44 x 44 px
//   close    { sel, expect }: an overlay is closed again this way and must
//            reach that state; while open it is measured (inside the
//            viewport, its controls 44 px on touch, the close control tappable)
//   action   'fill:<text>' types into the control instead of tapping it
// hq2-brand.css / command-ops.css switch the shell at 780 px (bottom tab bar,
// no logo, no DESK link); hq.js switches Command at 760 px
const shellWide = vp => vp.width > 780;
const phoneW = vp => vp.width <= 760;
// hq.js PHONE / hq.css: Command's pocket layout on a phone, and on a phone on
// its side (<= 500 px tall and <= 1024 px wide)
const pocket = vp => vp.width <= 760 || (vp.height <= 500 && vp.width <= 1024);
// trader.css hides the Command link and Reduce motion at <= 900 px, every
// link but the current one and the full-screen button at <= 600 px
const traderAbove = (w) => vp => vp.width > w;
const nav = (name, sel, to, o) => Object.assign({ name, sel, expect: { nav: to } }, o || {});
const RAIL = [
  nav('rail: logo', '.bt-hq2-shell .bt-hq2-logo', '/', { when: shellWide }),
  nav('rail: Operations', '.bt-hq2-nav a[href="/ops"]', '/ops'),
  nav('rail: HQ', '.bt-hq2-nav a[href="/"]', '/'),
  nav('rail: Floor', '.bt-hq2-nav a[href="/floor"]', '/floor'),
  nav('rail: Positions', '.bt-hq2-nav a[href="/positions"]', '/positions'),
  nav('rail: Economics', '.bt-hq2-nav a[href="/profitability"]', '/profitability'),
  nav('rail: Company', '.bt-hq2-nav a[href="/company"]', '/company'),
];
const PULSE = [{ name: 'Company Pulse', sel: '#hq5-pulse', expect: { visible: '#hq5-pulse-pop' }, close: { sel: '#hq5-pulse', expect: { hidden: '#hq5-pulse-pop' } } }];
const OPS_HDR = [nav('header: incidents', '#ops-h-inc', '/ops'), nav('header: operations desk', '#ops-hdr .ops-h-desk', '/ops', { when: shellWide })];
const SHELL = RAIL.concat(PULSE, OPS_HDR);
const AGENT_NAV = [nav('agents: COMMAND', 'body > nav a.brand', '/'), nav('agents: Trading floor', 'body > nav a.floor', '/floor')]
  .concat(AGENTS.map(a => nav('agents: ' + a, 'body > nav a[data-agent="' + a + '"]', '/' + a)));
const WS_TABS = [
  { name: 'tab: Desk & conversation', sel: '#tab-desk', expect: { self: ['aria-selected', 'true'], hidden: '#ws-root' } },
  { name: 'tab: Workspace', sel: '#tab-ws', expect: { self: ['aria-selected', 'true'], visible: '#ws-root' } },
];
// agent.html: derek / xavier / audrey / karen have the workspace AND the
// classic desk (two tabs); allocator the workspace only; archer / scout their
// framed page under the floor strip
const TABBED = ['derek', 'xavier', 'audrey', 'karen'];
// Talk opens the conversation: the desk tab (two-tab agents) or the framed
// page brought into view with the live strip still above it (archer, scout).
// Allie has no conversation desk: her page draws no Talk (hq2-agent.js)
const agentControls = a => AGENT_NAV.concat(TABBED.includes(a) ? WS_TABS : [],
  a === 'allocator' ? [] : [{ name: 'Talk to the agent', sel: '#bt-agent2-talk',
    expect: TABBED.includes(a) ? { attr: ['#tab-desk', 'aria-selected', 'true'] } : { inView: '#page', visible: '#ws-root .wsx-strip' } }],
  [nav('Back to floor', '.bt-agent2-actions a[href="/floor"]', '/floor')], SHELL);
const HQ_VIEWS = ['command', 'floor', 'markets', 'capital', 'reports'];
const CONTROLS = {
  command: [
    // a desk opens from the team list's rows; the 3D stage's desk tags are
    // labels that move with the camera (measured as the stage layer's
    // children: LAYER CHILDREN), never a fixed place to tap
    { name: 'desk panel', sel: '[data-render="team"] [data-desk]', optional: true,
      expect: { bodyClass: 'desk-open', visible: '#hq-desk' }, close: { sel: '#hq-desk [data-close-desk]', expect: { notBodyClass: 'desk-open' } } },
    { name: 'Briefing (CRITICAL bar)', sel: '#hq-alert [data-go="reports"]', optional: true, expect: { bodyAttr: ['data-view', 'reports'] } },
  ].concat(HQ_VIEWS.map(v => ({ name: 'view: ' + v, sel: '#hq-nav button[data-go="' + v + '"], #hq-tabbar button[data-go="' + v + '"]',
    // hq.js: in the pocket layout Floor opens the /floor room
    expect: vp => (pocket(vp) && v === 'floor') ? { nav: '/floor' } : { bodyAttr: ['data-view', v], self: ['aria-current', 'page'] } })),
  [{ name: 'brand: Command home', sel: '#hq-top .hq-brand', expect: { bodyAttr: ['data-view', 'command'] } },
    nav('Trader launcher', '.bt-trader-launch', '/trader')]),
  // the real floor (meeting-release.js: body.meeting-real-floor) hides the old
  // .fl-top header on every device (hq5-workspace.css); the shell's page bar
  // and rail replace it and the view bar holds Overview and the floor map
  floor: [
    { name: 'agent panel', sel: '#roster .fl-agent', optional: true, expect: { visible: '#fl-panel' }, close: { sel: '#fl-panel .fl-x', expect: { hidden: '#fl-panel' } } },
  ].concat(['capital', 'opps', 'health', 'active'].map(v => ({ name: 'floor view: ' + v, sel: '#hq5-floorbar .hq5-view[data-v="' + v + '"]', expect: { selfClass: 'on' } })),
  [{ name: 'floor view: map', sel: '#hq5-floorbar .hq5-view[data-v="map"]', expect: { selfClass: 'on', visible: '#fl-map' } },
    { name: 'floor view: overview', sel: '#hq5-floorbar .hq5-view[data-v="overview"]', expect: { selfClass: 'on' } }], SHELL),
  trader: [
    { name: 'evidence dialog', sel: '#positions .card-actions [data-evidence]', optional: true, expect: { dialogOpen: '#evidence-dialog' }, close: { sel: '#close-evidence', expect: { dialogClosed: '#evidence-dialog' } } },
    { name: 'focus a position', sel: '#positions .card-focus', optional: true, expect: { visible: '#positions .position-card.focused' } },
    // the broadcast shows a position: with none on the wall it has nothing to open
    { name: 'Broadcast', sel: 'body:has(#positions .position-card) #bt-v4-trader-rail [data-v4-open-broadcast]', optional: true, expect: { bodyClass: 'bt-v4-broadcast-open' }, close: { sel: '#bt-v4-broadcast [data-v4-close]', expect: { notBodyClass: 'bt-v4-broadcast-open' } } },
    { name: 'view: Standing orders', sel: '.view-tabs [data-view="orders"]', expect: { self: ['aria-pressed', 'true'], visible: '#positions .orders-view' } },
    { name: 'view: Position wall', sel: '.view-tabs [data-view="positions"]', expect: { self: ['aria-pressed', 'true'], hidden: '#positions .orders-view' } },
  ].concat(['near', 'blocked', 'settlement', 'all'].map(f => ({ name: 'filter: ' + f, sel: '.filters [data-filter="' + f + '"]', expect: { self: ['aria-pressed', 'true'] } })),
  // experience-v4.js docks the quick-lane rail into the toolbar above 760 px,
  // where its filters (the toolbar's own) are not drawn
  [{ name: 'rail filter: near', sel: '#bt-v4-trader-rail [data-v4-filter="near"]', expect: { attr: ['.filters [data-filter="near"]', 'aria-pressed', 'true'] }, when: phoneW },
    { name: 'rail filter: all', sel: '#bt-v4-trader-rail [data-v4-filter="all"]', expect: { attr: ['.filters [data-filter="all"]', 'aria-pressed', 'true'] }, when: phoneW },
    { name: 'search', sel: '#search', action: 'fill:no-such-position-zz', expect: { visible: '#positions .empty-state' } },
    { name: 'search cleared', sel: '#search', action: 'fill:', expect: { attr: ['#search', 'value', null] } },
    { name: 'Follow Xavier', sel: '#follow', expect: { self: ['aria-pressed', 'true'] } },
    { name: 'Reduce motion', sel: '#motion', expect: { self: ['aria-pressed', 'true'] }, when: traderAbove(900) },
    { name: 'Wall mode', sel: '#wall', expect: { self: ['aria-pressed', 'true'], bodyClass: 'wall-mode' } },
    { name: 'Wall mode off', sel: '#wall', expect: { self: ['aria-pressed', 'false'], notBodyClass: 'wall-mode' } },
    { name: 'notification sound', sel: '#sound', expect: { self: ['aria-pressed', 'true'] } },
    { name: 'full screen', sel: '#fullscreen', expect: { any: [{ fullscreen: true }, { visible: '#toast.show' }] }, when: traderAbove(600) },
    nav('wordmark: BETTOR Command', '.topbar .wordmark', ['/index.html', '/']),
    nav('nav: Command', '.topbar nav a[href="index.html"]', ['/index.html', '/'], { when: traderAbove(900) }),
    nav('nav: Floor', '.topbar nav a[href="index.html#floor"]', ['/index.html', '/'], { when: traderAbove(600) }),
    nav('nav: Trader mode', '.topbar nav a[href="trader.html"]', ['/trader.html', '/trader'])]),
  ops: ['funnel', 'coverage', 'opportunities', 'blotter', 'agents', 'refusals', 'pinnapi', 'capital', 'incidents']
    .map(id => ({ name: 'jump: ' + id, sel: '.od-jump a[href="#' + id + '"]', expect: { hash: '#' + id, inView: '#' + id } }))
    // the table is redrawn on a sort: the header is read afresh
    .concat([{ name: 'sort coverage by health', sel: 'th[data-sort="health"]', optional: true, expect: { any: [{ attr: ['th[data-sort="health"]', 'aria-sort', 'ascending'] }, { attr: ['th[data-sort="health"]', 'aria-sort', 'descending'] }] } }],
      RAIL, PULSE, [{ name: 'header: incidents', sel: '#ops-h-inc', expect: { hash: '#incidents' } }]),
  positions: [
    nav('bar: Position rooms', '.pr-bar .pr-brand', '/'), nav('bar: Command', '.pr-nav a[href="/"]', '/'),
    nav('bar: Xavier', '.pr-nav a[href="/xavier"]', '/xavier'), nav('bar: All positions', '.pr-nav a[data-nav="list"]', '/positions'),
    nav('bar: Legacy mirror', '.pr-nav a[href="live.html"]', '/live.html'),
    { name: 'Refresh', sel: '#pr-refresh', expect: { request: '/api/command/positions/' } },
    nav('book: Paper', '.pr-tab[data-book="PAPER"]', '/positions'), nav('book: Actual', '.pr-tab[data-book="ACTUAL"]', '/positions?book=ACTUAL'),
    nav('open a room', '#pr-view a.pr-card', '/position', { optional: true }),
  ].concat(SHELL),
  room: [
    nav('bar: Position rooms', '.pr-bar .pr-brand', '/'), nav('bar: Command', '.pr-nav a[href="/"]', '/'),
    nav('bar: Xavier', '.pr-nav a[href="/xavier"]', '/xavier'), nav('bar: All positions', '.pr-nav a[data-nav="list"]', '/positions'),
    { name: 'Refresh', sel: '#pr-refresh', expect: { request: '/api/command/positions/' } },
  ].concat(SHELL),
};
AGENTS.forEach(a => { CONTROLS[a] = agentControls(a); });
// /eddie is Archer's page at his historical address (ALIAS_AGENTS): his controls
CONTROLS.eddie = agentControls('archer');
// the verdict on one control from what was observed after the tap: obs holds
// each DOM question asked (observe), navs the paths of the navigations it
// started, requests the API paths it read; [] = landed where expected
function verdict(expect, obs, navs, requests) {
  const fails = [];
  const keys = Object.keys(expect || {});
  if (!keys.length) return ['NO_EXPECTATION'];
  for (const k of keys) {
    const v = expect[k];
    if (k === 'any') { if (!v.some(e => verdict(e, obs, navs, requests).length === 0)) fails.push('NONE_OF_ANY'); continue; }
    if (k === 'nav') {
      const want = [].concat(v);
      if (!navs.some(u => want.some(w => (w.includes('?') ? u === w : u.split('?')[0] === w)))) fails.push('NO_NAVIGATION_TO:' + want.join('|') + (navs.length ? ' got ' + navs.join(',') : ''));
      continue;
    }
    if (k === 'request') { if (!requests.some(u => u.startsWith(v))) fails.push('NO_REQUEST:' + v); continue; }
    if (obs[k + ':' + JSON.stringify(v)] !== true) fails.push(k.toUpperCase() + '_NOT_MET:' + JSON.stringify(v));
  }
  return fails;
}
// where a tap landed (read in the page after the touch). The page records
// at pointerdown -- the moment the touch lands -- the element it reached and,
// when that element lies inside a match of the control's selector, that
// match's index among all matches and its box (__previewTapSeen). On the
// control: OK. On the same control redrawn between the hit test and the touch
// (a poll re-rendering the team list or the roster replaces every row): the
// same selector, the same index, the same box (2 px) -- REDRAWN, the touch
// reached the control and its state is then read from the replacement.
// Anything else is the element the touch reached instead (a failure).
// twin: { sel, idx, box } of the control at the hit test, or null (strict)
function landing(el, twin) {
  const t = window.__previewTapTarget, seen = window.__previewTapSeen;
  if (!t) return 'NONE';
  if (el === t || el.contains(t) || t.contains(el)) return 'OK';
  if (twin && seen && seen.idx === twin.idx && [0, 1, 2, 3].every(k => Math.abs(seen.box[k] - twin.box[k]) < 2)) return 'REDRAWN';
  return t.id ? '#' + t.id : t.tagName.toLowerCase();
}
// <<< CONTROLS

// the DOM questions an expectation asks, answered in the page; el is the
// control (locator / handle evaluate), so 'self' reads the tapped element
function observe(el, expect) {
  const out = {};
  const vis = (e) => { if (!e || (e.checkVisibility && !e.checkVisibility())) return false; const s = getComputedStyle(e); const r = e.getBoundingClientRect();
    return !e.closest('[hidden]') && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };
  const q = (s) => document.querySelector(s);
  // on screen and not under a fixed bar: a point just inside the box's top
  // edge must hit the box (a jump that lands its heading under the header fails)
  const clear = (t) => { const r = t.getBoundingClientRect(); if (!(r.top < innerHeight && r.bottom > 0)) return false;
    const x = Math.min(Math.max(r.left + Math.min(24, r.width / 2), 1), innerWidth - 2), y = Math.min(Math.max(r.top + Math.min(12, r.height / 2), 1), innerHeight - 2);
    const h = document.elementFromPoint(x, y); return !!h && (h === t || t.contains(h)); };
  const ask = (e) => Object.keys(e).forEach((k) => {
    const v = e[k];
    if (k === 'any') { v.forEach(ask); return; }
    let ok;
    if (k === 'hash') ok = location.hash === v;
    else if (k === 'inView') { const t = q(v); ok = !!t && clear(t); }
    else if (k === 'visible') ok = [...document.querySelectorAll(v)].some(vis);
    else if (k === 'hidden') ok = ![...document.querySelectorAll(v)].some(vis);
    else if (k === 'attr') { const t = q(v[0]); ok = !!t && (v[1] === 'value' ? (t.value || null) === v[2] : t.getAttribute(v[1]) === v[2]); }
    else if (k === 'self') ok = !!el && el.getAttribute(v[0]) === v[1];
    else if (k === 'selfClass') ok = !!el && el.classList.contains(v);
    else if (k === 'bodyAttr') ok = document.body.getAttribute(v[0]) === v[1];
    else if (k === 'bodyClass') ok = document.body.classList.contains(v);
    else if (k === 'notBodyClass') ok = !document.body.classList.contains(v);
    else if (k === 'dialogOpen') { const t = q(v); ok = !!t && t.open === true; }
    else if (k === 'dialogClosed') { const t = q(v); ok = !!t && t.open === false; }
    else if (k === 'fullscreen') ok = !!document.fullscreenElement === v;
    else return;
    out[k + ':' + JSON.stringify(v)] = ok;
  });
  ask(expect || {});
  return out;
}

async function measure(page, touch, insets) {
  return page.evaluate(({ touch, suspectSrc, insets }) => {
    // checkVisibility(): a box inside a closed <details> or a content-visibility
    // hidden subtree keeps a layout box but is not rendered (Chromium; the
    // tap layer agrees: Playwright calls it not visible)
    const vis = (el) => { if (el.checkVisibility && !el.checkVisibility()) return false; const s = el.ownerDocument.defaultView.getComputedStyle(el); const r = el.getBoundingClientRect();
      return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05 && r.width > 0 && r.height > 0; };
    const vw = innerWidth, vh = innerHeight;
    const canvases = [...document.querySelectorAll('canvas')].filter(vis).map(c => {
      // three.js stamps its WebGL canvases; probing getContext() would create one
      const gl = c.getAttribute('data-engine') || null;
      const r = c.getBoundingClientRect();
      const shown = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      return { gl, w: Math.round(r.width), h: Math.round(r.height), viewport_share: +(shown / (vw * vh)).toFixed(3) };
    });
    const idOf = (el) => (el.id ? '#' + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
    // >>> PUBLIC LABEL (pinned by backend/tests/js/frontend-preview-public-artifact.test.cjs)
    // A control or a text box is named in preview.json by its STRUCTURE -- tag,
    // id, two classes, the page's UI enum attributes, a link's path (a room
    // path's key masked) -- never by its text or its aria-label: what a page
    // draws can come from the paper book, and preview.json is public. The RC5
    // production device run (pm-acceptance 37836393458) wrote a focused Trader
    // card's market slug as current_workspace text: the ribbon item's
    // aria-current="true" button reads the card's market title
    // (experience-v4.js); its position id sits in data-v4-ribbon, which is no
    // UI enum and is never read here.
    const UI_ATTRS = ['data-go', 'data-v', 'data-view', 'data-filter', 'data-book', 'data-agent', 'data-desk', 'data-nav', 'data-sort', 'role'];
    const labelOf = (el) => {
      const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      const attrs = UI_ATTRS.filter(a => el.hasAttribute(a)).map(a => '[' + a + '=' + String(el.getAttribute(a)).replace(/[^\w-]/g, '').slice(0, 24) + ']').join('');
      const href = el.tagName === 'A' ? el.getAttribute('href') : null;
      let at = '';
      if (href) { try { at = '[path=' + new URL(href, location.href).pathname.replace(/\/positions\/room\/[^/]*/g, '/positions/room/:key') + ']'; } catch (e) { at = '[path=?]'; } }
      return (el.id ? '#' + el.id : el.tagName.toLowerCase()) + cls + attrs + at;
    };
    // <<< PUBLIC LABEL
    // the outermost fixed / sticky box holding el (el itself included), or null
    const fixedOf = (el) => { let top = null; for (let a = el; a && a !== document.body && a !== document.documentElement; a = a.parentElement) { const q = getComputedStyle(a).position; if (q === 'fixed' || q === 'sticky') top = a; } return top; };
    const fixed = [...document.querySelectorAll('body *')].filter(el => {
      const p = getComputedStyle(el).position; if (p !== 'fixed' && p !== 'sticky') return false;
      if (!vis(el)) return false;
      let a = el.parentElement; while (a && a !== document.body) { const q = getComputedStyle(a).position; if (q === 'fixed' || q === 'sticky') return false; a = a.parentElement; }
      return true; }).map(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        const shown = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        return { el, r, share: +(shown / (vw * vh)).toFixed(3), z: s.zIndex, pe: s.pointerEvents,
          id: (el.id ? '#' + el.id : el.tagName.toLowerCase()) + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '') }; });
    // >>> BAR PAIRING (pinned by backend/tests/js/frontend-preview-bars.test.cjs)
    // Two fixed / sticky bars that overlap hide each other's content. A
    // FULL-VIEWPORT BACKGROUND LAYER is not a bar: a box showing on >= 90 %
    // of the viewport that paints BENEATH the other box of a pair is the
    // scene the bars float over and cannot cover them. Production device run
    // 37834226533: every Command overlap on desktop / iPad paired #hq-stage
    // (the WebGL headquarters canvas, 100 % of the viewport, z-index 0) or
    // #hq-tags (its desk-label overlay, 100 %, z-index 8, pointer-events
    // none) with a HUD bar at z-index 9-22 above it. Such a pair is not an
    // overlap; the layer is listed in background_layers with every box it
    // sits beneath, so nothing excluded goes unrecorded. A full-viewport box
    // painted ABOVE another box, and any two boxes under 90 %, still overlap.
    const BG_SHARE = 0.9;
    // stacking order: walk each box's chain of stacking contexts from the
    // root; where the chains part, the lower z-index paints beneath (auto =
    // 0) and on a tie the earlier in the document (CSS 2.1 appendix E)
    const formsContext = (el) => { const s = getComputedStyle(el);
      if (s.position === 'fixed' || s.position === 'sticky') return true;
      if (s.zIndex !== 'auto' && (s.position !== 'static' || (el.parentElement && /flex|grid/.test(getComputedStyle(el.parentElement).display)))) return true;
      return Number(s.opacity) < 1 || s.transform !== 'none' || s.filter !== 'none' || (s.backdropFilter || 'none') !== 'none'
        || (s.perspective || 'none') !== 'none' || (s.clipPath || 'none') !== 'none' || (s.maskImage || 'none') !== 'none'
        || s.isolation === 'isolate' || (s.mixBlendMode || 'normal') !== 'normal'
        || /transform|opacity|filter|perspective/.test(s.willChange || '') || /paint|layout|strict|content/.test(s.contain || ''); };
    const contexts = (el) => { const c = []; for (let n = el; n && n !== document.documentElement; n = n.parentElement) if (n === el || formsContext(n)) c.unshift(n); return c; };
    const zOf = (el) => { const z = parseInt(getComputedStyle(el).zIndex, 10); return Number.isFinite(z) ? z : 0; };
    const paintsBeneath = (x, y) => { const cx = contexts(x), cy = contexts(y); let k = 0;
      while (k < cx.length && k < cy.length && cx[k] === cy[k]) k++;
      if (k === cx.length || k === cy.length) return k === cx.length;
      const zx = zOf(cx[k]), zy = zOf(cy[k]); if (zx !== zy) return zx < zy;
      return !!(cx[k].compareDocumentPosition(cy[k]) & Node.DOCUMENT_POSITION_FOLLOWING); };
    // bars: [{id, r, share, z, pe}]; nested(i, j): one box holds the other;
    // beneath(i, j): box i paints beneath box j
    const pairBars = (bars, nested, beneath) => {
      const overlaps = [], layers = {};
      for (let i = 0; i < bars.length; i++) for (let j = i + 1; j < bars.length; j++) {
        if (nested(i, j)) continue;
        const a = bars[i].r, b = bars[j].r;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (!(w > 2 && h > 2)) continue;
        const lo = beneath(i, j) ? i : j, hi = lo === i ? j : i;
        if (bars[lo].share >= BG_SHARE) {
          const L = layers[lo] || (layers[lo] = { id: bars[lo].id, viewport_share: bars[lo].share, z_index: bars[lo].z, pointer_events: bars[lo].pe, beneath: [] });
          L.beneath.push(bars[hi].id);
          continue;
        }
        overlaps.push({ a: bars[i].id, b: bars[j].id, area_px: Math.round(w * h) });
      }
      return { overlaps, background_layers: Object.keys(layers).map(k => layers[k]) };
    };
    // <<< BAR PAIRING
    const paired = pairBars(fixed, (i, j) => fixed[i].el.contains(fixed[j].el) || fixed[j].el.contains(fixed[i].el),
      (i, j) => paintsBeneath(fixed[i].el, fixed[j].el));
    // >>> LAYER CHILDREN (pinned by backend/tests/js/frontend-preview-layers.test.cjs)
    // A background layer's own CONTROLS are UI, not scene: #hq-tags is
    // pointer-events none, but every desk tag in it is a button (pointer-
    // events auto) a finger or a mouse must reach. A tag that runs under a bar
    // painted above its layer is half hidden and its covered part takes no
    // tap, so each child control that intersects a bar its layer sits beneath
    // is an overlap like any other (a: the child, b: the bar).
    // children: [{id, r}]; above: [{id, r}]
    const layerChildOverlaps = (children, above) => {
      const out = [];
      for (const c of children) for (const b of above) {
        const w = Math.min(c.r.right, b.r.right) - Math.max(c.r.left, b.r.left);
        const h = Math.min(c.r.bottom, b.r.bottom) - Math.max(c.r.top, b.r.top);
        if (w > 2 && h > 2) out.push({ a: c.id, b: b.id, area_px: Math.round(w * h) });
      }
      return out;
    };
    // <<< LAYER CHILDREN
    const CONTROL_SEL = 'a[href],button,[role=button],[role=tab],input,select,summary,[tabindex]:not([tabindex="-1"])';
    const layerEvidence = [], layerOverlaps = [];
    for (const L of paired.background_layers) {
      const box = fixed.find(f => f.id === L.id); if (!box) continue;
      const kids = [...box.el.querySelectorAll(CONTROL_SEL)].filter(e => vis(e) && getComputedStyle(e).pointerEvents !== 'none')
        .map(e => ({ id: L.id + ' > ' + idOf(e) + (e.dataset && e.dataset.desk ? '[data-desk=' + e.dataset.desk + ']' : ''), r: e.getBoundingClientRect() }));
      const above = fixed.filter(f => L.beneath.includes(f.id));
      layerOverlaps.push(...layerChildOverlaps(kids, above));
      layerEvidence.push({ id: L.id, holds_canvas: box.el.tagName === 'CANVAS' || !!box.el.querySelector('canvas'), child_controls: kids.length });
    }
    const overlaps = paired.overlaps.concat(layerOverlaps);
    const CONTROL_SEL_SMALL = CONTROL_SEL;
    const small = [];
    const scanSmall = (doc, dx, dy, where) => {
      for (const el of doc.querySelectorAll(CONTROL_SEL_SMALL)) {
        if (!vis(el)) continue; const q = el.getBoundingClientRect();
        const r = { left: q.left + dx, top: q.top + dy, right: q.right + dx, bottom: q.bottom + dy, width: q.width, height: q.height };
        if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
        if (r.width < 44 || r.height < 44) small.push({ el: labelOf(el), w: Math.round(r.width), h: Math.round(r.height), where });
      }
    };
    // a framed page (the agent desks) is part of the screen a finger uses
    const frames = [];
    for (const f of document.querySelectorAll('iframe')) {
      if (!vis(f)) continue; let doc = null; try { doc = f.contentDocument; } catch (e) { doc = null; }
      const fr = f.getBoundingClientRect();
      frames.push({ title: f.title || null, same_origin: !!doc, controls: doc ? doc.querySelectorAll(CONTROL_SEL).length : null });
      if (touch && doc) scanSmall(doc, fr.left, fr.top, 'frame');
    }
    if (touch) scanSmall(document, 0, 0, 'page');
    // >>> REACHABILITY (pinned by backend/tests/js/frontend-preview-reach.test.cjs)
    // A control on screen at rest must take a tap at its centre. The visible
    // part of a control is its box cut by the viewport and by every ancestor
    // that clips (overflow other than visible); a control mostly scrolled out
    // of a scrolling strip is reached by scrolling, not here. At the centre of
    // the visible part the topmost box must be the control, inside it, or an
    // ancestor of it. Anything else COVERS it, unless the cover belongs to a
    // fixed / sticky bar anchored in the lower half of the screen with room
    // left to scroll the control up out from under it (a bottom tab bar over
    // the content beneath the fold). A bar in the upper half (a page bar, a
    // status strip) can never be scrolled away at rest: scrolling only moves
    // the content further under it.
    // clips: [{left, top, right, bottom, scrolls}]
    const visiblePart = (r, clips, vw, vh) => {
      let v = { left: Math.max(0, r.left), top: Math.max(0, r.top), right: Math.min(vw, r.right), bottom: Math.min(vh, r.bottom) };
      let scroller = false;
      for (const c of clips) { v = { left: Math.max(v.left, c.left), top: Math.max(v.top, c.top), right: Math.min(v.right, c.right), bottom: Math.min(v.bottom, c.bottom) }; if (c.scrolls) scroller = true; }
      const area = Math.max(0, v.right - v.left) * Math.max(0, v.bottom - v.top), full = Math.max(1, (r.right - r.left) * (r.bottom - r.top));
      return { v, share: area / full, scroller, empty: !(v.right - v.left > 1 && v.bottom - v.top > 1) };
    };
    // hit: 'self' | 'cover'; cover: {fixed, top, bottom} of the covering
    // bar; room: px the document can still scroll down; vh: viewport height
    const reachVerdict = (r, part, hit, cover, room, vh) => {
      if (part.empty || (part.scroller && part.share < 0.5)) return 'OFFSCREEN';
      if (hit === 'self') return 'REACHED';
      if (cover && cover.fixed && (cover.top + cover.bottom) / 2 > vh / 2 && room >= r.bottom - cover.top) return 'SCROLL_REVEALS';
      return 'COVERED';
    };
    // <<< REACHABILITY
    const clipsOf = (el) => { const out = [];
      for (let a = el.parentElement; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
        const s = getComputedStyle(a);
        if (/(auto|scroll|hidden|clip)/.test(s.overflowX + ' ' + s.overflowY)) { const q = a.getBoundingClientRect();
          out.push({ left: q.left, top: q.top, right: q.right, bottom: q.bottom, scrolls: /(auto|scroll)/.test(s.overflowX + ' ' + s.overflowY) && (a.scrollWidth > a.clientWidth + 1 || a.scrollHeight > a.clientHeight + 1) }); }
        if (s.position === 'fixed') break;
      }
      return out; };
    const room = Math.max(0, document.documentElement.scrollHeight - innerHeight - scrollY);
    const unreachable = []; let reached = 0;
    for (const el of document.querySelectorAll(CONTROL_SEL)) {
      if (!vis(el)) continue; const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
      const part = visiblePart(r, clipsOf(el), vw, vh);
      if (part.empty) continue;
      const x = (part.v.left + part.v.right) / 2, y = (part.v.top + part.v.bottom) / 2;
      const h = document.elementFromPoint(x, y);
      const self = !!h && (h === el || el.contains(h) || h.contains(el));
      let cover = null;
      if (!self && h) { const bar = fixedOf(h); const br = bar && bar.getBoundingClientRect(); cover = bar ? { fixed: true, top: br.top, bottom: br.bottom, id: idOf(bar) } : { fixed: false, top: -1, bottom: -1, id: idOf(h) }; }
      const v = reachVerdict(r, part, self ? 'self' : 'cover', cover, room, vh);
      if (v === 'REACHED') reached++;
      if (v === 'COVERED') unreachable.push({ el: labelOf(el), by: h ? idOf(h) : null, bar: cover && cover.fixed ? cover.id : null, at: [Math.round(x), Math.round(y)] });
    }
    // >>> SAFE AREA (pinned by backend/tests/js/frontend-preview-safe-area.test.cjs)
    // insets: {top, right, bottom, left}; boxes: [{id, r, fixed}]. Text and
    // controls INSIDE a pinned bar (fixed, or sticky and stuck) must lie inside the safe rectangle
    // (the bar's own paint may run under the notch, its content may not).
    // In-flow content must keep clear of the side insets (the landscape notch)
    // and, at rest (scroll 0), of the top inset (the installed app's status
    // bar); in-flow content under the bottom inset is passed by scrolling. A
    // box is its visible part (cut by clipping ancestors); 1 px is tolerated.
    const safeAreaViolations = (boxes, S, vw, vh) => {
      const out = [];
      for (const b of boxes) {
        const r = b.r;
        if (r.right <= 0 || r.left >= vw || r.bottom <= 0 || r.top >= vh) continue;
        const edges = [];
        if (S.top > 0 && S.top - r.top > 1) edges.push(['top', S.top - r.top]);
        if (b.fixed && S.bottom > 0 && r.bottom - (vh - S.bottom) > 1) edges.push(['bottom', r.bottom - (vh - S.bottom)]);
        if (S.left > 0 && S.left - r.left > 1) edges.push(['left', S.left - r.left]);
        if (S.right > 0 && r.right - (vw - S.right) > 1) edges.push(['right', r.right - (vw - S.right)]);
        for (const [edge, px] of edges) out.push({ id: b.id, edge, px: Math.round(px) });
      }
      return out;
    };
    // <<< SAFE AREA
    let safeArea = null;
    // pinned to the screen right now: fixed, or sticky and currently stuck at
    // its offset (a sticky box at rest scrolls with the page like any other)
    const pinnedOf = (el) => { let top = null;
      for (let a = el; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
        const s = getComputedStyle(a);
        if (s.position === 'fixed') top = a;
        else if (s.position === 'sticky') { const r = a.getBoundingClientRect(), t = parseFloat(s.top), b = parseFloat(s.bottom);
          if ((Number.isFinite(t) && Math.abs(r.top - t) < 1) || (Number.isFinite(b) && Math.abs(innerHeight - r.bottom - b) < 1)) top = a; }
      }
      return top; };
    if (insets) {
      const boxes = [];
      const textOwn = (el) => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      const layerEls = paired.background_layers.map(L => (fixed.find(f => f.id === L.id) || {}).el).filter(Boolean);
      for (const el of document.querySelectorAll('body *')) {
        const control = el.matches(CONTROL_SEL);
        if (!(control || textOwn(el))) continue;
        // the scene behind the HUD is not content; a control inside it is
        if (layerEls.some(L => L === el || (L.contains(el) && !control && !el.closest(CONTROL_SEL)))) continue;
        if (!vis(el)) continue; const r = el.getBoundingClientRect();
        if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
        const part = visiblePart(r, clipsOf(el), vw, vh); if (part.empty) continue;
        const bar = pinnedOf(el);
        if (!bar) { // in-flow content covered at rest is REACHABILITY's finding, not this one
          const h = document.elementFromPoint((part.v.left + part.v.right) / 2, (part.v.top + part.v.bottom) / 2);
          if (h && !(h === el || el.contains(h) || h.contains(el))) continue; }
        boxes.push({ id: (bar ? idOf(bar) + ' > ' : '') + labelOf(el), r: part.v, fixed: !!bar });
      }
      const v = safeAreaViolations(boxes, insets, vw, vh);
      // what the page itself reads for env(safe-area-inset-*): proof the insets were applied
      const probe = document.createElement('div');
      probe.style.cssText = 'position:fixed;top:env(safe-area-inset-top,0px);left:env(safe-area-inset-left,0px);width:1px;height:1px;visibility:hidden';
      document.body.appendChild(probe); const pr = probe.getBoundingClientRect(); probe.remove();
      safeArea = { insets, env_read: { top: Math.round(pr.top), left: Math.round(pr.left) }, checked: boxes.length, violations: v.length, first: v.slice(0, 12) };
    }
    const text = (document.body && document.body.innerText) || '';
    const cur = [...document.querySelectorAll('[aria-current]')].filter(vis).map(labelOf);
    const suspect = text.match(new RegExp(suspectSrc, 'ig')) || [];
    return {
      title: document.title,
      // no sign-in step on screen: unlock.js's card, or a page's own
      // SIGN-IN REQUIRED state (agent.html); the API's 401 / 403 count is
      // added by the caller
      signed_in: !document.getElementById('command-unlock') && !/\bSIGN-IN REQUIRED\b/.test(text),
      // against the initial containing block (clientWidth), not innerWidth: on
      // a phone / tablet a page wider than the screen widens the LAYOUT
      // viewport to fit it, so scrollWidth - innerWidth reads 0 for a page
      // that scrolls sideways (local preview, iPad landscape /derek: scrollWidth
      // 1389, innerWidth 1389, clientWidth 1180). On desktop both read alike.
      overflow_px: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      layout_viewport_px: innerWidth,
      reduced_motion: matchMedia('(prefers-reduced-motion: reduce)').matches,
      canvases, webgl_canvases: canvases.filter(c => c.gl).length,
      largest_webgl_share: Math.max(0, ...canvases.filter(c => c.gl).map(c => c.viewport_share)),
      fixed_bars: fixed.length, fixed_overlaps: overlaps.slice(0, 12), background_layers: paired.background_layers,
      background_layer_evidence: layerEvidence, layer_child_overlaps: layerOverlaps.length,
      touch_targets_under_44: small.length, touch_targets_under_44_first: small.slice(0, 12), frames,
      controls_on_screen_reached: reached, unreachable_controls: unreachable.length, unreachable_controls_first: unreachable.slice(0, 12),
      safe_area: safeArea,
      paper_mentions: (text.match(/\bPAPER\b/g) || []).length, shadow_mentions: (text.match(/\bSHADOW\b/g) || []).length,
      current_workspace: cur.slice(0, 4), suspect_words: [...new Set(suspect.map(s => s.toLowerCase()))],
      text_chars: text.length,
      safe_area_css: [...document.styleSheets].some(s => { try { return [...s.cssRules].some(r => /safe-area-inset/.test(r.cssText)); } catch (e) { return false; } }),
    };
  }, { touch, suspectSrc: SUSPECT.source, insets: insets || null });
}

// A TAP AS A FINGER MAKES IT: the control is brought to the middle of the
// screen (as a user scrolls it into reach; a control pinned under a bar at the
// top of the document cannot be brought out), a hit test at the centre of its
// visible part must find the control itself (not a box over it), and only then
// is a real touch (a click on desktop) sent at that point. Playwright's own
// actionability check waits for two identical animation frames, which a
// software-WebGL page painting at 0-1 fps (production device runs: Command at
// 0 fps) never delivers inside a timeout; the hit test is the part of it that
// matters here, and it is kept.
async function tapAt(page, handle, touch, twin) {
  // a page that redraws (a poll re-rendering a panel, a strip growing a row)
  // can move a box for a moment: the hit test is retried for up to ~2 s; a
  // cover that stays is a finding. A touch that was delivered and landed on
  // something else is final: it is never sent again (the first touch may
  // have changed the page)
  let last = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    if (attempt) await page.waitForTimeout(500);
    try { return await tapOnce(page, handle, touch, twin); } catch (e) { last = e; if (e.delivered || /CONTROL_DETACHED|Element is not attached/.test(String(e))) break; }
  }
  throw last;
}
async function tapOnce(page, handle, touch, twin) {
  const at = await handle.evaluate((el, twin) => {
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    const r = el.getBoundingClientRect();
    let v = { left: Math.max(0, r.left), top: Math.max(0, r.top), right: Math.min(innerWidth, r.right), bottom: Math.min(innerHeight, r.bottom) };
    for (let a = el.parentElement; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX + ' ' + s.overflowY)) { const q = a.getBoundingClientRect(); v = { left: Math.max(v.left, q.left), top: Math.max(v.top, q.top), right: Math.min(v.right, q.right), bottom: Math.min(v.bottom, q.bottom) }; }
      if (s.position === 'fixed') break;
    }
    if (!(v.right - v.left > 1 && v.bottom - v.top > 1)) return { error: 'NOT_ON_SCREEN' };
    const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2, h = document.elementFromPoint(x, y);
    if (!h || !(h === el || el.contains(h) || h.contains(el))) {
      const id = h ? (h.id ? '#' + h.id : h.tagName.toLowerCase()) + (typeof h.className === 'string' && h.className.trim() ? '.' + h.className.trim().split(/\s+/).slice(0, 2).join('.') : '') : 'nothing';
      return { error: 'COVERED_BY ' + id };
    }
    // where the pointer actually lands (pointerdown precedes click and any navigation)
    window.__previewTapTarget = null; window.__previewTapSeen = null;
    document.addEventListener('pointerdown', (e) => {
      const t = e.target, m = twin && t.closest ? t.closest(twin.sel) : null, q = m && m.getBoundingClientRect();
      window.__previewTapTarget = t;
      window.__previewTapSeen = m ? { idx: [...document.querySelectorAll(twin.sel)].indexOf(m), box: [q.left, q.top, q.width, q.height] } : null;
    }, { capture: true, once: true });
    // input lands in VISUAL viewport coordinates; a mobile layout viewport can
    // be panned against it (vv.offsetLeft / offsetTop), client coordinates not
    const vv = window.visualViewport || { offsetLeft: 0, offsetTop: 0, scale: 1 };
    return { x, y, vx: (x - vv.offsetLeft) * vv.scale, vy: (y - vv.offsetTop) * vv.scale, w: r.width, h: r.height,
      twin: twin ? { sel: twin.sel, idx: twin.idx, box: [r.left, r.top, r.width, r.height] } : null };
  }, twin || null);
  if (at.error) throw new Error(at.error);
  if (touch) await page.touchscreen.tap(at.vx, at.vy); else await page.mouse.click(at.vx, at.vy);
  const landed = await handle.evaluate(landing, at.twin).catch(() => 'OK');
  // a tap that reached the control's replacement was delivered: it is never
  // sent again (a retry would tap whatever the first tap opened)
  if (landed === 'REDRAWN') at.redrawn = true;
  else if (landed !== 'OK') throw Object.assign(new Error('TAP_LANDED_ON ' + landed), { delivered: true });
  return at;
}

// Recorded in every page before its own scripts: when the page last asked
// for a SMOOTH scroll (scrollIntoView / scrollTo / scrollBy with behavior
// 'smooth'). The call itself goes through unchanged. A smooth scroll only
// advances on painted frames: on a frame-starved page (software GL, 0-1 fps)
// it starts seconds after the request, so stillness alone does not mean rest.
const SMOOTH_MARK = `(() => { const mark = (o) => { if (o && typeof o === 'object' && o.behavior === 'smooth') window.__previewSmoothAt = performance.now(); };
  const siv = Element.prototype.scrollIntoView; Element.prototype.scrollIntoView = function (o) { mark(o); return siv.apply(this, arguments); };
  for (const k of ['scrollTo', 'scrollBy']) { const f = window[k]; window[k] = function (o) { mark(o); return f.apply(this, arguments); }; } })();`;

// tap each declared control and check where it lands (see CONTROLS)
async function runControls(page, capture, pg, d, cdp) {
  const vp = d.viewport, touch = !!d.hasTouch;
  const res = { declared: 0, checked: 0, passed: 0, failed: [], unmeasured: [], not_this_layout: [], overlays: [] };
  const herePath = async () => { const at = () => page.evaluate(() => location.pathname + location.search).catch(() => null);
    const p = await at(); if (p !== null) return p; await page.waitForTimeout(500); return at(); };
  const coarse = () => page.evaluate(() => matchMedia('(pointer:coarse)').matches).catch(() => false);
  const home = await herePath();
  for (const c of CONTROLS[pg] || []) {
    res.declared++;
    // a touch device stays a touch device for every control: a screenshot
    // still finishing in the browser after its timeout resets the touch
    // emulation (the page then reports pointer:fine and drops every touch
    // rule); it is switched back on and counted, and a control is never
    // measured without it
    if (touch && !(await coarse())) {
      if (cdp) await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 }).catch(() => {});
      res.touch_restored = (res.touch_restored || 0) + 1;
      if (!(await coarse())) { res.failed.push({ name: c.name, why: ['TOUCH_EMULATION_LOST'] }); continue; }
    }
    const here = await herePath();
    if (home && here !== home) {
      // the last control left the page outside a tap (a delayed navigation):
      // recorded against it, and the page is loaded again for the next one
      res.failed.push({ name: 'page left before: ' + c.name, why: ['PAGE_LEFT_TO ' + PA.publicPath(here)] });
      await page.goto(new URL(home, page.url()).href, { waitUntil: 'load', timeout: 60000 }).catch(() => {});
      await page.waitForTimeout(5000);
    }
    if (c.when && !c.when(vp)) { res.not_this_layout.push(c.name); continue; }
    const expect = typeof c.expect === 'function' ? c.expect(vp) : c.expect;
    // every control starts from the page at rest. A smooth scroll the last
    // control started is let finish first (Talk brings the agent's frame into
    // view 80 ms after its tap; tapped mid-scroll, the next touch landed on
    // whatever scrolled under the finger -- into the frame on Archer / Scout
    // and landscape Audrey): the page must hold still for 400 ms, and for 3 s
    // after a smooth scroll it asked for (SMOOTH_MARK: on a frame-starved page
    // it began 2.1 s after Talk) -- at most 4 s in all. Then it is scrolled to
    // the top (a finger reaches the next control from there, not from where
    // the last left it)
    await page.evaluate(() => new Promise((res) => { let last = scrollY, still = 0, n = 0;
      const t = setInterval(() => { n++; if (scrollY === last) still++; else { still = 0; last = scrollY; }
        const asked = window.__previewSmoothAt != null && performance.now() - window.__previewSmoothAt < 3000;
        if ((still >= 4 && !asked) || n >= 40) { clearInterval(t); res(); } }, 100); })).catch(() => {});
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })).catch(() => {});
    await page.waitForTimeout(120);
    // the first match a user can get to: shown, or inside a closed <details>
    // whose summary is shown (opened first, as a finger would)
    const find = () => page.evaluate((sel) => { const shown = (e) => { const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return !e.closest('[hidden]') && s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };
      const vis = (e) => shown(e) && (!e.checkVisibility || e.checkVisibility() || (() => { const d = e.closest('details:not([open])'); return !!d && !!d.querySelector('summary') && shown(d.querySelector('summary')); })());
      return [...document.querySelectorAll(sel)].findIndex(vis); }, c.sel).catch(() => -1);
    // a panel being redrawn is waited for (up to ~3 s), never mistaken for an absent control
    let idx = await find();
    for (let k = 0; idx < 0 && k < 6; k++) { await page.waitForTimeout(500); idx = await find(); }
    if (idx < 0) { if (c.optional) res.unmeasured.push(c.name + ': ABSENT (no data shows it)'); else res.failed.push({ name: c.name, why: ['CONTROL_NOT_SHOWN'] }); continue; }
    res.checked++;
    let loc = page.locator(c.sel).nth(idx);
    let handle = await loc.elementHandle({ timeout: 3000 }).catch(() => null);
    const summary = handle ? await handle.evaluateHandle(e => { const d = e.closest('details:not([open])'); return d ? d.querySelector('summary') : null; }).catch(() => null) : null;
    if (summary && await summary.evaluate(x => !!x).catch(() => false)) { await tapAt(page, summary.asElement(), touch).catch(() => {}); await page.waitForTimeout(200); }
    capture.navs.length = 0; capture.requests.length = 0; capture.on = true;
    let why = [], box = null, tapErr = null;
    try {
      if (!handle) throw new Error('CONTROL_DETACHED');
      if (c.action && c.action.startsWith('fill:')) await handle.fill(c.action.slice(5), { timeout: 4000 });
      else box = await tapAt(page, handle, touch, { sel: c.sel, idx });
    } catch (e) { tapErr = e; why.push('NOT_TAPPABLE: ' + String(e.message || e).replace(/\x1b\[[0-9;]*m/g, '').split('\n')[0].slice(0, 200)); }
    // the control left the page (or was hidden) between being found and the
    // touch -- a desk tag the moving camera hid, a row a poll removed -- and
    // no touch was delivered: the first match a finger can get to NOW is
    // tapped instead, once. A control still shown but out of reach, or a
    // touch that landed elsewhere, keeps its failure.
    if (why.length && !box && !(tapErr && tapErr.delivered) && !(c.action && c.action.startsWith('fill:'))) {
      const gone = handle ? await handle.evaluate(e => !e.isConnected || (e.checkVisibility && !e.checkVisibility()) || !e.getBoundingClientRect().width).catch(() => true) : true;
      const again = gone ? await find() : -1;
      if (again >= 0) {
        loc = page.locator(c.sel).nth(again);
        const h2 = await loc.elementHandle({ timeout: 3000 }).catch(() => null);
        if (h2) {
          if (handle) await handle.dispose().catch(() => {});
          handle = h2; why = []; res.retargeted = (res.retargeted || 0) + 1;
          try { box = await tapAt(page, handle, touch, { sel: c.sel, idx: again }); } catch (e) { why.push('NOT_TAPPABLE: ' + String(e.message || e).replace(/\x1b\[[0-9;]*m/g, '').split('\n')[0].slice(0, 200)); }
        }
      }
    }
    // the control was redrawn under the finger: its state is read from the replacement
    if (box && box.redrawn) { const fresh = await loc.elementHandle({ timeout: 1000 }).catch(() => null); if (fresh) { await handle.dispose().catch(() => {}); handle = fresh; } }
    // a finger-sized target wherever the control sits, on screen at rest or not
    if (touch && box && (box.w < 44 || box.h < 44)) why.push('UNDER_44_PX:' + Math.round(box.w) + 'x' + Math.round(box.h));
    if (!why.length) {
      // a navigation is captured at once; a state change gets up to 3 s
      const t0 = Date.now(); why = ['PENDING'];
      while (Date.now() - t0 < 3000) {
        await page.waitForTimeout(150);
        const obs = handle ? await handle.evaluate(observe, expect).catch(() => ({})) : {};
        // a navigation is compared and named with its private search values hashed (/position?g=<room key>)
        why = verdict(expect, obs, PA.navPaths(capture.navs), capture.requests);
        if (!why.length) break;
      }
    }
    if (!why.length && c.close) {
      // the overlay, open: inside the viewport and finger-sized, then closed again
      const o = await page.evaluate(({ touch }) => {
        const vw = innerWidth, vh = innerHeight; let small = 0;
        const open = [...document.querySelectorAll('dialog[open], #hq-desk, #fl-panel, #hq5-pulse-pop, #bt-v4-broadcast')].filter(e => { const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return !e.closest('[hidden]') && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; });
        const shown = (el) => (!el.checkVisibility || el.checkVisibility()) && getComputedStyle(el).visibility !== 'hidden';
        const boxes = open.map(e => { const r = e.getBoundingClientRect(); return { id: e.id || e.tagName.toLowerCase(), left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom) }; });
        if (touch) for (const e of open) for (const el of e.querySelectorAll('a[href],button,[role=button],input,select,summary')) { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
          if (!shown(el) || s.display === 'none' || !r.width || r.bottom < 0 || r.top > vh) continue; if (r.width < 44 || r.height < 44) small++; }
        return { boxes, outside_viewport: boxes.filter(b => b.left < -1 || b.right > vw + 1).length, touch_targets_under_44: small };
      }, { touch }).catch(e => ({ error: String(e).slice(0, 120) }));
      let closed = ['PENDING'];
      try {
        const ch = await page.$(c.close.sel);
        if (!ch) throw new Error('CLOSE_CONTROL_ABSENT');
        await tapAt(page, ch, touch, { sel: c.close.sel, idx: 0 });
        const t0 = Date.now();
        while (Date.now() - t0 < 3000) { await page.waitForTimeout(150); closed = verdict(c.close.expect, await page.locator('body').evaluate(observe, c.close.expect).catch(() => ({})), [], []); if (!closed.length) break; }
      } catch (e) { closed = ['CLOSE_NOT_TAPPABLE: ' + String(e.message || e).split('\n')[0].slice(0, 200)]; }
      o.name = c.name; o.closed = !closed.length; if (closed.length) o.close_failure = closed;
      res.overlays.push(o);
      if (o.outside_viewport) why.push('OVERLAY_OUTSIDE_VIEWPORT');
      if (o.touch_targets_under_44) why.push('OVERLAY_TOUCH_TARGETS_UNDER_44:' + o.touch_targets_under_44);
      if (closed.length) why.push('OVERLAY_DID_NOT_CLOSE: ' + closed.join(','));
    }
    capture.on = false;
    if (handle) await handle.dispose().catch(() => {});
    if (why.length) res.failed.push({ name: c.name, why: why.slice(0, 3) }); else res.passed++;
  }
  return res;
}

// the first position room the production read returns (its key is a
// production identifier: only a short hash of it is written out), and every
// private identifier the harness can know of -- the room keys of both books,
// the room's own read, the Trader snapshot's positions, orders and reviews --
// so that none of them reaches preview.json wherever a page, a URL or an
// error message would carry it (public-artifact.js, layer 3)
async function findRoom(browser) {
  const ctx = await browser.newContext();
  await ctx.route('**/*', route => { const m = route.request().method(); return (m === 'GET' || m === 'HEAD') ? route.continue() : route.abort(); });
  const page = await ctx.newPage();
  let got = {};
  try {
    await page.goto(BASE + '/positions', { waitUntil: 'load', timeout: 60000 });
    got = await page.evaluate(async () => {
      const out = { key: null, book: null, why: null, reads: [] };
      const read = async (u) => { try { const r = await fetch(u, { credentials: 'same-origin', cache: 'no-store' }); return { ok: r.ok, status: r.status, j: r.ok ? await r.json() : null }; } catch (e) { return { ok: false, status: 'FETCH_FAILED', j: null }; } };
      for (const book of ['PAPER', 'ACTUAL']) {
        const r = await read('/api/command/positions/rooms?book=' + book);
        if (!r.ok) { if (!out.key) out.why = 'ROOMS_READ_HTTP_' + r.status; break; }
        out.reads.push(r.j);
        if (!out.key) for (const v of Object.values(r.j.venues || {})) { const room = (v.rooms || []).find(x => x && x.group_key); if (room) { out.key = room.group_key; out.book = book; break; } }
      }
      if (!out.key && !out.why) out.why = 'NO_OPEN_POSITION_ROOM_IN_THE_PRODUCTION_READ';
      if (out.key) { const r = await read('/api/command/positions/room/' + encodeURIComponent(out.key)); if (r.ok) out.reads.push(r.j); }
      const t = await read('/api/command/paper/trader-mode'); if (t.ok) out.reads.push(t.j);
      return out;
    });
  } catch (e) { got = { why: 'ROOMS_READ_FAILED' }; }
  await ctx.close();
  const identifiers = PA.collectIdentifiers(got.reads || []);
  if (got.key) PA.roomIdentifiers(got.key, identifiers);
  return { key: got.key || null, book: got.book || null, why: got.key ? null : (got.why || null), identifiers, reads: (got.reads || []).length,
    key_sha256_12: got.key ? crypto.createHash('sha256').update(got.key).digest('hex').slice(0, 12) : null };
}

(async () => {
  fs.mkdirSync(path.join(OUT, 'shots'), { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const views = VIEWS.filter(([, pg]) => !ONLY.length || ONLY.includes(pg));
  // read for every run (not only one with the room view): the identifiers protect every view
  const room = await findRoom(browser);
  const SECRETS = PA.compile(room.identifiers);
  const records = [];
  const writeOut = () => fs.writeFileSync(path.join(OUT, 'preview.json'), JSON.stringify({ base: BASE, at: new Date().toISOString(),
    views_planned: VIEWS.length, views_run: records.length, subset: ONLY.length ? ONLY : null, safe_area_insets: SAFE_AREA,
    alias_pages: ALIAS_AGENTS, desk_only: DESK_ONLY,
    // how many private identifiers the scrub held (a count, never the values)
    redaction: { identifiers_known: room.identifiers.size, reads: room.reads }, records }, null, 1));
  for (const [dev, pg, motion] of views) {
    const d = DEVICES[dev];
    const name = `${dev}_${pg}${motion ? '_reduced_motion' : ''}`;
    if (pg === 'room' && !(room && room.key)) {
      // no room to open: the view is UNMEASURED and says why (never a pass)
      records.push(PA.scrubDeep({ view: name, device: dev, page: pg, viewport: d.viewport, status: null, signed_in: null, unmeasured: (room && room.why) || 'NO_ROOM_KEY' }, SECRETS));
      continue;
    }
    const insets = d.hasTouch ? SAFE_AREA[dev] : null;
    const ctx = await browser.newContext(Object.assign({}, d, { reducedMotion: motion === 'reduce' ? 'reduce' : 'no-preference' }));
    let aborted = 0; const api = {}; const errors = []; let authRefusals = 0;
    const capture = { on: false, navs: [], requests: [] };
    let page = null;
    await ctx.route('**/*', route => {
      const req = route.request(); const m = req.method();
      if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }
      // CONTROLS: a navigation of the page under test is recorded and
      // answered 204 (no content), so the page stays where it is
      if (capture.on && page && req.isNavigationRequest() && req.frame() === page.mainFrame()) { capture.navs.push(req.url()); return route.fulfill({ status: 204, body: '' }); }
      if (capture.on) { const u = new URL(req.url()); if (u.pathname.startsWith('/api/')) capture.requests.push(u.pathname + u.search); }
      return route.continue();
    });
    await ctx.addInitScript(SMOOTH_MARK);
    page = await ctx.newPage();
    const cdp = d.hasTouch ? await ctx.newCDPSession(page).catch(() => null) : null;
    if (insets && cdp) { try { await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets }); } catch (e) { errors.push('safe-area override: ' + String(e).slice(0, 120)); } }
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 200)));
    // counted by path with the room key masked (a room read is /api/command/positions/room/<key>)
    page.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) { const k = PA.apiPathKey(u.pathname) + ' ' + r.status(); api[k] = (api[k] || 0) + 1;
      if (u.pathname.startsWith('/api/command/') && (r.status() === 401 || r.status() === 403)) authRefusals++; } });
    let status = null;
    const target = pg === 'room' ? PAGES.room + '?g=' + encodeURIComponent(room.key) : PAGES[pg];
    // COMMAND polls its feeds, so 'networkidle' may never come: wait for the
    // load event, then a fixed settle for the first reads and the 3D scene
    try { const r = await page.goto(BASE + target, { waitUntil: 'load', timeout: 60000 }); status = r ? r.status() : null; }
    catch (e) { errors.push('goto: ' + String(e).slice(0, 200)); }
    await page.waitForTimeout(9000);
    // a page whose main thread never yields (e.g. a self-feeding observer)
    // never reaches DOMContentLoaded and cannot answer a 5 s evaluate
    const ready = await Promise.race([new Promise((_, rej) => setTimeout(() => rej(new Error('main thread did not answer in 20 s')), 20000)), page.evaluate(() => { const n = performance.getEntriesByType('navigation')[0];
      return { dom_content_loaded_ms: n && n.domContentLoadedEventEnd ? Math.round(n.domContentLoadedEventEnd) : null,
               load_ms: n && n.loadEventEnd ? Math.round(n.loadEventEnd) : null, ready_state: document.readyState }; })])
      .then(r => Object.assign({ main_thread_responsive: true }, r))
      .catch(e => ({ main_thread_responsive: false, ready_error: String(e).slice(0, 120) }));
    // rendering cost: frames painted and main-thread long tasks over 4 s
    const perf = ready.main_thread_responsive ? await Promise.race([
      new Promise(res => setTimeout(() => res({ perf_error: 'no answer in 30 s' }), 30000)),
      page.evaluate(() => new Promise(res => {
        let frames = 0, long = 0, longest = 0; const t0 = performance.now();
        let po = null;
        try { po = new PerformanceObserver(l => { for (const e of l.getEntries()) { long++; longest = Math.max(longest, e.duration); } }); po.observe({ type: 'longtask', buffered: false }); } catch (e) { po = null; }
        const tick = () => { frames++; if (performance.now() - t0 < 4000) requestAnimationFrame(tick); else { if (po) po.disconnect(); res({ fps_4s: +(frames / ((performance.now() - t0) / 1000)).toFixed(1), long_tasks_4s: long, longest_task_ms: Math.round(longest) }); } };
        requestAnimationFrame(tick);
      }))]).catch(e => ({ perf_error: String(e).slice(0, 120) })) : {};
    const m = ready.main_thread_responsive ? await measure(page, !!d.hasTouch, insets).catch(e => ({ measure_error: String(e).slice(0, 200) })) : {};
    // signed in only when the API refused none of the page's reads either
    if (m.signed_in !== undefined) { m.signed_in = m.signed_in === true && authRefusals === 0; m.api_auth_refusals = authRefusals; }
    await page.screenshot({ path: path.join(OUT, 'shots', name + '.png'), timeout: 20000 }).catch(() => {});
    if (ready.main_thread_responsive) await page.screenshot({ path: path.join(OUT, 'shots', name + '_full.png'), fullPage: true, timeout: 20000 }).catch(() => {});
    // a full-page screenshot resets Chromium's touch emulation (the page then
    // reports pointer:fine and drops every touch rule: measured locally, the
    // Trader view tabs went from 44 to 35 px): it is switched back on, and
    // the controls run only once the page reports a coarse pointer again
    let touchOk = !d.hasTouch;
    if (d.hasTouch && cdp) { await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 }).catch(() => {}); touchOk = await page.evaluate(() => matchMedia('(pointer:coarse)').matches).catch(() => false); }
    // WORKING CONTROLS, after every measurement and screenshot of the view
    // (the reduced-motion repeats measure motion, not controls)
    const controls = !(ready.main_thread_responsive && !motion) ? null
      : !touchOk ? { error: 'TOUCH_EMULATION_NOT_RESTORED', declared: (CONTROLS[pg] || []).length, checked: 0, passed: 0, failed: [{ name: 'all', why: ['TOUCH_EMULATION_NOT_RESTORED'] }], unmeasured: [] }
      : await runControls(page, capture, pg, d, cdp).catch(e => ({ error: String(e).slice(0, 200) }));
    // written only as the public artifact may hold it (public-artifact.js)
    records.push(PA.scrubDeep(Object.assign({ view: name, device: dev, page: pg, viewport: d.viewport, status, non_get_aborted: aborted,
      console_errors: errors.length, first_errors: errors.slice(0, 6), api_by_path_status: api },
      pg === 'room' ? { room_key_sha256_12: room.key_sha256_12, room_book: room.book } : {}, ready, perf, m, { controls }), SECRETS));
    await ctx.close();
    // written after every view: a run cut short still leaves what it measured,
    // and says how much of the matrix that is (views_run < views_planned)
    writeOut();
  }
  await browser.close();
  writeOut();
  for (const r of records) {
    if (r.unmeasured) { console.log(`${r.view.padEnd(34)} UNMEASURED ${r.unmeasured}`); continue; }
    const c = r.controls || {};
    console.log(`${r.view.padEnd(34)} HTTP ${r.status} dcl=${r.dom_content_loaded_ms} fps=${r.fps_4s} lt=${r.long_tasks_4s}/${r.longest_task_ms}ms in=${r.signed_in} ovf=${r.overflow_px} gl=${r.webgl_canvases}/${r.largest_webgl_share} bars=${r.fixed_bars} ovl=${(r.fixed_overlaps || []).length} bg=${(r.background_layers || []).map(l => l.id + ':' + l.beneath.length).join(',') || 0} t<44=${r.touch_targets_under_44} PAPER=${r.paper_mentions} SHADOW=${r.shadow_mentions} cur=${JSON.stringify(r.current_workspace)} err=${r.console_errors} sus=${JSON.stringify(r.suspect_words)} reach!=${r.unreachable_controls} safe!=${r.safe_area ? r.safe_area.violations : '-'} ctl=${c.passed}/${c.checked}${(c.failed || []).length ? ' FAIL=' + JSON.stringify(c.failed.map(f => f.name)) : ''}${(c.unmeasured || []).length ? ' unmeasured=' + c.unmeasured.length : ''}`);
  }
})();
