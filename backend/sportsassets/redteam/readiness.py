"""THE FINAL CAPITAL-READINESS INTERLOCK (red_team.readiness) OVER BETTOR'S
OWN EVIDENCE.

PAPER_SHADOW_ONLY unless EVERY check is green: release lineage / exact SHA,
runtime, market data, held freshness, priority freshness, settlement,
certified Twin, truth-source reconciliation, positive independent edge,
positive capacity, canonical exposure, mechanism profit breakers, live
authority still shadow, historical PAPER immutable. Only then
CAPITAL_CANDIDATE -- which activates nothing.

Everything is read inside the caller's READ ONLY transaction (the
completion readback, the controls); the runner writes the receipts.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal

from ..red_team import readiness as RTR
from ..red_team import ui_truth as UT
from ..red_team.models import ReadinessInput
from . import controls as C
from . import venue_health as VH

VERSION = "RED_TEAM_READINESS_V1"
ACCOUNT_ID = "paper_acct_main"
CAPACITY_WINDOW_DAYS = 14
QTY_GRID = ((1, 10), (11, 50), (51, 100), (101, 500), (501, 10 ** 9))
METRIC_MAX_AGE_S = 900.0


def implementation_sha() -> str:
    return (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower() \
        or "UNKNOWN"


# ── readers ──────────────────────────────────────────────────────────────

#: ONE OPPORTUNITY IS ONE (strategy, contract, held side). The bind evaluates
#: the same contract again on every decision cycle; those re-evaluations are
#: one opportunity, never N units of capacity (RC6 ev-audit).
CAPACITY_OPPORTUNITY_KEY = ("strategy", "us_market_slug", "holding_side")

CAPACITY_SQL = """
SELECT strategy, us_market_slug, holding_side, fixture, qty_in,
       ev_per_contract_usd ev, fill_probability fp, all_in_ev_usd net,
       market_price px, expected_hold_hours h,
       extract(epoch FROM evaluated_at) at
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - make_interval(days => $1)
   AND qty_in IS NOT NULL AND ev_per_contract_usd IS NOT NULL"""


def _f0(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def qty_bucket(q: float):
    """The QTY_GRID bucket of a quantity: (previous edge, edge], so every
    positive quantity has exactly one bucket; None for q <= 0."""
    prev = 0
    for lo, hi in QTY_GRID:
        if prev < q <= hi:
            return (lo, hi)
        prev = hi
    return None


def capacity_points_from_rows(rows: list) -> list:
    """The positive-capacity frontier by size bucket from the bind's own
    per-entry evaluations. PURE.

    WHAT WAS WRONG (RC6 ev-audit), each a capacity OVERSTATEMENT that the
    all-negative production evidence hid (RC5 research read 2026-10-09:
    1,720 evaluations in 14 days, every one CASH, the best EV per contract
    -0.0006):

      * every re-evaluation of one contract was summed as more capital and
        more expected net -- the same contract evaluated 50 times at 100
        contracts was "5,000 contracts of capacity". In production the 1,720
        evaluations are 453 opportunities, and the capital summed over
        evaluations was $839,121 against $129,570 at one (latest) evaluation
        per opportunity: 6.5x. Each bucket now keeps
        the LATEST evaluation per opportunity (CAPACITY_OPPORTUNITY_KEY), and
        each opportunity carries its own capital so the deployment cap never
        counts one contract twice across buckets either (controls.capacity);
      * a bucket reported its UPPER bound as its quantity (1-10 -> 10,
        11-50 -> 50): evidence at 1 contract claimed capacity at 10. That is
        the linear extrapolation of a tiny PAPER trade the red-team directive
        forbids. A point's quantity is now the largest quantity actually
        evaluated in it;
      * the grid was closed on both ends with integer edges, so a fractional
        size between buckets (10.5 from a REDUCED_SIZE factor) fell in no
        bucket and was silently dropped. Buckets are now (previous edge,
        edge], covering every positive quantity.

    The bucket's lower bound is unchanged: one value per fixture (the mean of
    its opportunities' EV per contract), then the event-clustered one-sided
    95% bound over fixtures.
    """
    latest: dict = {}
    evaluations: dict = {}
    for r in rows:
        b = qty_bucket(_f0(r.get("qty_in")))
        if b is None:
            continue
        evaluations[b] = evaluations.get(b, 0) + 1
        key = (b,) + tuple(str(r.get(k) or "?")
                           for k in CAPACITY_OPPORTUNITY_KEY)
        cur = latest.get(key)
        if cur is None or _f0(r.get("at")) >= _f0(cur.get("at")):
            latest[key] = r
    out = []
    for lo, hi in QTY_GRID:
        rs = [(k, r) for k, r in latest.items() if k[0] == (lo, hi)]
        if not rs:
            continue
        by_fx: dict = {}
        for _k, r in rs:
            by_fx.setdefault(r.get("fixture") or "?", []).append(
                _f0(r.get("ev")))
        per = [sum(v) / len(v) for v in by_fx.values()]
        n = len(per)
        mean = sum(per) / n
        sd = (sum((x - mean) ** 2 for x in per) / (n - 1)) ** 0.5 \
            if n > 1 else None
        lb = (mean - 1.645 * sd / n ** 0.5) if sd is not None else -1.0
        fps = [_f0(r.get("fp")) for _k, r in rs if r.get("fp") is not None]
        cap_h = sum(_f0(r.get("qty_in")) * _f0(r.get("px")) * _f0(r.get("h"))
                    for _k, r in rs)
        opp_cap = {"|".join(k[1:]): round(_f0(r.get("qty_in"))
                                          * _f0(r.get("px")), 2)
                   for k, r in rs}
        out.append({"qty": max(_f0(r.get("qty_in")) for _k, r in rs),
                    "bucket": [lo, hi], "events": n,
                    "opportunities": len(rs),
                    "evaluations": evaluations.get((lo, hi), 0),
                    "lb_ev_per_contract": round(lb, 6),
                    "fill_probability": round(sum(fps) / len(fps), 4)
                    if fps else 0,
                    "capital_hours": round(cap_h, 2),
                    "expected_net": round(sum(_f0(r.get("net"))
                                              for _k, r in rs), 4),
                    "capital_usd": round(sum(opp_cap.values()), 2),
                    "opportunity_capital_usd": opp_cap})
    return out


async def capacity_points(conn) -> list:
    """capacity_points_from_rows over the last CAPACITY_WINDOW_DAYS of the
    bind's evaluations (READ ONLY)."""
    if not await C._has(conn, "paper_profitability_evaluations"):
        return []
    rows = [dict(r) for r in await conn.fetch(CAPACITY_SQL,
                                              CAPACITY_WINDOW_DAYS)]
    return capacity_points_from_rows(rows)


