"""THE OPPORTUNITY SCORE V1 / V2 SHADOW TOURNAMENT (R30C). SHADOW, NO
AUTHORITY.

WHAT IS RECORDED (migration 233, opportunity_score_tournament, append-only):
for EVERY canonical decision intent, at its decision instant, BOTH scores
from the same inputs --

  V1  LOL_OPPORTUNITY_SCORE_V1 (lost_opportunity/score), the score the
      intent already carries (canonical_components.opportunity_at_decision)
  V2  LOL_OPPORTUNITY_SCORE_V2_LCB (opportunity_score_v2), the
      uncertainty-adjusted lower-confidence-bound score, with its frozen
      spec sha

-- keyed by the intent (id, version, sha) and by the UNIQUE OPPORTUNITY it is
(`opportunity_key`): the intent's own opportunity_id when it carries one,
else the derived key event / market / side / line / scope. Re-evaluations of
one opportunity are separate rows and are counted, never pooled as new
evidence.

WHAT IS JOINED LATER (`intent_outcome` / `opportunities` / `compute` here;
the bounded SELECTs in api/command_opportunity_tournament.gather): the
resolved net economics of
each unique opportunity from the paper ledger (bettor_paper_ledger.positions:
realized P&L after fees) -- INVESTMENT sleeve only, FORWARD only (decisions at
or after the R30 production cutover; none recorded -> NOT_ESTABLISHED). An
opportunity's score is the one recorded at its FIRST forward decision; its
outcome is the sum over every intent of it. A terminal paper order that never
filled realized a MEASURED zero (nothing was traded); an order still open, a
position not yet resolved, or no paper order at all is excluded and counted.

THE REPORT, per score, on the opportunities BOTH scored (and on each own set):
Spearman rank correlation with realized net, top-k realized net (top
quintile), calibration of each score's own predicted net against realized
net, n, independent events -- with 95% intervals from an EVENT-CLUSTER
bootstrap (opportunities on one event share one outcome, so events are
resampled, never opportunities) -- and the paired V2 - V1 differences.

V2 HAS NO AUTHORITY. Nothing reads a tournament row to decide anything; the
ENTER rule is untouched. `promotion_evidence` applies the PRE-DECLARED rule
below and, even when every check passes, says only
EVIDENCE_SUPPORTS_V2_PENDING_OWNER_DECISION: promotion is a later owner
decision and a code change, never automatic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random

from . import canonical_intent as CI

log = logging.getLogger(__name__)

VERSION = "OPPORTUNITY_SCORE_TOURNAMENT_V1"
KEY_VERSION = "TOURNAMENT_OPPORTUNITY_KEY_V1"
AUTHORITY = "SHADOW_NO_AUTHORITY"
MEASURED, UNAVAILABLE = "MEASURED", "UNAVAILABLE"
INVESTMENT = CI.INVESTMENT

#: the pre-declared promotion-evidence rule (changing it is a new VERSION)
MIN_EVENTS = 30
TOP_SHARE = 0.20
BOOTSTRAP_RESAMPLES = 2000
DRAW_BUDGET = 300000
CAL_BUCKETS = 5
PROMOTION_RULE = {
    "version": VERSION,
    "population": ("unique opportunities, INVESTMENT sleeve, decided at or "
                   "after the R30 production cutover, both scores MEASURED, "
                   "outcome resolved"),
    "checks": {
        "MIN_INDEPENDENT_EVENTS": ">= %d independent events" % MIN_EVENTS,
        "RANK_CORRELATION_GAIN": ("event-cluster bootstrap 95% interval of "
                                  "Spearman(V2) - Spearman(V1) lies above 0"),
        "TOP_K_GAIN": ("event-cluster bootstrap 95%% interval of mean "
                       "realized net of V2's top %d%% minus V1's top %d%% "
                       "lies above 0" % (int(TOP_SHARE * 100),
                                         int(TOP_SHARE * 100))),
    },
    "outcome": ("EVIDENCE_SUPPORTS_V2_PENDING_OWNER_DECISION when every check "
                "passes -- NEVER an automatic promotion; V2 has no authority "
                "until the owner decides and a new migration and code change "
                "give it one"),
}

R_NO_CUTOVER = "NO_PRODUCTION_CUTOVER_RECORDED"


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v, n=9):
    return None if v is None else round(float(v), n)


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


# ═════════════════════════════════════════════════════════════════════
# THE ENTRY (pure)
# ═════════════════════════════════════════════════════════════════════

def opportunity_key(intent: dict) -> dict:
    """THE UNIQUE OPPORTUNITY an intent is. The intent's own opportunity_id
    when it carries one (the canonical intent's, forward-compatible);
    otherwise derived from the intent's contract: event, market, side, line,
    scope (the sleeve). Pure."""
    contract = _j(intent.get("contract")) or {}
    evidence = _j(intent.get("evidence")) or {}
    own = intent.get("opportunity_id") or evidence.get("opportunity_id")
    event = (contract.get("event_key") or contract.get("fixture")
             or intent.get("us_market_slug"))
    key = {"event": event, "market": intent.get("us_market_slug"),
           "side": intent.get("holding_side"),
           "line": contract.get("line"),
           "scope": intent.get("sleeve") or CI.sleeve_of(
               intent.get("strategy"))}
    if own:
        return {"opportunity_id": str(own),
                "version": str(intent.get("opportunity_key_version")
                               or evidence.get("opportunity_key_version")
                               or "CANONICAL_INTENT"),
                "key": key, "event_key": str(event)}
    oid = "opp_" + hashlib.sha256(
        CI.canonical_json(key).encode()).hexdigest()[:24]
    return {"opportunity_id": oid, "version": KEY_VERSION, "key": key,
            "event_key": str(event)}


def v1_predicted_net(v1: dict):
    """V1's own predicted net USD: E[net | fill] x P(fill) x capacity."""
    ev = _num((v1 or {}).get("expected_net_executable_ev_usd"))
    fp = _num((v1 or {}).get("fill_probability"))
    cf = _num((v1 or {}).get("capacity_factor"))
    if (v1 or {}).get("score_basis") == "MEASURED_ZERO_NO_POSITIVE_NET_LEVEL":
        return 0.0
    if None in (ev, fp, cf):
        return None
    return ev * fp * cf


def build_entry(*, intent: dict, v1: dict | None, v2: dict | None) -> dict:
    """ONE TOURNAMENT ROW for one canonical decision intent. Pure. A score
    that is not MEASURED is UNAVAILABLE with its reason (never 0)."""
    from .lost_opportunity import score as SC
    from . import opportunity_score_v2 as V2
    ok = opportunity_key(intent)
    v1 = v1 or {"status": UNAVAILABLE, "why": "V1_NOT_COMPUTED"}
    v2 = v2 or {"status": UNAVAILABLE, "why": "V2_NOT_COMPUTED"}
    s1 = _num(v1.get("opportunity_score"))
    s2 = _num(v2.get("opportunity_score"))
    st1 = MEASURED if (v1.get("status") == MEASURED and s1 is not None) \
        else UNAVAILABLE
    st2 = MEASURED if (v2.get("status") == MEASURED and s2 is not None) \
        else UNAVAILABLE
    return {
        "entry_id": "ost_" + hashlib.sha256(
            ("OST:" + intent["intent_id"]).encode()).hexdigest()[:24],
        "tournament_version": VERSION,
        "intent_id": intent["intent_id"],
        "intent_version": intent["intent_version"],
        "intent_sha": intent["content_sha"],
        "decision_id": intent["decision_id"],
        "opportunity_id": ok["opportunity_id"],
        "opportunity_key_version": ok["version"],
        "opportunity_key": ok["key"], "event_key": ok["event_key"],
        "strategy": intent["strategy"], "sleeve": intent["sleeve"],
        "us_market_slug": intent["us_market_slug"],
        "holding_side": intent["holding_side"],
        "decided_at": float(intent["created_at"]),
        "v1_version": v1.get("version") or SC.VERSION,
        "v1_status": st1, "v1_score": s1 if st1 == MEASURED else None,
        "v1_predicted_net_usd": (v1_predicted_net(v1) if st1 == MEASURED
                                 else None),
        "v1_why": None if st1 == MEASURED else (
            v1.get("why") or "V1_NOT_MEASURED"),
        "v1_detail": {k: v1.get(k) for k in (
            "score_basis", "expected_net_executable_ev_usd",
            "fill_probability", "fill_probability_evidence",
            "capacity_factor", "capital_hours", "unmeasured")},
        "v2_version": v2.get("version") or V2.VERSION,
        "v2_spec_sha": v2.get("spec_sha") or V2.SPEC_SHA,
        "v2_status": st2, "v2_score": s2 if st2 == MEASURED else None,
        "v2_predicted_net_lcb_usd": (_num(v2.get("predicted_net_lcb_usd"))
                                     if st2 == MEASURED else None),
        "v2_why": None if st2 == MEASURED else (
            v2.get("why") or "V2_NOT_MEASURED"),
        "v2_detail": {k: v2.get(k) for k in (
            "score_basis", "components", "unmeasured",
            "execution_evidence")},
        "authority": AUTHORITY}


async def record_entry(conn, *, intent: dict, v1: dict | None,
                       v2: dict | None) -> bool:
    """Record both scores of one intent at its decision instant, in a
    savepoint; idempotent on the intent. Never raises into the caller (a
    tournament failure never touches the decision it rides on)."""
    try:
        e = build_entry(intent=intent, v1=v1, v2=v2)
        async with conn.transaction():
            got = await conn.fetchval(
                """INSERT INTO opportunity_score_tournament (entry_id,
                     tournament_version, intent_id, intent_version,
                     intent_sha, decision_id, opportunity_id,
                     opportunity_key_version, opportunity_key, event_key,
                     strategy, sleeve, us_market_slug, holding_side,
                     decided_at, v1_version, v1_status, v1_score,
                     v1_predicted_net_usd, v1_why, v1_detail, v2_version,
                     v2_spec_sha, v2_status, v2_score,
                     v2_predicted_net_lcb_usd, v2_why, v2_detail, authority)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,
                     $14,to_timestamp($15),$16,$17,$18,$19,$20,$21::jsonb,
                     $22,$23,$24,$25,$26,$27,$28::jsonb,$29)
                   ON CONFLICT (intent_id) DO NOTHING RETURNING entry_id""",
                e["entry_id"], e["tournament_version"], e["intent_id"],
                e["intent_version"], e["intent_sha"], e["decision_id"],
                e["opportunity_id"], e["opportunity_key_version"],
                json.dumps(e["opportunity_key"], default=str), e["event_key"],
                e["strategy"], e["sleeve"], e["us_market_slug"],
                e["holding_side"], e["decided_at"], e["v1_version"],
                e["v1_status"], e["v1_score"], e["v1_predicted_net_usd"],
                e["v1_why"], json.dumps(CI._norm(e["v1_detail"]),
                                        default=str),
                e["v2_version"], e["v2_spec_sha"], e["v2_status"],
                e["v2_score"], e["v2_predicted_net_lcb_usd"], e["v2_why"],
                json.dumps(CI._norm(e["v2_detail"]), default=str),
                e["authority"])
        return bool(got)
    except Exception:                                         # noqa: BLE001
        log.warning("tournament entry not recorded for %s",
                    (intent or {}).get("intent_id"), exc_info=True)
        return False


# ═════════════════════════════════════════════════════════════════════
# THE OUTCOME JOIN (pure)
# ═════════════════════════════════════════════════════════════════════

PAPER_TERMINAL = ("FILLED", "CANCELED", "EXPIRED", "REJECTED")


def intent_outcome(order: dict | None, positions: list) -> dict:
    """The resolved net economics of ONE intent's paper execution. Pure.

    no paper order                  -> EXCLUDED (NO_PAPER_ORDER)
    order open / pending            -> UNRESOLVED (ORDER_NOT_TERMINAL)
    terminal, never filled          -> RESOLVED, net 0 (a measured zero:
                                       nothing traded, nothing paid)
    filled, any position still open -> UNRESOLVED (POSITION_OPEN)
    filled, all positions closed    -> RESOLVED, net = sum of the ledger's
                                       realized P&L (fees included)"""
    if not order:
        return {"state": "EXCLUDED", "why": "NO_PAPER_ORDER",
                "realized_net_usd": None}
    filled = _num(order.get("filled_qty")) or 0.0
    if filled <= 1e-9:
        if order.get("state") in PAPER_TERMINAL:
            return {"state": "RESOLVED", "why": None, "realized_net_usd": 0.0,
                    "basis": "TERMINAL_ORDER_NEVER_FILLED_MEASURED_ZERO"}
        return {"state": "UNRESOLVED", "why": "ORDER_NOT_TERMINAL",
                "realized_net_usd": None}
    if not positions:
        return {"state": "UNRESOLVED", "why": "FILLED_BUT_NO_LEDGER_POSITION",
                "realized_net_usd": None}
    if any((_num(p.get("open_qty")) or 0.0) > 1e-9 for p in positions):
        return {"state": "UNRESOLVED", "why": "POSITION_OPEN",
                "realized_net_usd": None}
    nets = [_num(p.get("realized_pnl_usd")) for p in positions]
    if any(v is None for v in nets):
        return {"state": "UNRESOLVED", "why": "REALIZED_PNL_UNREADABLE",
                "realized_net_usd": None}
    return {"state": "RESOLVED", "why": None,
            "realized_net_usd": sum(nets),
            "basis": "LEDGER_REALIZED_PNL_AFTER_FEES"}


def opportunities(entries: list, outcomes: dict) -> tuple:
    """Unique opportunities from tournament entries (oldest first) and
    {intent_id: intent_outcome}. Returns (rows, counts). Pure."""
    by: dict = {}
    for e in sorted(entries, key=lambda r: (_num(r.get("decided_at")) or 0,
                                            r.get("intent_id"))):
        by.setdefault(e["opportunity_id"], []).append(e)
    rows, counts = [], {"opportunities": len(by), "resolved": 0,
                        "unresolved": 0, "excluded": 0,
                        "reevaluations": 0, "unresolved_why": {},
                        "excluded_why": {}}
    for oid, es in by.items():
        counts["reevaluations"] += len(es) - 1
        first = es[0]
        outs = [outcomes.get(e["intent_id"]) or {
            "state": "EXCLUDED", "why": "NO_OUTCOME_READ"} for e in es]
        traded = [o for o in outs if o["state"] != "EXCLUDED"]
        if not traded:
            counts["excluded"] += 1
            w = outs[0].get("why") or "EXCLUDED"
            counts["excluded_why"][w] = counts["excluded_why"].get(w, 0) + 1
            continue
        if any(o["state"] == "UNRESOLVED" for o in traded):
            counts["unresolved"] += 1
            w = next(o["why"] for o in traded if o["state"] == "UNRESOLVED")
            counts["unresolved_why"][w] = counts["unresolved_why"].get(w,
                                                                       0) + 1
            continue
        counts["resolved"] += 1
        rows.append({
            "opportunity_id": oid, "event_key": first.get("event_key"),
            "decided_at": _num(first.get("decided_at")),
            "intents": len(es),
            "v1": _num(first.get("v1_score")) if first.get(
                "v1_status") == MEASURED else None,
            "v2": _num(first.get("v2_score")) if first.get(
                "v2_status") == MEASURED else None,
            "v1_pred": _num(first.get("v1_predicted_net_usd")),
            "v2_pred": _num(first.get("v2_predicted_net_lcb_usd")),
            "realized_net_usd": sum(o["realized_net_usd"] for o in traded)})
    return rows, counts


# ═════════════════════════════════════════════════════════════════════
# THE STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def ranks(xs: list) -> list:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return out


def spearman(xs: list, ys: list):
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def top_k_mean(rows: list, key: str):
    """Mean realized net of the top TOP_SHARE of `rows` by score `key`
    (at least one), ties broken by opportunity id (deterministic)."""
    if not rows:
        return None
    k = max(1, int(math.ceil(len(rows) * TOP_SHARE)))
    top = sorted(rows, key=lambda r: (-r[key], r["opportunity_id"]))[:k]
    return sum(r["realized_net_usd"] for r in top) / k


def ols_slope(xs: list, ys: list):
    if len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _seed(obj) -> int:
    return int(hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                              .encode()).hexdigest()[:12], 16)


