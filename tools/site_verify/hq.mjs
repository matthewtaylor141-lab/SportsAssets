// READ-ONLY: render the production BETTOR HQ pages (homepage equity wall,
// trading floor, position rooms, agent workspaces) signed in (x-admin-token added to /api/command/** only), at
// desktop and phone widths; report per-section text, UNAVAILABLE counts,
// suspicious zero/NaN renderings, console errors and horizontal overflow.
import fs from "node:fs";
import { chromium } from "playwright";
const HOST = process.env.HOST || "https://command.bettortoken.com";
const TOKEN = process.env.ADMIN_TOKEN || "";
const OUT = process.env.OUT || "site_verify_out";
fs.mkdirSync(OUT, { recursive: true });
const report = { at: new Date().toISOString(), host: HOST, runs: [] };
const browser = await chromium.launch();
for (const [label, vw, vh] of [["desktop", 1440, 900], ["phone", 390, 844]].filter(([l]) => !process.env.ONLY || process.env.ONLY === l)) {
  for (const path of (process.env.PAGES || "/,/floor,/company,/positions,/derek,/karen,/scout,/eddie,/allocator,/audrey,/xavier,/profitability,/acceptance,/improvements").split(",")) {
    const ctx = await browser.newContext({ viewport: { width: vw, height: vh } });
    if (TOKEN) await ctx.route(HOST + "/api/command/**", (r) =>
      r.continue({ headers: { ...r.request().headers(), "x-admin-token": TOKEN } }));
    const page = await ctx.newPage();
    const errors = []; const apis = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
    page.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 200)));
    page.on("response", (r) => { if (r.url().includes("/api/command/")) apis.push(`${r.status()} ${r.url().replace(HOST, "")}`); });
    // COMMAND FINAL readback (read-only): what this page itself was served by
    // /api/command/equity/live, /api/command/floor and /api/command/floor/{slug}
    // (parsed from its own responses), and any non-GET to /api/command/** (must be none).
    const cfT0 = Date.now(); const cfNet = { eq: [], floor: [], agent: [], nonGet: [], pending: [] };
    page.on("request", (q) => { if (q.url().includes("/api/command/") && q.method() !== "GET") cfNet.nonGet.push({ t: Date.now() - cfT0, method: q.method(), url: q.url().replace(HOST, "").slice(0, 200) }); });
    page.on("response", (r) => cfCapture(r, cfNet, cfT0));
    const t0 = Date.now();
    let status = null;
    try { status = (await page.goto(HOST + path, { waitUntil: "load", timeout: 60000 }))?.status(); } catch (e) { errors.push("GOTO " + String(e).slice(0, 160)); }
    await page.waitForTimeout(12000);
    // the floor mounts its characters one by one after the room is drawn;
    // wait (bounded) for its own completion signal before the screenshot
    let characters = null;
    if (path === "/floor") {
      characters = await page.waitForFunction(() => document.body.getAttribute("data-characters"), null, { timeout: 90000 })
        .then((h) => h.jsonValue()).catch(() => "NOT_SIGNALLED_IN_90S");
      await page.waitForTimeout(1500);
    }
    const info = await page.evaluate(() => {
      const secs = [...document.querySelectorAll("section, [data-section]")].map((s) => {
        const h = s.querySelector("h1,h2,h3"); const t = (s.innerText || "").replace(/\s+/g, " ");
        return { h: h ? h.innerText.trim().slice(0, 60) : (s.id || s.dataset.section || "?"), unavailable: (t.match(/UNAVAILABLE/g) || []).length,
                 nan: (t.match(/NaN|undefined|\[object Object\]/g) || []).length, chars: t.length, sample: t.slice(0, 160) };
      });
      const de = document.documentElement;
      return { title: document.title, sections: secs, overflowX: de.scrollWidth > de.clientWidth + 1,
               scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
               overflowers: [...document.querySelectorAll("body *")].map((el) => {
                 const r = el.getBoundingClientRect();
                 let clipped = false; for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox !== "visible") { clipped = true; break; } } return { clipped, r: Math.round(r.right), w: Math.round(r.width), sel: el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".") : "") };
               }).filter((o) => o.r > de.clientWidth + 1 && !o.clipped).sort((a, b) => b.r - a.r).slice(0, 12),
               canvases: [...document.querySelectorAll("canvas")].map((c) => { const r = c.getBoundingClientRect(); return Math.round(r.width) + "x" + Math.round(r.height); }).filter((x) => !/^0x|x0$/.test(x)),
               employeeLinks: [...document.querySelectorAll("#hq4-company-home a.hq4-agent-card, #hq4-company-agents a.hq4-agent-card, #mtg-team a, #co-team a")].map((a) => a.getAttribute("href")),
               iframes: [...document.querySelectorAll("iframe")].filter((f) => !f.hidden && f.src).map((f) => f.getAttribute("src")),
               humanDirectory: (document.body.innerText.match(/HUMAN DIRECTORY · [A-Z ]+/) || [null])[0],
               build: [document.querySelector('script[data-meeting]') && "MEETING", document.querySelector('script[data-hq4],#hq4-company-root') && "HQ4"].filter(Boolean).join("+") || "BASE",
               homeOrder: [...document.querySelectorAll("#bt-meeting-home .mtg-head, #hq4-company-home, #bt-meeting-home .mtg-attention, #bt-meeting-home .mtg-capital, #bt-meeting-home .mtg-team")].filter((e) => getComputedStyle(e).display !== "none").sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map((e) => e.id || e.className.split(" ")[0]).join(">"),
               hq5: { pulse: !!document.getElementById("hq5-pulse"), tapeHidden: !document.querySelector(".bt-hq2-market-tape") || getComputedStyle(document.querySelector(".bt-hq2-market-tape")).display === "none",
                      floorbar: document.querySelectorAll("#hq5-floorbar .hq5-view").length, palette: !!document.querySelector(".hq5-palette-backdrop"),
                      dockPortraits: [...document.querySelectorAll(".fl-agent .fl-mono")].filter((e) => /portraits\//.test(getComputedStyle(e).backgroundImage)).length },
               hq6: { rail: document.querySelectorAll(".bt-hq2-nav a").length,
                      brief: [...document.querySelectorAll("#hq6-brief .hq6-brief-card")].map((c) => (c.innerText || "").replace(/\s+/g, " ").trim().slice(0, 90)),
                      agentLayout: !!document.querySelector(".hq6-agent-layout"),
                      agentCanvas: [...document.querySelectorAll(".hq6-agent-layout canvas")].some((c) => c.getBoundingClientRect().width > 40),
                      tab: ((document.querySelector('#ws-tabs [aria-selected="true"]') || {}).textContent || "").trim() || null,
                      identityOpen: document.querySelector(".hq6-agent-more") ? document.querySelector(".hq6-agent-more").open : null,
                      now: ((document.getElementById("hq6-now-title") || {}).textContent || "") + " / " + ((document.getElementById("hq6-now-code") || {}).textContent || ""),
                      watch: document.querySelector(".hq6-watch") ? (document.querySelector(".hq6-watch").getAttribute("aria-pressed") || document.querySelector(".hq6-watch").className) : null },
               brand: { icon: [...document.querySelectorAll('link[rel~="icon"]')].map((l) => l.getAttribute("href")).join(" "),
                        rail: (getComputedStyle(document.querySelector(".bt-hq2-logo") || document.body).backgroundImage.match(/brand\/[a-z0-9-]+\.png/) || [null])[0],
                        topbar: (getComputedStyle(document.querySelector(".bt-hq2-brandword") || document.body, "::before").backgroundImage.match(/brand\/[a-z0-9-]+\.png/) || [null])[0],
                        fullLogos: [...document.querySelectorAll("img.bt-brand-logo")].filter((i) => i.naturalWidth > 0).length,
                        loneB: [...document.querySelectorAll("body *")].filter((e) => e.children.length === 0 && (e.textContent || "").trim() === "B" && e.getBoundingClientRect().width > 0).length },
               shadowLabels: (document.body.innerText.match(/SHADOW/g) || []).length,
               liveWord: (document.body.innerText.match(/\bLIVE\b/g) || []).length };
    }).catch((e) => ({ error: String(e).slice(0, 200) }));
    const shot = `${OUT}/${label}${(path === "/" ? "_home" : path.replace(/[\/.?=]/g, "_"))}.png`;
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    report.runs.push({ label, path, status, ms: Date.now() - t0, characters, errors, apis: [...new Set(apis)].slice(0, 40), ...info });
    // COMMAND FINAL per-run record (read-only), attached to the run pushed just above.
    report.runs[report.runs.length - 1].cf = await cfCollect(page, path, cfNet, cfT0).catch((e) => ({ error: String(e).slice(0, 200) }));
    // COMMAND OPS: the executive command header as this page shows it (DOM read only)
    report.runs[report.runs.length - 1].execHeader = await page.evaluate(opsHeaderProbe).catch((e) => ({ error: String(e).slice(0, 200) }));
    await ctx.close();
  }
}
// R28 production readback: authenticated GETs of the release, equity, floor,
// Xavier, positions and Profitability OS endpoints (bodies trimmed), plus up to
// three REAL position rooms discovered from the live rooms list. Read only.
if (!process.env.ONLY || process.env.ONLY === "desktop") {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  if (TOKEN) await ctx.route(HOST + "/api/command/**", (r) =>
    r.continue({ headers: { ...r.request().headers(), "x-admin-token": TOKEN } }));
  const page = await ctx.newPage();
  await page.goto(HOST + "/", { waitUntil: "load", timeout: 60000 }).catch(() => {});
  const probes = (process.env.HQ_PROBES || [
    "/api/command/release", "/api/command/equity/live", "/api/command/floor",
    "/api/command/paper/xavier", "/api/command/positions/rooms?book=PAPER",
    "/api/command/positions/rooms?book=ACTUAL", "/api/command/coverage",
    "/api/command/profitability", "/api/command/profitability/north-star",
    "/api/command/profitability/capital", "/api/command/profitability/capacity",
    "/api/command/profitability/forecast", "/api/command/profitability/sleeves",
    "/api/command/eddie", "/api/command/eddie/estimates", "/api/command/scout",
    "/api/command/tournament/models", "/api/command/tournament/agents",
    "/api/command/profitability/edge-confidence", "/api/command/experiments",
    "/api/command/twin", "/api/command/profitability/scorecards",
    "/api/command/profitability/evidence-ladder",
    "/api/command/profitability/lost-opportunities", "/api/command/profitability/opportunity-scores",
    "/api/command/profitability/forecast-horizons", "/api/command/improvements",
    "/api/command/agents/derek", "/api/command/agents/karen", "/api/command/agents/xavier",
    "/api/command/agents/audrey", "/api/command/agents/eddie", "/api/command/agents/scout",
    "/api/command/agents/derek/identity", "/api/command/agents/xavier/identity", "/api/command/agents/audrey/identity",
    "/api/command/agents/karen/identity", "/api/command/agents/allocator/identity", "/api/command/agents/eddie/identity",
    "/api/command/agents/scout/identity"].join(",")).split(",");
  report.probes = {};
  for (const u of probes) {
    report.probes[u] = await page.evaluate(async (u) => {
      try { const r = await fetch(u, { credentials: "same-origin" }); const t = await r.text();
            return { status: r.status, bytes: t.length, body: t.slice(0, 60000) }; }
      catch (e) { return { error: String(e).slice(0, 200) }; }
    }, u);
  }
  // room keys from the UNTRIMMED list (the probe body above is trimmed for the report)
  let keys = [];
  try { keys = await page.evaluate(async () => {
    const r = await fetch("/api/command/positions/rooms?book=PAPER"); const j = await r.json();
    const all = [].concat(j.rooms || [], ...Object.values(j.venues || {}).map((v) => (v && v.rooms) || []));
    return all.map((x) => x.group_key).filter(Boolean).slice(0, 3); }); } catch (e) { report.rooms_error = String(e).slice(0, 200); }
  report.rooms = [];
  for (const k of keys) {
    const detail = await page.evaluate(async (k) => {
      const r = await fetch("/api/command/positions/room/" + encodeURIComponent(k)); const t = await r.text();
      return { status: r.status, body: t.slice(0, 80000) };
    }, k);
    const pg = await ctx.newPage(); const errors = [];
    pg.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
    const st = (await pg.goto(HOST + "/position?g=" + encodeURIComponent(k), { waitUntil: "load", timeout: 60000 }).catch(() => null))?.status();
    await pg.waitForTimeout(8000);
    const text = (await pg.evaluate(() => document.body.innerText).catch(() => "")).slice(0, 6000);
    await pg.screenshot({ path: `${OUT}/room_${keys.indexOf(k)}.png`, fullPage: true }).catch(() => {});
    report.rooms.push({ key: k, api: detail, page_status: st, errors, text });
    await pg.close();
  }
  await ctx.close();
}
// COMMAND FINAL reduced-motion readback (read-only): one extra signed-in desktop
// context each for '/' and '/floor' with prefers-reduced-motion: reduce. A counter
// on window.requestAnimationFrame (installed before the page's own scripts) shows
// whether the floor keeps rendering frames while idle; nothing else is changed.
report.cf_reduced_motion = [];
if (process.env.CF_REDUCED_MOTION !== "0" && (!process.env.ONLY || process.env.ONLY === "desktop")) {
  const want = (process.env.PAGES || "/,/floor").split(",");
  for (const path of ["/", "/floor"].filter((p) => want.includes(p))) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
    if (TOKEN) await ctx.route(HOST + "/api/command/**", (r) =>
      r.continue({ headers: { ...r.request().headers(), "x-admin-token": TOKEN } }));
    await ctx.addInitScript(() => { window.__cfRaf = 0; const raf = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = (cb) => { window.__cfRaf++; return raf(cb); }; });
    const page = await ctx.newPage(); const t0 = Date.now(); const errors = []; const nonGet = [];
    page.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 200)));
    page.on("request", (q) => { if (q.url().includes("/api/command/") && q.method() !== "GET") nonGet.push({ t: Date.now() - t0, method: q.method(), url: q.url().replace(HOST, "").slice(0, 200) }); });
    let status = null;
    try { status = (await page.goto(HOST + path, { waitUntil: "load", timeout: 60000 }))?.status(); } catch (e) { errors.push("GOTO " + String(e).slice(0, 160)); }
    await page.waitForTimeout(12000);
    let characters = null;
    if (path === "/floor") {
      characters = await page.waitForFunction(() => document.body.getAttribute("data-characters"), null, { timeout: 90000 })
        .then((h) => h.jsonValue()).catch(() => "NOT_SIGNALLED_IN_90S");
      await page.waitForTimeout(3000);
    }
    const first = await page.evaluate(cfReducedProbe).catch((e) => ({ error: String(e).slice(0, 200) }));
    await page.waitForTimeout(3000);
    const second = await page.evaluate(cfReducedProbe).catch((e) => ({ error: String(e).slice(0, 200) }));
    await page.screenshot({ path: `${OUT}/reduced_motion${path === "/" ? "_home" : path.replace(/[\/.?=]/g, "_")}.png`, fullPage: true }).catch(() => {});
    report.cf_reduced_motion.push({ path, status, ms: Date.now() - t0, characters, errors, nonGet,
      rafCallsIn3s: typeof first.raf === "number" && typeof second.raf === "number" ? second.raf - first.raf : null, first, second });
    await ctx.close();
  }
}
// COMMAND OPS readback (read-only, bounded): the operations desk (OPS_PATH, default /ops) signed in
// at 1440x900 and 390x844. Per panel text and DATA NOT AVAILABLE counts; the executive header
// (API / worker / FRONTEND build SHA, MISMATCH flag) compared with /api/command/release and
// /command/build.json AS SERVED TO THIS PAGE; the opportunity table's rows before and after one
// client-side filter (restored afterwards; a DOM control, no request is sent by the probe); blotter
// rows; SOFTWARE vs ECONOMIC refusal totals; console errors; horizontal overflow; every non-GET to
// /api/command/** (must be none); and summaries of the page's own /api/command/release and
// /api/command/coverage responses so displayed == served can be compared. OPS=0 skips it.
report.ops = [];
if (process.env.OPS !== "0") {
  const opsPath = process.env.OPS_PATH || "/ops";
  const opsWait = Math.min(60000, +(process.env.OPS_WAIT_MS || 15000));
  for (const [label, vw, vh] of [["desktop", 1440, 900], ["phone", 390, 844]].filter(([l]) => !process.env.ONLY || process.env.ONLY === l)) {
    const ctx = await browser.newContext({ viewport: { width: vw, height: vh } });
    if (TOKEN) await ctx.route(HOST + "/api/command/**", (r) =>
      r.continue({ headers: { ...r.request().headers(), "x-admin-token": TOKEN } }));
    const page = await ctx.newPage(); const t0 = Date.now();
    const net = { errors: [], nonGet: [], api: [], release: [], coverage: [], build: [], pending: [] };
    page.on("console", (m) => { if (m.type() === "error") net.errors.push(m.text().slice(0, 200)); });
    page.on("pageerror", (e) => net.errors.push("PAGEERROR " + String(e).slice(0, 200)));
    page.on("request", (q) => { if (q.url().includes("/api/command/") && q.method() !== "GET") net.nonGet.push({ t: Date.now() - t0, method: q.method(), url: q.url().replace(HOST, "").slice(0, 200) }); });
    page.on("response", (r) => opsCapture(r, net, t0));
    let status = null;
    try { status = (await page.goto(HOST + opsPath, { waitUntil: "load", timeout: 60000 }))?.status(); } catch (e) { net.errors.push("GOTO " + String(e).slice(0, 160)); }
    await page.waitForTimeout(opsWait);
    const err = (e) => ({ error: String(e).slice(0, 200) });
    const dom = await page.evaluate(opsDomProbe).catch(err);
    const header = await page.evaluate(opsHeaderProbe).catch(err);
    const filter = await opsFilterProbe(page).catch(err);
    await Promise.allSettled(net.pending.slice());
    // the frontend build stamp: the page's own GET of /command/build.json, else ONE probe GET from the page
    let build = net.build.slice(-1)[0] || null;
    if (!build) build = await page.evaluate(async () => {
      try { const r = await fetch("/build.json", { cache: "no-store", credentials: "same-origin" }); const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) { j = null; }
            return { via: "probe GET", http: r.status, json: j, text: j ? null : t.slice(0, 120) }; }
      catch (e) { return { via: "probe GET", error: String(e).slice(0, 120) }; } }).catch(err);
    const rel = net.release.filter((x) => x.http === 200 && !x.parseError).slice(-1)[0] || net.release.slice(-1)[0] || null;
    const cov = net.coverage.filter((x) => x.http === 200 && !x.parseError).slice(-1)[0] || net.coverage.slice(-1)[0] || null;
    await page.screenshot({ path: `${OUT}/ops_${label}.png`, fullPage: true }).catch(() => {});
    report.ops.push({ label, path: opsPath, status, ms: Date.now() - t0, title: dom && dom.title, dom, header, filter,
                      served: { release: rel, coverage: cov, build, releaseResponses: net.release.length, coverageResponses: net.coverage.length },
                      cmp: opsCompare(header, dom, rel, cov, build), errors: net.errors.slice(0, 30), nErrors: net.errors.length, nonGet: net.nonGet,
                      apis: [...new Set(net.api)].slice(0, 60) });
    await ctx.close();
  }
}
report.ops_non_get = report.ops.flatMap((r) => r.nonGet.map((x) => ({ label: r.label, path: r.path, ...x })));
report.cf_non_get = [
  ...report.runs.flatMap((r) => ((r.cf && r.cf.nonGet) || []).map((x) => ({ label: r.label, path: r.path, ...x }))),
  ...report.cf_reduced_motion.flatMap((r) => r.nonGet.map((x) => ({ label: "reduced-motion", path: r.path, ...x })))];
