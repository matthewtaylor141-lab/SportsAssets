"""THE AUTONOMOUS IMPROVEMENT DRIVER: ORDINARY DEFICITS INTO THE PEER LOOP.

On a schedule (the paper pass, at most every RUN_EVERY_S), Audrey looks for
four ordinary, measurable deficits in production records:

  COVERAGE_COLLAPSE        a coverage collapse alert (coverage_integrity)
  CALIBRATION_DRIFT        the decision probability's Brier is significantly
                           worse than the executable price's, or worse than
                           the prior window by more than DRIFT_DELTA
  STALE_REVIEWS            open paper positions Xavier has not reviewed
                           within STALE_REVIEW_S
  ADMISSION_REFUSAL_SPIKE  the actual admission refusal share of the last
                           SPIKE_WINDOW_S above the prior baseline by
                           SPIKE_DELTA, two-proportion z >= 1.96

and moves each through the EXISTING collaboration loop
(agents/collaboration_loop.py, migration 203) -- and NO FURTHER than this:

  1 EVIDENCE            Audrey's finding (paper_audrey_findings) is the
                        evidence; the PROPOSER agent opens the loop finding
  2 HYPOTHESIS          the proposer's hypothesis, with the measured numbers
  3 PEER_CHALLENGE      a DIFFERENT agent re-measures the deficit its own way
                        (persistence, sample size, significance) and records
                        SUSTAINED or REFUTED; REFUTED is closed by it
  4 BOUNDED_EXPERIMENT  on SUSTAINED, the proposer registers a PAPER_ONLY
                        shadow measurement with a pre-registered metric and
                        stopping rule

THE DRIVER NEVER RECORDS A CANDIDATE, AN EVALUATION OR RELEASE ELIGIBILITY,
and nothing it writes changes production: promotion, capital, limits,
policy, strategy and credentials remain human decisions. The loop's
database guards (no self-challenge, no skipped stage, PAPER_ONLY, no
authority keys) stay in force and are not bypassed: every stage goes through
the loop module, and `improvement_deficits` (migration 209) CHECKs that the
challenger differs from the proposer and that no stage past
BOUNDED_EXPERIMENT is ever recorded.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import time
from typing import Any

from . import collaboration_loop as CL

VERSION = "IMPROVEMENT_DRIVER_V1"
WATERMARK_KEY = "improvement_driver_last"
RUN_EVERY_S = 3600.0
STALE_REVIEW_S = 1800.0
DRIFT_DELTA = 0.02
SPIKE_WINDOW_S = 86400.0
SPIKE_BASELINE_S = 7 * 86400.0
SPIKE_DELTA = 0.20
SPIKE_MIN_N = 20
COVERAGE_LOOKBACK_S = 2 * 86400.0
STOPPING_RULE = {"max_duration_s": 14 * 86400, "max_samples": 1000}
DESIGN = ("SHADOW MEASUREMENT ONLY: the metric is re-measured from production "
          "records on its schedule; no mapping, policy, limit, capital, "
          "credential or order path is changed by this experiment. Any "
          "candidate change is a human decision.")

#: kind -> (proposer, challenger). The challenger is always a different
#: agent (also a CHECK on improvement_deficits).
ROLES = {"COVERAGE_COLLAPSE": ("DEREK", "AUDREY"),
         "CALIBRATION_DRIFT": ("DEREK", "XAVIER"),
         "STALE_REVIEWS": ("XAVIER", "AUDREY"),
         "ADMISSION_REFUSAL_SPIKE": ("AUDREY", "DEREK")}
#: The furthest stage this driver may record.
MAX_STAGE = CL.BOUNDED_EXPERIMENT

R_NO_SESSION = "NO_PAPER_SESSION_CONTEXT"


def _h(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


async def _regclass(conn, name: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                           # noqa: BLE001
        return False


def _day(at: float) -> str:
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).date().isoformat()


def two_prop_z(k1, n1, k0, n0) -> float | None:
    if not n1 or not n0:
        return None
    p = (k1 + k0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    if se == 0:
        return None
    return (k1 / n1 - k0 / n0) / se


# ═════════════════════════════════════════════════════════════════════
# DEFICIT DETECTORS (each returns a list of deficit dicts)
# ═════════════════════════════════════════════════════════════════════

async def coverage_deficits(conn, *, now: float) -> list:
    if not await _regclass(conn, "coverage_collapse_alerts"):
        return []
    rows = await conn.fetch(
        "SELECT * FROM coverage_collapse_alerts "
        " WHERE audrey_finding_id IS NOT NULL "
        "   AND detected_at >= to_timestamp($1) ORDER BY detected_at",
        now - COVERAGE_LOOKBACK_S)
    out = []
    for r in rows:
        from .coverage_integrity import league_name
        bl = r["baseline"]
        out.append({
            "kind": "COVERAGE_COLLAPSE",
            "subject": "%s:%s:%s" % (r["league"], r["kind"], r["stage_to"]),
            "audrey_finding_id": r["audrey_finding_id"],
            "evidence": {"alert_id": r["alert_id"], "tz": r["tz"],
                         "day": r["day"].isoformat(), "league": r["league"],
                         "alert_kind": r["kind"], "stage_from": r["stage_from"],
                         "stage_to": r["stage_to"], "ratio": r["ratio"],
                         "baseline": bl},
            "title": "Coverage collapse: %s at %s (%s)" % (
                league_name(r["league"]), r["stage_to"], r["day"]),
            "statement": "Audrey's coverage funnel shows %s %s at stage %s "
                         "on %s (%s day): ratio %s against baseline %s." % (
                             league_name(r["league"]), r["kind"],
                             r["stage_to"], r["day"], r["tz"], r["ratio"],
                             bl),
            "hypothesis": "The %s loss at %s is a pipeline defect (mapping, "
                          "catalogue or settlement support), not an absence "
                          "of markets: the provider still lists the events."
                          % (league_name(r["league"]), r["stage_to"]),
            "metric": {"name": "coverage_ratio:%s:%s->%s" % (
                r["league"], r["stage_from"], r["stage_to"]),
                "direction": "INCREASE",
                "threshold": round(float(bl) * 0.5, 6) if bl else 0.5},
        })
    return out


async def calibration_deficits(conn, *, now: float) -> list:
    from . import quality_scorecard as Q
    if not await _regclass(conn, "external_valuations"):
        return []
    cur = Q.brier(await Q.brier_rows(conn, now - Q.WINDOW_S, now))
    pri = Q.brier(await Q.brier_rows(conn, now - 2 * Q.WINDOW_S,
                                     now - Q.WINDOW_S))
    if cur["n"] < Q.MIN_RATE_SAMPLE or cur["diff"] is None:
        return []
    worse_than_price = cur["ci"] is not None and cur["ci"]["low"] > 0
    drifted = (pri["diff"] is not None and pri["n"] >= Q.MIN_RATE_SAMPLE
               and cur["diff"] - pri["diff"] > DRIFT_DELTA)
    if not (worse_than_price or drifted):
        return []
    return [{
        "kind": "CALIBRATION_DRIFT", "subject": "entry_decision:%s"
        % _day(now),
        "evidence": {"n": cur["n"], "brier_model": cur["model"],
                     "brier_baseline": cur["baseline"], "diff": cur["diff"],
                     "ci": cur["ci"], "prior_diff": pri["diff"],
                     "prior_n": pri["n"], "worse_than_price": worse_than_price,
                     "drifted": drifted},
        "title": "Calibration drift on ENTRY_DECISION valuations (%s)"
                 % _day(now),
        "statement": "Brier(probability) - Brier(price) = %s over %d settled "
                     "decisions (prior window %s)." % (cur["diff"], cur["n"],
                                                       pri["diff"]),
        "hypothesis": "The decision probability has lost skill against the "
                      "executable price (source staleness or a regime "
                      "change), measured on settled decisions.",
        "metric": {"name": "brier_model_minus_baseline_7d",
                   "direction": "DECREASE", "threshold": 0.0},
    }]


async def stale_review_deficits(conn, *, now: float, account_id: str) -> list:
    if not await _regclass(conn, "paper_xavier_reviews"):
        return []
    from .. import bettor_paper_ledger as L
    pos = await L.positions(conn, account_id)
    if not pos:
        return []
    stale = []
    for p in pos:
        last = await conn.fetchval(
            "SELECT extract(epoch FROM max(reviewed_at)) FROM "
            " paper_xavier_reviews WHERE account_id=$1 AND group_id=$2",
            account_id, p["group_id"])
        ref = float(last) if last is not None else p.get("first_fill_at")
        if ref is not None and now - float(ref) > STALE_REVIEW_S:
            stale.append({"group_id": p["group_id"],
                          "last_review_at": None if last is None
                          else float(last),
                          "age_s": round(now - float(ref), 1)})
    if not stale:
        return []
    return [{
        "kind": "STALE_REVIEWS", "subject": "%s:%s" % (account_id, _day(now)),
        "evidence": {"open_positions": len(pos), "stale": len(stale),
                     "limit_s": STALE_REVIEW_S, "sample": stale[:10]},
        "title": "Stale Xavier reviews: %d of %d open positions (%s)"
                 % (len(stale), len(pos), _day(now)),
        "statement": "%d open paper position(s) were not reviewed within "
                     "%d s." % (len(stale), STALE_REVIEW_S),
        "hypothesis": "Review cadence falls behind the open book (pass "
                      "budget or probability reads), leaving positions "
                      "unmanaged between passes.",
        "metric": {"name": "share_of_open_positions_reviewed_within_%ds"
                   % STALE_REVIEW_S, "direction": "INCREASE",
                   "threshold": 0.95},
    }]


async def admission_deficits(conn, *, now: float) -> list:
    if not await _regclass(conn, "execution_intents"):
        return []

    async def share(s, e):
        r = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE actual_state <> 'PAPER_ONLY') AS n,"
            " count(*) FILTER (WHERE actual_state = 'REFUSED') AS k "
            " FROM execution_intents WHERE decided_at >= to_timestamp($1) "
            "  AND decided_at < to_timestamp($2)", s, e)
        return int(r["k"] or 0), int(r["n"] or 0)
    k1, n1 = await share(now - SPIKE_WINDOW_S, now)
    k0, n0 = await share(now - SPIKE_WINDOW_S - SPIKE_BASELINE_S,
                         now - SPIKE_WINDOW_S)
    if n1 < SPIKE_MIN_N or n0 < SPIKE_MIN_N:
        return []
    z = two_prop_z(k1, n1, k0, n0)
    if z is None or z < 1.96 or (k1 / n1 - k0 / n0) < SPIKE_DELTA:
        return []
    return [{
        "kind": "ADMISSION_REFUSAL_SPIKE", "subject": "actual:%s" % _day(now),
        "evidence": {"refused": k1, "considered": n1, "share": round(k1 / n1,
                                                                     6),
                     "baseline_refused": k0, "baseline_considered": n0,
                     "baseline_share": round(k0 / n0, 6), "z": round(z, 3)},
        "title": "Actual admission refusal spike (%s)" % _day(now),
        "statement": "Refused %d of %d live-considered intents in 24 h "
                     "against %d of %d in the prior 7 days (z=%.2f)."
                     % (k1, n1, k0, n0, z),
        "hypothesis": "A single admission input (book currency, depth or "
                      "settlement admissibility) degraded and now refuses "
                      "intents that previously passed.",
        "metric": {"name": "admission_refusal_share_24h",
                   "direction": "DECREASE",
                   "threshold": round(k0 / n0, 6)},
    }]


# ═════════════════════════════════════════════════════════════════════
# PEER CHALLENGES: the challenger re-measures, its own way
# ═════════════════════════════════════════════════════════════════════

async def challenge(conn, d: dict, *, now: float, account_id: str) -> tuple:
    """(outcome, text). Pure re-measurement from records; never trusts the
    proposer's numbers."""
    k = d["kind"]
    if k == "COVERAGE_COLLAPSE":
        ev = d["evidence"]
        col = None
        from .coverage_integrity import COLUMN
        col = COLUMN.get(ev["stage_to"])
        row = await conn.fetchrow(
            "SELECT * FROM coverage_funnel_snapshots WHERE tz=$1 AND "
            " league=$2 AND day=$3::date", ev["tz"], ev["league"],
            _dt.date.fromisoformat(ev["day"]))
        if row is None or col is None:
            return "REFUTED", ("No persisted funnel snapshot backs the alert "
                               "(%s %s): nothing to sustain." % (
                                   ev["league"], ev["day"]))
        prov = row["provider_events"] or 0
        n = row[col]
        if ev["alert_kind"] == "ABSENT_DOWNSTREAM":
            ok = prov >= 3 and n == 0
        else:
            prev = row[COLUMN[ev["stage_from"]]] or 0
            ok = prev >= 5 and ev["ratio"] is not None and \
                (n or 0) / prev <= float(ev["ratio"]) + 1e-9
        return ("SUSTAINED" if ok else "REFUTED"), (
            "Re-read the persisted snapshot: provider %s, %s = %s. %s" % (
                prov, ev["stage_to"], n,
                "The deficit persists." if ok else
                "The deficit is not confirmed on the record."))
    if k == "CALIBRATION_DRIFT":
        ev = d["evidence"]
        ci = ev.get("ci") or {}
        ok = ev["n"] >= 30 and (ci.get("low") is not None and
                                (ci["low"] > 0 or ev.get("drifted")))
        return ("SUSTAINED" if ok else "REFUTED"), (
            "Paired Brier difference %s, 95%% CI %s on n=%d: %s" % (
                ev["diff"], ci, ev["n"],
                "significant." if ok else "not significant; refuted."))
    if k == "STALE_REVIEWS":
        again = await stale_review_deficits(conn, now=now,
                                            account_id=account_id)
        ok = bool(again)
        return ("SUSTAINED" if ok else "REFUTED"), (
            "Re-queried open positions at challenge time: %s stale." % (
                again[0]["evidence"]["stale"] if ok else 0))
    if k == "ADMISSION_REFUSAL_SPIKE":
        ev = d["evidence"]
        ok = ev["considered"] >= SPIKE_MIN_N and ev["z"] >= 1.96
        return ("SUSTAINED" if ok else "REFUTED"), (
            "Two-proportion z=%s on %d vs %d intents: %s" % (
                ev["z"], ev["considered"], ev["baseline_considered"],
                "a real spike." if ok else "not significant."))
    return "REFUTED", "unknown deficit kind"