def cluster_bootstrap(rows: list, stat, *, seed: int,
                      resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """Percentile 95% interval of `stat(rows)` resampling INDEPENDENT EVENTS
    (event_key clusters) with replacement. Bounded draws; None below two
    events or when the statistic is undefined on most resamples."""
    by: dict = {}
    for r in rows:
        by.setdefault(r.get("event_key") or r["opportunity_id"], []).append(r)
    events = sorted(by)
    g = len(events)
    if g < 2:
        return {"ci_low": None, "ci_high": None, "resamples": 0,
                "why": "FEWER_THAN_TWO_INDEPENDENT_EVENTS"}
    rng = random.Random(seed)
    k = max(200, min(resamples, DRAW_BUDGET // max(1, len(rows))))
    vals = []
    for _ in range(k):
        sample = []
        for _ in range(g):
            sample.extend(by[events[rng.randrange(g)]])
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < k * 0.5:
        return {"ci_low": None, "ci_high": None, "resamples": k,
                "why": "STATISTIC_UNDEFINED_ON_MOST_RESAMPLES"}
    vals.sort()

    def q(p):
        pos = p * (len(vals) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] * (1 - (pos - lo)) + vals[hi] * (pos - lo)
    return {"ci_low": _r(q(0.025)), "ci_high": _r(q(0.975)),
            "resamples": k, "method": "EVENT_CLUSTER_BOOTSTRAP_PERCENTILE",
            "why": None}


def score_report(rows: list, key: str, pred: str, *, seed: int) -> dict:
    """One score's report over rows that carry it. Pure."""
    rs = [r for r in rows if r.get(key) is not None]
    n = len(rs)
    events = len({r.get("event_key") or r["opportunity_id"] for r in rs})
    out = {"n_opportunities": n, "n_independent_events": events}
    if n < 3:
        out.update(status=UNAVAILABLE,
                   why="FEWER_THAN_3_RESOLVED_SCORED_OPPORTUNITIES")
        return out

    def rho(s):
        return spearman([r[key] for r in s], [r["realized_net_usd"]
                                              for r in s])
    r0 = rho(rs)
    out["rank_correlation"] = dict(
        {"value": _r(r0), "statistic": "SPEARMAN_SCORE_VS_REALIZED_NET"},
        **cluster_bootstrap(rs, rho, seed=seed))
    tk = top_k_mean(rs, key)
    out["top_k_realized_net"] = dict(
        {"value_mean_usd": _r(tk, 6),
         "k": max(1, int(math.ceil(n * TOP_SHARE))),
         "share": TOP_SHARE,
         "all_mean_usd": _r(sum(r["realized_net_usd"] for r in rs) / n, 6)},
        **cluster_bootstrap(rs, lambda s: top_k_mean(s, key),
                            seed=seed + 1))
    cal_rows = [r for r in rs if r.get(pred) is not None]
    cal = {"n": len(cal_rows), "predicted": pred,
           "basis": ("the score's own predicted net USD at the decision vs "
                     "the opportunity's realized net")}
    if len(cal_rows) >= 3:
        xs = [r[pred] for r in cal_rows]
        ys = [r["realized_net_usd"] for r in cal_rows]
        cal["mean_predicted_usd"] = _r(sum(xs) / len(xs), 6)
        cal["mean_realized_usd"] = _r(sum(ys) / len(ys), 6)
        cal["slope_realized_on_predicted"] = _r(ols_slope(xs, ys))
        cal.update({"slope_" + k: v for k, v in cluster_bootstrap(
            cal_rows, lambda s: ols_slope([r[pred] for r in s],
                                          [r["realized_net_usd"]
                                           for r in s]),
            seed=seed + 2).items()})
        srt = sorted(cal_rows, key=lambda r: (r[key], r["opportunity_id"]))
        b = min(CAL_BUCKETS, len(srt))
        cal["buckets"] = []
        for i in range(b):
            chunk = srt[i * len(srt) // b:(i + 1) * len(srt) // b]
            if chunk:
                cal["buckets"].append({
                    "bucket": i + 1, "n": len(chunk),
                    "mean_score": _r(sum(r[key] for r in chunk)
                                     / len(chunk)),
                    "mean_predicted_usd": _r(sum(r[pred] for r in chunk)
                                             / len(chunk), 6),
                    "mean_realized_usd": _r(sum(r["realized_net_usd"]
                                                for r in chunk)
                                            / len(chunk), 6)})
    else:
        cal["why"] = "FEWER_THAN_3_WITH_A_PREDICTED_NET"
    out["calibration"] = cal
    out["status"] = MEASURED
    out["why"] = None
    return out


def compute(entries: list, outcomes: dict, *, since, cutover) -> dict:
    """THE TOURNAMENT REPORT (pure). `entries`: tournament rows of the
    INVESTMENT sleeve decided at or after `since`; `outcomes`:
    {intent_id: intent_outcome(...)}."""
    base = {"version": VERSION, "authority": AUTHORITY, "sleeve": INVESTMENT,
            "since": since, "cutover": cutover,
            "promotion_rule": PROMOTION_RULE,
            "enter_rule_changed": False,
            "v2_authority": ("NONE: V2 is recorded beside each canonical "
                             "intent, never in it; nothing reads it to "
                             "decide")}
    if cutover is None:
        return dict(base, status="NOT_ESTABLISHED", why=R_NO_CUTOVER,
                    entries_recorded=len(entries),
                    note=("entries recorded before a production cutover are "
                          "not forward evidence"))
    inv = [e for e in entries if e.get("sleeve") == INVESTMENT
           and (_num(e.get("decided_at")) or 0) >= float(since)]
    rows, counts = opportunities(inv, outcomes)
    seed = _seed([VERSION, sorted((r["opportunity_id"], r["realized_net_usd"])
                                  for r in rows)])
    both = [r for r in rows if r["v1"] is not None and r["v2"] is not None]
    rep = {
        "entries": len(inv), "counts": counts,
        "coverage": {
            "v1_measured": sum(1 for e in inv if e.get("v1_status")
                               == MEASURED),
            "v2_measured": sum(1 for e in inv if e.get("v2_status")
                               == MEASURED),
            "v1_unavailable_why": _why_counts(inv, "v1"),
            "v2_unavailable_why": _why_counts(inv, "v2")},
        "common": {"V1": score_report(both, "v1", "v1_pred", seed=seed),
                   "V2": score_report(both, "v2", "v2_pred", seed=seed)},
        "own": {"V1": score_report(rows, "v1", "v1_pred", seed=seed + 7),
                "V2": score_report(rows, "v2", "v2_pred", seed=seed + 7)},
    }
    rep["paired_v2_minus_v1"] = paired(both, seed=seed + 11)
    rep["promotion_evidence"] = promotion(both, rep["paired_v2_minus_v1"])
    return dict(base, status="OK", why=None, **rep)


def _why_counts(entries: list, which: str) -> dict:
    out: dict = {}
    for e in entries:
        if e.get(which + "_status") != MEASURED:
            w = str(e.get(which + "_why") or "UNKNOWN").split(":")[0][:80]
            out[w] = out.get(w, 0) + 1
    return out


def paired(both: list, *, seed: int) -> dict:
    """V2 - V1 on the opportunities both scored: rank correlation and top-k
    realized net, with event-cluster bootstrap intervals. Pure."""
    def d_rho(s):
        a = spearman([r["v2"] for r in s], [r["realized_net_usd"] for r in s])
        b = spearman([r["v1"] for r in s], [r["realized_net_usd"] for r in s])
        return None if a is None or b is None else a - b

    def d_top(s):
        a, b = top_k_mean(s, "v2"), top_k_mean(s, "v1")
        return None if a is None or b is None else a - b
    if len(both) < 3:
        return {"status": UNAVAILABLE, "n": len(both),
                "why": "FEWER_THAN_3_OPPORTUNITIES_SCORED_BY_BOTH"}
    return {"status": MEASURED, "n": len(both),
            "rank_correlation_diff": dict({"value": _r(d_rho(both))},
                                          **cluster_bootstrap(
                                              both, d_rho, seed=seed)),
            "top_k_mean_diff_usd": dict({"value": _r(d_top(both), 6)},
                                        **cluster_bootstrap(
                                            both, d_top, seed=seed + 1))}


def promotion(both: list, pair: dict) -> dict:
    """The pre-declared promotion-evidence rule. NEVER an automatic
    promotion: the best outcome is PENDING_OWNER_DECISION. Pure."""
    events = len({r.get("event_key") or r["opportunity_id"] for r in both})
    rc = (pair or {}).get("rank_correlation_diff") or {}
    tk = (pair or {}).get("top_k_mean_diff_usd") or {}
    checks = {
        "MIN_INDEPENDENT_EVENTS": events >= MIN_EVENTS,
        "RANK_CORRELATION_GAIN": (rc.get("ci_low") is not None
                                  and rc["ci_low"] > 0),
        "TOP_K_GAIN": tk.get("ci_low") is not None and tk["ci_low"] > 0}
    failed = [k for k, ok in checks.items() if not ok]
    if not checks["MIN_INDEPENDENT_EVENTS"]:
        st = "INSUFFICIENT_OUT_OF_SAMPLE_EVIDENCE"
    elif failed:
        st = "V2_NOT_SHOWN_BETTER"
    else:
        st = "EVIDENCE_SUPPORTS_V2_PENDING_OWNER_DECISION"
    return {"status": st, "independent_events": events,
            "min_independent_events": MIN_EVENTS, "checks": checks,
            "failed_checks": failed, "automatic_promotion": False,
            "authority_granted": "NONE"}
