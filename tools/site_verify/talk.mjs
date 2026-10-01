// "TALK TO <AGENT>" ACCEPTANCE ON THE PRODUCTION MANAGEMENT SITE.
// Drives the real signed-in pages command.bettortoken.com/derek, /xavier and
// /audrey (the agent page inside the shell's same-origin frame):
//   1 type a question and receive a grounded answer (citations + "Memory
//     supplied" shown on the page),
//   2 ask a follow-up that needs the earlier turn (the chat response must
//     report prior_turns > 0 sent to the model),
//   3 reload the page and see the conversation resumed,
//   4 SPEAK a question (Chromium's fake microphone plays a WAV of a spoken
//     question; the page records with MediaRecorder, the server transcribes,
//     the page sends the text) and hear the reply (the /speech audio in that
//     agent's voice, played by the page's <audio>),
//   5 permission denied (no microphone permission) and a provider error
//     (one /speech response replaced by a 503 provider error -- SIMULATED,
//     labelled as such) render their states.
// Engines: Chromium desktop 1280x800, Chromium phone 390x844 (both with the
// fake microphone), WebKit iPhone 13 emulation (Safari engine, NOT a
// physical iPhone; no fake microphone, so its mic check is permission
// denied). Signed in = the admin token attached ONLY to this site's own
// /api/command/* requests. Writes screenshots and talk_report.json to OUT.
import { webkit, chromium, devices } from "playwright";
import fs from "node:fs";

const HOST = process.env.HOST || "https://command.bettortoken.com";
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
const WAV = process.env.QUESTION_WAV || "";
fs.mkdirSync(OUT, { recursive: true });

const QUESTIONS = {
  derek: ["What is the active entry rule, exactly?",
          "Given that rule, why did nothing qualify in the last decisions?"],
  xavier: ["What are you managing right now?",
           "And what would you do first if Derek handed you a position?"],
  audrey: ["Does the paper ledger reconcile right now?",
           "What did you just check to say that?"],
};

const ENGINES = [
  ["chromium-desktop-1280", chromium, { viewport: { width: 1280, height: 800 } }, true],
  ["chromium-phone-390", chromium, { viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2, isMobile: true, hasTouch: true }, true],
  ["webkit-iphone13", webkit, devices["iPhone 13"], false],
];
const report = [];
const short = (s, n = 400) => String(s == null ? "" : s).replace(/\s+/g, " ").slice(0, n);

async function signedIn(ctx) {
  if (!TOKEN) return;
  await ctx.route(HOST + "/api/command/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-admin-token": TOKEN } }));
}

function frameOf(page) {
  return page.frames().find((f) => /\/api\/command\/agents\/(derek|xavier|audrey)\/page/.test(f.url()));
}

async function waitFrame(page, ms = 45000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const f = frameOf(page);
    if (f) { try { await f.waitForSelector("#talk", { timeout: 5000 }); return f; } catch (_) {} }
    await page.waitForTimeout(500);
  }
  return null;
}

async function shot(page, name) {
  const p = `${OUT}/talk_${name}.png`;
  try { await page.screenshot({ path: p, fullPage: false, timeout: 15000 }); } catch (_) {}
  // the panel itself, in the frame
  const f = frameOf(page);
  if (f) { try { await f.locator("#talk").screenshot({ path: `${OUT}/talk_${name}_panel.png`, timeout: 15000 }); } catch (_) {} }
  return p;
}

async function askTyped(page, f, q, chats) {
  const before = await f.locator(".talk-msg.a").count();
  const nChats = chats.length;
  await f.fill("#talk-in", q);
  await f.click("#talk-send");
  await f.waitForFunction((n) => document.querySelectorAll(".talk-msg.a").length > n, before, { timeout: 150000 });
  const t0 = Date.now();
  while (chats.length <= nChats && Date.now() - t0 < 10000) await page.waitForTimeout(200);
  const last = f.locator(".talk-msg.a").last();
  return { text: short(await last.innerText(), 1500), chat: chats[chats.length - 1] || null };
}

