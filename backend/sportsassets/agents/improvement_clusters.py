"""ROOT-CAUSE IMPROVEMENT CLUSTERS (owner R30 program section 20, migration
301 §3): REPEATED KAREN / AUDREY FINDINGS BECOME ONE ENGINEERING WORK ITEM.

THE DEFECT (production read-only evidence, research-sql run 37226555657,
2026-10-04 19:00Z). Karen's HOLD_ON_STALE_PROBABILITY detector has 684
UPHELD challenges -- 684 distinct reviews of 7 groups, one strategy
(PINNACLE_EXPLORATION_PAPER), every one recorded between 2026-10-01 20:43Z
and 2026-10-02 00:09Z -- and the improvement pipeline seeded 232 separate
items from them (one per challenge). Meanwhile the rule itself held on
9,978 reviews on 2026-10-04 alone: the challenges are a THROTTLED SAMPLE
(three per detector per pass, oldest first) of a defect that kept running,
the items are a reporting sink, and nothing measured whether a fix took.

WHAT THIS DOES.

  refresh(conn)  (a paper-pass step, on the main account, at most every
                 REFRESH_EVERY_S)
    1. CLUSTERS the repeated findings: Karen's UPHELD challenges by
       (detector, target agent), Audrey's WARNING / CRITICAL findings by
       kind -- each class with at least REPEAT_MIN members is ONE row of
       improvement_clusters (cluster key "SOURCE|class|target"), opened by
       the runner with an OPENED event. The owner is the agent whose records
       are defective (Karen's target; Audrey for her own findings).
    2. MEASURES THE EFFECT of a linked fix: once a person has linked a fix
       (FIX_LINKED: a 40-hex commit SHA and the instant it took effect) and
       MIN_POST_FIX_S has passed, the defect rate BEFORE and AFTER the fix
       is measured and recorded (EFFECT_MEASURED, at most every
       MEASURE_EVERY_S):
         * for a Karen detector whose rule is a predicate over a table
           (karen_runner.RULES), the rate is THE RULE ITSELF over that
           table: the share of its rows in each window where the predicate
           holds -- never the challenge count, which Karen throttles;
         * otherwise the members' rate per day (labelled as such).
       FIX_EFFECTIVE only when the drop is SIGNIFICANT (the 95% interval of
       after - before lies wholly below zero) AND MATERIAL: the after
       window's 95% upper bound is at most max(TARGET_RATE, (1 -
       MIN_RELATIVE_REDUCTION) x the before rate) -- the fix removed at
       least 90% of the defect (members per day: the rate ratio's upper
       bound at most 1 - MIN_RELATIVE_REDUCTION). Significant but not
       material is FIX_PARTIALLY_EFFECTIVE (R30B review: production's own
       10-03 -> 10-04 figures, a stale HOLD on 97.6% then 62.4% of
       reviews, had read FIX_EFFECTIVE while ~10,000 stale HOLDs a day
       continued). FIX_NOT_EFFECTIVE when the interval lies at or above
       zero; otherwise FIX_LINKED (inconclusive). Small windows are
       INSUFFICIENT_SAMPLE, a missing window UNAVAILABLE with its reason.
       MEASURING NEVER STOPS while a fix is linked: FIX_EFFECTIVE, partial
       and not-effective clusters are re-measured every MEASURE_EVERY_S on
       the most recent post-fix window, so a regression moves the status
       back and re-raises Audrey's triage (agents/agent_work.py).

  view(conn)     (GET /api/command/improvement-clusters, read only)
    every cluster: key, count (and the count per challenge state), first /
    last seen, affected strategies / markets (from the challenged records),
    owner, status, the linked fix, the measured effect after it (live, or
    UNAVAILABLE: NO_FIX_LINKED), the rule's current defect rate, and the
    per-challenge improvement items the cluster now stands for.

  link_fix / assign_owner / close / reopen  -- the HUMAN steps, for a named
    person (the database refuses a machine name, and refuses them in the
    runner's session); nothing in the runner calls them. Their application
    path is the admin-token POST in api/command_improvement_clusters.py
    (the clear-halt pattern of api/command_live_parity.py): the operator
    names the person, who is recorded as actor and recorded_by.

The improvement pipeline stops seeding one item per challenge of a class
that has a cluster (improvement_pipeline.seed_karen): the cluster is the
item.

NO AUTHORITY. Writes only improvement_clusters / improvement_cluster_events
(the runner's rows: OPENED, EFFECT_MEASURED). The linked fix is a TEXT
reference: nothing here merges, deploys, runs or approves anything. It
imports no order, venue, execution, funded or paper module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

log = logging.getLogger(__name__)

VERSION = "IMPROVEMENT_CLUSTERS_V1"
RUNNER = VERSION
REPEAT_MIN = 2
REFRESH_EVERY_S = 900.0
MEASURE_EVERY_S = 86400.0
MIN_POST_FIX_S = 6 * 3600.0
BASELINE_MAX_S = 7 * 86400.0
MIN_WINDOW_ROWS = 30
Z95 = 1.959964
MAX_DIM = 20
#: = bettor_paper_ledger.ACCOUNT_ID (agent_work.MAIN_PAPER_ACCOUNT, pinned)
MAIN_PAPER_ACCOUNT = "paper_acct_main"

S_OPEN, S_FIX_LINKED, S_EFFECTIVE, S_NOT_EFFECTIVE, S_CLOSED = (
    "OPEN", "FIX_LINKED", "FIX_EFFECTIVE", "FIX_NOT_EFFECTIVE", "CLOSED")
S_PARTIAL = "FIX_PARTIALLY_EFFECTIVE"
#: the statuses a linked fix keeps being measured in (never stops at
#: FIX_EFFECTIVE: a regression must be seen)
S_MEASURED = (S_FIX_LINKED, S_EFFECTIVE, S_PARTIAL, S_NOT_EFFECTIVE)
#: MATERIALITY: an effective fix removes at least this share of the defect
#: (the after window's 95% upper bound at most (1 - this) x the before
#: rate), or brings the rate under TARGET_RATE -- the rule's own near-zero
#: expectation (each Karen rule names a defect that should not occur)
MIN_RELATIVE_REDUCTION = 0.9
TARGET_RATE = 0.01
MEASURED, INSUFFICIENT, UNAVAILABLE = (
    "MEASURED", "INSUFFICIENT_SAMPLE", "UNAVAILABLE")
R_NO_SCHEMA = "MIGRATION_301_NOT_APPLIED"
R_NO_FIX = "NO_FIX_LINKED"
R_TOO_SOON = "LESS_THAN_%dH_SINCE_THE_FIX" % int(MIN_POST_FIX_S // 3600)
R_NO_BASELINE = "NO_RECORDS_BEFORE_THE_FIX"
R_NO_POST = "NO_RECORDS_SINCE_THE_FIX"

#: the time column of each table a Karen rule is a predicate over (a rule
#: over a table not named here is measured by its members' rate instead)
RULE_TIME = {"paper_xavier_reviews": "reviewed_at",
             "paper_decisions": "decided_at",
             "agent_decisions": "decided_at",
             "paper_audrey_findings": "found_at",
             "execution_intents": "created_at"}

#: the challenged record -> (strategy, market) of a Karen cluster's members
TARGET_DIMENSIONS = {
    "paper_xavier_reviews": (
        "SELECT count(DISTINCT r.group_id) AS subjects, "
        "       (array_agg(DISTINCT coalesce(r.measure->>'strategy', "
        "        r.strategy)))[1:%d] AS strategies, "
        "       count(DISTINCT coalesce(r.measure->>'strategy', r.strategy))"
        "         AS n_strategies, "
        "       (array_agg(DISTINCT o.us_market_slug) FILTER (WHERE "
        "        o.us_market_slug IS NOT NULL))[1:%d] AS markets, "
        "       count(DISTINCT o.us_market_slug) AS n_markets "
        "  FROM karen_challenges k "
        "  JOIN paper_xavier_reviews r ON r.review_id = k.target_id "
        "  LEFT JOIN LATERAL (SELECT po.us_market_slug FROM paper_orders po "
        "        WHERE po.group_id = r.group_id AND po.role = 'ENTRY' "
        "        ORDER BY po.created_at LIMIT 1) o ON true "
        " WHERE k.detector = $1 AND k.target_agent = $2 "
        "   AND k.state = 'UPHELD'" % (MAX_DIM, MAX_DIM)),
    "paper_decisions": (
        "SELECT count(DISTINCT d.decision_id) AS subjects, "
        "       (array_agg(DISTINCT d.strategy))[1:%d] AS strategies, "
        "       count(DISTINCT d.strategy) AS n_strategies, "
        "       (array_agg(DISTINCT d.us_market_slug))[1:%d] AS markets, "
        "       count(DISTINCT d.us_market_slug) AS n_markets "
        "  FROM karen_challenges k "
        "  JOIN paper_decisions d ON d.decision_id = k.target_id "
        " WHERE k.detector = $1 AND k.target_agent = $2 "
        "   AND k.state = 'UPHELD'" % (MAX_DIM, MAX_DIM)),
    # Audrey's findings (AUDIT_DISCREPANCY_LEFT_OPEN, production's second
    # largest class): the finding's own strategy and subject, as
    # AUDREY_DIMENSIONS reads them (R30B review: this had read
    # TARGET_KIND_CARRIES_NO_STRATEGY_OR_MARKET although the table carries
    # both)
    "paper_audrey_findings": (
        "SELECT count(DISTINCT f.finding_id) AS subjects, "
        "       (array_agg(DISTINCT f.detail->>'strategy') FILTER (WHERE "
        "        f.detail->>'strategy' IS NOT NULL))[1:%d] AS strategies, "
        "       count(DISTINCT f.detail->>'strategy') AS n_strategies, "
        "       (array_agg(DISTINCT f.subject) FILTER (WHERE f.subject IS "
        "        NOT NULL))[1:%d] AS markets, "
        "       count(DISTINCT f.subject) AS n_markets "
        "  FROM karen_challenges k "
        "  JOIN paper_audrey_findings f ON f.finding_id = k.target_id "
        " WHERE k.detector = $1 AND k.target_agent = $2 "
        "   AND k.state = 'UPHELD'" % (MAX_DIM, MAX_DIM)),
    # small-live reconciliations: the handed-off position's market (its
    # strategy is not recorded on the hand-off: none is claimed)
    "smalllive_reconciliations": (
        "SELECT count(DISTINCT r.group_id) AS subjects, "
        "       ARRAY[]::text[] AS strategies, 0 AS n_strategies, "
        "       (array_agg(DISTINCT h.us_market_slug) FILTER (WHERE "
        "        h.us_market_slug IS NOT NULL))[1:%d] AS markets, "
        "       count(DISTINCT h.us_market_slug) AS n_markets "
        "  FROM karen_challenges k "
        "  JOIN smalllive_reconciliations r ON r.group_id = k.target_id "
        "  LEFT JOIN smalllive_handoffs h ON h.group_id = r.group_id "
        " WHERE k.detector = $1 AND k.target_agent = $2 "
        "   AND k.state = 'UPHELD'" % MAX_DIM),
    "execution_intents": (
        "SELECT count(DISTINCT i.intent_id) AS subjects, "
        "       (array_agg(DISTINCT i.strategy))[1:%d] AS strategies, "
        "       count(DISTINCT i.strategy) AS n_strategies, "
        "       (array_agg(DISTINCT i.us_market_slug))[1:%d] AS markets, "
        "       count(DISTINCT i.us_market_slug) AS n_markets "
        "  FROM karen_challenges k "
        "  JOIN execution_intents i ON i.intent_id = k.target_id "
        " WHERE k.detector = $1 AND k.target_agent = $2 "
        "   AND k.state = 'UPHELD'" % (MAX_DIM, MAX_DIM)),
}
AUDREY_DIMENSIONS = (
    "SELECT count(DISTINCT subject) AS subjects, "
    "       (array_agg(DISTINCT detail->>'strategy') FILTER (WHERE "
    "        detail->>'strategy' IS NOT NULL))[1:%d] AS strategies, "
    "       count(DISTINCT detail->>'strategy') AS n_strategies, "
    "       (array_agg(DISTINCT subject) FILTER (WHERE subject IS NOT "
    "        NULL))[1:%d] AS markets, count(DISTINCT subject) AS n_markets "
    "  FROM paper_audrey_findings WHERE kind = $1 "
    "   AND severity IN ('WARNING', 'CRITICAL')" % (MAX_DIM, MAX_DIM))


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def cluster_key(source: str, finding_class: str, target: str | None) -> str:
    return "%s|%s|%s" % (source, finding_class, target or "*")


def cluster_id_for(key: str) -> str:
    return "rcc:" + _h("cluster", key)


# ═════════════════════════════════════════════════════════════════════
# THE MEASURED EFFECT (pure)
# ═════════════════════════════════════════════════════════════════════

def wilson_upper(k: int, n: int, z: float = Z95) -> float | None:
    """The Wilson score interval's upper bound of k / n. Pure."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = p + z * z / (2 * n)
    rad = z * math.sqrt(max(p * (1 - p) / n + z * z / (4 * n * n), 0.0))
    return min(1.0, (mid + rad) / den)


