// CINEMATIC FILM ACCEPTANCE ON THE PRODUCTION MANAGEMENT SITE.
// Opens the homepage, follows its "Meet your AI team" link to the team demo,
// presses "Watch cinematic demo" (prepares each chapter's answer through the
// agent's own persona chat and fetches that stored message's speech from
// /speak), presses it again and plays the WHOLE film (exportVideo:false --
// direct playback, no MediaRecorder, no pre-rendered video).
// Measured, not assumed:
//   * every /speak response: agent, status, content type, configured voice id
//     header, bytes -- so each chapter is the named agent's actual voice;
//   * the Web Audio graph, tapped without altering it: an analyser on the
//     score bus (music only), one on the voice buffer sources (speech only)
//     and one on the context output (what the speakers get), sampled every
//     100 ms with the chapter on screen -- music beneath speech means the
//     score analyser is non-silent WHILE a voice source is playing;
//   * the film canvas: frames captured during playback and a pixel-variance
//     check (the 3D office is drawn by the WebGL world into this canvas);
//   * console errors and page errors; the old pre-rendered video is never
//     requested (any request for a .mp4/.webm is recorded and fails the run).
// Signed in = the admin token attached ONLY to this site's /api/command/*
// requests. LOCAL=1 serves frontend/public/command and mocks the two routes.
import { chromium } from "playwright";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const LOCAL = process.env.LOCAL === "1";
const HOST = LOCAL ? "http://127.0.0.1:8777" : (process.env.HOST || "https://command.bettortoken.com");
const OUT = process.env.OUT || "site_verify_out";
const TOKEN = process.env.ADMIN_TOKEN || "";
const ROOT = process.env.ROOT || "";
fs.mkdirSync(OUT, { recursive: true });
const short = (s, n = 400) => String(s == null ? "" : s).replace(/\s+/g, " ").slice(0, n);

let server = null;
if (LOCAL) {
  const types = { ".js": "text/javascript", ".css": "text/css", ".glb": "model/gltf-binary",
    ".json": "application/json", ".jpg": "image/jpeg", ".html": "text/html" };
  server = http.createServer((req, res) => {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p === "/") p = "/index.html";
    try {
      const f = path.join(ROOT, p);
      res.setHeader("Content-Type", types[path.extname(f)] || "text/html");
      res.end(fs.readFileSync(f));
    } catch { res.writeHead(404).end(); }
  });
  await new Promise((r) => server.listen(8777, "127.0.0.1", r));
}

// Taps the audio graph without changing what is heard.
const INSTRUMENT = () => {
  window.__film = { taps: [], samples: [], voiceOn: 0, sources: 0 };
  const F = window.__film;
  const rms = (an, buf) => { an.getFloatTimeDomainData(buf); let s = 0; for (const v of buf) s += v * v; return Math.sqrt(s / buf.length); };
  const Real = window.AudioContext;
  class Tapped extends Real {
    constructor(...a) {
      super(...a);
      const mk = () => { const an = Real.prototype.createAnalyser.call(this); an.fftSize = 2048; return an; };
      this.__t = { out: mk(), score: mk(), voice: mk(), buf: new Float32Array(2048) };
      this.__t.out.connect(super.destination);
      F.taps.push(this.__t);
    }
    get destination() { return this.__t ? this.__t.out : super.destination; }
  }
  window.AudioContext = Tapped; window.webkitAudioContext = Tapped;
  const connect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = connect.call(this, dest, ...rest);
    const t = this.context && this.context.__t;
    if (t && dest !== t.score && dest !== t.voice && dest !== t.out) {
      // the score bus is the gain created at 0.24 that feeds the master gain
      if (this instanceof GainNode && Math.abs(this.gain.value - 0.24) < 1e-6 && !this.__scoreTapped) {
        this.__scoreTapped = true; connect.call(this, t.score); F.scoreBus = true;
      }
      if (this instanceof AudioBufferSourceNode && !this.__voiceTapped) {
        this.__voiceTapped = true; connect.call(this, t.voice);
      }
    }
    return r;
  };
  const start = AudioBufferSourceNode.prototype.start, stop = AudioBufferSourceNode.prototype.stop;
  AudioBufferSourceNode.prototype.start = function (...a) {
    F.voiceOn++; F.sources++; let done = false;
    const end = () => { if (!done) { done = true; F.voiceOn--; } };
    this.addEventListener("ended", end);
    this.__end = end; return start.apply(this, a);
  };
  AudioBufferSourceNode.prototype.stop = function (...a) { try { this.__end && this.__end(); } catch {} return stop.apply(this, a); };
  setInterval(() => {
    const t = F.taps[F.taps.length - 1];
    if (!t || !document.querySelector("canvas[style*='177.7vh']")) return;
    F.samples.push({ t: performance.now(), chapter: (document.querySelector("#chapter") || {}).textContent || "",
      voice_playing: F.voiceOn > 0, out: rms(t.out, t.buf), score: rms(t.score, t.buf), voice: rms(t.voice, t.buf) });
  }, 100);
};