async def karen_counterfactuals(conn) -> tuple:
    """(saved loss, false-block cost, source, twin rows) from the NEWEST
    twin run that scored Karen's counterfactual, both metrics from that one
    run.

    THE BOOK (RC6). twin.scorecards.karen writes loss_avoided_paper_basis /
    profit_sacrificed_paper_basis in book COUNTERFACTUAL -- a metric derived
    from a twin world names its basis book in the metric, never in `book`
    (migration 219 allows ACTUAL / PAPER / COUNTERFACTUAL). This read asked
    for book = 'PAPER', a row the twin never writes, so KAREN_VALUE read
    KAREN_COUNTERFACTUALS_UNMEASURED whatever the twin had measured
    (production RC5, 43 runs 2026-10-04 .. 10-09: every Karen row of these
    metrics is COUNTERFACTUAL; research/rc6_redteam_controls.sql K1/K2).
    The two metrics were also read DISTINCT ON metric across runs, so they
    could come from two different runs. Each row carries the twin's own
    status and reason (controls.karen decides on them)."""
    if not await C._has(conn, "twin_agent_scorecards"):
        return None, None, "twin_agent_scorecards absent", {}
    names = list(C.KAREN_TWIN_METRICS)
    got = await conn.fetch(
        "SELECT metric, book, value, status, reason, sample_n, run_id, "
        "       extract(epoch FROM computed_at)::float8 AS computed_at "
        "  FROM twin_agent_scorecards "
        " WHERE agent = 'KAREN' AND book = $2 AND metric = ANY($1) "
        "   AND run_id = (SELECT run_id FROM twin_agent_scorecards "
        "                  WHERE agent = 'KAREN' AND book = $2 "
        "                    AND metric = ANY($1) "
        "                  ORDER BY computed_at DESC LIMIT 1)",
        names, C.KAREN_TWIN_BOOK)
    rows = {r["metric"]: dict(r) for r in got}
    val = {m: (rows.get(m) or {}).get("value") for m in names}
    return (val[names[0]], val[names[1]],
            "twin_agent_scorecards book %s (RESEARCH counterfactual; the "
            "twin is not certified, so this is never capital evidence)"
            % C.KAREN_TWIN_BOOK, rows)


