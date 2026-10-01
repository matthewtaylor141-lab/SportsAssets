// PRODUCTION MANAGEMENT-SITE VERIFICATION (read-only).
// Renders the homepage and /derek, /xavier, /audrey on command.bettortoken.com
// in mobile WebKit (iPhone 13 emulation: the Safari engine, NOT a physical
// iPhone) and phone-sized Chromium, AUTHENTICATED and SIGNED OUT.
// Authenticated = the admin token attached ONLY to this site's own
// /api/command/* requests (never to any other host), not the cookie sign-in
// flow. Writes screenshots and report.json to OUT; prints a summary. GETs only.
import { webkit, chromium, devices } from "playwright";
import fs from "node:fs";

const HOST = process.env.HOST || "https://command.bettortoken.com";
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
fs.mkdirSync(OUT, { recursive: true });
const PAGES = ["/", "/derek", "/xavier", "/audrey"];
const ENGINES = [
  ["webkit-iphone13", webkit, devices["iPhone 13"]],
  ["chromium-390x844", chromium, { viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2, isMobile: true, hasTouch: true }],
];
const report = [];
for (const [ename, engine, dev] of ENGINES) {
  const browser = await engine.launch();
  for (const auth of ["authenticated", "signed-out"]) {
    const ctx = await browser.newContext({ ...dev });
    if (auth === "authenticated" && TOKEN) {
      await ctx.route(HOST + "/api/command/**", (route) =>
        route.continue({ headers: { ...route.request().headers(),
                                    "x-admin-token": TOKEN } }));
    }
    for (const path of PAGES) {
      const page = await ctx.newPage();
      const api = [];
      const consoleErr = [];
      page.on("response", (r) => {
        const u = r.url();
        if (u.startsWith(HOST + "/api/command/"))
          api.push({ path: u.slice(HOST.length).split("?")[0],
                     status: r.status() });
      });
      page.on("console", (m) => { if (m.type() === "error")
        consoleErr.push(m.text().slice(0, 160)); });
      let nav = null;
      try {
        const resp = await page.goto(HOST + path,
                                     { waitUntil: "domcontentloaded",
                                       timeout: 45000 });
        nav = resp ? resp.status() : null;
      } catch (e) { nav = "ERROR " + String(e).slice(0, 120); }
      await page.waitForTimeout(9000);
      const frames = page.frames();
      const texts = [];
      let overflow = [];
      for (const f of frames) {
        try {
          const t = await f.evaluate(() => document.body ?
            document.body.innerText.slice(0, 20000) : "");
          texts.push(t);
          const o = await f.evaluate(() => {
            const cw = document.documentElement.clientWidth;
            const wide = [];
            for (const el of document.querySelectorAll("body *")) {
              const r = el.getBoundingClientRect();
              if (r.right > cw + 1 && r.width > 0 && wide.length < 5)
                wide.push((el.tagName + "." + (el.className || "")
                           .toString().slice(0, 40) + "#" + (el.id || ""))
                          + " right=" + Math.round(r.right));
            }
            return { url: location.pathname,
                     scrollW: document.documentElement.scrollWidth,
                     clientW: cw, wide };
          });
          if (o.scrollW > o.clientW + 1) overflow.push(o);
        } catch (e) { /* cross-origin or detached */ }
      }
      const all = texts.join("\n");
      const grab = (re) => { const m = all.match(re); return m ? m[0] : null; };
      const shot = `${OUT}/${ename}_${auth}_${path === "/" ? "home" :
                    path.slice(1)}.png`;
      try { await page.screenshot({ path: shot, fullPage: true,
                                    timeout: 20000 }); }
      catch (e) {
        try { await page.screenshot({ path: shot, timeout: 10000 }); }
        catch (e2) { /* noop */ } }
      report.push({
        engine: ename, auth, path, nav,
        api_status: Object.entries(api.reduce((a, r) => {
          const k = r.path + " " + r.status; a[k] = (a[k] || 0) + 1;
          return a; }, {})).slice(0, 40),
        has_sign_in_prompt: /sign[- ]?in/i.test(all),
        has_disconnected: /DISCONNECTED|FEED UNAVAILABLE|RECONNECTING/.test(all),
        has_unavailable: /UNAVAILABLE/.test(all),
        cash_text: grab(/\$\s?500,000(\.00)?/),
        stamps: {
          server_read: grab(/(last )?server read[^\n]{0,60}/i),
          heartbeat: grab(/(agent )?heartbeat[^\n]{0,60}/i),
          ledger: grab(/ledger (activity|transaction|entry)[^\n]{0,60}/i) },
        funded_label: /FUNDED SYSTEM \(INACTIVE\)/i.test(all),
        completed_game_label: /COMPLETED[_ ]GAME/i.test(all),
        overflow, console_errors: consoleErr.slice(0, 5),
        screenshot: shot });
      await Promise.race([page.close(),
                          new Promise((r) => setTimeout(r, 5000))]);
    }
    await ctx.close();
  }
  await browser.close();
}
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
for (const r of report) {
  console.log(JSON.stringify({ e: r.engine, a: r.auth, p: r.path, nav: r.nav,
    signin: r.has_sign_in_prompt, disc: r.has_disconnected,
    cash: r.cash_text, stamps: r.stamps, funded_label: r.funded_label,
    cg: r.completed_game_label, overflow: r.overflow,
    api: r.api_status.slice(0, 12), console: r.console_errors.length }));
}
