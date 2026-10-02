// TEAM LOGOS ON THE PRODUCTION SITE (read-only): homepage, /derek, /xavier,
// /audrey on Chromium desktop 1440x1000 and WebKit iPhone 13 emulation (the
// Safari engine, not a physical iPhone).
//   PAYLOAD  every `matchup` the pages' own API responses carried, grouped by
//            league: teams displayed, verified logos, initials fallbacks.
//   DOM      every rendered team mark: image loaded (naturalWidth > 0),
//            initials-only, broken (an <img> left with naturalWidth 0),
//            rendered box size; both teams of each matchup.
//   SESSION  a context with no credential: the image route must refuse
//            (401/403) and the pages must show no broken image.
//   CSP      console errors naming Content Security Policy.
//   LAYOUT   Chromium layout-shift total after load (CLS-style sum).
// Evidence: logos_report.json + screenshots in OUT.
import { webkit, chromium, devices } from "playwright";
import fs from "node:fs";

const HOST = process.env.HOST || "https://command.bettortoken.com";
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
fs.mkdirSync(OUT, { recursive: true });
const report = { host: HOST, at: new Date().toISOString(), engines: {}, payload: {}, session: {} };
const leagues = {};      // league -> {teams:{id:name}, logo:{id:bool}}

function walk(o) {
  if (Array.isArray(o)) { o.forEach(walk); return; }
  if (!o || typeof o !== "object") return;
  if (Array.isArray(o.matchup)) {
    for (const t of o.matchup) {
      const L = leagues[t.league || "?"] = leagues[t.league || "?"] || { teams: {}, logo: {}, kind: {} };
      L.teams[t.team_id] = t.name;
      L.logo[t.team_id] = !!(t.logo && t.logo.url);
      if (t.logo) L.kind[t.team_id] = t.logo.kind;
    }
  }
  for (const v of Object.values(o)) walk(v);
}

const MARKS = () => [...document.querySelectorAll(".team-mark, .xp-team")].map((el) => {
  const img = el.querySelector("img"), r = el.getBoundingClientRect();
  return { title: el.getAttribute("title"), img: !!img,
           loaded: !!(img && img.complete && img.naturalWidth > 0),
           broken: !!(img && img.complete && img.naturalWidth === 0),
           w: Math.round(r.width), h: Math.round(r.height),
           pair: el.parentElement ? el.parentElement.querySelectorAll(".team-mark, .xp-team").length : 0 };
});
const SHIFT = () => { window.__cls = 0; window.__shifts = []; try { new PerformanceObserver((l) => { for (const e of l.getEntries()) { if (e.hadRecentInput) continue; window.__cls += e.value; window.__shifts.push({ v: Math.round(e.value * 1000) / 1000, t: Math.round(e.startTime), src: (e.sources || []).slice(0, 3).map((s) => { const n = s.node; return n && n.nodeType === 1 ? (n.tagName.toLowerCase() + (n.id ? "#" + n.id : "") + (n.className && typeof n.className === "string" ? "." + n.className.trim().split(/\s+/).slice(0, 2).join(".") : "")) : "?"; }) }); } }).observe({ type: "layout-shift", buffered: true }); } catch (_) {} };
// load lazy images the way a reader does: bring each mark into view in turn
// (bounded: the first 30 marks of a view; enough to prove they load)
const REVEAL = async () => { const els = [...document.querySelectorAll(".team-mark, .xp-team")].slice(0, 30); for (const el of els) { el.scrollIntoView({ block: "center" }); await new Promise((r) => setTimeout(r, 60)); } window.scrollTo(0, 0); };
const within = (ms, p) => Promise.race([p, new Promise((r) => setTimeout(() => r("TIMEOUT"), ms))]);
const T0 = Date.now();
const step = (...a) => console.error(`[${((Date.now() - T0) / 1000).toFixed(1)}s]`, ...a);   // where a hang happened

async function signed(ctx) {
  if (!TOKEN) return;
  await ctx.route(HOST + "/api/command/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-admin-token": TOKEN } }));
}
function frameOf(page, agent) {
  return page.frames().find((f) => new RegExp(`/api/command/agents/${agent}/page`).test(f.url()));
}