# ═════════════════════════════════════════════════════════════════════
# THE DRIVE
# ═════════════════════════════════════════════════════════════════════

async def _evidence_finding(conn, d: dict, ctx: dict, now: float) -> str:
    if d.get("audrey_finding_id"):
        return d["audrey_finding_id"]
    from . import paper_audrey as PA
    got = await PA.finding(conn, dict(ctx, now=now),
                           kind="IMPROVEMENT_DEFICIT_%s" % d["kind"],
                           subject=d["subject"], severity="WARNING",
                           scope=d["subject"],
                           detail=dict(d["evidence"], statement=d["statement"],
                                       version=VERSION))
    return got["finding_id"]


async def _record(conn, d: dict, **kw) -> None:
    proposer, challenger = ROLES[d["kind"]]
    did = "deficit:%s" % _h(d["kind"], d["subject"])
    await conn.execute(
        "INSERT INTO improvement_deficits (deficit_id, kind, subject, "
        " observed_at, evidence, proposer, challenger, audrey_finding_id, "
        " agent_finding_id, stage_reached, challenge_outcome, refusal, "
        " updated_at) VALUES ($1,$2,$3,to_timestamp($4),$5::jsonb,$6,$7,$8,"
        " $9,$10,$11,$12,to_timestamp($4)) ON CONFLICT (deficit_id) DO UPDATE"
        " SET audrey_finding_id = coalesce(EXCLUDED.audrey_finding_id, "
        "       improvement_deficits.audrey_finding_id),"
        "     agent_finding_id = coalesce(EXCLUDED.agent_finding_id, "
        "       improvement_deficits.agent_finding_id),"
        "     stage_reached = coalesce(EXCLUDED.stage_reached, "
        "       improvement_deficits.stage_reached),"
        "     challenge_outcome = coalesce(EXCLUDED.challenge_outcome, "
        "       improvement_deficits.challenge_outcome),"
        "     refusal = EXCLUDED.refusal, updated_at = EXCLUDED.updated_at",
        did, d["kind"], d["subject"], float(kw["now"]),
        json.dumps(d["evidence"], default=str), proposer, challenger,
        kw.get("audrey_finding_id"), kw.get("agent_finding_id"),
        kw.get("stage"), kw.get("challenge_outcome"), kw.get("refusal"))


