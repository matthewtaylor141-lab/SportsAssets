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
  for (const path of (process.env.PAGES || "/,/floor,/positions,/xavier,/allocator,/eddie,/scout,/profitability,/acceptance,/improvements").split(",")) {
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
               shadowLabels: (document.body.innerText.match(/SHADOW/g) || []).length,
               liveWord: (document.body.innerText.match(/\bLIVE\b/g) || []).length };
    }).catch((e) => ({ error: String(e).slice(0, 200) }));
    const shot = `${OUT}/${label}${(path === "/" ? "_home" : path.replace(/[\/.?=]/g, "_"))}.png`;
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    report.runs.push({ label, path, status, ms: Date.now() - t0, errors, apis: [...new Set(apis)].slice(0, 40), ...info });
    await ctx.close();
  }
}
await browser.close();
fs.writeFileSync(`${OUT}/hq_report.json`, JSON.stringify(report, null, 1));
for (const r of report.runs) {
  if (r.overflowers && r.overflowers.length) console.log("   overflow: " + JSON.stringify(r.overflowers));
  console.log(`== ${r.label} ${r.path} HTTP ${r.status} ${r.ms}ms title="${r.title}" overflowX=${r.overflowX} (${r.scrollWidth}/${r.clientWidth}) errors=${r.errors.length} SHADOW=${r.shadowLabels}`);
  for (const e of r.errors.slice(0, 6)) console.log("   err: " + e);
  console.log("   apis: " + r.apis.join(" | ").slice(0, 1800));
  for (const s of (r.sections || [])) console.log(`   [${s.h}] unavailable=${s.unavailable} nan=${s.nan} chars=${s.chars} :: ${s.sample}`);
}
