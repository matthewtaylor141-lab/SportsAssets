/* THE PAPER EXPERIMENT ON THE COMMAND HOMEPAGE -- the default operational view.

   TWO ELEMENTS, ONE SOURCE OF TRUTH (the one paper ledger, one account):
     #paper-strip  the compact account strip at the top of every COMMAND view;
     #paper-home   the overview's lead panel: the session, the account figures,
                   each agent's paper status (Derek, Xavier, Audrey) and THREE
                   freshness stamps. app.js renders a slot (#paper-home-slot)
                   on the overview and calls BTPaper.mount(slot); the panel is
                   MOVED into it, so a re-render of the app never loses it.

   READS (same origin, the HttpOnly COMMAND cookie; nothing here holds a
   credential):
     GET /api/command/paper/overview            THE ONE READ: the account
                                                (bettor_paper_ops.account_
                                                section -> ledger balances(),
                                                the same function every agent
                                                page shows), session, agents,
                                                freshness
     GET /api/command/paper/stream              committed ledger entries (SSE):
                                                each one triggers a re-read of
                                                the overview, so the figures
                                                are never patched piecemeal

   SIGNED OUT (401/403 from a read, or the stream failing while a probe read
   answers 401/403): a SIGN-IN prompt that opens the existing unlock
   (unlock.js, BTUnlock.open) -- never an unexplained RECONNECTING.

   NEVER A DEFAULT FIGURE. A failed read shows UNAVAILABLE with the reason; a
   401 shows SIGN-IN REQUIRED; a route the serving API does not have yet says
   so. No balance is interpolated or recomputed here.

   THE THREE STAMPS ARE NEVER CONFLATED:
     last successful server read   this page's own clock at its last good read;
     last agent heartbeat          the paper runtime's own record
                                   (paper_session_health.heartbeat_at);
     last ledger transaction       max(committed_at) in the paper ledger.
   An unchanged cash balance is not stale; a recent read is not agent health.

   LIVE ON A PHONE (mobile Safari): the stream reconnects with exponential
   backoff; while it is not open every read repeats every 15 s; returning
   from the background (visibilitychange) or the back-forward cache
   (pageshow, persisted) re-reads at once and reopens the stream.

   LIVE MARKET DATA / SIMULATED EXECUTION. Real-money submission is disabled.
   The funded system is inactive; its feed status is shown by app.js in its
   own labelled section, never here. */
