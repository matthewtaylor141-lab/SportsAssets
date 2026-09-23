/* BETTOR COMMAND CENTRE · five views over one evidence read.
 *
 * THE RENDERER'S ONE RULE: a figure is displayed WITH its source or it
 * is not displayed. Every number on this page arrives as a cell --
 * {value, source, as_of, status, note} -- and `cellHtml` refuses to
 * print a value whose source is missing. That refusal is what makes
 * "reconcile the screen against the record" a mechanical exercise
 * rather than an act of memory.
 *
 * AND UNKNOWN IS NOT ZERO. A cell with status UNKNOWN renders the word
 * UNKNOWN and its reason. It never renders 0, never renders a dash that
 * could be mistaken for one, and is never charted.
 *
 * Transport is same-origin and narrow: no credential is read, no host
 * but this one is reachable, and nothing is cached.
 */
(function (root) {
  'use strict';

  var API = '/api/command/center/snapshot';
  var state = { data: null, error: null, at: null, view: 'management' };

  /* ── escaping ─────────────────────────────────────────────────── */
  function esc(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
      });
  }

  /* ── time ─────────────────────────────────────────────────────── */
  function clock(iso) {
    if (!iso) return '—';
    var t = Date.parse(iso);
    if (!Number.isFinite(t)) return esc(iso);
    return new Date(t).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
  }
  function ago(iso) {
    var t = Date.parse(iso);
    if (!Number.isFinite(t)) return '';
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return Math.floor(s) + 's ago';
    if (s < 5400) return Math.floor(s / 60) + 'm ago';
    return Math.floor(s / 3600) + 'h ago';
  }

  /* ── the cell ─────────────────────────────────────────────────── */
  function isCell(x) {
    return x && typeof x === 'object' && 'value' in x && 'status' in x
      && 'source' in x;
  }

  function cellValue(c) {
    var v = c.value;
    if (c.status === 'UNKNOWN') return '<em class="cc-unknown">UNKNOWN</em>';
    if (c.status === 'NOT_APPLICABLE') return '<em class="cc-na">N/A</em>';
    if (v === null || v === undefined) {
      /* A null with an OK status is a gap in the read model, not a
       * zero. Say so rather than printing something tidy. */
      return '<em class="cc-unknown">NO VALUE</em>';
    }
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return esc(String(v));
    if (Array.isArray(v)) {
      if (!v.length) return '<em class="cc-unknown">EMPTY</em>';
      if (v.every(function (x) { return typeof x !== 'object'; })) {
        return esc(v.join(', '));
      }
      return '<pre class="cc-json">' + esc(JSON.stringify(v, null, 1))
        + '</pre>';
    }
    if (typeof v === 'object') {
      return '<pre class="cc-json">' + esc(JSON.stringify(v, null, 1))
        + '</pre>';
    }
    return esc(v);
  }

  function cellHtml(label, c) {
    if (!isCell(c)) {
      return '<div class="cc-cell cc-broken"><dt>' + esc(label)
        + '</dt><dd><em class="cc-unknown">NOT A SOURCED CELL</em></dd></div>';
    }
    if (!c.source) {
      /* Belt and braces: the server refuses to build one, and the
       * renderer refuses to print one. */
      return '<div class="cc-cell cc-broken"><dt>' + esc(label)
        + '</dt><dd><em class="cc-unknown">WITHHELD — NO SOURCE</em>'
        + '</dd></div>';
    }
    var meta = '<span class="cc-src" title="source">' + esc(c.source)
      + '</span>';
    /* EVERY FIGURE CARRIES AN "AS OF", and a blank one is not an
     * answer. A cell computed from the journal has no stamp of its own
     * -- its currency is the currency of the read that produced it --
     * so the read's instant is shown instead, marked as the read
     * rather than dressed up as a per-figure measurement. A reader
     * must never have to guess which of the two they are looking at. */
    if (c.as_of) {
      meta += '<span class="cc-asof">as of ' + clock(c.as_of) + '</span>';
    } else if (payloadAsOf()) {
      /* The EVIDENCE's instant, not the browser's fetch. */
      meta += '<span class="cc-asof cc-asof-read">as of this read, '
        + clock(payloadAsOf()) + '</span>';
    } else {
      meta += '<span class="cc-asof cc-unknown">as of UNKNOWN</span>';
    }
    var note = c.note
      ? '<p class="cc-note">' + esc(c.note) + '</p>' : '';
    return '<div class="cc-cell cc-' + esc((c.status || 'ok').toLowerCase())
      + '"><dt>' + esc(label) + '</dt><dd>' + cellValue(c) + '</dd>'
      + '<div class="cc-meta">' + meta + '</div>' + note + '</div>';
  }

  function cells(obj, order) {
    var keys = order || Object.keys(obj || {});
    return '<dl class="cc-cells">' + keys.map(function (k) {
      return isCell(obj[k]) ? cellHtml(label(k), obj[k]) : '';
    }).join('') + '</dl>';
  }

  function label(k) {
    return String(k).replace(/_/g, ' ');
  }

  /* ── views ────────────────────────────────────────────────────── */

  var LIFECYCLE_TONE = {
    COLLECTING: 'good', COMPLETED: 'good',
    ARMED: 'wait', SCHEDULED: 'wait', STOPPED: 'wait',
    ARMED_NO_FRAMES: 'bad', INTERRUPTED: 'bad', FAILED: 'bad'
  };

  var HEALTH_TONE = {
    CONNECTED_ACTIVE: 'good',
    /* QUIET IS NOT A FAULT. It is deliberately not red: a change-driven
     * feed says nothing about a market nobody is trading, and colouring
     * that red teaches the reader to ignore red. */
    CONNECTED_QUIET: 'good',
    NOT_STARTED: 'wait', UNKNOWN: 'wait',
    DISCONNECTED: 'bad'
  };

  function viewLive(v) {
    var lc = v.lifecycle || {};
    var h = v.health || {};
    var per = v.periods || {};
    var out = [];

    out.push('<section class="cc-panel"><h2>Run state</h2>'
      + '<div class="cc-badges">'
      + badge(lc.state, LIFECYCLE_TONE[lc.state] || 'wait')
      + badge(h.verdict, HEALTH_TONE[h.verdict] || 'wait')
      + '</div>'
      + '<p class="cc-why">' + esc(lc.why || '') + '</p>'
      + '<p class="cc-evidence">Evidence: ' + esc(lc.evidence || '—')
      + '</p>'
      + '<p class="cc-why">Connection: ' + esc(h.why || '') + '</p>'
      + '</section>');

    out.push('<section class="cc-panel"><h2>Identity</h2>'
      + cells(v.identity) + '</section>');

    out.push('<section class="cc-panel"><h2>Schedule &amp; control</h2>'
      + cells(v.schedule) + '</section>');

    out.push('<section class="cc-panel"><h2>Persisted frames</h2>'
      + cells(v.frames) + '</section>');

    /* THE TWO PERIODS, VISIBLY APART. */
    out.push('<section class="cc-panel cc-split"><h2>Early collection vs '
      + 'the measurement window</h2>'
      + '<div class="cc-two">'
      + '<div class="cc-half cc-early"><h3>Early — operational only</h3>'
      + '<p class="cc-big">' + esc((per.early || {}).frames) + '</p>'
      + '<p class="cc-sub">frames before ' + esc((per.window || {}).start)
      + '</p><p class="cc-note">' + esc((per.early || {}).why || '')
      + '</p>'
      + '<p class="cc-sub">first ' + clock((per.early || {}).first)
      + ' · last ' + clock((per.early || {}).last) + '</p></div>'
      + '<div class="cc-half cc-window"><h3>Measurement window</h3>'
      + '<p class="cc-big">' + esc((per.measurement || {}).frames) + '</p>'
      + '<p class="cc-sub">' + esc((per.window || {}).half_open)
      + ' ' + esc((per.window || {}).start) + ' → '
      + esc((per.window || {}).end) + '</p>'
      + '<p class="cc-sub">first ' + clock((per.measurement || {}).first)
      + ' · last ' + clock((per.measurement || {}).last) + '</p></div>'
      + '</div></section>');

    out.push(coverageTimeHtml(v.coverage_time));
    out.push(coverageTable('Market coverage — measurement window',
                           v.coverage));
    out.push(coverageTable('Market coverage — early period (operational)',
                           v.early_coverage,
                           {nothing_ran: ((per.early || {}).frames === 0)}));

    /* Allowance against limits. */
    var al = v.allowance || {};
    if (al.known) {
      out.push('<section class="cc-panel"><h2>Allowance against limits</h2>'
        + '<table class="cc-table"><thead><tr><th>Limit</th><th>Used</th>'
        + '<th>Cap</th><th>Left</th><th>State</th></tr></thead><tbody>'
        + ['http', 'socket_connect', 'socket_subscribe'].map(function (k) {
          var c = al[k];
          if (!isCell(c)) return '';
          if (c.status === 'UNKNOWN') {
            return '<tr><td>' + esc(label(k)) + '</td><td colspan="4">'
              + '<em class="cc-unknown">UNKNOWN</em> — ' + esc(c.note || '')
              + '</td></tr>';
          }
          var u = c.value;
          return '<tr class="' + (u.exhausted ? 'cc-row-bad' : '') + '"><td>'
            + esc(label(k)) + '</td><td>' + esc(u.used) + '</td><td>'
            + esc(u.limit) + '</td><td>' + esc(u.remaining) + '</td><td>'
            + (u.exhausted ? '<b class="cc-bad">EXHAUSTED</b>' : 'open')
            + '</td></tr>';
        }).join('') + '</tbody></table>'
        + cells(al, ['general_loop_zeroed', 'deadline_at'])
        + '</section>');
    } else {
      out.push('<section class="cc-panel"><h2>Allowance</h2>'
        + '<p class="cc-why"><em class="cc-unknown">NOT ARMED</em> — '
        + esc(al.why || '') + '</p></section>');
    }

    /* Gaps. */
    var g = v.gaps || {};
    out.push('<section class="cc-panel"><h2>Gaps — intervals nobody '
      + 'observed</h2>'
      + '<p class="cc-note">' + esc(g.note || '') + '</p>'
      + '<p class="cc-sub">' + esc(g.n_open) + ' open · '
      + esc(g.n_closed) + ' closed · ' + esc(g.unobserved_s)
      + 's unobserved in total</p>'
      + (g.n_open || g.n_closed
        ? '<table class="cc-table"><thead><tr><th>From</th><th>To</th>'
          + '<th>Why</th><th>Seconds</th></tr></thead><tbody>'
          + (g.open || []).map(function (r) {
            return '<tr class="cc-row-bad"><td>' + clock(iso(r.from))
              + '</td><td><b>STILL OPEN</b></td><td>' + esc(r.why)
              + '</td><td>—</td></tr>';
          }).join('')
          + (g.closed || []).map(function (r) {
            return '<tr><td>' + clock(iso(r.from)) + '</td><td>'
              + clock(iso(r.to)) + '</td><td>' + esc(r.why) + '</td><td>'
              + esc(r.duration_s) + '</td></tr>';
          }).join('')
          + '</tbody></table>'
        : '<p class="cc-sub">No gap has been recorded.</p>')
      + '</section>');

    /* Connection detail. */
    out.push('<section class="cc-panel"><h2>Connection detail</h2>'
      + cells(h, ['run_started', 'epochs_opened', 'last_epoch_at',
                  'last_record_at', 'last_frame_at', 'silence_s',
                  'socket_counters'])
      + '</section>');

    /* Segments. */
    var sg = v.segments || {};
    if (sg.known && sg.n) {
      out.push('<section class="cc-panel"><h2>Observable segments</h2>'
        + '<p class="cc-note">' + esc(sg.end_basis) + '</p>'
        + '<table class="cc-table"><thead><tr><th>Boot</th><th>Epoch</th>'
        + '<th>Start</th><th>End</th><th>Ladders</th><th>Markets</th>'
        + '</tr></thead><tbody>'
        + sg.segments.map(function (s) {
          return '<tr><td class="cc-mono">' + esc(s.boot_id) + '</td><td>'
            + esc(s.epoch) + '</td><td>' + clock(s.start) + '</td><td>'
            + clock(s.end) + '</td><td>' + esc(s.ladders) + '</td><td>'
            + esc(s.slugs) + '</td></tr>';
        }).join('') + '</tbody></table></section>');
    }
    return out.join('');
  }

  function coverageTimeHtml(ct) {
    /* OBSERVED TIME, AS A UNION. Two numbers that must never be one:
     * what has been observed, and the ceiling if collection runs
     * unbroken to the fixed end. The ceiling is not a forecast and is
     * labelled so on the page, not only in the payload. */
    if (!ct) return '';
    var a = (ct.achieved || {}).value || {};
    var b = (ct.attainable_at_fixed_end || {}).value || {};
    var pct = function (x) {
      return (typeof x === 'number') ? (x * 100).toFixed(2) + '%'
        : '<em class="cc-unknown">UNKNOWN</em>';
    };
    var hrs = function (x) {
      return (typeof x === 'number') ? (x / 3600).toFixed(2) + ' h' : '—';
    };
    return '<section class="cc-panel"><h2>Observed time against the '
      + 'fixed window</h2>'
      + '<div class="cc-two">'
      + '<div class="cc-half cc-window"><h3>Achieved</h3>'
      + '<p class="cc-big">' + pct(a.fraction_of_window) + '</p>'
      + '<p class="cc-sub">' + hrs(a.observed_s) + ' observed of '
      + hrs(a.window_s) + ' in the window</p>'
      + '<p class="cc-sub">' + pct(a.fraction_of_elapsed)
      + ' of the ' + hrs(a.elapsed_window_s)
      + ' of window elapsed so far</p>'
      + '<p class="cc-note">' + esc((ct.achieved || {}).note || '')
      + '</p></div>'
      + '<div class="cc-half cc-early"><h3>Attainable — a ceiling</h3>'
      + '<p class="cc-big">' + pct(b.fraction_of_window) + '</p>'
      + '<p class="cc-sub">' + hrs(b.if_unbroken_from_now_s)
      + ' if unbroken · ' + hrs(b.remaining_s) + ' still to run</p>'
      + '<p class="cc-note">'
      + esc((ct.attainable_at_fixed_end || {}).note || '') + '</p></div>'
      + '</div>'
      + '<table class="cc-table"><thead><tr><th>Interval</th>'
      + '<th>From</th><th>To</th><th>Seconds</th></tr></thead><tbody>'
      + (ct.observed_intervals || []).map(function (i) {
        return '<tr><td>observed</td><td>' + clock(i.from) + '</td><td>'
          + clock(i.to) + '</td><td>' + esc(i.seconds) + '</td></tr>';
      }).join('')
      + (ct.gaps_subtracted || []).map(function (i) {
        return '<tr><td>gap</td><td>' + clock(i.from) + '</td><td>'
          + clock(i.to) + '</td><td>−' + esc(i.seconds) + '</td></tr>';
      }).join('')
      + '</tbody></table>'
      + ((ct.gaps_outside_segments || []).length
        ? '<p class="cc-note"><b>Not subtracted twice:</b> '
          + (ct.gaps_outside_segments || []).map(function (g) {
              return clock(g.from) + ' → ' + clock(g.to) + ' ('
                + esc(g.seconds) + 's) ' + esc(g.why);
            }).join('; ') + '</p>'
        : '')
      + '<p class="cc-note">' + esc(ct.validity_is_not_activity || '')
      + '</p></section>';
  }

  function iso(t) {
    if (t === null || t === undefined) return null;
    if (typeof t === 'string') return t;
    return new Date(Number(t) * 1000).toISOString();
  }

  function coverageTable(title, cov, opts) {
    if (!cov) return '';
    /* NOTHING ARRIVED IS NOT THE SAME FAULT AS A MARKET GOING QUIET.
     *
     * The early period ran before the collector existed, so every
     * market showed SILENT in red -- twelve alarms about a process
     * that was not running. A period in which NOTHING was persisted
     * says nothing about any individual market, so it says that
     * instead of accusing each of them in turn. */
    var nothing = (cov.receiving === 0);
    var empty = nothing && (opts || {}).nothing_ran;
    return '<section class="cc-panel"><h2>' + esc(title) + '</h2>'
      + '<p class="cc-sub">' + esc(cov.receiving) + ' of '
      + esc(cov.allowlisted) + ' receiving \u00b7 ' + esc(cov.with_depth)
      + ' with depth persisted</p>'
      + (empty
        ? '<p class="cc-note">NOT COLLECTING IN THIS PERIOD. No frame was '
          + 'persisted by any market, so this is not evidence about the '
          + 'markets -- it is the absence of a collector. Each row below '
          + 'reads NOT COLLECTED rather than SILENT.</p>'
        : '')
      + '<p class="cc-note">' + esc(cov.independence_note || cov.note || '')
      + '</p>'
      + '<table class="cc-table"><thead><tr><th>Market</th>'
      + '<th>Frames</th><th>With depth</th><th>State</th></tr></thead>'
      + '<tbody>' + (cov.markets || []).map(function (m) {
        var st = !m.receiving
            ? (empty ? '<b class="cc-na">NOT COLLECTED</b>'
                     : '<b class="cc-bad">SILENT</b>')
          : !m.depth_persisted ? '<b class="cc-warn">NO DEPTH</b>'
          : 'depth persisted';
        return '<tr class="' + (m.receiving || empty ? '' : 'cc-row-bad')
          + '"><td class="cc-mono">' + esc(m.slug) + '</td><td>'
          + esc(m.frames) + '</td><td>' + esc(m.frames_with_depth)
          + '</td><td>' + st + '</td></tr>';
      }).join('') + '</tbody></table></section>';
  }

  function viewTests(v) {
    var rows = v.suites || [];
    var out = ['<section class="cc-panel"><h2>What each kind of run '
      + 'actually exercises</h2><dl class="cc-kinds">'
      + Object.keys(v.kinds || {}).map(function (k) {
        return '<dt>' + esc(k) + '</dt><dd>' + esc(v.kinds[k]) + '</dd>';
      }).join('') + '</dl><p class="cc-note">' + esc(v.note || '')
      + '</p></section>'];

    rows.forEach(function (r, i) {
      var counts = r.complete
        ? '<span class="cc-pass">' + esc(r.passed.value) + ' passed</span> · '
          + '<span class="' + (r.failed.value ? 'cc-bad' : '')
          + '">' + esc(r.failed.value) + ' failed</span> · '
          + esc(r.skipped.value) + ' skipped'
        : '<em class="cc-unknown">COUNTS WITHHELD</em> — '
          + esc(r.why_no_counts || 'run did not complete');
      out.push('<section class="cc-panel cc-suite"><h2>' + esc(r.suite)
        + '</h2>'
        + '<div class="cc-badges">' + badge(r.kind, 'kind')
        + badge(r.complete ? 'COMPLETE' : 'INCOMPLETE',
                r.complete ? 'good' : 'bad') + '</div>'
        + '<dl class="cc-kv">'
        + '<dt>tested commit</dt><dd class="cc-mono">'
        + esc(r.tested_sha || '—') + '</dd>'
        + '<dt>environment</dt><dd>' + esc(r.environment || '—') + '</dd>'
        + '<dt>completed at</dt><dd>' + (r.at ? clock(r.at)
          : '<em class="cc-unknown">UNKNOWN</em>') + '</dd>'
        + '<dt>artifact</dt><dd class="cc-mono">' + esc(r.artifact || '—')
        + '</dd>'
        + (r.superseded_by
          ? '<dt>superseded by</dt><dd><b class="cc-warn">'
            + esc(r.superseded_by) + '</b> — this evidence is kept, not '
            + 'deleted, and its replacement is named</dd>' : '')
        + '</dl>'
        + provenanceHtml(r.provenance)
        + '<p class="cc-counts">' + counts + '</p>'
        + failuresHtml(r, i)
        + '</section>');
    });
    return out.join('');
  }

  function provenanceHtml(p) {
    /* WHAT THE EVIDENCE LICENSES, ON THE PAGE.
     *
     * THREE DIFFERENT CLAIMS, AND THEY ARE NOT INTERCHANGEABLE:
     *
     *   a digest proves CONTENT INTEGRITY -- these are the bytes that
     *     were published, unaltered since;
     *   a publisher-supplied source_sha is a RECORDED CLAIM about where
     *     they came from, and nothing checked it;
     *   an ATTESTATION from the CI run -- its id, the commit it
     *     actually checked out, and its association with these bytes --
     *     is what would make the attribution verified.
     *
     * The page had these collapsed into the word "VERIFIED", which
     * asserted a chain of custody that does not exist. It now says
     * RECORDED, and names what is missing. */
    if (!p) return '';
    if (p.source !== 'evidence store (postgres)') {
      return '<p class="cc-prov cc-prov-tree"><b>NO PROVENANCE</b> — '
        + esc(p.integrity || 'read from disk') + '. Declared commit '
        + '<code>' + esc(p.declared_sha) + '</code>, unchecked.</p>';
    }
    var attested = p.provenance_class === 'ATTESTED';
    var mismatch = (p.sha_matches_declared === false)
      ? '<br><span class="cc-warn">The recorded commit DISAGREES with the '
        + 'declared <code>' + esc(p.declared_sha) + '</code>. Both are '
        + 'shown; neither is verified.</span>'
      : '';
    var gap = (!attested && (p.missing_attestation || []).length)
      ? '<br><span class="cc-sub">To become ATTESTED this needs: '
        + esc((p.missing_attestation || []).join(', ')) + '.</span>'
      : '';
    return '<p class="cc-prov ' + (attested ? 'cc-prov-attested'
                                            : 'cc-prov-store') + '">'
      + '<b>' + (attested ? 'ATTESTED PROVENANCE'
                          : 'RECORDED PROVENANCE') + '</b> — '
      + 'integrity: sha256 <code>'
      + esc(String(p.digest || '').slice(0, 16)) + '</code>, published '
      + clock(p.published_at) + '.<br>'
      + 'attribution: <code>' + esc(p.source_sha) + '</code> — '
      + esc(p.attribution || '') + mismatch + gap + '</p>';
  }

  function failuresHtml(r, i) {
    if (!r.complete || !(r.failure_ids || []).length) return '';
    var detail = r.failures || [];
    return '<details class="cc-drill"><summary>'
      + esc(r.failure_ids.length) + ' failing test'
      + (r.failure_ids.length === 1 ? '' : 's') + ' — show</summary>'
      + '<table class="cc-table"><thead><tr><th>Test</th><th>Message</th>'
      + '</tr></thead><tbody>'
      + (detail.length ? detail : r.failure_ids.map(function (id) {
          return { id: id, message: '' };
        })).map(function (f) {
        return '<tr><td class="cc-mono">' + esc(f.id) + '</td><td>'
          + esc(f.message || '—') + '</td></tr>';
      }).join('') + '</tbody></table></details>';
  }

  var BUCKET_TITLE = {
    REALIZED_TRADING_PNL: 'Realized trading P&L — our own fills',
    REPLAY_PNL: 'Replay P&L — a recorded tape, simulated',
    HYPOTHETICAL_INCENTIVE_REWARD: 'Hypothetical incentive reward — a '
      + 'counterfactual clip',
    VENUE_CONFIRMED_REWARD: 'Venue-confirmed reward — money actually '
      + 'credited'
  };

  function viewEconomics(v) {
    var b = v.buckets || {};
    var out = ['<section class="cc-panel cc-warnbox"><h2>Four buckets, '
      + 'and they never sum</h2><p>Realized trading P&amp;L, replay '
      + 'P&amp;L, a hypothetical reward share and a venue-confirmed '
      + 'reward are four different kinds of claim. A total across them '
      + 'would be a number with no referent, so none is computed '
      + 'anywhere in this page or in the read model behind it.</p>'
      + '</section>'];

    ['REALIZED_TRADING_PNL', 'REPLAY_PNL',
     'HYPOTHETICAL_INCENTIVE_REWARD', 'VENUE_CONFIRMED_REWARD']
      .forEach(function (k) {
        var x = b[k];
        if (!x) return;
        out.push('<section class="cc-panel cc-bucket"><h2>'
          + esc(BUCKET_TITLE[k] || k) + '</h2>'
          + cellHtml('figure', x.value)
          + '<dl class="cc-cells">'
          + cellHtml('denominator', x.denominator)
          + cellHtml('fees', x.fees)
          + cellHtml('residual inventory', x.residual_inventory)
          + cellHtml('capital committed', x.capital_committed)
          + '</dl>'
          + '<dl class="cc-kv">'
          + '<dt>policy version</dt><dd>' + (x.policy_version
            ? esc(x.policy_version)
            : '<em class="cc-unknown">UNKNOWN</em>') + '</dd>'
          + '<dt>dataset</dt><dd>' + (x.dataset ? esc(x.dataset)
            : '<em class="cc-unknown">UNKNOWN</em>') + '</dd>'
          + '<dt>execution assumptions</dt><dd>'
          + esc(x.execution_assumptions || '—') + '</dd>'
          + (x.events ? '<dt>events</dt><dd>' + cellValue(x.events)
            + ' <span class="cc-note">' + esc(x.events.note || '')
            + '</span></dd>' : '')
          + (x.provenance
            ? '<dt>provenance</dt><dd>' + (x.provenance.independent_holdout
                ? 'independent holdout'
                : '<b class="cc-warn">NOT AN INDEPENDENT HOLDOUT</b> — '
                  + esc(x.provenance.status || ''))
              + (x.provenance.why ? ' — ' + esc(x.provenance.why) : '')
              + '</dd>' : '')
          + '</dl>'
          + (x.note ? '<p class="cc-note">' + esc(x.note) + '</p>' : '')
          + '</section>');
      });
    return out.join('');
  }

  var EVIDENCE_TONE = {
    NOT_IMPLEMENTED: 'bad', IMPLEMENTED: 'wait',
    REPLAY_TESTED: 'wait', LIVE_OBSERVED: 'good',
    LIVE_EXECUTION_VALIDATED: 'good'
  };

  function viewCapabilities(rows) {
    return '<section class="cc-panel cc-warnbox"><h2>Observation does '
      + 'not validate trading performance</h2><p>LIVE_OBSERVED is a rung '
      + 'below LIVE_EXECUTION_VALIDATED, and nothing reaches the top rung '
      + 'without our own fills. Watching a book is not trading it.</p>'
      + '</section>'
      + (rows || []).map(function (c) {
        return '<section class="cc-panel cc-cap"><h2>'
          + esc(c.capability) + '</h2>'
          + '<div class="cc-badges">'
          + badge(c.evidence, EVIDENCE_TONE[c.evidence] || 'wait')
          + '</div>'
          + '<dl class="cc-kv">'
          + '<dt>implementation</dt><dd class="cc-mono">'
          + esc(c.implementation) + '</dd>'
          + '<dt>strongest evidence</dt><dd>' + esc(c.strongest_evidence)
          + '</dd>'
          + '<dt>case-study mechanism</dt><dd>'
          + (c.case_study ? esc(c.case_study)
            : '<em class="cc-unknown">NONE — this is a newly introduced '
              + 'hypothesis, not a case-study-derived decision</em>')
          + '</dd>'
          + '<dt>venue difference</dt><dd>'
          + (c.venue_difference ? esc(c.venue_difference)
            : '<span class="cc-sub">none recorded</span>') + '</dd>'
          + '</dl>'
          + (c.note ? '<p class="cc-note">' + esc(c.note) + '</p>' : '')
          + '</section>';
      }).join('');
  }

  function viewManagement(m) {
    var sh = m.system_health || {};
    var ec = m.economic_qualification || {};
    var le = m.latest_verified_evidence || {};
    return '<section class="cc-panel cc-mgmt"><h2>' + esc(sh.heading || '')
      + '</h2>'
      + cells(sh, ['state', 'connection', 'markets_with_depth',
                   'measurement_frames'])
      + '<p class="cc-caveat">' + esc(sh.caveat || '') + '</p></section>'

      + '<section class="cc-panel cc-mgmt"><h2>' + esc(ec.heading || '')
      + '</h2>'
      + '<dl class="cc-cells">'
      + cellHtml('qualification status', ec.status)
      + cellHtml('realized trading P&L', ec.realized_trading_pnl)
      + cellHtml('venue-confirmed reward', ec.venue_confirmed_reward)
      + '</dl>'
      + '<p class="cc-caveat">' + esc(ec.caveat || '') + '</p></section>'

      + '<section class="cc-panel"><h2>Latest verified evidence</h2>'
      + (le.suite
        ? '<dl class="cc-kv"><dt>suite</dt><dd>' + esc(le.suite) + '</dd>'
          + '<dt>kind</dt><dd>' + esc(le.kind) + '</dd>'
          + '<dt>tested commit</dt><dd class="cc-mono">'
          + esc(le.tested_sha) + '</dd>'
          + '<dt>environment</dt><dd>' + esc(le.environment) + '</dd>'
          + '<dt>at</dt><dd>' + clock(le.at) + '</dd>'
          + '<dt>passed</dt><dd>' + cellValue(le.passed || {}) + '</dd>'
          + '<dt>failed</dt><dd>' + cellValue(le.failed || {}) + '</dd>'
          + '</dl>'
        : '<p class="cc-why"><em class="cc-unknown">NONE</em> — '
          + esc(le.why || '') + '</p>')
      + '</section>'

      + '<section class="cc-panel"><h2>Material blockers</h2>'
      + ((m.blockers || []).length
        ? (m.blockers || []).map(function (b) {
            return '<div class="cc-blocker"><h3>' + esc(b.blocker)
              + '</h3><p>' + esc(b.consequence) + '</p>'
              + '<p class="cc-sub">Resolvable by: ' + esc(b.resolvable_by)
              + '</p><p class="cc-src">' + esc(b.source) + '</p></div>';
          }).join('')
        : '<p class="cc-sub">None recorded.</p>')
      + '</section>'

      + '<section class="cc-panel cc-next"><h2>Next action</h2>'
      + '<p class="cc-big-text">' + esc((m.next_action || {}).action)
      + '</p><p class="cc-sub">' + esc((m.next_action || {}).why)
      + '</p><p class="cc-sub">at ' + clock((m.next_action || {}).at)
      + '</p></section>';
  }

  function badge(txt, tone) {
    if (!txt) return '';
    return '<span class="cc-badge cc-' + esc(tone) + '">' + esc(txt)
      + '</span>';
  }

  /* ── shell ────────────────────────────────────────────────────── */

  var TABS = [
    ['management', 'Overview'],
    ['live', 'Live operation'],
    ['tests', 'Test &amp; release'],
    ['economics', 'Economics'],
    ['capabilities', 'Capabilities']
  ];

  function render() {
    var el = document.getElementById('cc-root');
    if (!el) return;

    /* THE PAGE SAYS WHICH VIEW IT IS SHOWING, in the DOM, and it says
     * it on EVERY path -- including the unavailable one, which returns
     * early and would otherwise never stamp it.
     *
     * A screenshot tool that clicks a tab and then waits a fixed
     * number of milliseconds is racing the render: under load it
     * scrapes the PREVIOUS view and files it under the new one's name,
     * and a reconciliation run against that capture checks the wrong
     * page and passes. That happened. Anything automated waits for
     * this attribute instead of for a timer.
     *
     * Setting it here rather than after the write is not a second
     * race: rendering is synchronous, so nothing outside can observe
     * the attribute until the body beside it is already in place. */
    el.setAttribute('data-current-view', state.view);

    if (state.error) {
      el.innerHTML = header()
        + '<section class="cc-panel cc-unavailable">'
        + '<h2>EVIDENCE UNAVAILABLE</h2>'
        + '<p>' + esc(state.error.reason || 'read failed') + '</p>'
        + '<p class="cc-sub">' + esc(state.error.detail || '') + '</p>'
        + '<p class="cc-note">The read did not produce the real records, '
        + 'so nothing is shown. A page of zeros during an outage is the '
        + 'one failure that would actually mislead somebody.</p>'
        + '</section>';
      return;
    }
    if (!state.data) {
      el.innerHTML = header() + '<section class="cc-panel">'
        + '<p>Reading evidence…</p></section>';
      return;
    }
    var v = state.data.views || {};
    var body =
      state.view === 'live' ? viewLive(v.live_operation || {})
      : state.view === 'tests' ? viewTests(v.test_evidence || {})
      : state.view === 'economics' ? viewEconomics(v.economics || {})
      : state.view === 'capabilities' ? viewCapabilities(v.capabilities)
      : viewManagement(v.management || {});
    el.innerHTML = header() + '<main class="cc-body">' + body + '</main>'
      + footer();
    Array.prototype.forEach.call(
      el.querySelectorAll('[data-view]'), function (b) {
        b.addEventListener('click', function () {
          state.view = b.getAttribute('data-view');
          render();
        });
      });
    var r = el.querySelector('[data-refresh]');
    if (r) r.addEventListener('click', function () { load(); });
  }

  /* The banner already names the class and the instant, and the
   * warning it carries opens by naming them again. Drop that opening
   * clause rather than printing the same sentence twice. */
  function trimLead(w) {
    return String(w || '').replace(
      /^(ACTUAL JOURNAL RECORDS|RECONSTRUCTED FROM REAL READINGS)[^.]*\.\s*/,
      '');
  }

  function previewBanner() {
    /* SYNTHETIC FIXTURES ANNOUNCE THEMSELVES, at the top, in red.
     * A screenshot of a fixture is worthless as evidence and dangerous
     * as a claim, so the page will not render one quietly. The banner
     * is driven by the payload, which only the preview harness sets --
     * production snapshots carry no `preview` key and show nothing. */
    var p = (state.data || {}).preview;
    if (!p) return '';
    /* THREE CLASSES, THREE BANNERS. They are three different claims:
     * the rows the collector wrote; records rebuilt to match figures
     * read back from production; and invented fixtures. One banner for
     * any two of them would make a screenshot of one pass for a
     * screenshot of another, which is the whole reason the banner
     * exists. */
    if (p.ACTUAL_RECORDS) {
      return '<div class="cc-actual" role="alert">'
        + '<b>ACTUAL JOURNAL RECORDS</b> — scenario <code>'
        + esc(p.scenario) + '</code>, read back from production at '
        + clock(p.read_at) + '. ' + esc(trimLead(p.warning)) + '</div>';
    }
    if (p.REAL_READINGS) {
      return '<div class="cc-real" role="alert">'
        + '<b>RECONSTRUCTED FROM REAL READINGS — NOT THE COLLECTOR’S '
        + 'OWN ROWS</b> — scenario <code>'
        + esc(p.scenario) + '</code>, read back from production at '
        + clock(p.read_at) + '. ' + esc(trimLead(p.warning)) + '</div>';
    }
    return '<div class="cc-synthetic" role="alert">'
      + '<b>SYNTHETIC FIXTURE — NOT VENUE DATA, NOT A RUN RESULT.</b> '
      + 'scenario <code>' + esc(p.scenario) + '</code>. '
      + esc(p.why) + '</div>';
  }

  /* THE EVIDENCE'S INSTANT, NOT THE BROWSER'S.
   *
   * This printed the moment the page FETCHED, labelled "read", with an
   * age of "0s ago" beside it. For a payload whose figures were
   * measured thirteen minutes earlier that is exactly the failure this
   * page exists to prevent: a stale reading wearing a fresh timestamp.
   * The evidence's own as_of is the headline and the age is computed
   * from it; the fetch appears separately, and only when it is
   * meaningfully later, so the lag is visible rather than averaged
   * away. */
  function payloadAsOf() {
    return (state.data && state.data.as_of) || null;
  }

  function asOfBar() {
    var evidence = payloadAsOf();
    if (!evidence) {
      return state.at
        ? '<span class="cc-asof-read">fetched ' + clock(state.at)
          + ' \u00b7 evidence instant UNKNOWN</span>'
        : '';
    }
    var out = '<span>evidence as of ' + clock(evidence) + ' \u00b7 '
      + ago(evidence) + '</span>';
    var lag = state.at
      ? (Date.parse(state.at) - Date.parse(evidence)) / 1000 : 0;
    if (Number.isFinite(lag) && lag > 5) {
      out += '<span class="cc-asof-read">fetched ' + clock(state.at)
        + ', ' + Math.round(lag) + 's later</span>';
    }
    return out;
  }

  function header() {
    return previewBanner() + '<header class="cc-head">'
      + '<div class="cc-title"><h1>BETTOR Command Centre</h1>'
      + '<p class="cc-sub">Read-only. Five views over one evidence read.'
      + '</p></div>'
      + '<div class="cc-asofbar">' + asOfBar()
      + '<button data-refresh class="cc-btn">Re-read</button></div>'
      + '<nav class="cc-tabs">' + TABS.map(function (t) {
        return '<button data-view="' + t[0] + '" class="cc-tab'
          + (state.view === t[0] ? ' cc-on' : '') + '">' + t[1]
          + '</button>';
      }).join('') + '</nav></header>';
  }

  function footer() {
    var d = state.data || {};
    return '<footer class="cc-foot"><h3>What this page refuses to do</h3>'
      + '<ul>' + (d.refusals || []).map(function (r) {
        return '<li>' + esc(r) + '</li>';
      }).join('') + '</ul>'
      + '<p class="cc-sub">' + esc(d.version || '') + ' · payload read at '
      + clock(d.as_of) + '</p>'
      + (d.evidence_availability
        ? '<p class="cc-note">Evidence artifacts present: '
          + esc(JSON.stringify(d.evidence_availability.artifacts))
          + '. From the evidence store: '
          + ((d.evidence_availability.from_store || []).length
             ? esc((d.evidence_availability.from_store || []).join(', '))
             : 'none — everything below was read from disk and its '
               + 'attribution is unverified')
          + '. Store reachable: '
          + esc(String((d.evidence_availability.store || {}).reachable))
          + '. ' + esc(d.evidence_availability.why_absent) + '</p>' : '')
      + '</footer>';
  }

  /* ── transport ────────────────────────────────────────────────── */

  async function load() {
    state.error = null;
    render();
    try {
      /* Same-origin, no custom header, no credential in JavaScript, and
       * explicitly uncached: a cached body served as the current one is
       * exactly what this page exists to prevent. */
      var res = await fetch(API, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      });
      if (res.status === 401 || res.status === 403) {
        state.error = { reason: 'COMMAND UNLOCK REQUIRED',
                        detail: 'Sign in on the main Command page first.' };
      } else if (!res.ok) {
        var body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        var d = (body && body.detail) || {};
        state.error = { reason: d.reason || ('HTTP ' + res.status),
                        detail: d.detail || d.note || '' };
      } else {
        state.data = await res.json();
        state.at = new Date().toISOString();
      }
    } catch (e) {
      state.error = { reason: 'FEED UNREACHABLE', detail: String(e) };
    }
    render();
  }

  root.BTCenter = { load: load, render: render, state: state,
                    cellHtml: cellHtml, gapless: true };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})(window);
