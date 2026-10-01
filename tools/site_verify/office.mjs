// THE MANAGEMENT OFFICE ON THE PRODUCTION SITE, AGAINST THE PERSISTED RECORDS.
// Signed in (admin token attached ONLY to this site's /api/command/* calls).
// Engines: Chromium desktop 1440x1000, WebKit iPhone 13 emulation (the Safari
// engine, NOT a physical iPhone).
//   RECORDS  the operations / account / experiment responses the PAGE ITSELF
//            received are captured and compared with what it shows: Xavier's
//            open positions (group ids, open qty), the standing orders linked
//            to each (order ids, remaining qty, limits), the cash and its
//            ledger entry, Audrey's recommendation ids, Derek's policy
//            versions. A failed operations read must keep the last records
//            (never a fresh zero).
//   CHAT     one question per agent (concise), one full-analysis question to
//            Xavier, timed: submit -> first visible answer; submit -> first
//            `playing` event of the speech audio (actual audible start in the
//            browser), with the playback mode the page reports (streaming vs
//            buffered). Then: a second question while speech is loading or
//            playing (send stays enabled, the earlier speech is aborted and
//            never plays afterwards), Stop, a SIMULATED provider error (one
//            /speech replaced by a 503 -- text stays), and a reload that must
//            resume the conversation.
// Writes conversation records only (the persona chat's own tables). No order,
// policy or ledger write. Evidence: office_report.json + screenshots in OUT.
import { webkit, chromium, devices } from "playwright";
import fs from "node:fs";

const HOST = process.env.HOST || "https://command.bettortoken.com";
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
fs.mkdirSync(OUT, { recursive: true });
const short = (s, n = 400) => String(s == null ? "" : s).replace(/\s+/g, " ").slice(0, n);
const report = { host: HOST, at: new Date().toISOString(), engines: {} };

async function signedIn(ctx) {
  if (!TOKEN) return;
  await ctx.route(HOST + "/api/command/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-admin-token": TOKEN } }));
}
function frameOf(page, agent) {
  return page.frames().find((f) => new RegExp(`/api/command/agents/${agent}/page`).test(f.url()));
}
async function waitOffice(page, agent, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const f = frameOf(page, agent);
    if (f) {
      try {
        await f.waitForSelector("#office-content", { timeout: 5000 });
        await f.waitForFunction(() => /Connected to recorded workspace|Read unavailable/.test(
          (document.getElementById("office-status") || {}).textContent || ""), null, { timeout: 30000 });
        return f;
      } catch (_) {}
    }
    await page.waitForTimeout(500);
  }
  return null;
}
async function shot(page, name, f) {
  try { await page.screenshot({ path: `${OUT}/office_${name}.png`, fullPage: false, timeout: 15000 }); } catch (_) {}
  if (f) { try { await f.locator(".office-layout").screenshot({ path: `${OUT}/office_${name}_board.png`, timeout: 20000 }); } catch (_) {} }
}
const INIT = () => {
  // Speech instrumentation: every Audio element the page creates reports its
  // playing / pause / ended events with a performance.now() stamp.
  window.__sp = [];
  const O = window.Audio;
  window.Audio = function (...a) {
    const el = new O(...a), id = window.__sp.length;
    for (const ev of ["playing", "pause", "ended", "error", "abort", "emptied"]) {
      el.addEventListener(ev, () => window.__sp.push({ id, ev, t: performance.now() }));
    }
    window.__sp.push({ id, ev: "created", t: performance.now() });
    return el;
  };
  window.Audio.prototype = O.prototype;
};

const ENGINES = [
  ["chromium-desktop-1440", chromium, { viewport: { width: 1440, height: 1000 } },
   ["--autoplay-policy=no-user-gesture-required"]],
  ["webkit-iphone13", webkit, devices["iPhone 13"], []],
];