def material_target(p0: float) -> float:
    """The after-rate bound a fix must reach to count as effective. Pure."""
    return max(TARGET_RATE, (1.0 - MIN_RELATIVE_REDUCTION) * p0)


def proportion_effect(before: tuple, after: tuple) -> dict:
    """THE DEFECT RATE BEFORE vs AFTER A FIX from (hits, rows) per window.
    Pure. Difference of proportions with a 95% normal interval and the
    after rate's Wilson upper bound for materiality; a window with no rows
    is UNAVAILABLE, fewer than MIN_WINDOW_ROWS rows INSUFFICIENT_SAMPLE
    (shown, never concluded from)."""
    (k0, n0), (k1, n1) = before, after
    out: dict[str, Any] = {"basis": "RULE_PREDICATE_SHARE_OF_TABLE_ROWS",
                           "before": {"hits": k0, "rows": n0},
                           "after": {"hits": k1, "rows": n1}}
    if not n0:
        return dict(out, status=UNAVAILABLE, why=R_NO_BASELINE, verdict=None)
    if not n1:
        return dict(out, status=UNAVAILABLE, why=R_NO_POST, verdict=None)
    p0, p1 = k0 / n0, k1 / n1
    d = p1 - p0
    se = math.sqrt(max(p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1, 0.0))
    lo, hi = d - Z95 * se, d + Z95 * se
    small = min(n0, n1) < MIN_WINDOW_ROWS
    up1 = wilson_upper(k1, n1)
    target = material_target(p0)
    material = up1 is not None and up1 <= target
    verdict = ((S_EFFECTIVE if material else S_PARTIAL) if hi < 0
               else S_NOT_EFFECTIVE if lo >= 0 else None)
    out["before"]["rate"] = round(p0, 6)
    out["after"]["rate"] = round(p1, 6)
    out["after"]["upper_95"] = round(up1, 6)
    return dict(out, difference=round(d, 6), ci95=[round(lo, 6),
                                                   round(hi, 6)],
                materiality={"after_upper_95": round(up1, 6),
                             "target": round(target, 6),
                             "material": material,
                             "rule": "after upper bound <= max(%.2f, "
                                     "%.1f x before rate)" % (
                                         TARGET_RATE,
                                         1.0 - MIN_RELATIVE_REDUCTION)},
                status=INSUFFICIENT if small else MEASURED,
                why=("FEWER_THAN_%d_ROWS_IN_A_WINDOW" % MIN_WINDOW_ROWS
                     if small else None),
                verdict=None if small else verdict)


