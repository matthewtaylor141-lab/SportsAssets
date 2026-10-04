"""MEMORY USEFULNESS (owner R30 program section 19, migration 234 §2):
WHICH LESSON WAS IN WHICH DECISION'S CONTEXT, AND WHETHER IT HELPED.

THE DEFECT. The agents learn long-term lessons (agent_memory_events, 224:
LESSON / SELF_CORRECTION; paper_agent_lessons, 185: forward-record lessons
per strategy) and nothing ever measures them: no record says which lesson
was in force for which decision, so a lesson that coincides with worse
outcomes can never be downweighted, and an "experienced" agent cannot be
told from one carrying a harmful belief.

WHAT THIS DOES.

  retrieve(conn)   (a paper-pass step on the main account) for each
                   decision of record not yet retrieved for -- Derek's paper
                   ENTER decisions and Xavier's FIRST review of each position
                   (one management decision per position: his every-minute
                   backstop reviews would only repeat it) -- the deciding
                   agent's lessons IN FORCE AT THE DECISION INSTANT:
                     * agent_memory_events of kind LESSON / SELF_CORRECTION,
                       learned at or before the decision, not expired, not
                       superseded by a memory learned at or before it;
                     * paper_agent_lessons, the series' latest version
                       learned at or before the decision, of the decision's
                       account and of its strategy (or general);
                     * never one SUPERSEDED (agent_lesson_supersessions)
                       before the decision; a DOWNWEIGHTED one ranks lower.
                   The top MAX_LESSONS (strategy match first, then weight,
                   then recency) are recorded in agent_lesson_retrievals.
                   INFLUENCE NONE_RECORD_ONLY: no decision reads a lesson to
                   decide (memory never grants authority) -- "used" means
                   "in the agent's context for that decision", and the
                   comparison below is observational, stated as such.

  usefulness(conn) for each retrieved lesson, the FORWARD INVESTMENT-sleeve
                   outcomes (the profitability validation's own position
                   rows: api.command_validation.gather) of the decisions that
                   had it in context against COMPARABLE decisions that did
                   not -- the same agent, the same strategies, decided inside
                   the same span after the lesson was learned
                   (CONTEMPORANEOUS); when a lesson was in force for every
                   such decision, an equal-length window BEFORE it was
                   learned (BEFORE_AFTER, confounded by time, labelled). Outcomes are
                   resolved positions' realized net (fees included),
                   averaged per INDEPENDENT EVENT (validation.event_key) so
                   repeated positions on one fixture are one observation.
                   Welch's difference of means (used - comparable) with
                   one-sided Student-t upper bounds at 95% and 99%.
                   UNAVAILABLE (with the reason) below two events per arm;
                   SMALL_SAMPLE below MIN_EVENTS_FOR_MEASURED events per arm
                   -- reported, never hidden.

  supersede(conn)  HARMFUL EVIDENCE lowers a lesson's weight through the
                   append-only agent_lesson_supersessions record (decided by
                   this evaluator's VERSION, never an agent or a person):
                     DOWNWEIGHT (weight halved)  the 95% one-sided upper
                                                 bound of the difference is
                                                 below zero;
                     SUPERSEDE (weight 0)        the 99% bound is below zero
                                                 on the CONTEMPORANEOUS
                                                 comparison (a before / after
                                                 one only downweights).
                   A weight only ever falls (the database refuses anything
                   else); nothing follows SUPERSEDE; a superseded lesson is
                   never retrieved again.

NO AUTHORITY. Writes only agent_lesson_retrievals / agent_lesson_
supersessions. Nothing reads a lesson weight to trade; no order, venue,
execution, funded or paper module is imported (the outcome rows come
through the profitability validation's read-only reader).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

log = logging.getLogger(__name__)

VERSION = "MEMORY_USEFULNESS_V1"
RETRIEVER = "LESSON_RETRIEVAL_V1"
MAX_LESSONS = 3
MAX_DECISIONS_PER_PASS = 200
RETRIEVE_LOOKBACK_S = 6 * 3600.0
MEMORY_KINDS = ("LESSON", "SELF_CORRECTION")
DOWNWEIGHT_FACTOR = 0.5
MIN_EVENTS = 2
MIN_EVENTS_FOR_MEASURED = 30
MAX_EVIDENCE_REFS = 20
STEP_EVERY_S = 60.0
EVALUATE_EVERY_S = 3600.0
#: = bettor_paper_ledger.ACCOUNT_ID (agent_work.MAIN_PAPER_ACCOUNT, pinned)
MAIN_PAPER_ACCOUNT = "paper_acct_main"
INVESTMENT = "INVESTMENT"

A_DOWNWEIGHT, A_SUPERSEDE = "DOWNWEIGHT", "SUPERSEDE"
D_CONTEMPORANEOUS = "CONTEMPORANEOUS_SAME_SPAN"
D_BEFORE_AFTER = "BEFORE_AFTER_SAME_LENGTH"
PRE_WINDOW_MIN_S = 86400.0
PRE_WINDOW_MAX_S = 30 * 86400.0
MEASURED, SMALL, UNAVAILABLE = "MEASURED", "SMALL_SAMPLE", "UNAVAILABLE"
R_NO_SCHEMA = "MIGRATION_234_NOT_APPLIED"

#: one-sided Student-t critical values, df 1..30 (95%: = profitability.
#: validation.T95_ONE_SIDED; 99% from the same tables)
T95 = (6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
       1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
       1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697)
T99 = (31.821, 6.965, 4.541, 3.747, 3.365, 3.143, 2.998, 2.896, 2.821,
       2.764, 2.718, 2.681, 2.650, 2.624, 2.602, 2.583, 2.567, 2.552, 2.539,
       2.528, 2.518, 2.508, 2.500, 2.492, 2.485, 2.479, 2.473, 2.467, 2.462,
       2.457)
Z = {0.95: 1.6448536, 0.99: 2.3263479}


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


# ═════════════════════════════════════════════════════════════════════
# THE STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def t_crit(df: float, level: float) -> float:
    """One-sided Student-t critical value (table to df 30, then the
    Cornish-Fisher expansion about z)."""
    table = T95 if level == 0.95 else T99
    d = max(1, int(math.floor(df)))
    if d <= len(table):
        return table[d - 1]
    z = Z[level]
    return (z + (z ** 3 + z) / (4 * d)
            + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * d ** 2))


def event_means(rows: list) -> list:
    """[(event_key, mean outcome)]: positions on one independent event are
    one observation. Pure."""
    by: dict = {}
    for r in rows:
        by.setdefault(r["event"], []).append(float(r["outcome"]))
    return [(k, sum(v) / len(v)) for k, v in sorted(by.items())]


def compare(used: list, comparable: list) -> dict:
    """USED vs COMPARABLE event-level outcomes. Pure. Welch's difference of
    means with one-sided upper bounds; UNAVAILABLE below MIN_EVENTS per arm,
    SMALL_SAMPLE below MIN_EVENTS_FOR_MEASURED."""
    a = [x for _, x in event_means(used)]
    b = [x for _, x in event_means(comparable)]
    out: dict[str, Any] = {"n_used": len(a), "n_comparable": len(b),
                           "unit": "INDEPENDENT_EVENT_MEAN_REALIZED_NET_USD"}
    if len(a) < MIN_EVENTS or len(b) < MIN_EVENTS:
        return dict(out, status=UNAVAILABLE, mean_difference_usd=None,
                    why=("FEWER_THAN_%d_EVENTS_WITH_THE_LESSON" % MIN_EVENTS
                         if len(a) < MIN_EVENTS else
                         "FEWER_THAN_%d_COMPARABLE_EVENTS_WITHOUT_IT"
                         % MIN_EVENTS), harmful_95=None, harmful_99=None)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    sa, sb = va / len(a), vb / len(b)
    se = math.sqrt(sa + sb)
    d = ma - mb
    if se <= 0:
        df = float(len(a) + len(b) - 2)
        up95 = up99 = d
    else:
        den = ((sa ** 2) / (len(a) - 1) + (sb ** 2) / (len(b) - 1))
        df = (sa + sb) ** 2 / den if den > 0 else float(len(a) + len(b) - 2)
        up95 = d + t_crit(df, 0.95) * se
        up99 = d + t_crit(df, 0.99) * se
    small = min(len(a), len(b)) < MIN_EVENTS_FOR_MEASURED
    return dict(out, status=SMALL if small else MEASURED,
                why=("FEWER_THAN_%d_EVENTS_PER_ARM" % MIN_EVENTS_FOR_MEASURED
                     if small else None),
                mean_used_usd=round(ma, 6), mean_comparable_usd=round(mb, 6),
                mean_difference_usd=round(d, 6), welch_df=round(df, 3),
                upper_95_usd=round(up95, 6), upper_99_usd=round(up99, 6),
                harmful_95=up95 < 0, harmful_99=up99 < 0)


def action_for(measure: dict, current_weight: float) -> dict | None:
    """The supersession HARMFUL evidence calls for, or None. Pure. Weights
    only fall. SUPERSEDE needs the contemporaneous comparison; a
    before / after comparison (confounded by time) can only DOWNWEIGHT."""
    if measure.get("status") == UNAVAILABLE or current_weight <= 0:
        return None
    if measure.get("harmful_99") and \
            measure.get("design", D_CONTEMPORANEOUS) == D_CONTEMPORANEOUS:
        return {"action": A_SUPERSEDE, "weight": 0.0}
    if measure.get("harmful_95"):
        w = round(current_weight * DOWNWEIGHT_FACTOR, 6)
        if 0 < w < current_weight:
            return {"action": A_DOWNWEIGHT, "weight": w}
    return None


def rank(candidates: list) -> list:
    """Strategy match first, then weight, then the most recent. Pure."""
    return sorted(candidates, key=lambda c: (
        -int(bool(c.get("strategy_match"))), -float(c.get("weight") or 0),
        -float(c.get("learned_at") or 0), str(c.get("lesson_id"))))


# ═════════════════════════════════════════════════════════════════════
# READS
# ═════════════════════════════════════════════════════════════════════

async def _exists(conn, table: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        table))
    except Exception:                                           # noqa: BLE001
        return False


async def has_schema(conn) -> bool:
    return await _exists(conn, "agent_lesson_retrievals")


WEIGHT_AT_SQL = (
    "SELECT coalesce((SELECT s.weight FROM agent_lesson_supersessions s "
    "  WHERE s.lesson_table = $1 AND s.lesson_id = $2 "
    "    AND s.decided_at <= to_timestamp($3) "
    "  ORDER BY s.decided_at DESC, s.recorded_at DESC LIMIT 1), 1.0)")


async def weight_at(conn, table: str, lesson_id: str, at: float) -> float:
    return float(await conn.fetchval(WEIGHT_AT_SQL, table, lesson_id,
                                     float(at)))


async def lessons_in_force(conn, *, agent: str, account_id: str,
                           strategy: str | None, at: float) -> list:
    """The agent's lessons in force at `at` (point in time), with weights;
    superseded ones excluded. SELECT only."""
    out = []
    if await _exists(conn, "agent_memory_events"):
        for r in await conn.fetch(
                "SELECT m.memory_id, m.learned_at, m.subject_type, "
                "       m.subject_id, m.memory_kind FROM agent_memory_events m"
                " WHERE m.agent_id = $1 AND m.memory_kind = ANY($2::text[]) "
                "   AND m.learned_at <= to_timestamp($3) "
                "   AND (m.expires_at IS NULL "
                "        OR m.expires_at > to_timestamp($3)) "
                "   AND NOT EXISTS (SELECT 1 FROM agent_memory_events n "
                "        WHERE n.memory_id = m.superseded_by "
                "          AND n.learned_at <= to_timestamp($3)) "
                " ORDER BY m.learned_at DESC LIMIT 50",
                agent, list(MEMORY_KINDS), float(at)):
            out.append({"lesson_table": "agent_memory_events",
                        "lesson_id": r["memory_id"],
                        "learned_at": _ep(r["learned_at"]),
                        "strategy_match": False,
                        "relevance": {"memory_kind": r["memory_kind"],
                                      "subject": "%s:%s" % (
                                          r["subject_type"],
                                          r["subject_id"])}})
    if await _exists(conn, "paper_agent_lessons"):
        for r in await conn.fetch(
                "SELECT l.lesson_id, l.learned_at, l.kind, l.strategy "
                "  FROM paper_agent_lessons l "
                " WHERE l.account_id = $1 AND l.agent_id = $2 "
                "   AND l.learned_at <= to_timestamp($3) "
                "   AND (l.strategy IS NULL OR l.strategy = $4) "
                "   AND NOT EXISTS (SELECT 1 FROM paper_agent_lessons n "
                "        WHERE n.account_id = l.account_id "
                "          AND n.series_key = l.series_key "
                "          AND n.version > l.version "
                "          AND n.learned_at <= to_timestamp($3)) "
                " ORDER BY l.learned_at DESC LIMIT 50",
                account_id, agent, float(at), strategy):
            out.append({"lesson_table": "paper_agent_lessons",
                        "lesson_id": r["lesson_id"],
                        "learned_at": _ep(r["learned_at"]),
                        "strategy_match": bool(r["strategy"])
                        and r["strategy"] == strategy,
                        "relevance": {"kind": r["kind"],
                                      "lesson_strategy": r["strategy"]}})
    kept = []
    for c in out:
        w = await weight_at(conn, c["lesson_table"], c["lesson_id"], at)
        if w > 0:
            kept.append(dict(c, weight=w))
    return kept


async def _decisions_to_retrieve(conn, *, account_id: str, now: float,
                                 limit: int) -> list:
    """Derek's ENTER decisions and Xavier's first review per position, in
    the lookback, with no retrieval recorded yet."""
    out = []
    lo = now - RETRIEVE_LOOKBACK_S
    if await _exists(conn, "paper_decisions"):
        for r in await conn.fetch(
                "SELECT d.decision_id, d.decided_at, d.strategy "
                "  FROM paper_decisions d WHERE d.account_id = $1 "
                "   AND d.verdict = 'ENTER' "
                "   AND d.decided_at BETWEEN to_timestamp($2) "
                "                        AND to_timestamp($3) "
                "   AND NOT EXISTS (SELECT 1 FROM agent_lesson_retrievals x "
                "        WHERE x.decision_table = 'paper_decisions' "
                "          AND x.decision_id = d.decision_id) "
                " ORDER BY d.decided_at, d.decision_id LIMIT $4",
                account_id, lo, now, limit):
            out.append({"agent": "DEREK", "table": "paper_decisions",
                        "id": r["decision_id"],
                        "decided_at": _ep(r["decided_at"]),
                        "strategy": r["strategy"]})
    if await _exists(conn, "paper_xavier_reviews"):
        for r in await conn.fetch(
                "SELECT f.review_id, f.reviewed_at, f.strategy FROM ("
                "  SELECT DISTINCT ON (group_id) review_id, reviewed_at, "
                "         strategy, group_id FROM paper_xavier_reviews "
                "   WHERE account_id = $1 "
                "   ORDER BY group_id, reviewed_at, review_id) f "
                " WHERE f.reviewed_at BETWEEN to_timestamp($2) "
                "                         AND to_timestamp($3) "
                "   AND NOT EXISTS (SELECT 1 FROM agent_lesson_retrievals x "
                "        WHERE x.decision_table = 'paper_xavier_reviews' "
                "          AND x.decision_id = f.review_id) "
                " ORDER BY f.reviewed_at, f.review_id LIMIT $4",
                account_id, lo, now, limit):
            out.append({"agent": "XAVIER", "table": "paper_xavier_reviews",
                        "id": r["review_id"],
                        "decided_at": _ep(r["reviewed_at"]),
                        "strategy": r["strategy"]})
    return out


# ═════════════════════════════════════════════════════════════════════
# THE WRITES
# ═════════════════════════════════════════════════════════════════════

async def retrieve(conn, *, account_id: str = MAIN_PAPER_ACCOUNT,
                   now: float | None = None) -> dict:
    """RECORD WHICH LESSONS WERE IN EACH NEW DECISION'S CONTEXT. Never
    raises."""
    t = float(now if now is not None else time.time())
    out: dict[str, Any] = {"decisions": 0, "retrievals": 0,
                           "without_lessons": 0, "errors": {}}
    try:
        if not await has_schema(conn):
            return dict(out, refusal=R_NO_SCHEMA)
        todo = await _decisions_to_retrieve(
            conn, account_id=account_id, now=t,
            limit=MAX_DECISIONS_PER_PASS)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__)
    for d in todo:
        out["decisions"] += 1
        try:
            cands = rank(await lessons_in_force(
                conn, agent=d["agent"], account_id=account_id,
                strategy=d["strategy"], at=d["decided_at"]))[:MAX_LESSONS]
            if not cands:
                out["without_lessons"] += 1
                continue
            async with conn.transaction():
                for i, c in enumerate(cands, start=1):
                    got = await conn.fetchval(
                        "INSERT INTO agent_lesson_retrievals (retrieval_id, "
                        " agent_id, lesson_table, lesson_id, "
                        " lesson_learned_at, decision_table, decision_id, "
                        " decided_at, retrieved_at, rank, weight, strategy, "
                        " relevance, retriever_version) VALUES ($1,$2,$3,$4,"
                        " to_timestamp($5),$6,$7,to_timestamp($8),"
                        " to_timestamp($9),$10,$11,$12,$13::jsonb,$14) "
                        "ON CONFLICT DO NOTHING RETURNING retrieval_id",
                        "alr:" + _h(c["lesson_table"], c["lesson_id"],
                                    d["table"], d["id"]),
                        d["agent"], c["lesson_table"], c["lesson_id"],
                        c["learned_at"], d["table"], d["id"],
                        d["decided_at"], t, i, c["weight"], d["strategy"],
                        json.dumps(dict(c["relevance"],
                                        strategy_match=c["strategy_match"])),
                        RETRIEVER)
                    out["retrievals"] += 1 if got else 0
        except Exception as exc:                                # noqa: BLE001
            out["errors"][d["id"]] = type(exc).__name__
    return out


async def _outcomes(conn, account_id: str, now: float) -> tuple:
    """({decision_ref: outcome row}) for resolved INVESTMENT positions, from
    the profitability validation's own reader. ({}, why) when unreadable."""
    try:
        from ..api import command_validation as CV
        data, sources = await CV.gather(conn, account_id, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {}, "OUTCOMES_UNREADABLE:%s" % type(exc).__name__
    if data is None:
        return {}, str(sources)
    from ..profitability import validation as V
    by_group: dict = {}
    for p in data["positions"]:
        if p.get("sleeve") != INVESTMENT:
            continue
        if (p.get("open_qty") or 0.0) > 1e-9 or \
                p.get("realized_pnl_usd") is None:
            continue
        g = by_group.setdefault(p["group_id"], {
            "group_id": p["group_id"], "event": V.event_key(p),
            "outcome": 0.0, "strategy": p.get("strategy")})
        g["outcome"] += float(p["realized_pnl_usd"])
    out: dict = {}
    if by_group:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (o.decision_id) o.decision_id, "
                "       o.group_id, d.decided_at FROM paper_orders o "
                "  JOIN paper_decisions d ON d.decision_id = o.decision_id "
                " WHERE o.account_id = $1 AND o.role = 'ENTRY' "
                "   AND o.group_id = ANY($2::text[]) "
                " ORDER BY o.decision_id, o.created_at", account_id,
                sorted(by_group)):
            out[("paper_decisions", r["decision_id"])] = dict(
                by_group[r["group_id"]], decided_at=_ep(r["decided_at"]))
        for r in await conn.fetch(
                "SELECT DISTINCT ON (group_id) review_id, group_id, "
                "       reviewed_at FROM paper_xavier_reviews "
                " WHERE account_id = $1 AND group_id = ANY($2::text[]) "
                " ORDER BY group_id, reviewed_at, review_id", account_id,
                sorted(by_group)):
            out[("paper_xavier_reviews", r["review_id"])] = dict(
                by_group[r["group_id"]], decided_at=_ep(r["reviewed_at"]))
    return out, None


