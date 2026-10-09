// AGENT PAGES: "Talk to <agent>" opens the conversation the page really has.
//
// hq2-agent.js clicked the "Desk & conversation" tab whenever the element
// existed. On Allie's page (agent.html: the workspace only, no classic desk)
// the tab is hidden, yet the click still ran agent.html's classic(): a GET of
// /api/command/agents/allocator/page (404, logged in the console) and the
// note "NOT YET RELEASED: the serving API does not have the allocator page
// yet" -- for a page that does not exist by design. On Archer's and Scout's
// pages (their own page framed, no tabs) the same click hid the live floor
// strip above the frame. Found by the device matrix (synthetic reads,
// f90dafdd): console 404 on all five Allie views. Now: the tab on the
// two-tab pages, the frame on the framed pages, and no Talk on Allie's.
//
// The block is evaluated exactly as written, with a minimal page around it.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq2-agent.js'), 'utf8');
const START = SRC.indexOf("  // Talk opens the agent's conversation");
const BLOCK = SRC.slice(START, SRC.indexOf('  })();', START) + '  })();'.length);

function page({ tabsHidden, deskHidden }) {
  const log = [];
  const el = (id, extra) => Object.assign({ id, hidden: false, listeners: {}, addEventListener(t, fn) { this.listeners[t] = fn; } }, extra || {});
  const els = {
    'bt-agent2-talk': el('bt-agent2-talk'),
    'tab-desk': el('tab-desk', { hidden: deskHidden, click() { log.push('desk tab clicked'); } }),
    'ws-tabs': el('ws-tabs', { hidden: tabsHidden }),
    page: el('page', { scrollIntoView() { log.push('frame into view'); } }),
  };
  const timers = [];
  vm.runInNewContext(BLOCK, { document: { getElementById: (id) => els[id] || null }, setTimeout: (fn) => { timers.push(fn); } });
  const tap = () => { els['bt-agent2-talk'].listeners.click(); timers.splice(0).forEach((fn) => fn()); };
  return { els, log, tap };
}

test('the block under test is hq2-agent.js Talk wiring', () => {
  assert.ok(START > 0);
  assert.ok(BLOCK.includes("var tabbed=!!(desk&&!desk.hidden&&tabs&&!tabs.hidden)"));
});

test('two-tab agents (derek, xavier, audrey, karen): Talk selects the desk tab and shows the frame', () => {
  const p = page({ tabsHidden: false, deskHidden: false });
  assert.equal(p.els['bt-agent2-talk'].hidden, false);
  p.tap();
  assert.deepEqual(p.log, ['desk tab clicked', 'frame into view']);
});

test('framed agents (archer, scout): Talk brings the frame into view and never clicks the hidden tab', () => {
  const p = page({ tabsHidden: true, deskHidden: false });
  assert.equal(p.els['bt-agent2-talk'].hidden, false);
  p.tap();
  assert.deepEqual(p.log, ['frame into view']);
});

test('Allie (no conversation desk): no Talk button, nothing requested', () => {
  const p = page({ tabsHidden: true, deskHidden: true });
  assert.equal(p.els['bt-agent2-talk'].hidden, true);
  assert.equal(p.els['bt-agent2-talk'].listeners.click, undefined);
  // agent.html: allocator is a workspace agent without a classic page
  const html = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/agent.html'), 'utf8');
  assert.ok(html.includes('var CLASSIC = {derek: 1, xavier: 1, audrey: 1, karen: 1, archer: 1, scout: 1};'));
  assert.ok(html.includes('if (CLASSIC[name]) { tabs.hidden = false; } else { tabDesk.hidden = true; }'));
});

test("Archer's and Scout's own page stays shown: the Work tab is selected only where the tabs are", () => {
  const HQ6 = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq6-complete.js'), 'utf8');
  const line = HQ6.split('\n').find((l) => l.includes("var workTab=document.getElementById('tab-ws');"));
  assert.ok(line, 'hq6-complete.js Work tab default');
  const run = (tabsHidden) => {
    let clicked = 0;
    const workTab = { hidden: false, click() { clicked++; } };
    vm.runInNewContext(line, { tabs: { hidden: tabsHidden }, document: { getElementById: (id) => (id === 'tab-ws' ? workTab : null) }, setTimeout: (fn) => fn() });
    return clicked;
  };
  // two-tab agents: Work is the default
  assert.equal(run(false), 1);
  // archer / scout: tabs never shown -- their page is not hidden behind a Work click
  assert.equal(run(true), 0);
});

test('on touch the framed agent page (rendered by the API) gets 44 px controls, only raised', () => {
  const frameCss = SRC.slice(SRC.indexOf("var style=d.createElement('style');"), SRC.indexOf('d.head.appendChild(style);'));
  assert.ok(frameCss.includes("'@media (pointer:coarse){.top .brand,nav a{display:inline-flex;align-items:center;min-height:44px}button,select,summary,input[type=button],input[type=submit]{min-height:44px}}'"));
  for (const banned of ['max-height', 'max-width', 'display:none', 'font-size']) assert.ok(!frameCss.includes(banned), banned);
});