def rate_effect(before: tuple, after: tuple) -> dict:
    """MEMBERS PER DAY BEFORE vs AFTER A FIX from (count, days) per window.
    Pure. Poisson rates; the log rate ratio's 95% interval (a zero count
    gets the usual half-count continuity correction)."""
    (c0, d0), (c1, d1) = before, after
    out: dict[str, Any] = {"basis": "CLUSTER_MEMBERS_PER_DAY",
                           "before": {"count": c0, "days": round(d0, 4)},
                           "after": {"count": c1, "days": round(d1, 4)}}
    if d0 <= 0:
        return dict(out, status=UNAVAILABLE, why=R_NO_BASELINE, verdict=None)
    if d1 <= 0:
        return dict(out, status=UNAVAILABLE, why=R_NO_POST, verdict=None)
    r0, r1 = c0 / d0, c1 / d1
    a0, a1 = (c0 or 0.5), (c1 or 0.5)
    lrr = math.log((a1 / d1) / (a0 / d0))
    se = math.sqrt(1.0 / a0 + 1.0 / a1)
    lo, hi = lrr - Z95 * se, lrr + Z95 * se
    small = (c0 + c1) < 10
    material = math.exp(hi) <= 1.0 - MIN_RELATIVE_REDUCTION
    verdict = ((S_EFFECTIVE if material else S_PARTIAL) if hi < 0
               else S_NOT_EFFECTIVE if lo >= 0 else None)
    out["before"]["per_day"] = round(r0, 4)
    out["after"]["per_day"] = round(r1, 4)
    return dict(out, log_rate_ratio=round(lrr, 6),
                ci95=[round(lo, 6), round(hi, 6)],
                materiality={"rate_ratio_upper_95": round(math.exp(hi), 6),
                             "target": round(1.0 - MIN_RELATIVE_REDUCTION,
                                             6),
                             "material": material},
                status=INSUFFICIENT if small else MEASURED,
                why="FEWER_THAN_10_MEMBERS" if small else None,
                verdict=None if small else verdict)


