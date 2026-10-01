// THE PAPER EXPERIMENT, READ BACK ON THE PRODUCTION SITE. READ-ONLY.
// 1 reads GET /api/command/paper/experiment (signed in) -- the persisted
//   records: account cash/reserved, open positions, recent fills, standing
//   orders, the exploration budget;
// 2 renders the homepage (command.bettortoken.com/) and each agent page
//   (/derek, /xavier, /audrey: the agent page inside the shell's frame) in
//   Chromium 1280x800 and WebKit iPhone 13, signed in;
// 3 checks that the SAME records appear: the homepage's experiment section
//   says CONNECTED and shows every open position's market and every recent
//   fill's ledger number; the cash figure appears on the homepage and on
//   Audrey's page; each open position's market appears on Xavier's page.
// Writes experiment_report.json, the API read, each page's visible text and
// screenshots to OUT. Places no order and writes nothing.
import { webkit, chromium, devices } from "playwright";
import fs from "node:fs";

const HOST = process.env.HOST || "https://command.bettortoken.com";
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
fs.mkdirSync(OUT, { recursive: true });

const usd = (v) => (typeof v === "number" && isFinite(v))
  ? v.toLocaleString("en-US", { style: "currency", currency: "USD" }) : null;
const shortSlug = (s) => String(s || "").replace(/^(aec|atc)-/, "");

async function api(path) {
  const r = await fetch(HOST + path, { headers: { "x-admin-token": TOKEN,
    Accept: "application/json" } });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) {}
  return { status: r.status, json, text: json ? null : text.slice(0, 400) };
}

async function signedIn(ctx) {
  if (!TOKEN) return;
  await ctx.route(HOST + "/api/command/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-admin-token": TOKEN } }));
}

async function agentText(page, who) {
  const t0 = Date.now();
  while (Date.now() - t0 < 60000) {
    const f = page.frames().find((x) => new RegExp(`/api/command/agents/${who}/page`).test(x.url()));
    if (f) {
      try {
        await f.waitForLoadState("load", { timeout: 10000 });
        await page.waitForTimeout(8000);           // the page's own reads
        return await f.evaluate(() => document.body.innerText);
      } catch (_) {}
    }
    await page.waitForTimeout(500);
  }
  return "";
}

const report = { host: HOST, at: new Date().toISOString(), checks: [], engines: {} };
const check = (name, ok, detail) => report.checks.push({ name, ok: !!ok, detail });

const x = await api("/api/command/paper/experiment");
fs.writeFileSync(`${OUT}/experiment_api.json`, JSON.stringify(x, null, 2));
const j = x.json || {};
const sec = (k) => (j[k] && j[k].status === "OK") ? j[k].data : null;
const pos = sec("positions") || {};
const acct = pos.account || {};
const open = pos.open_positions || [];
const fills = sec("fills") || [];
const orders = sec("orders") || {};
report.api = {
  status: x.status, connected: j.state && j.state.connected,
  headline: j.state && j.state.headline, serving_build: j.serving_build,
  cash_usd: acct.cash_usd, reserved_usd: acct.reserved_usd,
  open_positions: open.map((p) => ({ group_id: p.group_id, strategy: p.strategy,
    label: p.label, market: p.market, open_qty: p.open_qty,
    cost_basis_usd: p.cost_basis_usd })),
  fills: fills.slice(0, 10).map((f) => ({ fill_id: f.fill_id, strategy: f.strategy,
    qty: f.qty, price: f.price, fee_usd: f.fee_usd,
    ledger_seq: f.ledger_seq })),
  entry_orders: (orders.entry_orders || []).length,
  exploration_limits: pos.exploration_limits,
};
check("api_connected", x.status === 200 && j.state && j.state.connected === true,
      { status: x.status });

