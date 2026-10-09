// FRONTEND PREVIEW HARNESS: working controls (.github/frontend-preview/shots.js).
//
// The device matrix used to load a page and measure it; nothing proved a
// navigation link or a primary control did anything. shots.js now taps every
// declared control on every device and checks where it lands (CONTROLS). Found
// with it (local preview, synthetic reads, frontend f90dafdd): on every agent
// page the nine links of agent.html's own navigation sat under the shell's
// page bar / status strip / rail and took no tap; on /positions the bar's
// five links and Refresh likewise; on a landscape phone the rail's Company
// link was below the screen's edge. These tests pin the verdict rules (a
// missing navigation, a wrong hash, an attribute not reached all fail; an
// absent data-dependent control is UNMEASURED, never passed) and the
// declared control set (every page, every agent link, the shell).
//
// The block between the CONTROLS markers is evaluated exactly as written.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../../../.github/frontend-preview/shots.js'), 'utf8');
const BLOCK = SRC.slice(SRC.indexOf('// >>> CONTROLS'), SRC.indexOf('// <<< CONTROLS'));
const CTX = { AGENTS: ['derek', 'xavier', 'audrey', 'karen', 'allocator', 'archer', 'scout'], Object, JSON, Array, String, Math, window: {} };
const H = vm.runInNewContext('(() => {' + BLOCK + '; return { CONTROLS, verdict, landing, RAIL, PULSE, OPS_HDR, AGENT_NAV, AGENTS: typeof AGENTS === "undefined" ? null : AGENTS, shellWide, phoneW, pocket, traderAbove }; })()', CTX);
const plain = (x) => JSON.parse(JSON.stringify(x));
const PHONE = { width: 390, height: 844 }, LAND = { width: 844, height: 390 }, IPAD = { width: 820, height: 1180 }, DESK = { width: 1440, height: 900 };

test('the block under test is the harness controls code', () => {
  assert.ok(BLOCK.includes('function verdict(expect, obs, navs, requests)'));
  assert.ok(SRC.includes(": await runControls(page, capture, pg, d, cdp).catch(e => ({ error: String(e).slice(0, 200) }));"));
  // the controls run only with touch emulation back on after the full-page screenshot
  assert.ok(SRC.includes("await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })"));
  assert.ok(SRC.includes(": !touchOk ? { error: 'TOUCH_EMULATION_NOT_RESTORED'"));
  // a navigation of the page under test is answered 204: the page stays, the destination is recorded
  assert.ok(SRC.includes("if (capture.on && page && req.isNavigationRequest() && req.frame() === page.mainFrame()) { capture.navs.push(req.url()); return route.fulfill({ status: 204, body: '' }); }"));
  // every non-GET is still aborted first
  assert.ok(SRC.indexOf("if (m !== 'GET' && m !== 'HEAD') { aborted++; return route.abort(); }") < SRC.indexOf('capture.navs.push(req.url())'));
});

test('a navigation control passes only when the navigation to that path started', () => {
  assert.deepEqual(plain(H.verdict({ nav: '/floor' }, {}, ['/floor'], [])), []);
  assert.equal(H.verdict({ nav: '/floor' }, {}, [], []).length, 1);
  assert.match(H.verdict({ nav: '/floor' }, {}, ['/ops'], [])[0], /NO_NAVIGATION_TO:\/floor got \/ops/);
  // several accepted spellings of one destination
  assert.deepEqual(plain(H.verdict({ nav: ['/index.html', '/'] }, {}, ['/'], [])), []);
  // a search is compared only when the expectation names one
  assert.deepEqual(plain(H.verdict({ nav: '/positions' }, {}, ['/positions?book=PAPER'], [])), []);
  assert.equal(H.verdict({ nav: '/positions?book=ACTUAL' }, {}, ['/positions?book=PAPER'], []).length, 1);
  assert.deepEqual(plain(H.verdict({ nav: '/positions?book=ACTUAL' }, {}, ['/positions?book=ACTUAL'], [])), []);
});