const WAV = (() => { // 1 s of a 440 Hz tone, for the local mock only
  const n = 8000, b = Buffer.alloc(44 + n * 2);
  b.write("RIFF"); b.writeUInt32LE(36 + n * 2, 4); b.write("WAVEfmt ", 8); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(8000, 24); b.writeUInt32LE(16000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write("data", 36); b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) b.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / 8000)), 44 + i * 2);
  return b;
})();

const report = { host: HOST, local: LOCAL, started: new Date().toISOString() };
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM || undefined,
  args: ["--no-sandbox", "--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=swiftshader",
         "--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"],
});
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();
const consoleErrors = [], pageErrors = [], videoRequests = [], speak = [], chats = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(short(m.text(), 300)); });
page.on("pageerror", (e) => pageErrors.push(short(String(e), 300)));
page.on("request", (r) => { if (/\.(mp4|webm|mov)(\?|$)/i.test(r.url())) videoRequests.push(r.url().replace(/\?.*/, "")); });
page.on("response", async (r) => {
  const u = new URL(r.url()); const m = u.pathname.match(/\/api\/command\/agents\/(\w+)\/(speak|persona\/chat)$/);
  if (!m) return;
  if (m[2] === "speak") {
    const h = r.headers(); let bytes = Number(h["content-length"] || 0) || null;
    try { bytes = (await r.body()).length; } catch {}
    speak.push({ agent: m[1], status: r.status(), type: h["content-type"] || null,
      voice_id: h["x-speech-voice-id"] || null, voice_status: h["x-speech-voice-status"] || null, bytes });
  } else {
    let j = {}; try { j = await r.json(); } catch {}
    chats.push({ agent: m[1], status: r.status(), mode: j.provider && j.provider.mode || null,
      message_id: j.message_id || null, answer: short(j.answer, 160) });
  }
});
await page.addInitScript(INSTRUMENT);
if (LOCAL) {
  await ctx.route(HOST + "/api/command/**", (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p.endsWith("/persona/chat")) return route.fulfill({ json: { answer: "LOCAL MOCK " + p.split("/")[4], message_id: "m" + Math.random(), provider: { mode: "LLM" } } });
    if (p.endsWith("/speak")) return route.fulfill({ contentType: "audio/wav", headers: { "x-speech-voice-id": "mock-" + p.split("/")[4] }, body: WAV });
    return route.fulfill({ status: 404, json: {} });
  });
} else if (TOKEN) {
  await ctx.route(HOST + "/api/command/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-admin-token": TOKEN } }));
}