def windows(fix_at: float, now: float) -> tuple:
    """(before_start, fix_at, after_start, now): the post-fix window is the
    most recent BASELINE_MAX_S since the fix (so a regression weeks after a
    fix is not diluted by the good days right after it); the baseline is
    the same length immediately before the fix."""
    length = min(max(now - fix_at, 0.0), BASELINE_MAX_S)
    return fix_at - length, fix_at, now - length, now


# ═════════════════════════════════════════════════════════════════════
# READS (SELECT only)
# ═════════════════════════════════════════════════════════════════════

async def _exists(conn, table: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        table))
    except Exception:                                           # noqa: BLE001
        return False


async def has_schema(conn) -> bool:
    return await _exists(conn, "improvement_cluster_events")


def _rule(detector: str):
    """(table, key, predicate, time column) of a Karen rule, or None."""
    try:
        from . import karen_runner as KR
    except Exception:                                           # noqa: BLE001
        return None
    r = KR.RULES.get(detector)
    if not r or r[0] not in RULE_TIME:
        return None
    return r[0], r[1], r[2], RULE_TIME[r[0]]


async def rule_rate(conn, detector: str, lo: float, hi: float) -> tuple | None:
    """(hits, rows) of a Karen rule over its own table in [lo, hi), or None
    when the rule is not a measurable table predicate."""
    rule = _rule(detector)
    if rule is None or not await _exists(conn, rule[0]):
        return None
    table, _key, pred, tcol = rule
    r = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE %s) AS hits, count(*) AS n FROM %s t "
        " WHERE t.%s >= to_timestamp($1) AND t.%s < to_timestamp($2)"
        % (pred, table, tcol, tcol), float(lo), float(hi))
    return int(r["hits"] or 0), int(r["n"] or 0)


