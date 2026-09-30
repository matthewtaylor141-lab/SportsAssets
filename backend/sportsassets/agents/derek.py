"""DEREK -- DISCOVERY AND INITIAL ENTRY, RUN ONCE PER SCHEDULED CYCLE.

`after_cycle(conn, cycle=<ext_pinnacle_loop.cycle() result>, now=...)` is the
hook the core stream calls after every scheduled cycle. It:

  0. records ONE NON-FUNDED RESEARCH OBSERVATION per valuation of the entry
     experiment in the cycle window, CALIBRATION_ONLY and ENTRY_DECISION
     alike (`derek_research`, migration 170) -- the internal entry model's
     training and evaluation records, collected whatever the venue's book
     currency, with no dependence on a model, an account or admission --
     then one bounded step of the one-time BACKFILL of older stored
     valuations, and once per UTC day the MODEL RUN: fit and evaluate the
     entry model on labelled research fixtures, or record the shortfall.
     It never promotes;

  1. records ONE Derek entry decision per candidate the entry lane evaluated
     this cycle -- every `external_valuations` row with record_purpose
     ENTRY_DECISION written in the cycle window that has no Derek decision yet
     (the funded gate may already have recorded the admitted ones; the same
     deterministic id makes that a no-op) -- each judged AS OF ITS OWN
     DECISION INSTANT from the row's recorded evidence, under
     DEREK_ENTRY_POLICY_V1 (`derek_policy`);
  2. records the coverage census of the Polymarket US catalogue (`coverage`);
  3. records latency: source-to-decision from the provider's own stamp, and
     decision-to-send / send-to-ack from funded intents when one exists --
     otherwise EMPTY, 'no funded order has been sent';
  4. links each decision into the agent registry (`registry.link_decision`)
     and heartbeats DEREK -- both GUARDED: a missing registry skips the link,
     never the decision.

WHICH KEYS OF THE CYCLE DICT ARE READ: `elapsed_s` (to widen the pick-up
window past a long cycle), and `evaluated`, `written`, `cycle_label` (echoed
on the result). All are optional: `{}` works. Everything else is read from
the rows the cycle wrote -- ENTRY_DECISION valuations decided in
(now - max(1800 s, elapsed_s + 60 s), now] with no Derek decision under this
policy version -- because the cycle dict carries counts and samples, not
valuation ids.

It never raises, never sends, never edits a row it did not write.
"""

from __future__ import annotations

import time
from typing import Any

from . import coverage as COV
from . import derek_policy as DP

#: Rows older than this are not picked up as "this cycle's", so a restarted
#: process does not re-judge history under a newer clock. Two cycles.
LOOKBACK_S = 2 * 900.0
MAX_PER_CYCLE = 200

CANDIDATES_SQL = """
    SELECT v.*
      FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.record_purpose = 'ENTRY_DECISION'
       AND v.decided_at > to_timestamp($2)
       AND v.decided_at <= to_timestamp($3)
       AND NOT EXISTS (SELECT 1 FROM derek_entry_decisions d
                        WHERE d.valuation_id = v.id
                          AND d.policy_version = $4)
     ORDER BY v.id
     LIMIT $5
"""


async def _regclass(conn, name) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                          # noqa: BLE001
        return False


async def _heartbeat(conn, *, state, activity, waiting_on=None,
                     dependencies=None, run=None, now=None) -> dict:
    try:
        from . import registry as REG
        return await REG.heartbeat(conn, DP.AGENT_ID, state=state,
                                   activity=activity, waiting_on=waiting_on,
                                   dependencies=dependencies, run=run,
                                   now=now)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "registry unavailable: %s"
                % type(exc).__name__}


