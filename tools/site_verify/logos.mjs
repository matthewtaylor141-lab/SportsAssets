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
const SHIFT = () => { window.__cls = 0; try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); } catch (_) {} };

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
    await ctx.addInitScript(SHIFT);
    const page = await ctx.newPage();
    const csp = [], imgs = [];
    page.on("console", (m) => { if (m.type() === "error" && /Content Security Policy|CSP/i.test(m.text())) csp.push(m.text().slice(0, 200)); });
    page.on("response", async (r) => {
      const u = r.url();
      if (/\/api\/command\/agents\/static\/(venue-team|mlb-logo)/.test(u)) imgs.push({ u: u.split("/").pop(), s: r.status(), t: r.headers()["content-type"] });
      if (/\/api\/command\/(paper\/(operations|experiment)|agents\/.*operations)/.test(u) && r.ok()) {
        try { walk(await r.json()); } catch (_) {}
      }
    });
    try {
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
        // open every work item so linked records render
        await f.evaluate(() => document.querySelectorAll("details.work-item").forEach((d) => d.open = true)).catch(() => {});
      }
      await page.waitForTimeout(4000);
      const marks = await f.evaluate(MARKS).catch((e) => ({ error: String(e) }));
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
      R.layout_shift = await page.evaluate(() => window.__cls).catch(() => null);
      await page.screenshot({ path: `${OUT}/logos_${ename}_${where}.png`, fullPage: where === "home" });
      if (where !== "home") {
        try { await f.locator(".office-layout").screenshot({ path: `${OUT}/logos_${ename}_${where}_board.png`, timeout: 20000 }); } catch (_) {}
      }
    } catch (x) { R.error = String(x).slice(0, 300); }
    await ctx.close();
  }
  // EXPIRED / ABSENT SESSION: no credential at all
  const ctx = await browser.newContext({ ...dev });
  const page = await ctx.newPage();
  const S = report.session[ename] = {};
  try {
    const r = await page.request.get(HOST + "/api/command/agents/static/venue-team-16888.png");
    S.image_without_session = r.status();
    await page.goto(HOST + "/xavier", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(8000);
    const f = frameOf(page, "xavier") || page.mainFrame();
    S.broken_images = await f.evaluate(() => [...document.images].filter((i) => i.complete && i.naturalWidth === 0).length).catch(() => null);
    S.marks = await f.evaluate(() => document.querySelectorAll(".team-mark").length).catch(() => null);
    await page.screenshot({ path: `${OUT}/logos_${ename}_signed_out.png` });
  } catch (x) { S.error = String(x).slice(0, 300); }
  await ctx.close();
  await browser.close();
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
  engines: Object.fromEntries(Object.entries(report.engines).map(([e, v]) => [e, Object.fromEntries(Object.entries(v).map(([w, r]) => [w, r.marks && { total: r.marks.total, loaded: r.marks.loaded, initials: r.marks.initials_only, broken: r.marks.broken, pairs: r.marks.pairs, csp: (r.csp_errors || []).length, cls: r.layout_shift, err: r.error }]))])) }, null, 1));