const ENGINES = [
  ["chromium-desktop-1280", chromium, { viewport: { width: 1280, height: 800 } }],
  ["webkit-iphone13", webkit, devices["iPhone 13"]],
];
for (const [name, eng, opts] of ENGINES) {
  const e = report.engines[name] = {};
  const browser = await eng.launch();
  try {
    const ctx = await browser.newContext(opts);
    await signedIn(ctx);
    const page = await ctx.newPage();
    await page.goto(HOST + "/", { waitUntil: "domcontentloaded", timeout: 60000 });
    try {
      await page.waitForSelector(".xp-banner.xp-up, .xp-banner.xp-down, .xp-banner.xp-signin",
                                 { timeout: 60000 });
    } catch (_) {}
    await page.waitForTimeout(6000);
    const home = await page.evaluate(() => document.body.innerText);
    const xp = await page.evaluate(() => {
      const s = document.querySelector("section.xp");
      return s ? s.innerText : "";
    });
    fs.writeFileSync(`${OUT}/experiment_${name}_home.txt`, home);
    try { await page.screenshot({ path: `${OUT}/experiment_${name}_home.png`, fullPage: true, timeout: 20000 }); } catch (_) {}
    try { await page.locator("section.xp").screenshot({ path: `${OUT}/experiment_${name}_section.png`, timeout: 20000 }); } catch (_) {}
    e.home_connected = /CONNECTED/.test(xp) && !/DISCONNECTED/.test(xp.split("\n")[0] || "");
    e.home_section_present = xp.length > 0;
    const cash = usd(acct.cash_usd);
    e.cash_text = cash;
    e.home_has_cash = !!cash && home.includes(cash);
    e.home_positions = open.map((p) => ({ market: p.market,
      shown: xp.includes(shortSlug(p.market)) }));
    e.home_fills = fills.slice(0, 6).map((f) => ({ fill_id: f.fill_id,
      ledger_seq: f.ledger_seq, shown: xp.includes("ledger #" + f.ledger_seq) }));
    for (const who of ["derek", "xavier", "audrey"]) {
      await page.goto(HOST + "/" + who, { waitUntil: "domcontentloaded", timeout: 60000 });
      const t = await agentText(page, who);
      fs.writeFileSync(`${OUT}/experiment_${name}_${who}.txt`, t);
      try { await page.screenshot({ path: `${OUT}/experiment_${name}_${who}.png`, fullPage: true, timeout: 20000 }); } catch (_) {}
      e[who] = { chars: t.length, has_cash: !!cash && t.includes(cash),
        positions: open.map((p) => ({ market: p.market,
          shown: t.includes(p.market) || t.includes(shortSlug(p.market)) ||
                 t.includes(p.group_id) })),
        fills: fills.slice(0, 6).map((f) => ({ fill_id: f.fill_id,
          shown: [f.fill_id, f.ledger_seq && ("#" + f.ledger_seq)]
            .filter(Boolean).some((k) => t.includes(String(k))) })) };
    }
    check(`${name}:home_connected`, e.home_connected, xp.split("\n").slice(0, 3).join(" | "));
    check(`${name}:home_positions_match_api`, e.home_positions.every((p) => p.shown), e.home_positions);
    check(`${name}:home_fills_match_api`, e.home_fills.every((p) => p.shown), e.home_fills);
    check(`${name}:xavier_positions_match_api`, e.xavier.positions.every((p) => p.shown), e.xavier.positions);
    check(`${name}:audrey_or_home_cash_match_api`, e.home_has_cash || e.audrey.has_cash,
          { cash, home: e.home_has_cash, audrey: e.audrey.has_cash, xavier: e.xavier.has_cash, derek: e.derek.has_cash });
  } catch (err) {
    e.error = String(err && err.message || err).slice(0, 400);
    check(`${name}:ran`, false, e.error);
  } finally {
    await browser.close();
  }
}
report.ok = report.checks.every((c) => c.ok);
fs.writeFileSync(`${OUT}/experiment_report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ ok: report.ok, api: report.api,
  failed: report.checks.filter((c) => !c.ok) }, null, 2));