test('a state control passes only when every named DOM answer is true', () => {
  const e = { self: ['aria-pressed', 'true'], visible: '#positions .orders-view' };
  const k = (x, v) => x + ':' + JSON.stringify(v);
  assert.deepEqual(plain(H.verdict(e, { [k('self', e.self)]: true, [k('visible', e.visible)]: true }, [], [])), []);
  assert.equal(H.verdict(e, { [k('self', e.self)]: true, [k('visible', e.visible)]: false }, [], []).length, 1);
  // an unanswered question is a failure, never a pass
  assert.equal(H.verdict(e, {}, [], []).length, 2);
  assert.deepEqual(plain(H.verdict({}, {}, [], [])), ['NO_EXPECTATION']);
  // any: one alternative must hold completely
  const any = { any: [{ hash: '#a' }, { hash: '#b' }] };
  assert.deepEqual(plain(H.verdict(any, { [k('hash', '#b')]: true }, [], [])), []);
  assert.deepEqual(plain(H.verdict(any, { [k('hash', '#a')]: false, [k('hash', '#b')]: false }, [], [])), ['NONE_OF_ANY']);
  // a request the tap must send
  assert.deepEqual(plain(H.verdict({ request: '/api/command/positions/' }, {}, [], ['/api/command/positions/rooms?book=PAPER'])), []);
  assert.equal(H.verdict({ request: '/api/command/positions/' }, {}, [], ['/api/command/floor']).length, 1);
});

test('every page of the matrix declares its controls', () => {
  const pages = Object.keys(H.CONTROLS).sort();
  assert.deepEqual(pages, ['allocator', 'archer', 'audrey', 'command', 'derek', 'floor', 'karen', 'ops', 'positions', 'room', 'scout', 'trader', 'xavier']);
  for (const p of pages) assert.ok(H.CONTROLS[p].length >= 5, p);
  for (const p of pages) for (const c of H.CONTROLS[p]) { assert.ok(c.name && c.sel && c.expect, p + ' ' + c.name); }
});

test('every agent page taps all nine links of its navigation, the shell rail, Company Pulse and the strip', () => {
  for (const a of ['derek', 'xavier', 'audrey', 'karen', 'allocator', 'archer', 'scout']) {
    const names = H.CONTROLS[a].map(c => c.name);
    for (const n of ['agents: COMMAND', 'agents: Trading floor', 'agents: derek', 'agents: xavier', 'agents: audrey', 'agents: karen', 'agents: allocator', 'agents: archer', 'agents: scout'])
      assert.ok(names.includes(n), a + ' ' + n);
    for (const n of ['rail: Operations', 'rail: HQ', 'rail: Floor', 'rail: Positions', 'rail: Economics', 'rail: Company', 'Company Pulse', 'header: incidents', 'Back to floor'])
      assert.ok(names.includes(n), a + ' ' + n);
    // Talk wherever a conversation exists; Allie has none and her page draws no Talk
    assert.equal(names.includes('Talk to the agent'), a !== 'allocator', a);
    const nav = H.CONTROLS[a].find(c => c.name === 'agents: archer');
    assert.deepEqual(plain(nav.expect), { nav: '/archer' });
    assert.equal(nav.sel, 'body > nav a[data-agent="archer"]');
  }
  // the two-tab agents (workspace + classic desk) tap both tabs
  for (const a of ['derek', 'xavier', 'audrey', 'karen']) assert.ok(H.CONTROLS[a].some(c => c.name === 'tab: Desk & conversation'));
  for (const a of ['allocator', 'archer', 'scout']) assert.ok(!H.CONTROLS[a].some(c => c.name === 'tab: Desk & conversation'));
});