async def _members(conn, c: dict) -> dict:
    """The cluster's members now: count, per-state counts, first / last
    seen (when the defect occurred: the challenged record's record_at /
    the finding's found_at)."""
    if c["source"] == "KAREN":
        rows = await conn.fetch(
            "SELECT state, count(*) AS n, min(record_at) AS first, "
            "       max(record_at) AS last, "
            "       (array_agg(challenge_id ORDER BY record_at DESC))[1:5] "
            "         AS ids FROM karen_challenges "
            " WHERE detector = $1 AND target_agent = $2 GROUP BY state",
            c["finding_class"], c["target_agent"])
        by = {r["state"]: r for r in rows}
        up = by.get("UPHELD")
        return {"count": int(up["n"]) if up else 0,
                "by_state": {r["state"]: int(r["n"]) for r in rows},
                "first_seen_at": _ep(up["first"]) if up else None,
                "last_seen_at": _ep(up["last"]) if up else None,
                "sample_ids": list(up["ids"] or []) if up else [],
                "member_basis": "karen_challenges UPHELD (detector x target)"}
    r = await conn.fetchrow(
        "SELECT count(*) AS n, min(found_at) AS first, max(found_at) AS last,"
        "       count(*) FILTER (WHERE severity = 'CRITICAL') AS critical, "
        "       count(*) FILTER (WHERE improvement_task_id IS NULL) AS "
        "         no_task, (array_agg(finding_id ORDER BY found_at DESC))"
        "         [1:5] AS ids FROM paper_audrey_findings WHERE kind = $1 "
        "   AND severity IN ('WARNING', 'CRITICAL')", c["finding_class"])
    return {"count": int(r["n"] or 0),
            "by_state": {"CRITICAL": int(r["critical"] or 0),
                         "WITHOUT_TASK": int(r["no_task"] or 0)},
            "first_seen_at": _ep(r["first"]), "last_seen_at": _ep(r["last"]),
            "sample_ids": list(r["ids"] or []),
            "member_basis": "paper_audrey_findings WARNING / CRITICAL (kind)"}


#: what a cluster's "markets" / "strategies" are, per challenged record
DIMENSION_BASIS = {
    "AUDREY": {"markets": "the finding's subject"},
    "paper_audrey_findings": {"markets": "the finding's subject"},
    "smalllive_reconciliations": {
        "strategies": "NOT_RECORDED_ON_THE_SMALL_LIVE_HANDOFF"},
}


async def _dimensions(conn, c: dict) -> dict:
    tk = "AUDREY"
    if c["source"] == "AUDREY":
        sql = AUDREY_DIMENSIONS
        args = (c["finding_class"],)
    else:
        rule = _rule(c["finding_class"])
        tk = rule[0] if rule else None
        if tk is None:
            tk = await conn.fetchval(
                "SELECT target_kind FROM karen_challenges WHERE detector=$1 "
                "   AND target_agent=$2 LIMIT 1", c["finding_class"],
                c["target_agent"])
        sql = TARGET_DIMENSIONS.get(tk)
        if sql is None:
            return {"status": UNAVAILABLE,
                    "why": "TARGET_KIND_CARRIES_NO_STRATEGY_OR_MARKET:%s"
                    % tk}
        if not await _exists(conn, tk):
            return {"status": UNAVAILABLE, "why": "TABLE_ABSENT:%s" % tk}
        args = (c["finding_class"], c["target_agent"])
    r = await conn.fetchrow(sql, *args)
    return {"status": MEASURED,
            "subjects": int(r["subjects"] or 0),
            "strategies": [s for s in (r["strategies"] or []) if s],
            "n_strategies": int(r["n_strategies"] or 0),
            "markets": [m for m in (r["markets"] or []) if m],
            "n_markets": int(r["n_markets"] or 0),
            "basis": DIMENSION_BASIS.get(tk) or {}}


async def _events(conn, cid: str) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT event_id, kind, status_to, owner_agent, fix_commit_sha, "
        "       fix_ref, extract(epoch FROM fix_effective_at)::float8 AS "
        "       fix_effective_at, effect, note, actor, actor_class, "
        "       extract(epoch FROM at)::float8 AS at "
        "  FROM improvement_cluster_events WHERE cluster_id = $1 "
        " ORDER BY at, event_id", cid)]


def state_of(c: dict, events: list) -> dict:
    """Status, owner, linked fix and last measurement from the events.
    Pure."""
    status, owner, fix, measured = S_OPEN, c.get("owner_agent"), None, None
    for e in events:
        status = e["status_to"]
        if e.get("owner_agent"):
            owner = e["owner_agent"]
        if e["kind"] == "FIX_LINKED":
            fix = {"commit_sha": e["fix_commit_sha"], "ref": e["fix_ref"],
                   "effective_at": e["fix_effective_at"],
                   "linked_by": e["actor"], "linked_at": e["at"],
                   "event_id": e["event_id"]}
            measured = None
        elif e["kind"] == "REOPENED":
            fix, measured = None, None
        elif e["kind"] == "EFFECT_MEASURED":
            measured = {"at": e["at"], "effect": _j(e["effect"]),
                        "event_id": e["event_id"]}
    return {"status": status, "owner": owner, "fix": fix,
            "last_measurement": measured}