const ENGINES = [
  ["chromium-desktop-1440", chromium, { viewport: { width: 1440, height: 1000 } }],
  ["webkit-iphone13", webkit, devices["iPhone 13"]],
];
for (const [ename, engine, dev] of ENGINES) {
  const E = report.engines[ename] = {};
  const browser = await engine.launch();
  for (const where of ["home", "derek", "xavier", "audrey"]) {
    const R = E[where] = {};
    const ctx = await browser.newContext({ ...dev });
    await signed(ctx);
    if (engine === chromium) await ctx.addInitScript(SHIFT);
    const page = await ctx.newPage();
    const csp = [], imgs = [];
    page.on("console", (m) => { if (m.type() === "error" && /Content Security Policy|CSP/i.test(m.text())) csp.push(m.text().slice(0, 200)); });
    page.on("response", async (r) => {
      const u = r.url();
      if (/\/api\/command\/agents\/static\/(venue-team|mlb-logo)/.test(u)) imgs.push({ u: u.split("/").pop(), s: r.status(), t: r.headers()["content-type"] });
      if (engine === chromium && /\/api\/command\/(paper\/(operations|experiment)|agents\/.*operations)/.test(u) && r.ok()) {
        try { walk(await r.json()); } catch (_) {}
      }
    });
    if (E.__wedged) { R.error = "SKIPPED: " + E.__wedged; await within(10000, ctx.close().catch(() => {})); continue; }
    // the whole visit is bounded: a WebKit goto once hung for 17 min past its
    // own 60 s timeout (run 37037853946), so the timeout cannot be left to it
    const visit = async () => { try {
      step(ename, where, "goto");
      await page.goto(HOST + (where === "home" ? "/" : "/" + where), { waitUntil: "domcontentloaded", timeout: 60000 });
      let f = page.mainFrame();
      if (where !== "home") {
        for (let i = 0; i < 120 && !frameOf(page, where); i++) await page.waitForTimeout(500);
        f = frameOf(page, where) || f;
      }
      // marks are rendered once the records arrive; give the reads time
      for (let i = 0; i < 60; i++) {
        const n = await f.evaluate(() => document.querySelectorAll(".team-mark, .xp-team").length).catch(() => 0);
        if (n) break; await page.waitForTimeout(1000);
      }
      if (where !== "home") {
        // every office view (Workboard, Activity, Work & learning): open its
        // collapsed records, bring each mark into view, count, screenshot
        R.views = {};
        const views = await f.evaluate(() => [...document.querySelectorAll("button[data-mg-view]")].map((b) => b.getAttribute("data-mg-view"))).catch(() => []);
        for (const v of views) {
          const t0 = Date.now();
          step(ename, where, "view", v);
          const res = await within(60000, (async () => {
          await f.evaluate((v) => document.querySelector('button[data-mg-view="' + v + '"]').click(), v).catch(() => {});
          await page.waitForTimeout(1500);
          await f.evaluate(() => [...document.querySelectorAll("details.work-item, details.ops, details[data-panel]")].slice(0, 40).forEach((d) => { d.open = true; })).catch(() => {});
          await f.evaluate(REVEAL).catch(() => {});
          await page.waitForTimeout(2500);
          const vm = await f.evaluate(MARKS).catch(() => []);
          const vis = vm.filter((m) => m.w > 0 && m.h > 0);
          R.views[v] = { total: vm.length, visible: vis.length, loaded: vis.filter((m) => m.loaded).length,
                         broken: vm.filter((m) => m.broken).length, names: [...new Set(vis.map((m) => m.title))].slice(0, 40) };
          try { await f.locator(".office-layout").screenshot({ path: `${OUT}/logos_${ename}_${where}_${v}.png`, timeout: 20000 }); } catch (_) {}
          })());
          if (res === "TIMEOUT") { step(ename, where, "view TIMEOUT", v); R.views[v] = Object.assign(R.views[v] || {}, { timed_out_after_ms: Date.now() - t0 }); }
        }
        await f.evaluate(() => { const b = document.querySelector('button[data-mg-view="work"]'); if (b) b.click(); }).catch(() => {});
        await page.waitForTimeout(1000);
      } else {
        await page.evaluate(REVEAL).catch(() => {});
      }
      await page.waitForTimeout(4000);
      step(ename, where, "marks");
      const marks = await within(20000, f.evaluate(MARKS).catch((e) => ({ error: String(e) }))).then((m) => m === "TIMEOUT" ? { error: "MARKS_READ_TIMED_OUT (renderer busy)" } : m);
      if (Array.isArray(marks) && marks.some((m) => m.img && !m.loaded)) {
        R.unloaded = await within(20000, f.evaluate(async () => {
          const out = [];
          for (const el of [...document.querySelectorAll(".team-mark, .xp-team")].filter((e) => { const i = e.querySelector("img"); return i && !(i.complete && i.naturalWidth > 0); }).slice(0, 6)) {
            const i = el.querySelector("img");
            el.scrollIntoView({ block: "center" }); await new Promise((r) => setTimeout(r, 400));
            const r = el.getBoundingClientRect(), anc = [];
            for (let a = el.parentElement; a && anc.length < 6; a = a.parentElement) { const cs = getComputedStyle(a); anc.push(a.tagName.toLowerCase() + (a.id ? "#" + a.id : "") + (typeof a.className === "string" && a.className ? "." + a.className.trim().split(/\s+/).slice(0, 2).join(".") : "") + (cs.overflowY !== "visible" ? "[ov:" + cs.overflowY + "]" : "") + (cs.display === "none" ? "[none]" : "") + (a.tagName === "DETAILS" && !a.open ? "[closed]" : "")); }
            out.push({ title: el.getAttribute("title"), loading: i.loading, complete: i.complete, nw: i.naturalWidth, src: (i.getAttribute("src") || "").split("/").pop(), cur: (i.currentSrc || "").split("/").pop(), rect: [Math.round(r.top), Math.round(r.left), Math.round(r.width), Math.round(r.height)], vh: innerHeight, anc });
          }
          return out;
        }).catch((e) => ({ error: String(e).slice(0, 200) })));
      }
      R.marks = Array.isArray(marks) ? {
        total: marks.length, loaded: marks.filter((m) => m.loaded).length,
        initials_only: marks.filter((m) => !m.img).length,
        broken: marks.filter((m) => m.broken).length,
        pairs: marks.filter((m) => m.pair >= 2).length,
        sizes: [...new Set(marks.map((m) => m.w + "x" + m.h))].slice(0, 8),
        names: [...new Set(marks.map((m) => m.title))].slice(0, 80) } : marks;
      R.image_responses = imgs.slice(0, 80);
      R.image_status = imgs.reduce((a, x) => (a[x.s] = (a[x.s] || 0) + 1, a), {});
      R.csp_errors = csp;
      R.layout_shift = await within(10000, page.evaluate(() => window.__cls).catch(() => null));
      R.shift_sources = await within(10000, page.evaluate(() => (window.__shifts || []).sort((a, b) => b.v - a.v).slice(0, 8)).catch(() => null));
      step(ename, where, "screenshot");
      await page.screenshot({ path: `${OUT}/logos_${ename}_${where}.png`, fullPage: where === "home", timeout: 30000 }).catch((e) => step("screenshot failed", String(e).slice(0, 120)));
      if (where !== "home") {
        try { await f.locator(".office-layout").screenshot({ path: `${OUT}/logos_${ename}_${where}_board.png`, timeout: 20000 }); } catch (_) {}
      }
    } catch (x) { R.error = String(x).slice(0, 300); } };
    if (await within(240000, visit()) === "TIMEOUT") {
      step(ename, where, "VISIT TIMEOUT");
      R.error = "VISIT_TIMED_OUT_240S"; E.__wedged = "an earlier page in this engine timed out (" + where + ")";
    }
    await within(10000, ctx.close().catch(() => {}));
    fs.writeFileSync(`${OUT}/logos_report.json`, JSON.stringify(report, null, 1));   // partial evidence survives a timeout
  }
  // EXPIRED / ABSENT SESSION: no credential at all
  if (E.__wedged) { report.session[ename] = { error: "SKIPPED: " + E.__wedged }; await within(10000, browser.close().catch(() => {})); continue; }
  const ctx = await browser.newContext({ ...dev });
  const page = await ctx.newPage();
  const S = report.session[ename] = {};
  if (await within(120000, (async () => { try {
    const r = await page.request.get(HOST + "/api/command/agents/static/venue-team-16888.png");
    S.image_without_session = r.status();
    await page.goto(HOST + "/xavier", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(8000);
    const f = frameOf(page, "xavier") || page.mainFrame();
    S.broken_images = await f.evaluate(() => [...document.images].filter((i) => i.complete && i.naturalWidth === 0).length).catch(() => null);
    S.marks = await f.evaluate(() => document.querySelectorAll(".team-mark").length).catch(() => null);
    await page.screenshot({ path: `${OUT}/logos_${ename}_signed_out.png` });
  } catch (x) { S.error = String(x).slice(0, 300); } })()) === "TIMEOUT") S.error = "SESSION_CHECK_TIMED_OUT_120S";
  await within(10000, ctx.close().catch(() => {}));
  await within(10000, browser.close().catch(() => {}));
  delete E.__wedged;
}
report.payload = Object.fromEntries(Object.entries(leagues).map(([lg, L]) => {
  const ids = Object.keys(L.teams);
  return [lg, { teams_displayed: ids.length,
                verified_logos: ids.filter((i) => L.logo[i]).length,
                fallbacks: ids.filter((i) => !L.logo[i]).length,
                flags: ids.filter((i) => L.kind[i] === "flag").length,
                fallback_names: ids.filter((i) => !L.logo[i]).map((i) => L.teams[i]) }];
}));
fs.writeFileSync(`${OUT}/logos_report.json`, JSON.stringify(report, null, 1));
console.log(JSON.stringify({ payload: report.payload, session: report.session,
  engines: Object.fromEntries(Object.entries(report.engines).map(([e, v]) => [e, Object.fromEntries(Object.entries(v).map(([w, r]) => [w, r.marks && { total: r.marks.total, loaded: r.marks.loaded, initials: r.marks.initials_only, broken: r.marks.broken, pairs: r.marks.pairs, csp: (r.csp_errors || []).length, cls: r.layout_shift, shifts: r.shift_sources, views: r.views, err: r.error }]))])) }, null, 1));
process.exit(0);   // a wedged browser process must not hold the job open