test('the Command views, the Trader controls and the position bar are declared', () => {
  const cmd = H.CONTROLS.command.map(c => c.name);
  for (const v of ['view: command', 'view: floor', 'view: markets', 'view: capital', 'view: reports', 'desk panel', 'Trader launcher']) assert.ok(cmd.includes(v), v);
  // hq.js: on a phone Floor opens the /floor room; elsewhere it switches the view
  const floor = H.CONTROLS.command.find(c => c.name === 'view: floor');
  assert.deepEqual(plain(floor.expect(PHONE)), { nav: '/floor' });
  // a phone on its side is a phone to Command (hq.js PHONE)
  assert.deepEqual(plain(floor.expect(LAND)), { nav: '/floor' });
  assert.deepEqual(plain(floor.expect(IPAD)), { bodyAttr: ['data-view', 'floor'], self: ['aria-current', 'page'] });
  assert.deepEqual(plain(floor.expect({ width: 1180, height: 820 })), { bodyAttr: ['data-view', 'floor'], self: ['aria-current', 'page'] });
  const tr = H.CONTROLS.trader.map(c => c.name);
  for (const v of ['evidence dialog', 'view: Standing orders', 'filter: near', 'filter: all', 'search', 'Follow Xavier', 'Wall mode', 'Broadcast', 'nav: Command', 'nav: Floor'])
    assert.ok(tr.includes(v), v);
  const pos = H.CONTROLS.positions.map(c => c.name);
  for (const v of ['bar: Position rooms', 'bar: Command', 'bar: Xavier', 'bar: All positions', 'bar: Legacy mirror', 'Refresh', 'book: Actual']) assert.ok(pos.includes(v), v);
  const ops = H.CONTROLS.ops.map(c => c.name);
  for (const id of ['funnel', 'coverage', 'opportunities', 'blotter', 'agents', 'refusals', 'pinnapi', 'capital', 'incidents']) assert.ok(ops.includes('jump: ' + id));
  // a jump must land its section on screen AND clear of the fixed bars
  assert.deepEqual(plain(H.CONTROLS.ops[0].expect), { hash: '#funnel', inView: '#funnel' });
  assert.ok(SRC.includes("else if (k === 'inView') { const t = q(v); ok = !!t && clear(t); }"));
});

test('layout applicability comes from the shell\'s own breakpoints, not from results', () => {
  // hq2-brand.css / command-ops.css: <= 780 px is the bottom tab bar, no logo, no DESK link
  assert.equal(H.shellWide(PHONE), false);
  assert.equal(H.shellWide({ width: 780, height: 500 }), false);
  assert.equal(H.shellWide({ width: 781, height: 500 }), true);
  assert.equal(H.shellWide(LAND), true);
  // hq.js PHONE: max-width 760 px
  assert.equal(H.phoneW({ width: 760, height: 900 }), true);
  assert.equal(H.phoneW({ width: 761, height: 900 }), false);
  // hq.js PHONE (Command's pocket layout): a phone, or a phone on its side
  const hqjs = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq.js'), 'utf8');
  assert.ok(hqjs.includes("const PHONE = !!(window.matchMedia && matchMedia('(max-width: 760px), (max-height: 500px) and (max-width: 1024px)').matches);"));
  assert.equal(H.pocket(PHONE), true); assert.equal(H.pocket(LAND), true);
  assert.equal(H.pocket({ width: 1024, height: 500 }), true); assert.equal(H.pocket({ width: 1025, height: 500 }), false);
  assert.equal(H.pocket({ width: 1024, height: 501 }), false); assert.equal(H.pocket(IPAD), false); assert.equal(H.pocket(DESK), false);
  const css = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq2-brand.css'), 'utf8');
  assert.ok(css.includes('@media(max-width:780px){') && css.includes('.bt-hq2 .bt-hq2-logo,.bt-hq2 .bt-hq2-shell-foot{display:none}'));
  const ops = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/command-ops.css'), 'utf8');
  assert.ok(ops.includes('  .ops-h-desk{display:none}'));
  // trader.css: <= 900 px hides the Command link and Reduce motion; <= 600 px
  // every link but the current one and the full-screen button
  const tcss = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/trader.css'), 'utf8');
  assert.ok(tcss.includes('@media(max-width:900px){.topbar{height:64px}.topbar nav a:first-child{display:none}'));
  assert.ok(tcss.includes('.heading-controls .subtle{display:none}'));
  assert.ok(tcss.includes('.topbar nav a{display:none!important}.topbar nav a[aria-current]{display:flex!important;') && tcss.includes('.topbar .icon-button{display:none}'));
  const tr = Object.fromEntries(H.CONTROLS.trader.map(c => [c.name, c]));
  assert.equal(tr['nav: Command'].when({ width: 900 }), false); assert.equal(tr['nav: Command'].when({ width: 901 }), true);
  assert.equal(tr['Reduce motion'].when({ width: 901 }), true); assert.equal(tr['nav: Floor'].when({ width: 600 }), false);
  assert.equal(tr['full screen'].when({ width: 601 }), true);
  // only these controls carry a layout condition; nothing else is excused by device
  const conditioned = [].concat(...Object.values(H.CONTROLS)).filter(c => c.when).map(c => c.name);
  // experience-v4: the quick-lane rail docks above 760 px and hides its filters there
  const v4 = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/experience-v4.css'), 'utf8');
  const v4js = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/experience-v4.js'), 'utf8');
  assert.ok(v4.includes('#bt-v4-trader-rail.bt-v4-rail-docked [data-v4-filter]{display:none}') && v4js.includes("matchMedia('(max-width:760px)')"));
  assert.deepEqual([...new Set(conditioned)].sort(), ['Reduce motion', 'full screen', 'header: operations desk', 'nav: Command', 'nav: Floor', 'rail filter: all', 'rail filter: near', 'rail: logo']);
});