for (const [ename, engine, dev, args] of ENGINES) {
  const E = report.engines[ename] = { agents: {} };
  const browser = await engine.launch({ args });
  for (const agent of ["xavier", "derek", "audrey"]) {
    const R = E.agents[agent] = { url: `${HOST}/${agent}`, checks: {} };
    const ctx = await browser.newContext({ ...dev });
    await signedIn(ctx);
    await ctx.addInitScript(INIT);
    const page = await ctx.newPage();
    const seen = { ops: [], account: [], chats: [], speech: [], errs: [] };
    page.on("console", (m) => { if (m.type() === "error") seen.errs.push(short(m.text(), 160)); });
    page.on("pageerror", (e) => seen.errs.push("pageerror: " + short(e.message, 160)));
    page.on("response", async (r) => {
      const u = r.url();
      try {
        if (u.includes("/api/command/paper/operations?agent=" + agent)) {
          seen.ops.push({ at: Date.now(), status: r.status(), json: r.status() === 200 ? await r.json() : null });
        } else if (u.includes("/api/command/paper/account")) {
          seen.account.push({ at: Date.now(), status: r.status(), json: r.status() === 200 ? await r.json() : null });
        } else if (u.endsWith(`/agents/${agent}/persona/chat`)) {
          const j = await r.json();
          seen.chats.push({ at: Date.now(), status: r.status(), message_id: j.message_id,
            conversation_id: j.conversation_id, depth: (j.context_supplied || {}).depth || j.depth || null,
            prior_turns: (j.context_supplied || {}).prior_turns, persona: j.persona_version || j.persona || null,
            chars: (j.answer || "").length });
        } else if (u.endsWith(`/agents/${agent}/speech`)) {
          seen.speech.push({ at: Date.now(), status: r.status(), type: r.headers()["content-type"],
            cached: r.headers()["x-speech-cache"] || null, voice: r.headers()["x-speech-voice-id"] ? "configured" : null });
        }
      } catch (_) {}
    });
    try {
      await page.goto(`${HOST}/${agent}`, { waitUntil: "domcontentloaded", timeout: 60000 });
      let f = await waitOffice(page, agent);
      if (!f) throw new Error("office workboard not rendered");
      await page.waitForTimeout(2500);
      const ops = (seen.ops.filter((x) => x.json).slice(-1)[0] || {}).json || {};
      const okd = (s) => (s && (s.status === "OK" || s.status === "EMPTY")) ? s.data : null;
      const shown = await f.evaluate(() => ({
        positions: [...document.querySelectorAll("[data-office-position]")].map((a) => ({
          group_id: a.dataset.officePosition, text: a.textContent })),
        decisions: [...document.querySelectorAll("[data-office-decision]")].map((a) => ({
          id: a.dataset.officeDecision, text: a.textContent })),
        asks: [...document.querySelectorAll("[data-office-question]")].map((b) => b.dataset.officeQuestion),
        status: (document.getElementById("office-status") || {}).textContent || "",
        board: (document.querySelector(".office-board") || {}).textContent || "",
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        talkVisible: !!document.getElementById("talk-in"),
        jump: (() => { const j = document.querySelector(".office-talk-jump"); return j ? getComputedStyle(j).display : null; })(),
        strip: (document.body.innerText.match(/CASH\s*\$[\d,]+\.\d\d/) || [""])[0],
        ledgerEntry: (document.body.innerText.match(/ledger entry #(\d+)/) || [])[1] || null,
      }));
      R.checks.page = { status: short(shown.status, 200), overflow: shown.overflow, talk: shown.talkVisible,
        mobile_jump_display: shown.jump, js_errors: seen.errs.slice(0, 6) };
      // ACCOUNT: the strip's cash vs the account read the page received
      const acc = (seen.account.filter((x) => x.json).slice(-1)[0] || {}).json || {};
      const accd = ((acc.account || {}).data) || {};
      const led = ((acc.ledger || {}).data);
      const ledRows = Array.isArray(led) ? led : (led && (led.entries || led.rows)) || [];
      R.checks.account = { strip: shown.strip, ledger_entry_shown: shown.ledgerEntry,
        account_read: { cash_usd: accd.cash_usd, reserved_usd: accd.reserved_usd,
          open_positions: Array.isArray(accd.open_positions) ? accd.open_positions.length : accd.open_positions,
          ledger_consistent: accd.ledger_consistent,
          ledger_head_seq: ledRows.length ? Math.max(...ledRows.map((x) => Number(x.seq) || 0)) : null,
          keys: Object.keys(accd).slice(0, 40) },
        match: !!(accd.cash_usd != null && shown.strip.replace(/[^\d.]/g, "") ===
          Number(accd.cash_usd).toFixed(2)) };
      if (agent === "xavier") {
        const owned = (okd(ops.owned_positions) || []).filter((p) => p.remaining && p.remaining.open_qty > 0);
        const orders = (okd(ops.standing_orders) || []).filter((o) => o.open === true);
        R.checks.positions = owned.map((p) => {
          const card = shown.positions.find((x) => x.group_id === p.group_id);
          const linked = orders.filter((o) => o.group_id === p.group_id);
          return { group_id: p.group_id, open_qty: p.remaining.open_qty,
            cost_basis_usd: p.remaining.cost_basis_usd, marked_value_usd: p.remaining.marked_value_usd,
            review_id: (p.recommendation || {}).review_id, recommendation: (p.recommendation || {}).recommendation,
            shown: !!card, qty_shown: !!card && card.text.includes(String(p.remaining.open_qty) + " contracts"),
            orders: linked.map((o) => ({ order_id: o.order_id, remaining: o.qty - o.filled_qty, limit: o.limit_price,
              state: o.state, in_card: !!card && card.text.includes(o.order_id) })),
            ask_carries_group: shown.asks.some((q) => q.includes(p.group_id)) };
        });
        R.checks.positions_ok = owned.length > 0 && R.checks.positions.every((p) => p.shown && p.qty_shown &&
          p.ask_carries_group && p.orders.every((o) => o.in_card));
      }
      if (agent === "derek") {
        const vers = (ops.strategies || []).map((s) => ({ strategy: s.strategy, serving: s.version }));
        R.checks.versions = { serving: vers, cards: shown.decisions.length,
          historical_label_shown: shown.board.includes("Historical policy version"),
          version_label_shown: shown.board.includes("Recorded policy:") };
      }
      if (agent === "audrey") {
        const recs = okd(ops.operational_audit) || [];
        R.checks.recommendations = { ids: recs.map((r) => r.recommendation_id),
          shown: recs.map((r) => shown.asks.some((q) => q.includes(r.recommendation_id))),
          automated_labelled: /automated/i.test(shown.board) };
      }
      await shot(page, `${ename}_${agent}_board`, f);
      // FAILED READ: the next operations read fails; the records stay
      await page.route(`${HOST}/api/command/paper/operations*`, (route) => route.fulfill({ status: 503,
        contentType: "application/json", body: JSON.stringify({ detail: "SIMULATED read failure (test)" }) }));
      await f.evaluate(() => { const b = document.querySelector("[data-act='refresh'],#refresh-btn,.cc-refresh"); if (b) b.click(); });
      await page.waitForTimeout(17000);
      const after = await f.evaluate(() => ({ status: (document.getElementById("office-status") || {}).textContent || "",
        positions: document.querySelectorAll("[data-office-position]").length,
        decisions: document.querySelectorAll("[data-office-decision]").length }));
      R.checks.failed_read = { status: short(after.status, 200), positions_after: after.positions,
        decisions_after: after.decisions, kept: /Last successful records retained/.test(after.status) };
      await page.unroute(`${HOST}/api/command/paper/operations*`);
      await shot(page, `${ename}_${agent}_failed_read`, f);

      // ── CHAT AND VOICE ──────────────────────────────────────────────
      const sel = await f.$("#office-answer-style");
      if (sel) await f.selectOption("#office-answer-style", "brief");
      const Q = { xavier: "What are you doing with the San Diego position right now?",
        derek: "Why did the closest recent opportunity not qualify?",
        audrey: "Does the paper ledger reconcile right now?" }[agent];
      const ask = async (q) => {
        const nA = await f.locator(".talk-msg.a").count();
        const t = await f.evaluate(() => performance.now());
        await f.fill("#talk-in", q);
        await f.click("#talk-send");
        await f.waitForFunction((n) => document.querySelectorAll(".talk-msg.a").length > n, nA, { timeout: 150000 });
        const tA = await f.evaluate(() => performance.now());
        return { t, tA };
      };
      const sp0 = await f.evaluate(() => window.__sp.length);
      const a1 = await ask(Q);
      // first audible speech: the next `playing` event after the question
      let tPlay = null, mode = null;
      for (let i = 0; i < 90 && tPlay === null; i++) {
        await page.waitForTimeout(500);
        const s = await f.evaluate((n) => window.__sp.slice(n), sp0);
        const p = s.find((x) => x.ev === "playing");
        if (p) tPlay = p.t;
        const st = await f.locator("#talk-state").innerText().catch(() => "");
        if (/streaming audio/.test(st)) mode = "streaming";
        else if (/buffered playback/.test(st)) mode = mode || "buffered";
      }
      // AUTOPLAY BLOCKED (e.g. WebKit after an async fetch): measure the
      // voice from an explicit Play press instead, and say so.
      let replay = null;
      if (tPlay === null) {
        const st0 = await f.locator("#talk-state").innerText().catch(() => "");
        const n0 = await f.evaluate(() => window.__sp.length);
        const tc = await f.evaluate(() => performance.now());
        await f.locator(".talk-replay").last().click().catch(() => {});
        let tp = null, m2 = null;
        for (let i = 0; i < 60 && tp === null; i++) {
          await page.waitForTimeout(500);
          const s2 = await f.evaluate((n) => window.__sp.slice(n), n0);
          const p2 = s2.find((x) => x.ev === "playing"); if (p2) tp = p2.t;
          const st = await f.locator("#talk-state").innerText().catch(() => "");
          if (/streaming audio/.test(st)) m2 = "streaming"; else if (/buffered playback/.test(st)) m2 = m2 || "buffered";
        }
        replay = { autoplay_state_before: short(st0, 200), play_press_to_audible_ms: tp === null ? null : Math.round(tp - tc),
          playback_mode_reported: m2, speech_response: seen.speech.slice(-1)[0] || null };
      }
      R.checks.timing = { question: Q, style: "brief", replay_after_autoplay_block: replay,
        first_visible_answer_ms: Math.round(a1.tA - a1.t),
        first_audible_speech_ms: tPlay === null ? null : Math.round(tPlay - a1.t),
        speech_after_text_ms: tPlay === null ? null : Math.round(tPlay - a1.tA),
        playback_mode_reported: mode, chat: seen.chats.slice(-1)[0] || null,
        speech_response: seen.speech.slice(-1)[0] || null };
      await shot(page, `${ename}_${agent}_answer`, f);
      if (agent === "xavier") {
        // INTERRUPT: a second (full-analysis) question while speech loads / plays
        if (sel) await f.selectOption("#office-answer-style", "full");
        const spBefore = await f.evaluate(() => window.__sp.length);
        const sendEnabled = await f.evaluate(() => !document.getElementById("talk-send").disabled);
        const a2 = await ask("Full analysis please: walk me through every open position, its standing order and what would make you exit.");
        await page.waitForTimeout(8000);
        const ev = await f.evaluate((n) => window.__sp.slice(n), spBefore);
        const firstId = (await f.evaluate((n) => window.__sp.slice(n).filter((x) => x.ev === "created").map((x) => x.id), sp0))[0];
        R.checks.interrupt = { send_enabled_during_speech: sendEnabled,
          full_answer_chars: (seen.chats.slice(-1)[0] || {}).chars, brief_answer_chars: (seen.chats.slice(-2)[0] || {}).chars,
          depth_full: (seen.chats.slice(-1)[0] || {}).depth, depth_brief: (seen.chats.slice(-2)[0] || {}).depth,
          earlier_audio_played_after_new_question: ev.some((x) => x.id === firstId && x.ev === "playing"),
          events_after: ev.map((x) => x.id + ":" + x.ev).slice(0, 20),
          first_visible_answer_ms_full: Math.round(a2.tA - a2.t),
          same_conversation: (seen.chats.slice(-1)[0] || {}).conversation_id === (seen.chats.slice(-2)[0] || {}).conversation_id,
          prior_turns: (seen.chats.slice(-1)[0] || {}).prior_turns };
        // STOP
        await f.click("#talk-stop").catch(() => {});
        await page.waitForTimeout(1200);
        const stopped = await f.evaluate(() => [...window.__sp].reverse().slice(0, 4).map((x) => x.id + ":" + x.ev));
        R.checks.stop = { last_events: stopped, stop_disabled_after: await f.evaluate(() => document.getElementById("talk-stop").disabled) };
        // PROVIDER ERROR (SIMULATED): one /speech -> 503; the text must remain
        await page.route(`${HOST}/api/command/agents/${agent}/speech`, (route) => route.fulfill({ status: 503,
          contentType: "application/json", body: JSON.stringify({ status: "VOICE_UNAVAILABLE",
            reason: "VOICE_PROVIDER_FAILED", simulated_by_the_test: true }) }), { times: 1 });
        const nMsgs = await f.locator(".talk-msg.a").count();
        await f.locator(".talk-replay").last().click().catch(() => {});
        await page.waitForTimeout(2500);
        R.checks.provider_error = { state: short(await f.locator("#talk-state").innerText().catch(() => ""), 200),
          answers_still_shown: await f.locator(".talk-msg.a").count(), before: nMsgs };
      }
      // RELOAD: the conversation resumes from the server
      const before = await f.locator(".talk-msg").count();
      await page.reload({ waitUntil: "domcontentloaded" });
      f = await waitOffice(page, agent);
      await f.waitForFunction((n) => document.querySelectorAll(".talk-msg").length >= n, before, { timeout: 30000 }).catch(() => {});
      R.checks.resume = { before, after: await f.locator(".talk-msg").count(),
        state: short(await f.locator("#talk-state").innerText().catch(() => ""), 160) };
      await shot(page, `${ename}_${agent}_resumed`, f);
    } catch (e) {
      R.error = short(String(e), 300);
      await shot(page, `${ename}_${agent}_error`);
    }
    R.js_errors = seen.errs.slice(0, 8);
    await ctx.close();
  }
  await browser.close();
}
fs.writeFileSync(`${OUT}/office_report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 1).slice(0, 20000));