async def drive_one(conn, d: dict, *, ctx: dict, now: float) -> dict:
    """Move one deficit as far as BOUNDED_EXPERIMENT (or CLOSED on a
    refutation). Idempotent and resumable. Never raises."""
    proposer, challenger = ROLES[d["kind"]]
    res: dict[str, Any] = {"kind": d["kind"], "subject": d["subject"],
                           "proposer": proposer, "challenger": challenger}
    try:
        async with conn.transaction():
            fid_a = await _evidence_finding(conn, d, ctx, now)
    except Exception as exc:                                    # noqa: BLE001
        res["refusal"] = "AUDREY_FINDING_FAILED:%s" % type(exc).__name__
        return res
    refs = [{"kind": "paper_audrey_findings", "id": fid_a}]
    opened = await CL.open_finding(
        conn, proposer=proposer, title=d["title"][:300],
        statement=d["statement"], evidence_refs=refs,
        evidence_window_end=now, at=now)
    if not opened.get("ok"):
        res["refusal"] = opened.get("refusal")
        await _record(conn, d, now=now, audrey_finding_id=fid_a,
                      refusal=opened.get("refusal"))
        return res
    fid = opened["finding_id"]
    res["agent_finding_id"] = fid
    cur = await CL.finding(conn, fid)
    stage = cur["finding"]["stage"]
    outcome = None
    if stage == CL.EVIDENCE:
        got = await CL.record_hypothesis(conn, fid, actor=proposer,
                                         hypothesis=d["hypothesis"],
                                         evidence_refs=refs, at=now)
        if not got.get("ok"):
            res["refusal"] = got.get("refusal")
        else:
            stage = CL.HYPOTHESIS
    if stage == CL.HYPOTHESIS:
        outcome, text = await challenge(conn, d, now=now,
                                        account_id=ctx.get("account_id"))
        got = await CL.record_challenge(conn, fid, actor=challenger,
                                        challenge=text, outcome=outcome,
                                        evidence_refs=refs, at=now)
        if not got.get("ok"):
            res["refusal"] = got.get("refusal")
        else:
            stage = CL.PEER_CHALLENGE
    if stage == CL.PEER_CHALLENGE:
        last = cur["stages"][-1] if cur["stages"] and \
            cur["stages"][-1]["stage"] == CL.PEER_CHALLENGE else None
        outcome = outcome or (last or {}).get("outcome")
        if outcome is None:
            again = await CL.finding(conn, fid)
            outcome = again["stages"][-1].get("outcome")
        if outcome == "SUSTAINED":
            got = await CL.register_experiment(
                conn, fid, actor=proposer, design=DESIGN, metric=d["metric"],
                stopping_rule=STOPPING_RULE, at=now)
            if got.get("ok"):
                stage = CL.BOUNDED_EXPERIMENT
            else:
                res["refusal"] = got.get("refusal")
        else:
            got = await CL.close(conn, fid, actor=challenger,
                                 reason="refuted by the peer challenge: "
                                        "the deficit was not confirmed",
                                 at=now)
            if got.get("ok"):
                stage = CL.CLOSED
    res.update(stage=stage, challenge_outcome=outcome)
    await _record(conn, d, now=now, audrey_finding_id=fid_a,
                  agent_finding_id=fid,
                  stage=stage if stage in (CL.EVIDENCE, CL.HYPOTHESIS,
                                           CL.PEER_CHALLENGE,
                                           CL.BOUNDED_EXPERIMENT, CL.CLOSED)
                  else None,
                  challenge_outcome=outcome, refusal=res.get("refusal"))
    return res


