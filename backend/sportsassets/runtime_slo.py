"""RUNTIME SLOs (R30A): the eight service levels the owner audit names, each
with a TARGET, a MEASURED value, a WINDOW and a status OK / BREACH /
UNAVAILABLE(reason).

    feed freshness          Pinnacle age at decision, p50 / p90 vs the odds
                            source's 30 s rule (paper_decisions.pinnacle
                            age_s, the age the decision itself recorded)
    decision latency        provider change received -> reactive evaluation
                            finished, p50 / p90 vs the scheduler's own 12 s
                            evaluation deadline (pinnapi_reactive_attempts)
    open positions without  open PAPER + ACTUAL positions whose ONE current
      a fresh Xavier review Xavier review is not CURRENT (agent_work_state,
                            the same read the floor's work states use)
    agent task age          open agent_work_requests past their own expiry
                            (each request expires within 1 h by CHECK)
    reconciliation age      the reconciliation of every open ACTUAL position
                            vs 3 x the mirror's reconcile cadence
    Opportunity Score age   the SCORES component's last run and the newest
                            score vs 3 x the profitability cycle
    parity divergence       LOGIC_DIVERGENCE rows in live_parity_ledger since
                            the recorded production cutover
    release state           API SHA == workers SHA == the latest committed
                            release receipt

RULES. Read-only (SELECT inside the caller's READ ONLY transaction, each SLO
in its own savepoint, the caller's statement_timeout bounds every read). A
missing table, an absent cutover, an unset build SHA or an empty window is
UNAVAILABLE with its reason -- never a manufactured zero and never OK by
default. No threshold here is an economic, risk or freshness threshold of
any trading rule: every target is the system's OWN recorded bound, cited
beside it, and nothing reads this module to decide anything.

WHAT THIS MODULE CANNOT DO. It imports no order, venue, execution, ledger,
paper or funded module (tests walk its imports) and writes nothing.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

VERSION = "RUNTIME_SLO_V1"
OK, BREACH, UNAVAILABLE = "OK", "BREACH", "UNAVAILABLE"
STATEMENT_TIMEOUT_MS = 4000

#: the odds source's own freshness rule (paper_decisions.pinnacle limit_s =
#: the paper session's entry.pinnacle_max_age_s, 30 s in production; read
#: back as limit_min/limit_max 30.0 by research-sql run 37226381750). The
#: SLO measures it; it never loosens or tightens it.
FEED_FRESHNESS_TARGET_S = 30.0
FEED_WINDOW_S = 24 * 3600.0
#: = pinnapi_reactive.Scheduler(deadline=12): the scheduler's own bound on
#: one evaluation (copied; a test pins equality)
DECISION_LATENCY_TARGET_S = 12.0
LATENCY_WINDOW_S = 24 * 3600.0
#: = execmirror.MANAGEMENT_EVERY_S (60 s): audrey_reconcile's cadence while
#: the mirror lane RUNS; 3 x, the loop-health rule
RECONCILE_EVERY_S = 60.0
RECONCILE_TARGET_S = 3 * RECONCILE_EVERY_S
#: = profitability.runner.CYCLE_S (3600 s): the cycle that runs SCORES
SCORES_CYCLE_S = 3600.0
SCORES_TARGET_S = 3 * SCORES_CYCLE_S


def _ep(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(t):
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=timezone.utc).isoformat(
        timespec="seconds")


def slo(name, *, target, window, status, measured=None, why=None,
        source=None) -> dict:
    return {"slo": name, "target": target, "window": window,
            "measured": measured, "status": status,
            "why": why if status != OK else None, "source": source}


def unavailable(name, why, *, target, window, source=None,
                measured=None) -> dict:
    return slo(name, target=target, window=window, status=UNAVAILABLE,
               why=why, source=source, measured=measured)


async def _section(conn, name, fn, judge_empty):
    """One SLO in its own savepoint. A read that fails is UNAVAILABLE with
    the reason, carrying the SLO's own target and window (`judge_empty`
    builds the shape); a missing table names the table."""
    try:
        async with conn.transaction():
            return await fn()
    except Exception as exc:                                    # noqa: BLE001
        why = type(exc).__name__
        if why == "UndefinedTableError":
            why = "TABLE_NOT_DEPLOYED: %s" % (str(exc).split('"')[1]
                                              if '"' in str(exc) else exc)
        shape = judge_empty()
        return unavailable(name, why, target=shape["target"],
                           window=shape["window"], source=shape["source"])


# ── 1 · feed freshness ─────────────────────────────────────────────────

def judge_feed(row: dict | None) -> dict:
    target = {"p90_age_s_at_most": FEED_FRESHNESS_TARGET_S,
              "basis": "the odds source's 30 s freshness rule (decision "
                       "pinnacle.limit_s)"}
    window = "decisions in the last %d h" % (FEED_WINDOW_S / 3600)
    src = "paper_decisions.pinnacle->age_s"
    r = row or {}
    n, with_age = int(r.get("decisions") or 0), int(r.get("with_age") or 0)
    measured = {"decisions": n, "with_recorded_age": with_age,
                "without_recorded_age": n - with_age,
                "p50_age_s": r.get("p50"), "p90_age_s": r.get("p90"),
                "over_own_limit": r.get("over_limit"),
                "limit_s_recorded": r.get("limit_max")}
    if n == 0:
        return unavailable("FEED_FRESHNESS", "NO_DECISIONS_IN_WINDOW",
                           target=target, window=window, source=src,
                           measured=measured)
    if with_age == 0 or r.get("p90") is None:
        return unavailable("FEED_FRESHNESS", "NO_RECORDED_PINNACLE_AGE",
                           target=target, window=window, source=src,
                           measured=measured)
    ok = float(r["p90"]) <= FEED_FRESHNESS_TARGET_S
    return slo("FEED_FRESHNESS", target=target, window=window, source=src,
               measured=measured, status=OK if ok else BREACH,
               why=None if ok else "P90_PINNACLE_AGE_ABOVE_30S")


FEED_SQL = """
SELECT count(*) AS decisions,
       count(*) FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number')
           AS with_age,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8)
           FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number') AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8)
           FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number') AS p90,
       count(*) FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number'
           AND jsonb_typeof(pinnacle->'limit_s') = 'number'
           AND (pinnacle->>'age_s')::float8 > (pinnacle->>'limit_s')::float8)
           AS over_limit,
       max((pinnacle->>'limit_s')::float8) FILTER (
           WHERE jsonb_typeof(pinnacle->'limit_s') = 'number') AS limit_max
  FROM paper_decisions
 WHERE decided_at > to_timestamp($1) AND decided_at <= to_timestamp($2)