async def usefulness(conn, *, account_id: str = MAIN_PAPER_ACCOUNT,
                     now: float | None = None) -> dict:
    """EVERY RETRIEVED LESSON'S FORWARD INVESTMENT-SLEEVE EVIDENCE (read
    only): {"lessons": [...], "why"}."""
    t = float(now if now is not None else time.time())
    if not await has_schema(conn):
        return {"lessons": [], "why": R_NO_SCHEMA}
    outcomes, why = await _outcomes(conn, account_id, t)
    lessons = await conn.fetch(
        "SELECT lesson_table, lesson_id, agent_id, min(lesson_learned_at) "
        "       AS learned_at, count(*) AS retrievals, "
        "       min(decided_at) AS first_at, max(decided_at) AS last_at, "
        "       array_agg(DISTINCT decision_table) AS tables "
        "  FROM agent_lesson_retrievals GROUP BY 1, 2, 3 ORDER BY 1, 2")
    decided: dict = {}
    for r in await conn.fetch(
            "SELECT lesson_table, lesson_id, decision_table, decision_id, "
            "       strategy FROM agent_lesson_retrievals"):
        decided.setdefault((r["lesson_table"], r["lesson_id"]), []).append(
            (r["decision_table"], r["decision_id"], r["strategy"]))
    # every decision of record per agent (the comparable pool)
    pool: dict = {}
    for (tbl, did), o in outcomes.items():
        pool.setdefault(tbl, []).append((did, o))
    out = []
    for r in lessons:
        key = (r["lesson_table"], r["lesson_id"])
        tables = set(r["tables"] or [])
        mine = decided.get(key, [])
        used_ids = {(tb, d) for tb, d, _s in mine}
        strategies = {s for _tb, _d, s in mine if s}
        lo, hi = _ep(r["first_at"]), _ep(r["last_at"])
        used = [dict(outcomes[k], decision=k) for k in sorted(used_ids)
                if k in outcomes]
        learned = _ep(r["learned_at"]) or 0.0

        def pick(a, b):
            got = []
            for tbl in tables:
                for did, o in pool.get(tbl, []):
                    if (tbl, did) in used_ids:
                        continue
                    if strategies and o.get("strategy") not in strategies:
                        continue
                    dat = o.get("decided_at")
                    if dat is not None and a - 1e-6 <= dat <= b + 1e-6:
                        got.append(dict(o, decision=(tbl, did)))
            return got
        # CONTEMPORANEOUS: the same span, after the lesson was learned
        comp = pick(max(lo, learned), hi)
        design = D_CONTEMPORANEOUS
        if len(event_means(comp)) < MIN_EVENTS:
            # A lesson in force for every decision of its span has no
            # contemporaneous comparison: the same agent and strategies in
            # an equal-length window BEFORE it was learned (confounded by
            # time -- labelled, and never enough to SUPERSEDE)
            span = min(max(hi - lo, PRE_WINDOW_MIN_S), PRE_WINDOW_MAX_S)
            comp = pick(learned - span, learned - 1e-6)
            design = D_BEFORE_AFTER
        m = dict(compare(used, comp), design=design)
        w = await weight_at(conn, key[0], key[1], t)
        out.append({"lesson_table": key[0], "lesson_id": key[1],
                    "agent": r["agent_id"], "retrievals": int(r["retrievals"]),
                    "learned_at": _ep(r["learned_at"]),
                    "span": [lo, hi], "strategies": sorted(strategies),
                    "weight": w, "measure": m,
                    "used_decisions": [{"table": x["decision"][0],
                                        "id": x["decision"][1]}
                                       for x in used][:MAX_EVIDENCE_REFS],
                    "comparable_decisions": len(comp),
                    "comparison": ("observational: decisions WITH the lesson "
                                   "in context vs the same agent's "
                                   "decisions WITHOUT it, same strategies; "
                                   "%s; INVESTMENT sleeve, resolved "
                                   "positions, one observation per "
                                   "independent event" % (
                                       "same span, forward of the lesson"
                                       if design == D_CONTEMPORANEOUS else
                                       "an equal-length window BEFORE the "
                                       "lesson (confounded by time)")),
                    "influence": "NONE_RECORD_ONLY"})
    return {"lessons": out, "why": why}