await browser.close();
fs.writeFileSync(`${OUT}/hq_report.json`, JSON.stringify(report, null, 1));
for (const r of report.runs) {
  if (r.overflowers && r.overflowers.length) console.log("   overflow: " + JSON.stringify(r.overflowers));
  console.log(`== ${r.label} ${r.path} HTTP ${r.status} ${r.ms}ms title="${r.title}" build=${r.build} overflowX=${r.overflowX} (${r.scrollWidth}/${r.clientWidth}) errors=${r.errors.length} canvases=${JSON.stringify(r.canvases)} iframes=${JSON.stringify(r.iframes)}${r.employeeLinks && r.employeeLinks.length ? " employees=" + r.employeeLinks.join(",") : ""}${r.humanDirectory ? " [" + r.humanDirectory + "]" : ""}${r.homeOrder ? " order=" + r.homeOrder : ""}${r.brand ? " brand=" + JSON.stringify(r.brand) : ""}${r.hq5 ? " hq5=" + JSON.stringify(r.hq5) : ""}${r.hq6 ? " hq6=" + JSON.stringify(r.hq6) : ""}${r.characters ? " characters=" + r.characters : ""}`);
  for (const e of r.errors.slice(0, 6)) console.log("   err: " + e);
  console.log("   apis: " + r.apis.join(" | ").slice(0, 1800));
  for (const s of (r.sections || [])) console.log(`   [${s.h}] unavailable=${s.unavailable} nan=${s.nan} chars=${s.chars} :: ${s.sample}`);
}
for (const [u, p] of Object.entries(report.probes || {})) console.log(`probe ${p.status || "ERR"} ${p.bytes || 0}B ${u}`); for (const r of (report.rooms || [])) console.log(`room ${r.key} api=${r.api && r.api.status} page=${r.page_status} errors=${r.errors.length}`);
console.log("rooms_error " + (report.rooms_error || "none"));
// COMMAND FINAL readback lines: one per page run, one per reduced-motion context,
// then the non-GET total. Full objects: hq_report.json runs[].cf,
// cf_reduced_motion, cf_non_get.
for (const r of report.runs) console.log(cfLine(r));
for (const x of report.cf_reduced_motion || []) console.log(cfRmLine(x));
console.log(`cf non-GET /api/command/** requests: ${(report.cf_non_get || []).length}${(report.cf_non_get || []).length ? " " + JSON.stringify(report.cf_non_get.slice(0, 10)) : ""}`);
// COMMAND OPS readback lines: the header on every page run, one line per /ops run, the non-GET total.
// Full objects: hq_report.json runs[].execHeader, ops[], ops_non_get.
for (const r of report.runs) console.log(opsHdrLine(r.label, r.path, r.execHeader));
for (const r of report.ops || []) console.log(opsLine(r));
console.log(`ops non-GET /api/command/** requests: ${(report.ops_non_get || []).length}${(report.ops_non_get || []).length ? " " + JSON.stringify(report.ops_non_get.slice(0, 10)) : ""}`);