test('an absent data-dependent control is UNMEASURED and never counted as passed', () => {
  assert.ok(SRC.includes("if (idx < 0) { if (c.optional) res.unmeasured.push(c.name + ': ABSENT (no data shows it)'); else res.failed.push({ name: c.name, why: ['CONTROL_NOT_SHOWN'] }); continue; }"));
  assert.ok(SRC.indexOf('res.passed++') > SRC.indexOf("res.unmeasured.push(c.name + ': ABSENT"));
  // the optional ones are exactly those that need a record to exist
  const optional = [].concat(...Object.values(H.CONTROLS)).filter(c => c.optional).map(c => c.name);
  assert.deepEqual([...new Set(optional)].sort(), ['Briefing (CRITICAL bar)', 'Broadcast', 'agent panel', 'desk panel', 'evidence dialog', 'focus a position', 'open a room', 'sort coverage by health']);
  // the floor's old header is hidden on the real floor: its controls are the view bar's
  const hq5 = fs.readFileSync(path.join(__dirname, '../../../frontend/public/command/hq5-workspace.css'), 'utf8');
  assert.ok(hq5.includes('body.meeting-real-floor .fl-top{display:none!important}'));
  assert.ok(H.CONTROLS.floor.some(c => c.name === 'floor view: map') && H.CONTROLS.floor.some(c => c.name === 'floor view: overview'));
});

test('on touch every tapped control is 44 x 44 px, and an overlay is measured open and must close', () => {
  assert.ok(SRC.includes("if (touch && box && (box.w < 44 || box.h < 44)) why.push('UNDER_44_PX:' + Math.round(box.w) + 'x' + Math.round(box.h));"));
  // a tap is sent only where a hit test found the control itself
  assert.ok(SRC.includes("if (!h || !(h === el || el.contains(h) || h.contains(el))) {"));
  assert.ok(SRC.includes("return { error: 'COVERED_BY ' + id };"));
  assert.ok(SRC.includes('if (touch) await page.touchscreen.tap(at.vx, at.vy); else await page.mouse.click(at.vx, at.vy);'));
  assert.ok(SRC.includes("if (o.outside_viewport) why.push('OVERLAY_OUTSIDE_VIEWPORT');"));
  assert.ok(SRC.includes("if (o.touch_targets_under_44) why.push('OVERLAY_TOUCH_TARGETS_UNDER_44:' + o.touch_targets_under_44);"));
  assert.ok(SRC.includes("if (closed.length) why.push('OVERLAY_DID_NOT_CLOSE: ' + closed.join(','));"));
  const overlays = [].concat(...Object.values(H.CONTROLS)).filter(c => c.close).map(c => c.name);
  assert.deepEqual([...new Set(overlays)].sort(), ['Broadcast', 'Company Pulse', 'agent panel', 'desk panel', 'evidence dialog']);
});