"""


# ── 2 · decision latency ───────────────────────────────────────────────

def judge_latency(row: dict | None) -> dict:
    target = {"p90_latency_s_at_most": DECISION_LATENCY_TARGET_S,
              "basis": "the reactive scheduler's own evaluation deadline "
                       "(pinnapi_reactive deadline=12)"}
    window = "reactive evaluations in the last %d h" % (
        LATENCY_WINDOW_S / 3600)
    src = ("pinnapi_reactive_attempts COMPLETED: detail.finished_at - "
           "detail.received_at")
    r = row or {}
    n = int(r.get("completed") or 0)
    measured = {"completed": n, "p50_s": r.get("p50"), "p90_s": r.get("p90"),
                "max_s": r.get("max"), "timeouts": r.get("timeouts"),
                "orphaned_started": r.get("orphaned")}
    if n == 0:
        return unavailable("DECISION_LATENCY",
                           "NO_COMPLETED_REACTIVE_EVALUATION_IN_WINDOW",
                           target=target, window=window, source=src,
                           measured=measured)
    ok = float(r["p90"]) <= DECISION_LATENCY_TARGET_S
    return slo("DECISION_LATENCY", target=target, window=window, source=src,
               measured=measured, status=OK if ok else BREACH,
               why=None if ok else "P90_LATENCY_ABOVE_THE_12S_DEADLINE")


LATENCY_SQL = """
SELECT count(*) FILTER (WHERE state = 'COMPLETED' AND lat IS NOT NULL)
           AS completed,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY lat)
           FILTER (WHERE state = 'COMPLETED' AND lat IS NOT NULL) AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY lat)
           FILTER (WHERE state = 'COMPLETED' AND lat IS NOT NULL) AS p90,
       max(lat) FILTER (WHERE state = 'COMPLETED') AS max,
       count(*) FILTER (WHERE state = 'TIMEOUT') AS timeouts,
       count(*) FILTER (WHERE state = 'STARTED'
                        AND updated_at < to_timestamp($2) - interval '60 seconds')
           AS orphaned
  FROM (SELECT state, updated_at,
               CASE WHEN jsonb_typeof(detail->'finished_at') = 'number'
                     AND jsonb_typeof(detail->'received_at') = 'number'
                    THEN (detail->>'finished_at')::float8
                         - (detail->>'received_at')::float8 END AS lat
          FROM pinnapi_reactive_attempts
         WHERE created_at > to_timestamp($1)
           AND created_at <= to_timestamp($2)) q
