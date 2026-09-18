/* COMMAND ACCESS — the sign-in the page did not have.
 *
 * core.Feed surfaces a 401 as "Authentication required. Sign in through the
 * secured host." There was no secured host to sign in through: the page had
 * no unlock, and its fetch sends no headers by design. So on a deployed
 * browser COMMAND could reach the real feed and then sit at that message
 * for ever. This file is that missing step, and nothing else.
 *
 * WHAT IT DOES NOT DO. It does not store the password, put it in a URL, a
 * log line, localStorage or any global. It never sees the session token:
 * the API returns the token as an HttpOnly cookie, which JavaScript cannot
 * read by construction, and the response body carries no token at all.
 * It touches no view, no route and no report — if the feed is reachable it
 * removes itself and COMMAND runs exactly as before.
 *
 * It is separate from app.js on purpose: access must not be able to break
 * navigation or reporting. */
(function () {
  'use strict';

  var SNAPSHOT = '/api/command/snapshot';
  var SESSION = '/api/command/session';

  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text != null) n.textContent = text;
    return n;
  }

  function panel(onSubmit) {
    var wrap = el('div', {
      id: 'command-unlock', role: 'dialog', 'aria-modal': 'true',
      'aria-label': 'Sign in to Command',
      style: 'position:fixed;inset:0;z-index:9999;display:flex;' +
        'align-items:center;justify-content:center;background:#090d14;' +
        'font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#e8edf5'
    });
    var card = el('form', {
      style: 'width:min(380px,calc(100% - 32px));padding:28px;border-radius:14px;' +
        'background:#111826;border:1px solid #223049;display:flex;' +
        'flex-direction:column;gap:14px'
    });
    card.appendChild(el('div', {
      style: 'letter-spacing:.22em;font-size:11px;color:#8296b4'
    }, 'B E T T O R T O K E N   C O M M A N D'));
    card.appendChild(el('h1', { style: 'margin:0;font-size:20px;font-weight:600' },
      'Sign in'));
    card.appendChild(el('p', { style: 'margin:0;color:#8296b4;font-size:13px' },
      'Read-only workspace. Access is enforced by the server; this page never ' +
      'stores or reads your credential.'));
    var input = el('input', {
      type: 'password', autocomplete: 'current-password', required: 'required',
      'aria-label': 'Workspace password',
      style: 'padding:11px 12px;border-radius:9px;border:1px solid #2b3b58;' +
        'background:#0b1220;color:#e8edf5;font-size:15px'
    });
    var err = el('div', {
      role: 'alert', 'aria-live': 'polite',
      style: 'min-height:18px;color:#ff9b9b;font-size:13px'
    });
    var go = el('button', {
      type: 'submit',
      style: 'padding:11px 12px;border-radius:9px;border:0;background:#3b82f6;' +
        'color:#fff;font-size:15px;font-weight:600;cursor:pointer'
    }, 'Unlock');
    card.appendChild(input);
    card.appendChild(err);
    card.appendChild(go);
    card.addEventListener('submit', function (e) {
      e.preventDefault();
      err.textContent = '';
      go.disabled = true;
      go.textContent = 'Checking…';
      onSubmit(input.value, function (message) {
        // The value is cleared whether we succeeded or not, and it is
        // never copied anywhere on the way past.
        input.value = '';
        go.disabled = false;
        go.textContent = 'Unlock';
        err.textContent = message || '';
      });
    });
    wrap.appendChild(card);
    document.body.appendChild(wrap);
    input.focus();
    return wrap;
  }

  function submit(password, done) {
    fetch(SESSION, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password })
    }).then(function (r) {
      if (r.status === 429) { done('Too many attempts. Wait a minute.'); return null; }
      if (!r.ok) { done('Sign-in unavailable (' + r.status + ').'); return null; }
      return r.json();
    }).then(function (body) {
      if (!body) return;
      if (!body.ok) { done('That password was not accepted.'); return; }
      // The cookie is set; reload so the feed starts clean rather than
      // half-connected.
      location.reload();
    }).catch(function () {
      done('Sign-in could not be reached.');
    });
  }

  function check() {
    fetch(SNAPSHOT, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json' }
    }).then(function (r) {
      // 401/403 is the ONLY case this file acts on. A 503 means the feed
      // is authenticated and the ledger is unreadable -- COMMAND's own
      // banner says so far better than a login box would, and showing a
      // password prompt for an outage would be a lie about the cause.
      if (r.status === 401 || r.status === 403) panel(submit);
    }).catch(function () { /* offline: COMMAND's own banner covers it */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', check);
  } else {
    check();
  }
})();