test('a tap counts where it landed: the control, or the same control redrawn in place; anywhere else fails', () => {
  const mk = (tag, parent) => ({ tagName: tag.toUpperCase(), id: '', parentElement: parent || null,
    contains(x) { for (let a = x; a; a = a.parentElement) if (a === this) return true; return false; } });
  const roster = mk('div'), btn = mk('button', roster), b = mk('b', btn);
  // what the page recorded at pointerdown
  const at = (t, seen) => { CTX.window.__previewTapTarget = t; CTX.window.__previewTapSeen = seen || null; };
  const twin = { sel: '#roster .fl-agent', idx: 0, box: [9, 577, 152, 48] };
  // on the control, or inside it
  at(btn); assert.equal(H.landing(btn, twin), 'OK');
  at(b); assert.equal(H.landing(btn, twin), 'OK');
  // nothing received the touch
  at(null); assert.equal(H.landing(btn, twin), 'NONE');
  // a poll redrew the roster between the hit test and the touch (Floor, every
  // touch device): the touch reached the replacement -- the first match of the
  // same selector, in the same box
  const btn2 = mk('button', roster), b2 = mk('b', btn2);
  at(b2, { idx: 0, box: [9, 577, 152, 48] }); assert.equal(H.landing(btn, twin), 'REDRAWN');
  at(b2, { idx: 0, box: [9.6, 577.4, 152, 48] }); assert.equal(H.landing(btn, twin), 'REDRAWN');
  // ... never another match, the same match moved, or anything outside the selector
  at(b2, { idx: 1, box: [9, 577, 152, 48] }); assert.equal(H.landing(btn, twin), 'b');
  at(b2, { idx: 0, box: [9, 628, 152, 48] }); assert.equal(H.landing(btn, twin), 'b');
  const bar = mk('nav'); bar.id = 'hq5-floorbar';
  at(bar, null); assert.equal(H.landing(btn, twin), '#hq5-floorbar');
  // without a twin (a <details> summary) only the element itself counts
  at(b2, { idx: 0, box: [9, 577, 152, 48] }); assert.equal(H.landing(btn, null), 'b');
  // the harness: the record is made at pointerdown, a delivered tap is never
  // sent again, and the state is read from the replacement
  assert.ok(SRC.includes("const t = e.target, m = twin && t.closest ? t.closest(twin.sel) : null, q = m && m.getBoundingClientRect();"));
  assert.ok(SRC.includes("window.__previewTapSeen = m ? { idx: [...document.querySelectorAll(twin.sel)].indexOf(m), box: [q.left, q.top, q.width, q.height] } : null;"));
  assert.ok(SRC.includes("twin: twin ? { sel: twin.sel, idx: twin.idx, box: [r.left, r.top, r.width, r.height] } : null };"));
  assert.ok(SRC.includes("const landed = await handle.evaluate(landing, at.twin).catch(() => 'OK');"));
  assert.ok(SRC.includes("if (landed === 'REDRAWN') at.redrawn = true;\n  else if (landed !== 'OK') throw Object.assign(new Error('TAP_LANDED_ON ' + landed), { delivered: true });"));
  assert.ok(SRC.includes('else box = await tapAt(page, handle, touch, { sel: c.sel, idx });'));
  assert.ok(SRC.includes('await tapAt(page, ch, touch, { sel: c.close.sel, idx: 0 });'));
  assert.ok(SRC.includes('if (box && box.redrawn) { const fresh = await loc.elementHandle({ timeout: 1000 }).catch(() => null); if (fresh) { await handle.dispose().catch(() => {}); handle = fresh; } }'));
});