"""


# ── 3 · open positions without a fresh Xavier review ───────────────────

def judge_reviews(pos: dict | None) -> dict:
    target = {"open_positions_without_current_review_at_most": 0,
              "basis": "every open position has ONE current Xavier review "
                       "inside its own freshness window (xavier_freshness)"}
    window = "now (each position's current review)"
    src = "agent_work_state positions (paper_fills / smalllive_handoffs + " \
          "the current review per position)"
    p = pos or {}
    if p.get("open") is None:
        return unavailable("OPEN_POSITIONS_WITHOUT_FRESH_REVIEW",
                           p.get("why") or "OPEN_POSITIONS_UNREADABLE",
                           target=target, window=window, source=src)
    classes: dict = {}
    for row in p.get("positions") or []:
        classes[row["class"]] = classes.get(row["class"], 0) + 1
    current = classes.get("CURRENT", 0)
    stale = int(p["open"]) - current
    measured = {"open_positions": int(p["open"]),
                "with_current_review": current,
                "without_current_review": stale,
                "by_class": classes,
                "listed": len(p.get("positions") or [])}
    ok = stale == 0
    return slo("OPEN_POSITIONS_WITHOUT_FRESH_REVIEW", target=target,
               window=window, source=src, measured=measured,
               status=OK if ok else BREACH,
               why=None if ok else "%d_OPEN_POSITIONS_WITHOUT_A_CURRENT_"
                                   "REVIEW" % stale)


async def read_positions(conn, now: float) -> dict:
    from . import agent_work_state as AWS
    s = AWS._Sections(conn)
    market = await AWS._read_market(s)
    ms = AWS.market_status(market, now)
    pos = await AWS._read_positions(s, now)
    out = {"open": pos["open"], "why": pos["why"], "positions": []}
    for p in pos["positions"]:
        cls, why = AWS.position_class(p, market=ms, now=now)
        out["positions"].append({"position_kind": p["position_kind"],
                                 "group_id": p["group_id"], "class": cls,
                                 "why": why})
    return out


# ── 4 · agent task age ─────────────────────────────────────────────────

def judge_tasks(row: dict | None, *, now: float) -> dict:
    target = {"open_requests_past_expiry_at_most": 0,
              "basis": "each agent_work_request expires within 1 h of "
                       "enqueue (CHECK in migration 226)"}
    window = "open requests (agent_work_open)"
    src = "agent_work_open JOIN agent_work_requests"
    r = row or {}
    n = int(r.get("open_requests") or 0)
    oldest = _ep(r.get("oldest_opened_at"))
    measured = {"open_requests": n,
                "oldest_open_age_s": None if oldest is None
                else round(now - oldest, 1),
                "past_expiry": int(r.get("past_expiry") or 0),
                "by_agent": r.get("by_agent") or {}}
    ok = measured["past_expiry"] == 0
    return slo("AGENT_TASK_AGE", target=target, window=window, source=src,
               measured=measured, status=OK if ok else BREACH,
               why=None if ok else "OPEN_REQUESTS_PAST_THEIR_EXPIRY")


TASKS_SQL = """
SELECT count(*) AS open_requests, min(o.opened_at) AS oldest_opened_at,
       count(*) FILTER (WHERE r.expires_at < to_timestamp($1)) AS past_expiry
  FROM agent_work_open o
  JOIN agent_work_requests r ON r.request_id = o.request_id