async def send_latency(conn, cand: dict, *, decided_at: float) -> dict:
    """Decision-to-send and send-to-ack from the funded intent that executed
    this decision's contract, when one exists after the decision. EMPTY with
    the named reason otherwise."""
    empty = {"status": "EMPTY", "why": "no funded order has been sent"}
    if not cand.get("us_market_slug") or not await _regclass(
            conn, "bettor_funded_intents"):
        return empty
    try:
        r = await conn.fetchrow(
            "SELECT intent_id, state, extract(epoch FROM created_at) AS c, "
            "       extract(epoch FROM sent_at) AS s, "
            "       extract(epoch FROM resolved_at) AS r "
            "  FROM bettor_funded_intents "
            " WHERE kind = 'ENTRY' AND us_market_slug = $1 "
            "   AND order_intent = $2 AND created_at >= to_timestamp($3) "
            " ORDER BY created_at LIMIT 1",
            str(cand["us_market_slug"]), str(cand.get("side") or ""),
            float(decided_at))
    except Exception as exc:                                   # noqa: BLE001
        return {"status": "UNAVAILABLE", "why": type(exc).__name__}
    if r is None or r["s"] is None:
        return empty
    acked = r["state"] in ("ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED")
    return {"status": "OK", "intent_id": r["intent_id"],
            "decision_to_send_s": round(float(r["s"]) - decided_at, 3),
            "send_to_ack_s": (round(float(r["r"]) - float(r["s"]), 3)
                              if acked and r["r"] is not None else None),
            "ack_basis": ("resolved_at of an acknowledged intent"
                          if acked else "not acknowledged")}


async def after_cycle(conn, *, cycle: dict, now: float) -> dict:
    """THE CORE HOOK. Never raises; returns what it recorded and why not."""
    t0 = time.time()
    at = float(now)
    cyc = dict(cycle or {})
    out: dict[str, Any] = {"agent": DP.AGENT_ID,
                           "policy_version": DP.POLICY_VERSION, "at": at,
                           "cycle_evaluated": cyc.get("evaluated"),
                           "cycle_written": cyc.get("written"),
                           "cycle_label": cyc.get("cycle_label"),
                           "decisions_recorded": 0, "already_recorded": 0,
                           "verdicts": {}, "refusals": {}, "errors": {},
                           "decision_ids": []}
    await _heartbeat(conn, state="EVALUATING",
                     activity="recording entry decisions for the cycle",
                     now=at)
    # ── 0 · NON-FUNDED RESEARCH OBSERVATIONS (migration 170) ────────
    # FIRST, and dependent on nothing but the cycle's valuation rows: not on
    # Derek's decision tables, an approved model, an account, the submission
    # switches or trade admission. One frozen observation per valuation of
    # either purpose, bounded and idempotent; see `derek_research`.
    out["research_observations"] = await research_step(
        conn, now=at, elapsed_s=float(cyc.get("elapsed_s") or 0.0))
    # ── 0b · THE DAILY MODEL RUN: fit + evaluate, NEVER promote ──────
    out["model_run"] = await model_run_step(conn, now=at)
    if not await _regclass(conn, "derek_entry_decisions"):
        out.update(ok=False, refusal="DEREK_TABLES_ABSENT",
                   why="migration 153 is not applied here")
        await _heartbeat(conn, state="FAILED", activity=out["why"], now=at)
        return out
    # ── 1 · THE CYCLE'S EVALUATED CANDIDATES ────────────────────────
    elapsed = float(cyc.get("elapsed_s") or 0.0)
    since = at - max(LOOKBACK_S, elapsed + 60.0)
    rows: list = []
    if await _regclass(conn, "external_valuations"):
        from .. import bettor_external_shadow as ext
        try:
            rows = [dict(r) for r in await conn.fetch(
                CANDIDATES_SQL, ext.EXPERIMENT_ID, since, at + 1.0,
                DP.POLICY_VERSION, MAX_PER_CYCLE)]
        except Exception as exc:                               # noqa: BLE001
            out["errors"]["CANDIDATES_READ:" + type(exc).__name__] = 1
    out["candidates"] = len(rows)
    pol = await DP.policy_params(conn)
    out["params_source"] = pol.get("source")
    ap = await DP.approved_entry_model(conn) if rows else None
    auth = await DP.authority_checks(conn, now=at) if rows else []
    for row in rows:
        try:
            cand = DP.candidate_from_row(row)
            decided = float(cand.get("decided_at") or at)
            model = DP.model_estimate(ap, cand, at=decided)
            cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
            dec = DP.evaluate(cand, model=model, params=pol["params"],
                              authority=auth, catalogue_row=cat,
                              policy_version=pol["version"])
            did = DP.decision_id_for(valuation_id=cand["valuation_id"],
                                     policy_version=pol["version"])
            lat = DP.latency_for(cand, decided_at=decided)
            sent = await send_latency(conn, cand, decided_at=decided)
            if sent.get("status") == "OK":
                lat.update(decision_to_send_s=sent["decision_to_send_s"],
                           send_to_ack_s=sent["send_to_ack_s"], send=sent)
            else:
                lat["send"] = sent
            wrote = await DP.record(conn, did, cand, dec, latency=lat,
                                    decided_by=DP.DECIDED_BY_CYCLE)
        except Exception as exc:                               # noqa: BLE001
            k = "DECISION:" + type(exc).__name__
            out["errors"][k] = out["errors"].get(k, 0) + 1
            out.setdefault("error_sample", str(exc)[:200])
            continue
        if wrote is None:
            out["already_recorded"] += 1
            continue
        out["decisions_recorded"] += 1
        out["decision_ids"].append(did)
        out["verdicts"][dec["verdict"]] = \
            out["verdicts"].get(dec["verdict"], 0) + 1
        if dec["refusal"]:
            out["refusals"][dec["refusal"]] = \
                out["refusals"].get(dec["refusal"], 0) + 1
        await DP._link(conn, did, cand, verdict=dec["verdict"],
                       refusal=dec["refusal"], decided_at=decided, dec=dec)
    # ── 2 · THE COVERAGE CENSUS ─────────────────────────────────────
    try:
        cen = await COV.census(conn, now=at)
        out["census_id"] = await COV.record(conn, cen)
        out["census"] = {"ok": cen.get("ok"), "refusal": cen.get("refusal"),
                         "categories": (cen.get("categories") or {})}
    except Exception as exc:                                   # noqa: BLE001
        out["census"] = {"ok": False, "refusal": type(exc).__name__}
    # ── 3 · STATUS ──────────────────────────────────────────────────
    deps = blockers_from(out, model=ap)
    state = ("DECISION_RECORDED" if out["decisions_recorded"]
             else "WAITING_FOR_EVIDENCE" if deps else "IDLE")
    out["dependencies"] = deps
    out["elapsed_s"] = round(time.time() - t0, 3)
    out["ok"] = not out["errors"]
    await _heartbeat(
        conn, state=state,
        activity=("%d decision(s) recorded (%s)" % (
            out["decisions_recorded"], out["verdicts"] or "none")),
        waiting_on=[d["what"] for d in deps], dependencies=deps,
        run={"summary": dict({k: out.get(k) for k in (
            "decisions_recorded", "verdicts", "refusals", "candidates",
            "census_id", "elapsed_s")}, research_observations_recorded=(
                out.get("research_observations") or {}).get("recorded"))},
        now=at)
    return out


