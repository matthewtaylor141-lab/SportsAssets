"""THE SIGN-IN PAGE THE DESK NEEDS ON THIS ORIGIN.

WHY IT EXISTS, AND THE DEFECT IT CLOSES. The COMMAND cookie is minted by
`POST /api/command/session` with `path=/api/command`, so a browser sends
it only to paths under `/api/command`. The desk page was first served at
`/command/desk`, OUTSIDE that path: a signed-in browser sent no cookie
there and got 401 on every attempt, forever. Caught by opening the URL in
a real browser, which is the only way it could have been caught -- the
route was gated correctly and the header-authenticated checks all passed.

So the page moved under `/api/command/...`, and this is what an
unauthenticated viewer gets there: a form that posts the desk password to
the session endpoint and reloads. It holds no credential of its own,
stores nothing, and the desk's data endpoint stays gated either way.
"""

from __future__ import annotations

SIGN_IN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bettor EV Engine | Sign in</title>
<style>
:root {
  --ink-1: #e8edf4; --ink-2: #aab6c6; --ink-3: #7d8a9c;
  --bg: #090d14; --card: #111823; --line: #222c3a; --accent: #6aa9ff;
  --bad: #ff8794;
}
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: flex; align-items: center;
  justify-content: center; background: var(--bg); color: var(--ink-1);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif; padding: 24px;
}
.box {
  width: 100%; max-width: 420px; background: var(--card);
  border: 1px solid var(--line); border-radius: 14px; padding: 24px;
}
.mark {
  display: inline-flex; width: 30px; height: 30px; border-radius: 8px;
  align-items: center; justify-content: center; font-weight: 700;
  background: #1b2634; color: var(--accent); margin-right: 10px;
}
h1 { font-size: 17px; margin: 0 0 4px; display: flex; align-items: center; }
p { color: var(--ink-2); font-size: 13px; margin: 10px 0 18px; }
label { display: block; font-size: 12px; color: var(--ink-3);
        margin-bottom: 6px; }
input, button {
  width: 100%; font: inherit; padding: 10px 12px; border-radius: 9px;
  border: 1px solid var(--line); background: #0d131c; color: var(--ink-1);
}
button {
  margin-top: 12px; font-weight: 600; cursor: pointer; background: #1b2430;
  border-color: #3a4757;
}
button:focus-visible, input:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
.msg { margin-top: 12px; font-size: 12.5px; color: var(--bad);
       min-height: 18px; }
.note { margin-top: 16px; font-size: 11.5px; color: var(--ink-3); }
</style>
</head>
<body>
<div class="box">
  <h1><span class="mark">B</span>Bettor EV Engine</h1>
  <p>The operating desk is read-protected. Sign in with the Command desk
     password to open it on this origin.</p>
  <label for="pw">Desk password</label>
  <input id="pw" type="password" autocomplete="current-password"
         spellcheck="false">
  <button id="go" type="button">Open the desk</button>
  <div class="msg" id="msg" role="status" aria-live="polite"></div>
  <div class="note">The password is sent to this origin's own session
    endpoint and is never stored by this page. Control actions on the desk
    need a separate operator token.</div>
</div>
<script>
(function () {
  var pw = document.getElementById('pw');
  var msg = document.getElementById('msg');
  function go() {
    msg.textContent = '';
    fetch('/api/command/session', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pw.value})
    }).then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (j) {
        if (j && j.ok) { window.location.reload(); return; }
        msg.textContent = 'That password was not accepted.';
      })
      .catch(function (e) {
        msg.textContent = 'The sign-in request failed: ' + e.message;
      });
  }
  document.getElementById('go').addEventListener('click', go);
  pw.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { go(); }
  });
  pw.focus();
}());
</script>
</body>
</html>
"""