(function () {
  "use strict";
  var PROBE = "/api/command/paper/account?entries=1";
  var OVERVIEW = "/api/command/paper/overview";
  var STREAM = "/api/command/paper/stream";
  var POLL_MS = 15000, LIVE_MS = 30000, STALE_HB_S = 300;
  // THE SEVEN FIGURES: the same keys, labels and formatting rule as the agent
  // pages (agent_cc_ops.ACCOUNT_FIGURES / CC.ops.accountFigures)
  var FIELDS = [["cash_usd", "Cash"], ["reserved_usd", "Reserved"],
    ["available_usd", "Available"], ["open_position_value_usd", "Position value"],
    ["total_equity_usd", "Equity"], ["realized_pnl_usd", "Realized P&L"],
    ["unrealized_pnl_usd", "Unrealized P&L"]];
  var STRIP_KEEP = {cash_usd: 1, total_equity_usd: 1, realized_pnl_usd: 1};
  var st = {
    bal: null, balWhy: null, balState: "READING",
    ov: null, ovOkAt: null, ovTryAt: null, ovFail: null,
    stream: "CONNECTING", streamNote: "", lastSeq: null, signedOut: false
  };
  function figText(k, v) { return num(v) ? usd(v, /pnl/.test(k)) : v === null ? "NOT STATED" : "not sent"; }

  // ── formatting ────────────────────────────────────────────────────
  function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  function num(v) { return typeof v === "number" && isFinite(v); }
  function usd(v, signed) {
    if (!num(v)) { return null; }
    var s = Math.abs(v).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return (v < 0 ? "−$" : signed && v > 0 ? "+$" : "$") + s;
  }
  function nowS() { return Date.now() / 1000; }
  function epoch(t) {
    if (num(t)) { return t > 1e12 ? t / 1000 : t; }
    if (typeof t === "string" && /^\d{4}-\d{2}-\d{2}/.test(t)) { var x = Date.parse(t); return isNaN(x) ? null : x / 1000; }
    return null;
  }
  function when(t) {
    var e = epoch(t); if (e === null) { return null; }
    try {
      return new Date(e * 1000).toLocaleString("en-US", {timeZone: "America/New_York", month: "short",
        day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}) + " ET";
    } catch (_) { return new Date(e * 1000).toISOString(); }
  }
  function dur(s) {
    if (!num(s)) { return ""; }
    var a = Math.abs(s), t = a < 90 ? Math.round(a) + " s" : a < 5400 ? Math.round(a / 60) + " min" :
      a < 172800 ? (a / 3600).toFixed(1) + " h" : (a / 86400).toFixed(1) + " d";
    return s < 0 ? "in " + t : t + " ago";
  }
  function sec(s) { return s && typeof s === "object" && /^(OK|EMPTY|UNAVAILABLE)$/.test(s.status) ? s : null; }
  function okData(s) { s = sec(s); return s && s.status === "OK" ? s.data : null; }
  // an age on the server's clock, advanced by the time since the read
  function serverAge(at) {
    var ov = st.ov; if (!ov || !num(at) || !num(ov.as_of)) { return null; }
    return (ov.as_of - at) + (num(st.ovOkAt) ? Math.max(0, nowS() - st.ovOkAt) : 0);
  }

  // ── reads ─────────────────────────────────────────────────────────
  function fail(r, what) {
    if (r.status === 401 || r.status === 403) { return {state: "SIGN-IN REQUIRED", why: "unlock COMMAND to view the paper " + what}; }
    if (r.status === 404) { return {state: "UNAVAILABLE", why: "NOT YET RELEASED: the serving API does not have the paper " + what + " route yet"}; }
    return {state: "UNAVAILABLE", why: "the paper " + what + " read answered HTTP " + r.status};
  }
  function get(url, what) {
    return fetch(url, {method: "GET", credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}})
      .then(function (r) {
        if (!r.ok) { return {fail: fail(r, what)}; }
        return r.json().then(function (j) { return {json: j}; },
          function () { return {fail: {state: "UNAVAILABLE", why: "the paper " + what + " response was not JSON"}}; });
      }, function (e) { return {fail: {state: "UNAVAILABLE", why: "network error reading the paper " + what + " (" + ((e && e.name) || "Error") + ")"}}; });
  }
  function readOverview() {
    st.ovTryAt = nowS();
    return get(OVERVIEW, "overview").then(function (o) {
      if (o.fail) {
        st.ovFail = o.fail;
        if (o.fail.state === "SIGN-IN REQUIRED") { st.signedOut = true; st.bal = null; st.balState = o.fail.state; st.balWhy = o.fail.why; }
        else if (!st.ov) { st.bal = null; st.balState = o.fail.state; st.balWhy = o.fail.why; }
        return;
      }
      var whole = sec(o.json && o.json.overview);
      if (whole && whole.status !== "OK") { st.ovFail = {state: "UNAVAILABLE", why: whole.why || "the server named no reason"}; st.bal = null; st.balState = "UNAVAILABLE"; st.balWhy = st.ovFail.why; return; }
      st.ov = o.json; st.ovOkAt = nowS(); st.ovFail = null; st.signedOut = false;
      var a = sec(o.json.account);
      if (a && a.status === "OK" && a.data) {
        st.bal = a.data; st.balState = "OK"; st.balWhy = null;
        if (num(a.data.last_sequence)) { st.lastSeq = Math.max(st.lastSeq || 0, a.data.last_sequence); }
        if (!es && !reconnectT) { stream(); }
      } else { st.bal = null; st.balState = a ? a.status : "UNAVAILABLE"; st.balWhy = (a && a.why) || "the overview carried no account section"; }
    });
  }
  var busy = false;
  function load() {
    if (busy) { return Promise.resolve(); }
    busy = true;
    return readOverview().then(function () { busy = false; render(); },
      function () { busy = false; render(); });
  }
  // AN EventSource CANNOT SEE A 401: on a stream error, ask a plain read
  function probeAuth() {
    return get(PROBE, "account").then(function (o) {
      if (o.fail && o.fail.state === "SIGN-IN REQUIRED") {
        if (es) { es.close(); es = null; }
        clearTimeout(reconnectT); reconnectT = null;
        st.signedOut = true; st.stream = "SIGN-IN REQUIRED"; st.streamNote = "the live stream needs a COMMAND session";
        render(); return true;
      }
      return false;
    });
  }

  // ── the stream, with backoff ─────────────────────────────────────
  var es = null, retry = 0, reconnectT = null, pollT = null, soonT = null;
  function open() { return !!(es && es.readyState === 1); }
  function parse(ev) { try { return JSON.parse(ev.data); } catch (e) { return null; } }
  function stream() {
    if (!window.EventSource) { st.stream = "POLLING"; st.streamNote = "this browser has no EventSource"; return; }
    if (es || reconnectT) { return; }
    var url = STREAM + (st.lastSeq !== null ? "?last=" + encodeURIComponent(st.lastSeq) : "");
    var src = es = new EventSource(url);
    src.onopen = function () { if (src !== es) { return; } retry = 0; st.stream = "LIVE"; st.streamNote = ""; render(); schedule(); };
    src.addEventListener("snapshot", function () { if (src !== es) { return; } st.stream = "LIVE"; render(); });
    src.addEventListener("ledger", function (ev) {
      if (src !== es) { return; }
      var d = parse(ev);
      if (d && num(d.sequence)) { st.lastSeq = Math.max(st.lastSeq || 0, d.sequence); }
      st.stream = "LIVE";
      // A COMMITTED ENTRY: re-read the one account read (debounced), so the
      // strip, the panel and every agent page show the same figures
      clearTimeout(soonT); soonT = setTimeout(function () { readOverview().then(render, render); }, 800);
    });
    src.addEventListener("heartbeat", function () { if (src !== es) { return; } st.stream = "LIVE"; st.streamNote = "server heartbeat " + new Date().toISOString().slice(11, 19) + "Z"; render(); });
    src.addEventListener("unavailable", function (ev) {
      if (src !== es) { return; }
      var d = parse(ev); src.close(); es = null; st.stream = "UNAVAILABLE"; st.streamNote = (d && d.why) || ""; render(); schedule();
    });
    src.onerror = function () {
      if (src !== es) { return; }
      probeAuth();
      if (src.readyState === 2) {
        src.close(); es = null; st.stream = "RECONNECTING";
        var wait = Math.min(30000, 1000 * Math.pow(2, retry++));
        st.streamNote = "retry in " + Math.round(wait / 1000) + " s; polling every 15 s meanwhile";
        reconnectT = setTimeout(function () { reconnectT = null; stream(); }, wait);
      } else { st.stream = "RECONNECTING"; st.streamNote = "polling every 15 s meanwhile"; }
      render(); schedule();
    };
  }
  // THE POLLING FALLBACK: 15 s whenever the stream is not open, 30 s while it is
  function schedule() {
    clearTimeout(pollT);
    pollT = setTimeout(function () {
      if (document.hidden) { schedule(); return; }
      load().then(schedule, schedule);
    }, open() ? LIVE_MS : POLL_MS);
  }
  function wake() {
    if (document.hidden) { return; }
    if (es && es.readyState === 2) { es.close(); es = null; }
    if (!es) { clearTimeout(reconnectT); reconnectT = null; retry = 0; }
    load().then(function () { if (!es && st.bal && !st.signedOut) { stream(); } schedule(); }, schedule);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { wake(); } });
  window.addEventListener("pageshow", function (e) {
    if (e && e.persisted) { if (es) { es.close(); es = null; } clearTimeout(reconnectT); reconnectT = null; retry = 0; wake(); }
  });
  window.addEventListener("online", wake);

  // ── the strip (every view) ───────────────────────────────────────
  var strip = document.createElement("div");
  strip.id = "paper-strip";
  strip.setAttribute("role", "region");
  strip.setAttribute("aria-label", "Paper trading account");
  strip.setAttribute("aria-live", "polite");
  document.body.insertBefore(strip, document.body.firstChild);
  function streamLabel() { return st.signedOut ? "SIGN-IN REQUIRED" : st.stream === "LIVE" ? "LIVE" : st.stream === "CONNECTING" ? "CONNECTING" : st.stream === "UNAVAILABLE" ? "STREAM UNAVAILABLE" : "POLLING 15 s"; }
  // THE SIGN-IN PROMPT: opens the existing unlock (unlock.js); a plain link to
  // the homepage when the unlock is not loaded
  function signinHtml() {
    return '<div class="ph-signin" role="alert"><b>SIGN-IN REQUIRED</b> · your COMMAND session is missing or has expired, so nothing here is current. ' +
      '<a href="/" data-paper-signin>Sign in to COMMAND →</a> <span class="ph-mute">Reads retry every 15 s and resume once you are signed in.</span></div>';
  }
  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-paper-signin]") : null;
    if (t && window.BTUnlock && typeof window.BTUnlock.open === "function") { e.preventDefault(); window.BTUnlock.open(); }
  });
  function renderStrip() {
    var head = '<span class="ps-tag">PAPER · LIVE MARKET DATA · SIMULATED EXECUTION</span>', b = st.bal;
    if (!b) {
      strip.innerHTML = head + '<span class="ps-why"><b>' + esc(st.balState) + "</b>" + (st.balWhy ? ": " + esc(st.balWhy) : "") +
        (st.signedOut ? ' · <a href="/" data-paper-signin>Sign in</a>' : "") + "</span>";
      return;
    }
    var warn = "";
    if (b.stale_marks && b.stale_marks.length) { warn += ' <span class="ps-warn">' + b.stale_marks.length + " STALE MARK(S)</span>"; }
    if (b.marks_complete === false) { warn += ' <span class="ps-warn">EQUITY INCOMPLETE: UNMARKED POSITIONS</span>'; }
    strip.innerHTML = head + FIELDS.map(function (f) {
      var shown = figText(f[0], b[f[0]]);
      return '<span class="ps-f' + (STRIP_KEEP[f[0]] ? " ps-keep" : "") + '"><small>' + esc(f[1]) + "</small><b>" + esc(shown) + "</b></span>";
    }).join("") + warn + '<span class="ps-meta">' + esc(streamLabel()) + " · last ledger change " + esc(when(b.last_updated_at) || "not sent") + "</span>";
  }

  // ── the overview panel ───────────────────────────────────────────
  var panel = document.createElement("section");
  panel.id = "paper-home";
  panel.className = "paper-home";
  panel.setAttribute("aria-label", "Paper experiment: the active session");
  function stampHtml(label, value, sub, chip) {
    return '<div class="ph-stamp"><div class="ph-l">' + esc(label) + (chip ? ' <span class="ph-chip ' + esc(chip.replace(/[^A-Z-]/g, "")) + '">' + esc(chip) + "</span>" : "") +
      '</div><div class="ph-v">' + value + '</div><div class="ph-s">' + sub + "</div></div>";
  }
  function stamps() {
    var ov = st.ov, f = st.ovFail, gone = f && f.state === "SIGN-IN REQUIRED", fr = ov && ov.freshness ? ov.freshness : {};
    var a = stampHtml("Last successful server read",
      num(st.ovOkAt) ? esc(when(st.ovOkAt)) + ' <span class="ph-age">' + esc(dur(nowS() - st.ovOkAt)) + "</span>" : esc(gone ? "SIGN-IN REQUIRED" : "no successful read yet"),
      (f ? "last attempt " + esc(f.state) + ": " + esc(f.why) + ". " : "") + "A recent read is not agent health.",
      f ? (gone ? "SIGN-IN" : "UNAVAILABLE") : num(st.ovOkAt) ? "OK" : null);
    var hb = sec(fr.agent_heartbeat), b;
    if (!ov) { b = stampHtml("Last agent heartbeat (paper runtime)", esc(gone ? "SIGN-IN REQUIRED" : "UNAVAILABLE"), esc(f ? f.why : "not read yet"), gone ? "SIGN-IN" : "UNAVAILABLE"); }
    else if (!hb || hb.status !== "OK") { b = stampHtml("Last agent heartbeat (paper runtime)", hb && hb.status === "EMPTY" ? "NO HEARTBEAT YET" : "UNAVAILABLE", esc((hb && hb.why) || "not in the read"), hb && hb.status === "EMPTY" ? "STALE" : "UNAVAILABLE"); }
    else {
      var h = hb.data || {}, age = serverAge(h.heartbeat_at), lim = num(h.stale_after_s) ? h.stale_after_s : STALE_HB_S;
      b = stampHtml("Last agent heartbeat (paper runtime)",
        num(h.heartbeat_at) ? esc(when(h.heartbeat_at)) + ' <span class="ph-age">' + esc(dur(age)) + "</span>" : "no pass has run in this session",
        (num(h.passes) ? h.passes : "?") + " pass(es) · " + (num(h.errors) ? h.errors : "?") + " error(s)" + (h.last_error ? " · last error: " + esc(h.last_error) : "") + " · from paper_session_health",
        age === null || age > lim ? "STALE" : "RECENT");
    }
    var lg = sec(fr.ledger), c;
    if (!ov) { c = stampHtml("Last ledger transaction", esc(gone ? "SIGN-IN REQUIRED" : "UNAVAILABLE"), esc(f ? f.why : "not read yet"), gone ? "SIGN-IN" : "UNAVAILABLE"); }
    else if (!lg || lg.status !== "OK") { c = stampHtml("Last ledger transaction", lg && lg.status === "EMPTY" ? "NO LEDGER ENTRY" : "UNAVAILABLE", esc((lg && lg.why) || "not in the read"), lg && lg.status === "EMPTY" ? null : "UNAVAILABLE"); }
    else {
      var l = lg.data || {};
      c = stampHtml("Last ledger transaction", esc(when(l.committed_at) || "no commit time") + ' <span class="ph-age">' + esc(dur(serverAge(l.committed_at))) + "</span>",
        esc(l.kind || "") + (num(l.sequence) ? " · entry #" + l.sequence : "") + " · an unchanged balance is not a stale one", null);
    }
    return '<div class="ph-stamps">' + a + b + c + '</div><div class="ph-conn">Live updates: <b>' + esc(streamLabel()) + "</b>" + (st.streamNote ? " · " + esc(st.streamNote) : "") + "</div>";
  }
  function figures() {
    var b = st.bal;
    if (!b) { return '<div class="ph-fail"><b>' + esc(st.balState) + "</b> · " + esc(st.balWhy || "not read yet") + '<br><span class="ph-mute">No paper figure is shown: none was read. This is not a zero balance.</span></div>'; }
    return '<div class="ph-figs">' + FIELDS.map(function (f) {
      var t = figText(f[0], b[f[0]]), shown = t === "NOT STATED" || t === "not sent" ? '<span class="ph-ns">' + esc(t) + "</span>" : esc(t);
      return '<div data-acct7="' + f[0] + '"><small>' + esc(f[1]) + "</small><b>" + shown + "</b></div>";
    }).join("") + '</div><p class="ph-note">' + esc(b.equity_basis || "") + (num(b.last_sequence) ? " · ledger entry #" + b.last_sequence : "") +
      (b.ledger_consistent === true ? " · running balance agrees with the ledger" : b.ledger_consistent === false ? " · LEDGER INCONSISTENT" : "") +
      " · fictional USD, never summed with the funded system · real-money submission " + esc(b.real_money_submission || "DISABLED") + ".</p>";
  }
  var AGENTS = [["derek", "Derek", "Discovery & Entry"], ["xavier", "Xavier", "Portfolio Management"], ["audrey", "Audrey", "Audit & Intelligence"]];
  function agentLine(k, d) {
    if (k === "derek") {
      var by = d.by_strategy || {}, rows = ["DEREK_ENTRY_POLICY_V2", "PINNACLE_ONLY_PAPER_BENCHMARK", "PINNACLE_COMPLETED_GAME_PAPER"].filter(function (s) { return by[s]; });
      var names = {DEREK_ENTRY_POLICY_V2: "research (two-model)", PINNACLE_ONLY_PAPER_BENCHMARK: "benchmark · experimental", PINNACLE_COMPLETED_GAME_PAPER: "completed-game · conditional, experimental"};
      return rows.map(function (s) { var r = by[s]; return '<li><b>' + esc(r.decisions) + "</b> " + esc(names[s]) + " decisions · " + esc(r.enter) + " ENTER · " + esc(r.last_24h) + " in 24 h</li>"; }).join("");
    }
    if (k === "xavier") {
      return "<li><b>" + esc(d.handoffs) + "</b> positions handed over · <b>" + esc(d.open_positions) + "</b> open · " + esc(d.pending_settlement) + " pending settlement</li><li>" +
        esc(d.reviews) + " reviews (" + esc(d.reviews_24h) + " in 24 h) · " + esc(d.open_management_orders) + " open protective orders (orders, not fills: not protection until filled) · " + esc(d.settlements) + " settlements</li>";
    }
    return "<li><b>" + esc(d.findings) + "</b> findings (" + esc(d.warnings_or_critical) + " warning or critical) · " + esc(d.findings_24h) + " in 24 h</li><li>latest daily report " + esc(d.last_report_day || "none yet") + "</li>";
  }
  function agents() {
    var ov = st.ov, f = st.ovFail, ag = ov && ov.agents ? ov.agents : {};
    return '<div class="ph-agents">' + AGENTS.map(function (a) {
      var s = ov ? sec(ag[a[0]]) : null, body;
      if (!ov) { body = '<p class="ph-fail"><b>' + esc(f ? f.state : "READING") + "</b> · " + esc(f ? f.why : "reading") + "</p>"; }
      else if (!s) { body = '<p class="ph-fail"><b>UNAVAILABLE</b> · the overview carried no ' + esc(a[1]) + " section</p>"; }
      else if (s.status === "UNAVAILABLE") { body = '<p class="ph-fail"><b>UNAVAILABLE</b> · ' + esc(s.why) + "</p>"; }
      else if (s.status === "EMPTY") { body = '<p class="ph-mute">Nothing recorded yet · ' + esc(s.why) + "</p>"; }
      else { body = '<ul class="ph-list">' + agentLine(a[0], s.data || {}) + "</ul>" + (num((s.data || {}).last_activity_at) ? '<p class="ph-mute">last activity ' + esc(when(s.data.last_activity_at)) + "</p>" : ""); }
      return '<a class="ph-agent" href="/' + a[0] + '"><span class="ph-an"><span class="agent-dot ' + a[0] + '" aria-hidden="true"></span><b>' + esc(a[1]) + "</b><small>" + esc(a[2]) + '</small><span class="ph-go">Open →</span></span>' + body + "</a>";
    }).join("") + "</div>";
  }
  function session() {
    var s = st.ov ? okData(st.ov.session) : null;
    if (!s) { return ""; }
    return '<p class="ph-sess">' + (s.active ? "<b>SESSION ACTIVE</b> · " + esc(s.session_id) + " · started " + esc(when(s.started_at) || "?")
      : "<b>SESSION NOT RUNNING</b> · " + esc(s.reason || "no reason given") + (s.session_id ? " · last session " + esc(s.session_id) : "")) + "</p>";
  }
  function renderPanel() {
    panel.innerHTML = '<div class="ph-head"><h2>Paper experiment <span class="ph-tag">LIVE MARKET DATA · SIMULATED EXECUTION</span></h2>' +
      '<p class="ph-sub">The active paper session: one fictional account, one ledger. This is the default view; the funded system is inactive and shown separately below.</p></div>' +
      (st.signedOut ? signinHtml() : "") + session() + stamps() + figures() + agents();
  }
  function render() { renderStrip(); renderPanel(); }

  // ── the API app.js uses ──────────────────────────────────────────
  window.BTPaper = {
    mount: function (slot) { if (slot && panel.parentNode !== slot) { slot.appendChild(panel); } },
    status: function () { return {stream: st.stream, account: st.balState, signedOut: st.signedOut, overview: st.ovFail ? st.ovFail.state : st.ov ? "OK" : "READING"}; },
    // the formatting contract, for the proof that every page agrees
    figures: function (a) { return FIELDS.map(function (f) { return {key: f[0], label: f[1], value: figText(f[0], a ? a[f[0]] : undefined)}; }); }
  };
  setInterval(function () { if (!document.hidden) { renderPanel(); } }, 5000);
  render();
  var slot0 = document.getElementById("paper-home-slot"); if (slot0) { window.BTPaper.mount(slot0); }
  load().then(schedule, schedule);
})();