async def workers_boot_credentials(conn) -> tuple[dict | None, dict | None]:
    """(credential_classes, pmus_credential_census) from ONE read of the
    workers' boot heartbeat -- names and shape enums only, and the boot
    instant it was taken at (env changes restart the process)."""
    r = await conn.fetchrow(
        "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
    if r is None:
        return None, None
    v = C._j(r["value"]) or {}
    census = v.get("pmus_credential_census")
    if isinstance(census, dict):
        census = dict(census, taken_at_boot=v.get("at"))
    return v.get("credential_classes"), census


async def workers_credential_classes(conn) -> dict | None:
    return (await workers_boot_credentials(conn))[0]


def api_pmus_census() -> tuple[dict, dict]:
    """THIS process's census and its timing; never raises (an env-only
    read, so no savepoint), and a failure is recorded, never a pass."""
    from .. import pmus_credential_census as PCC
    t0 = time.monotonic()
    try:
        out = PCC.census(service="sportsassets-api")
        return out, {"ms": round((time.monotonic() - t0) * 1000.0, 1),
                     "ok": True}
    except Exception as exc:                                    # noqa: BLE001
        return ({"error": type(exc).__name__},
                {"ms": round((time.monotonic() - t0) * 1000.0, 1),
                 "ok": False, "why": type(exc).__name__})


# ── authority, READ (RC6 red-team, authority scenario) ───────────────────
#
# The `authority` block of this readback was four literals ("SHADOW",
# "NOT_ACTIVATED", "SHADOW_ONLY", False), and the interlock's
# live_authority_still_shadow compared completion's own literal
# small_live = "SHADOW" -- a readback that could not fail, whatever the
# lanes' real state (the Risk card's small_live_shadow / kalshi_live_money_
# not_activated / adriana_shadow_only units read it). Each is now READ from
# the state that actually decides it; an unreadable source is UNREAD (a
# failure), never the safe-sounding value.
SHADOW, NOT_ACTIVATED, SHADOW_ONLY = "SHADOW", "NOT_ACTIVATED", "SHADOW_ONLY"
#: kalshi_venue.ENABLED_ENV (the per-process submission switch)
KALSHI_ENABLED_ENV = "KALSHI_SMALLLIVE_ENABLED"


def small_live_authority(comp: dict) -> dict:
    """SHADOW only when the completion read's small_live_shadow gate (the
    execmirror_control row: the actual lane is not enabled-and-running) is
    True; ACTUAL_LANE_ACTIVE when it read the lane running; UNREAD
    otherwise (gate absent, table / row missing, section down)."""
    g = ((comp or {}).get("gates") or {}).get("small_live_shadow")
    if not isinstance(g, dict):
        return {"state": "UNREAD:SMALL_LIVE_GATE_ABSENT",
                "source": "completion gates.small_live_shadow"}
    if g.get("value") is True:
        state = SHADOW
    elif g.get("reason") == "SMALL_LIVE_ACTUAL_LANE_ACTIVE":
        state = "ACTUAL_LANE_ACTIVE"
    else:
        state = "UNREAD:%s" % (g.get("reason") or "NO_REASON")
    return {"state": state, "gate": {"value": g.get("value"),
                                     "reason": g.get("reason")},
            "source": "completion gates.small_live_shadow "
                      "(execmirror_control enabled / stopped)"}


async def kalshi_live_money(conn, *, env=None) -> dict:
    """NOT_ACTIVATED when the durable Kalshi control row (kalshi_smalllive_
    control, which kalshi_venue.submission_gate requires enabled and not
    stopped) is disabled or stopped; CONTROL_ENABLED_NOT_STOPPED when it is
    not (a real precondition of submission is set); UNREAD when the row
    cannot be read. THIS process's KALSHI_SMALLLIVE_ENABLED switch is
    evidence beside it (it is per process)."""
    # kalshi_venue is never imported outside the Kalshi modules
    # (test_kalshi_isolation): its switch's name and parsing are read here,
    # pinned equal to kalshi_venue's by test
    env = os.environ if env is None else env
    on = str(env.get(KALSHI_ENABLED_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")
    ev = {"env_switch_on_this_process": on,
          "source": "kalshi_smalllive_control (kalshi_venue.submission_"
                    "gate) + this process's %s" % KALSHI_ENABLED_ENV}
    if not await C._has(conn, "kalshi_smalllive_control"):
        return dict(ev, state="UNREAD:KALSHI_CONTROL_TABLE_ABSENT",
                    control=None)
    r = await conn.fetchrow(
        "SELECT enabled, stopped, revision FROM kalshi_smalllive_control "
        " WHERE id = 1")
    if r is None:
        return dict(ev, state="UNREAD:KALSHI_CONTROL_ROW_MISSING",
                    control=None)
    ctl = {"enabled": bool(r["enabled"]), "stopped": bool(r["stopped"]),
           "revision": r["revision"]}
    state = (NOT_ACTIVATED if (not ctl["enabled"] or ctl["stopped"])
             else "CONTROL_ENABLED_NOT_STOPPED")
    return dict(ev, state=state, control=ctl)


def adriana_authority() -> dict:
    """SHADOW_ONLY when every Adriana module's own AUTHORITY assertion holds
    (the agent's code refuses by it); AUTHORITY_GRANTED otherwise."""
    from ..agents import adriana as AD
    from ..agents import adriana_arb as AA
    from ..agents import adriana_claims as ACL
    try:
        ok = bool(AA.assert_no_authority() and AD.assert_no_authority()
                  and ACL.assert_no_authority())
    except AssertionError as exc:
        return {"state": "AUTHORITY_GRANTED", "why": str(exc)[:200]}
    return {"state": SHADOW_ONLY if ok else "AUTHORITY_GRANTED",
            "mode": AA.AUTHORITY.get("mode"),
            "source": "agents.adriana_arb / adriana / adriana_claims "
                      "assert_no_authority"}


async def release_receipt(conn, sha: str) -> dict | None:
    if not sha or sha == "UNKNOWN" or not await C._has(
            conn, "red_team_release_receipts"):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM red_team_release_receipts WHERE deployed_sha = $1 "
        " ORDER BY observed_at DESC LIMIT 1", sha)
    return dict(r) if r else None


async def certificate_census(conn) -> dict:
    if not await C._has(conn, "red_team_settlement_certificates"):
        return {"status": "TABLE_ABSENT"}
    rows = await conn.fetch(
        "SELECT status, count(*) n FROM (SELECT DISTINCT ON (alias_key) "
        " alias_key, status FROM red_team_settlement_certificates ORDER BY "
        " alias_key, at DESC) x GROUP BY 1")
    return {r["status"]: int(r["n"]) for r in rows}


# ── assembly ─────────────────────────────────────────────────────────────

def _metric(value, *, as_of, source, status, num=None, den=None) -> dict:
    m = {"value": value, "as_of": as_of, "source": source, "status": status}
    if num is not None or den is not None:
        m.update(numerator=num, denominator=den)
    return m


def ui_truth(metrics: dict, *, now: float) -> dict:
    out, bad = {}, []
    for k, m in metrics.items():
        g = UT.metric_truth_gate(m, now=now, max_age_s=METRIC_MAX_AGE_S)
        shown = m["status"] if g["green"] else "STALE_OR_UNKNOWN"
        out[k] = dict(m, displayed_status=shown, blockers=list(g["blockers"]))
        if not g["green"]:
            bad.append(k)
    return C.result("UI_TRUTH", C.GREEN if not bad else C.RED,
                    ["METRIC_NOT_DISPLAYABLE_AS_GREEN:%s" % k for k in bad],
                    {"metrics": out, "max_age_s": METRIC_MAX_AGE_S})


#: each sectioned read and the controls it feeds
SECTION_CONTROLS = {
    "venue_health": ("VENUE_HEALTH",),
    "truth_quorum": ("TRUTH_QUORUM",),
    # the attribution rows are also MULTIPLE_TESTING's PBO / DSR input
    "attribution": ("PROFIT_BREAKERS", "ATTRIBUTION", "MULTIPLE_TESTING"),
    "holdout_registry": ("SAMPLE_INTEGRITY", "MULTIPLE_TESTING"),
    "capacity": ("CAPACITY",),
    "karen": ("KAREN_VALUE",),
    "credential_classes": ("CREDENTIAL_CLASSES",),
    # an env-only read (no savepoint): its timing is recorded by hand
    "pmus_census_api": ("CREDENTIAL_CLASSES",),
    "release_receipt": ("RELEASE", "MIGRATION_INTEGRITY"),
    "migrations": ("MIGRATION_INTEGRITY",),
    "fee_evidence": ("FEE_EVIDENCE",),
    "certificates": ("SETTLEMENT_CERTIFICATES",),
    "canonical_exposure": ("CANONICAL_EXPOSURE",),
}


async def evaluate(conn, *, now: float | None = None,
                   completion: dict | None = None) -> dict:
    from ..completion import read as CR
    from . import exposure as X
    from . import fees as F
    now = float(now if now is not None else time.time())
    sha = implementation_sha()
    comp = completion or await CR.read(conn, now=now)
    ready = comp.get("readiness") or {}
    md = comp.get("market_data") or {}
    mgmt = comp.get("management_freshness") or {}
    runtime = comp.get("runtime") or {}
    venue = comp.get("venue_positions") or {}
    held = {"markable": mgmt.get("markable"),
            "freshly_manageable": (None if mgmt.get("fresh") is None else
                                   int(mgmt.get("fresh") or 0)
                                   + int(mgmt.get("quiet_valid") or 0)),
            "as_of": now}
    pri = md.get("priority_freshness") or {}
    sec = CR._Sections(conn)
    unk = {"rate": None, "green": False, "status": "UNAVAILABLE",
           "source": "UNAVAILABLE", "numerator": None, "denominator": None}
    vh = await sec.run("venue_health", lambda: VH.read(
        conn, held=held, priority=pri, total=None, now=now,
        api_stream=md.get("pmx_primary")), {
            "venues": {}, "isolated": None, "cross_venue_pair": None,
            "freshness": {"held": dict(unk), "priority": dict(unk)}})
    venues = vh["venues"]
    pm_green = bool((venues.get(VH.POLYMARKET_US) or {}).get("green"))
    controls = {}
    controls["VENUE_HEALTH"] = C.result(
        "VENUE_HEALTH",
        C.GREEN if venues and all(v.get("green") for v in venues.values())
        else C.RED,
        ["%s:%s" % (k, b) for k, v in venues.items()
         for b in v.get("blockers") or ()],
        {"venues": venues, "isolated": vh["isolated"],
         "cross_venue_pair": vh["cross_venue_pair"],
         "freshness": vh["freshness"],
         # WHY a PMX stream counts as a gap (None = no gap); absent from
         # the evidence before, so a readback could not tell
         "polymarket_stream_gap": vh.get("polymarket_stream_gap",
                                         "VENUE_HEALTH_UNREAD"),
         "polymarket_api_stream_gap": vh.get("polymarket_api_stream_gap",
                                             "VENUE_HEALTH_UNREAD"),
         # which mechanism serves the Kalshi books (reported, not a gate)
         "kalshi_mechanism": vh.get("kalshi_mechanism",
                                    "VENUE_HEALTH_UNREAD")})
    # what the quorum's reader learned (snapshot, lane state, Audrey's
    # coverage and the ACTUAL positions she must have reconciled), so the
    # control names every source exactly and why it is not current
    qdetail: dict = {}
    rows, open_disc = await sec.run("truth_quorum", lambda: C.quorum_rows(
        conn, now=now, venue_confirmed=bool(venue.get("venue_confirmed")),
        market_data_green=pm_green, detail=qdetail), ([], None))
    controls["TRUTH_QUORUM"] = C.quorum(rows, now=now,
                                        audrey_open_discrepancies=open_disc,
                                        source_detail=qdetail, venue=venue)
    # EVERY PAPER position, read once; each control takes its own declared
    # population from it, and a read that left one of its members out turns
    # it RED by name (ATTRIBUTION_READ_TRUNCATED), never a pass on a subset
    aread: dict = {}
    every, fixtures = await sec.run(
        "attribution", lambda: C.attributed_positions(conn, now=now,
                                                      detail=aread),
        ([], {}))
    attributed, wread = C.attribution_window(every, aread, now=now)
    mech = C.mechanism_rows(attributed, fixtures)
    controls["PROFIT_BREAKERS"] = C.profit_breakers(mech, read=wread)
    controls["ATTRIBUTION"] = C.attribution(attributed, read=wread)
    controls["DIGITAL_TWIN"] = C.twin(comp.get("digital_twin") or {})
    reg = await sec.run("holdout_registry",
                        lambda: C.holdout_registry(conn), {})
    # THE REGISTERED STUDY'S PBO / DSR over the attribution rows read above
    # (pure; train + test events only, the holdout slice never read) -- the
    # plan's population is EVERY position: it declares no window
    # (a worker thread: up to 12,870 CSCV splits must not hold the API loop)
    import asyncio
    from . import research_registry as RREG
    mt = await asyncio.to_thread(RREG.measure, reg, every, fixtures)
    reg = dict(reg, measurement=mt, pbo_ok=mt.get("pbo_ok"),
               dsr_ok=mt.get("dsr_ok"),
               candidates_tested=max(int(reg.get("candidates_tested") or 0),
                                     int(mt.get("candidates_tested") or 0)))
    controls["SAMPLE_INTEGRITY"] = C.samples(comp.get("probability") or {},
                                             reg)
    controls["MULTIPLE_TESTING"] = C.multiple_testing(
        reg, read=C.population_read(aread, since=None, window_days=None))
    from .. import allie_capital as ALLIE
    controls["CAPACITY"] = C.capacity(
        await sec.run("capacity", lambda: capacity_points(conn), []),
        requested_usd=ALLIE.BOOK_CAP_USD)
    saved, false_cost, ksrc, krows = await sec.run(
        "karen", lambda: karen_counterfactuals(conn),
        (None, None, None, {}))
    controls["KAREN_VALUE"] = C.karen(saved, false_cost, source=ksrc,
                                      twin_rows=krows)
    wcls, wcensus = await sec.run("credential_classes",
                                  lambda: workers_boot_credentials(conn),
                                  (None, None))
    controls["CREDENTIAL_CLASSES"] = C.credentials({
        "api": C.credential_classes(), "workers": wcls})
    # the census is EVIDENCE beside the classes: one workers_boot read for
    # both, and the API's own census never raises out of the readout
    acensus, sec.timings["pmus_census_api"] = api_pmus_census()
    controls["CREDENTIAL_CLASSES"]["evidence"]["pmus_census"] = {
        "sportsassets-api": acensus, "sportsassets-workers": wcensus}
    rel = await sec.run("release_receipt", lambda: release_receipt(conn, sha),
                        None)
    controls["MIGRATION_INTEGRITY"] = C.migrations(
        await sec.run("migrations", lambda: C.applied_migrations(conn), {}),
        C.repo_migrations(),
        fresh_db_passed=(bool(rel["capital_critical_green"]) if rel
                         else None))
    controls["FEE_EVIDENCE"] = C.fee_control(
        await sec.run("fee_evidence", lambda: F.census(conn, now=now), {}),
        now=now)
    certs = await sec.run("certificates", lambda: certificate_census(conn),
                          {})
    controls["SETTLEMENT_CERTIFICATES"] = C.result(
        "SETTLEMENT_CERTIFICATES",
        C.GREEN if certs.get("CERTIFIED") and not certs.get("INVALIDATED")
        else C.RED if certs.get("INVALIDATED") else C.UNKNOWN,
        (["INVALIDATED_ALIASES:%d" % certs["INVALIDATED"]]
         if certs.get("INVALIDATED") else [])
        + ([] if certs.get("CERTIFIED") else ["NO_CERTIFIED_ALIAS"]),
        {"latest_by_status": certs})
    ex = await sec.run("canonical_exposure", lambda: X.census(
        conn, ACCOUNT_ID, sha=sha, at=now), {
            "eligible": False, "blockers": ["READ_UNAVAILABLE"],
            "receipts": []})
    controls["CANONICAL_EXPOSURE"] = C.result(
        "CANONICAL_EXPOSURE", C.GREEN if ex["eligible"] else C.RED,
        ex["blockers"], {k: v for k, v in ex.items() if k != "receipts"})
    release_ok = bool(rel) and not (C._j(rel.get("blockers")) or []) and \
        rel.get("release_sha") == sha
    controls["RELEASE"] = C.result(
        "RELEASE", C.GREEN if release_ok else C.UNKNOWN if not rel else C.RED,
        (["NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA"] if not rel else
         list(C._j(rel.get("blockers")) or [])),
        {"running_sha": sha, "receipt": {k: (str(v) if not isinstance(
            v, (bool, int, float, type(None), str)) else v)
            for k, v in (rel or {}).items()}})
    # a read that failed is never a vacuous pass: every control it feeds is
    # RED and names it
    for name, t in sec.timings.items():
        if t["ok"]:
            continue
        for ctl in SECTION_CONTROLS.get(name, ()):
            c = controls.get(ctl)
            if c is not None:
                controls[ctl] = C.result(
                    ctl, C.RED, list(c.get("blockers") or []) + [
                        "READ_UNAVAILABLE:%s" % name],
                    dict(c.get("evidence") or {}, read_failure=t))
    fr = vh["freshness"]
    # each metric's as_of is its SOURCE read's, never this render's clock
    src_at = comp.get("as_of") if isinstance(comp.get("as_of"),
                                              (int, float)) else None
    metrics = {
        "held_freshness": _metric(fr["held"]["rate"], as_of=src_at,
                                  source=fr["held"]["source"],
                                  status=fr["held"]["status"],
                                  num=fr["held"]["numerator"],
                                  den=fr["held"]["denominator"]),
        "priority_freshness": _metric(fr["priority"]["rate"], as_of=src_at,
                                      source=fr["priority"]["source"],
                                      status=fr["priority"]["status"],
                                      num=fr["priority"]["numerator"],
                                      den=fr["priority"]["denominator"]),
        "readiness_status": _metric(ready.get("status"), as_of=src_at,
                                    source="completion readiness",
                                    status=ready.get("status")),
    }
    controls["UI_TRUTH"] = ui_truth(metrics, now=now)
    # THE AUTHORITY, READ (never four literals)
    auth_small = small_live_authority(comp)
    auth_kalshi = await sec.run("authority", lambda: kalshi_live_money(conn),
                                {"state": "UNREAD:READ_FAILED"})
    auth_adriana = adriana_authority()
    authority_shadow = (auth_small["state"] == SHADOW
                        and auth_kalshi["state"] == NOT_ACTIVATED
                        and auth_adriana["state"] == SHADOW_ONLY)
    sw = runtime.get("shared_workers") or {}
    gov = ready.get("governors") or {}
    prob = comp.get("probability") or {}
    edge_ok = (prob.get("authority") == "BETTOR_RESIDUAL_ALLOWED"
               and (prob.get("improvement_lower_bound") or 0) > 0
               and "CASH" not in gov.values())
    inp = ReadinessInput(
        release_ok=release_ok,
        runtime_ok=("SHARED_WORKER_RSS_OR_UPTIME" not in str(
            ready.get("blockers")) and float(sw.get(
                "minutes_since_process_start") or 0) >= 60
            and float(sw.get("rss_highwater_fraction") or 1.0) < 0.85
            and not sw.get("universal_market_plane_started_here")),
        market_data_ok=controls["VENUE_HEALTH"]["status"] == C.GREEN,
        held_freshness_ok=fr["held"]["green"],
        priority_freshness_ok=fr["priority"]["green"],
        settlement_ok=bool((comp.get("settlement") or {}).get(
            "settlement_proven")) and not certs.get("INVALIDATED"),
        twin_ok=controls["DIGITAL_TWIN"]["status"] == C.GREEN,
        reconciliation_ok=controls["TRUTH_QUORUM"]["status"] == C.GREEN,
        positive_edge_ok=edge_ok,
        capacity_ok=controls["CAPACITY"]["status"] == C.GREEN,
        claim_exposure_ok=controls["CANONICAL_EXPOSURE"]["status"]
        == C.GREEN,
        profit_breakers_ok=controls["PROFIT_BREAKERS"]["status"] == C.GREEN,
        live_authority_still_shadow=authority_shadow,
        historical_paper_immutable=True)
    g = RTR.readiness_gate(inp)
    return {"version": VERSION, "as_of": now, "implementation_sha": sha,
            "status": g["status"], "checks": g["checks"],
            "blockers": list(g["blockers"]),
            "completion_readiness": {"status": ready.get("status"),
                                     "blockers": ready.get("blockers"),
                                     "governors": gov},
            "controls": controls,
            "mechanism_rows": len(mech),
            "exposure_receipts": ex["receipts"],
            "auto_activation": False,
            "authority": {"small_live": auth_small["state"],
                          "kalshi_live_money": auth_kalshi["state"],
                          "adriana": auth_adriana["state"],
                          # this read activates nothing: no code path of
                          # the interlock grants capital (auto_activation)
                          "capital_authority_granted": False},
            "authority_basis": {"small_live": auth_small,
                                "kalshi_live_money": auth_kalshi,
                                "adriana": auth_adriana},
            "historical_paper_immutable_basis": (
                "this read writes nothing; PAPER history is append-only by "
                "the ledger's own guards"),
            "read_timings": sec.timings,
            "completion": comp}


def capital_status(result: dict) -> str:
    return result.get("status") or "PAPER_SHADOW_ONLY"


def as_decimal(x):
    return Decimal(str(x))