test('a touch that landed elsewhere is final; a control that vanished before the touch is found again once', () => {
  // a delivered touch is never sent again (it may have changed the page)
  assert.ok(SRC.includes("else if (landed !== 'OK') throw Object.assign(new Error('TAP_LANDED_ON ' + landed), { delivered: true });"));
  assert.ok(SRC.includes("try { return await tapOnce(page, handle, touch, twin); } catch (e) { last = e; if (e.delivered || /CONTROL_DETACHED|Element is not attached/.test(String(e))) break; }"));
  // only a control gone from the page (detached, hidden, no box) with no touch
  // delivered is looked up again -- the first match a finger can reach now --
  // and tapped once; one still shown but out of reach keeps its failure
  assert.ok(SRC.includes("if (why.length && !box && !(tapErr && tapErr.delivered) && !(c.action && c.action.startsWith('fill:'))) {"));
  assert.ok(SRC.includes("const gone = handle ? await handle.evaluate(e => !e.isConnected || (e.checkVisibility && !e.checkVisibility()) || !e.getBoundingClientRect().width).catch(() => true) : true;"));
  assert.ok(SRC.includes('const again = gone ? await find() : -1;'));
  assert.ok(SRC.includes('handle = h2; why = []; res.retargeted = (res.retargeted || 0) + 1;'));
  // the retarget happens once: no loop around it
  const i = SRC.indexOf('const again = gone ? await find() : -1;');
  assert.ok(!/while|for \(/.test(SRC.slice(i, SRC.indexOf('// the control was redrawn under the finger', i))));
});

test('a touch device stays one for every control; the desk opens from the team list', () => {
  // checked before every control: switched back on and counted if a late
  // screenshot reset it, and a control is never measured with a fine pointer
  assert.ok(SRC.includes("if (touch && !(await coarse())) {"));
  assert.ok(SRC.includes("res.touch_restored = (res.touch_restored || 0) + 1;"));
  assert.ok(SRC.includes("if (!(await coarse())) { res.failed.push({ name: c.name, why: ['TOUCH_EMULATION_LOST'] }); continue; }"));
  const i = SRC.indexOf('async function runControls(');
  assert.ok(SRC.indexOf('if (touch && !(await coarse())) {', i) < SRC.indexOf('const here = await herePath();', i));
  // a page that does not answer once is asked again before it counts as left
  assert.ok(SRC.includes('const p = await at(); if (p !== null) return p; await page.waitForTimeout(500); return at(); };'));
  // the desk control is a team-list row (the stage's tags move with the camera)
  const desk = H.CONTROLS.command.find(c => c.name === 'desk panel');
  assert.equal(desk.sel, '[data-render="team"] [data-desk]');
  assert.deepEqual(plain(desk.expect), { bodyClass: 'desk-open', visible: '#hq-desk' });
  assert.ok(desk.optional && desk.close.sel === '#hq-desk [data-close-desk]');
});

test('Talk lands on the conversation: the desk tab, or the framed page with the live strip kept', () => {
  const talk = (a) => H.CONTROLS[a].find(c => c.name === 'Talk to the agent');
  for (const a of ['derek', 'xavier', 'audrey', 'karen']) assert.deepEqual(plain(talk(a).expect), { attr: ['#tab-desk', 'aria-selected', 'true'] });
  // an error note in place of the page is no longer a landing (#state was accepted)
  for (const a of ['archer', 'scout']) assert.deepEqual(plain(talk(a).expect), { inView: '#page', visible: '#ws-root .wsx-strip' });
  assert.equal(talk('allocator'), undefined);
});

test('every control starts from a page at rest: a smooth scroll the last one started is let finish', () => {
  const i = SRC.indexOf('async function runControls(');
  const settle = SRC.indexOf("if ((still >= 4 && !asked) || n >= 40) { clearInterval(t); res(); } }, 100); })).catch(() => {});", i);
  const top = SRC.indexOf("await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' })).catch(() => {});", i);
  const find = SRC.indexOf('let idx = await find();', i);
  assert.ok(settle > i && settle < top && top < find);
  // 400 ms still, and 3 s after a smooth scroll the page asked for; at most 4 s
  assert.ok(SRC.includes("const asked = window.__previewSmoothAt != null && performance.now() - window.__previewSmoothAt < 3000;"));
  // the marker is installed in every view before the page's own scripts
  assert.ok(SRC.indexOf('await ctx.addInitScript(SMOOTH_MARK);') < SRC.indexOf('page = await ctx.newPage();\n    const cdp'));
  // it records smooth requests only, and every call goes through unchanged
  const mark = /const SMOOTH_MARK = `([\s\S]*?)`;/.exec(SRC)[1];
  const calls = [];
  function El() {} El.prototype.scrollIntoView = function (o) { calls.push(['siv', o]); };
  const win = { scrollTo(o) { calls.push(['to', o]); }, scrollBy(o) { calls.push(['by', o]); } };
  let now = 1000;
  const ctx = { window: win, Element: El, performance: { now: () => now } };
  vm.runInNewContext(mark, ctx);
  new El().scrollIntoView({ block: 'center', behavior: 'instant' });
  win.scrollTo({ top: 0, behavior: 'instant' });
  assert.equal(win.__previewSmoothAt, undefined);
  now = 2500; new El().scrollIntoView({ behavior: 'smooth' });
  assert.equal(win.__previewSmoothAt, 2500);
  now = 4000; win.scrollBy({ top: 10, behavior: 'smooth' });
  assert.equal(win.__previewSmoothAt, 4000);
  assert.deepEqual(calls.map(c => c[0]), ['siv', 'to', 'siv', 'by']);
});
