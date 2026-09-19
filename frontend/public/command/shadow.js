/* BETTORTOKEN COMMAND · THE SHADOW ENVIRONMENT.
 *
 * Owner directive 2026-09-19 (COMMAND IS P0): a first-class top-level
 * destination reading the REAL append-only shadow ledger, live, before
 * the dataset is large.
 *
 * THE RULE THIS WHOLE FILE IS BUILT AROUND: nothing on this screen is
 * invented. There is no fixture, no placeholder series, no synthetic
 * tape and no demo path -- not behind a flag, not for an empty state.
 * Every number rendered here came out of /api/command/shadow/*, and
 * where the ledger has nothing the screen says so in words:
 *
 *   zero decisions      -> ZERO, beside LISTENING
 *   engine blocked      -> the blocker, by name
 *   source gone quiet   -> STALE, from the SOURCE timestamp
 *   metric unavailable  -> NOT IDENTIFIED, never 0, never a dash alone
 *   read failed (503)   -> FEED UNAVAILABLE, never a page of zeros
 *
 * The last two are different states and are drawn differently, because
 * "we measured nothing" and "we could not read" are different claims
 * and only one of them is about the world.
 *
 * READ-ONLY, AND STRUCTURALLY SO. This module has no order method, no
 * venue client and no credential of any kind. It fetches same-origin
 * /api/command/ paths through BTCore's narrow transport, which refuses
 * any other path; the session is an HttpOnly cookie this code cannot
 * read. There is no control on any screen below that could place,
 * cancel, size or price a real order.
 *
 * MOTION HAS MEANING. Animation here marks NEW DATA, a STATE
 * TRANSITION or SYSTEM HEALTH -- a row illuminating because it just
 * arrived, a pulse when the source clock advances. Nothing pulses to
 * look busy. A dead engine renders still, which is information.
 */