async def supersede(conn, *, account_id: str = MAIN_PAPER_ACCOUNT,
                    now: float | None = None) -> dict:
    """LOWER THE WEIGHT OF EVERY LESSON WITH HARMFUL EVIDENCE (append-only).
    Never raises."""
    t = float(now if now is not None else time.time())
    out: dict[str, Any] = {"evaluated": 0, "downweighted": [],
                           "superseded": [], "errors": {}}
    try:
        rep = await usefulness(conn, account_id=account_id, now=t)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, error=type(exc).__name__)
    for l in rep["lessons"]:
        out["evaluated"] += 1
        act = action_for(l["measure"], l["weight"])
        if act is None:
            continue
        # ONE ACTION PER BODY OF EVIDENCE: the same evidence never lowers a
        # weight twice -- a further downweight needs more decisions with the
        # lesson than the last supersession saw
        prev = await conn.fetchrow(
            "SELECT action, evidence FROM agent_lesson_supersessions "
            " WHERE lesson_table = $1 AND lesson_id = $2 "
            " ORDER BY decided_at DESC, recorded_at DESC LIMIT 1",
            l["lesson_table"], l["lesson_id"])
        if prev is not None and act["action"] == A_DOWNWEIGHT and int(
                (_j(prev["evidence"]) or {}).get("n_used") or 0) >= int(
                l["measure"].get("n_used") or 0):
            continue
        refs = [{"kind": l["lesson_table"], "id": l["lesson_id"]}] + [
            {"kind": d["table"], "id": d["id"]}
            for d in l["used_decisions"]][:MAX_EVIDENCE_REFS]
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_lesson_supersessions (supersession_id,"
                    " agent_id, lesson_table, lesson_id, action, "
                    " previous_weight, weight, basis, evidence, "
                    " evidence_refs, decided_by, decided_at) VALUES ($1,$2,"
                    " $3,$4,$5,$6,$7,'FORWARD_INVESTMENT_OUTCOMES',$8::jsonb,"
                    " $9::jsonb,$10,to_timestamp($11))",
                    "als:" + _h(l["lesson_table"], l["lesson_id"],
                                act["action"], t),
                    l["agent"], l["lesson_table"], l["lesson_id"],
                    act["action"], l["weight"], act["weight"],
                    json.dumps(dict(l["measure"], comparison=l["comparison"],
                                    span=l["span"],
                                    strategies=l["strategies"]),
                               default=str),
                    json.dumps(refs), VERSION, t)
            out["superseded" if act["action"] == A_SUPERSEDE
                else "downweighted"].append(l["lesson_id"])
        except Exception as exc:                                # noqa: BLE001
            out["errors"][l["lesson_id"]] = type(exc).__name__
    return out


_LAST: dict = {}


async def step(conn, ctx: dict) -> dict:
    """THE PAPER-PASS HOOK, on the main account's pass: retrieval at most
    every STEP_EVERY_S, the usefulness evaluation (and any supersession) at
    most every EVALUATE_EVERY_S, in this process. Never raises."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    acct = ctx.get("account_id")
    if acct != MAIN_PAPER_ACCOUNT and not ctx.get("agent_work_any_account"):
        return {"ran": False, "why": "NOT_THE_MAIN_PAPER_ACCOUNT"}
    acct = acct or MAIN_PAPER_ACCOUNT
    out: dict[str, Any] = {"ran": False}
    last = _LAST.get("retrieve")
    if last is None or not 0 <= at - last < STEP_EVERY_S:
        _LAST["retrieve"] = at
        out.update(ran=True, retrieve=await retrieve(conn, account_id=acct,
                                                     now=at))
    last = _LAST.get("evaluate")
    if last is None or not 0 <= at - last < EVALUATE_EVERY_S:
        _LAST["evaluate"] = at
        out.update(ran=True, supersede=await supersede(conn, account_id=acct,
                                                       now=at))
    return out