const shots = [];
const shot = async (name) => { const p = `${OUT}/film_${name}.png`; try { await page.screenshot({ path: p, timeout: 20000 }); shots.push(path.basename(p)); } catch {} };
try {
  // 1 · the homepage's existing demo link
  await page.goto(HOST + "/", { waitUntil: "domcontentloaded", timeout: 60000 });
  const link = page.locator('a[href*="team-demo/index.html"]').first();
  await link.waitFor({ timeout: 45000 });
  report.homepage_link = await link.getAttribute("href");
  await shot("1_homepage");
  await Promise.all([page.waitForURL(/team-demo\/index\.html/, { timeout: 60000 }), link.click()]);
  report.demo_url = page.url().replace(/\?.*/, "");
  // 2 · the served files are the cinematic release
  const served = {};
  for (const f of ["cinematic.js", "film-world.js", "record.js", "demo.js"]) {
    const r = await page.request.get(new URL(f, report.demo_url).href);
    const t = await r.text();
    served[f] = { status: r.status(), bytes: t.length,
      exportVideo_false: f === "demo.js" ? /runFilm\(false\)/.test(t) : f === "record.js" ? /exportVideo=true/.test(t) && /exportVideo\?/.test(t) : undefined };
  }
  report.served = served;
  await page.waitForFunction(() => window.BTDemo && Object.keys(window.BTDemo.avatars).length === 3, null, { timeout: 120000 })
    .catch(() => {});
  report.avatars_mounted = await page.evaluate(() => Object.keys((window.BTDemo || {}).avatars || {}));
  report.status_after_load = short(await page.locator("#status").textContent());
  await shot("2_demo_loaded");
  // 3 · first press prepares the actual answers + voices
  await page.click("#play");
  await page.waitForFunction(() => window.BTDemo.prepared.size === window.BTDemo.scenes.length || /Prepared \d+ \/ \d+\./.test(document.querySelector("#status").textContent),
    null, { timeout: 15 * 60000 });
  report.status_after_prepare = short(await page.locator("#status").textContent());
  report.prepared = await page.evaluate(() => window.BTDemo.prepared.size);
  report.scenes = await page.evaluate(() => window.BTDemo.scenes.map((s) => s.agent));
  if (report.prepared !== report.scenes.length) throw Error("voices not prepared: " + report.status_after_prepare);
  await shot("3_prepared");
  // 4 · second press plays the film directly
  await page.waitForFunction(() => !document.querySelector("#play").disabled, null, { timeout: 30000 });
  const t0 = Date.now();
  await page.click("#play");
  await page.getByRole("button", { name: "Close film", exact: true }).waitFor({ timeout: 60000 });
  report.close_button = true;
  const seenChapters = new Set(); let k = 0;
  while (Date.now() - t0 < 20 * 60000) {
    const st = short(await page.locator("#status").textContent());
    const m = st.match(/Playing cinematic film · (\d+) \/ (\d+)/);
    if (m && !seenChapters.has(m[1])) {
      seenChapters.add(m[1]);
      await page.waitForTimeout(m[1] === "1" ? 2500 : 4000);
      await shot(`4_film_ch${String(m[1]).padStart(2, "0")}`);
      const v = await page.evaluate(() => {
        const c = document.querySelector("canvas[style*='177.7vh']"); if (!c) return null;
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let s = 0, s2 = 0, n = 0;
        for (let i = 0; i < d.length; i += 64) { const y = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; s += y; s2 += y * y; n++; }
        const mean = s / n; return { mean: +mean.toFixed(1), stdev: +Math.sqrt(s2 / n - mean * mean).toFixed(1), w: c.width, h: c.height };
      });
      (report.frames ||= []).push({ chapter: +m[1], ...v });
    }
    if (/Film complete|Film:/.test(st)) { report.status_end = st; break; }
    if (++k % 50 === 0) console.log("film:", st);
    await page.waitForTimeout(400);
  }
  report.film_seconds = Math.round((Date.now() - t0) / 1000);
  report.chapters_played = [...seenChapters].map(Number);
  await page.waitForFunction(() => !document.querySelector("canvas[style*='177.7vh']"), null, { timeout: 30000 }).catch(() => {});
  report.overlay_closed_after_film = await page.evaluate(() => !document.querySelector("canvas[style*='177.7vh']"));
  await shot("5_after_film");
  // 5 · replay, then Close film mid-way
  await page.waitForFunction(() => !document.querySelector("#play").disabled, null, { timeout: 30000 });
  await page.click("#play");
  await page.getByRole("button", { name: "Close film", exact: true }).waitFor({ timeout: 60000 });
  await page.waitForTimeout(6000);
  await shot("6_replay");
  await page.getByRole("button", { name: "Close film", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector("canvas[style*='177.7vh']") && !document.querySelector("#play").disabled, null, { timeout: 30000 });
  report.replay_and_close = { status: short(await page.locator("#status").textContent()) };
  await shot("7_closed");
} catch (e) {
  report.error = short(String(e), 500);
  await shot("error");
}

// audio analysis
const samples = await page.evaluate(() => (window.__film || {}).samples || []).catch(() => []);
const score_bus_tapped = await page.evaluate(() => !!(window.__film || {}).scoreBus).catch(() => false);
const film = samples; // includes the replay; analyse the first playback only via gaps
const sum = (xs, f) => xs.reduce((a, x) => a + f(x), 0);
const avg = (xs, f) => xs.length ? +(sum(xs, f) / xs.length).toFixed(4) : null;
const during = film.filter((s) => s.voice_playing), between = film.filter((s) => !s.voice_playing);
const perChapter = {};
for (const s of film) {
  const c = s.chapter.slice(0, 2); const p = (perChapter[c] ||= { n: 0, voice_n: 0, voice_rms: 0, score_under_voice: 0 });
  p.n++; if (s.voice_playing) { p.voice_n++; p.voice_rms += s.voice; p.score_under_voice += s.score; }
}
for (const p of Object.values(perChapter)) { if (p.voice_n) { p.voice_rms = +(p.voice_rms / p.voice_n).toFixed(4); p.score_under_voice = +(p.score_under_voice / p.voice_n).toFixed(4); } }
report.audio = {
  score_bus_tapped, samples: film.length,
  speech_samples: during.length, music_only_samples: between.length,
  mean_rms_music_only: { score: avg(between, (s) => s.score), out: avg(between, (s) => s.out) },
  mean_rms_during_speech: { voice: avg(during, (s) => s.voice), score: avg(during, (s) => s.score), out: avg(during, (s) => s.out) },
  speech_samples_with_music_audible: during.filter((s) => s.score > 0.003).length,
  per_chapter: perChapter,
};
report.speak = speak; report.chats = chats;
report.voices_by_agent = {};
for (const s of speak) { const v = (report.voices_by_agent[s.agent] ||= { responses: 0, ok_audio: 0, voice_ids: [] }); v.responses++; if (s.status === 200 && /^audio\//.test(s.type || "")) v.ok_audio++; if (s.voice_id && !v.voice_ids.includes(s.voice_id)) v.voice_ids.push(s.voice_id); }
report.console_errors = consoleErrors; report.page_errors = pageErrors; report.video_requests = videoRequests;
report.screenshots = shots;
report.finished = new Date().toISOString();
const ids = Object.values(report.voices_by_agent).map((v) => v.voice_ids.join(","));
report.checks = {
  homepage_link_opens_demo: !!report.demo_url && /team-demo\/index\.html$/.test(report.demo_url),
  cinematic_files_served: !!(report.served && report.served["cinematic.js"]?.status === 200 && report.served["film-world.js"]?.status === 200 && report.served["demo.js"]?.exportVideo_false),
  three_models_mounted: (report.avatars_mounted || []).length === 3,
  all_chapters_prepared_with_model: report.prepared === (report.scenes || []).length && chats.length >= report.prepared && chats.every((c) => c.status === 200 && c.mode === "LLM"),
  every_chapter_audio_from_speak: speak.length >= (report.scenes || []).length && speak.every((s) => s.status === 200 && /^audio\//.test(s.type || "")),
  three_agents_three_distinct_voices: Object.keys(report.voices_by_agent).length === 3 && new Set(ids).size === 3 && ids.every(Boolean),
  all_chapters_played: (report.chapters_played || []).length === (report.scenes || []).length && /Film complete/.test(report.status_end || ""),
  film_canvas_not_blank: (report.frames || []).length > 0 && report.frames.every((f) => f && f.stdev > 8),
  music_audible_between_speech: (report.audio.mean_rms_music_only.score || 0) > 0.003,
  music_audible_beneath_speech: during.length > 0 && report.audio.speech_samples_with_music_audible / during.length > 0.9,
  speech_audible: (report.audio.mean_rms_during_speech.voice || 0) > 0.003,
  replay_and_close: !!report.replay_and_close,
  no_old_video_requested: videoRequests.length === 0,
  no_console_or_page_errors: consoleErrors.length === 0 && pageErrors.length === 0,
};
report.passed = Object.values(report.checks).every(Boolean) && !report.error;
fs.writeFileSync(`${OUT}/film_report.json`, JSON.stringify(report, null, 2));
console.log(JSON.stringify({ passed: report.passed, checks: report.checks, error: report.error || null,
  voices: report.voices_by_agent, audio: { ...report.audio, per_chapter: undefined }, frames: report.frames,
  console_errors: consoleErrors.slice(0, 5), page_errors: pageErrors.slice(0, 5) }, null, 1));
await browser.close();
if (server) server.close();