async def research_step(conn, *, now: float, elapsed_s: float = 0.0) -> dict:
    """`derek_research.observe_cycle`, guarded: never raises, never blocks
    the decisions or the census."""
    try:
        from . import derek_research as DR
        got = await DR.observe_cycle(conn, now=now, elapsed_s=elapsed_s)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": "RESEARCH_STEP_RAISED:%s"
                % type(exc).__name__}
    return {k: got.get(k) for k in (
        "ok", "refusal", "why", "candidates", "recorded", "already_recorded",
        "not_observed", "by_cohort", "model_frozen", "errors",
        "bound_reached", "limit", "window", "backfill")}


async def model_run_step(conn, *, now: float) -> dict:
    """`derek_research.daily_model_run`, guarded: once per UTC day it fits
    and evaluates the internal entry model on research observations, or
    records why not. It never promotes and never blocks the cycle."""
    try:
        from . import derek_research as DR
        got = await DR.daily_model_run(conn, now=now)
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "refusal": "MODEL_RUN_RAISED:%s"
                % type(exc).__name__, "error": str(exc)[:200]}
    return {k: got.get(k) for k in (
        "run_day", "ran", "already_ran", "run_id", "outcome", "counts",
        "fitted", "refusal", "promoted", "promotion")}


async def collection(conn, *, now: float) -> dict:
    """THE COLLECTION METRICS, IN FIXTURES, NOT ROWS.

    Repeated observations or decisions on one fixture are ONE independent
    example, so every count below is `count(DISTINCT fixture)` beside the row
    count. Each shortfall carries the owner's dependency class: accumulating
    independent prospective outcomes is ELAPSED_TIME; fixing a collection
    bottleneck or building the model is ENGINEERING. Never raises.
    """
    from .. import bettor_funded_model as FM
    from .. import bettor_pair_observations as PO
    at = float(now)
    out: dict[str, Any] = {"at": at, "unit": "DISTINCT_FIXTURES"}
    try:
        if await _regclass(conn, "us_premap"):
            out["fixtures_offered_by_the_catalogue"] = int(await conn.fetchval(
                "SELECT count(DISTINCT coalesce(event_slug, market_slug)) "
                "  FROM us_premap WHERE updated_at > to_timestamp($1)",
                at - PO.CATALOGUE_RESEEN_S) or 0)
        else:
            out["fixtures_offered_by_the_catalogue"] = None
            out["catalogue"] = "UNAVAILABLE: us_premap absent"
    except Exception as exc:                                   # noqa: BLE001
        out["catalogue"] = "UNAVAILABLE: %s" % type(exc).__name__
    obs = {"available": False}
    if await _regclass(conn, "bettor_pair_observation_attempts"):
        try:
            r = await conn.fetchrow(
                "SELECT count(*) AS rows, count(DISTINCT fixture) AS fx, "
                "       count(DISTINCT fixture) FILTER (WHERE attempted_at > "
                "             to_timestamp($1)) AS fx_24h "
                "  FROM bettor_pair_observation_attempts", at - 86400.0)
            obs["attempted"] = {"rows": int(r["rows"]),
                                "fixtures": int(r["fx"]),
                                "fixtures_24h": int(r["fx_24h"])}
            obs["attempt_refusals_7d"] = [dict(x) for x in await conn.fetch(
                "SELECT coalesce(refusal, outcome) AS reason, count(*) AS rows,"
                "       count(DISTINCT fixture) AS fixtures "
                "  FROM bettor_pair_observation_attempts "
                " WHERE attempted_at > to_timestamp($1) "
                "   AND outcome <> 'RECORDED' GROUP BY 1 ORDER BY 3 DESC, 1 "
                " LIMIT 20", at - 7 * 86400.0)]
            obs["available"] = True
        except Exception as exc:                               # noqa: BLE001
            obs["attempted"] = "UNAVAILABLE: %s" % type(exc).__name__
    if await _regclass(conn, "bettor_pair_observations"):
        try:
            has_adm = await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                " WHERE table_name='bettor_pair_observations' "
                "   AND column_name='admission_status'")
            adm = ("admission_status" if has_adm
                   else "'ADMITTED_BY_DISCOVERY'")
            obs["observed"] = dict(await conn.fetchrow(
                "SELECT count(*) AS rows, count(DISTINCT fixture) AS fixtures "
                "  FROM bettor_pair_observations"))
            obs["by_admission"] = [dict(x) for x in await conn.fetch(
                "SELECT %s AS admission_status, count(*) AS rows, "
                "       count(DISTINCT fixture) AS fixtures "
                "  FROM bettor_pair_observations GROUP BY 1 ORDER BY 1" % adm)]
            obs["by_label_status"] = [dict(x) for x in await conn.fetch(
                "SELECT label_status, count(*) AS rows, "
                "       count(DISTINCT fixture) AS fixtures "
                "  FROM bettor_pair_observations GROUP BY 1 ORDER BY 1")]
            elig = int(await conn.fetchval(
                "SELECT count(DISTINCT fixture) FROM bettor_pair_observations"
                " WHERE label_status = 'LABELLED' AND %s = "
                "       'ADMITTED_BY_DISCOVERY'" % adm) or 0)
            obs["settled_and_labelled_fixtures"] = int(await conn.fetchval(
                "SELECT count(DISTINCT fixture) FROM bettor_pair_observations"
                " WHERE label_status = 'LABELLED'") or 0)
            obs["training_eligible_fixtures"] = elig
            obs["training_eligibility"] = {
                "eligible_fixtures": elig,
                "needed_to_fit": FM.MIN_TRAIN_EVENTS,
                "shortfall": max(0, FM.MIN_TRAIN_EVENTS - elig),
                "rule": "admitted AND labelled; research-only rows never train"}
            obs["evaluation_eligibility"] = {
                "needed_prospective_fixtures": FM.MIN_EVALUATION_EVENTS,
                "rule": ("decided AND resolved after the candidate was "
                         "frozen; only elapsed time produces these")}
            obs["outstanding_evidence_by_reason"] = [dict(x) for x in
                                                     await conn.fetch(
                "SELECT label_status, coalesce(label_why, label_status) AS "
                "       reason, count(*) AS rows, "
                "       count(DISTINCT fixture) AS fixtures "
                "  FROM bettor_pair_observations "
                " WHERE label_status <> 'LABELLED' GROUP BY 1, 2 "
                " ORDER BY 4 DESC LIMIT 20")]
            days = [dict(x) for x in await conn.fetch(
                "SELECT to_char(first_at, 'YYYY-MM-DD') AS day, "
                "       count(*) AS new_fixtures FROM ("
                "   SELECT fixture, min(observed_at) AS first_at "
                "     FROM bettor_pair_observations GROUP BY fixture) f "
                " WHERE first_at > to_timestamp($1) GROUP BY 1 ORDER BY 1",
                at - 7 * 86400.0)]
            obs["new_independent_fixtures_by_day_7d"] = days
            obs["daily_rate_new_independent_fixtures_7d"] = round(
                sum(int(d["new_fixtures"]) for d in days) / 7.0, 3)
            obs["available"] = True
        except Exception as exc:                               # noqa: BLE001
            obs["observed"] = "UNAVAILABLE: %s" % type(exc).__name__
    out["pair_observations"] = obs
    ent: dict[str, Any] = {"available": False}
    if await _regclass(conn, "derek_entry_decisions"):
        try:
            r = await conn.fetchrow(
                "SELECT count(*) AS rows, count(DISTINCT fixture) AS fixtures,"
                "       count(*) FILTER (WHERE features IS NOT NULL) AS vec "
                "  FROM derek_entry_decisions")
            lab = await DP.labelled_entries(conn)
            ent = {"available": True, "decisions": int(r["rows"]),
                   "fixtures_decided": int(r["fixtures"]),
                   "decisions_with_a_model_vector": int(r["vec"]),
                   "labelled_decisions": lab.get("n"),
                   "labelled_fixtures": lab.get("n_events"),
                   "entry_model_training": {
                       "eligible_fixtures": lab.get("n_events"),
                       "needed_prospective_fixtures":
                           FM.MIN_EVALUATION_EVENTS,
                       "dependency": DP.DEP_ELAPSED_TIME,
                       "why": ("labels arrive only as fixtures resolve; the "
                               "model cannot be promoted before enough "
                               "independent prospective outcomes exist")}}
        except Exception as exc:                               # noqa: BLE001
            ent = {"available": False, "why": type(exc).__name__}
    out["entry_decisions"] = ent
    # THE NON-FUNDED RESEARCH OBSERVATIONS the entry model can now be fit on,
    # by price cohort -- never pooled -- with the exact minimums.
    res: dict[str, Any] = {"available": False}
    if await _regclass(conn, "derek_research_observations"):
        try:
            from . import derek_research as DR
            res = dict(await DR.summary(conn), available=True)
        except Exception as exc:                               # noqa: BLE001
            res = {"available": False, "why": type(exc).__name__}
    res["minimums"] = FM.qualification_minimums(FM.KEY_ENTRY_PAYOUT)
    res["dependency"] = DP.DEP_ELAPSED_TIME
    res["why"] = ("collected every cycle whatever the venue's book currency; "
                  "labels arrive as fixtures settle, so only elapsed time "
                  "produces training and prospective fixtures")
    out["research_observations"] = res
    out["dependencies"] = [
        {"what": "independent prospective outcomes (labels)",
         "class": DP.DEP_ELAPSED_TIME},
        {"what": "collection bottlenecks named in attempt_refusals_7d",
         "class": DP.DEP_ENGINEERING},
        {"what": "the internal entry model (fit, evaluate, promote)",
         "class": DP.DEP_ENGINEERING}]
    return out


def blockers_from(out: dict, *, model: dict | None) -> list:
    """What Derek is waiting on, each with the owner's dependency class."""
    deps = []
    if model is not None and not (model or {}).get("ok"):
        deps.append({"what": DP.R_NO_MODEL, "class": DP.DEP_ENGINEERING,
                     "why": ("no approved internal entry model: building it "
                             "(fit, prospective evaluation, promotion) is "
                             "engineering; its labels accrue with resolved "
                             "fixtures")})
    for code, n in sorted((out.get("refusals") or {}).items()):
        if code in (DP.R_BELOW, DP.R_DISAGREE, DP.R_NET, DP.R_BELOW_NET):
            continue
        cls = (DP.DEP_ENGINEERING if code in (DP.R_NO_MODEL,
                                              DP.R_MODEL_CANNOT_SCORE)
               else DP.classify_lane_code(code.split(":", 1)[-1])
               if code.startswith(DP.R_LANE) else DP.DEP_EVIDENCE)
        deps.append({"what": code, "class": cls, "count": n})
    return deps