async def measure_effect(conn, c: dict, fix: dict | None, *,
                         now: float) -> dict:
    """The defect rate before vs after the linked fix (read only)."""
    if not fix or fix.get("effective_at") is None:
        return {"status": UNAVAILABLE, "why": R_NO_FIX, "verdict": None}
    fix_at = float(fix["effective_at"])
    if now - fix_at < MIN_POST_FIX_S:
        return {"status": UNAVAILABLE, "why": R_TOO_SOON, "verdict": None,
                "fix_effective_at": fix_at}
    lo, mid, a0, hi = windows(fix_at, now)
    out: dict[str, Any] = {"fix_effective_at": fix_at,
                           "windows": {"before": [lo, mid],
                                       "after": [a0, hi]}}
    if c["source"] == "KAREN":
        b = await rule_rate(conn, c["finding_class"], lo, mid)
        a = await rule_rate(conn, c["finding_class"], a0, hi)
        if b is not None and a is not None:
            return dict(out, **proportion_effect(b, a),
                        rule=c["finding_class"])
        cnt = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE record_at >= to_timestamp($3) "
            "         AND record_at < to_timestamp($4)) AS c0, "
            "       count(*) FILTER (WHERE record_at >= to_timestamp($5) "
            "         AND record_at < to_timestamp($6)) AS c1 "
            "  FROM karen_challenges "
            " WHERE detector = $1 AND target_agent = $2 "
            "   AND state = 'UPHELD'",
            c["finding_class"], c["target_agent"], lo, mid, a0, hi)
    else:
        cnt = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE found_at >= to_timestamp($2) "
            "         AND found_at < to_timestamp($3)) AS c0, "
            "       count(*) FILTER (WHERE found_at >= to_timestamp($4) "
            "         AND found_at < to_timestamp($5)) AS c1 "
            "  FROM paper_audrey_findings WHERE kind = $1 "
            "   AND severity IN ('WARNING', 'CRITICAL')",
            c["finding_class"], lo, mid, a0, hi)
    eff = rate_effect((int(cnt["c0"] or 0), (mid - lo) / 86400.0),
                      (int(cnt["c1"] or 0), (hi - a0) / 86400.0))
    if c["source"] == "KAREN":
        eff["note"] = ("members are Karen's challenges, which she raises at "
                       "most three per detector per pass: a throttled "
                       "sample, not the defect rate")
    return dict(out, **eff)


async def current_rate(conn, c: dict, *, now: float) -> dict:
    """The defect's rate over the last day (Karen table rules only)."""
    if c["source"] != "KAREN":
        return {"status": UNAVAILABLE, "why": "NOT_A_TABLE_RULE"}
    got = await rule_rate(conn, c["finding_class"], now - 86400.0, now)
    if got is None:
        return {"status": UNAVAILABLE, "why": "NOT_A_TABLE_RULE"}
    k, n = got
    return {"status": MEASURED if n else UNAVAILABLE,
            "why": None if n else "NO_RECORDS_IN_THE_LAST_DAY",
            "window_s": 86400.0, "hits": k, "rows": n,
            "rate": round(k / n, 6) if n else None,
            "basis": "the rule's predicate over its own table"}


async def _folded_items(conn, c: dict) -> dict:
    """The improvement items this cluster stands for (221's per-finding
    items)."""
    if not await _exists(conn, "improve_items"):
        return {"status": UNAVAILABLE, "why": "MIGRATION_221_NOT_APPLIED"}
    if c["source"] == "KAREN":
        n = await conn.fetchval(
            "SELECT count(*) FROM improve_items i JOIN karen_challenges k "
            "  ON k.challenge_id = i.source_key "
            " WHERE i.source_kind = 'KAREN_UPHELD_CHALLENGE' "
            "   AND k.detector = $1 AND k.target_agent = $2",
            c["finding_class"], c["target_agent"])
    else:
        n = await conn.fetchval(
            "SELECT count(*) FROM improve_items WHERE source_kind = "
            " 'AUDREY_FINDING' AND source_key = $1", c["finding_class"])
    return {"status": MEASURED, "improve_items": int(n or 0)}


async def view(conn, *, now: float | None = None,
               cluster_id: str | None = None) -> dict:
    """EVERY ROOT-CAUSE CLUSTER with its evidence (read only)."""
    t = float(now if now is not None else time.time())
    if not await has_schema(conn):
        return {"status": UNAVAILABLE, "why": R_NO_SCHEMA, "clusters": []}
    rows = await conn.fetch(
        "SELECT * FROM improvement_clusters "
        " WHERE ($1::text IS NULL OR cluster_id = $1) "
        " ORDER BY first_seen_at, cluster_id", cluster_id)
    out = []
    for r in rows:
        c = dict(r)
        ev = await _events(conn, c["cluster_id"])
        st = state_of(c, ev)
        mem = await _members(conn, c)
        out.append({
            "cluster_id": c["cluster_id"], "cluster_key": c["cluster_key"],
            "source": c["source"], "finding_class": c["finding_class"],
            "target_agent": c["target_agent"], "title": c["title"],
            "owner": st["owner"], "status": st["status"],
            "count": mem["count"], "by_state": mem["by_state"],
            "first_seen_at": mem["first_seen_at"],
            "last_seen_at": mem["last_seen_at"],
            "member_basis": mem["member_basis"],
            "sample_member_ids": mem["sample_ids"],
            "affected": await _dimensions(conn, c),
            "linked_fix": st["fix"],
            "measured_effect": await measure_effect(conn, c, st["fix"],
                                                    now=t),
            "last_recorded_measurement": st["last_measurement"],
            "current_defect_rate": await current_rate(conn, c, now=t),
            "folded": await _folded_items(conn, c),
            "rule_ref": _j(c["rule_ref"]) or {},
            "opened_at": _ep(c["opened_at"]),
            "events": [{"kind": e["kind"], "status_to": e["status_to"],
                        "actor": e["actor"], "at": e["at"]} for e in ev]})
    return {"status": "OK", "why": None, "clusters": out,
            "repeat_min": REPEAT_MIN, "version": VERSION}