// ── COMMAND FINAL helpers (hoisted function declarations; read-only) ──────────
function cfCapture(r, net, t0) {
  let u; try { u = new URL(r.url()); } catch (e) { return; }
  const p = u.pathname;
  const kind = p === "/api/command/equity/live" ? "eq" : p === "/api/command/floor" ? "floor" : /^\/api\/command\/floor\/[a-z_-]+$/.test(p) ? "agent" : null;
  if (!kind) return;
  const t = Date.now() - t0, http = r.status();
  if (http !== 200) { net[kind].push({ t, http }); return; }
  net.pending.push(r.json()
    .then((j) => net[kind].push({ t, http, ...(kind === "eq" ? cfEqSummary(j) : kind === "floor" ? cfFloorSummary(j) : cfAgentDetailSummary(j, p)) }))
    .catch((e) => net[kind].push({ t, http, parseError: String(e).slice(0, 120) })));
}
function cfEqSummary(j) {
  const p = (j && j.paper) || {}, m = p.marks_as_of || {}, o = p.open_positions || {};
  return { status: p.status ?? null, why: p.why == null ? null : String(p.why).slice(0, 120), equity_usd: p.equity_usd ?? null,
           newest_at: m.newest_at ?? null, newest_age_s: m.newest_age_s ?? null, stale_mark_after_s: m.stale_mark_after_s ?? null,
           count: o.count ?? null, marked: o.marked ?? null, unmarked: o.unmarked ?? null,
           seq: (j && j.seq) ?? null, computed_at: (j && j.computed_at) ?? null, schema: (j && j.schema) ?? null };
}
function cfAgentSummary(a) {
  a = a || {}; const cut = (s, n) => (s == null ? null : String(s).slice(0, n)), own = (k) => Object.prototype.hasOwnProperty.call(a, k);
  return { slug: a.slug ?? null, agent: a.agent ?? null, state: a.state ?? null, has_work_state: own("work_state"),
           work_state: a.work_state ?? null, work_detail: cut(a.work_detail, 120), state_detail: cut(a.state_detail, 120),
           status_row_state: a.status_row ? a.status_row.state ?? null : null, status_row_errors: a.status_row ? a.status_row.errors ?? null : null,
           heartbeat_age_s: a.heartbeat ? a.heartbeat.age_s ?? null : null, has_alerts: own("alerts"), challenges: a.challenges || null };
}
function cfFloorSummary(j) {
  const edges = (j && j.edges) || [], e = edges.slice().sort((x, y) => (y.at || 0) - (x.at || 0))[0];
  return { agents: ((j && j.agents) || []).map(cfAgentSummary), edges: edges.length,
           latest_edge: e ? { from: e.from, to: e.to, kind: e.kind, at: e.at } : null };
}
function cfAgentDetailSummary(j, p) {
  const len = (x) => (Array.isArray(x) ? x.length : null), o = (j && j.outputs) || [], c = (j && j.challenges) || {};
  return { slug: p.split("/").pop(), agent: cfAgentSummary(j && j.agent), queue: len(j && j.queue), outputs: len(j && j.outputs),
           latest_output: o[0] ? { kind: o[0].kind ?? null, verdict: o[0].verdict ?? null, at: o[0].at ?? null } : null,
           timeline: len(j && j.timeline), challenges: { given: len(c.given), received: len(c.received), evaluated: len(c.evaluated) } };
}
async function cfCollect(page, path, net, t0) {
  const err = (e) => ({ error: String(e).slice(0, 200) });
  const s1 = { t: Date.now() - t0, ...(await page.evaluate(cfDomSample, path).catch(err)) };
  const assets = await page.evaluate(cfAssetHashes).catch(err);
  const smallText = await page.evaluate(cfSmallText).catch(err);
  let s2 = null;
  if (path === "/") {   // second sample 12 s after the first: the live binding may move only with served data
    await page.waitForTimeout(Math.max(0, 12000 - (Date.now() - t0 - s1.t)));
    s2 = { t: Date.now() - t0, ...(await page.evaluate(cfDomSample, path).catch(err)) };
  }
  await Promise.allSettled(net.pending.slice());
  const eq = net.eq.slice().sort((a, b) => a.t - b.t), ok = eq.filter((e) => e.http === 200 && !e.parseError);
  const servedAt = (t) => ok.filter((e) => e.t <= t).pop() || null;
  const cf = { loaded: s1.loaded || s1.error || null, assets, smallText, nonGet: net.nonGet.slice(),
               net: { eq: eq.length, eq304: eq.filter((e) => e.http === 304).length, floor: net.floor.length, agent: net.agent.length },
               eqLast: ok[ok.length - 1] || null, xavier: s1.xavier || null };
  const fl = net.floor.filter((x) => x.agents).sort((a, b) => a.t - b.t).pop();
  if (fl) cf.servedFloor = { t: fl.t, workStateInProduction: fl.agents.some((a) => a.has_work_state), edges: fl.edges, latest_edge: fl.latest_edge,
                             xavier: fl.agents.find((a) => a.slug === "xavier") || null, ...(path === "/floor" ? { agents: fl.agents } : {}) };
  if (path === "/" && s1.home) {
    const sv1 = servedAt(s1.t), sv2 = s2 ? servedAt(s2.t) : null, h1 = s1.home, h2 = (s2 && s2.home) || null;
    const disp = ["equity", "markAge", "last", "markState", "marked", "unmarked", "openpos"].filter((k) => h2 && h1[k] !== h2[k]);
    const serv = sv1 && sv2 ? ["equity_usd", "newest_at", "newest_age_s", "status", "count", "marked", "unmarked"].filter((k) => sv1[k] !== sv2[k]) : null;
    cf.home = { sample1: { t: s1.t, ...h1 }, sample2: h2 ? { t: s2.t, ...h2 } : null, served1: sv1, served2: sv2,
                cmp1: cfCompare(h1, sv1), cmp2: cfCompare(h2, sv2),
                moved: { display: disp, served: serv, equityMovedWithoutServedChange: !!(h2 && h1.equity !== h2.equity && sv1 && sv2 && sv1.equity_usd === sv2.equity_usd) },
                eqResponses: eq.slice(-24) };
  }
  if (path === "/floor" && s1.floor) {
    const F = s1.floor;
    cf.floor = { ...F, perAgent: fl ? fl.agents.map((a) => ({ slug: a.slug, state: a.state, work_state: a.has_work_state ? a.work_state : "<absent>", dock: (F.docks || {})[a.slug] || null })) : null };
    cf.floor.xavier.served = fl ? fl.agents.find((a) => a.slug === "xavier") || null : null;
    cf.floor.xavier.desk3d = "3D desk board is a WebGL canvas texture: not readable from the DOM";
  }
  if (/^\/(derek|karen|scout|eddie|allocator|audrey|xavier)$/.test(path)) {
    const ad = net.agent.filter((x) => x.agent).sort((a, b) => a.t - b.t).pop() || null;
    cf.agent = { mission: s1.agent || null, served: ad, responses: net.agent.length,
                 http: net.agent.filter((x) => x.http !== 200).map((x) => x.http).slice(0, 5) };
  }
  return cf;
}
function cfCompare(h, s) {
  if (!h || !s) return null;
  const usd = (x) => { const m = String(x || "").match(/([−-])?\s*\$\s*([\d,]+(?:\.\d+)?)/); return m ? (m[1] ? -1 : 1) * Number(m[2].replace(/,/g, "")) : null; };
  const age = (x) => { const m = String(x || "").match(/(\d+(?:\.\d+)?)\s*(s|m|h|d)\b/); if (!m) return null; const k = { s: 1, m: 60, h: 3600, d: 86400 }[m[2]]; return { sec: Number(m[1]) * k, unit: k }; };
  const same = (a, b) => (a == null || b == null ? null : String(a).trim() === String(b));
  const cents = (a, b) => (typeof a === "number" && typeof b === "number" ? Math.round(a * 100) === Math.round(b * 100) : null);
  const eqShown = usd(h.equity), lastShown = usd(h.last), ag = age(h.markAge);
  return { equity: { shown: eqShown, served: s.equity_usd, cents: cents(eqShown, s.equity_usd), dollars: typeof eqShown === "number" && typeof s.equity_usd === "number" ? Math.round(eqShown) === Math.round(s.equity_usd) : null },
           last: { shown: lastShown, cents: cents(lastShown, s.equity_usd), live: /^LIVE/.test(String(h.last || "")), frozen: /FROZEN/.test(String(h.last || "")) },
           markAge: { shown: h.markAge, served: s.newest_age_s, match: ag && typeof s.newest_age_s === "number" ? Math.abs(ag.sec - s.newest_age_s) < ag.unit + 1 : null },
           marked: { shown: h.marked, served: s.marked, match: same(h.marked, s.marked) },
           unmarked: { shown: h.unmarked, served: s.unmarked, match: same(h.unmarked, s.unmarked) },
           openpos: { shown: h.openpos, served: s.count, match: same(h.openpos, s.count) },
           markState: { shown: h.markState, servedStatus: s.status,
                        servedFresh: s.status === "OK" && typeof s.newest_age_s === "number" && s.newest_age_s <= (s.stale_mark_after_s || 300) } };
}
function cfDomSample(path) {
  const clean = (s, n) => (s == null ? null : String(s).replace(/\s+/g, " ").trim().slice(0, n || 160));
  const txt = (s) => { const e = document.querySelector(s); return e ? clean(e.textContent) : null; };
  const parts = (e) => [...e.children].map((c) => clean(c.textContent, 90)).filter(Boolean).join(" | ");
  const js = document.querySelector("script[data-command-final]"), css = document.querySelector("link[data-command-final]");
  const out = { loaded: { js: !!js, css: !!css, jsAttr: js ? js.getAttribute("data-command-final") : null, cssAttr: css ? css.getAttribute("data-command-final") : null,
                          cssSheet: !!(css && css.sheet), booted: window.__BTCommandFinal === true,
                          bodyClasses: [...document.body.classList].filter((c) => /^command-final/.test(c)) } };
  // Xavier's cards / chips / dock entries (whole page sections excluded); innerText is what is shown (text-transform applied)
  const xe = [...document.querySelectorAll('a[href="/xavier"], [data-slug="xavier"], [data-agent="xavier"], .hq4-agent-card.xavier')]
    .filter((e) => e.getClientRects().length).map((e) => ({ e, t: clean(e.getAttribute("aria-label") || e.innerText, 400) })).filter((x) => x.t && x.t.length > 2 && x.t.length < 400);
  const xs = [...new Set(xe.map((x) => x.t.slice(0, 140)))].slice(0, 10);
  const one = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "");
  out.xavier = { surfaces: xs, idleWord: xe.some((x) => /\bIDLE\b/.test(x.t)), idleAt: xe.filter((x) => /\bIDLE\b/.test(x.t)).map((x) => one(x.e) + " :: " + x.t.slice(0, 80)).slice(0, 5) };
  if (path === "/") {
    const ms = document.getElementById("cf-mark-state");
    out.home = { strip: [...document.querySelectorAll("#cf-command-strip .cf-strip-cell")].map(parts), companyState: txt("#cf-company-state"), openpos: txt("#cf-openpos"),
                 markState: txt("#cf-mark-state"), markStateClass: ms ? ms.className || null : null, markAge: txt("#cf-mark-age"),
                 marked: txt("#cf-marked"), unmarked: txt("#cf-unmarked"), equity: txt("#mtg-equity"), last: txt("#mtg-last") };
  }
  if (path === "/floor") {
    const hud = document.getElementById("cf-floor-hud"), ti = document.querySelector(".cf-hud-truth i"), st = window.__floor;
    const fx = st && st.floor && (st.floor.agents || []).find((a) => a.slug === "xavier");
    let stats = null; try { stats = st && st.scene && st.scene.stats ? st.scene.stats() : null; } catch (e) { stats = null; }
    const xd = document.querySelector('.fl-agent[data-slug="xavier"]'), xm = document.querySelector('.m-desk[data-slug="xavier"]');
    let fn = null; try { fn = fx && window.BTFloor && typeof window.BTFloor.stateOf === "function" ? window.BTFloor.stateOf(fx) : null; } catch (e) { fn = "ERR"; }
    out.floor = { hud: hud ? [...hud.children].map(parts) : null, characters: document.body.getAttribute("data-characters"),
                  hudTruthAnimations: ti ? ti.getAnimations().length : null,
                  docks: Object.fromEntries([...document.querySelectorAll(".fl-agent[data-slug]")].map((e) => [e.getAttribute("data-slug"), clean(e.innerText, 100)])),
                  xavier: { dock: xd ? clean(xd.innerText) : null, map2d: xm ? clean(xm.getAttribute("aria-label")) : null, floorStateOf: fn,
                            inMemory: fx ? { state: fx.state ?? null, work_state: fx.work_state ?? null } : null },
                  scene: stats ? { avatars: stats.avatars, walking: stats.walking, trails: stats.trails, fpsLast: (stats.fps || []).slice(-1)[0] ?? null } : null,
                  reducedMotionFn: window.BTFloor && typeof window.BTFloor.reducedMotion === "function" ? window.BTFloor.reducedMotion() : null };
  }
  if (/^\/(derek|karen|scout|eddie|allocator|audrey|xavier)$/.test(path)) {
    const m = document.getElementById("cf-agent-mission"), g = (id) => txt("#" + id), chip = document.getElementById("cf-am-state");
    const known = ["cf-am-state", "cf-am-title", "cf-am-detail", "cf-am-queue", "cf-am-queue-sub", "cf-am-output", "cf-am-output-sub", "cf-am-peer", "cf-am-peer-sub", "cf-am-attn"];
    out.agent = !m ? { present: false } : {
      present: true, eyebrow: txt("#cf-agent-mission .cf-eyebrow"), state: g("cf-am-state"), stateTone: chip ? chip.getAttribute("data-tone") : null,
      title: g("cf-am-title"), detail: g("cf-am-detail"), queue: [g("cf-am-queue"), g("cf-am-queue-sub")].join(" · "),
      output: [g("cf-am-output"), g("cf-am-output-sub")].join(" · "), peer: [g("cf-am-peer"), g("cf-am-peer-sub")].join(" · "), attention: g("cf-am-attn"),
      cells: [...m.querySelectorAll(".cf-mission-grid > div")].map(parts).slice(0, 12),
      extra: Object.fromEntries([...m.querySelectorAll('[id^="cf-am-"]')].filter((e) => !known.includes(e.id)).map((e) => [e.id, clean(e.textContent, 120)]).slice(0, 12)) };
  }
  return out;
}
async function cfAssetHashes() {
  const hex = (b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
  const out = {};
  for (const [k, q, attr, file] of [["js", "script[data-command-final]", "src", "command-final.js"], ["css", "link[data-command-final]", "href", "command-final.css"]]) {
    const el = document.querySelector(q), url = (el && el[attr]) || new URL(file, location.href).href;
    try {
      const r = await fetch(url, { cache: "no-store", credentials: "same-origin" });
      const body = new Uint8Array(await r.arrayBuffer()), head = new TextEncoder().encode("blob " + body.length + "\0");
      const blob = new Uint8Array(head.length + body.length); blob.set(head); blob.set(body, head.length);
      out[k] = { url: url.replace(location.origin, ""), tagged: !!el, http: r.status, type: r.headers.get("content-type"), etag: r.headers.get("etag"), bytes: body.length,
                 sha256: hex(await crypto.subtle.digest("SHA-256", body)), gitBlob: hex(await crypto.subtle.digest("SHA-1", blob)) };
    } catch (e) { out[k] = { url: url.replace(location.origin, ""), tagged: !!el, error: String(e).slice(0, 160) }; }
  }
  return out;
}
function cfSmallText() {
  const one = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "");
  const named = (el) => !!(el.id || (typeof el.className === "string" && el.className.trim()));
  const sel = (el) => { if (named(el)) return one(el); let a = el.parentElement, path = one(el); for (let i = 0; a && i < 3; i++, a = a.parentElement) { path = one(a) + ">" + path; if (named(a)) break; } return path; };
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), rng = document.createRange(), vis = new Map(), by = {};
  let total = 0, small = 0, minPx = null;
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const t = n.nodeValue.trim(); if (!t) continue;
    const el = n.parentElement; if (!el || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|TITLE|OPTION)$/.test(el.tagName)) continue;
    let v = vis.get(el);
    if (v === undefined) { v = el.checkVisibility ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : el.getClientRects().length > 0; vis.set(el, v); }
    if (!v) continue;
    rng.selectNodeContents(n); const rr = rng.getBoundingClientRect(); if (rr.width < 1 || rr.height < 1) continue;
    total++;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 10.5) {
      small++; minPx = minPx == null ? px : Math.min(minPx, px);
      const k = sel(el), b = by[k] || (by[k] = { n: 0, px: Math.round(px * 10) / 10, sample: t.slice(0, 40) }); b.n++;
    }
  }
  return { total, small, minPx, bySelector: Object.entries(by).sort((a, b) => b[1].n - a[1].n).slice(0, 15).map(([s, b]) => ({ sel: s, ...b })) };
}
function cfReducedProbe() {
  const one = (el) => (!el || !el.tagName ? "?" : el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""));
  const i = document.querySelector(".cf-hud-truth i"), st = window.__floor, anims = document.getAnimations(), markers = [];
  let stats = null; try { stats = st && st.scene && st.scene.stats ? st.scene.stats() : null; } catch (e) { stats = { error: String(e).slice(0, 120) }; }
  for (const el of [document.documentElement, document.body]) for (const a of el.attributes) if (/reduc|motion/i.test(a.name + "=" + a.value)) markers.push(el.tagName.toLowerCase() + "[" + a.name + "=" + a.value.slice(0, 60) + "]");
  document.querySelectorAll("[data-reduced-motion],[data-motion],[class*='reduced']").forEach((e) => markers.push(one(e)));
  const fpsEl = document.querySelector("[data-fps]");
  return { mediaReduce: matchMedia("(prefers-reduced-motion: reduce)").matches,
           hudTruth: i ? { animations: i.getAnimations().length, animationName: getComputedStyle(i).animationName } : null,
           docAnimations: anims.length,
           animTargets: [...new Set(anims.map((a) => one(a.effect && a.effect.target) + ":" + (a.animationName || a.transitionProperty || a.constructor.name)))].slice(0, 10),
           btFloorReducedMotion: window.BTFloor && typeof window.BTFloor.reducedMotion === "function" ? window.BTFloor.reducedMotion() : null,
           scene: stats && !stats.error ? { avatars: stats.avatars, walking: stats.walking, trails: stats.trails, fpsSamples: (stats.fps || []).length, fpsLast: (stats.fps || []).slice(-1)[0] ?? null } : stats,
           dataFps: fpsEl ? fpsEl.getAttribute("data-fps") : null,
           mapPulse: document.querySelectorAll(".m-pulse").length, mapFlowEdges: document.querySelectorAll(".m-edge.flow").length,
           characters: document.body.getAttribute("data-characters"),
           raf: typeof window.__cfRaf === "number" ? window.__cfRaf : null, markers: markers.slice(0, 12) };
}
function cfLine(r) {
  const c = r.cf || {}, q = (v) => JSON.stringify(v === undefined ? null : v);
  if (c.error) return `cf ${r.label} ${r.path} ERROR ${c.error}`;
  const h = (a) => (!a ? "none" : a.error ? "ERR " + a.error : `${a.http} ${a.type || "?"} ${a.bytes}B sha256=${a.sha256} blob=${a.gitBlob}`);
  const L = c.loaded || {}, A = c.assets || {}, T = c.smallText || {};
  let s = `cf ${r.label} ${r.path} layer js=${L.js ? "Y" : "N"} css=${L.css ? "Y" : "N"} booted=${L.booted ? "Y" : "N"} js[${h(A.js)}] css[${h(A.css)}]` +
          ` text<10.5px=${T.small ?? "?"}/${T.total ?? "?"}${T.minPx != null ? " min=" + T.minPx + "px" : ""} nonGet=${(c.nonGet || []).length} eqResp=${c.net ? c.net.eq : 0}`;
  if (c.xavier && c.xavier.surfaces && c.xavier.surfaces.length) s += ` xavierSurfaces=${c.xavier.surfaces.length} xavierIDLE=${c.xavier.idleWord ? "Y" + JSON.stringify(c.xavier.idleAt || []) : "N"}`;
  if (c.servedFloor) { const x = c.servedFloor.xavier; s += ` servedWorkState=${c.servedFloor.workStateInProduction ? "Y" : "N"} servedXavier=${q(x && [x.state, x.has_work_state ? x.work_state : "work_state:absent"])}`; }
  const ok = (m) => (!m ? "n/a" : ["equity", "markAge", "marked", "unmarked", "openpos"].map((k) => k + ":" + (m[k].match ?? m[k].cents) ).join(",") + ",last¢:" + m.last.cents);
  if (c.home) {
    const H = c.home, a = H.sample1 || {}, b = H.sample2 || {}, sv = (x) => x && [x.status, x.equity_usd, x.newest_age_s, x.count, x.marked, x.unmarked];
    s += ` home{strip=${q(a.strip)} mark=${q(a.markState)}->${q(b.markState)} age=${q(a.markAge)}->${q(b.markAge)} marked=${q(a.marked)} unmarked=${q(a.unmarked)}` +
         ` eq=${q(a.equity)}->${q(b.equity)} last=${q(a.last)}->${q(b.last)} served[status,eq,age,n,marked,unmarked]=${q(sv(H.served1))}->${q(sv(H.served2))}` +
         ` match1=${ok(H.cmp1)} match2=${ok(H.cmp2)} moved=${q(H.moved)}}`;
  }
  if (c.floor) {
    const F = c.floor;
    s += ` floor{hud=${q(F.hud)} chars=${F.characters} hudTruthAnim=${F.hudTruthAnimations} scene=${q(F.scene)}` +
         ` agents=${q((F.perAgent || []).map((a) => `${a.slug}:${a.state}/${a.work_state}`))} xavier{dock=${q(F.xavier.dock)} map2d=${q(F.xavier.map2d)} stateOf=${q(F.xavier.floorStateOf)}}}`;
  }
  if (c.agent) {
    const M = c.agent.mission || {}, S = c.agent.served, a = S && S.agent;
    s += ` agent{mission=${M.present ? q([M.state, M.title, M.detail, M.queue, M.output, M.peer, M.attention]) : "ABSENT"}${M.extra && Object.keys(M.extra).length ? " extra=" + q(M.extra) : ""}` +
         ` served=${a ? q([a.state, a.has_work_state ? a.work_state : "work_state:absent", a.work_detail || a.state_detail]) : "NOT_CAPTURED"}}`;
  }
  return s;
}
function cfRmLine(x) {
  const a = x.first || {}, b = x.second || {};
  return `cf-rm ${x.path} HTTP ${x.status} ${x.ms}ms reduce=${a.mediaReduce} hudTruthAnim=${a.hudTruth ? a.hudTruth.animations + "(" + a.hudTruth.animationName + ")" : "n/a"}` +
         ` docAnimations=${a.docAnimations}->${b.docAnimations} btFloorReduced=${a.btFloorReducedMotion} rafCallsIn3s=${x.rafCallsIn3s}` +
         ` scene=${JSON.stringify(b.scene || null)} pulse=${b.mapPulse} flowEdges=${b.mapFlowEdges} chars=${x.characters} markers=${JSON.stringify(b.markers || [])}` +
         ` animTargets=${JSON.stringify(b.animTargets || [])} errors=${x.errors.length} nonGet=${x.nonGet.length}`;
}