(function (root) {
  'use strict';
  const C = root.BTCore;
  const esc = v => C.esc(v);
  const NI = 'NOT IDENTIFIED';

  const TABS = [
    ['overview', 'Overview'],
    ['decisions', 'Live decisions'],
    ['positions', 'Positions'],
    ['execution', 'Execution'],
    ['pairing', 'Pairing'],
    ['comparison', 'RN1 vs BETTOR'],
    ['audit', 'Trade audit'],
    ['performance', 'Performance']
  ];

  const POLL_MS = 8000;

  const state = {
    tab: 'overview',
    data: {},          // endpoint -> payload
    error: {},         // endpoint -> {status, reason, detail}
    lastUiUpdate: null,
    seen: new Set(),   // decision ids already drawn, so "new" means new
    trade: null,       // the open audit subject
    timer: null,
    inflight: false
  };

  /* ── transport ─────────────────────────────────────────────────────
   * One helper, and it distinguishes the three outcomes that matter:
   * a payload, a named refusal (503 carrying the reason the server
   * could not read), and an auth challenge. A network failure never
   * becomes an empty payload -- that is the single most dangerous
   * bug this screen could have. */
  async function pull(path) {
    try {
      // THE PATH GOES THROUGH core.endpoint() AND THE QUERY DOES NOT.
      // That guard refuses anything outside /api/command/ and refuses a
      // '?' outright, which is exactly the property that stops this
      // page being pointed at a third-party host -- so the query string
      // is appended AFTER validation rather than the guard being
      // loosened to admit one.
      const cut = path.indexOf('?');
      const base = cut < 0 ? path : path.slice(0, cut);
      const query = cut < 0 ? '' : path.slice(cut);
      const res = await fetch(
        C.endpoint('/api/command/shadow/' + base) + query, {
          credentials: 'same-origin', cache: 'no-store',
          headers: { 'Accept': 'application/json' }
        });
      if (res.status === 401 || res.status === 403) {
        // unlock.js owns the sign-in panel and acts on its own 401. This
        // screen only reports the state; two password prompts racing
        // each other is worse than one.
        state.error[path] = { status: res.status, reason: 'LOCKED',
          detail: 'COMMAND session required' };
        return null;
      }
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        const d = body && body.detail || {};
        state.error[path] = { status: res.status,
          reason: d.reason || ('HTTP_' + res.status),
          detail: d.detail || '' };
        return null;
      }
      delete state.error[path];
      state.data[path] = body;
      return body;
    } catch (e) {
      state.error[path] = { status: 0, reason: 'NETWORK',
        detail: String(e && e.message || e).slice(0, 120) };
      return null;
    }
  }

  const NEEDS = {
    overview: ['summary', 'health', 'decisions?limit=40'],
    decisions: ['summary', 'decisions?limit=100'],
    positions: ['positions'],
    execution: ['executions', 'summary'],
    pairing: ['positions'],
    comparison: ['comparison', 'summary'],
    audit: ['decisions?limit=60'],
    performance: ['equity', 'summary']
  };

  async function refresh() {
    if (state.inflight) return;
    state.inflight = true;
    try {
      await Promise.all((NEEDS[state.tab] || []).map(pull));
      if (state.tab === 'audit' && state.trade)
        await pull('trades/' + encodeURIComponent(state.trade));
      state.lastUiUpdate = new Date().toISOString();
      paint();
    } finally { state.inflight = false; }
  }

  /* ── formatting that refuses to invent ─────────────────────────────
   * The difference between "0" and "we do not know" is the whole
   * point of this screen, so it lives in the formatters rather than
   * in each caller's discipline. */
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const n = v => isNum(v) ? C.count(v) : NI;
  const zeroOk = v => isNum(v) ? C.count(v) : NI;   // a real 0 prints 0
  const px = v => isNum(v) ? (v * 100).toFixed(1) + '¢' : NI;
  const ms = v => isNum(v) ? (v >= 1000 ? (v / 1000).toFixed(2) + ' s'
    : v.toFixed(0) + ' ms') : NI;
  const pctv = (v, d) => isNum(v) ? (v * 100).toFixed(d == null ? 1 : d) + '%'
    : NI;
  const qty = v => isNum(v) ? C.count(v) : NI;
  const str = v => (v === null || v === undefined || v === '') ? NI : esc(v);
  const stamp = iso => Number.isFinite(Date.parse(iso))
    ? new Date(iso).toISOString().slice(11, 23) : '—';
  const day = iso => Number.isFinite(Date.parse(iso))
    ? new Date(iso).toISOString().slice(0, 19).replace('T', ' ') + 'Z' : NI;

  /* ── the standing disclosure ───────────────────────────────────────
   * Rendered from the SERVER's environment block, on every tab, above
   * the fold and inside the exportable area. "Never let an
   * investor-facing screenshot make simulated performance appear to be
   * actual trading performance" is only enforceable if the label is
   * part of the page rather than part of the operator's memory. */
  function disclosure(env) {
    const lines = (env && env.disclosureLines) ||
      ['SHADOW TRADING', 'NO REAL CAPITAL',
       'COUNTERFACTUAL / SIMULATED EXECUTION'];
    return `<div class="sh-disclosure" role="note">
      <span class="sh-disc-dot"></span>
      ${lines.map(l => `<strong>${esc(l)}</strong>`).join('<span class="sh-sep">·</span>')}
      <span class="sh-disc-tail">CAPITAL AT RISK ${esc(String(env && env.capitalAtRisk != null ? env.capitalAtRisk : 0))}
      <span class="sh-sep">·</span>REAL ORDER ACTIVITY ${esc((env && env.realOrderActivity) || 'NONE')}</span>
    </div>`;
  }

  function feedProblem(path) {
    const e = state.error[path];
    if (!e) return '';
    if (e.reason === 'LOCKED')
      return panelNote('SESSION REQUIRED',
        'COMMAND is locked. Unlock to read the shadow ledger.', 'amber');
    return panelNote('FEED UNAVAILABLE',
      `${esc(e.reason)}${e.detail ? ' · ' + esc(e.detail) : ''} — the ledger `
      + 'could not be read. This is not a reading of zero.', 'red');
  }

  function panelNote(label, body, tone) {
    return `<div class="sh-note ${tone || ''}"><span class="sh-note-label">${esc(label)}</span><span>${body}</span></div>`;
  }

  function emptyState(title, body) {
    return `<div class="sh-empty"><div class="sh-empty-mark"></div>
      <h3>${esc(title)}</h3><p>${body}</p></div>`;
  }

  /* ── source freshness, from the SOURCE ─────────────────────────────
   * "Do not turn browser heartbeat into source freshness." The two
   * clocks are shown side by side and labelled, so a page happily
   * polling a dead writer reads STALE next to a ticking UI clock. */
  function freshness(s) {
    const src = s && s.lastSourceTimestamp;
    const st = s && s.sourceState || 'NO_DATA_YET';
    const tone = st === 'LIVE' ? 'green' : st === 'STALE' ? 'amber' : 'grey';
    return `<div class="sh-fresh">
      <span class="sh-chip ${tone}"><i class="sh-beat ${st === 'LIVE' ? 'on' : ''}"></i>${esc(st.replace(/_/g, ' '))}</span>
      <span class="sh-fresh-pair"><label>LAST SOURCE UPDATE</label><b>${src ? day(src) : NI}</b></span>
      <span class="sh-fresh-pair"><label>LAST UI UPDATE</label><b>${state.lastUiUpdate ? day(state.lastUiUpdate) : '—'}</b></span>
    </div>`;
  }

  /* ── OVERVIEW ──────────────────────────────────────────────────── */

  // ORDERED BY THE HIERARCHY, not by which lane happened to collect
  // first. RN1 data may arrive sooner; that is not a reason to make the
  // benchmark the visual centre of the management screen.
  const KPI = [
    ['bettorEvDecisions', 'BETTOR EV decisions', zeroOk],
    ['bettorOpportunitiesObserved', 'BETTOR opportunities observed', zeroOk],
    ['bettorNoTrades', 'BETTOR no-trades', zeroOk],
    ['rn1SignalsObserved', 'RN1 signals observed (benchmark)', zeroOk],
    ['rn1ShadowDecisions', 'RN1 shadow decisions (benchmark)', zeroOk],
    ['shadowPositions', 'Shadow positions', zeroOk],
    ['shadowCapitalEmployed', 'Shadow capital employed', zeroOk],
    ['shadowPnl', 'Shadow P&L', zeroOk],
    ['settledShadowPnl', 'Settled shadow P&L', zeroOk],
    ['unrealizedShadowPnl', 'Unrealized shadow P&L', zeroOk],
    ['pairCompletions', 'Pair completions', zeroOk],
    ['noTradeRate', 'No-trade rate', v => pctv(v, 1)],
    ['averageLatencyMs', 'Average latency', ms],
    ['averageSlippage', 'Average slippage', v => isNum(v) ? px(v) : NI],
    ['averageEntryEv', 'Average entry EV', zeroOk]
  ];

  function kpiGrid(s) {
    const b = s.bettor || {};
    const k = Object.assign({}, s.kpi || {}, {
      bettorOpportunitiesObserved: b.opportunitiesObserved,
      bettorNoTrades: b.noTrades
    });
    return `<div class="sh-kpis">${KPI.map(([key, label, fmt]) => {
      const raw = k[key];
      // A STRING SENTINEL FROM THE SERVER IS RENDERED AS ITSELF. The
      // API says NOT_IDENTIFIED where a figure does not exist; the UI
      // does not convert that into a 0 on its way to the screen.
      const shown = typeof raw === 'string' ? raw.replace(/_/g, ' ')
        : fmt(raw);
      const unknown = shown === NI || typeof raw === 'string';
      return `<div class="sh-kpi ${unknown ? 'unknown' : ''}">
        <label>${esc(label)}</label>
        <strong>${esc(shown)}</strong></div>`;
    }).join('')}</div>`;
  }

  function laneCards(s) {
    const L = s.lanes || {};
    const card = (id, title, sub) => {
      const l = L[id] || {};
      const live = l.state === 'LIVE';
      const primary = id === (s.environment && s.environment.primaryLane);
      const extra = id === 'BETTOR_EV_SHADOW' ? `
        <div class="sh-lane-counts">
          <span><label>Opportunities observed</label><b>${zeroOk(l.opportunitiesObserved)}</b></span>
          <span><label>Trades</label><b>${zeroOk(l.trades)}</b></span>
          <span><label>No-trades</label><b>${zeroOk(l.noTrades)}</b></span>
        </div>` : '';
      return `<div class="sh-lane ${live ? 'live' : 'idle'} ${primary ? 'primary' : ''}">
        ${l.role ? `<span class="sh-lane-role">${esc(l.role)}</span>` : ''}
        <div class="sh-lane-head"><h3>${esc(title)}</h3>
          <span class="sh-chip ${live ? 'green' : 'grey'}">
            <i class="sh-beat ${live ? 'on' : ''}"></i>${esc(String(l.state || 'NOT ESTABLISHED').replace(/_/g, ' '))}</span></div>
        <div class="sh-lane-n">${zeroOk(l.decisions)}<small>decisions</small></div>
        ${extra}
        <div class="sh-lane-meta">
          <span>P_BETTOR <b>${esc(String(l.pBettor || 'NOT_ESTABLISHED').replace(/_/g, ' '))}</b></span>
          <span>INFORMATION EV <b>${esc(String(l.informationEv || 'NOT_ESTABLISHED').replace(/_/g, ' '))}</b></span>
        </div>
        <p class="sh-lane-note">${esc(l.note || sub)}</p></div>`;
    };
    // THE PRIMARY PRODUCT IS FIRST, and the order comes from the
    // SERVER's laneOrder rather than from this file, so a future client
    // or a PDF inherits the same hierarchy instead of re-deciding it.
    const order = (s.environment && s.environment.laneOrder)
      || ['BETTOR_EV_SHADOW', 'RN1_SHADOW'];
    const titles = {
      BETTOR_EV_SHADOW: ['BETTOR EV Shadow',
        'What BETTOR independently decides. RN1 is excluded from this lane.'],
      RN1_SHADOW: ['RN1 Shadow',
        'What BETTOR would have done observing RN1, under BETTOR execution conditions.']
    };
    return `<div class="sh-lanes">
      ${order.map(id => card(id, titles[id] ? titles[id][0] : id,
                             titles[id] ? titles[id][1] : '')).join('')}
      <div class="sh-lane split">
        <div class="sh-lane-head"><h3>Combined P&amp;L</h3>
          <span class="sh-chip grey">WITHHELD</span></div>
        <div class="sh-lane-n">—</div>
        <p class="sh-lane-note">${esc(s.combinedPnlNote || '')}</p></div>
    </div>`;
  }

  function bettorBlockerPanel(s) {
    const b = s.bettor || {};
    if (b.state !== 'COLLECTING')
      return panelNote('BETTOR STORE',
        esc(b.why || 'BETTOR collection is not ready'), 'amber');
    const rows = b.blockers || [];
    return `<section class="sh-panel"><div class="sh-panel-head">
      <h2>Why BETTOR said no</h2>
      <span class="sh-sub">A refusal recorded prospectively is part of the dataset. These are what currently prevent the most trades.</span>
      </div>${rows.length ? `<div class="sh-blockers">${rows.map(x => `
        <div class="sh-blocker"><span class="sh-blocker-code">${esc(String(x.code || NI).replace(/_/g, ' '))}</span>
        <span class="sh-blocker-n">${zeroOk(x.count)}</span></div>`).join('')}</div>`
      : `<div class="sh-panel-body">${emptyState('No refusal recorded yet',
          'BETTOR has not yet looked at a market on this deployment.')}</div>`}
      </section>`;
  }

  function blockerPanel(s) {
    const rows = s.blockers || [];
    if (!rows.length) return '';
    return `<section class="sh-panel"><div class="sh-panel-head">
      <h2>Why the RN1 benchmark said no</h2>
      <span class="sh-sub">Every NO_TRADE is retained. A refusal is evidence, not an absence.</span>
      </div><div class="sh-blockers">${rows.map(b => `
        <div class="sh-blocker"><span class="sh-blocker-code">${esc(String(b.code || NI).replace(/_/g, ' '))}</span>
        <span class="sh-blocker-n">${zeroOk(b.count)}</span></div>`).join('')}
      </div></section>`;
  }

  const HEALTH_LABEL = {
    BETTOR_EV_ENGINE: 'BETTOR EV engine',
    BETTOR_DECISION_PIPELINE: 'BETTOR decision pipeline',
    INSTITUTIONAL_MARKET_DATA: 'Institutional market data',
    L2: 'L2 depth',
    RN1_BENCHMARK_FEED: 'RN1 benchmark feed',
    RN1_LISTENER: 'RN1 listener', SHADOW_ENGINE: 'Shadow engine',
    SHADOW_WRITER: 'Shadow writer', LABEL_MATURITY: 'Label maturity',
    DATABASE: 'Database', COMMAND_API: 'Command API'
  };

  /* A component key with no label of its own is still DRAWN, under its
   * own name. A health panel that silently omitted a component the
   * server reported would be the one shape of this screen that could
   * hide a failure -- and the server's order is the hierarchy's order,
   * so it is taken from the payload rather than re-decided here. */
  function healthKeys(comps) {
    const seen = Object.keys(comps || {});
    const known = Object.keys(HEALTH_LABEL).filter(k => seen.includes(k));
    const extra = seen.filter(k => !(k in HEALTH_LABEL));
    const missing = Object.keys(HEALTH_LABEL).filter(k => !seen.includes(k));
    return known.concat(extra, missing);
  }

  function healthLabel(key) {
    return HEALTH_LABEL[key] ||
      key.replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase());
  }

  /* The benchmark feed carries its own four fields, and one of them is
   * the answer to the question an operator actually asks when RN1 goes
   * quiet: is BETTOR affected? It is not, and the row says so rather
   * than leaving it to be inferred from two green dots. */
  /* The counters whose disagreement was the whole incident: 31
   * opportunities beside 0 decisions, with nothing on this screen
   * saying so. They are now drawn next to each other, and an orphan or
   * a recorded failure paints the row DEGRADED however healthy
   * collection looks. */
  function pipelineDetail(c) {
    const rate = (c.opportunityToDecisionSuccessRate === null ||
                  c.opportunityToDecisionSuccessRate === undefined)
      ? NI : (c.opportunityToDecisionSuccessRate * 100).toFixed(1) + '%';
    const bad = (c.orphanOpportunities || 0) || (c.decisionWriteFailures || 0);
    return `<span class="sh-feed-facts">
      <span>opportunities <b>${zeroOk(c.opportunitiesObserved)}</b></span>
      <span>decisions <b>${zeroOk(c.decisionsRecorded)}</b></span>
      <span class="${c.orphanOpportunities ? 'sh-pipe-bad' : ''}">orphans <b>${zeroOk(c.orphanOpportunities)}</b></span>
      <span class="${c.decisionWriteFailures ? 'sh-pipe-bad' : ''}">write failures <b>${zeroOk(c.decisionWriteFailures)}</b></span>
      <span>rate <b>${esc(rate)}</b></span>
    </span><span class="sh-feed-facts">
      <span>last decision <b>${c.lastSuccessfulDecision ? stamp(c.lastSuccessfulDecision) : NI}</b></span>
      <span>last failure <b>${c.lastFailure ? stamp(c.lastFailure) : NI}</b></span>
    </span>${bad ? `<span class="sh-feed-note sh-pipe-bad">${str(c.lastFailureText)}</span>` : ''}
    <span class="sh-feed-note">${str(c.detail)}</span>`;
  }

  function feedDetail(key, c) {
    if (key === 'BETTOR_DECISION_PIPELINE') return pipelineDetail(c);
    if (key !== 'RN1_BENCHMARK_FEED') return str(c.detail);
    const lag = (c.rn1FeedLagSeconds === null ||
                 c.rn1FeedLagSeconds === undefined)
      ? NI : `${c.rn1FeedLagSeconds}s`;
    return `<span class="sh-feed-facts">
      <span>last source event <b>${c.rn1FeedLastSourceEvent
        ? stamp(c.rn1FeedLastSourceEvent) : NI}</b></span>
      <span>last received <b>${c.rn1FeedLastReceivedEvent
        ? stamp(c.rn1FeedLastReceivedEvent) : NI}</b></span>
      <span>lag <b>${esc(lag)}</b></span>
      <span class="sh-feed-isolated">${c.affectsBettor === false
        ? 'BETTOR unaffected' : 'affects BETTOR'}</span>
    </span><span class="sh-feed-note">${str(c.detail)}</span>`;
  }

  function healthPanel() {
    const h = state.data['health'];
    if (!h) return feedProblem('health') ||
      `<section class="sh-panel"><div class="sh-panel-head"><h2>System health</h2></div>
       <div class="sh-panel-body">${emptyState('Reading…', 'The health payload has not arrived yet.')}</div></section>`;
    const comps = h.components || {};
    return `<section class="sh-panel"><div class="sh-panel-head">
      <h2>System health</h2>
      <span class="sh-sub">Each state comes from a source row's own timestamp. A browser heartbeat is not freshness.</span>
      </div><div class="sh-health">${healthKeys(comps).map(key => {
        const c = comps[key] || { state: 'NOT_ESTABLISHED' };
        const st = String(c.state || 'NOT_ESTABLISHED');
        const tone = st === 'LIVE' ? 'green' : st === 'DEGRADED' ? 'amber'
          : st === 'STALE' ? 'amber' : st === 'BLOCKED' ? 'red' : 'grey';
        const primary = key === 'BETTOR_EV_ENGINE' ? ' primary' : '';
        return `<div class="sh-health-row${primary}">
          <span class="sh-dot ${tone} ${st === 'LIVE' ? 'pulse' : ''}"></span>
          <span class="sh-health-name">${esc(healthLabel(key))}</span>
          <span class="sh-health-state ${tone}">${esc(st.replace(/_/g, ' '))}</span>
          <span class="sh-health-src">${c.sourceTimestamp ? stamp(c.sourceTimestamp) : NI}</span>
          <span class="sh-health-detail">${feedDetail(key, c)}</span></div>`;
      }).join('')}</div></section>`;
  }

  /* ── the live decision tape ────────────────────────────────────────
   * DRIVEN BY ACTUAL LEDGER EVENTS. Each line below is a real column
   * from a real row with its real timestamp; the stages are the ones
   * the ledger records, not a dramatization of a pipeline. When the
   * ledger is empty the tape is empty and says LISTENING -- it does
   * not scroll invented activity to look alive. */
  function tapeEvents(rows) {
    const out = [];
    rows.forEach(d => {
      if (d.venueSourceTs) out.push({ at: d.venueSourceTs, id: d.shadowDecisionId,
        kind: 'RN1 SIGNAL OBSERVED',
        note: `${esc(d.symbol || '')} ${esc(d.proposedSide || d.rn1Price != null ? '' : '')} ${d.rn1Price != null ? px(d.rn1Price) : ''}`.trim() });
      if (d.bettorReceivedTs) out.push({ at: d.bettorReceivedTs, id: d.shadowDecisionId,
        kind: 'SOURCE RECEIVED',
        note: d.sourceType ? esc(d.sourceType) : '' });
      if (d.marketStateId) out.push({ at: d.bettorReceivedTs, id: d.shadowDecisionId,
        kind: 'MARKET STATE SEALED',
        note: d.marketBid != null || d.marketAsk != null
          ? `${px(d.marketBid)} / ${px(d.marketAsk)}` : 'UNREADABLE' });
      const blocked = (d.blockers || [])[0];
      out.push({
        at: d.decisionTs, id: d.shadowDecisionId,
        kind: d.proposedAction === 'NO_TRADE' ? 'NO TRADE'
          : d.proposedAction === 'BUY' || d.proposedAction === 'SELL'
            ? 'FOLLOW RN1 · ' + d.proposedAction : d.proposedAction,
        tone: d.proposedAction === 'NO_TRADE' ? 'amber' : 'green',
        note: blocked ? esc(String(blocked.code || '').replace(/_/g, ' '))
          : `${qty(d.proposedQuantity)} @ ${px(d.proposedPrice)}`
      });
    });
    return out.filter(e => e.at).sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
      .slice(0, 80);
  }

  function tape() {
    const payload = state.data['decisions?limit=40']
      || state.data['decisions?limit=100'] || state.data['decisions?limit=60'];
    const problem = feedProblem('decisions?limit=40')
      || feedProblem('decisions?limit=100');
    const rows = payload && payload.rows || [];
    const head = `<section class="sh-panel sh-tape-panel"><div class="sh-panel-head">
      <h2>Live decision tape</h2>
      <span class="sh-sub">Ledger events, in arrival order. Nothing on this tape is generated.</span></div>`;
    if (problem) return head + `<div class="sh-panel-body">${problem}</div></section>`;
    if (!rows.length) return head + `<div class="sh-panel-body">${emptyState(
      'LISTENING — 0 decisions',
      'The prospective ledger is armed and empty. The first RN1 sighting will '
      + 'appear here the moment it is written. Nothing is shown until then.')}</div></section>`;
    const events = tapeEvents(rows);
    const html = events.map(e => {
      const key = e.id + '|' + e.kind + '|' + e.at;
      const fresh = !state.seen.has(key);
      state.seen.add(key);
      return `<button class="sh-tape-row ${fresh ? 'sh-new' : ''}" data-shadow-trade="${esc(e.id)}">
        <span class="sh-tape-clock">${stamp(e.at)}</span>
        <span class="sh-tape-kind ${e.tone || ''}">${esc(e.kind)}</span>
        <span class="sh-tape-note">${e.note || ''}</span></button>`;
    }).join('');
    if (state.seen.size > 4000) state.seen = new Set();
    return head + `<div class="sh-tape">${html}</div></section>`;
  }

  function overview() {
    const s = state.data['summary'];
    const problem = feedProblem('summary');
    if (!s) return `<div class="sh-body">${problem || emptyState('Reading the shadow ledger…', 'One moment.')}</div>`;
    return `${disclosure(s.environment)}${freshness(s)}
      ${kpiGrid(s)}
      ${laneCards(s)}
      <div class="sh-two">
        <div>${tape()}</div>
        <div>${bettorBlockerPanel(s)}${healthPanel()}${blockerPanel(s)}${policyPanel(s)}</div>
      </div>`;
  }

  function policyPanel(s) {
    const env = s.environment || {};
    return `<section class="sh-panel"><div class="sh-panel-head"><h2>Frozen policy</h2>
      <span class="sh-sub">The rule set was frozen before row one. A decision cannot be written under an unfrozen policy.</span></div>
      <div class="sh-kv">
        <div><label>Policy version</label><b>${str(env.policyVersion)}</b></div>
        <div><label>Policy SHA</label><b class="mono">${env.policySha ? esc(env.policySha.slice(0, 16)) : NI}</b></div>
        <div><label>Policies frozen</label><b>${zeroOk(s.policiesFrozen)}</b></div>
        <div><label>Shadow mode</label><b>${env.shadowMode ? 'TRUE' : 'FALSE'}</b></div>
        <div><label>Real order submission</label><b>${env.realOrderSubmissionEnabled ? 'ENABLED' : 'DISABLED'}</b></div>
        <div><label>mirror_live</label><b>${env.mirrorLive ? 'TRUE' : 'FALSE'}</b></div>
      </div></section>`;
  }

  /* ── DECISIONS ─────────────────────────────────────────────────── */

  function decisionsTab() {
    const s = state.data['summary'];
    const p = state.data['decisions?limit=100'];
    const problem = feedProblem('decisions?limit=100');
    const rows = p && p.rows || [];
    return `${disclosure(s && s.environment)}${s ? freshness(s) : ''}
      ${problem || ''}
      ${tape()}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Decision ledger</h2>
        <span class="sh-sub">Append-only. NO_TRADE rows are retained and shown, because a refusal is a decision.</span></div>
      ${rows.length ? `<div class="sh-scroll"><table class="sh-table">
        <thead><tr><th>Decision</th><th>Lane</th><th>Market</th><th>Leg</th>
        <th>Action</th><th>RN1 price</th><th>Our price</th><th>Qty</th>
        <th>Observe → decide</th><th>Reason / blocker</th><th>Decided</th></tr></thead>
        <tbody>${rows.map(d => `<tr class="sh-click" data-shadow-trade="${esc(d.shadowDecisionId)}">
          <td class="mono">${esc(d.shadowDecisionId.slice(0, 14))}</td>
          <td><span class="sh-tag ${d.lane === 'RN1_SHADOW' ? 'rn1' : 'bettor'}">${esc(d.lane.replace(/_/g, ' '))}</span></td>
          <td>${str(d.symbol)}</td><td>${str(d.outcomeLeg)}</td>
          <td><span class="sh-action ${d.proposedAction === 'NO_TRADE' ? 'no' : 'yes'}">${esc(d.proposedAction)}</span></td>
          <td>${px(d.rn1Price)}</td><td>${px(d.proposedPrice)}</td>
          <td>${qty(d.proposedQuantity)}</td>
          <td>${ms(d.decisionComputeMs)}</td>
          <td class="sh-reason">${(d.blockers || []).length
            ? `<span class="sh-blocker-inline">${esc(String(d.blockers[0].code || '').replace(/_/g, ' '))}</span>`
            : esc((d.reasonCodes || []).slice(0, 2).join(' · ').replace(/_/g, ' ')) || NI}</td>
          <td>${stamp(d.decisionTs)}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="sh-panel-body">${problem ? '' : emptyState('0 decisions',
            p ? esc(p.emptyMeans || '') : 'Not read yet.')}</div>`}
      </section>`;
  }

  /* ── POSITIONS ─────────────────────────────────────────────────── */

  function positionsTab() {
    const p = state.data['positions'];
    const problem = feedProblem('positions');
    const rows = p && p.rows || [];
    return `${disclosure(p && p.environment)}
      ${problem || ''}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Shadow blotter</h2>
        <span class="sh-sub">One row per LEG. YES and NO are different positions and are never netted.</span></div>
      ${rows.length ? `<div class="sh-scroll"><table class="sh-table">
        <thead><tr><th>Sport</th><th>Market</th><th>Leg</th><th>Lane</th>
        <th>Entry time</th><th>Entry px</th><th>Qty</th><th>Mark</th>
        <th>Entry EV</th><th>Unrealized</th><th>Realized</th><th>Pair</th>
        <th>Exit intention</th><th>Capital hours</th><th>Policy</th></tr></thead>
        <tbody>${rows.map(r => `<tr class="sh-click" data-shadow-trade="${esc(r.originatingDecisionId || '')}">
          <td>${str(r.sport)}</td><td>${str(r.symbol)}</td><td>${str(r.leg)}</td>
          <td><span class="sh-tag ${r.lane === 'RN1_SHADOW' ? 'rn1' : 'bettor'}">${esc(String(r.lane).replace(/_/g, ' '))}</span></td>
          <td>${stamp(r.entryTime)}</td><td>${px(r.entryPrice)}</td>
          <td>${qty(r.entryQuantity)}</td><td>${px(r.currentExecutableMark)}</td>
          <td>${isNum(r.entryEv) ? r.entryEv.toFixed(4) : NI}</td>
          <td>${isNum(r.unrealizedPnl) ? C.signed(r.unrealizedPnl, 2) : NI}</td>
          <td>${isNum(r.realizedPnl) ? C.signed(r.realizedPnl, 2) : NI}</td>
          <td>${str(r.pairStatus).replace(/_/g, ' ')}</td>
          <td>${str(r.exitIntention).replace(/_/g, ' ')}</td>
          <td>${isNum(r.capitalHours) ? r.capitalHours.toFixed(2) : NI}</td>
          <td class="mono">${str(r.policyVersion)}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="sh-panel-body">${problem ? '' : emptyState('No shadow position yet',
            'A position opens when a decision produces a reconstructed fill. '
            + 'Until then this blotter is empty — it is not a zero-value portfolio.')}</div>`}
      </section>`;
  }

  /* ── EXECUTION ─────────────────────────────────────────────────── */

  function executionTab() {
    const p = state.data['executions'];
    const s = state.data['summary'];
    const problem = feedProblem('executions');
    const rows = p && p.rows || [];
    return `${disclosure(p && p.environment || s && s.environment)}
      ${problem || ''}
      ${s ? classPanel(s) : ''}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Five prices, never substituted</h2>
        <span class="sh-sub">RN1's fill price is recorded beside ours and is never used as our executable price.</span></div>
      ${rows.length ? `<div class="sh-scroll"><table class="sh-table">
        <thead><tr><th>Market</th><th>Class</th><th>Status</th>
        <th>RN1 price</th><th>When observed</th><th>When decided</th>
        <th>At shadow arrival</th><th>Shadow VWAP</th>
        <th>Data lat.</th><th>Compute lat.</th><th>Exec lat.</th><th>Total</th>
        <th>Basis</th><th>Slippage</th><th>Spread cost</th></tr></thead>
        <tbody>${rows.map(e => `<tr class="sh-click" data-shadow-trade="${esc(e.shadowDecisionId)}">
          <td>${str(e.symbol)}</td>
          <td><span class="sh-class ${e.realizable ? 'real' : 'sim'}">${esc(String(e.executionClass).replace(/_/g, ' '))}</span></td>
          <td>${str(e.status)}</td>
          <td>${px(e.rn1Price)}</td><td>${px(e.priceWhenBettorObserved)}</td>
          <td>${px(e.priceWhenBettorDecided)}</td><td>${px(e.priceAtShadowArrival)}</td>
          <td>${px(e.shadowExecutionVwap)}</td>
          <td>${ms(e.dataLatencyMs)}</td><td>${ms(e.decisionComputeMs)}</td>
          <td>${ms(e.executionLatencyMs)}</td><td>${ms(e.totalToArrivalMs)}</td>
          <td>${str(e.latencyBasis)}</td>
          <td>${isNum(e.slippage) ? px(e.slippage) : NI}</td>
          <td>${isNum(e.spreadCost) ? px(e.spreadCost) : NI}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="sh-panel-body">${problem ? '' : emptyState('No reconstructed execution yet',
            'This is the screen that will answer whether RN1 is economically '
            + 'copyable. It stays empty until a decision reaches a book — no '
            + 'illustrative latency or slippage is shown in the meantime.')}</div>`}
      </section>`;
  }

  function classPanel(s) {
    const by = s.byExecutionClass || {};
    const keys = Object.keys(by);
    if (!keys.length) return '';
    return `<section class="sh-panel"><div class="sh-panel-head"><h2>Economics by execution class</h2>
      <span class="sh-sub">Simulated and observed economics are never added together. Only ACTUAL_FILL is realizable, and none exists.</span></div>
      <div class="sh-classgrid">${keys.map(k => {
        const c = by[k];
        return `<div class="sh-classcard ${c.realizable ? 'real' : 'sim'}">
          <label>${esc(k.replace(/_/g, ' '))}</label>
          <strong>${zeroOk(c.n)}<small>rows</small></strong>
          <div class="sh-kv-mini">
            <span>Avg slippage<b>${isNum(c.avgSlippage) ? px(c.avgSlippage) : NI}</b></span>
            <span>Avg spread cost<b>${isNum(c.avgSpreadCost) ? px(c.avgSpreadCost) : NI}</b></span>
            <span>Avg total latency<b>${ms(c.avgTotalLatencyMs)}</b></span>
          </div>
          <span class="sh-realflag">${c.realizable ? 'REALIZABLE' : 'COUNTERFACTUAL — NOT REALIZED P&L'}</span>
        </div>`;
      }).join('')}</div></section>`;
  }

  /* ── PAIRING ───────────────────────────────────────────────────── */

  function pairingTab() {
    const p = state.data['positions'];
    const problem = feedProblem('positions');
    const rows = (p && p.rows || []).filter(r =>
      r.pairStatus && r.pairStatus !== 'NOT_IDENTIFIED');
    return `${disclosure(p && p.environment)}
      ${problem || ''}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Pair completion</h2>
        <span class="sh-sub">A negative pair completion is not automatically an error. Loss-locks are shown as they were.</span></div>
      ${rows.length ? `<div class="sh-scroll"><table class="sh-table">
        <thead><tr><th>Market</th><th>First leg</th><th>Complement</th>
        <th>Time between legs</th><th>BETTOR pair basis</th><th>Latency drag</th>
        <th>Locked shadow P&amp;L</th><th>Capital released</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${str(r.symbol)}</td><td>${str(r.leg)}</td>
          <td>${NI}</td><td>${NI}</td><td>${NI}</td><td>${NI}</td>
          <td>${isNum(r.realizedPnl) ? C.signed(r.realizedPnl, 2) : NI}</td>
          <td>${NI}</td></tr>`).join('')}</tbody></table></div>`
        : `<div class="sh-panel-body">${problem ? '' : emptyState('No pair yet',
            'A pair is recorded when a shadow position\'s complement is '
            + 'observed and the pair EV beats holding. No pair basis, drag or '
            + 'locked result is estimated before that happens.')}</div>`}
      </section>`;
  }

  /* ── COMPARISON ────────────────────────────────────────────────── */

  const CLASS_LABEL = {
    RN1_ONLY: 'RN1 only', BETTOR_ONLY: 'BETTOR only', BOTH_AGREE: 'Both agree',
    DISAGREE_DIRECTION: 'Disagree on direction', BOTH_NO_TRADE: 'Both no-trade',
    BETTOR_NOT_YET_ELIGIBLE: 'BETTOR not yet eligible'
  };

  function comparisonTab() {
    const c = state.data['comparison'];
    const s = state.data['summary'];
    const problem = feedProblem('comparison');
    if (!c) return `<div class="sh-body">${problem || emptyState('Reading…', '')}</div>`;
    const counts = c.classes || {};
    const total = Object.keys(counts).reduce((a, k) => a + (counts[k] || 0), 0);
    return `${disclosure(c.environment)}${s ? freshness(s) : ''}
      ${s ? laneCards(s) : ''}
      ${!c.bettorEligible ? panelNote('BETTOR EV',
        'LEARNING / NOT YET ELIGIBLE — P_BETTOR NOT ESTABLISHED. '
        + 'An honest NO_TRADE is the expected output until independent EV is '
        + 'earned. No trade is invented to fill this panel.', 'amber') : ''}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Disagreement dataset</h2>
        <span class="sh-sub">One row per contemporaneous opportunity. Outcomes are filled in afterwards, in their own columns, and never edit the decisions.</span></div>
      <div class="sh-compare">${Object.keys(CLASS_LABEL).map(k => {
        const v = counts[k] || 0;
        const share = total ? v / total : null;
        return `<div class="sh-compare-row">
          <span class="sh-compare-name">${esc(CLASS_LABEL[k])}</span>
          <span class="sh-compare-bar"><i style="width:${share ? Math.max(share * 100, 1.5) : 0}%"></i></span>
          <span class="sh-compare-n">${zeroOk(v)}</span>
          <span class="sh-compare-share">${share === null ? '—' : pctv(share, 1)}</span>
        </div>`;
      }).join('')}</div>
      ${!total ? `<div class="sh-panel-body">${emptyState('No contemporaneous comparison yet',
        'A comparison row needs both lanes to have looked at the same moment. '
        + 'BETTOR EV has no eligible decision yet, so there is nothing to compare.')}</div>` : ''}
      </section>`;
  }

  /* ── TRADE AUDIT ───────────────────────────────────────────────── */

  function auditTab() {
    const p = state.data['decisions?limit=60'];
    const rows = p && p.rows || [];
    const t = state.trade && state.data['trades/' + encodeURIComponent(state.trade)];
    const problem = feedProblem('decisions?limit=60')
      || (state.trade ? feedProblem('trades/' + encodeURIComponent(state.trade)) : '');
    return `${disclosure((t && t.environment) || (p && p.environment))}
      ${problem || ''}
      <div class="sh-audit-wrap">
        <aside class="sh-audit-list"><div class="sh-panel-head"><h2>Pick a decision</h2></div>
          ${rows.length ? rows.map(d => `<button class="sh-audit-pick ${state.trade === d.shadowDecisionId ? 'on' : ''}" data-shadow-trade="${esc(d.shadowDecisionId)}">
            <span class="sh-audit-pick-top"><b>${str(d.symbol)}</b>
            <span class="sh-action ${d.proposedAction === 'NO_TRADE' ? 'no' : 'yes'}">${esc(d.proposedAction)}</span></span>
            <span class="sh-audit-pick-sub">${stamp(d.decisionTs)} · ${esc(String(d.lane).replace(/_/g, ' '))}</span>
          </button>`).join('')
            : emptyState('0 decisions', 'Nothing to replay yet.')}
        </aside>
        <div class="sh-audit-main">${t ? replay(t)
          : emptyState('Select a decision',
            'Every stage of a shadow trade is replayed here with the '
            + 'milliseconds between stages, taken from the recorded timestamps.')}</div>
      </div>`;
  }

  function replay(t) {
    const stages = t.stages || [];
    return `<section class="sh-panel"><div class="sh-panel-head">
      <h2>${str(t.symbol)} <span class="sh-tag ${t.lane === 'RN1_SHADOW' ? 'rn1' : 'bettor'}">${esc(String(t.lane).replace(/_/g, ' '))}</span></h2>
      <span class="sh-sub mono">${esc(t.shadowDecisionId)}</span></div>
      <ol class="sh-replay">${stages.map((s, i) => `
        <li class="sh-stage ${s.at ? '' : 'absent'}" style="--i:${i}">
          <span class="sh-stage-rail"><i></i></span>
          <div class="sh-stage-body">
            <div class="sh-stage-head"><strong>${esc(String(s.stage).replace(/_/g, ' '))}</strong>
              <span class="sh-stage-clock">${s.at ? stamp(s.at) : 'DID NOT HAPPEN'}</span>
              ${isNum(s.elapsedMsFromPrevious)
                ? `<span class="sh-elapsed">+${ms(s.elapsedMsFromPrevious)}</span>` : ''}</div>
            ${detailBlock(s.detail)}
          </div></li>`).join('')}</ol>
      <div class="sh-panel-body">
        <h3 class="sh-h3">Final shadow economics</h3>
        ${economics(t.finalEconomics)}
      </div></section>`;
  }

  function detailBlock(detail) {
    if (!detail || !Object.keys(detail).length) return '';
    return `<div class="sh-stage-kv">${Object.keys(detail).map(k => {
      let v = detail[k];
      if (v === null || v === undefined) v = NI;
      else if (typeof v === 'object') v = JSON.stringify(v);
      else if (typeof v === 'boolean') v = v ? 'TRUE' : 'FALSE';
      return `<span><label>${esc(k.replace(/([A-Z])/g, ' $1').toUpperCase())}</label><b>${esc(String(v)).slice(0, 220)}</b></span>`;
    }).join('')}</div>`;
  }

  function economics(fe) {
    if (!fe) return emptyState('Not identified', '');
    const by = fe.byExecutionClass || {};
    const keys = Object.keys(by);
    if (!keys.length) return panelNote('NO EXECUTION',
      'This decision produced no reconstructed execution. That is the record, '
      + 'not a gap.', 'grey');
    return `<div class="sh-classgrid">${keys.map(k => `
      <div class="sh-classcard ${by[k].realizable ? 'real' : 'sim'}">
        <label>${esc(k.replace(/_/g, ' '))}</label>
        <strong>${px(by[k].vwap)}<small>vwap</small></strong>
        <div class="sh-kv-mini"><span>Filled<b>${qty(by[k].filledQty)}</b></span>
        <span>Slippage<b>${isNum(by[k].slippage) ? px(by[k].slippage) : NI}</b></span></div>
        <span class="sh-realflag">${by[k].realizable ? 'REALIZABLE' : 'COUNTERFACTUAL'}</span>
      </div>`).join('')}</div>
      ${panelNote('NO TOTAL', esc(fe.why || ''), 'grey')}`;
  }

  /* ── PERFORMANCE ───────────────────────────────────────────────── */

  function performanceTab() {
    const e = state.data['equity'];
    const s = state.data['summary'];
    const problem = feedProblem('equity');
    return `${disclosure((e && e.environment) || (s && s.environment))}
      ${s ? freshness(s) : ''}${problem || ''}
      <div class="sh-perf-lanes">
        ${perfCard('RN1 Shadow P&L', s)}
        ${perfCard('BETTOR EV Shadow P&L', s, true)}
      </div>
      ${s ? classPanel(s) : ''}
      <section class="sh-panel"><div class="sh-panel-head"><h2>Shadow equity</h2>
        <span class="sh-sub">Per lane. Never combined, and never a flat line standing in for an absent measurement.</span></div>
      <div class="sh-panel-body">${!e ? emptyState('Reading…', '')
        : e.state === 'NO_EXECUTIONS_YET'
          ? emptyState('No equity curve yet', esc(e.why || ''))
          : curves(e)}</div></section>`;
  }

  function perfCard(title, s, bettor) {
    const buckets = ['Settled', 'Unrealized', 'Pairing', 'Cashout',
      'Hold to settlement', 'Marketable execution', 'Passive counterfactual'];
    const lane = s && s.lanes && s.lanes[bettor ? 'BETTOR_EV_SHADOW' : 'RN1_SHADOW'];
    return `<section class="sh-panel"><div class="sh-panel-head"><h2>${esc(title)}</h2>
      <span class="sh-chip ${lane && lane.state === 'LIVE' ? 'green' : 'grey'}">${esc(String(lane && lane.state || 'NOT ESTABLISHED').replace(/_/g, ' '))}</span></div>
      <div class="sh-kv">${buckets.map(b => `<div><label>${esc(b)}</label><b>${NI}</b></div>`).join('')}</div>
      <div class="sh-panel-footer">PASSIVE_FILL_STATUS ${NI} — an unidentified passive fill is never counted as realized shadow P&amp;L.</div>
    </section>`;
  }

  function curves(e) {
    const series = e.series || {};
    return Object.keys(series).map(lane => {
      const pts = series[lane] || [];
      if (!pts.length) return `<div class="sh-curve-empty"><label>${esc(lane.replace(/_/g, ' '))}</label><span>${NI}</span></div>`;
      const vals = pts.map(p => isNum(p.executionDrag) ? p.executionDrag : 0);
      const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
      const span = (max - min) || 1;
      const d = pts.map((p, i) => `${(i / Math.max(pts.length - 1, 1)) * 100},${100 - ((vals[i] - min) / span) * 100}`).join(' ');
      return `<div class="sh-curve"><label>${esc(lane.replace(/_/g, ' '))} · execution drag</label>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <polyline points="${d}" fill="none" stroke="currentColor" stroke-width="1.2" vector-effect="non-scaling-stroke"/>
        </svg>
        <span class="sh-curve-meta">${pts.length} buckets · ${esc(e.why || '')}</span></div>`;
    }).join('');
  }

  /* ── shell ─────────────────────────────────────────────────────── */

  const VIEW = {
    overview, decisions: decisionsTab, positions: positionsTab,
    execution: executionTab, pairing: pairingTab, comparison: comparisonTab,
    audit: auditTab, performance: performanceTab
  };

  function shell() {
    return `<div class="sh-env">
      <header class="sh-head">
        <div class="sh-head-mark"><span class="sh-core"><i></i><i></i><i></i></span></div>
        <div><h1>BETTOR EV Engine</h1>
          <p>The primary intelligence lane, collecting its own prospective evidence.
             RN1 Shadow runs beside it as an external benchmark, and the two are
             never combined into one number.</p></div>
      </header>
      <nav class="sh-tabs">${TABS.map(([id, label]) =>
        `<button class="${state.tab === id ? 'on' : ''}" data-shadow-tab="${id}">${esc(label)}</button>`).join('')}</nav>
      <div id="shadow-view" class="sh-view"></div>
    </div>`;
  }

  function paint() {
    const host = document.getElementById('shadow-view');
    if (!host) { stop(); return; }
    host.innerHTML = (VIEW[state.tab] || overview)();
  }

  function stop() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  /* The page markup app.js renders for #shadow. Everything inside is
   * filled by paint() from live payloads. */
  function page() { return shell(); }

  function mount(sub) {
    if (sub && VIEW[sub]) state.tab = sub;
    paint();
    refresh();
    stop();
    // BOUNDED POLLING, not a socket. "Do not create unnecessary
    // infrastructure merely for animation" -- the ledger's own cadence
    // is seconds, and a poll the browser can stop is cheaper to reason
    // about than a stream nobody is watching.
    state.timer = setInterval(() => {
      if (!document.getElementById('shadow-view')) { stop(); return; }
      if (document.hidden) return;
      refresh();
    }, POLL_MS);
  }

  document.addEventListener('click', ev => {
    const tab = ev.target.closest && ev.target.closest('[data-shadow-tab]');
    if (tab) {
      state.tab = tab.dataset.shadowTab;
      if (location.hash !== '#shadow/' + state.tab)
        history.replaceState(null, '', '#shadow/' + state.tab);
      paint(); refresh(); return;
    }
    const trade = ev.target.closest && ev.target.closest('[data-shadow-trade]');
    if (trade && trade.dataset.shadowTrade) {
      state.trade = trade.dataset.shadowTrade;
      state.tab = 'audit';
      history.replaceState(null, '', '#shadow/audit');
      paint(); refresh();
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && document.getElementById('shadow-view')) refresh();
  });

  root.BTShadow = { page, mount, stop, TABS, _state: state };
})(window);