# ═════════════════════════════════════════════════════════════════════
# THE RUNNER (writes improvement_clusters / OPENED / EFFECT_MEASURED only)
# ═════════════════════════════════════════════════════════════════════

async def _candidates(conn) -> list:
    out = []
    if await _exists(conn, "karen_challenges"):
        for r in await conn.fetch(
                "SELECT detector, target_agent, count(*) AS n, "
                "       min(record_at) AS first FROM karen_challenges "
                " WHERE state = 'UPHELD' GROUP BY detector, target_agent "
                "HAVING count(*) >= $1 ORDER BY min(record_at)", REPEAT_MIN):
            rule = _rule(r["detector"])
            out.append({"source": "KAREN", "finding_class": r["detector"],
                        "target_agent": r["target_agent"],
                        "owner_agent": r["target_agent"],
                        "first_seen_at": _ep(r["first"]), "n": int(r["n"]),
                        "title": ("Root cause: Karen %s on %s's records "
                                  "(%d upheld)" % (r["detector"],
                                                   r["target_agent"].title(),
                                                   int(r["n"])))[:300],
                        "rule_ref": ({"table": rule[0], "key": rule[1],
                                      "time_column": rule[3],
                                      "predicate_sha": _h(rule[2])}
                                     if rule else {})})
    if await _exists(conn, "paper_audrey_findings"):
        for r in await conn.fetch(
                "SELECT kind, count(*) AS n, min(found_at) AS first "
                "  FROM paper_audrey_findings "
                " WHERE severity IN ('WARNING', 'CRITICAL') GROUP BY kind "
                "HAVING count(*) >= $1 ORDER BY min(found_at)", REPEAT_MIN):
            out.append({"source": "AUDREY", "finding_class": r["kind"],
                        "target_agent": None, "owner_agent": "AUDREY",
                        "first_seen_at": _ep(r["first"]), "n": int(r["n"]),
                        "title": ("Root cause: Audrey finding %s (%d)"
                                  % (r["kind"], int(r["n"])))[:300],
                        "rule_ref": {}})
    return out


async def _end_runner_session(conn) -> None:
    """The runner's declaration ends with its write: a transaction-local
    setting outlives a SAVEPOINT's release (when the runner is called inside
    a caller's transaction), so it is switched off explicitly."""
    await conn.execute(
        "SELECT set_config('bettor.cluster_runner', 'off', true)")


async def refresh(conn, *, now: float | None = None) -> dict:
    """OPEN the clusters of repeated findings and RECORD the measured effect
    of linked fixes. Each write in its own transaction, declared as the
    cluster runner (the database then refuses any human step in it). Never
    raises."""
    t = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": t, "opened": [],
                           "measured": [], "errors": {}}
    try:
        if not await has_schema(conn):
            return dict(out, refusal=R_NO_SCHEMA)
        cands = await _candidates(conn)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__)
    for c in cands:
        key = cluster_key(c["source"], c["finding_class"], c["target_agent"])
        cid = cluster_id_for(key)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('bettor.cluster_runner', 'on', true)")
                got = await conn.fetchval(
                    "INSERT INTO improvement_clusters (cluster_id, "
                    " cluster_key, source, finding_class, target_agent, "
                    " owner_agent, title, rule_ref, first_seen_at, "
                    " opened_at, opened_by) VALUES ($1,$2,$3,$4,$5,$6,$7,"
                    " $8::jsonb,to_timestamp($9),to_timestamp($10),$11) "
                    "ON CONFLICT (cluster_id) DO NOTHING RETURNING "
                    " cluster_id", cid, key, c["source"], c["finding_class"],
                    c["target_agent"], c["owner_agent"], c["title"],
                    json.dumps(c["rule_ref"]),
                    min(c["first_seen_at"] or t, t), t, RUNNER)
                if got is not None:
                    await conn.execute(
                        "INSERT INTO improvement_cluster_events (cluster_id,"
                        " kind, status_to, owner_agent, note, actor, "
                        " actor_class, recorded_by, at) VALUES ($1,"
                        " 'OPENED','OPEN',$2,$3,$4,'RUNNER',$4,"
                        " to_timestamp($5))", cid, c["owner_agent"],
                        "%d repeated findings at opening" % c["n"], RUNNER,
                        t)
                    out["opened"].append(cid)
                await _end_runner_session(conn)
        except Exception as exc:                                # noqa: BLE001
            out["errors"][cid] = type(exc).__name__
    # the effect of each linked fix
    try:
        rows = await conn.fetch("SELECT * FROM improvement_clusters")
    except Exception as exc:                                    # noqa: BLE001
        out["errors"]["read"] = type(exc).__name__
        return out
    for r in rows:
        c = dict(r)
        try:
            ev = await _events(conn, c["cluster_id"])
            st = state_of(c, ev)
            if st["status"] not in S_MEASURED or not st["fix"]:
                continue
            last = st["last_measurement"]
            if last is not None and t - float(last["at"]) < MEASURE_EVERY_S:
                continue
            eff = await measure_effect(conn, c, st["fix"], now=t)
            if eff.get("status") == UNAVAILABLE:
                continue
            status_to = eff.get("verdict") or S_FIX_LINKED
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('bettor.cluster_runner', 'on', true)")
                await conn.execute(
                    "INSERT INTO improvement_cluster_events (cluster_id, "
                    " kind, status_to, effect, actor, actor_class, "
                    " recorded_by, at) VALUES ($1,'EFFECT_MEASURED',$2,"
                    " $3::jsonb,$4,'RUNNER',$4,to_timestamp($5))",
                    c["cluster_id"], status_to,
                    json.dumps(dict(eff, fix_commit_sha=st["fix"][
                        "commit_sha"]), default=str), RUNNER, t)
                await _end_runner_session(conn)
            out["measured"].append({"cluster_id": c["cluster_id"],
                                    "status": status_to})
        except Exception as exc:                                # noqa: BLE001
            out["errors"][c["cluster_id"]] = type(exc).__name__
    return out


