"""AUDREY'S OPERATIONAL AUDIT OF THE PAPER EXPERIMENT (migration 189).

PAPER ONLY. At most every AUDIT_EVERY_S, from PERSISTED RECORDS only, Audrey
audits the parts of the experiment that decide whether it is producing
evidence at all -- before anyone asks whether it is profitable:

  FUNNEL          decisions per strategy and their refusal reasons (6 h)
  BOOK READS      the share of decisions whose book read was cut by the
                  decision deadline or the venue cooldown
  FEES            every paper fill's fee recomputed from the PUBLISHED
                  schedule (`bettor_fee_schedule`, taker theta, half-even
                  cents) against what the simulator charged (the deployed
                  `calibration_fees` schedule, half-up cents); every
                  fee-consumed refusal's fee per contract recomputed
  STALE INPUTS    refusals for a stale Pinnacle reading or a stale book
  MISSING         entry-experiment valuations with no decision from a
                  strategy that was enabled when they were written
  COVERAGE        the collection cycle's refusals by code (what never
                  reaches a decision), never weakened to raise counts
  MANAGEMENT      held groups Xavier has not reviewed recently

Each audit writes findings (`paper_audrey_findings`, kind OPS_AUDIT_*), and
assigns an evidence-backed RECOMMENDATION to DEREK or XAVIER
(`paper_recommendations`) with the metric and its baseline. With it goes an
AUTOMATED_ACKNOWLEDGEMENT event, actor SYSTEM: a predefined template naming
the capability the running build has for that kind of finding. It is NOT a
response from Derek or Xavier, not an independent evaluation and not
evidence of self-improvement, and it is labelled so wherever it is shown.
RESPONSE is reserved for a genuine agent review, recorded separately; every
later audit appends a MEASUREMENT of the same metric, and only those
measurements move the status to IMPROVED or NOT_IMPROVED after a measurement
window with enough samples.

OPERATIONAL, NOT LEARNING. Every recommendation here is category
OPERATIONAL. Nothing in this module claims that an agent became more
profitable; LEARNING_CLAIM is reserved for forward-evaluated proposals
(`paper_learning`), and the database CHECK keeps the two apart.

Never places, cancels or modifies an order.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
from typing import Any

from .. import bettor_paper_ledger as L

VERSION = "PAPER_OPS_AUDIT_V1"
WATERMARK_KEY = "paper_ops_audit_last"
AUDIT_EVERY_S = 600.0
WINDOW_S = 6 * 3600.0
MEASURE_EVERY_S = 1800.0
MEASUREMENT_WINDOW_S = 2 * 3600.0
MIN_SAMPLES = 20

CG = "PINNACLE_COMPLETED_GAME_PAPER"
MAKER = "PINNACLE_COMPLETED_GAME_MAKER_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
ACTIVE_STRATEGIES = (CG, MAKER, EXPLORE)
EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"
BOOK_CUT = "BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE"
STALE = ("PROBABILITY_EVIDENCE_STALE", "THE_PAPER_BOOK_OBSERVATION_IS_NOT_"
         "CURRENT", "QUOTE_STALE_ON_ARRIVAL",
         "QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER")
FEES_CONSUME = "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT"

# ── AUTOMATED ACKNOWLEDGEMENTS (templates, actor SYSTEM) ─────────────────
# What the running build can do about each kind of recommendation, stated
# as capabilities in this code, never as results and never as the named
# agent's own words. Measurements decide whether anything helped.
ACK_LABEL = ("Automated acknowledgement (predefined template; not an agent "
             "review, not an evaluation, not evidence of improvement)")
RESPONSES = {
    "BOOK_READ_CUTS": ("DEREK", (
        "Accepted. This build shares one venue book read per valuation (the "
        "collection cycle's own read, reused while it is at most 6 s old) "
        "and gives a cut read ONE bounded retry after the venue cooldown "
        "while the Pinnacle reading stays inside its 30 s rule (at most 2 "
        "retries at once, 60 per hour). Freshness checks are unchanged. The "
        "measurements below decide whether the cut rate fell.")),
    "FEES_CONSUME_EDGE": ("DEREK", (
        "Accepted. The maker-entry policy (PINNACLE_COMPLETED_GAME_MAKER_"
        "PAPER_V1) now rests a bid below the ask where the 0.5 pp threshold "
        "and the after-fee rule hold at that price, charging the taker fee "
        "(the maker rebate is not assumed). The investment policy's taker "
        "rule is unchanged. Measured by resting orders placed and filled.")),
    "MISSING_DECISIONS": ("DEREK", (
        "Accepted. Every attempted evaluation is now recorded "
        "(paper_evaluation_attempts), including timeouts and deferrals; "
        "the pass backstop decides any valuation the cycle missed. Measured "
        "by valuations without a decision.")),
    "STALE_INPUTS": ("DEREK", (
        "Acknowledged. Freshness rules are not relaxed. Shared book reads "
        "and the retry shorten the time from valuation to decision; the "
        "Pinnacle 30 s rule and the 10 s book rule stay as they are.")),
    "COLLECTION_COVERAGE": ("DEREK", (
        "Acknowledged. The unmatched events are recorded per code and sport; "
        "no identity, market or settlement matching rule is weakened to "
        "raise the count. Coverage changes need a correct mapping, not a "
        "looser one.")),
    "FEE_ROUNDING": ("DEREK", (
        "Acknowledged. The simulator charges the deployed schedule; the "
        "published schedule's rounding (half-even) and effective date are "
        "recorded beside it. A one-cent tie difference is reported, not "
        "silently corrected.")),
    "UNREVIEWED_POSITIONS": ("XAVIER", (
        "Accepted. Every held group is reviewed on its first fill, on fill "
        "and market events and on the 60 s backstop; the next pass reviews "
        "the groups named here.")),
}


def _rid(account_id: str, kind: str) -> str:
    return "paperrec:" + hashlib.sha256(
        ("%s:%s" % (account_id, kind)).encode()).hexdigest()[:24]


def _fid(session_id: str, kind: str, subject: str) -> str:
    return "paperfind:" + hashlib.sha256(
        ("%s:%s:%s" % (session_id, kind, subject)).encode()).hexdigest()[:24]


def _hour(at: float) -> str:
    return _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H")


# ═════════════════════════════════════════════════════════════════════
# THE MEASUREMENTS (read-only)
# ═════════════════════════════════════════════════════════════════════

#: the most decision rows one audit reads for the unique-opportunity funnel
FUNNEL_ROWS_MAX = 50000


async def funnel(conn, account_id: str, *, since: float) -> dict:
    """Per strategy: its decisions, and its refusal reasons ranked by UNIQUE
    OPPORTUNITY (R30A, owner audit 2026-10-04).

    THE DEFECT. `reasons` was count(*) per refusal -- decision ROWS -- and
    every policy re-evaluates the same live market on each valuation, so a
    reason that refused three markets ninety times each outranked one that
    refused thirty markets once (production: 22 rows per contract on
    average, up to 91). `reasons` is now the number of unique opportunities
    (fixture / market / side / line / period) each reason BINDS for that
    strategy (profitability.opportunity_funnel, the one definition); the
    rows are kept beside it as `reason_evaluations`, with `ENTER` counted
    the same way. The sleeve of each strategy is named (migration 223's
    map; outside it UNCLASSIFIED)."""
    from ..profitability import common as PC
    from ..profitability import opportunity_funnel as FN
    rows = [dict(r) for r in await conn.fetch(
        "SELECT decision_id, strategy, verdict, refusal, refusals, "
        "       us_market_slug, holding_side, fixture, "
        "       label->>'line' AS line, label->>'period' AS scope, "
        "       extract(epoch FROM decided_at)::float8 AS decided_at "
        "  FROM paper_decisions WHERE account_id=$1 "
        "   AND decided_at > to_timestamp($2) "
        " ORDER BY decided_at DESC LIMIT $3",
        account_id, since, FUNNEL_ROWS_MAX)]
    out: dict[str, Any] = {}
    for strat in sorted({r["strategy"] for r in rows}):
        mine = [r for r in rows if r["strategy"] == strat]
        sleeve = PC.strategy_sleeve(strat)
        f = FN.compute([dict(r, ev=None) for r in mine], sleeve=sleeve,
                       strategy=strat, top_n=0)
        reasons = {e["blocker"]: e["unique_opportunities"]
                   for e in f["blockers"] if e["unique_opportunities"]}
        if f["totals"]["entered_unique"]:
            reasons["ENTER"] = f["totals"]["entered_unique"]
        evals: dict = {}
        for r in mine:
            b = FN.blocker_of(r)
            k = "ENTER" if b == FN.ENTERED else b
            evals[k] = evals.get(k, 0) + 1
        out[strat] = {
            "decisions": len(mine), "sleeve": sleeve,
            "unique_opportunities": f["totals"]["unique_opportunities"],
            "re_evaluations": f["totals"]["re_evaluations"],
            "unkeyed_decisions": f["totals"]["unkeyed_evaluations"],
            "reasons": dict(sorted(reasons.items(),
                                   key=lambda kv: (-kv[1], kv[0]))),
            "reason_evaluations": dict(sorted(
                evals.items(), key=lambda kv: (-kv[1], kv[0]))),
            "reasons_basis": ("unique opportunities each reason binds "
                              "(fixture / market / side / line / period); "
                              "rows are reason_evaluations")}
    if len(rows) >= FUNNEL_ROWS_MAX:
        for v in out.values():
            v["truncated_to_newest_rows"] = FUNNEL_ROWS_MAX
    return out


async def book_cut_rate(conn, account_id: str, *, since: float) -> dict:
    r = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE book_obs_id IS NOT NULL) AS reads, "
        "       count(*) FILTER (WHERE refusal = $3) AS cut "
        "  FROM paper_decisions WHERE account_id=$1 "
        "   AND decided_at > to_timestamp($2) AND strategy = ANY($4::text[])",
        account_id, since, BOOK_CUT, list(ACTIVE_STRATEGIES))
    reads, cut = int(r["reads"] or 0), int(r["cut"] or 0)
    shared = 0
    try:
        shared = int(await conn.fetchval(
            "SELECT count(*) FROM paper_evaluation_attempts WHERE "
            " at > to_timestamp($1) AND book_source = "
            " 'SHARED_RECENT_PROCESS_READ'", since) or 0)
    except Exception:                                           # noqa: BLE001
        pass
    return {"book_reads": reads, "cut": cut,
            "rate": round(cut / reads, 6) if reads else None,
            "shared_reads": shared}


async def stale_rate(conn, account_id: str, *, since: float) -> dict:
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE refusal = "
        " ANY($3::text[])) AS stale FROM paper_decisions WHERE "
        " account_id=$1 AND decided_at > to_timestamp($2) "
        " AND strategy = ANY($4::text[])", account_id, since, list(STALE),
        list(ACTIVE_STRATEGIES))
    n, s = int(r["n"] or 0), int(r["stale"] or 0)
    return {"decisions": n, "stale": s,
            "rate": round(s / n, 6) if n else None}


async def fees_consume(conn, account_id: str, *, since: float) -> dict:
    r = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE refusal = $3) AS consumed, "
        "       count(*) FILTER (WHERE (policy_decision->'conditions'->3"
        "         ->>'passed')::boolean) AS gross_cleared "
        "  FROM paper_decisions WHERE account_id=$1 AND strategy=$4 "
        "   AND decided_at > to_timestamp($2)", account_id, since,
        FEES_CONSUME, CG)
    maker = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE verdict='ENTER') AS placed, "
        "       count(*) AS decisions FROM paper_decisions "
        " WHERE account_id=$1 AND strategy=$2 "
        "   AND decided_at > to_timestamp($3)", account_id, MAKER, since)
    filled = await conn.fetchval(
        "SELECT count(DISTINCT o.order_id) FROM paper_orders o "
        " WHERE o.account_id=$1 AND o.strategy=$2 AND o.role='ENTRY' "
        "   AND o.filled_qty > 0 AND o.created_at > to_timestamp($3)",
        account_id, MAKER, since)
    return {"fees_consumed_edge": int(r["consumed"] or 0),
            "gross_cleared": int(r["gross_cleared"] or 0),
            "maker_decisions": int(maker["decisions"] or 0),
            "maker_resting_orders_placed": int(maker["placed"] or 0),
            "maker_orders_with_fills": int(filled or 0)}


async def missing_decisions(conn, *, since: float, now: float) -> dict:
    out = {}
    for s in ACTIVE_STRATEGIES:
        start = await conn.fetchval(
            "SELECT extract(epoch FROM updated_at) FROM paper_control "
            " WHERE control_key=$1 AND enabled", s)
        if start is None:
            continue
        lo = max(float(since), float(start))
        n = await conn.fetchval(
            "SELECT count(*) FROM external_valuations v "
            " WHERE v.experiment_id=$1 AND v.us_market_slug IS NOT NULL "
            "   AND v.decided_at > to_timestamp($2) "
            "   AND v.decided_at < to_timestamp($3) "
            "   AND NOT EXISTS (SELECT 1 FROM paper_decisions d "
            "        WHERE d.valuation_id = v.id AND d.strategy = $4)",
            EXPERIMENT_ID, lo, float(now) - 600.0, s)
        out[s] = int(n or 0)
    return {"by_strategy": out, "total": sum(out.values())}


def published_fee(qty, price, filled_at: float):
    """The PUBLISHED taker fee for one fill (half-even cents), by date."""
    from .. import bettor_fee_schedule as FS
    day = _dt.datetime.fromtimestamp(filled_at, _dt.timezone.utc
                                     ).date().isoformat()
    sch = FS.for_date(day)
    return float(sch.taker_fee(qty, price)), sch.schedule_id


async def fee_audit(conn, account_id: str, *, since: float) -> dict:
    rows = await conn.fetch(
        "SELECT fill_id, role, strategy, qty, price, fee_usd, "
        "       extract(epoch FROM filled_at) AS at FROM paper_fills "
        " WHERE account_id=$1 AND filled_at > to_timestamp($2) "
        " ORDER BY filled_at", account_id, since)
    checked, diffs = 0, []
    for r in rows:
        try:
            pub, sid = published_fee(float(r["qty"]), float(r["price"]),
                                     float(r["at"]))
        except Exception as exc:                                # noqa: BLE001
            diffs.append({"fill_id": r["fill_id"], "error":
                          type(exc).__name__})
            continue
        checked += 1
        d = round(float(r["fee_usd"]) - pub, 6)
        if abs(d) >= 0.005:
            diffs.append({"fill_id": r["fill_id"], "role": r["role"],
                          "strategy": r["strategy"],
                          "qty": float(r["qty"]), "price": float(r["price"]),
                          "charged_usd": float(r["fee_usd"]),
                          "published_usd": pub, "schedule": sid,
                          "difference_usd": d})
    # the fee-consumed refusals' fee per contract, recomputed
    dec = await conn.fetch(
        "SELECT decision_id, (economics->'fee_stop'->>'price')::float8 AS px,"
        "       (economics->'fee_stop'->>'fee_per_contract_usd')::float8 AS fpc,"
        "       extract(epoch FROM decided_at) AS at FROM paper_decisions "
        " WHERE account_id=$1 AND refusal=$2 "
        "   AND decided_at > to_timestamp($3)", account_id, FEES_CONSUME,
        since)
    dchk, ddiff = 0, []
    for r in dec:
        if r["px"] is None or r["fpc"] is None:
            continue
        pub, sid = published_fee(10000, r["px"], float(r["at"]))
        dchk += 1
        if abs(pub / 10000.0 - float(r["fpc"])) > 0.0005:
            ddiff.append({"decision_id": r["decision_id"], "price": r["px"],
                          "recorded_fee_per_contract": r["fpc"],
                          "published_fee_per_contract": pub / 10000.0,
                          "schedule": sid})
    return {"fills_checked": checked, "fill_differences": diffs[:20],
            "fills_differing": len(diffs),
            "decisions_checked": dchk, "decision_differences": ddiff[:20],
            "decisions_differing": len(ddiff),
            "basis": ("published taker theta x C x p x (1-p), half-even "
                      "cents (bettor_fee_schedule.for_date) vs the fee the "
                      "simulator charged")}


async def coverage(conn) -> dict:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key="
                            "'ext_pinnacle_last_cycle'")
    cyc = L._j(v) or {}
    rows = []
    try:
        rows = await conn.fetch(
            "SELECT coalesce(first_refusal, outcome) AS code, count(*) AS n "
            "  FROM ext_candidate_outcomes WHERE recorded_at > now() - "
            "  interval '24 hours' GROUP BY 1 ORDER BY 2 DESC LIMIT 15")
    except Exception:                                           # noqa: BLE001
        rows = []
    return {"last_cycle_at": cyc.get("at"),
            "last_cycle_refusals": cyc.get("refusals") or {},
            "last_cycle_label": cyc.get("cycle_label"),
            "candidate_outcomes_24h": {r["code"]: int(r["n"]) for r in rows}}


async def unreviewed(conn, account_id: str, *, now: float) -> dict:
    groups = {p["group_id"] for p in await L.positions(conn, account_id)}
    late = []
    for g in sorted(groups):
        last = await conn.fetchval(
            "SELECT extract(epoch FROM max(reviewed_at)) FROM "
            " paper_xavier_reviews WHERE group_id=$1", g)
        if last is None or now - float(last) > 600.0:
            late.append({"group_id": g, "last_review_at": last})
    return {"held_groups": len(groups), "unreviewed_10min": len(late),
            "groups": late[:20]}


# ═════════════════════════════════════════════════════════════════════
# FINDINGS, RECOMMENDATIONS, RESPONSES, MEASUREMENTS
# ═════════════════════════════════════════════════════════════════════

async def _finding(conn, ctx, *, kind: str, severity: str, at: float,
                   detail: dict) -> str:
    subject = "ops:%s:%s" % (kind, _hour(at))
    fid = _fid(ctx["session_id"], "OPS_AUDIT_" + kind, subject)
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail) VALUES "
        " ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT (finding_id) DO "
        " NOTHING", fid, ctx["session_id"], ctx["account_id"], L._ts(at),
        "OPS_AUDIT_" + kind, severity, subject,
        json.dumps(dict(detail, audit_version=VERSION,
                        category="OPERATIONAL"), default=str))
    return fid


async def _event(conn, rid: str, *, actor: str, kind: str, body: str,
                 detail: dict, at: float) -> None:
    await conn.execute(
        "INSERT INTO paper_recommendation_events (recommendation_id, at, "
        " actor, kind, body, detail) VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
        rid, L._ts(at), actor, kind, body, json.dumps(detail, default=str))


async def recommend(conn, ctx, *, kind: str, metric: str, value,
                    open_now: bool, text: str, evidence: dict,
                    finding_id: str, at: float,
                    lower_is_better: bool = True) -> dict:
    """Open (once) a recommendation of `kind` with its automated (template)
    acknowledgement,
    and append a measurement of `metric` at most every MEASURE_EVERY_S.
    Status moves only on measurements, after MEASUREMENT_WINDOW_S and
    MIN_SAMPLES."""
    acct = ctx["account_id"]
    owner, response = RESPONSES[kind]
    rid = _rid(acct, kind)
    row = await conn.fetchrow(
        "SELECT * FROM paper_recommendations WHERE recommendation_id=$1", rid)
    out = {"recommendation_id": rid, "kind": kind, "owner": owner}
    if row is None:
        if not open_now:
            return dict(out, opened=False)
        baseline = {"metric": metric, "value": value, "at": at,
                    "samples": evidence.get("samples")}
        await conn.execute(
            "INSERT INTO paper_recommendations (recommendation_id, "
            " account_id, finding_id, created_at, updated_at, owner_agent, "
            " category, kind, metric, baseline, recommendation, evidence, "
            " status) VALUES ($1,$2,$3,$4,$4,$5,'OPERATIONAL',$6,$7,"
            " $8::jsonb,$9,$10::jsonb,'ACKNOWLEDGED') ON CONFLICT DO NOTHING",
            rid, acct, finding_id, L._ts(at), owner, kind, metric,
            json.dumps(baseline, default=str), text,
            json.dumps(evidence, default=str))
        await _event(conn, rid, actor="AUDREY", kind="ASSIGNED",
                     body="Assigned to %s: %s" % (owner, text),
                     detail={"finding_id": finding_id, "baseline": baseline},
                     at=at)
        await _event(conn, rid, actor="SYSTEM",
                     kind="AUTOMATED_ACKNOWLEDGEMENT",
                     body="%s. Addressed to %s: %s" % (ACK_LABEL, owner,
                                                       response),
                     detail={"addressed_to": owner, "template": kind,
                             "automated": True, "agent_review": False,
                             "build_capability": True, "is_a_result": False,
                             "label": ACK_LABEL}, at=at)
        return dict(out, opened=True, baseline=baseline)
    last = await conn.fetchval(
        "SELECT extract(epoch FROM max(at)) FROM paper_recommendation_events "
        " WHERE recommendation_id=$1 AND kind='MEASUREMENT'", rid)
    if last is not None and at - float(last) < MEASURE_EVERY_S:
        return dict(out, measured=False)
    base = L._j(row["baseline"]) or {}
    b = base.get("value")
    detail = {"metric": metric, "value": value, "baseline": b,
              "samples": evidence.get("samples")}
    await _event(conn, rid, actor="AUDREY", kind="MEASUREMENT",
                 body="%s now %s (baseline %s)" % (metric, value, b),
                 detail=detail, at=at)
    status = row["status"]
    age = at - L._epoch(row["created_at"])
    samples = int(evidence.get("samples") or 0)
    if (status in ("ACKNOWLEDGED", "MEASURING") and age >=
            MEASUREMENT_WINDOW_S and samples >= MIN_SAMPLES
            and isinstance(b, (int, float)) and isinstance(
                value, (int, float))):
        better = value < b if lower_is_better else value > b
        status = "IMPROVED" if better else "NOT_IMPROVED"
    elif status == "ACKNOWLEDGED":
        status = "MEASURING"
    if status != row["status"]:
        await conn.execute(
            "UPDATE paper_recommendations SET status=$2, updated_at=$3 "
            " WHERE recommendation_id=$1", rid, status, L._ts(at))
        await _event(conn, rid, actor="AUDREY", kind="STATUS",
                     body="status %s -> %s" % (row["status"], status),
                     detail=detail, at=at)
    return dict(out, measured=True, status=status)


# ═════════════════════════════════════════════════════════════════════
# THE AUDIT
# ═════════════════════════════════════════════════════════════════════

async def audit(conn, ctx: dict, *, now: float) -> dict:
    acct = ctx["account_id"]
    since = now - WINDOW_S
    res: dict[str, Any] = {"version": VERSION, "at": now,
                           "window_s": WINDOW_S, "findings": [],
                           "recommendations": []}
    fun = await funnel(conn, acct, since=since)
    fid = await _finding(conn, ctx, kind="FUNNEL", severity="INFO", at=now,
                         detail={"by_strategy": fun})
    res["findings"].append(fid)
    res["funnel"] = fun

    cut = await book_cut_rate(conn, acct, since=since)
    sev = "WARNING" if (cut["rate"] or 0) > 0.05 else "INFO"
    fid = await _finding(conn, ctx, kind="BOOK_READ_CUTS", severity=sev,
                         at=now, detail=cut)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="BOOK_READ_CUTS", metric="book_read_cut_rate_6h",
        value=cut["rate"], open_now=(cut["rate"] or 0) > 0.05,
        text=("%d of %d book reads in the last 6 h were cut by the decision "
              "deadline or the venue cooldown; remove the duplicate read and "
              "retry within the freshness window" % (cut["cut"],
                                                     cut["book_reads"])),
        evidence=dict(cut, samples=cut["book_reads"]), finding_id=fid,
        at=now))
    res["book_reads"] = cut

    fc = await fees_consume(conn, acct, since=since)
    fid = await _finding(conn, ctx, kind="FEES_CONSUME_EDGE", severity=(
        "WARNING" if fc["fees_consumed_edge"] else "INFO"), at=now, detail=fc)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="FEES_CONSUME_EDGE",
        metric="maker_resting_orders_placed_6h",
        value=fc["maker_resting_orders_placed"],
        open_now=fc["fees_consumed_edge"] > 0,
        text=("%d decisions cleared the 0.5 pp gross threshold but the taker "
              "fee consumed the edge; rest a bid below the ask where both "
              "rules hold at the price paid" % fc["fees_consumed_edge"]),
        evidence=dict(fc, samples=fc["maker_decisions"]), finding_id=fid,
        at=now, lower_is_better=False))
    res["fees_consume"] = fc

    st = await stale_rate(conn, acct, since=since)
    fid = await _finding(conn, ctx, kind="STALE_INPUTS", severity=(
        "WARNING" if (st["rate"] or 0) > 0.2 else "INFO"), at=now, detail=st)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="STALE_INPUTS", metric="stale_input_refusal_rate_6h",
        value=st["rate"], open_now=(st["rate"] or 0) > 0.2,
        text=("%d of %d decisions were refused for stale inputs; shorten the "
              "valuation-to-decision path without relaxing freshness"
              % (st["stale"], st["decisions"])),
        evidence=dict(st, samples=st["decisions"]), finding_id=fid, at=now))
    res["stale"] = st

    md = await missing_decisions(conn, since=since, now=now)
    fid = await _finding(conn, ctx, kind="MISSING_DECISIONS", severity=(
        "WARNING" if md["total"] else "INFO"), at=now, detail=md)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="MISSING_DECISIONS",
        metric="valuations_without_a_decision_6h", value=md["total"],
        open_now=md["total"] > 0,
        text=("%d entry-experiment valuations have no decision from an "
              "enabled strategy; record every attempt and decide the misses"
              % md["total"]),
        evidence=dict(md, samples=md["total"]), finding_id=fid, at=now))
    res["missing"] = md

    fa = await fee_audit(conn, acct, since=since)
    fid = await _finding(conn, ctx, kind="FEES", severity=(
        "WARNING" if fa["fills_differing"] or fa["decisions_differing"]
        else "INFO"), at=now, detail=fa)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="FEE_ROUNDING", metric="fills_fee_differs_6h",
        value=fa["fills_differing"],
        open_now=fa["fills_differing"] > 0 or fa["decisions_differing"] > 0,
        text=("%d fills and %d decisions differ from the published fee "
              "schedule by a cent or more" % (fa["fills_differing"],
                                             fa["decisions_differing"])),
        evidence=dict(fa, samples=fa["fills_checked"]), finding_id=fid,
        at=now))
    res["fees"] = fa

    cov = await coverage(conn)
    fid = await _finding(conn, ctx, kind="COLLECTION_COVERAGE",
                         severity="INFO", at=now, detail=cov)
    unmatched = sum(int(v) for k, v in (cov["last_cycle_refusals"] or {}
                                        ).items())
    res["recommendations"].append(await recommend(
        conn, ctx, kind="COLLECTION_COVERAGE",
        metric="last_cycle_unmatched_events", value=unmatched,
        open_now=unmatched > 0,
        text=("%d events in the last collection cycle never reached a "
              "decision (by code: %s); investigate per sport without "
              "weakening identity, market or settlement matching"
              % (unmatched, json.dumps(cov["last_cycle_refusals"]))),
        evidence=dict(cov, samples=unmatched), finding_id=fid, at=now))
    res["coverage"] = cov

    ur = await unreviewed(conn, acct, now=now)
    fid = await _finding(conn, ctx, kind="POSITION_MANAGEMENT", severity=(
        "WARNING" if ur["unreviewed_10min"] else "INFO"), at=now, detail=ur)
    res["recommendations"].append(await recommend(
        conn, ctx, kind="UNREVIEWED_POSITIONS",
        metric="held_groups_unreviewed_10min", value=ur["unreviewed_10min"],
        open_now=ur["unreviewed_10min"] > 0,
        text=("%d held groups have no Xavier review in the last 10 minutes"
              % ur["unreviewed_10min"]),
        evidence=dict(ur, samples=ur["held_groups"]), finding_id=fid,
        at=now))
    res["management"] = ur
    return res


async def step(conn, ctx: dict) -> dict:
    """At most every AUDIT_EVERY_S (a watermark in ingestion_state)."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    try:
        present = await conn.fetchval(
            "SELECT to_regclass('paper_recommendations') IS NOT NULL")
        if not present:
            return {"ran": False, "why": "MIGRATION_189_NOT_APPLIED"}
        last = L._j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1",
            WATERMARK_KEY)) or {}
        if last.get("at") is not None and \
                at - float(last["at"]) < AUDIT_EVERY_S:
            return {"ran": False, "why": "NOT_DUE",
                    "last_at": last.get("at"), "every_s": AUDIT_EVERY_S}
        res = await audit(conn, ctx, now=at)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            WATERMARK_KEY, json.dumps({"at": at, "version": VERSION,
                                       "findings": len(res["findings"])},
                                      default=str))
        return {"ran": True, "findings": len(res["findings"]),
                "recommendations": [
                    {k: r.get(k) for k in ("kind", "owner", "opened",
                                           "measured", "status")}
                    for r in res["recommendations"]]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:200])}