// ── COMMAND OPS helpers (hoisted function declarations; read-only) ───────────
function opsCapture(r, net, t0) {
  let u; try { u = new URL(r.url()); } catch (e) { return; }
  const p = u.pathname, t = Date.now() - t0, http = r.status();
  if (p.startsWith("/api/command/") && net.api.length < 400) net.api.push(`${http} ${p}${u.search}`.slice(0, 160));
  const kind = p === "/api/command/release" ? "release" : p === "/api/command/coverage" ? "coverage" : (p === "/build.json" || p === "/command/build.json") ? "build" : null;
  if (!kind) return;
  if (http !== 200) { net[kind].push({ t, http, path: p + u.search }); return; }
  net.pending.push(r.json()
    .then((j) => net[kind].push({ t, http, path: p + u.search, ...(kind === "release" ? opsReleaseSummary(j) : kind === "coverage" ? opsCoverageSummary(j) : { via: "page", json: j }) }))
    .catch((e) => net[kind].push({ t, http, path: p + u.search, parseError: String(e).slice(0, 120) })));
}
function opsReleaseSummary(j) {
  j = j || {}; const a = j.api || {}, w = j.workers || {}, sc = j.schema || {}, rc = j.receipts || {};
  return { version: j.version ?? null, generated_at: j.generated_at ?? null,
           api: { status: a.status ?? null, sha: a.sha ?? null, short: a.short ?? null, why: a.why ?? null },
           workers: { status: w.status ?? null, sha: w.sha ?? null, short: w.short ?? null, boot_at: w.boot_at ?? null, venue_writes: w.venue_writes ?? null, why: w.why ?? null },
           alignment: j.alignment || null,
           schema: { status: sc.status ?? null, max_version: sc.max_version ?? null, applied_count: sc.applied_count ?? null,
                     build_ahead_of_database: Array.isArray(sc.build_ahead_of_database) ? sc.build_ahead_of_database.length : null, numbers_absent: sc.numbers_absent ?? null, why: sc.why ?? null },
           receipts: { status: rc.status ?? null, items: Array.isArray(rc.items) ? rc.items.length : null }, running_build_receipts: j.running_build_receipts ?? null, running_build_gated: j.running_build_gated ?? null };
}
function opsCoverageSummary(j) {
  j = j || {}; const days = Array.isArray(j.days) ? j.days : [], today = days[0] || null, ls = j.league_status || {}, ps = j.provider_supplement || {}, al = Array.isArray(j.alerts) ? j.alerts : [];
  const C = ["provider_events", "normalized_events", "venue_discovered", "mapped_events", "settlement_supported", "evaluated_events", "decided_events", "entered_events", "ordered_events", "filled_events"];
  const totals = {};
  if (today) for (const c of C) { const v = (today.leagues || []).map((r) => r[c]); totals[c] = v.some((x) => x == null) ? null : v.reduce((a, x) => a + x, 0); }
  const by = {}; al.forEach((a) => { const k = a.kind + ":" + a.severity; by[k] = (by[k] || 0) + 1; });
  return { version: j.version ?? null, status: j.status ?? null, why: j.why ?? null, tz: j.tz ?? null, as_of: j.as_of ?? null, today_computed_live: j.today_computed_live ?? null, days: days.length,
           today: today ? { day: today.day, totals, leagues: (today.leagues || []).slice(0, 40).map((r) => ({ league: r.league, league_name: r.league_name, ...Object.fromEntries(C.map((c) => [c, r[c] ?? null])),
                                                                                                              unavailable: Object.keys(r.unavailable || {}) })) } : null,
           league_status: ls.statuses ? { day: ls.day ?? null, summary: ls.summary || null, collector: ls.collector || null,
                                          statuses: ls.statuses.slice(0, 40).map((s) => ({ league: s.league, league_name: s.league_name, status: s.status, stage: s.stage ?? null,
                                                                                           reason: s.reason == null ? null : String(s.reason).slice(0, 140), provider_events: (s.counts || {}).provider_events ?? null })) }
                                     : { status: ls.status ?? null, why: ls.why ?? null },
           alerts: { n: al.length, by, latest: al.slice(0, 6).map((a) => ({ kind: a.kind, league: a.league, stage_from: a.stage_from, stage_to: a.stage_to, severity: a.severity, day: a.day, detected_at: a.detected_at })) },
           provider_supplement: { status: ps.status ?? null, why: ps.why ?? null, age_s: ps.age_s ?? null, matched_events: (ps.census || {}).matched_events ?? null, total_contracts: (ps.census || {}).total_contracts ?? null },
           nfl_reconciliation: j.nfl_reconciliation ? { status: j.nfl_reconciliation.status ?? null, expected: j.nfl_reconciliation.expected ?? null, reached: j.nfl_reconciliation.reached || null } : null };
}
function opsHeaderProbe() {
  const clean = (s, n) => (s == null ? null : String(s).replace(/\s+/g, " ").trim().slice(0, n || 300));
  const one = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "");
  const vis = (e) => !!e && e.getClientRects().length > 0;
  const hdr = [...document.querySelectorAll("[data-exec-header], #exec-header, #cmd-exec-header, #ops-exec-header, .exec-header, .cmd-exec-header, [data-component='exec-header']")].find(vis)
    || [...document.querySelectorAll("header, [role='banner'], [class*='exec'], [class*='header']")].filter(vis).find((e) => /\bWORKER/i.test(e.innerText || "") && /\bAPI\b/i.test(e.innerText || "") && (e.innerText || "").length < 3000) || null;
  if (!hdr) return { present: false };
  const t = clean(hdr.innerText, 3000) || "";
  const hex = (re) => { const m = t.match(re); return m ? m[1].toLowerCase() : null; };
  const fields = Object.fromEntries([...hdr.querySelectorAll("[data-field], [data-key]")].slice(0, 40).map((e) => [e.getAttribute("data-field") || e.getAttribute("data-key"), clean(e.innerText, 120)]));
  const mm = [...hdr.querySelectorAll("*")].find((e) => e.children.length === 0 && /MISMATCH/i.test(e.textContent || ""));
  const r = hdr.getBoundingClientRect(), cs = getComputedStyle(hdr);
  return { present: true, sel: one(hdr), position: cs.position, top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width), overflowsX: hdr.scrollWidth > hdr.clientWidth + 1,
           text: t.slice(0, 900), fields,
           apiSha: hex(/\bAPI(?:\s+SHA)?\b\W{0,8}([0-9a-f]{7,40})\b/i), workerSha: hex(/\bWORKERS?(?:\s+SHA)?\b\W{0,8}([0-9a-f]{7,40})\b/i), frontendSha: hex(/\bFRONTEND(?:\s+(?:SHA|BUILD))?\b\W{0,8}([0-9a-f]{7,40})\b/i),
           hexTokens: (t.match(/\b[0-9a-f]{7,40}\b/gi) || []).slice(0, 12),
           mismatch: /MISMATCH/i.test(t), mismatchColor: mm ? getComputedStyle(mm).color + " / " + getComputedStyle(mm).backgroundColor : null,
           dna: (t.match(/DATA NOT AVAILABLE/g) || []).length, unavailable: (t.match(/UNAVAILABLE/g) || []).length,
           words: { production: /PRODUCTION/.test(t), paper: /PAPER/.test(t), shadow: /SHADOW/.test(t), smallLive: /SMALL\s*LIVE/i.test(t), pinnapi: /PINN\s*API/i.test(t), incident: /INCIDENT/i.test(t), stale: /STALE/i.test(t) } };
}
function opsDomProbe() {
  const clean = (s, n) => (s == null ? null : String(s).replace(/\s+/g, " ").trim().slice(0, n || 300));
  const one = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".") : "");
  const vis = (e) => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  const count = (t, re) => ((t || "").match(re) || []).length;
  const rowsOf = (root) => [...root.querySelectorAll("tbody tr, [role='row']:not(:first-child), [data-row]")].filter(vis);
  const marked = [...document.querySelectorAll("[data-panel], [data-ops-panel]")].filter(vis);
  let panels = marked.length ? marked : [...document.querySelectorAll("main section, section, .ops-panel, [class*='panel']")].filter(vis);
  if (!marked.length) panels = panels.filter((p) => !panels.some((q) => q !== p && p.contains(q) && q.tagName === p.tagName));   // innermost sections
  const name = (s) => s.getAttribute("data-panel") || s.getAttribute("data-ops-panel") || s.id || clean((s.querySelector("h1,h2,h3,h4,[data-title]") || {}).innerText, 60) || one(s);
  const P = panels.slice(0, 40).map((s) => {
    const t = clean(s.innerText, 200000) || "", h = s.querySelector("h1,h2,h3,h4,[data-title]");
    return { key: name(s), sel: one(s), h: h ? clean(h.innerText, 80) : null, dna: count(t, /DATA NOT AVAILABLE/g), unavailable: count(t, /UNAVAILABLE/g),
             nan: count(t, /NaN|undefined|\[object Object\]/g), tables: s.querySelectorAll("table").length, rows: rowsOf(s).length, chars: t.length, text: t.slice(0, 500) };
  });
  const find = (re) => panels.find((s) => re.test(name(s) + " " + clean((s.querySelector("h1,h2,h3,h4,[data-title]") || {}).innerText, 80)));
  const tableOf = (s) => { if (!s) return null; const tb = [...s.querySelectorAll("table")].filter(vis)[0] || null; const rows = tb ? rowsOf(tb) : rowsOf(s);
    return { panel: name(s), headers: tb ? [...tb.querySelectorAll("thead th")].map((th) => clean(th.innerText, 40)).slice(0, 30) : [], rows: rows.length,
             sample: rows.slice(0, 3).map((r) => clean(r.innerText, 200)), bound: (clean(s.innerText, 100000).match(/(?:showing|top|latest|first|last|limit|bounded)[^.|·]{0,60}/i) || [null])[0] }; };
  const opp = find(/opportun/i), blot = find(/blotter|orders\s*(?:&|and)\s*fills|fills/i), refp = find(/refus|lost\s*opp|losses/i), funnel = find(/funnel/i), matrix = find(/coverage|matrix|sport/i), pinn = find(/pinn\s*api|feed/i),
        cap = find(/capital|exposure/i), agents = find(/agent/i), inc = find(/incident|system\s*status/i);
  const section = (root, re) => {
    if (!root) return null;
    const lab = [...root.querySelectorAll("h1,h2,h3,h4,h5,h6,th,caption,legend,strong,b,[data-class],[data-kind],summary,span,div")].filter(vis).find((e) => e.children.length < 4 && re.test(clean(e.innerText, 60) || "") && (clean(e.innerText, 200) || "").length < 200);
    if (!lab) return null;
    const box = lab.closest("[data-class], [data-kind], section, article, details, .card, div") || lab;
    const t = clean(box.innerText, 2000) || "", n = lab.closest("[data-count]") ? +lab.closest("[data-count]").getAttribute("data-count") : null;
    const m = t.replace(re, "§").match(/§[^0-9]{0,40}?(\d[\d,]*)/);
    return { label: clean(lab.innerText, 60), count: n != null && Number.isFinite(n) ? n : m ? +m[1].replace(/,/g, "") : null, text: t.slice(0, 300) };
  };
  const de = document.documentElement, body = document.body.innerText || "";
  const matrixRows = matrix ? [...matrix.querySelectorAll("tbody tr")].filter(vis).slice(0, 20).map((tr) => [...tr.children].map((c) => clean(c.innerText, 40))) : [];
  return { title: document.title, url: location.pathname, dnaTotal: count(body, /DATA NOT AVAILABLE/g), unavailableTotal: count(body, /UNAVAILABLE/g), nanTotal: count(body, /NaN|undefined|\[object Object\]/g),
           panels: P, opportunities: tableOf(opp), blotter: tableOf(blot), agents: tableOf(agents),
           refusals: refp ? { panel: name(refp), software: section(refp, /SOFTWARE/i), economic: section(refp, /ECONOMIC/i), unclassified: section(refp, /UNCLASSIFIED/i) } : null,
           funnel: funnel ? { panel: name(funnel), text: clean(funnel.innerText, 1200) } : null, matrix: matrix ? { panel: name(matrix), rows: matrixRows } : null,
           pinnapi: pinn ? { panel: name(pinn), text: clean(pinn.innerText, 600) } : null, capital: cap ? { panel: name(cap), text: clean(cap.innerText, 600) } : null,
           incidents: inc ? { panel: name(inc), text: clean(inc.innerText, 800) } : null,
           overflowX: de.scrollWidth > de.clientWidth + 1, scrollWidth: de.scrollWidth, clientWidth: de.clientWidth,
           overflowers: [...document.querySelectorAll("body *")].map((el) => {
             const r = el.getBoundingClientRect(); let clipped = false;
             for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { if (getComputedStyle(a).overflowX !== "visible") { clipped = true; break; } }
             return { clipped, r: Math.round(r.right), w: Math.round(r.width), sel: one(el) }; }).filter((o) => o.r > de.clientWidth + 1 && !o.clipped).sort((a, b) => b.r - a.r).slice(0, 12) };
}
// One client-side filter on the opportunity table: rows before, after, restored. The probe only uses
// a control the page renders (select / checkbox / pressed-button); it sends no request of its own.
async function opsFilterProbe(page) {
  const rows = () => page.evaluate(() => {
    const vis = (e) => !!e && e.getClientRects().length > 0;
    const panels = [...document.querySelectorAll("[data-panel], [data-ops-panel], section")].filter(vis);
    const p = panels.find((s) => /opportun/i.test((s.getAttribute("data-panel") || s.getAttribute("data-ops-panel") || s.id || "") + " " + ((s.querySelector("h1,h2,h3,h4") || {}).innerText || "")));
    if (!p) return null;
    const tb = [...p.querySelectorAll("table")].filter(vis)[0] || p;
    const r = [...tb.querySelectorAll("tbody tr, [data-row]")].filter(vis);
    return { n: r.length, first: r[0] ? String(r[0].innerText).replace(/\s+/g, " ").trim().slice(0, 160) : null };
  });
  const pick = () => page.evaluate(() => {
    const vis = (e) => !!e && e.getClientRects().length > 0;
    const panels = [...document.querySelectorAll("[data-panel], [data-ops-panel], section")].filter(vis);
    const p = panels.find((s) => /opportun/i.test((s.getAttribute("data-panel") || s.getAttribute("data-ops-panel") || s.id || "") + " " + ((s.querySelector("h1,h2,h3,h4") || {}).innerText || "")));
    if (!p) return null;
    const all = [...document.querySelectorAll("select, input[type=checkbox], button[aria-pressed], [data-filter]")];
    const inP = all.filter((e) => p.contains(e) && vis(e) && !e.disabled);
    const lab = (e) => ((e.getAttribute("aria-label") || e.name || e.id || "") + " " + ((e.labels && e.labels[0] && e.labels[0].innerText) || e.innerText || "")).replace(/\s+/g, " ").trim().slice(0, 60);
    const sel = inP.filter((e) => e.tagName === "SELECT" && e.options.length >= 2);
    const s = sel.find((e) => /sport/i.test(lab(e))) || sel[0];
    if (s) { const opt = [...s.options].find((o) => o.value !== s.value && o.value !== "") || [...s.options].find((o) => o.value !== s.value);
             if (opt) return { index: all.indexOf(s), type: "select", label: lab(s), original: s.value, value: opt.value, optionText: opt.text.slice(0, 40) }; }
    const cb = inP.filter((e) => e.type === "checkbox"), c = cb.find((e) => /positive|ev/i.test(lab(e))) || cb[0];
    if (c) return { index: all.indexOf(c), type: "checkbox", label: lab(c), original: c.checked };
    const b = inP.find((e) => e.tagName !== "SELECT" && e.type !== "checkbox");
    if (b) return { index: all.indexOf(b), type: "button", label: lab(b), original: b.getAttribute("aria-pressed") };
    return { index: -1 };
  });
  const before = await rows();
  if (!before) return { found: false, why: "no opportunity panel (data-panel / id / heading matching /opportun/i)" };
  const ctl = await pick();
  if (!ctl || ctl.index < 0) return { found: true, before: before.n, filter: null, why: "no filter control inside the opportunity panel" };
  const loc = page.locator("select, input[type=checkbox], button[aria-pressed], [data-filter]").nth(ctl.index);
  if (ctl.type === "select") await loc.selectOption(ctl.value); else await loc.click();
  await page.waitForTimeout(1500);
  const after = await rows();
  if (ctl.type === "select") await loc.selectOption(ctl.original); else await loc.click();
  await page.waitForTimeout(800);
  const restored = await rows();
  return { found: true, filter: ctl, before: before.n, after: after ? after.n : null, restored: restored ? restored.n : null,
           changed: !!after && after.n !== before.n, firstBefore: before.first, firstAfter: after ? after.first : null };
}
function opsCompare(H, dom, rel, cov, build) {
  H = H || {};
  const okRel = rel && rel.http === 200 && rel.api;
  const pre = (full, shown) => (full && shown ? String(full).toLowerCase().startsWith(String(shown).toLowerCase()) : null);
  const bj = build && build.json ? build.json : null;
  const out = {
    release: okRel ? "served" : rel ? "HTTP " + rel.http + (rel.parseError ? " " + rel.parseError : "") : "the page did not GET /api/command/release",
    apiSha: { shown: H.apiSha ?? null, served: okRel ? rel.api.sha : null, servedStatus: okRel ? rel.api.status : null, match: okRel ? pre(rel.api.sha, H.apiSha) : null },
    workerSha: { shown: H.workerSha ?? null, served: okRel ? rel.workers.sha : null, servedStatus: okRel ? rel.workers.status : null, match: okRel ? pre(rel.workers.sha, H.workerSha) : null },
    mismatch: { shown: !!H.mismatch, served: okRel && rel.alignment ? rel.alignment.verdict : null,
                consistent: okRel && rel.alignment && rel.alignment.verdict !== "UNKNOWN" ? (rel.alignment.verdict === "MISALIGNED") === !!H.mismatch : null },
    frontendSha: { shown: H.frontendSha ?? null, served: bj ? bj.sha ?? null : null, context: bj ? bj.context ?? null : null, via: build ? build.via || "page" : null, http: build ? build.http ?? 200 : null,
                   match: bj ? pre(bj.sha, H.frontendSha) : null },
    coverage: cov && cov.http === 200 && !cov.parseError ? { served: { status: cov.status, summary: cov.league_status && cov.league_status.summary, alerts: cov.alerts && cov.alerts.n,
                                                                       provider_supplement: cov.provider_supplement && cov.provider_supplement.status, todayTotals: cov.today && cov.today.totals },
                                                              matrix: opsMatrixCheck(dom && dom.matrix, cov) }
                                                          : cov ? { served: "HTTP " + cov.http } : { served: "the page did not GET /api/command/coverage" } };
  return out;
}
function opsMatrixCheck(matrix, cov) {
  // heuristic: a matrix row whose first cell names a served league shows that league's served provider_events
  if (!matrix || !matrix.rows || !cov || !cov.today) return null;
  const by = {}; (cov.today.leagues || []).forEach((l) => { by[String(l.league_name || "").toUpperCase()] = l; });
  return matrix.rows.map((cells) => { const k = String(cells[0] || "").toUpperCase().split(/\s/)[0], l = by[k];
    return { row: k, served_provider_events: l ? l.provider_events : null, shownInRow: l && l.provider_events != null ? cells.slice(1).includes(String(l.provider_events)) : null }; });
}
function opsHdrLine(label, path, h) {
  if (!h) return `ops-hdr ${label} ${path} NOT_READ`;
  if (h.error) return `ops-hdr ${label} ${path} ERROR ${h.error}`;
  if (!h.present) return `ops-hdr ${label} ${path} header=ABSENT`;
  return `ops-hdr ${label} ${path} header=${h.sel} pos=${h.position} h=${h.height} api=${h.apiSha} worker=${h.workerSha} frontend=${h.frontendSha} mismatch=${h.mismatch ? "Y" : "N"} dna=${h.dna} overflowX=${h.overflowsX ? "Y" : "N"}`;
}
function opsLine(r) {
  const q = (v) => JSON.stringify(v === undefined ? null : v), d = r.dom || {}, h = r.header || {}, f = r.filter || {}, c = r.cmp || {}, rel = (r.served || {}).release, cov = (r.served || {}).coverage;
  const rf = d.refusals || {};
  return `ops ${r.label} ${r.path} HTTP ${r.status} ${r.ms}ms title=${q(r.title)} panels=${(d.panels || []).length} DNA=${d.dnaTotal ?? "?"} UNAVAILABLE=${d.unavailableTotal ?? "?"} nan=${d.nanTotal ?? "?"}` +
         ` header{${h.present ? `api=${h.apiSha} worker=${h.workerSha} frontend=${h.frontendSha} mismatch=${h.mismatch ? "Y" : "N"} pos=${h.position}` : "ABSENT"}}` +
         ` cmp{api=${c.apiSha && c.apiSha.match} worker=${c.workerSha && c.workerSha.match} mismatchConsistent=${c.mismatch && c.mismatch.consistent} frontend=${c.frontendSha && c.frontendSha.match}(${c.frontendSha && c.frontendSha.context})}` +
         ` opp{rows=${d.opportunities ? d.opportunities.rows : "NO_PANEL"} filter=${f.filter ? q([f.filter.type, f.filter.label, f.filter.value ?? null]) : q(f.why || null)} ${f.before ?? "?"}->${f.after ?? "?"}->${f.restored ?? "?"}}` +
         ` blotter=${d.blotter ? d.blotter.rows : "NO_PANEL"} refusals{software=${rf.software ? rf.software.count : "?"} economic=${rf.economic ? rf.economic.count : "?"} unclassified=${rf.unclassified ? rf.unclassified.count : "?"}}` +
         ` errors=${r.nErrors} overflowX=${d.overflowX} (${d.scrollWidth}/${d.clientWidth}) nonGet=${(r.nonGet || []).length}` +
         ` served{release=${rel ? (rel.http === 200 ? q([rel.api && rel.api.short, rel.workers && rel.workers.short, rel.alignment && rel.alignment.verdict]) : "HTTP " + rel.http) : "NOT_FETCHED"}` +
         ` coverage=${cov ? (cov.http === 200 ? q([cov.status, cov.league_status && cov.league_status.summary, cov.provider_supplement && cov.provider_supplement.status, cov.alerts && cov.alerts.n]) : "HTTP " + cov.http) : "NOT_FETCHED"}}`;
}