async def run(conn, *, ctx: dict | None, now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at, "deficits": [],
                           "errors": {}}
    if not (await _regclass(conn, "improvement_deficits")
            and await _regclass(conn, "agent_findings")):
        return dict(out, ran=False, why="MIGRATIONS_203_209_NOT_APPLIED")
    if not ctx or not ctx.get("session_id"):
        return dict(out, ran=False, why=R_NO_SESSION)
    found: list = []
    for name, fn in (
            ("coverage", lambda: coverage_deficits(conn, now=at)),
            ("calibration", lambda: calibration_deficits(conn, now=at)),
            ("stale_reviews", lambda: stale_review_deficits(
                conn, now=at, account_id=ctx["account_id"])),
            ("admission", lambda: admission_deficits(conn, now=at))):
        try:
            found.extend(await fn())
        except Exception as exc:                                # noqa: BLE001
            out["errors"][name] = "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])
    for d in found:
        try:
            out["deficits"].append(await drive_one(conn, d, ctx=ctx, now=at))
        except Exception as exc:                                # noqa: BLE001
            out["errors"][d["subject"]] = type(exc).__name__
    out["ran"] = True
    return out


async def step(conn, ctx: dict) -> dict:
    """THE SCHEDULED HOOK (paper pass): at most every RUN_EVERY_S, on the
    main paper account only. Never raises."""
    from .. import bettor_paper_ledger as L
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    if ctx.get("account_id") != await L.selected_account(conn):
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    try:
        last = L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last.get("at") is not None and at - float(last["at"]) < RUN_EVERY_S:
            return {"ran": False, "why": "NOT_DUE", "last_at": last["at"]}
        res = await run(conn, ctx=ctx, now=at)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            WATERMARK_KEY, json.dumps({"at": at, "version": VERSION,
                                       "deficits": len(res["deficits"])}))
        return {"ran": res.get("ran"), "deficits": [
            {k: x.get(k) for k in ("kind", "stage", "challenge_outcome",
                                   "refusal")} for x in res["deficits"]],
            "errors": res.get("errors")}
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}
