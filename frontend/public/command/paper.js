/* PAPER ACCOUNT STRIP -- the same authoritative paper ledger the agent pages
   read (GET /api/command/paper/account, live via /api/command/paper/stream).
   Shows ONLY what the server sends: no default balance, no interpolation, no
   recomputation. LIVE MARKET DATA / SIMULATED EXECUTION. Real-money
   submission is disabled.

   Server shape (api/command_paper.py):
     account: {status: OK|EMPTY|UNAVAILABLE, why, data: {cash_usd, reserved_usd,
               available_usd, open_position_value_usd, total_equity_usd,
               realized_pnl_usd, unrealized_pnl_usd, last_updated_at,
               last_sequence, stale_marks, ...}}
     session: {active, reason, session_id, started_at, ...}   (when present)
   Stream: named events `snapshot` (balances) and `ledger` (entry, balances on
   the last of a batch), `heartbeat`, `unavailable`; id = ledger sequence, so
   the browser's automatic reconnect resumes from Last-Event-ID. */
(function () {
  var strip = document.createElement("div");
  strip.id = "paper-strip";
  strip.setAttribute("role", "region");
  strip.setAttribute("aria-label", "Paper trading account");
  strip.setAttribute("aria-live", "polite");
  document.body.insertBefore(strip, document.body.firstChild);
  var FIELDS = [["cash_usd", "Cash"], ["reserved_usd", "Reserved cash"],
    ["available_usd", "Available cash"], ["open_position_value_usd", "Open-position value"],
    ["total_equity_usd", "Total equity"], ["realized_pnl_usd", "Realized P&L"],
    ["unrealized_pnl_usd", "Unrealized P&L"]];
  var state = {bal: null, session: null, conn: "CONNECTING", why: ""};
  function usd(v) {
    if (v === null || v === undefined || v === "" || !isFinite(Number(v))) { return "—"; }
    var n = Number(v);
    return (n < 0 ? "−$" : "$") + Math.abs(n).toLocaleString("en-US",
      {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  function when(t) {
    if (t === null || t === undefined || t === "") { return "—"; }
    var d = typeof t === "number" ? new Date(t * 1000) : new Date(t);
    if (isNaN(d.getTime())) { return String(t); }
    return d.toLocaleString("en-US", {timeZone: "America/New_York", month: "short",
      day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"}) + " ET";
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function render() {
    var b = state.bal, s = state.session,
      head = '<span class="ps-tag">LIVE MARKET DATA · SIMULATED EXECUTION</span>';
    if (!b) {
      strip.innerHTML = head + '<span class="ps-why">PAPER ACCOUNT ' + esc(state.conn) +
        (state.why ? ": " + esc(state.why) : "") + "</span>";
      return;
    }
    var warn = "";
    if (b.stale_marks && b.stale_marks.length) {
      warn += ' <span class="ps-warn">' + b.stale_marks.length + " STALE MARK(S)</span>";
    }
    if (b.marks_complete === false) {
      warn += ' <span class="ps-warn">EQUITY INCOMPLETE: UNMARKED POSITIONS</span>';
    }
    var sess = s ? (s.active ? " · session " + esc(s.session_id || "?") :
      " · SESSION NOT RUNNING" + (s.reason ? " (" + esc(s.reason) + ")" : "")) : "";
    strip.innerHTML = head + FIELDS.map(function (f) {
      return '<span class="ps-f"><small>' + f[1] + "</small><b>" + usd(b[f[0]]) + "</b></span>";
    }).join("") + warn + '<span class="ps-meta">' + esc(state.conn) + " · updated " +
      esc(when(b.last_updated_at)) + sess + "</span>";
  }
  function take(bal) {
    if (bal && typeof bal === "object" && bal.ok !== false) { state.bal = bal; }
  }
  function load() {
    fetch("/api/command/paper/account?entries=1", {credentials: "same-origin", cache: "no-store"})
      .then(function (r) {
        if (!r.ok) {
          state.bal = null;
          state.conn = r.status === 401 || r.status === 403 ? "SIGN-IN REQUIRED" : "UNAVAILABLE";
          state.why = r.status === 404 ? "paper account routes are not in the serving API yet" :
            (r.status === 401 || r.status === 403) ? "unlock COMMAND to view" : "HTTP " + r.status;
          render();
          return;
        }
        return r.json().then(function (j) {
          var a = j.account || {};
          state.session = j.session || null;
          if (a.status === "OK" && a.data) {
            take(a.data); state.conn = "LIVE"; state.why = ""; render(); stream();
          } else {
            state.bal = null; state.conn = "UNAVAILABLE";
            state.why = a.why || "no account in the response"; render();
          }
        });
      })
      .catch(function (e) { state.bal = null; state.conn = "DISCONNECTED"; state.why = e.name; render(); });
  }
  var es = null;
  function parse(ev) { try { return JSON.parse(ev.data); } catch (e) { return null; } }
  function stream() {
    if (es || !window.EventSource) { return; }
    es = new EventSource("/api/command/paper/stream", {withCredentials: true});
    es.addEventListener("snapshot", function (ev) {
      var d = parse(ev); if (d && d.balances) { take(d.balances); }
      state.conn = "LIVE"; render();
    });
    es.addEventListener("ledger", function (ev) {
      var d = parse(ev); if (!d) { return; }
      if (d.balances) { take(d.balances); }
      else if (d.running_balances && state.bal) {
        state.bal = Object.assign({}, state.bal, d.running_balances,
          {last_updated_at: d.committed_at || state.bal.last_updated_at});
      }
      state.conn = "LIVE"; render();
    });
    es.addEventListener("heartbeat", function () { state.conn = "LIVE"; render(); });
    es.addEventListener("unavailable", function (ev) {
      var d = parse(ev); state.conn = "UNAVAILABLE"; state.why = (d && d.why) || ""; render();
    });
    es.onerror = function () { state.conn = "RECONNECTING"; render(); };
    es.onopen = function () { state.conn = "LIVE"; render(); };
  }
  render(); load();
})();
