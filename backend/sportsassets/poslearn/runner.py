"""THE LEARNING RUNNER: one bounded, failure-isolated cycle every CYCLE_S in
the API process (armed from api/app.py's lifespan).

SHADOW / RESEARCH ONLY. It writes only its own poslearn_* tables (store.py)
and has no venue, order, sizing, threshold, allowlist, capital or
probability authority. POS_LEARN in {off, 0, false, no} is a kill switch
that keeps it from starting at all. Without migration 218 it idles.

BOUNDED. Every read has a window and a LIMIT; every component runs inside
its own savepoint with `SET LOCAL statement_timeout` and an asyncio timeout;
training (pure Python) runs off the event loop and at most every
TRAIN_EVERY_S; per-cycle capture is capped (MAX_CAPTURE).

FAILURE-ISOLATED. A component that raises or times out rolls back its own
savepoint, is recorded FAILED / TIMEOUT in poslearn_runs, and the later
components run without its output. Each opportunity's forecasts are written
in their own nested savepoint, so one refused row (an outcome that arrived
in between: the database refuses a late forecast) costs only that row.

ONE RUNNER AT A TIME: a session advisory lock (LOCK_KEY) per cycle.

ORDER: REGISTER -> EXPERIMENTS (setup / start / audit / stop / analyze) ->
CAPTURE -> FORECAST (models, edge confidence, avoidance, agent variants,
experiment assignment) -> OUTCOMES -> MODEL_TOURNAMENT -> AGENT_TOURNAMENT
-> PROMOTION (Karen, then Audrey) -> EDGE_CONFIDENCE -> AVOIDANCE.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .. import bettor_source_calibration as SC
from . import agents as AG
from . import audrey_review as AUD
from . import avoidance as AV
from . import common as C
from . import edge_confidence as EC
from . import experiments as EX
from . import features as FT
from . import karen_review as KAR
from . import models as M
from . import reads as R
from . import scoring as S
from . import store as ST

log = logging.getLogger(__name__)

VERSION = "POS_LEARN_RUNNER_V1"
ENV_KILL = "POS_LEARN"
LOCK_KEY = 0x504F534C              # 'POSL'
CYCLE_S = 900.0
FIRST_DELAY_S = 180.0
COMPONENT_TIMEOUT_S = 120.0
STATEMENT_TIMEOUT_MS = 20000
CAPTURE_LOOKBACK_S = 6 * 3600.0
HISTORY_DAYS = 180.0
TRAIN_ROWS = 5000
TRAIN_EVERY_S = 6 * 3600.0
MAX_CAPTURE = 500
SCORE_DAYS = 180.0
LIVE = ("ACTIVE_FORWARD", "AWAITING_FEATURES", "AWAITING_SOURCE",
        "AWAITING_INTERFACE")
RETIRE_POLICY = "APPROVED_POLICY_CHANGED"
RETIRE_INTERFACE = "INTERFACE_NOW_PRESENT"


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


async def _component(conn, run_id, name, fn, *, summary_of=None):
    started = time.time()
    value, status, error = None, "OK", None
    try:
        async with asyncio.timeout(COMPONENT_TIMEOUT_S):
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                value = await fn()
    except TimeoutError:
        status, error = "TIMEOUT", "component exceeded %ss" % (
            COMPONENT_TIMEOUT_S)
        value = None
    except Exception as exc:                                   # noqa: BLE001
        status, error = "FAILED", "%s: %s" % (type(exc).__name__,
                                              str(exc)[:300])
        value = None
        log.warning("pos learn component %s failed", name, exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component=name, started_at=started,
                finished_at=time.time(), status=status, error=error,
                summary=(summary_of(value) if (summary_of and value
                                               is not None) else {}))
    except Exception:                                          # noqa: BLE001
        log.warning("pos learn could not record %s", name, exc_info=True)
    return value, status


# ═════════════════════════════════════════════════════════════════════
# CONTEXT: history, training rows, as-of features
# ═════════════════════════════════════════════════════════════════════

def _regime_at(regimes, t):
    best = None
    for at, rec in regimes:
        if at <= t:
            best = rec
        else:
            break
    return best


def _first_per_unit(rows):
    best = {}
    for r in sorted(rows, key=lambda r: (r["decided_at"], r["id"])):
        best.setdefault(r["event_key"], r)
    return list(best.values())


def _history_records(resolved):
    out = []
    for r in resolved:
        if SC.classify(r) != SC.RESOLVED:
            continue
        p = C.num(r.get("probability"))
        out.append({"outcome_at": r["outcome_at"], "p": p,
                    "o": int(r["outcome"]), "sport": str(
                        r.get("sport_family") or "UNKNOWN"),
                    "probability_band": FT.prob_band(p)})
    return out


async def _featurize(conn, rows, *, history_rows, regimes, eddie_why=None,
                     eddie=None):
    """As-of features for valuation rows (ascending decided_at)."""
    rows = sorted(rows, key=lambda r: (r["decided_at"], r["id"]))
    if not rows:
        return []
    slugs = [r.get("us_market_slug") for r in rows]
    books = await R.books_asof(conn, [(r.get("us_market_slug"),
                                       r["decided_at"]) for r in rows],
                               max_age_s=FT.BOOK_MAX_AGE_S)
    qs = await R.quotes(conn, slugs,
                        since=min(r["decided_at"] for r in rows)
                        - FT.QUOTE_WINDOW_S,
                        until=max(r["decided_at"] for r in rows),
                        limit=100000)
    pm = await R.premap(conn, slugs)
    hist = FT.HistoryIndex(history_rows)
    out = []
    for r in rows:
        t = float(r["decided_at"])
        key = (r.get("us_market_slug"), r.get("payout_event"))
        prior = [(a, p) for a, p in qs.get(key, []) if a < t]
        est = None
        if eddie is not None:
            cand = [e for e in eddie.get(r.get("us_market_slug"), [])
                    if (e.get("at") or 0) <= t]
            est = cand[-1] if cand else {"why": "NO_EDDIE_ESTIMATE_AS_OF_T"}
        elif eddie_why:
            est = {"why": eddie_why}
        f, un, meta = FT.build(
            r, t=t, book=books.get((r.get("us_market_slug"), t)),
            premap=pm.get(r.get("us_market_slug")), quotes=prior,
            regime=_regime_at(regimes, t), history=hist, eddie=est)
        out.append((r, f, un, meta))
    return out


async def training_rows(conn, *, now) -> list:
    resolved = await R.resolved_valuations(
        conn, since=now - HISTORY_DAYS * 86400.0, until=now,
        limit=TRAIN_ROWS * 4)
    hist = _history_records(resolved)
    usable = [r for r in resolved if SC.classify(r) == SC.RESOLVED]
    firsts = _first_per_unit(usable)[-TRAIN_ROWS:]
    regimes = await R.regimes(conn, since=now - HISTORY_DAYS * 86400.0)
    feats = await _featurize(conn, firsts, history_rows=hist,
                             regimes=regimes,
                             eddie_why="TRAINING_ROWS_PREDATE_EDDIE")
    out = []
    for r, f, un, meta in feats:
        out.append({"p": C.num(r["probability"]), "p_reference": C.num(
            r["probability"]), "o": int(r["outcome"]), "features": f,
            "sport": meta["sport"], "league": meta["league"],
            "market": meta["market"], "live_state": meta["live_state"],
            "sport_key": M.sport_key(meta["sport"], meta["league"]),
            "decided_at": r["decided_at"], "outcome_at": r["outcome_at"],
            "price": meta["price"], "fee": meta["fee"]})
    return out


# ═════════════════════════════════════════════════════════════════════
# REGISTER
# ═════════════════════════════════════════════════════════════════════

def _latest(regs, subject):
    mine = [r for r in regs if r["subject_id"] == subject]
    return max(mine, key=lambda r: r["version"]) if mine else None


def _may_register(latest) -> bool:
    """A subject is (re)registered only when it never was, or its last
    version was retired for a policy / interface change -- NEVER after a
    forward failure (no retry-until-it-passes)."""
    if latest is None:
        return True
    if latest["status"] in LIVE:
        return False
    return str(latest.get("status_reason") or "").startswith(
        (RETIRE_POLICY, RETIRE_INTERFACE))


async def register(conn, *, now, force_training=False) -> dict:
    thr, basis = await R.approved_threshold(conn)
    regs = await R.registrations(conn)
    eddie_present = bool(await conn.fetchval(
        "SELECT to_regclass('%s') IS NOT NULL" % AG.EDDIE_TABLE))
    done, plans = [], {}
    # retire live registrations whose declared threshold is no longer the
    # approved one (a NEW version is registered below, before new data)
    for r in regs:
        if r["status"] != "ACTIVE_FORWARD" or r["kind"] not in (
                "MODEL", "AGENT_VARIANT"):
            continue
        doc = r["document"]
        rule = doc.get("decision_rule") or {}
        base_thr = rule.get("threshold_pp", (doc.get("parameters") or {})
                            .get("threshold_pp"))
        if r["subject_id"] == "DEREK_CHALLENGER_A" and base_thr is not None:
            base_thr = float(base_thr) - AG.THRESHOLD_STEP_PP
        if base_thr is not None and abs(float(base_thr) - thr) > 1e-9:
            await ST.set_registration_status(
                conn, r["registration_id"], "RETIRED",
                "%s_TO_%.2fPP" % (RETIRE_POLICY, thr))
            r["status"], r["status_reason"] = "RETIRED", RETIRE_POLICY
    for r in regs:
        if (r["status"] == "AWAITING_INTERFACE" and eddie_present):
            await ST.set_registration_status(conn, r["registration_id"],
                                             "RETIRED", RETIRE_INTERFACE)
            r["status"], r["status_reason"] = "RETIRED", RETIRE_INTERFACE

    def version_of(subject):
        lt = _latest(regs, subject)
        return 1 if lt is None else lt["version"] + 1

    # ── agent variants and the champion: declared rules, no training
    for subject in AG.subjects():
        lt = _latest(regs, subject)
        if not _may_register(lt):
            continue
        awaiting = ("AWAITING_INTERFACE" if subject.startswith("EDDIE")
                    and not eddie_present else None)
        doc = AG.document(subject, version=version_of(subject),
                          threshold_pp=thr, threshold_basis=basis,
                          awaiting=awaiting)
        await ST.insert_registration(conn, doc, registered_at=now,
                                     status=awaiting or "ACTIVE_FORWARD",
                                     status_reason=awaiting and doc[
                                         "placeholder"]["why"])
        done.append(subject)
    need_training = []
    for subject, kind, placeholder in M.SPECS:
        lt = _latest(regs, subject)
        if not _may_register(lt):
            continue
        if kind == "RAW" or placeholder:
            trained = M.train(subject, kind, [], now=now)
            doc = M.document(subject, kind, trained if kind == "RAW"
                             else {}, version=version_of(subject),
                             threshold_pp=thr, threshold_basis=basis,
                             placeholder=placeholder)
            await ST.insert_registration(
                conn, doc, registered_at=now,
                status=placeholder or "ACTIVE_FORWARD",
                status_reason=placeholder and M.PLACEHOLDER_WHY[placeholder])
            done.append(subject)
        else:
            need_training.append((subject, kind))
    meta_needed = [s for s in (EC.SUBJECT, AV.SUBJECT)
                   if _may_register(_latest(regs, s))]
    last = await R.last_run(conn, "REGISTER")
    last_try = ((last or {}).get("summary") or {}).get(
        "training_attempted_at")
    trained_info = {}
    if (need_training or meta_needed) and (
            force_training or last_try is None
            or now - float(last_try) >= TRAIN_EVERY_S):
        rows = await training_rows(conn, now=now)
        for subject, kind in need_training:
            got = await asyncio.to_thread(M.train, subject, kind, rows,
                                          now=now)
            trained_info[subject] = got.get("why") or "TRAINED_N_%s" % got.get(
                "n")
            if not got["trainable"]:
                plans[subject] = got["why"]
                continue
            doc = M.document(subject, kind, got,
                             version=version_of(subject), threshold_pp=thr,
                             threshold_basis=basis)
            await ST.insert_registration(conn, doc, registered_at=now,
                                         status="ACTIVE_FORWARD")
            done.append(subject)
        if EC.SUBJECT in meta_needed:
            got = await asyncio.to_thread(EC.train, rows, now=now)
            if got["trainable"]:
                await ST.insert_registration(
                    conn, EC.document(got, version=version_of(EC.SUBJECT)),
                    registered_at=now, status="ACTIVE_FORWARD")
                done.append(EC.SUBJECT)
            else:
                plans[EC.SUBJECT] = got["why"]
        if AV.SUBJECT in meta_needed:
            got = await asyncio.to_thread(AV.train, rows, now=now,
                                          threshold_pp=thr)
            if got["trainable"]:
                await ST.insert_registration(
                    conn, AV.document(got, version=version_of(AV.SUBJECT)),
                    registered_at=now, status="ACTIVE_FORWARD")
                done.append(AV.SUBJECT)
            else:
                plans[AV.SUBJECT] = got["why"]
        attempted = now
        training_n = len(rows)
    else:
        attempted = last_try
        training_n = None
        for subject, _ in need_training:
            plans[subject] = "AWAITING_NEXT_TRAINING_ATTEMPT"
        for s in meta_needed:
            plans[s] = "AWAITING_NEXT_TRAINING_ATTEMPT"
    return {"registered": done, "not_registered": plans,
            "threshold_pp": thr, "threshold_basis": basis,
            "training_attempted_at": attempted, "training_rows": training_n,
            "eddie_present": eddie_present}


# ═════════════════════════════════════════════════════════════════════
# CAPTURE
# ═════════════════════════════════════════════════════════════════════

async def capture(conn, *, now) -> dict:
    rows = await R.pending_valuations(conn, since=now - CAPTURE_LOOKBACK_S,
                                      until=now, limit=MAX_CAPTURE * 8)
    rows = [r for r in rows if SC.classify(r) == SC.UNRESOLVED]
    firsts = _first_per_unit(rows)
    have = await R.captured_units(conn, [r["event_key"] for r in firsts])
    todo = [r for r in firsts if r["event_key"] not in have][:MAX_CAPTURE]
    # SURVIVORSHIP DISCLOSURE: fixtures in the window whose outcome was
    # already known before any cycle saw them can never be captured; count
    # them instead of letting them vanish
    gone = await R.resolved_valuations(conn, since=now - CAPTURE_LOOKBACK_S,
                                       until=now, limit=MAX_CAPTURE * 8)
    gone_units = {r["event_key"] for r in gone}
    seen = await R.captured_units(conn, list(gone_units))
    missed = len(gone_units - seen - {r["event_key"] for r in firsts})
    if not todo:
        return {"captured": [], "candidates": len(firsts),
                "missed_outcome_known_before_capture": missed}
    resolved = await R.resolved_valuations(
        conn, since=now - HISTORY_DAYS * 86400.0, until=now)
    regimes = await R.regimes(conn, since=now - 7 * 86400.0)
    eddie, eddie_why = await R.eddie_estimates(
        conn, [r.get("us_market_slug") for r in todo])
    feats = await _featurize(conn, todo, history_rows=_history_records(
        resolved), regimes=regimes, eddie=eddie if not eddie_why else None,
        eddie_why=eddie_why)
    got = []
    for r, f, un, meta in feats:
        oid = "opp:ev:%d" % int(r["id"])
        row = {"opportunity_id": oid, "source_id": int(r["id"]),
               "unit": r["event_key"], "opportunity_at": r["decided_at"],
               "features_as_of": r["decided_at"],
               "captured_at": max(now, r["decided_at"]),
               "us_market_slug": r.get("us_market_slug"),
               "sport": meta["sport"], "league": meta["league"],
               "market": meta["market"], "live_state": meta["live_state"],
               "record_purpose": r.get("record_purpose"),
               "p_reference": C.num(r.get("probability")),
               "price": meta["price"], "price_basis": meta["price_basis"],
               "fee": meta["fee"], "features": f, "unavailable": un}
        try:
            async with conn.transaction():
                ok = await ST.insert_opportunity(conn, row)
        except Exception:                                      # noqa: BLE001
            log.info("pos learn: opportunity %s refused", oid, exc_info=True)
            continue
        if ok:
            got.append(oid)
    return {"captured": got, "candidates": len(firsts),
            "missed_outcome_known_before_capture": missed}


# ═════════════════════════════════════════════════════════════════════
# FORECAST
# ═════════════════════════════════════════════════════════════════════

async def forecast(conn, *, now, opportunity_ids) -> dict:
    if not opportunity_ids:
        return {"opportunities": 0, "forecasts": 0, "refused": 0,
                "assigned": 0}
    opps = await R.opportunities(conn, since=now - 30 * 86400.0)
    batch = [opps[i] for i in opportunity_ids if i in opps]
    regs = [r for r in await R.registrations(conn)
            if r["status"] == "ACTIVE_FORWARD"]
    by_subject = {r["subject_id"]: r for r in regs}
    thr = None
    if M.CHAMPION in by_subject:
        thr = by_subject[M.CHAMPION]["document"]["decision_rule"][
            "threshold_pp"]
    if thr is None:
        thr, _ = await R.approved_threshold(conn)
    eddie, eddie_why = await R.eddie_estimates(
        conn, [o.get("us_market_slug") for o in batch])
    exps = [e for e in await R.experiments(conn) if e["status"] == "RUNNING"]
    plan: dict = {o["opportunity_id"]: [] for o in batch}
    ec_by_opp, av_by_opp = {}, {}
    for o in batch:
        oid = o["opportunity_id"]
        for r in regs:
            if o["opportunity_at"] < r["registered_at"]:
                continue                     # not offered: predates it
            doc = r["document"]
            if r["kind"] == "MODEL":
                plan[oid].append((r, M.forecast(doc, o)))
            elif r["subject_id"] == EC.SUBJECT:
                fc = EC.forecast(doc, o)
                ec_by_opp[oid] = fc.get("edge_confidence")
                plan[oid].append((r, fc))
            elif r["subject_id"] == AV.SUBJECT:
                fc = AV.forecast(doc, o)
                av_by_opp[oid] = fc.get("avoidance_level")
                plan[oid].append((r, fc))
        for r in regs:
            if r["kind"] != "AGENT_VARIANT" or \
                    o["opportunity_at"] < r["registered_at"]:
                continue
            agent = r["document"]["agent"]
            if agent == "DEREK":
                plan[oid].append((r, AG.derek(
                    r["document"], o, avoidance_level=av_by_opp.get(oid))))
            elif agent == "XAVIER":
                plan[oid].append((r, AG.xavier(r["document"], o,
                                               threshold_pp=thr)))
            elif agent == "EDDIE":
                ests = [e for e in (eddie or {}).get(
                    o.get("us_market_slug"), [])
                    if (e.get("at") or 0) <= o["opportunity_at"]]
                plan[oid].append((r, AG.eddie(
                    r["document"], o, estimate=ests[-1] if ests else None,
                    threshold_pp=thr)))
    for r in regs:
        if r["kind"] == "AGENT_VARIANT" and \
                r["document"]["agent"] == "ALLOCATOR":
            offered = [o for o in batch
                       if o["opportunity_at"] >= r["registered_at"]]
            for oid, fc in AG.allocate(r["document"], offered,
                                       ec_by_opp=ec_by_opp,
                                       threshold_pp=thr).items():
                plan[oid].append((r, fc))
    written = refused = assigned = 0
    for o in batch:
        oid = o["opportunity_id"]
        try:
            async with conn.transaction():
                for r, fc in plan[oid]:
                    await ST.insert_forecast(conn, r["registration_id"], oid,
                                             fc, predicted_at=max(
                                                 now, o["opportunity_at"]))
                    written += 1
                for e in exps:
                    if not (e["start_epoch"] <= now < e["stop_epoch"]):
                        continue
                    d = EX.draw(e["seed"], e["experiment_id"], o["unit"])
                    await ST.insert_assignment(
                        conn, e["experiment_id"], unit_id=o["unit"],
                        opportunity_id=oid, arm=EX.arm_for(d, e["arms"]),
                        draw=d, assigned_at=now)
                    assigned += 1
        except Exception:                                      # noqa: BLE001
            refused += 1
            log.info("pos learn: forecasts for %s refused", oid,
                     exc_info=True)
    return {"opportunities": len(batch), "forecasts": written,
            "refused": refused, "assigned": assigned,
            "eddie": eddie_why or "PRESENT"}


# ═════════════════════════════════════════════════════════════════════
# OUTCOMES
# ═════════════════════════════════════════════════════════════════════

async def outcomes(conn, *, now) -> dict:
    opps = await R.opportunities(conn, since=now - SCORE_DAYS * 86400.0)
    have = await R.outcomes(conn, list(opps))
    pending = [o for i, o in opps.items() if i not in have]
    src = await R.source_outcomes(conn, [o["source_id"] for o in pending])
    n = {"RESOLVED": 0, "VOID": 0, "UNVERIFIED": 0, "PENDING": 0}
    for o in pending:
        s = src.get(int(o["source_id"]))
        cls = SC.classify(s) if s else SC.UNRESOLVED
        if cls == SC.RESOLVED:
            await ST.insert_outcome(conn, o["opportunity_id"],
                                    outcome_class="RESOLVED",
                                    outcome=int(s["outcome"]),
                                    basis=s.get("outcome_basis"),
                                    outcome_at=s.get("outcome_at"))
            n["RESOLVED"] += 1
        elif cls == SC.VOID:
            await ST.insert_outcome(conn, o["opportunity_id"],
                                    outcome_class="VOID", outcome=None,
                                    basis=s.get("outcome_basis"),
                                    outcome_at=s.get("outcome_at") or now)
            n["VOID"] += 1
        elif cls == SC.UNVERIFIED:
            await ST.insert_outcome(conn, o["opportunity_id"],
                                    outcome_class="UNVERIFIED", outcome=None,
                                    basis=s.get("outcome_basis"),
                                    outcome_at=s.get("outcome_at"))
            n["UNVERIFIED"] += 1
        else:
            n["PENDING"] += 1
    # experiment outcomes: the arm's own recorded decision, after its
    # persisted assignment
    exp_n = 0
    allout = await R.outcomes(conn, list(opps))
    regs = {r["registration_id"]: r for r in await R.registrations(conn)}
    for e in await R.experiments(conn):
        if e["status"] not in ("RUNNING", "STOPPED"):
            continue
        done = await R.experiment_outcomes(conn, e["experiment_id"])
        asg = [a for a in await R.assignments(conn, e["experiment_id"])
               if a["unit_id"] not in done and a["opportunity_id"] in allout]
        if not asg:
            continue
        fcs = await R.forecasts(conn, [a["opportunity_id"] for a in asg])
        pv = e["policy_versions"]
        for a in asg:
            rid = pv.get(a["arm"])
            mine = [f for f in fcs if f["opportunity_id"] ==
                    a["opportunity_id"] and f["registration_id"] == rid]
            action = mine[0]["action"] if mine else "ABSTAIN"
            op = opps.get(a["opportunity_id"])
            if op is None or regs.get(rid) is None:
                continue
            out = allout[a["opportunity_id"]]
            await ST.insert_experiment_outcome(
                conn, e["experiment_id"], a["unit_id"],
                EX.unit_metric(action, op, out),
                outcome_at=out.get("outcome_at"))
            exp_n += 1
    n["experiment_outcomes"] = exp_n
    return n


# ═════════════════════════════════════════════════════════════════════
# TOURNAMENTS
# ═════════════════════════════════════════════════════════════════════

async def _scoring_context(conn, *, now):
    opps = await R.opportunities(conn, since=now - SCORE_DAYS * 86400.0)
    outs = await R.outcomes(conn, list(opps))
    fcs = await R.forecasts(conn, list(opps))
    regs = await R.registrations(conn)
    by_reg: dict = {}
    for f in fcs:
        by_reg.setdefault(f["registration_id"], []).append(f)
    return opps, outs, by_reg, regs


def _regime_of(op):
    return (op.get("features") or {}).get("regime") or "UNKNOWN"


async def model_tournament(conn, *, now, run_id, plans=None) -> dict:
    opps, outs, by_reg, regs = await _scoring_context(conn, now=now)
    models = [r for r in regs if r["kind"] == "MODEL"]
    champ_fc = {}
    for r in models:
        if r["subject_id"] == M.CHAMPION:
            for f in by_reg.get(r["registration_id"], []):
                champ_fc[f["opportunity_id"]] = f
    reports, candidates = [], {}
    for r in models:
        rep = S.model_report(r, by_reg.get(r["registration_id"], []), opps,
                             outs)
        if r["document"]["role"] == "CHALLENGER" and \
                r["status"] in ("ACTIVE_FORWARD", "FAILED_FORWARD"):
            pv = S.paired(r, champ_fc, by_reg.get(r["registration_id"], []),
                          opps, outs)
            rep["versus_champion"] = pv
            if pv["verdict"] in ("CRITERIA_MET", "FAILED"):
                pairs = _model_pairs(r, champ_fc, by_reg, opps, outs)
                candidates[r["registration_id"]] = {
                    "registration": r, "verdict": pv, "pairs": pairs,
                    "base_subject": M.CHAMPION}
            if pv["verdict"] == "FAILED" and r["status"] == "ACTIVE_FORWARD":
                await ST.set_registration_status(
                    conn, r["registration_id"], "FAILED_FORWARD",
                    "PREDECLARED_FAILURE_THRESHOLD_MET")
        rep["document_summary"] = {
            k: r["document"].get(k) for k in (
                "training_window", "features", "validation_method",
                "minimum_sample", "promotion_threshold", "failure_threshold",
                "hypothesis_family", "family_size", "placeholder",
                "decision_rule")}
        reports.append(rep)
    for rid, c in candidates.items():
        if c["verdict"]["verdict"] == "CRITERIA_MET":
            await ST.insert_step(conn, rid, step="CRITERIA_MET",
                                 actor=C.RUNNER_ACTOR, outcome="CRITERIA_MET",
                                 evidence=c["verdict"])
    registered = {r["subject_id"] for r in models}
    payload = C.envelope(
        version=VERSION, computed_at=now, champion=M.CHAMPION,
        family=M.FAMILY, family_size=M.FAMILY_SIZE,
        models=reports,
        not_registered={s: (plans or {}).get(s, "NOT_YET_REGISTERED")
                        for s, _, _ in M.SPECS if s not in registered},
        feature_catalog=FT.catalog_status(list(opps.values())),
        opportunities=len(opps), resolved=sum(
            1 for o in outs.values() if o["outcome_class"] == "RESOLVED"),
        champion_rule="the champion stays champion until a challenger "
                      "meets its PREDECLARED criteria, survives Karen, "
                      "passes Audrey and a human approves")
    await ST.save_snapshot(conn, run_id=run_id, component="MODEL_TOURNAMENT",
                           payload=payload, now=now)
    return {"payload": payload, "candidates": candidates}


def _model_pairs(r, champ_fc, by_reg, opps, outs):
    n_min = int(r["document"]["minimum_sample"])
    rows = []
    for f in by_reg.get(r["registration_id"], []):
        op = opps.get(f["opportunity_id"])
        ch = champ_fc.get(f["opportunity_id"])
        o = outs.get(f["opportunity_id"])
        if (not op or op["opportunity_at"] < r["registered_at"]
                or f.get("probability") is None or not ch
                or ch.get("probability") is None or not o
                or o["outcome_class"] != "RESOLVED"):
            continue
        pc, px, y = float(ch["probability"]), float(f["probability"]), \
            int(o["o"])
        rows.append({"at": op["opportunity_at"], "id": f["opportunity_id"],
                     "diff": SC.log_loss([(pc, y)]) - SC.log_loss([(px, y)]),
                     "sport": op.get("sport"), "regime": _regime_of(op)})
    rows.sort(key=lambda x: (x["at"], x["id"]))
    return rows[:n_min]


async def agent_tournament(conn, *, now, run_id) -> dict:
    opps, outs, by_reg, regs = await _scoring_context(conn, now=now)
    variants = [r for r in regs if r["kind"] == "AGENT_VARIANT"]
    thr, _ = await R.approved_threshold(conn)
    paths: dict = {}
    xav_ids = {f["opportunity_id"] for r in variants
               if r["document"]["agent"] == "XAVIER"
               for f in by_reg.get(r["registration_id"], [])
               if f["action"] in ("TAKE_PROFIT_PLAN", "EXIT_ON_EDGE_LOSS_PLAN")
               and f["opportunity_id"] in outs}
    for oid in sorted(xav_ids)[:300]:
        op = opps.get(oid)
        if not op or not op.get("us_market_slug"):
            continue
        end = outs[oid].get("outcome_at") or now
        books = await R.books_between(conn, op["us_market_slug"],
                                      since=op["opportunity_at"], until=end)
        qs = await R.quotes(conn, [op["us_market_slug"]],
                            since=op["opportunity_at"] + 1e-6, until=end,
                            limit=500)
        key = (op["us_market_slug"],
               (op.get("features") or {}).get("payout_event") or None)
        paths[oid] = {"books": books, "quotes": [
            {"at": a, "p": p} for a, p in qs.get(key, [])]}
    per_variant, candidates = {}, {}
    rows_by: dict = {}
    for r in variants:
        rows = []
        for f in by_reg.get(r["registration_id"], []):
            op, o = opps.get(f["opportunity_id"]), outs.get(
                f["opportunity_id"])
            if not op or not o or o["outcome_class"] not in ("RESOLVED",
                                                              "VOID"):
                continue
            pr = AG.pnl_row(r["subject_id"], f, op, o,
                            paths.get(f["opportunity_id"]),
                            threshold_pp=thr)
            if pr is not None:
                pr["id"] = f["opportunity_id"]
                pr["opp_at"] = op["opportunity_at"]
                pr["regime"] = _regime_of(op)
                rows.append(pr)
        rows_by[r["registration_id"]] = rows
        per_variant[r["registration_id"]] = AG.summarize(rows)
    reports = []
    for r in variants:
        doc = r["document"]
        rep = {"registration_id": r["registration_id"],
               "subject_id": r["subject_id"], "agent": doc["agent"],
               "role": doc["role"], "status": r["status"],
               "status_reason": r.get("status_reason"),
               "policy": doc.get("policy"), "sha256": r.get("sha256"),
               "registered_at": r["registered_at"],
               "offered": len(by_reg.get(r["registration_id"], [])),
               "metrics": per_variant[r["registration_id"]]}
        if doc["role"] == "CHALLENGER" and r["status"] in (
                "ACTIVE_FORWARD", "FAILED_FORWARD"):
            base = next((b for b in variants
                         if b["subject_id"] == "%s_V1" % doc["agent"]
                         and b["status"] == "ACTIVE_FORWARD"), None)
            if base is None:
                rep["versus_v1"] = {"verdict": "NO_ACTIVE_V1"}
            else:
                bmap = {x["id"]: x for x in rows_by[base["registration_id"]]}
                pairs = []
                for x in rows_by[r["registration_id"]]:
                    if x["opp_at"] < r["registered_at"] or x["id"] not in \
                            bmap:
                        continue
                    pairs.append({"at": x["opp_at"], "id": x["id"],
                                  "diff": x["pnl"] - bmap[x["id"]]["pnl"],
                                  "sport": x["sport"],
                                  "regime": x["regime"]})
                pairs.sort(key=lambda p: (p["at"], p["id"]))
                n_min = int(doc["minimum_sample"])
                sample = pairs[:n_min]
                cs = AG.summarize([x for x in rows_by[r["registration_id"]]
                                   if x["id"] in {p["id"] for p in sample}])
                bs = AG.summarize([bmap[p["id"]] for p in sample])
                pv = AG.paired_verdict(
                    [p["diff"] for p in pairs], min_sample=n_min,
                    family_size=int(doc["family_size"]),
                    dd_challenger=cs.get("max_drawdown_usd"),
                    dd_base=bs.get("max_drawdown_usd"))
                rep["versus_v1"] = pv
                if pv["verdict"] in ("CRITERIA_MET", "FAILED"):
                    candidates[r["registration_id"]] = {
                        "registration": r, "verdict": pv, "pairs": sample,
                        "base_subject": base["subject_id"]}
                if pv["verdict"] == "FAILED" and \
                        r["status"] == "ACTIVE_FORWARD":
                    await ST.set_registration_status(
                        conn, r["registration_id"], "FAILED_FORWARD",
                        "PREDECLARED_FAILURE_THRESHOLD_MET")
        reports.append(rep)
    for rid, c in candidates.items():
        if c["verdict"]["verdict"] == "CRITERIA_MET":
            await ST.insert_step(conn, rid, step="CRITERIA_MET",
                                 actor=C.RUNNER_ACTOR, outcome="CRITERIA_MET",
                                 evidence=c["verdict"])
    payload = C.envelope(
        version=VERSION, computed_at=now, family=AG.FAMILY,
        family_size=AG.FAMILY_SIZE, variants=reports,
        same_opportunities=("every variant is offered every opportunity "
                            "captured at or after its registration; XAVIER, "
                            "ALLOCATOR and EDDIE act on the one shared entry "
                            "set DEREK_V1's rule selects"),
        no_self_promotion="Audrey evaluates, Karen challenges, a human "
                          "promotes; no variant promotes itself")
    await ST.save_snapshot(conn, run_id=run_id, component="AGENT_TOURNAMENT",
                           payload=payload, now=now)
    return {"payload": payload, "candidates": candidates}


async def promotion(conn, *, candidates: dict) -> dict:
    steps = await R.steps(conn)
    have = {(s["registration_id"], s["step"]): s for s in steps}
    did = []
    for rid, c in candidates.items():
        if (rid, "CRITERIA_MET") not in have and c["verdict"].get(
                "verdict") != "CRITERIA_MET":
            continue
        if (rid, "CLOSED") in have:
            continue
        r = c["registration"]
        if (rid, "KAREN_CHALLENGE") not in have:
            got = KAR.promotion_challenge(c["pairs"], document=r["document"],
                                          registered_at=r["registered_at"])
            await ST.insert_step(conn, rid, step="KAREN_CHALLENGE",
                                 actor="KAREN", outcome=got["outcome"],
                                 evidence=got)
            did.append((rid, "KAREN_CHALLENGE", got["outcome"]))
            if got["outcome"] == "BLOCKED":
                continue
        elif have[(rid, "KAREN_CHALLENGE")]["outcome"] == "BLOCKED":
            continue
        if (rid, "AUDREY_EVALUATION") not in have:
            got = await AUD.evaluate_candidate(
                conn, registration=r, recorded=c["verdict"],
                base_subject=c["base_subject"])
            await ST.insert_step(conn, rid, step="AUDREY_EVALUATION",
                                 actor="AUDREY", outcome=got["outcome"],
                                 evidence=got["findings"])
            did.append((rid, "AUDREY_EVALUATION", got["outcome"]))
    return {"steps": did}


# ═════════════════════════════════════════════════════════════════════
# META-MODEL READS
# ═════════════════════════════════════════════════════════════════════

async def edge_confidence(conn, *, now, run_id, plans=None) -> dict:
    opps, outs, by_reg, regs = await _scoring_context(conn, now=now)
    mine = [r for r in regs if r["subject_id"] == EC.SUBJECT]
    active = next((r for r in mine if r["status"] == "ACTIVE_FORWARD"), None)
    rows, latest = [], []
    for r in mine:
        for f in by_reg.get(r["registration_id"], []):
            op, o = opps.get(f["opportunity_id"]), outs.get(
                f["opportunity_id"])
            latest.append((op["opportunity_at"] if op else 0, f, op))
            if op and o and o["outcome_class"] == "RESOLVED" and \
                    op["opportunity_at"] >= r["registered_at"]:
                rows.append({"edge_confidence": f.get("edge_confidence"),
                             "probability": f.get("probability"),
                             "p_reference": op.get("p_reference"),
                             "price": op.get("price"), "fee": op.get("fee"),
                             "o": int(o["o"])})
    latest.sort(key=lambda x: -x[0])
    payload = C.envelope(
        version=VERSION, computed_at=now,
        registration=None if active is None else {
            k: active.get(k) for k in ("registration_id", "sha256",
                                       "registered_at", "status")},
        status=("SHADOW_FORWARD" if active else "NOT_REGISTERED"),
        why=None if active else (plans or {}).get(
            EC.SUBJECT, "NOT_YET_REGISTERED"),
        features_used=None if active is None else
        active["document"]["features"],
        features_unavailable=None if active is None else
        active["document"].get("features_unavailable"),
        feature_catalog=FT.catalog_status(list(opps.values())),
        forward_evaluation=EC.evaluate(rows),
        latest=[{"opportunity_id": f["opportunity_id"],
                 "edge_confidence": f.get("edge_confidence"),
                 "expected_net_edge": f.get("expected_net_edge"),
                 "q": (f.get("output") or {}).get("q"),
                 "q_ci": (f.get("output") or {}).get("q_ci"),
                 "sport": (op or {}).get("sport"),
                 "opportunity_at": at} for at, f, op in latest[:50]],
        future_sizing_formula=EC.FUTURE_SIZING_FORMULA,
        live_sizing_effect="NONE")
    await ST.save_snapshot(conn, run_id=run_id, component="EDGE_CONFIDENCE",
                           payload=payload, now=now)
    return payload


async def avoidance(conn, *, now, run_id, plans=None) -> dict:
    opps, outs, by_reg, regs = await _scoring_context(conn, now=now)
    mine = [r for r in regs if r["subject_id"] == AV.SUBJECT]
    active = next((r for r in mine if r["status"] == "ACTIVE_FORWARD"), None)
    thr, _ = await R.approved_threshold(conn)
    rows, latest = [], []
    for r in mine:
        for f in by_reg.get(r["registration_id"], []):
            op, o = opps.get(f["opportunity_id"]), outs.get(
                f["opportunity_id"])
            latest.append((op["opportunity_at"] if op else 0, f, op))
            if op and o and o["outcome_class"] == "RESOLVED" and \
                    op["opportunity_at"] >= r["registered_at"]:
                rows.append({"avoidance_level": f.get("avoidance_level"),
                             "p_reference": op.get("p_reference"),
                             "price": op.get("price"), "fee": op.get("fee"),
                             "o": int(o["o"])})
    latest.sort(key=lambda x: -x[0])
    levels: dict = {}
    for _, f, _ in latest:
        lv = f.get("avoidance_level") or "UNMEASURED"
        levels[lv] = levels.get(lv, 0) + 1
    payload = C.envelope(
        version=VERSION, computed_at=now,
        registration=None if active is None else {
            k: active.get(k) for k in ("registration_id", "sha256",
                                       "registered_at", "status")},
        status=("SHADOW_FORWARD" if active else "NOT_REGISTERED"),
        why=None if active else (plans or {}).get(
            AV.SUBJECT, "NOT_YET_REGISTERED"),
        dimensions=list(AV.DIMENSIONS),
        segments_flagged=None if active is None else {
            d: {v: c for v, c in vals.items()
                if c["flag"] in ("AVOID", "CAUTION")}
            for d, vals in active["document"]["parameters"][
                "segments"].items()},
        levels=levels,
        forward_evaluation=AV.evaluate(rows, threshold_pp=thr),
        latest=[{"opportunity_id": f["opportunity_id"],
                 "avoidance_risk": f.get("avoidance_risk"),
                 "level": f.get("avoidance_level"),
                 "sport": (op or {}).get("sport"),
                 "opportunity_at": at} for at, f, op in latest[:50]],
        blocks_production="NEVER: shadow output, applied = false")
    await ST.save_snapshot(conn, run_id=run_id, component="AVOIDANCE",
                           payload=payload, now=now)
    return payload


# ═════════════════════════════════════════════════════════════════════
# EXPERIMENTS
# ═════════════════════════════════════════════════════════════════════

async def experiments(conn, *, now, run_id) -> dict:
    regs = await R.registrations(conn)
    active = {r["subject_id"]: r for r in regs
              if r["status"] == "ACTIVE_FORWARD"}
    exps = {e["experiment_id"]: e for e in await R.experiments(conn)}
    actions = []
    for spec in EX.PLAN:
        if spec["experiment_id"] in exps:
            continue
        if spec["control"] not in active or spec["treatment"] not in active:
            actions.append((spec["experiment_id"],
                            "AWAITING_POLICY_REGISTRATIONS"))
            continue
        d = EX.design(spec, now=now, policy_versions={
            "CONTROL": active[spec["control"]]["registration_id"],
            "TREATMENT": active[spec["treatment"]]["registration_id"]})
        await ST.insert_experiment(conn, d, registered_at=now)
        actions.append((spec["experiment_id"], "REGISTERED"))
    exps = {e["experiment_id"]: e for e in await R.experiments(conn)}
    seeds = {k: e["seed"] for k, e in exps.items()}
    out = []
    for eid, e in exps.items():
        revs = await R.reviews(conn, eid)
        if e["status"] == "REGISTERED":
            if not any(r["kind"] == "KAREN_DESIGN_CHALLENGE" for r in revs):
                got = KAR.design_challenge(
                    {**e, "start_at": e["start_epoch"],
                     "stop_at": e["stop_epoch"]},
                    required_n_per_arm=EX.power_n_per_arm(),
                    declared_family_size=EX.FAMILY_SIZE,
                    other_seeds=[s for k, s in seeds.items() if k != eid])
                await ST.insert_review(conn, eid,
                                       kind="KAREN_DESIGN_CHALLENGE",
                                       actor="KAREN", outcome=got["outcome"],
                                       findings=got)
                revs = await R.reviews(conn, eid)
                actions.append((eid, "KAREN_" + got["outcome"]))
            karen = [r for r in revs if r["kind"] == "KAREN_DESIGN_CHALLENGE"]
            if karen and karen[-1]["outcome"] == "BLOCKED":
                await ST.set_experiment_status(
                    conn, eid, "ABANDONED", "KAREN_DESIGN_CHALLENGE_BLOCKED")
                actions.append((eid, "ABANDONED"))
            elif karen and now >= e["start_epoch"]:
                await ST.set_experiment_status(conn, eid, "RUNNING",
                                               "STARTED_AFTER_DESIGN_REVIEW")
                actions.append((eid, "RUNNING"))
        elif e["status"] == "RUNNING":
            asg = await R.assignments(conn, eid)
            outs = await R.experiment_outcomes(conn, eid)
            audits = [r for r in revs
                      if r["kind"] == "AUDREY_RANDOMIZATION_AUDIT"]
            last_n = (audits[-1]["findings"] or {}).get("assignments", 0) \
                if audits else 0
            stale_policy = [rid for rid in (e["policy_versions"] or {})
                            .values() if rid not in {
                                r["registration_id"] for r in active.values()}]
            stop_why = None
            if len(outs) >= int(e["min_sample"]):
                stop_why = "MIN_SAMPLE_OUTCOMES_REACHED"
            elif now >= e["stop_epoch"]:
                stop_why = "STOP_AT_REACHED"
            elif stale_policy:
                stop_why = "POLICY_VERSION_RETIRED_%s" % stale_policy[0]
            if len(asg) - last_n >= EX.AUDIT_EVERY or (stop_why and asg):
                await _audit(conn, e, asg)
                actions.append((eid, "AUDREY_AUDIT"))
            if stop_why:
                await ST.set_experiment_status(conn, eid, "STOPPED", stop_why)
                actions.append((eid, "STOPPED"))
        elif e["status"] == "STOPPED":
            asg = await R.assignments(conn, eid)
            outs = await R.experiment_outcomes(conn, eid)
            res = EX.analyze(e, asg, outs)
            if any(r["kind"] == "AUDREY_RANDOMIZATION_AUDIT"
                   and r["outcome"] == "FAIL" for r in revs):
                res["verdict"] = "INVALID_RANDOMIZATION"
            await ST.set_experiment_status(conn, eid, "ANALYZED",
                                           res["verdict"], result=res)
            actions.append((eid, "ANALYZED"))
    exps = await R.experiments(conn)
    for e in exps:
        asg = await R.assignments(conn, e["experiment_id"])
        outs = await R.experiment_outcomes(conn, e["experiment_id"])
        counts: dict = {}
        for a in asg:
            counts[a["arm"]] = counts.get(a["arm"], 0) + 1
        out.append({
            "experiment_id": e["experiment_id"], "status": e["status"],
            "status_reason": e["status_reason"],
            "hypothesis": e["hypothesis"],
            "primary_metric": e["primary_metric"],
            "secondary_metrics": e["secondary_metrics"],
            "assignment_unit": e["assignment_unit"],
            "randomization": e["randomization"], "seed": e["seed"],
            "arms": e["arms"], "policy_versions": e["policy_versions"],
            "start_at": e["start_epoch"], "stop_at": e["stop_epoch"],
            "min_sample": e["min_sample"], "power_target": e["power_target"],
            "alpha": e["alpha"], "family": e["family"],
            "family_size": e["family_size"],
            "stopping_rule": e["stopping_rule"],
            "failure_criteria": e["failure_criteria"],
            "design_sha256": e["design_sha256"],
            "assigned": len(asg), "assigned_by_arm": counts,
            "outcomes": len(outs),
            "interim_estimate": ("NOT_SHOWN_BEFORE_THE_SINGLE_PREDECLARED_"
                                 "ANALYSIS" if e["status"] != "ANALYZED"
                                 else None),
            "result": e["result"],
            "reviews": await R.reviews(conn, e["experiment_id"]),
            "scope": e["scope"]})
    payload = C.envelope(version=VERSION, computed_at=now,
                         family=EX.FAMILY, family_size=EX.FAMILY_SIZE,
                         experiments=out,
                         assignment_effect="PAPER_SHADOW_ONLY")
    await ST.save_snapshot(conn, run_id=run_id, component="EXPERIMENTS",
                           payload=payload, now=now)
    return {"actions": actions, "experiments": len(out)}


async def _audit(conn, e, asg):
    opps = await R.opportunities(conn, since=0.0)
    cov = {a["unit_id"]: (opps.get(a["opportunity_id"]) or {}).get(
        "p_reference") for a in asg}
    got = AUD.randomization_audit(e, asg, cov)
    await ST.insert_review(conn, e["experiment_id"],
                           kind="AUDREY_RANDOMIZATION_AUDIT", actor="AUDREY",
                           outcome=got["outcome"], findings=got["findings"])


# ═════════════════════════════════════════════════════════════════════
# THE CYCLE
# ═════════════════════════════════════════════════════════════════════

async def run_cycle(conn, *, now=None, force_training=False) -> dict:
    if not await R.tables_ready(conn):
        return {"ran": False, "why": "MIGRATION_218_NOT_APPLIED"}
    now = float(time.time() if now is None else now)
    run_id = ST.new_run_id()
    t0 = time.time()
    status: dict = {}

    reg, status["REGISTER"] = await _component(
        conn, run_id, "REGISTER",
        lambda: register(conn, now=now, force_training=force_training),
        summary_of=lambda r: {k: r[k] for k in (
            "registered", "not_registered", "threshold_pp",
            "threshold_basis", "training_attempted_at", "training_rows",
            "eddie_present")})
    plans = (reg or {}).get("not_registered") or {}
    _, status["EXPERIMENTS"] = await _component(
        conn, run_id, "EXPERIMENTS",
        lambda: experiments(conn, now=now, run_id=run_id),
        summary_of=lambda r: {"actions": r["actions"]})
    cap, status["CAPTURE"] = await _component(
        conn, run_id, "CAPTURE", lambda: capture(conn, now=now),
        summary_of=lambda r: {"captured": len(r["captured"]),
                              "candidates": r["candidates"],
                              "missed_outcome_known_before_capture":
                              r["missed_outcome_known_before_capture"]})
    _, status["FORECAST"] = await _component(
        conn, run_id, "FORECAST",
        lambda: forecast(conn, now=now, opportunity_ids=(cap or {}).get(
            "captured") or []),
        summary_of=lambda r: r)
    _, status["OUTCOMES"] = await _component(
        conn, run_id, "OUTCOMES", lambda: outcomes(conn, now=now),
        summary_of=lambda r: r)
    mt, status["MODEL_TOURNAMENT"] = await _component(
        conn, run_id, "MODEL_TOURNAMENT",
        lambda: model_tournament(conn, now=now, run_id=run_id, plans=plans),
        summary_of=lambda r: {"candidates": sorted(r["candidates"])})
    at, status["AGENT_TOURNAMENT"] = await _component(
        conn, run_id, "AGENT_TOURNAMENT",
        lambda: agent_tournament(conn, now=now, run_id=run_id),
        summary_of=lambda r: {"candidates": sorted(r["candidates"])})
    cands = dict((mt or {}).get("candidates") or {})
    cands.update((at or {}).get("candidates") or {})
    _, status["PROMOTION"] = await _component(
        conn, run_id, "PROMOTION",
        lambda: promotion(conn, candidates=cands),
        summary_of=lambda r: r)
    _, status["EDGE_CONFIDENCE"] = await _component(
        conn, run_id, "EDGE_CONFIDENCE",
        lambda: edge_confidence(conn, now=now, run_id=run_id, plans=plans),
        summary_of=lambda r: {"status": r["status"]})
    _, status["AVOIDANCE"] = await _component(
        conn, run_id, "AVOIDANCE",
        lambda: avoidance(conn, now=now, run_id=run_id, plans=plans),
        summary_of=lambda r: {"status": r["status"]})
    try:
        async with conn.transaction():
            await ST.prune(conn, now=now)
    except Exception:                                          # noqa: BLE001
        log.warning("pos learn prune failed", exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component="CYCLE", started_at=t0,
                finished_at=time.time(),
                status="OK" if all(v in ("OK", "SKIPPED")
                                   for v in status.values()) else "FAILED",
                summary={"components": status, "version": VERSION},
                version=VERSION)
    except Exception:                                          # noqa: BLE001
        log.warning("pos learn could not record the cycle", exc_info=True)
    return {"ran": True, "run_id": run_id, "components": status,
            "label": C.LABEL}


async def _one(pool) -> dict:
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   LOCK_KEY):
            return {"ran": False, "why": "STANDBY_ANOTHER_RUNNER_HOLDS_LOCK"}
        try:
            return await run_cycle(conn)
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                log.warning("pos learn unlock failed", exc_info=True)


async def run(get_pool) -> None:
    """The scheduled loop. Never raises into the API."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        if not enabled():
            await asyncio.sleep(CYCLE_S)
            continue
        try:
            pool = await get_pool()
            got = await _one(pool)
            log.info("pos learn cycle: %s", {k: got.get(k) for k in (
                "ran", "run_id", "why", "components")})
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("pos learn cycle failed", exc_info=True)
        await asyncio.sleep(CYCLE_S)