"""
TASKS_BY_AGENT_SQL = """
SELECT agent_id, count(*) AS n FROM agent_work_open GROUP BY agent_id
"""


# ── 5 · reconciliation age ─────────────────────────────────────────────

def judge_reconciliation(rows: list | None, newest, *, now: float) -> dict:
    target = {"open_actual_position_reconciliation_age_s_at_most":
              RECONCILE_TARGET_S,
              "basis": "3 x execmirror.MANAGEMENT_EVERY_S (audrey_reconcile "
                       "cadence while the mirror lane RUNS)"}
    window = "now (each open ACTUAL position's latest reconciliation)"
    src = "smalllive_handoffs (OPEN) LEFT JOIN smalllive_reconciliations"
    rows = list(rows or [])
    newest_t = _ep(newest)
    ctx = {"open_actual_positions": len(rows),
           "newest_reconciliation_at": _iso(newest_t),
           "newest_reconciliation_age_s": None if newest_t is None
           else round(now - newest_t, 1)}
    if not rows:
        ctx["note"] = ("no open ACTUAL position: nothing is owed a "
                       "reconciliation (SMALL LIVE is SHADOW)")
        return slo("RECONCILIATION_AGE", target=target, window=window,
                   source=src, measured=ctx, status=OK)
    ages, never = [], []
    for r in rows:
        t = _ep(r.get("reconciled_at"))
        if t is None:
            never.append(r.get("group_id"))
        else:
            ages.append(now - t)
    ctx.update(max_age_s=None if not ages else round(max(ages), 1),
               never_reconciled=never)
    if never:
        return slo("RECONCILIATION_AGE", target=target, window=window,
                   source=src, measured=ctx, status=BREACH,
                   why="OPEN_ACTUAL_POSITION_NEVER_RECONCILED")
    ok = max(ages) <= RECONCILE_TARGET_S
    return slo("RECONCILIATION_AGE", target=target, window=window, source=src,
               measured=ctx, status=OK if ok else BREACH,
               why=None if ok else "RECONCILIATION_OLDER_THAN_TARGET")


# ── 6 · Opportunity Score age ──────────────────────────────────────────

def judge_scores(last_run: dict | None, last_ok, newest_score, *,
                 now: float) -> dict:
    target = {"last_ok_scores_run_age_s_at_most": SCORES_TARGET_S,
              "latest_scores_run_status": "OK",
              "basis": "3 x the profitability cycle (CYCLE_S 3600) that "
                       "runs the SCORES component"}
    window = "the SCORES component's runs (lol_runs)"
    src = "lol_runs component=SCORES; lol_opportunity_scores"
    ok_t, sc_t = _ep(last_ok), _ep(newest_score)
    lr = last_run or {}
    measured = {"latest_run_status": lr.get("status"),
                "latest_run_at": _iso(_ep(lr.get("started_at"))),
                "latest_run_error": lr.get("error"),
                "last_ok_run_at": _iso(ok_t),
                "last_ok_run_age_s": None if ok_t is None
                else round(now - ok_t, 1),
                "newest_score_at": _iso(sc_t),
                "newest_score_age_s": None if sc_t is None
                else round(now - sc_t, 1)}
    if not lr:
        return unavailable("OPPORTUNITY_SCORE_AGE", "NO_SCORES_RUN_RECORDED",
                           target=target, window=window, source=src,
                           measured=measured)
    if lr.get("status") != "OK":
        return slo("OPPORTUNITY_SCORE_AGE", target=target, window=window,
                   source=src, measured=measured, status=BREACH,
                   why="LATEST_SCORES_RUN_%s" % lr.get("status"))
    if ok_t is None or now - ok_t > SCORES_TARGET_S:
        return slo("OPPORTUNITY_SCORE_AGE", target=target, window=window,
                   source=src, measured=measured, status=BREACH,
                   why="NO_OK_SCORES_RUN_WITHIN_TARGET")
    return slo("OPPORTUNITY_SCORE_AGE", target=target, window=window,
               source=src, measured=measured, status=OK)


# ── 7 · parity divergence ──────────────────────────────────────────────

def judge_parity(cutover: dict | None, counts: dict | None) -> dict:
    target = {"logic_divergences_since_cutover_at_most": 0,
              "basis": "any LOGIC_DIVERGENCE halts new live exposure "
                       "(live_parity)"}
    window = "since the recorded production cutover"
    src = "live_parity_cutover + live_parity_ledger"
    if not cutover:
        return unavailable("PARITY_DIVERGENCE",
                           "NO_PRODUCTION_CUTOVER_RECORDED", target=target,
                           window=window, source=src)
    c = counts or {}
    measured = {"cutover_at": _iso(_ep(cutover.get("cutover_at"))),
                "release_sha": cutover.get("release_sha"),
                "rows_since_cutover": int(c.get("rows") or 0),
                "logic_divergences": int(c.get("divergences") or 0),
                "small_live_halted": c.get("halted")}
    ok = measured["logic_divergences"] == 0
    return slo("PARITY_DIVERGENCE", target=target, window=window, source=src,
               measured=measured, status=OK if ok else BREACH,
               why=None if ok else "LOGIC_DIVERGENCE_SINCE_CUTOVER")


# ── 8 · release state ──────────────────────────────────────────────────

def judge_release(api: dict, workers: dict, receipts: dict) -> dict:
    target = {"api_sha == workers_sha == latest_receipt_sha": True}
    window = "now"
    src = ("env RENDER_GIT_COMMIT; ingestion_state.workers_boot; "
           "sportsassets/release_receipts (hash-verified)")
    items = [r for r in (receipts or {}).get("items") or []
             if r.get("hash_verified")]
    latest = max(items, key=lambda r: r.get("generated_at") or "") \
        if items else None
    a = (api or {}).get("sha")
    w = (workers or {}).get("sha")
    rsha = (latest or {}).get("sha")
    measured = {"api_sha": a, "workers_sha": w,
                "latest_receipt_sha": rsha,
                "latest_receipt_file": (latest or {}).get("file"),
                "latest_receipt_state": (latest or {}).get("state")}
    missing = [n for n, v in (("API_SHA", a), ("WORKERS_SHA", w),
                              ("LATEST_RECEIPT_SHA", rsha)) if not v]
    if missing:
        return unavailable("RELEASE_STATE", "%s_UNAVAILABLE" % missing[0],
                           target=target, window=window, source=src,
                           measured=measured)
    full = [str(x).lower() for x in (a, w, rsha)]
    ok = len(set(full)) == 1 and len(full[0]) == 40
    why = None
    if not ok:
        why = ("API_WORKERS_MISALIGNED" if full[0] != full[1]
               else "RUNNING_SHA_IS_NOT_THE_LATEST_RECEIPT"
               if full[0] != full[2] else "SHA_NOT_FULL_LENGTH")
    return slo("RELEASE_STATE", target=target, window=window, source=src,
               measured=measured, status=OK if ok else BREACH, why=why)


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

async def read_slos(conn, *, now: float | None = None, api=None,
                    receipts=None) -> dict:
    """All eight SLOs. `conn` is inside the caller's READ ONLY transaction
    (statement_timeout set). `api` / `receipts` default to this process's
    build and committed receipts (api.command_release)."""
    now = float(time.time() if now is None else now)
    from .api import command_release as CR
    out = []

    # 1 · feed freshness
    async def feed():
        r = await conn.fetchrow(FEED_SQL, now - FEED_WINDOW_S, now)
        return judge_feed(dict(r) if r else None)
    out.append(await _section(conn, "FEED_FRESHNESS", feed,
                              lambda: judge_feed(None)))

    # 2 · decision latency
    async def latency():
        r = await conn.fetchrow(LATENCY_SQL, now - LATENCY_WINDOW_S, now)
        return judge_latency(dict(r) if r else None)
    out.append(await _section(conn, "DECISION_LATENCY", latency,
                              lambda: judge_latency(None)))

    # 3 · reviews
    async def reviews():
        return judge_reviews(await read_positions(conn, now))
    out.append(await _section(conn, "OPEN_POSITIONS_WITHOUT_FRESH_REVIEW",
                              reviews, lambda: judge_reviews(None)))

    # 4 · agent task age
    async def tasks():
        r = await conn.fetchrow(TASKS_SQL, now)
        d = dict(r) if r else {}
        d["by_agent"] = {x["agent_id"]: int(x["n"]) for x in
                         await conn.fetch(TASKS_BY_AGENT_SQL)}
        return judge_tasks(d, now=now)
    out.append(await _section(conn, "AGENT_TASK_AGE", tasks,
                              lambda: judge_tasks(None, now=now)))

    # 5 · reconciliation age
    async def recon():
        rows = await conn.fetch(
            "SELECT h.group_id, r.reconciled_at FROM smalllive_handoffs h "
            "  LEFT JOIN smalllive_reconciliations r ON r.group_id = h.group_id "
            " WHERE h.state = 'OPEN'")
        newest = await conn.fetchval(
            "SELECT max(reconciled_at) FROM smalllive_reconciliations")
        return judge_reconciliation([dict(r) for r in rows], newest, now=now)
    out.append(await _section(conn, "RECONCILIATION_AGE", recon,
                              lambda: judge_reconciliation([], None, now=now)))

    # 6 · Opportunity Score age
    async def scores():
        last = await conn.fetchrow(
            "SELECT status, started_at, error FROM lol_runs "
            " WHERE component = 'SCORES' AND started_at <= to_timestamp($1) "
            " ORDER BY started_at DESC LIMIT 1", now)
        last_ok = await conn.fetchval(
            "SELECT max(finished_at) FROM lol_runs WHERE component = 'SCORES' "
            "   AND status = 'OK' AND started_at <= to_timestamp($1)", now)
        newest = await conn.fetchval(
            "SELECT max(computed_at) FROM lol_opportunity_scores "
            " WHERE computed_at <= to_timestamp($1)", now)
        return judge_scores(dict(last) if last else None, last_ok, newest,
                            now=now)
    out.append(await _section(conn, "OPPORTUNITY_SCORE_AGE", scores,
                              lambda: judge_scores(None, None, None, now=now)))

    # 7 · parity divergence
    async def parity():
        cut = await conn.fetchrow(
            "SELECT cutover_at, release_sha FROM live_parity_cutover "
            " WHERE id = 1")
        if cut is None:
            return judge_parity(None, None)
        c = await conn.fetchrow(
            "SELECT count(*) AS rows, count(*) FILTER (WHERE parity_state = "
            "       'LOGIC_DIVERGENCE') AS divergences, "
            "       (SELECT halted FROM small_live_control WHERE id = 1) "
            "           AS halted "
            "  FROM live_parity_ledger WHERE created_at >= $1",
            cut["cutover_at"])
        return judge_parity(dict(cut), dict(c) if c else None)
    out.append(await _section(conn, "PARITY_DIVERGENCE", parity,
                              lambda: judge_parity(None, None)))

    # 8 · release state
    async def release():
        wk = await CR.workers_boot(conn)
        return judge_release(api if api is not None else CR.api_build(), wk,
                             receipts if receipts is not None
                             else CR.read_receipts())
    out.append(await _section(conn, "RELEASE_STATE", release,
                              lambda: judge_release({}, {}, {})))

    tally: dict = {}
    for s in out:
        tally[s["status"]] = tally.get(s["status"], 0) + 1
    return {"version": VERSION, "read_only": True, "now": now,
            "generated_at": _iso(now), "slos": out, "summary": tally,
            "breaches": [s["slo"] for s in out if s["status"] == BREACH],
            "unavailable": {s["slo"]: s["why"] for s in out
                            if s["status"] == UNAVAILABLE}}