for (const [ename, engine, dev, fakeMic] of ENGINES) {
  const args = fakeMic && WAV ? ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${WAV}%noloop`, "--autoplay-policy=no-user-gesture-required"] : [];
  const browser = await engine.launch({ args });
  for (const agent of ["derek", "xavier", "audrey"]) {
    const row = { engine: ename, agent, url: `${HOST}/${agent}`, steps: {} };
    const ctx = await browser.newContext({ ...dev, ...(fakeMic && WAV ? { permissions: ["microphone"] } : {}) });
    await signedIn(ctx);
    const chats = [], speech = [], trans = [], errs = [];
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errs.push(short(m.text(), 160)); });
    page.on("response", async (r) => {
      const u = r.url();
      try {
        if (u.endsWith(`/agents/${agent}/persona/chat`)) {
          const j = await r.json();
          chats.push({ status: r.status(), message_id: j.message_id, conversation_id: j.conversation_id,
            provider_mode: (j.provider || {}).mode, model: (j.provider || {}).answered_model,
            context_supplied: j.context_supplied, citations: (j.citations || []).length,
            facts_cited: (j.facts || []).map((x) => x.source + ":" + x.record_id).slice(0, 8) });
        } else if (u.endsWith(`/agents/${agent}/speech`)) {
          speech.push({ status: r.status(), type: r.headers()["content-type"],
            voice_id: r.headers()["x-speech-voice-id"] || null,
            bytes: Number(r.headers()["content-length"] || 0) || null });
        } else if (u.endsWith(`/agents/${agent}/transcribe`)) {
          let j = null; try { j = await r.json(); } catch (_) {}
          trans.push({ status: r.status(), text: j && j.text, reason: j && j.reason,
            diag: j && j.provider_diagnostic ? { http: j.provider_diagnostic.http_status,
              err: j.provider_diagnostic.provider_error_status,
              perm: j.provider_diagnostic.required_permission } : null });
        }
      } catch (_) {}
    });
    try {
      await page.goto(`${HOST}/${agent}`, { waitUntil: "domcontentloaded", timeout: 45000 });
      let f = await waitFrame(page);
      if (!f) throw new Error("agent frame with #talk not found");
      // a clean conversation for this run
      await f.click("#talk-new");
      const vis = await f.locator("#talk").isVisible();
      const box = await f.locator("#talk").boundingBox();
      const funded = await f.evaluate(() => { const t = document.getElementById("talk"), d = document.querySelector("details.cc-funded");
        return { inside_funded: !!(d && d.contains(t)), above_funded: !!(d && (t.compareDocumentPosition(d) & Node.DOCUMENT_POSITION_FOLLOWING)) }; });
      row.steps.panel = { visible: vis, width: box && Math.round(box.width),
        frame_width: await f.evaluate(() => document.documentElement.clientWidth),
        overflow: await f.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1),
        ...funded };
      await shot(page, `${ename}_${agent}_0_panel`);
      // mute while typing checks; voice is exercised in the spoken step
      if ((await f.getAttribute("#talk-mute", "aria-pressed")) !== "true") await f.click("#talk-mute");
      // 1 typed question
      const a1 = await askTyped(page, f, QUESTIONS[agent][0], chats);
      row.steps.typed = { question: QUESTIONS[agent][0], answer: a1.text, chat: a1.chat,
        memory_line_shown: /Memory supplied/.test(a1.text) };
      await shot(page, `${ename}_${agent}_1_typed`);
      // 2 follow-up needing the earlier turn
      const a2 = await askTyped(page, f, QUESTIONS[agent][1], chats);
      row.steps.follow_up = { question: QUESTIONS[agent][1], answer: a2.text, chat: a2.chat,
        prior_turns_sent_to_model: a2.chat && a2.chat.context_supplied ? a2.chat.context_supplied.prior_turns : null,
        same_conversation: !!(a1.chat && a2.chat && a1.chat.conversation_id === a2.chat.conversation_id) };
      await shot(page, `${ename}_${agent}_2_followup`);
      // 3 refresh and resume
      await page.reload({ waitUntil: "domcontentloaded" });
      f = await waitFrame(page);
      await f.waitForFunction(() => document.querySelectorAll(".talk-msg").length >= 4, null, { timeout: 30000 }).catch(() => {});
      row.steps.resume = { messages_after_reload: await f.locator(".talk-msg").count(),
        state: short(await f.locator("#talk-state").innerText().catch(() => "")) };
      await shot(page, `${ename}_${agent}_3_resumed`);
      // 4 spoken question (fake microphone) and the spoken reply
      if ((await f.getAttribute("#talk-mute", "aria-pressed")) === "true") await f.click("#talk-mute");
      if (fakeMic && WAV) {
        const nS = speech.length;
        await f.click("#talk-mic");
        await page.waitForTimeout(5500);
        await f.click("#talk-mic");
        const t0 = Date.now();
        while (speech.length <= nS && Date.now() - t0 < 150000) await page.waitForTimeout(500);
        await page.waitForTimeout(2500);
        const played = await f.evaluate(() => { const a = [...document.querySelectorAll("audio")]; return a.length; }).catch(() => null);
        row.steps.spoken = { transcription: trans[trans.length - 1] || null,
          reply_chat: chats[chats.length - 1] || null, speech: speech[speech.length - 1] || null,
          state: short(await f.locator("#talk-state").innerText().catch(() => "")),
          audio_elements_in_dom: played };
        await shot(page, `${ename}_${agent}_4_spoken`);
      } else {
        // WebKit: the voice of a typed reply, replayed with the page's Play button
        const nS = speech.length;
        const play = f.locator(".talk-replay").last();
        if (await play.count()) { await play.click(); }
        const t0 = Date.now();
        while (speech.length <= nS && Date.now() - t0 < 60000) await page.waitForTimeout(500);
        await page.waitForTimeout(1500);
        row.steps.spoken = { mode: "replay of the typed answer (no fake microphone in WebKit)",
          speech: speech[speech.length - 1] || null,
          state: short(await f.locator("#talk-state").innerText().catch(() => "")) };
        await shot(page, `${ename}_${agent}_4_voice`);
      }
      // 5a provider error (SIMULATED: one /speech response replaced by a 503 provider error)
      await page.route(`${HOST}/api/command/agents/${agent}/speech`, (route) => route.fulfill({
        status: 503, contentType: "application/json",
        body: JSON.stringify({ status: "VOICE_UNAVAILABLE", reason: "VOICE_PROVIDER_FAILED",
          provider_diagnostic: { http_status: 429, provider_error_status: "quota_exceeded",
            required_permission: "text_to_speech" }, simulated_by_the_test: true }) }), { times: 1 });
      const play = f.locator(".talk-replay").last();
      if (await play.count()) await play.click();
      await page.waitForTimeout(2500);
      row.steps.provider_error_simulated = { state: short(await f.locator("#talk-state").innerText().catch(() => "")) };
      await shot(page, `${ename}_${agent}_5_provider_error`);
    } catch (e) {
      row.error = short(String(e), 300);
      await shot(page, `${ename}_${agent}_error`);
    }
    row.console_errors = errs.slice(0, 5);
    await ctx.close();
    // 5b permission denied: a context without the microphone permission
    try {
      const dctx = await browser.newContext({ ...dev });
      await signedIn(dctx);
      const dp = await dctx.newPage();
      await dp.goto(`${HOST}/${agent}`, { waitUntil: "domcontentloaded", timeout: 45000 });
      const df = await waitFrame(dp);
      if (df) {
        await df.click("#talk-mic").catch(() => {});
        await dp.waitForTimeout(4000);
        row.steps.permission_denied = { state: short(await df.locator("#talk-state").innerText().catch(() => "")),
          mic_disabled: await df.locator("#talk-mic").isDisabled().catch(() => null) };
        await shot(dp, `${ename}_${agent}_6_permission_denied`);
      }
      await dctx.close();
    } catch (e) { row.steps.permission_denied = { error: short(String(e), 200) }; }
    report.push(row);
    console.log(JSON.stringify({ e: ename, a: agent, error: row.error || null,
      panel: row.steps.panel, typed_mode: row.steps.typed && row.steps.typed.chat && row.steps.typed.chat.provider_mode,
      follow_prior: row.steps.follow_up && row.steps.follow_up.prior_turns_sent_to_model,
      resumed: row.steps.resume && row.steps.resume.messages_after_reload,
      spoken: row.steps.spoken && { t: row.steps.spoken.transcription, s: row.steps.spoken.speech, st: row.steps.spoken.state },
      provider_err: row.steps.provider_error_simulated, denied: row.steps.permission_denied }));
  }
  await browser.close();
}
// ── THE MANAGEMENT OFFICE (homepage) DRAWER AND THE DEMO PAGES ─────────
{
  const browser = await chromium.launch();
  for (const [vname, vp] of [["desktop-1280", { viewport: { width: 1280, height: 800 } }],
                             ["phone-390", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }]]) {
    const ctx = await browser.newContext(vp);
    await signedIn(ctx);
    const page = await ctx.newPage(); const errs = []; const chats = [];
    page.on("pageerror", (e) => errs.push(short(String(e), 160)));
    page.on("response", async (r) => { if (/\/persona\/chat$/.test(r.url())) { try { const j = await r.json();
      chats.push({ status: r.status(), message_id: j.message_id, provider_mode: (j.provider || {}).mode,
        prior_turns: (j.context_supplied || {}).prior_turns }); } catch (_) {} } });
    const row = { engine: "chromium-" + vname, page: "office", url: HOST + "/" };
    try {
      await page.goto(HOST + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
      await page.waitForSelector('[data-office-talk="derek"]', { timeout: 30000 });
      await page.click('[data-office-talk="derek"]');
      await page.waitForSelector("#office-message", { timeout: 15000 });
      await page.fill("#office-message", "What is the active entry rule?");
      await page.click(".office-chat-form button[type=submit]");
      await page.waitForFunction(() => document.querySelectorAll(".office-message.assistant").length >= 1, null, { timeout: 150000 });
      row.office_answer = short(await page.locator(".office-message.assistant").last().innerText(), 800);
      row.office_chat = chats[chats.length - 1] || null;
      await page.screenshot({ path: `${OUT}/office_${vname}_chat.png` });
    } catch (e) { row.error = short(String(e), 300); await page.screenshot({ path: `${OUT}/office_${vname}_error.png` }).catch(() => {}); }
    row.page_errors = errs.slice(0, 5);
    report.push(row);
    console.log(JSON.stringify({ office: vname, error: row.error || null, chat: row.office_chat, answer: (row.office_answer || "").slice(0, 200) }));
    for (const path of ["/team-demo/index.html", "/team-demo/video.html"]) {
      const p2 = await ctx.newPage(); const e2 = [];
      p2.on("pageerror", (e) => e2.push(short(String(e), 160)));
      const resp = await p2.goto(HOST + path, { waitUntil: "load", timeout: 45000 }).catch(() => null);
      await p2.waitForTimeout(6000);
      const ov = await p2.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth).catch(() => null);
      await p2.screenshot({ path: `${OUT}/demo_${vname}_${path.split("/").pop().replace(".html", "")}.png` }).catch(() => {});
      report.push({ engine: "chromium-" + vname, page: path, nav: resp && resp.status(), overflow_px: ov, page_errors: e2.slice(0, 5) });
      console.log(JSON.stringify({ demo: path, vp: vname, nav: resp && resp.status(), overflow_px: ov, errors: e2.length }));
      await p2.close();
    }
    await ctx.close();
  }
  await browser.close();
}
fs.writeFileSync(`${OUT}/talk_report.json`, JSON.stringify(report, null, 1));