_LAST_STEP: dict = {}


async def step(conn, ctx: dict) -> dict:
    """THE PAPER-PASS HOOK, on the main account's pass, at most every
    REFRESH_EVERY_S in this process. Never raises."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    if ctx.get("account_id") != MAIN_PAPER_ACCOUNT and \
            not ctx.get("agent_work_any_account"):
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    last = _LAST_STEP.get("refresh")
    if last is not None and 0 <= at - last < REFRESH_EVERY_S:
        return {"ran": False, "why": "NOT_DUE", "last_at": last}
    _LAST_STEP["refresh"] = at
    return dict(await refresh(conn, now=at), ran=True)


# ═════════════════════════════════════════════════════════════════════
# THE HUMAN STEPS (a named person; the database refuses a machine name and
# refuses these inside the runner's session). Nothing in the runner calls
# them.
# ═════════════════════════════════════════════════════════════════════

async def _human(conn, cid: str, *, kind: str, status_to: str, actor: str,
                 at: float, owner: str | None = None, sha: str | None = None,
                 ref: str | None = None, effective_at: float | None = None,
                 note: str | None = None,
                 actor_class: str = "ENGINEERING") -> dict:
    try:
        async with conn.transaction():
            eid = await conn.fetchval(
                "INSERT INTO improvement_cluster_events (cluster_id, kind, "
                " status_to, owner_agent, fix_commit_sha, fix_ref, "
                " fix_effective_at, note, actor, actor_class, recorded_by, "
                " at) VALUES ($1,$2,$3,$4,$5,$6,CASE WHEN $7::float8 IS "
                " NULL THEN NULL ELSE to_timestamp($7::float8) END,$8,$9,"
                " $10,$9,to_timestamp($11)) RETURNING event_id",
                cid, kind, status_to, owner, sha, ref, effective_at, note,
                actor, actor_class, float(at))
        return {"ok": True, "event_id": eid}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": type(exc).__name__,
                "why": str(exc)[:300]}


async def link_fix(conn, cluster_id: str, *, actor: str, commit_sha: str,
                   effective_at: float, at: float | None = None,
                   ref: str | None = None,
                   actor_class: str = "ENGINEERING") -> dict:
    """A PERSON links the fix (its commit SHA and when it took effect)."""
    return await _human(conn, cluster_id, kind="FIX_LINKED",
                        status_to=S_FIX_LINKED, actor=actor,
                        at=float(at if at is not None else time.time()),
                        sha=commit_sha, ref=ref, effective_at=effective_at,
                        actor_class=actor_class)


async def assign_owner(conn, cluster_id: str, *, actor: str, owner: str,
                       at: float | None = None) -> dict:
    """A PERSON assigns the owner; the cluster's status is kept (the
    database refuses an assignment that would change it)."""
    ev = await _events(conn, cluster_id)
    st = state_of({"owner_agent": None}, ev)["status"]
    return await _human(conn, cluster_id, kind="OWNER_ASSIGNED",
                        status_to=st, actor=actor,
                        at=float(at if at is not None else time.time()),
                        owner=owner)


async def close(conn, cluster_id: str, *, actor: str, note: str,
                at: float | None = None) -> dict:
    return await _human(conn, cluster_id, kind="CLOSED", status_to=S_CLOSED,
                        actor=actor, note=note,
                        at=float(at if at is not None else time.time()))


async def reopen(conn, cluster_id: str, *, actor: str, note: str,
                 at: float | None = None) -> dict:
    return await _human(conn, cluster_id, kind="REOPENED", status_to=S_OPEN,
                        actor=actor, note=note,
                        at=float(at if at is not None else time.time()))
