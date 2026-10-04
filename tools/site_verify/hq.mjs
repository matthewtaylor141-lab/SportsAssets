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
    "/api/command/agents/audrey", "/api/command/agents/eddie", "/api/command/agents/scout"].join(",")).split(",");
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
await browser.close();
fs.writeFileSync(`${OUT}/hq_report.json`, JSON.stringify(report, null, 1));
for (const r of report.runs) {
  if (r.overflowers && r.overflowers.length) console.log("   overflow: " + JSON.stringify(r.overflowers));
  console.log(`== ${r.label} ${r.path} HTTP ${r.status} ${r.ms}ms title="${r.title}" build=${r.build} overflowX=${r.overflowX} (${r.scrollWidth}/${r.clientWidth}) errors=${r.errors.length} canvases=${JSON.stringify(r.canvases)} iframes=${JSON.stringify(r.iframes)}${r.employeeLinks && r.employeeLinks.length ? " employees=" + r.employeeLinks.join(",") : ""}${r.humanDirectory ? " [" + r.humanDirectory + "]" : ""}${r.homeOrder ? " order=" + r.homeOrder : ""}${r.brand ? " brand=" + JSON.stringify(r.brand) : ""}${r.characters ? " characters=" + r.characters : ""}`);
  for (const e of r.errors.slice(0, 6)) console.log("   err: " + e);
  console.log("   apis: " + r.apis.join(" | ").slice(0, 1800));
  for (const s of (r.sections || [])) console.log(`   [${s.h}] unavailable=${s.unavailable} nan=${s.nan} chars=${s.chars} :: ${s.sample}`);
}
for (const [u, p] of Object.entries(report.probes || {})) console.log(`probe ${p.status || "ERR"} ${p.bytes || 0}B ${u}`); for (const r of (report.rooms || [])) console.log(`room ${r.key} api=${r.api && r.api.status} page=${r.page_status} errors=${r.errors.length}`);
console.log("rooms_error " + (report.rooms_error || "none"));
