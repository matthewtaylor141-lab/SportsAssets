/* BETTOR · PM ACCEPTANCE, DERIVED FROM EVIDENCE. window.BTAcceptance is the
 * only global it defines.
 *
 * NO STATE IS TYPED BY HAND. The page reads:
 *   data/requirements-matrix.json   DESIGN data: requirement -> code, tests,
 *                                   migrations, API, worker, frontend, owner,
 *                                   readback. It holds no status.
 *   data/receipts/index.json + each receipt file
 *                                   immutable release receipts written by
 *                                   backend/tools/release_receipt.py; each is
 *                                   re-hashed here (sha256 of its canonical
 *                                   JSON) and a mismatch is TAMPERED.
 *   GET /api/command/release        the running API SHA, the workers' boot
 *                                   SHA, schema_migrations 216-225, and the
 *                                   receipts the API itself serves.
 *   GET <each requirement's readback endpoint>, only when the release read
 *                                   succeeded (a session exists).
 * and COMPUTES every stage per requirement. Anything the evidence does not
 * establish is UNKNOWN and shown red. FORWARD-VALIDATED has no evidence
 * source yet, so it is UNKNOWN everywhere -- by construction, not by typing.
 *
 * Read only: no control, approval or deployment path. */
(function (root) {
  'use strict';

  var SRC = {
    matrix: 'data/requirements-matrix.json',
    index: 'data/receipts/index.json',
    gates: 'data/receipts/gates.json',
    receiptDir: 'data/receipts/',
    release: '/api/command/release'
  };
  var STAGES = ['BUILT', 'TESTED', 'GATED', 'DEPLOYED', 'PRODUCTION-READBACK', 'FORWARD-VALIDATED'];
  var SHORT = {'BUILT': 'Built', 'TESTED': 'Tested', 'GATED': 'Gated', 'DEPLOYED': 'Deployed',
    'PRODUCTION-READBACK': 'Readback', 'FORWARD-VALIDATED': 'Forward'};
  var S = {matrix: null, receipts: [], gates: [], live: null, readbacks: {}, sel: null,
    ui: {q: '', group: '', blocked: false}, readAt: null, staleCopy: []};

  // ── small helpers ─────────────────────────────────────────────────
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function short(sha) { return sha ? String(sha).slice(0, 7) : '—'; }
  function nowIso() { return new Date().toISOString().slice(0, 19) + 'Z'; }
  function hm(iso) { return iso ? String(iso).replace('T', ' ').replace('+00:00', 'Z').slice(0, 16) + 'Z' : '—'; }
  function num(n) { return n == null ? '—' : Number(n).toLocaleString('en-US'); }
  function st(s, why, src) { return {s: s, why: why || '', src: src || ''}; }

  // The receipt hash: sha256 over Python's json.dumps(sort_keys=True,
  // separators=(",", ":"), ensure_ascii=False) of every field but
  // receipt_sha256. JSON.stringify escapes strings the same way.
  function canonical(v) {
    if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
    if (v && typeof v === 'object') {
      return '{' + Object.keys(v).sort().map(function (k) {
        return JSON.stringify(k) + ':' + canonical(v[k]);
      }).join(',') + '}';
    }
    return JSON.stringify(v);
  }
  function verifyReceipt(doc) {
    var subtle = root.crypto && root.crypto.subtle;
    if (!subtle || !root.TextEncoder) return Promise.resolve('UNVERIFIED');
    var body = {};
    Object.keys(doc).forEach(function (k) { if (k !== 'receipt_sha256') body[k] = doc[k]; });
    return subtle.digest('SHA-256', new TextEncoder().encode(canonical(body))).then(function (buf) {
      var hex = Array.prototype.map.call(new Uint8Array(buf), function (b) {
        return ('0' + b.toString(16)).slice(-2);
      }).join('');
      return hex === doc.receipt_sha256 ? 'VERIFIED' : 'TAMPERED';
    }, function () { return 'UNVERIFIED'; });
  }

  function shaMatch(a, b) {
    a = String(a || '').toLowerCase(); b = String(b || '').toLowerCase();
    if (!/^[0-9a-f]{7,40}$/.test(a) || !/^[0-9a-f]{7,40}$/.test(b)) return null;
    var n = Math.min(a.length, b.length);
    if (a.slice(0, n) !== b.slice(0, n)) return 'DIFFERENT';
    return n === 40 ? 'EXACT' : 'PREFIX_' + n;
  }

  function getJson(url, timeoutMs) {
    var ctl = root.AbortController ? new AbortController() : null;
    var t = ctl ? setTimeout(function () { ctl.abort(); }, timeoutMs || 10000) : null;
    var t0 = Date.now();
    return fetch(url, {method: 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: {Accept: 'application/json'}, signal: ctl ? ctl.signal : undefined})
      .then(function (r) {
        if (t) clearTimeout(t);
        var meta = {code: r.status, at: nowIso(), ms: Date.now() - t0};
        if (!r.ok) return {ok: false, meta: meta, why: url + ' answered HTTP ' + r.status};
        return r.json().then(function (d) { return {ok: true, meta: meta, doc: d}; },
          function () { return {ok: false, meta: meta, why: url + ' did not return JSON'}; });
      }, function (e) {
        if (t) clearTimeout(t);
        return {ok: false, meta: {code: 0, at: nowIso(), ms: Date.now() - t0},
          why: url + ' unreachable (' + ((e && e.name) || 'network') + ')'};
      });
  }

  // ── THE DERIVATION: one requirement against one receipt ──────────
  function computeRow(row, R, live, readbacks) {
    var out = {}, doc = R && R.doc, tf = (doc && doc.test_files) || {};
    var hasReport = !!doc && Object.keys(tf).length > 0;
    var atSha = (doc && doc.migrations && doc.migrations.at_sha) || null;
    readbacks = readbacks || {};

    // BUILT: the requirement's tests were collected at this SHA and its
    // migrations are in this SHA's tree.
    if (!doc) out.BUILT = st('UNKNOWN', 'no receipt selected');
    else {
      var missT = row.tests.filter(function (t) { return !tf[t]; });
      var missM = row.migrations.filter(function (m) { return !atSha || atSha.indexOf(m) < 0; });
      if (!row.tests.length && !row.migrations.length) out.BUILT = st('UNKNOWN', 'no test or migration is mapped to this requirement');
      else if (row.tests.length && !hasReport) {
        out.BUILT = (missM.length || !row.migrations.length)
          ? st('UNKNOWN', 'receipt ' + doc.gate_id + ' has no completed test report (state ' + doc.state + '), so the code at ' + short(doc.sha) + ' is not established')
          : st('PARTIAL', 'migrations are in the tree at ' + short(doc.sha) + '; tests not established (no completed report)', 'receipt ' + doc.gate_id);
      } else if (missT.length || missM.length) {
        out.BUILT = st('FAIL', 'not in the gated tree at ' + short(doc.sha) + ': ' + missT.concat(missM).join(', '), 'receipt ' + doc.gate_id);
      } else out.BUILT = st('PASS', 'every mapped test collected and every mapped migration present at ' + short(doc.sha), 'receipt ' + doc.gate_id);
    }

    // TESTED: every mapped test file passed something and failed nothing.
    if (!doc || !hasReport) {
      out.TESTED = st(doc && doc.state === 'VOID' ? 'VOID' : 'UNKNOWN',
        doc ? 'no completed test report in receipt ' + doc.gate_id + ' (' + doc.state + ')' : 'no receipt');
    } else if (!row.tests.length) out.TESTED = st('UNKNOWN', 'no test mapped');
    else {
      var bad = [], absent = [], passed = 0;
      row.tests.forEach(function (t) {
        var c = tf[t];
        if (!c) { absent.push(t); return; }
        passed += c.passed;
        if (c.failed) bad.push(t + ' (' + c.failed + ' failed)');
        else if (!c.passed) bad.push(t + ' (nothing passed: ' + c.skipped + ' skipped)');
      });
      if (absent.length) out.TESTED = st('FAIL', 'not run at ' + short(doc.sha) + ': ' + absent.join(', '), 'receipt ' + doc.gate_id);
      else if (bad.length) out.TESTED = st('FAIL', bad.join('; '), 'receipt ' + doc.gate_id);
      else out.TESTED = st('PASS', passed + ' tests passed, 0 failed across ' + row.tests.length + ' file(s)', 'receipt ' + doc.gate_id);
    }

    // GATED: the receipt's own verdict, hash-verified, over a TESTED row.
    if (!doc) out.GATED = st('UNKNOWN', 'no receipt');
    else if (R.integrity === 'TAMPERED') out.GATED = st('FAIL', 'receipt hash does not verify');
    else if (doc.state === 'VOID') out.GATED = st('VOID', 'gate ' + doc.gate_id + ' is VOID: ' + (((doc.void_hits || [])[0] || {}).code || 'environment failure'));
    else if (doc.state === 'INCOMPLETE') out.GATED = st('UNKNOWN', 'gate ' + doc.gate_id + ' has not completed');
    else if (doc.state !== 'GATED') out.GATED = st('FAIL', 'gate ' + doc.gate_id + ' is ' + doc.state);
    else if (out.TESTED.s !== 'PASS') out.GATED = st('FAIL', 'the gate accepted the run, but this requirement is not TESTED in it');
    else if (R.integrity !== 'VERIFIED') out.GATED = st('UNKNOWN', 'gate ' + doc.gate_id + ' GATED, but this browser could not verify the receipt hash (' + R.integrity + ')');
    else out.GATED = st('PASS', 'gate ' + doc.gate_id + ' GATED ' + short(doc.sha) + ' with 0 new regressions; receipt hash verified', 'receipt ' + doc.gate_id);

    // DEPLOYED: the running SHAs against the receipt's SHA.
    var L = live && live.ok ? live.doc : null;
    if (!doc) out.DEPLOYED = st('UNKNOWN', 'no receipt');
    else if (out.BUILT.s === 'FAIL') out.DEPLOYED = st('FAIL', 'not built at ' + short(doc.sha) + ', so running that SHA does not deploy it');
    else if (L) {
      var am = shaMatch(L.api && L.api.sha, doc.sha);
      var wm = row.worker ? shaMatch(L.workers && L.workers.sha, doc.sha) : 'N/A';
      var srcL = 'GET /api/command/release at ' + hm(live.meta.at);
      if (am === null) out.DEPLOYED = st('UNKNOWN', 'the API reports no build SHA (' + ((L.api || {}).why || 'unavailable') + ')', srcL);
      else if (am === 'DIFFERENT') out.DEPLOYED = st('FAIL', 'the API runs ' + short(L.api.sha) + ', not ' + short(doc.sha), srcL);
      else if (wm === null) out.DEPLOYED = st('PARTIAL', 'API runs ' + short(doc.sha) + ' (' + am + '); worker SHA unavailable (' + ((L.workers || {}).why || '') + ')', srcL);
      else if (wm === 'DIFFERENT') out.DEPLOYED = st('PARTIAL', 'API runs ' + short(doc.sha) + '; workers booted ' + short(L.workers.sha), srcL);
      else out.DEPLOYED = st('PASS', 'API ' + am + (row.worker ? ' · workers ' + wm : '') + ' on ' + short(doc.sha), srcL);
    } else {
      var d = doc.deploy || {}, api = d.api || {}, wk = d.workers || {};
      var apiLive = api.status === 'LIVE' && /^(EXACT|PREFIX_)/.test(api.match || '');
      var wkLive = wk.status === 'LIVE' && /^(EXACT|PREFIX_)/.test(wk.match || '');
      var srcR = 'receipt ' + doc.gate_id + ' deploy evidence (live read unavailable: ' + ((live && live.why) || 'not read') + ')';
      if (apiLive && (wkLive || !row.worker)) out.DEPLOYED = st('PASS', 'recorded LIVE: API ' + api.match + (row.worker ? ', workers ' + wk.match : ''), srcR);
      else if (apiLive) out.DEPLOYED = st('PARTIAL', 'API recorded LIVE (' + api.match + ' ' + (api.sha || '') + ' at ' + hm(api.at) + '); workers ' + (wk.status || 'PENDING') + ' — live not read back', srcR);
      else out.DEPLOYED = st('UNKNOWN', 'no live read and no LIVE deploy evidence in receipt ' + doc.gate_id, srcR);
    }

    // PRODUCTION-READBACK: production answers for THIS requirement.
    var rb = row.readback || {}, K = 'PRODUCTION-READBACK';
    if (!L) out[K] = st('UNKNOWN', 'GET /api/command/release was not readable (' + ((live && live.why) || 'not read') + '), so production was not read back');
    else if (rb.kind === 'migration') {
      var tr = ((L.schema || {}).tracked || []).filter(function (x) { return x.number === rb.number; })[0];
      if (!L.schema || L.schema.status !== 'OK') out[K] = st('UNKNOWN', 'schema read ' + ((L.schema || {}).why || 'unavailable'));
      else if (tr && tr.status === 'APPLIED') out[K] = st('PASS', tr.version + ' applied ' + hm(tr.applied_at), 'schema_migrations via /api/command/release');
      else out[K] = st('FAIL', 'migration ' + rb.number + ' is not applied in production', 'schema_migrations via /api/command/release');
    } else if (rb.kind === 'release') {
      var rc = L.receipts || {};
      out[K] = rc.status === 'OK'
        ? st('PASS', 'production serves ' + (rc.items || []).length + ' verified-on-read receipt(s)', 'GET /api/command/release at ' + hm(live.meta.at))
        : st('FAIL', 'production serves no receipts (' + (rc.why || rc.status) + ')');
    } else if (rb.kind === 'endpoint') {
      var r = readbacks[rb.path];
      if (!r) out[K] = st('UNKNOWN', rb.path + ' not read');
      else if (r.code === 200 && out.DEPLOYED.s === 'PASS') out[K] = st('PASS', rb.path + ' answered 200 in ' + r.ms + ' ms', 'read ' + hm(r.at));
      else if (r.code === 200) out[K] = st('PARTIAL', rb.path + ' answers 200, but production is not established as running ' + short(doc && doc.sha), 'read ' + hm(r.at));
      else if (r.code === 401 || r.code === 403) out[K] = st('UNKNOWN', rb.path + ' answered ' + r.code + ' (no session)');
      else out[K] = st('FAIL', rb.path + ' answered ' + (r.code || 'nothing') + (r.why ? ' — ' + r.why : ''), 'read ' + hm(r.at));
    } else out[K] = st('UNKNOWN', 'no readback mapped');

    out['FORWARD-VALIDATED'] = st('UNKNOWN', 'no forward-validation evidence source exists for this requirement yet');

    var first = null;
    STAGES.forEach(function (k) { if (!first && out[k].s !== 'PASS') first = k; });
    out.blocker = first ? {stage: first, why: out[first].why, s: out[first].s} : null;
    return out;
  }

  function verdictOf(R) {
    if (!R) return {yes: false, word: 'NO', head: 'No release receipt is available, so acceptance is UNKNOWN.', tone: 'bad'};
    var d = R.doc;
    if (R.integrity === 'TAMPERED') return {yes: false, word: 'NO', head: 'Receipt ' + d.gate_id + ' does not verify (hash mismatch).', tone: 'bad'};
    if (d.acceptance === 'ACCEPTED' && R.integrity === 'VERIFIED') {
      return {yes: true, word: 'YES', head: d.candidate + ' ' + short(d.sha) + ' is ACCEPTED: gated and signed off by ' + ((d.approver || {}).name || '?') + '.', tone: 'good'};
    }
    var openB = (d.blockers || []).filter(function (b) { return b.blocking && !b.waived_by; });
    var heads = {
      VOID: 'Gate ' + d.gate_id + ' on ' + d.candidate + ' ' + short(d.sha) + ' is VOID — ' + (((d.void_hits || [])[0] || {}).code || 'environment failure') + '. Nothing in it is evidence.',
      INCOMPLETE: 'Gate ' + d.gate_id + ' on ' + d.candidate + ' ' + short(d.sha) + ' has not completed.',
      INVALID: 'Gate ' + d.gate_id + ' is INVALID: the run cannot support a comparison.',
      REJECTED: 'Gate ' + d.gate_id + ' REJECTED ' + short(d.sha) + ': ' + (d.new_regressions || []).length + ' new regression(s).',
      GATED_PENDING_SIGNOFF: 'Gate ' + d.gate_id + ' passed ' + d.candidate + ' ' + short(d.sha) + ' with 0 new regressions, but it is not accepted: ' +
        openB.length + ' blocking item(s) open' + ((d.approver || {}).status === 'PENDING' ? ' and no approver' : '') + '.'
    };
    return {yes: false, word: 'NO', head: heads[d.acceptance] || String(d.acceptance), tone: d.acceptance === 'GATED_PENDING_SIGNOFF' ? 'warn' : 'bad'};
  }

  // ── rendering ─────────────────────────────────────────────────────
  var TONE = {PASS: 'good', FAIL: 'bad', VOID: 'bad', UNKNOWN: 'unk', PARTIAL: 'warn'};
  function chip(x, label) {
    return '<span class="ac-chip ' + (TONE[x.s] || 'unk') + '" title="' + esc((label ? label + ': ' : '') + x.s + ' — ' + x.why + (x.src ? ' · source: ' + x.src : '')) + '">' +
      '<i aria-hidden="true"></i>' + esc(x.s) + '</span>';
  }

  function selected() {
    for (var i = 0; i < S.receipts.length; i++) if (S.receipts[i].file === S.sel) return S.receipts[i];
    return null;
  }

  function hero(R, rows) {
    var v = verdictOf(R), d = R && R.doc;
    return '<section class="ac-hero ' + v.tone + '" aria-labelledby="ac-qh">' +
      '<div class="ac-answer"><span class="ac-qtext" id="ac-qh">Is this release acceptable?</span>' +
      '<span class="ac-word">' + v.word + '</span></div>' +
      '<div class="ac-why"><p class="ac-head">' + esc(v.head) + '</p>' +
      (d ? '<p class="ac-sub"><span class="mono">' + esc(d.candidate) + ' · ' + esc(d.sha) + '</span> · receipt ' + esc(d.gate_id) + ' · ' +
        '<span class="ac-int ' + (R.integrity === 'VERIFIED' ? 'good-t' : 'bad-t') + '">hash ' + esc(R.integrity) + '</span> · generated ' + esc(hm(d.generated_at)) + '</p>' : '') +
      '<ol class="ac-ladder" aria-label="Requirements reaching each stage">' + STAGES.map(function (k) {
        var n = rows.filter(function (c) { return c[k].s === 'PASS'; }).length;
        return '<li class="' + (n === rows.length ? 'full' : n ? 'some' : 'none') + '"><span class="k">' + esc(SHORT[k]) + '</span><span class="v">' + n + '<small>/' + rows.length + '</small></span></li>';
      }).join('') + '</ol></div></section>';
  }

  function receiptPicker() {
    var known = {};
    S.receipts.forEach(function (r) { known[r.doc.gate_id] = true; });
    var pending = (S.gates || []).filter(function (g) { return !known[g.gate_id]; });
    return '<nav class="ac-picker" aria-label="Release receipt"><span class="lbl">Receipt</span>' +
      S.receipts.slice().reverse().map(function (r) {
        var d = r.doc;
        return '<button type="button" class="ac-rcpt ' + (r.file === S.sel ? 'sel ' : '') + 'st-' + esc(d.state) + '" data-file="' + esc(r.file) + '" aria-pressed="' + (r.file === S.sel) + '" title="' + esc(r.file + ' · ' + d.acceptance) + '">' +
          '<b>' + esc(d.gate_id) + '</b><span>' + esc(d.candidate) + ' ' + esc(short(d.sha)) + '</span><em>' + esc(d.state) + '</em></button>';
      }).join('') +
      pending.map(function (g) {
        return '<span class="ac-rcpt pend" title="Listed in gates.json; no receipt has been generated for it yet"><b>' + esc(g.gate_id) + '</b><span>' + esc(g.candidate) + ' ' + esc(short(g.sha)) + '</span><em>NO RECEIPT</em></span>';
      }).join('') + '</nav>';
  }

  function prodPanel(R) {
    var live = S.live, L = live && live.ok ? live.doc : null, d = R && R.doc;
    var h = '<article class="ac-card"><h2>Production now</h2>';
    if (!L) {
      h += '<p class="ac-na"><b>UNKNOWN</b> — ' + esc((live && live.why) || 'not read') + '. Sign in on this host to read the running SHAs and the schema.</p>';
      if (d && d.deploy) {
        var a = d.deploy.api || {}, w = d.deploy.workers || {};
        h += '<p class="muted small">Recorded in receipt ' + esc(d.gate_id) + ' (evidence quoted from deploy logs, not a live read):</p><dl class="ac-dl">' +
          '<dt>API</dt><dd>' + esc(a.status || 'PENDING') + (a.sha ? ' · <span class="mono">' + esc(a.sha) + '</span> ' + esc(a.match || '') : '') + (a.at ? ' · ' + esc(hm(a.at)) : '') + '</dd>' +
          '<dt>Workers</dt><dd>' + esc(w.status || 'PENDING') + (w.sha ? ' · <span class="mono">' + esc(short(w.sha)) + '</span>' : '') + (w.status !== 'LIVE' ? ' · <span class="bad-t">live not read back</span>' : '') + '</dd>' +
          '<dt>Schema</dt><dd class="bad-t">' + esc((d.production_schema || {}).status || 'PENDING') + '</dd>' +
          '<dt>Readbacks</dt><dd class="bad-t">' + esc(Array.isArray(d.production_readbacks) ? d.production_readbacks.length + ' recorded' : 'PENDING') + '</dd></dl>';
      }
      return h + '</article>';
    }
    var al = L.alignment || {}, sc = L.schema || {};
    h += '<dl class="ac-dl">' +
      '<dt>API build</dt><dd>' + (L.api && L.api.sha ? '<span class="mono">' + esc(L.api.sha) + '</span>' : '<span class="bad-t">UNAVAILABLE — ' + esc((L.api || {}).why) + '</span>') + '</dd>' +
      '<dt>Workers boot</dt><dd>' + (L.workers && L.workers.sha ? '<span class="mono">' + esc(L.workers.sha) + '</span> · booted ' + esc(hm(L.workers.boot_at)) : '<span class="bad-t">UNAVAILABLE — ' + esc((L.workers || {}).why) + '</span>') + '</dd>' +
      '<dt>Alignment</dt><dd class="' + (al.verdict === 'ALIGNED' ? 'good-t' : 'bad-t') + '">' + esc(al.verdict || 'UNKNOWN') + (al.matched_how ? ' · ' + esc(al.matched_how) : '') + '</dd>' +
      '<dt>Schema max</dt><dd class="mono">' + esc(sc.max_version || 'UNAVAILABLE') + '</dd></dl>';
    if (sc.tracked) {
      h += '<table class="ac-mig"><caption>Migrations 216–225 in production</caption><thead><tr><th scope="col">Version</th><th scope="col">Applied (UTC)</th></tr></thead><tbody>' +
        sc.tracked.map(function (t) {
          return '<tr><td class="mono">' + esc(t.version) + '</td><td class="' + (t.status === 'APPLIED' ? 'good-t' : 'bad-t') + '">' + esc(t.status === 'APPLIED' ? hm(t.applied_at) : t.status + (t.in_this_build ? ' (in this build)' : '')) + '</td></tr>';
        }).join('') + (sc.numbers_absent && sc.numbers_absent.length ? '<tr><td colspan="2" class="muted small">No migration numbered ' + esc(sc.numbers_absent.join(', ')) + ' exists in production or in the running build.</td></tr>' : '') + '</tbody></table>';
    }
    h += '<p class="muted small">Source: GET /api/command/release · read ' + esc(hm(live.meta.at)) + ' · ' + esc(live.meta.ms) + ' ms</p>';
    if (d) {
      var m = shaMatch(L.api && L.api.sha, d.sha);
      h += '<p class="small">Receipt SHA ' + esc(short(d.sha)) + ' ' + (m && m !== 'DIFFERENT' ? '<b class="good-t">is what the API runs (' + esc(m) + ')</b>' : '<b class="bad-t">is not what the API runs</b>') + '.</p>';
    }
    return h + '</article>';
  }

  function details(title, items) {
    if (!items) return '';
    return '<details class="ac-more"><summary>' + esc(title) + '</summary><ul>' + items + '</ul></details>';
  }

  function gatePanel(R) {
    if (!R) return '<article class="ac-card"><h2>Gate evidence</h2><p class="ac-na"><b>UNKNOWN</b> — no receipt.</p></article>';
    var d = R.doc, g = d.gate || {}, c = g.counts || {}, cr = d.critical || {}, cg = d.commit_guard || {};
    var kb = d.known_baseline_failures || [], nr = d.new_regressions || [];
    var h = '<article class="ac-card ac-gate"><h2>Gate evidence <span class="ac-state st-' + esc(d.state) + '">' + esc(d.state) + '</span></h2>';
    h += '<div class="ac-nums">' + [['passed', c.passed, 'good-t'], ['failed', c.failed, c.failed ? 'bad-t' : ''], ['skipped', c.skipped, ''], ['xfailed', c.xfailed, '']].map(function (x) {
      return '<div><span class="v ' + x[2] + '">' + num(x[1]) + '</span><span class="k">' + x[0] + '</span></div>';
    }).join('') + '</div>';
    h += '<dl class="ac-dl">' +
      '<dt>New regressions</dt><dd class="' + (g.counts && !nr.length ? 'good-t' : 'bad-t') + '">' + (g.counts ? nr.length + (nr.length ? ': ' + esc(nr.join(', ')) : '') : 'UNKNOWN — no completed report') + '</dd>' +
      '<dt>Baseline failures</dt><dd>' + num(kb.length) + ' known · ' + num(kb.filter(function (k) { return k.identical_in_every_baseline; }).length) + ' with the identical last error in every baseline</dd>' +
      '<dt>Critical proofs</dt><dd>' + (cr.expanded_nodes != null ? num(cr.passed) + ' / ' + num(cr.expanded_nodes) + ' passed · ' + (cr.not_run || []).length + ' not run · ' + (cr.not_passed || []).length + ' not passed' : '<span class="bad-t">UNKNOWN — no completed report</span>') + '</dd>' +
      '<dt>Void conditions</dt><dd class="' + ((d.void_hits || []).length ? 'bad-t' : 'good-t') + '">' + ((d.void_hits || []).length ? esc(d.void_hits.map(function (v) { return v.code + ' in ' + v.artifact + ' (' + v.occurrences + '×)'; }).join('; ')) : 'none tripped') + '</dd>' +
      '<dt>Commit guard</dt><dd class="' + (cg.status === 'PASS' ? 'good-t' : 'bad-t') + '">' + esc(cg.status || 'UNKNOWN') + (cg.undeclared ? ' · ' + cg.undeclared.length + ' of ' + cg.commits + ' commits declare no deploy intent' : '') + '</dd>' +
      '<dt>New migrations</dt><dd class="mono">' + esc((d.migrations || {}).new_vs_base ? (d.migrations.new_vs_base.join(', ') || 'none') : 'UNKNOWN') + '</dd>' +
      '<dt>Approver</dt><dd class="' + ((d.approver || {}).name ? 'good-t' : 'bad-t') + '">' + esc((d.approver || {}).name || 'PENDING — ' + ((d.approver || {}).why || 'none recorded')) + '</dd>' +
      '<dt>Baselines</dt><dd>' + (d.baselines || []).map(function (b) { return '<span class="mono">' + esc(short(b.sha)) + '</span> <span class="muted small">' + esc(b.report_path) + '</span>'; }).join('<br>') + '</dd>' +
      '</dl>';
    var blk = d.blockers || [];
    if (blk.length) {
      h += '<ul class="ac-blockers">' + blk.map(function (b) {
        return '<li class="' + (b.blocking && !b.waived_by ? 'hard' : 'soft') + '"><b>' + esc(b.code) + '</b> <em>' + (b.blocking ? (b.waived_by ? 'waived' : 'blocking') : 'not blocking') + '</em>' + (b.detail ? ' — ' + esc(b.detail) : '') + '</li>';
      }).join('') + '</ul>';
    }
    h += details('Known baseline failures (' + kb.length + ')', kb.map(function (k) {
      return '<li><span class="mono">' + esc(k.node) + '</span><br><span class="muted small">' + k.baselines.map(function (b) {
        return 'fails in baseline ' + esc(short(b.baseline_sha)) + (b.signature_identical ? ' with the identical last error' : ' with a DIFFERENT last error') + ' · ' + esc(b.report_path);
      }).join(' · ') + '</span></li>';
    }).join(''));
    if (cg.undeclared && cg.undeclared.length) {
      h += details('Commits without deploy intent (' + cg.undeclared.length + ')', cg.undeclared.map(function (u) {
        return '<li><span class="mono">' + esc(short(u.sha)) + '</span> ' + esc(u.subject) + '</li>';
      }).join(''));
    }
    var ci = Array.isArray(d.github_ci) ? d.github_ci : [];
    h += details('GitHub CI on this SHA (' + (ci.length || 'not recorded') + ')', ci.map(function (r) {
      return '<li><b>' + esc(r.workflow) + '</b> <span class="' + (r.conclusion === 'success' ? 'good-t' : 'bad-t') + '">' + esc(r.conclusion) + '</span> · <a href="' + esc(r.url) + '" rel="noopener">run ' + esc(r.run_id) + '</a><br><span class="small">' + esc(r.summary) + '</span>' +
        (r.failing_ids_readable === false ? '<br><span class="small bad-t">failing ids unreadable: ' + esc(r.why_unreadable) + '</span>' : '') +
        (r.why_not_comparable ? '<br><span class="small muted">not comparable to the gate: ' + esc(r.why_not_comparable) + '</span>' : '') + '</li>';
    }).join(''));
    h += '<p class="muted small mono rsha" title="sha256 of the receipt\'s canonical JSON">receipt ' + esc(d.receipt_sha256) + '</p>';
    return h + '</article>';
  }

  function rowMatches(row, c) {
    if (S.ui.group && row.group !== S.ui.group) return false;
    if (S.ui.blocked && !c.blocker) return false;
    if (S.ui.q) {
      var hay = [row.requirement, row.owner, row.group, row.id].concat(row.tests, row.code, row.api, row.migrations).join(' ').toLowerCase();
      if (hay.indexOf(S.ui.q) < 0) return false;
    }
    return true;
  }

  function artifacts(row, R) {
    var tf = (R && R.doc && R.doc.test_files) || {};
    return '<div class="ac-art">' +
      '<div><span class="k">Code</span>' + row.code.map(function (p) { return '<span class="mono">' + esc(p) + '</span>'; }).join('') + '</div>' +
      '<div><span class="k">Tests at ' + esc(short(R && R.doc && R.doc.sha)) + '</span>' + (row.tests.length ? row.tests.map(function (t) {
        var c = tf[t];
        return '<span class="mono">' + esc(t) + ' <b class="' + (!c || c.failed ? 'bad-t' : 'good-t') + '">' + (c ? c.passed + ' passed · ' + c.failed + ' failed · ' + c.skipped + ' skipped' : 'not in this receipt') + '</b></span>';
      }).join('') : '<span class="muted">none mapped</span>') + '</div>' +
      '<div><span class="k">Migrations</span>' + (row.migrations.length ? row.migrations.map(function (m) { return '<span class="mono">' + esc(m) + '</span>'; }).join('') : '<span class="muted">none</span>') + '</div>' +
      '<div><span class="k">API</span>' + row.api.map(function (p) { return '<span class="mono">GET ' + esc(p) + '</span>'; }).join('') + '</div>' +
      '<div><span class="k">Worker</span><span>' + esc(row.worker || 'none') + '</span></div>' +
      '<div><span class="k">Frontend</span>' + row.frontend.map(function (p) { return '<span class="mono">' + esc(p) + '</span>'; }).join('') + '</div>' +
      '</div>';
  }

  function matrix(R, computed) {
    var rows = S.matrix.rows, vis = [];
    rows.forEach(function (row, i) { if (rowMatches(row, computed[i])) vis.push(i); });
    var head = '<tr><th scope="col" class="req">Requirement</th>' + STAGES.map(function (k) { return '<th scope="col">' + esc(SHORT[k]) + '</th>'; }).join('') + '<th scope="col">Receipt</th><th scope="col" class="blkh">First blocker</th></tr>';
    var body = vis.map(function (i) {
      var row = rows[i], c = computed[i], d = R && R.doc;
      return '<tr class="' + (c.blocker ? 'has-blk' : 'clear') + '">' +
        '<th scope="row" class="req"><details><summary><span class="g">' + esc(row.group) + '</span><span class="t">' + esc(row.requirement) + '</span><span class="o">' + esc(row.owner) + '</span></summary>' + artifacts(row, R) + '</details></th>' +
        STAGES.map(function (k) { return '<td data-k="' + esc(SHORT[k]) + '">' + chip(c[k], k) + '</td>'; }).join('') +
        '<td data-k="Receipt" class="mono small rc">' + (d ? esc(d.gate_id) + ' · ' + esc(short(d.sha)) : 'UNKNOWN') + '</td>' +
        '<td data-k="Blocker" class="blk">' + (c.blocker ? '<span class="bstage">' + esc(SHORT[c.blocker.stage]) + '</span> ' + esc(c.blocker.why) : '<span class="good-t">none</span>') + '</td></tr>';
    }).join('');
    return '<p class="ac-count" role="status">' + vis.length + ' of ' + rows.length + ' requirements · every state computed from receipt ' + esc(R ? R.doc.gate_id : '—') + ', the release read and the readback endpoints</p>' +
      '<div class="ac-scroll" role="region" aria-label="Requirements matrix" tabindex="0"><table class="ac-matrix"><thead>' + head + '</thead><tbody>' +
      (body || '<tr><td colspan="9" class="muted">No requirement matches these filters.</td></tr>') + '</tbody></table></div>';
  }

  function filters() {
    var groups = [];
    S.matrix.rows.forEach(function (r) { if (groups.indexOf(r.group) < 0) groups.push(r.group); });
    return '<form class="ac-filters" role="search" onsubmit="return false">' +
      '<div class="ac-groups" role="group" aria-label="Group">' + [''].concat(groups).map(function (g) {
        return '<button type="button" class="ac-g' + (S.ui.group === g ? ' sel' : '') + '" data-g="' + esc(g) + '" aria-pressed="' + (S.ui.group === g) + '">' + esc(g || 'All') + '</button>';
      }).join('') + '</div>' +
      '<label class="srch"><span class="sr">Search requirements</span><input type="search" id="ac-search" value="' + esc(S.ui.q) + '" placeholder="Search requirement, test, endpoint…" autocomplete="off"></label>' +
      '<label class="chk"><input type="checkbox" id="ac-blocked"' + (S.ui.blocked ? ' checked' : '') + '> Blocked only</label>' +
      '<button type="button" class="ac-reread" id="ac-reread">Re-read evidence</button></form>';
  }

  function computeAll() {
    var R = selected();
    return {R: R, rows: S.matrix.rows.map(function (row) { return computeRow(row, R, S.live, S.readbacks); })};
  }

  function render() {
    var el = document.getElementById('ac-root'), c = computeAll();
    el.innerHTML = (S.staleCopy.length ? '<p class="ac-warnbar" role="status">The API serves receipt(s) this page\'s static copy lacks: ' + esc(S.staleCopy.join(', ')) + '. The matrix uses only the copies it could verify.</p>' : '') +
      hero(c.R, c.rows) + receiptPicker() +
      '<section class="ac-cards">' + gatePanel(c.R) + prodPanel(c.R) + '</section>' +
      '<section class="ac-mx" aria-labelledby="ac-mxh"><h2 id="ac-mxh">Requirements</h2>' + filters() + '<div id="ac-table">' + matrix(c.R, c.rows) + '</div></section>';
    wire(el);
    var pill = document.getElementById('ac-pill'), v = verdictOf(c.R);
    if (pill) { pill.textContent = v.yes ? 'ACCEPTABLE' : 'NOT ACCEPTABLE'; pill.className = 'pill ' + (v.yes ? 'pill-good' : 'pill-bad'); }
    var a = document.getElementById('ac-asof');
    if (a) a.textContent = 'Evidence read ' + hm(S.readAt) + ' · release API ' + (S.live && S.live.ok ? 'read' : 'UNAVAILABLE (' + ((S.live && S.live.why) || '') + ')') + ' · ' + S.receipts.length + ' receipt(s) verified in this browser';
  }

  function rerenderTable() {
    var c = computeAll(), t = document.getElementById('ac-table');
    if (t) t.innerHTML = matrix(c.R, c.rows);
    document.querySelectorAll('.ac-g').forEach(function (b) {
      var on = b.getAttribute('data-g') === S.ui.group;
      b.classList.toggle('sel', on); b.setAttribute('aria-pressed', String(on));
    });
  }

  function wire(el) {
    el.querySelectorAll('.ac-rcpt[data-file]').forEach(function (b) {
      b.addEventListener('click', function () { S.sel = b.getAttribute('data-file'); render(); });
    });
    el.querySelectorAll('.ac-g').forEach(function (b) {
      b.addEventListener('click', function () { S.ui.group = b.getAttribute('data-g'); rerenderTable(); });
    });
    var q = el.querySelector('#ac-search');
    if (q) q.addEventListener('input', function () { S.ui.q = q.value.trim().toLowerCase(); rerenderTable(); });
    var bk = el.querySelector('#ac-blocked');
    if (bk) bk.addEventListener('change', function () { S.ui.blocked = bk.checked; rerenderTable(); });
    var rr = el.querySelector('#ac-reread');
    if (rr) rr.addEventListener('click', function () { rr.disabled = true; rr.textContent = 'Reading…'; load(); });
  }

  function fail(why) {
    var el = document.getElementById('ac-root');
    el.innerHTML = '<div class="ac-fail" role="alert"><b>UNKNOWN</b> — ' + esc(why) + '. Without its design matrix and receipts this page cannot compute acceptance, so it answers nothing rather than a guess.</div>';
    var p = document.getElementById('ac-pill'); if (p) { p.textContent = 'UNKNOWN'; p.className = 'pill pill-bad'; }
  }

  // ── loading ───────────────────────────────────────────────────────
  function readbacks() {
    S.readbacks = {};
    if (!(S.live && S.live.ok)) return Promise.resolve();
    var paths = [];
    S.matrix.rows.forEach(function (r) {
      if (r.readback && r.readback.kind === 'endpoint' && paths.indexOf(r.readback.path) < 0) paths.push(r.readback.path);
    });
    var i = 0;
    function next() {
      if (i >= paths.length) return Promise.resolve();
      var p = paths[i++];
      return getJson(p, 8000).then(function (x) {
        S.readbacks[p] = {code: x.meta.code, at: x.meta.at, ms: x.meta.ms, why: x.ok ? null : x.why};
        return next();
      });
    }
    return Promise.all([next(), next(), next(), next()]).then(render);
  }

  function load() {
    S.readAt = nowIso();
    Promise.all([getJson(SRC.matrix), getJson(SRC.index), getJson(SRC.gates), getJson(SRC.release, 12000)]).then(function (got) {
      if (!got[0].ok || !got[0].doc || !Array.isArray(got[0].doc.rows)) throw new Error(got[0].why || 'the requirements matrix has no rows');
      if (!got[1].ok) throw new Error(got[1].why);
      S.matrix = got[0].doc;
      S.gates = got[2].ok ? (got[2].doc.gates || []) : [];
      S.live = got[3].ok ? got[3] : {ok: false, meta: got[3].meta,
        why: got[3].meta.code === 401 ? 'GET /api/command/release answered 401 — no command session' : got[3].why};
      return Promise.all((got[1].doc.receipts || []).map(function (r) {
        return getJson(SRC.receiptDir + encodeURIComponent(r.file)).then(function (x) {
          if (!x.ok) return {file: r.file, doc: null, why: x.why};
          return verifyReceipt(x.doc).then(function (integ) { return {file: r.file, doc: x.doc, integrity: integ}; });
        });
      }));
    }).then(function (rs) {
      S.receipts = rs.filter(function (r) { return r.doc; });
      S.receipts.sort(function (a, b) { return String(a.doc.generated_at).localeCompare(String(b.doc.generated_at)); });
      var L = S.live && S.live.ok ? S.live.doc : null;
      S.staleCopy = L && L.receipts && L.receipts.items ? L.receipts.items.filter(function (it) {
        return !S.receipts.some(function (r) { return r.file === it.file; });
      }).map(function (it) { return it.file; }) : [];
      if (!S.sel || !S.receipts.some(function (r) { return r.file === S.sel; })) {
        S.sel = S.receipts.length ? S.receipts[S.receipts.length - 1].file : null;
      }
      render();
      return readbacks();
    }).catch(function (e) { fail((e && e.message) || 'read failed'); });
  }

  root.BTAcceptance = {stages: STAGES, canonical: canonical, computeRow: computeRow, verdictOf: verdictOf, shaMatch: shaMatch};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load); else load();
})(window);
