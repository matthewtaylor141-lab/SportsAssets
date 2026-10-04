"""AGENT SCORECARDS: DECISION AND ECONOMIC QUALITY, NEVER ACTIVITY (owner R30
program section 18; reads only, no table of its own).

ONE CARD PER AGENT -- Derek, Xavier, Audrey, Karen, the Chief Allocator
(Allie), Eddie, Scout -- with the same TEN METRICS in a fixed order:

  decision_latency        how long the agent took from its evidence to its
                          decision (the evidence and the decision are named
                          per role in the metric's definition)
  evidence_completeness   share of the agent's decisions whose record holds
                          the evidence the decision rests on
  citation_correctness    share of the agent's cited evidence references
                          that resolve to an existing record, through
                          migration 221's FIXED kind -> (table, key) map; a
                          kind outside the map is UNVERIFIABLE (reported,
                          never counted right or wrong)
  calibration             where the agent states a probability: Brier
                          against the settlement (Derek, Xavier) or the
                          predicted-vs-realized error (Eddie); otherwise
                          NOT_APPLICABLE with the reason
  false_approval          share of the agent's approvals later shown
                          defective: Karen's SAME-RULE predicate over the
                          agent's whole table (a census -- her challenges
                          are a throttled sample) or an UPHELD challenge,
                          joined to the resolved outcome where one exists
  false_refusal           share of the agent's refusals later shown wrong:
                          Derek -- the lost-opportunity ledger (220)
                          FALSE_REFUSAL over its classified settled
                          refusals (UNKNOWABLE is reported, excluded);
                          Xavier -- his exits that left less than holding
                          to settlement would have (frozen value-add, 206);
                          Karen -- her challenges a third party REJECTED;
                          Eddie -- his WAIT / SKIP_EXECUTION advice on
                          candidates that filled anyway and kept their edge
  value_added             the agent's measured economic contribution: Xavier
                          -- actual minus HOLD-to-settlement (206); Eddie --
                          execution alpha against the naive taker (217); the
                          others -- the twin's latest persisted financial
                          scorecard row (219), labelled with its own window
  challenge_quality       Karen: UPHELD / (UPHELD + REJECTED); a challenge
                          TARGET (Derek, Xavier, Audrey, the Chief
                          Allocator): its disputes the independent evaluator
                          sustained / its resolved disputes
  freshness_compliance    share of the agent's ACTIONS taken on evidence
                          inside its freshness rule: the 30 s probability
                          rule (Derek's entries, Xavier's management
                          recommendations), Eddie's own book-age bound for
                          execution advice. Read, never changed.
  unresolved_blocker_age  the oldest open item of the agent's durable queue
                          (migration 234) that has a current blocker, aged
                          from when it became blocked

EVERY METRIC carries value, unit, n, window {start, end, seconds, basis},
status, reason, direction, definition, source and detail. The status is:
  MEASURED        n >= MIN_N
  SMALL_SAMPLE    0 < n < MIN_N: the value is shown, nothing is concluded
  UNAVAILABLE     no evidence: the value is null with the reason -- a missing
                  input is never a zero
  NOT_APPLICABLE  the role makes no such decision (the reason says why)
  NO_BLOCKER      unresolved_blocker_age only: the open items were read and
                  none is blocked (a measured empty set, not missing data)

NEVER ACTIVITY. No count of heartbeats, runs, passes, messages, reviews
written, memories or tokens is a metric: `NOT_A_SCORE` refuses such a name
when a card is built (the rule migration 219's twin_scorecards_economic_ck
holds for the twin). A count appears only as a metric's n or in its detail.

BUILT ON what exists, not beside it: Karen's RULES (karen_runner) for the
false-approval census; the lost-opportunity ledger (220); Xavier's frozen
value-add (206) and his assessments (206 / 222); Eddie's outcome definitions
(eddie.DEFINITIONS); the twin's persisted scorecards (219); the peer-review
columns of karen_challenges (207 / 212); the queue (226 / 234) through
agent_work_state's own OPEN_ITEMS_SQL and queue_item. MEMORY USEFULNESS
(section 19) is summarised beside the metrics from lesson_usage's records --
a summary, not a score, and it grants nothing.

NO AUTHORITY. SELECT only: nothing here writes, and nothing changes an order,
a size, a limit, a threshold, a policy or a lesson weight. It imports no
order, execution, venue, funded or paper module.
"""
from __future__ import annotations

import json
import re
import statistics
import time

from .. import agent_work_state as WS

VERSION = "AGENT_SCORECARDS_V1"
SCHEMA = "bettor.agent.scorecards.v1"
AGENTS = ("DEREK", "XAVIER", "AUDREY", "KAREN", "CHIEF_ALLOCATOR", "EDDIE",
          "SCOUT")
METRICS = ("decision_latency", "evidence_completeness",
           "citation_correctness", "calibration", "false_approval",
           "false_refusal", "value_added", "challenge_quality",
           "freshness_compliance", "unresolved_blocker_age")
MEASURED, SMALL, UNAVAILABLE, NOT_APPLICABLE, NO_BLOCKER = (
    "MEASURED", "SMALL_SAMPLE", "UNAVAILABLE", "NOT_APPLICABLE",
    "NO_BLOCKER")
STATUSES = (MEASURED, SMALL, UNAVAILABLE, NOT_APPLICABLE, NO_BLOCKER)
LOWER, HIGHER = "LOWER_IS_BETTER", "HIGHER_IS_BETTER"
#: a metric name that counts activity is refused (twin 219's rule, widened)
NOT_A_SCORE = re.compile(
    r"(heartbeat|volume|analyses|reports_written|messages|words|tokens|"
    r"runs|passes|count)", re.I)

DEFAULT_WINDOW_DAYS = 7
MIN_WINDOW_DAYS, MAX_WINDOW_DAYS = 1, 30
MIN_N = 30

# ── PINNED TO THEIR SOURCES (tests/test_agent_scorecards.py §5) ──────────
#: the 30-second probability freshness rule (ext_pinnacle_loop
#: .PINNACLE_MAX_AGE_S): READ to measure compliance, never loosened here
FRESHNESS_RULE_S = 30.0
#: Eddie's own book-age bound (eddie.MAX_BOOK_AGE_S)
EDDIE_MAX_BOOK_AGE_S = 120.0
#: the twin's replay window (twin.runner.WINDOW_DAYS)
TWIN_WINDOW_DAYS = 60.0
#: xavier_freshness.E_FRESH / E_NONE / NON_ACTIONS
E_FRESH = "FRESH_CURRENT_PROBABILITY"
E_NONE = "PROBABILITY_UNAVAILABLE"
NON_ACTIONS = ("WAITING_FOR_FRESH_EVIDENCE",
               "MANAGEMENT_UNAVAILABLE_STALE_INPUT")
#: eddie.RECOMMENDATIONS split into "act" and "hold back"
EXECUTE_RECS = ("EXECUTE_NOW", "REST_LIMIT", "SPLIT")
HOLD_BACK_RECS = ("WAIT", "SKIP_EXECUTION")
#: karen.TARGETS / karen.EVALUATOR_FOR
CHALLENGE_TARGETS = ("DEREK", "XAVIER", "AUDREY", "CHIEF_ALLOCATOR")
EVALUATORS = ("AUDREY", "XAVIER")
#: agents.agent_work.K_RESEARCH (Scout's research questions)
K_RESEARCH = "RESEARCH_QUESTION"
#: Karen's rules whose predicate decides an approval was defective
DEREK_APPROVAL_RULE = "ENTRY_WITHOUT_PROBABILITY"
XAVIER_APPROVAL_RULE = "HOLD_ON_STALE_PROBABILITY"
#: the twin rows that carry the counterfactual economic contribution of an
#: agent whose value no direct record attributes: (twin agent, metric(s))
TWIN_VALUE = {
    "DEREK": ("DEREK", ("accepted_opportunity_pnl",)),
    "KAREN": ("KAREN", ("loss_avoided_paper_basis",
                        "profit_sacrificed_paper_basis")),
    "CHIEF_ALLOCATOR": ("ALLOCATOR", ("pnl_vs_equal_weight_paper_basis",)),
    "SCOUT": ("SCOUT", ("incremental_realized_edge",)),
    "AUDREY": ("AUDREY", ("errors_detected_before_financial_impact",)),
}

#: where an agent cites evidence: (table, agent expression, refs column,
#: time column). The newest CITATION_ROWS per agent per source are read.
CITATION_SOURCES = (
    ("karen_challenges", "challenger", "evidence_refs", "challenged_at"),
    ("karen_challenges", "responded_by", "response_evidence_refs",
     "responded_at"),
    ("karen_challenges", "resolved_by", "resolution_evidence_refs",
     "resolved_at"),
    ("agent_findings", "proposer", "evidence_refs", "created_at"),
    ("agent_conversation_messages", "from_agent", "evidence_refs",
     "created_at"),
    ("agent_memory_events", "agent_id", "evidence_refs", "learned_at"),
    ("eddie_execution_estimates", "'EDDIE'::text", "evidence_refs",
     "estimated_at"),
)
CITATION_ROWS = 500
ID_CHUNK = 1000
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

#: an unmeasured dimension map (eddie / intel) that names nothing
_EMPTY_UNMEASURED = ("(%(c)s IS NULL OR %(c)s IN ('{}'::jsonb, '[]'::jsonb, "
                     "'null'::jsonb))")
_IN_WINDOW = ("%(c)s >= to_timestamp($1) AND %(c)s < to_timestamp($2)")


# ═════════════════════════════════════════════════════════════════════
# PURE
# ═════════════════════════════════════════════════════════════════════

def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _f(v, nd: int = 6):
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def window(now: float, seconds: float, basis: str = "TRAILING") -> dict:
    return {"start": float(now) - float(seconds), "end": float(now),
            "seconds": float(seconds), "basis": basis}


def window_seconds(days) -> float:
    """1..30 days; anything else is refused (ValueError), never clamped."""
    try:
        d = int(days)
    except (TypeError, ValueError):
        raise ValueError("WINDOW_DAYS_NOT_AN_INTEGER") from None
    if not MIN_WINDOW_DAYS <= d <= MAX_WINDOW_DAYS:
        raise ValueError("WINDOW_DAYS_OUT_OF_RANGE")
    return d * 86400.0


def metric(agent: str, name: str, *, win: dict, definition: str,
           source, direction: str | None = None, value=None, n=None,
           unit: str | None = None, reason: str | None = None,
           detail: dict | None = None, min_n: int | None = MIN_N,
           status: str | None = None) -> dict:
    """ONE METRIC. value None -> UNAVAILABLE with a reason (never 0);
    0 < n < min_n -> SMALL_SAMPLE (shown, nothing concluded);
    NOT_APPLICABLE / NO_BLOCKER only when the caller says so."""
    if name not in METRICS or NOT_A_SCORE.search(name):
        raise ValueError("NOT_A_SCORECARD_METRIC:%s" % name)
    if status in (NOT_APPLICABLE, NO_BLOCKER):
        if not reason:
            raise ValueError("A_%s_METRIC_NEEDS_ITS_REASON" % status)
        value = None
    elif value is None:
        status, reason = UNAVAILABLE, reason or "NOT_MEASURED"
    elif min_n is not None and (n or 0) < min_n:
        status = SMALL
        reason = reason or ("n=%d < %d: shown, nothing concluded"
                            % (n or 0, min_n))
    else:
        status, reason = MEASURED, None
    if isinstance(value, float):
        value = round(value, 6)
    return {"agent": agent, "metric": name, "value": value, "unit": unit,
            "n": None if n is None else int(n), "window": win,
            "status": status, "reason": reason, "direction": direction,
            "definition": definition,
            "source": list(source) if isinstance(source, (list, tuple))
            else [source], "detail": detail or {}}


def na(agent: str, name: str, win: dict, why: str) -> dict:
    return metric(agent, name, win=win, status=NOT_APPLICABLE, reason=why,
                  definition="not a decision this role makes", source=[])


def rate(k, n):
    return None if not n else round(float(k) / float(n), 6)


def percentile(xs: list, q: float):
    """Linear-interpolated percentile (PostgreSQL percentile_cont)."""
    s = sorted(float(x) for x in xs if x is not None)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def blocker_card(agent: str, items: list, now: float,
                 blocked_since: dict) -> dict:
    """unresolved_blocker_age from queue_item dicts. Pure."""
    blocked = []
    for it in items:
        if not it.get("blocker"):
            continue
        since = blocked_since.get(it["request_id"]) or it.get("enqueued_at")
        if since is None:
            continue
        blocked.append((max(0.0, now - float(since)), it))
    win = {"start": None, "end": float(now), "seconds": None,
           "basis": "OPEN_QUEUE_ITEMS_AT_READ"}
    src = ["agent_work_requests", "agent_work_open",
           "agent_work_request_events"]
    defn = ("seconds since the oldest open queue item with a current blocker "
            "became blocked (its first BLOCKED attempt, or its enqueue when "
            "the request itself names the blocker)")
    by: dict = {}
    for _a, it in blocked:
        by[it["blocker"]] = by.get(it["blocker"], 0) + 1
    detail = {"open_items": len(items), "blocked_items": len(blocked),
              "overdue_items": sum(1 for it in items if it.get("overdue")),
              "by_blocker": dict(sorted(by.items(), key=lambda kv: -kv[1])
                                 [:10])}
    if not blocked:
        return metric(agent, "unresolved_blocker_age", win=win,
                      status=NO_BLOCKER, definition=defn, source=src,
                      reason="NO_OPEN_ITEM_IS_BLOCKED (%d open)" % len(items),
                      detail=detail, direction=LOWER)
    blocked.sort(key=lambda x: -x[0])
    age, oldest = blocked[0]
    detail.update({
        "median_age_s": round(statistics.median(a for a, _ in blocked), 1),
        "oldest": {"request_id": oldest["request_id"],
                   "kind": oldest.get("kind"),
                   "blocker": oldest.get("blocker"),
                   "subject": oldest.get("subject"),
                   "due_at": oldest.get("due_at"),
                   "overdue": oldest.get("overdue")}})
    return metric(agent, "unresolved_blocker_age", win=win,
                  value=round(age, 1), n=len(blocked), unit="seconds",
                  definition=defn, source=src, detail=detail,
                  direction=LOWER, min_n=None)


def twin_card(agent: str, rows: dict, *, why_absent: str | None) -> dict:
    """value_added from the twin's latest persisted row(s). Pure. A twin
    UNAVAILABLE / UNPROVEN row stays UNAVAILABLE with the twin's reason;
    INSUFFICIENT_SAMPLE becomes SMALL_SAMPLE."""
    t_agent, names = TWIN_VALUE[agent]
    got = [rows.get((t_agent, n)) for n in names]
    src = ["twin_agent_scorecards"]
    head = got[0] or {}
    at = _ep(head.get("computed_at"))
    win = ({"start": at - TWIN_WINDOW_DAYS * 86400.0, "end": at,
            "seconds": TWIN_WINDOW_DAYS * 86400.0,
            "basis": "TWIN_REPLAY_WINDOW (twin.runner.WINDOW_DAYS)"}
           if at is not None else
           {"start": None, "end": None, "seconds": None,
            "basis": "TWIN_REPLAY_WINDOW"})
    defn = ("the twin's counterfactual economic contribution: %s (%s)"
            % (" - ".join("%s.%s" % (t_agent, n) for n in names),
               head.get("basis") or "migration 219"))
    detail = {"twin_rows": [None if g is None else {
        "metric": g.get("metric"), "book": g.get("book"),
        "value": _f(g.get("value")), "status": g.get("status"),
        "reason": g.get("reason"), "sample_n": g.get("sample_n"),
        "run_id": g.get("run_id"), "computed_at": _ep(g.get("computed_at"))}
        for g in got]}
    if any(g is None for g in got):
        return metric(agent, "value_added", win=win, definition=defn,
                      source=src, direction=HIGHER, detail=detail,
                      reason=why_absent or "TWIN_SCORECARD_NOT_COMPUTED")
    bad = [g for g in got if g.get("value") is None
           or g.get("status") in ("UNAVAILABLE", "UNPROVEN")]
    if bad:
        return metric(agent, "value_added", win=win, definition=defn,
                      source=src, direction=HIGHER, detail=detail,
                      reason="TWIN:%s" % (bad[0].get("reason")
                                          or bad[0].get("status")))
    value = float(got[0]["value"])
    for g in got[1:]:
        value -= float(g["value"])
    n = min(int(g.get("sample_n") or 0) for g in got)
    return metric(agent, "value_added", win=win, value=value, n=n,
                  unit=head.get("unit"), definition=defn, source=src,
                  direction=HIGHER, detail=detail)


# ═════════════════════════════════════════════════════════════════════
# GUARDED READS
# ═════════════════════════════════════════════════════════════════════

class _Ctx:
    def __init__(self, conn, now: float, seconds: float):
        self.conn, self.now, self.seconds = conn, float(now), float(seconds)
        self.lo, self.hi = self.now - self.seconds, self.now
        self.win = window(self.now, self.seconds)
        self._tables: dict = {}
        self._cols: dict = {}

    async def has(self, *tables) -> str | None:
        """None when every table exists, else the absent table's reason."""
        for t in tables:
            if t not in self._tables:
                self._tables[t] = bool(await self.conn.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL", t))
            if not self._tables[t]:
                return "TABLE_NOT_DEPLOYED:%s" % t
        return None

    async def col(self, table: str, column: str) -> bool:
        k = (table, column)
        if k not in self._cols:
            self._cols[k] = bool(await self.conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                " WHERE table_schema = current_schema() AND table_name = $1 "
                "   AND column_name = $2)", table, column))
        return self._cols[k]

    async def read(self, tables, fn):
        """(value, why): an absent table or a failed read (inside a
        savepoint, so the caller's transaction survives) is the reason."""
        why = await self.has(*tables)
        if why:
            return None, why
        try:
            async with self.conn.transaction():
                return await fn(), None
        except Exception as exc:                                # noqa: BLE001
            return None, "READ_FAILED:%s" % type(exc).__name__


def _rule_pred(detector: str) -> str | None:
    """Karen's SAME predicate (alias t) for a detector, or None."""
    try:
        from . import karen_runner as KR
    except Exception:                                           # noqa: BLE001
        return None
    r = KR.RULES.get(detector)
    return None if not r else r[2]


def _lat(row, agent, win, *, definition, source, reason_empty,
         detail=None) -> dict:
    n = int((row or {}).get("n") or 0)
    det = dict(detail or {})
    if row and n:
        det["p90_s"] = _f(row.get("p90"), 3)
    return metric(agent, "decision_latency", win=win,
                  value=None if not n else _f(row.get("med"), 3), n=n,
                  unit="seconds (median)", direction=LOWER,
                  definition=definition, source=source,
                  reason=None if n else reason_empty, detail=det)


_PCT = ("count(s) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY s) AS "
        "med, percentile_cont(0.9) WITHIN GROUP (ORDER BY s) AS p90")


# ═════════════════════════════════════════════════════════════════════
# DEREK
# ═════════════════════════════════════════════════════════════════════

_DEREK_AGE = ("CASE WHEN jsonb_typeof(pinnacle->'age_s') = 'number' "
              "THEN (pinnacle->>'age_s')::float8 END")


async def derek(cx: _Ctx) -> dict:
    a, w, c = "DEREK", cx.win, cx.conn
    out = {}
    row, why = await cx.read(("paper_decisions",), lambda: c.fetchrow(
        "SELECT " + _PCT + ", (SELECT count(*) FROM paper_decisions WHERE "
        + _IN_WINDOW % {"c": "decided_at"} + ") AS total FROM (SELECT "
        + _DEREK_AGE + " AS s FROM paper_decisions WHERE "
        + _IN_WINDOW % {"c": "decided_at"} + ") q WHERE s >= 0",
        cx.lo, cx.hi))
    out["decision_latency"] = _lat(
        row, a, w, source=["paper_decisions"],
        definition=("age of the Pinnacle probability at Derek's decision "
                    "(the provider's observation -> decided_at, as the "
                    "decision records it: pinnacle.age_s), over every "
                    "decision that recorded one"),
        reason_empty=why or "NO_DECISION_RECORDED_A_PROBABILITY_AGE",
        detail={"decisions_in_window": (row or {}).get("total")})

    row, why = await cx.read(("paper_decisions",), lambda: c.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE p IS NOT NULL AND "
        "  valuation_id IS NOT NULL AND book_obs_id IS NOT NULL AND "
        "  proposed_qty IS NOT NULL AND limit_price IS NOT NULL AND "
        "  NOT no_econ) AS complete, "
        "  count(*) FILTER (WHERE p IS NULL) AS no_probability, "
        "  count(*) FILTER (WHERE valuation_id IS NULL) AS no_valuation, "
        "  count(*) FILTER (WHERE book_obs_id IS NULL) AS no_book, "
        "  count(*) FILTER (WHERE proposed_qty IS NULL OR limit_price IS "
        "    NULL) AS no_size, count(*) FILTER (WHERE no_econ) AS "
        "  no_economics FROM (SELECT coalesce(p_blended, p_pinnacle, "
        "  p_internal) AS p, valuation_id, book_obs_id, proposed_qty, "
        "  limit_price, (economics IS NULL OR economics = '{}'::jsonb) AS "
        "  no_econ FROM paper_decisions WHERE verdict = 'ENTER' AND "
        + _IN_WINDOW % {"c": "decided_at"} + ") q", cx.lo, cx.hi))
    n = int((row or {}).get("n") or 0)
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("complete"), n), n=n, unit="share",
        direction=HIGHER, source=["paper_decisions"],
        definition=("ENTER decisions recording a probability, the "
                    "valuation it came from, the book observation, the "
                    "size and limit, and the economics / ENTER decisions"),
        reason=None if n else (why or "NO_ENTER_DECISION_IN_WINDOW"),
        detail={k: row[k] for k in ("no_probability", "no_valuation",
                                    "no_book", "no_size", "no_economics")}
        if n else {})

    row, why = await cx.read(("paper_decisions",), lambda: c.fetchrow(
        "SELECT count(*) FILTER (WHERE p_pinnacle IS NOT NULL) AS priced, "
        "  count(*) FILTER (WHERE p_pinnacle IS NOT NULL AND age >= 0 AND "
        "    age <= $3) AS fresh, "
        "  count(*) FILTER (WHERE p_pinnacle IS NOT NULL AND (age IS NULL "
        "    OR age < 0)) AS unknown_age, "
        "  count(*) FILTER (WHERE p_pinnacle IS NOT NULL AND age > $3) AS "
        "    stale, count(*) FILTER (WHERE p_pinnacle IS NULL) AS unpriced "
        "  FROM (SELECT p_pinnacle, " + _DEREK_AGE + " AS age FROM "
        "  paper_decisions WHERE verdict = 'ENTER' AND "
        + _IN_WINDOW % {"c": "decided_at"} + ") q",
        cx.lo, cx.hi, FRESHNESS_RULE_S))
    n = int((row or {}).get("priced") or 0)
    out["freshness_compliance"] = metric(
        a, "freshness_compliance", win=w,
        value=rate((row or {}).get("fresh"), n), n=n, unit="share",
        direction=HIGHER, source=["paper_decisions"],
        definition=("Pinnacle-priced ENTER decisions whose probability was "
                    "at most %.0f s old at the decision (an unknown age "
                    "counts as not compliant: UNKNOWN blocks) / Pinnacle-"
                    "priced ENTER decisions" % FRESHNESS_RULE_S),
        reason=None if n else (why or "NO_PINNACLE_PRICED_ENTRY_IN_WINDOW"),
        detail={k: row[k] for k in ("stale", "unknown_age", "unpriced")}
        if row else {})

    row, why = await cx.read(
        ("paper_decisions", "paper_handoffs", "paper_settlements"),
        lambda: c.fetchrow(
            "SELECT count(*) AS n, avg((p - won) ^ 2) AS brier, "
            "  avg((price - won) ^ 2) FILTER (WHERE price IS NOT NULL) AS "
            "  brier_price, count(price) AS n_price FROM (SELECT DISTINCT "
            "  ON (d.decision_id) coalesce(d.p_blended, d.p_pinnacle, "
            "  d.p_internal) AS p, d.limit_price::float8 AS price, "
            "  s.outcome, CASE WHEN s.outcome = 'WON' THEN 1.0 ELSE 0.0 END "
            "  AS won FROM paper_decisions d JOIN paper_handoffs h ON "
            "  h.decision_id = d.decision_id JOIN paper_settlements s ON "
            "  s.group_id = h.group_id AND s.holding_side = d.holding_side "
            " WHERE d.verdict = 'ENTER' AND "
            + _IN_WINDOW % {"c": "d.decided_at"} +
            " ORDER BY d.decision_id, s.version DESC) q "
            " WHERE outcome IN ('WON', 'LOST') AND p IS NOT NULL",
            cx.lo, cx.hi))
    n = int((row or {}).get("n") or 0)
    det = {}
    if n:
        det = {"brier_of_the_limit_price": _f(row["brier_price"]),
               "n_with_limit_price": int(row["n_price"] or 0),
               "skill_vs_limit_price": (
                   None if row["brier_price"] is None else
                   _f(float(row["brier_price"]) - float(row["brier"]))),
               "skill_reads": "positive = Derek's probability beat the "
                              "price he paid"}
    out["calibration"] = metric(
        a, "calibration", win=w, value=_f((row or {}).get("brier")), n=n,
        unit="brier", direction=LOWER,
        source=["paper_decisions", "paper_handoffs", "paper_settlements"],
        definition=("Brier score of the decision probability over ENTER "
                    "decisions of the window whose position settled WON / "
                    "LOST (latest settlement version); paired with the "
                    "Brier of the limit price"),
        reason=None if n else (why or "NO_SETTLED_ENTRY_FROM_THE_WINDOW"),
        detail=det)

    pred = _rule_pred(DEREK_APPROVAL_RULE)
    has_k = await cx.has("karen_challenges") is None

    async def fa():
        up = ("SELECT DISTINCT target_id FROM karen_challenges WHERE "
              "target_agent = 'DEREK' AND target_kind = 'paper_decisions' "
              "AND state = 'UPHELD'") if has_k else \
            "SELECT NULL::text AS target_id WHERE false"
        return await c.fetchrow(
            "WITH d AS (SELECT t.decision_id, (" + (pred or "false") +
            ") AS rule_hit FROM paper_decisions t WHERE t.verdict = 'ENTER' "
            " AND " + _IN_WINDOW % {"c": "t.decided_at"} + "), up AS (" + up
            + "), so AS (SELECT DISTINCT ON (h.decision_id) h.decision_id, "
            "  s.outcome FROM paper_handoffs h JOIN paper_settlements s ON "
            "  s.group_id = h.group_id WHERE h.decision_id IN (SELECT "
            "  decision_id FROM d) ORDER BY h.decision_id, s.version DESC), "
            "x AS (SELECT d.rule_hit, up.target_id IS NOT NULL AS upheld, "
            "  so.outcome FROM d LEFT JOIN up ON up.target_id = d.decision_id"
            "  LEFT JOIN so ON so.decision_id = d.decision_id) "
            "SELECT count(*) AS n, count(*) FILTER (WHERE rule_hit OR "
            "  upheld) AS k, count(*) FILTER (WHERE rule_hit) AS rule_hits, "
            "  count(*) FILTER (WHERE upheld) AS upheld, "
            "  count(*) FILTER (WHERE (rule_hit OR upheld) AND outcome = "
            "    'WON') AS k_won, count(*) FILTER (WHERE (rule_hit OR upheld)"
            "    AND outcome = 'LOST') AS k_lost, count(*) FILTER (WHERE "
            "    (rule_hit OR upheld) AND outcome IS NULL) AS k_unsettled "
            "  FROM x", cx.lo, cx.hi)
    row, why = await cx.read(
        ("paper_decisions", "paper_handoffs", "paper_settlements"), fa)
    n = int((row or {}).get("n") or 0)
    out["false_approval"] = metric(
        a, "false_approval", win=w, value=rate((row or {}).get("k"), n),
        n=n, unit="share", direction=LOWER,
        source=["paper_decisions", "karen_challenges", "paper_handoffs",
                "paper_settlements"],
        definition=("ENTER decisions on which Karen's %s rule holds (the "
                    "same predicate, over every ENTER of the window) or a "
                    "Karen challenge was UPHELD by a third party / ENTER "
                    "decisions; a LOWER BOUND: only defects a rule names "
                    "are counted" % DEREK_APPROVAL_RULE),
        reason=None if n else (why or "NO_ENTER_DECISION_IN_WINDOW"),
        detail=({"rule": DEREK_APPROVAL_RULE, "rule_hits": row["rule_hits"],
                 "upheld_challenges": row["upheld"],
                 "karen_table": has_k,
                 "flagged_resolved_outcomes": {
                     "WON": row["k_won"], "LOST": row["k_lost"],
                     "UNSETTLED": row["k_unsettled"]}} if n else {}))

    row, why = await cx.read(("lol_ledger",), lambda: c.fetchrow(
        "WITH l AS (SELECT DISTINCT ON (decision_ref) classification, "
        "  attribution, hypothetical_pnl_usd, classifier_version FROM "
        "  lol_ledger WHERE " + _IN_WINDOW % {"c": "decided_at"} +
        "  ORDER BY decision_ref, classified_at DESC) "
        "SELECT count(*) FILTER (WHERE classification = 'FALSE_REFUSAL') AS "
        "  false_refusals, count(*) FILTER (WHERE classification = "
        "  'GOOD_REFUSAL') AS good, count(*) FILTER (WHERE classification = "
        "  'UNKNOWABLE') AS unknowable, sum(hypothetical_pnl_usd) FILTER ("
        "  WHERE classification = 'FALSE_REFUSAL') AS false_hyp_pnl, "
        "  (SELECT jsonb_object_agg(attribution, n) FROM (SELECT "
        "   attribution, count(*) AS n FROM l WHERE classification = "
        "   'FALSE_REFUSAL' GROUP BY attribution) z) AS by_attribution, "
        "  array_agg(DISTINCT classifier_version) AS versions FROM l",
        cx.lo, cx.hi))
    good = int((row or {}).get("good") or 0)
    bad = int((row or {}).get("false_refusals") or 0)
    n = good + bad
    out["false_refusal"] = metric(
        a, "false_refusal", win=w, value=rate(bad, n), n=n, unit="share",
        direction=LOWER, source=["lol_ledger"],
        definition=("refusals of the window whose market settled and which "
                    "the lost-opportunity ledger classified FALSE_REFUSAL "
                    "(decision-time evidence showed executable net EV > 0 "
                    "and a named defect refused it) / FALSE + GOOD; "
                    "UNKNOWABLE is reported and excluded; the latest "
                    "classification per decision"),
        reason=None if n else (why or (
            "ONLY_UNKNOWABLE_REFUSALS_CLASSIFIED (%d)"
            % int((row or {}).get("unknowable") or 0)
            if row and row["unknowable"] else
            "NO_CLASSIFIED_SETTLED_REFUSAL_IN_WINDOW")),
        detail=({"unknowable": int(row["unknowable"] or 0),
                 "false_refusal_hypothetical_pnl_usd": _f(
                     row["false_hyp_pnl"]),
                 "hypothetical_label": "HYPOTHETICAL (never realized)",
                 "false_by_attribution": _j(row["by_attribution"]) or {},
                 "classifier_versions": [v for v in (row["versions"] or [])
                                         if v]} if row else {}))
    return out


# ═════════════════════════════════════════════════════════════════════
# XAVIER
# ═════════════════════════════════════════════════════════════════════

_INC = "incremental->'ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT'"


async def xavier(cx: _Ctx) -> dict:
    a, w, c = "XAVIER", cx.win, cx.conn
    out = {}
    xma = "xavier_management_assessments"
    row, why = await cx.read((xma,), lambda: c.fetchrow(
        "SELECT " + _PCT + ", count(*) FILTER (WHERE within_bound) AS "
        "  within, count(*) FILTER (WHERE position_kind = 'PAPER') AS paper,"
        "  count(*) FILTER (WHERE position_kind = 'ACTUAL') AS actual FROM "
        "  (SELECT review_latency_s AS s, within_bound, position_kind FROM "
        "  xavier_management_assessments WHERE trigger = 'MARKET_EVENT' AND "
        "  review_latency_s >= 0 AND " + _IN_WINDOW % {"c": "assessed_at"}
        + ") q", cx.lo, cx.hi))
    out["decision_latency"] = _lat(
        row, a, w, source=[xma],
        definition=("seconds from the market change (due_at) to Xavier's "
                    "assessment, over MARKET_EVENT assessments (PAPER and "
                    "ACTUAL books, split in detail)"),
        reason_empty=why or "NO_MARKET_EVENT_ASSESSMENT_IN_WINDOW",
        detail=({"within_latency_bound_share": rate(row["within"],
                                                     row["n"]),
                 "paper": row["paper"], "actual": row["actual"]}
                if row and row["n"] else {}))

    row, why = await cx.read((xma,), lambda: c.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE evidence_state IS NOT "
        "  NULL AND venue_economics IS NOT NULL AND (evidence_state = $3 OR "
        "  (probability IS NOT NULL AND probability_source IS NOT NULL AND "
        "  probability_age_s IS NOT NULL))) AS complete, "
        "  count(*) FILTER (WHERE evidence_state IS DISTINCT FROM $3 AND "
        "    probability IS NULL) AS no_probability, "
        "  count(*) FILTER (WHERE evidence_state IS DISTINCT FROM $3 AND "
        "    probability_age_s IS NULL) AS no_probability_age, "
        "  count(*) FILTER (WHERE venue_economics IS NULL) AS no_economics "
        "  FROM xavier_management_assessments WHERE "
        + _IN_WINDOW % {"c": "assessed_at"}, cx.lo, cx.hi, E_NONE))
    n = int((row or {}).get("n") or 0)
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("complete"), n), n=n, unit="share",
        direction=HIGHER, source=[xma],
        definition=("assessments recording their evidence state and venue "
                    "economics, and -- unless the state is %s, an explicit "
                    "unavailability -- the probability, its source and its "
                    "age / assessments" % E_NONE),
        reason=None if n else (why or "NO_ASSESSMENT_IN_WINDOW"),
        detail={k: row[k] for k in ("no_probability", "no_probability_age",
                                    "no_economics")} if n else {})

    row, why = await cx.read((xma, "paper_settlements"), lambda: c.fetchrow(
        "SELECT count(*) AS n, avg((p - won) ^ 2) AS brier FROM (SELECT "
        "  DISTINCT ON (x.group_id) x.probability AS p, s.outcome, CASE "
        "  WHEN s.outcome = 'WON' THEN 1.0 ELSE 0.0 END AS won FROM "
        "  xavier_management_assessments x JOIN LATERAL (SELECT outcome, "
        "  settled_at FROM paper_settlements z WHERE z.group_id = "
        "  x.group_id ORDER BY z.version DESC LIMIT 1) s ON true "
        " WHERE x.position_kind = 'PAPER' AND x.evidence_state = $3 AND "
        "  x.probability BETWEEN 0 AND 1 AND x.assessed_at <= s.settled_at "
        "  AND " + _IN_WINDOW % {"c": "x.assessed_at"} +
        " ORDER BY x.group_id, x.assessed_at DESC) q "
        " WHERE outcome IN ('WON', 'LOST')", cx.lo, cx.hi, E_FRESH))
    n = int((row or {}).get("n") or 0)
    out["calibration"] = metric(
        a, "calibration", win=w, value=_f((row or {}).get("brier")), n=n,
        unit="brier", direction=LOWER, source=[xma, "paper_settlements"],
        definition=("Brier score of Xavier's held-side probability -- his "
                    "LAST fresh assessment of each settled PAPER position "
                    "before its settlement (one per position: no position "
                    "weighs more for being reviewed more)"),
        reason=None if n else (why or "NO_SETTLED_POSITION_WITH_A_FRESH_"
                                      "ASSESSMENT_IN_WINDOW"))

    pred = _rule_pred(XAVIER_APPROVAL_RULE)
    has_k = await cx.has("karen_challenges") is None

    async def fa():
        up = ("SELECT DISTINCT target_id FROM karen_challenges WHERE "
              "target_agent = 'XAVIER' AND target_kind = "
              "'paper_xavier_reviews' AND state = 'UPHELD'") if has_k else \
            "SELECT NULL::text AS target_id WHERE false"
        return await c.fetchrow(
            "WITH r AS (SELECT t.review_id, t.group_id, (" + (pred or "false")
            + ") AS rule_hit FROM paper_xavier_reviews t WHERE "
            "  t.recommendation = 'HOLD' AND "
            + _IN_WINDOW % {"c": "t.reviewed_at"} + "), up AS (" + up + "), "
            "x AS (SELECT r.group_id, r.rule_hit, up.target_id IS NOT NULL "
            "  AS upheld FROM r LEFT JOIN up ON up.target_id = r.review_id), "
            "g AS (SELECT DISTINCT group_id FROM x WHERE rule_hit OR upheld),"
            "so AS (SELECT DISTINCT ON (s.group_id) s.group_id, s.outcome "
            "  FROM paper_settlements s WHERE s.group_id IN (SELECT group_id "
            "  FROM g) ORDER BY s.group_id, s.version DESC) "
            "SELECT (SELECT count(*) FROM x) AS n, (SELECT count(*) FROM x "
            "  WHERE rule_hit OR upheld) AS k, (SELECT count(*) FROM x WHERE "
            "  rule_hit) AS rule_hits, (SELECT count(*) FROM x WHERE upheld) "
            "  AS upheld, (SELECT count(*) FROM g) AS groups, (SELECT "
            "  count(*) FROM so WHERE outcome = 'WON') AS g_won, (SELECT "
            "  count(*) FROM so WHERE outcome = 'LOST') AS g_lost",
            cx.lo, cx.hi)
    row, why = await cx.read(("paper_xavier_reviews", "paper_settlements"),
                             fa)
    n = int((row or {}).get("n") or 0)
    out["false_approval"] = metric(
        a, "false_approval", win=w, value=rate((row or {}).get("k"), n),
        n=n, unit="share", direction=LOWER,
        source=["paper_xavier_reviews", "karen_challenges",
                "paper_settlements"],
        definition=("HOLD reviews on which Karen's %s rule holds (the same "
                    "predicate over every HOLD review of the window -- her "
                    "challenges are a throttled sample) or a Karen "
                    "challenge was UPHELD / HOLD reviews; a LOWER BOUND"
                    % XAVIER_APPROVAL_RULE),
        reason=None if n else (why or "NO_HOLD_REVIEW_IN_WINDOW"),
        detail=({"rule": XAVIER_APPROVAL_RULE,
                 "rule_hits": row["rule_hits"],
                 "upheld_challenges": row["upheld"], "karen_table": has_k,
                 "flagged_positions": row["groups"],
                 "flagged_positions_resolved": {"WON": row["g_won"],
                                                "LOST": row["g_lost"]}}
                if n else {}))

    rows, why = await cx.read(("xavier_value_add",), lambda: c.fetch(
        "WITH v AS (SELECT DISTINCT ON (thesis_id) position_kind, "
        "  incremental FROM xavier_value_add WHERE status = 'FINAL' AND "
        + _IN_WINDOW % {"c": "computed_at"} +
        "  ORDER BY thesis_id, computed_at DESC), x AS (SELECT "
        "  position_kind, CASE WHEN jsonb_typeof(" + _INC + "->'pnl_usd') = "
        "  'number' THEN (" + _INC + "->>'pnl_usd')::float8 END AS inc, "
        "  CASE WHEN jsonb_typeof(" + _INC + "->'turnover_usd') = 'number' "
        "  THEN (" + _INC + "->>'turnover_usd')::float8 END AS dturn FROM v "
        "  WHERE " + _INC + "->>'available' = 'true') "
        "SELECT position_kind, count(inc) AS n, avg(inc) AS mean_inc, "
        "  sum(inc) AS total_inc, count(*) FILTER (WHERE abs(coalesce("
        "  dturn, 0)) > 1e-9 AND inc IS NOT NULL) AS acted, count(*) "
        "  FILTER (WHERE abs(coalesce(dturn, 0)) > 1e-9 AND inc < 0) AS "
        "  acted_worse FROM x GROUP BY position_kind", cx.lo, cx.hi))
    books = {r["position_kind"]: dict(r) for r in (rows or [])}
    p = books.get("PAPER") or {}
    actual = books.get("ACTUAL")
    actual_d = None if actual is None else {
        "n": int(actual["n"]), "mean_usd": _f(actual["mean_inc"]),
        "total_usd": _f(actual["total_inc"]), "acted": int(actual["acted"]),
        "acted_worse": int(actual["acted_worse"])}
    n = int(p.get("n") or 0)
    out["value_added"] = metric(
        a, "value_added", win=w, value=_f(p.get("mean_inc")) if n else None,
        n=n, unit="usd per settled PAPER position", direction=HIGHER,
        source=["xavier_value_add"],
        definition=("mean of ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT over "
                    "FINAL value-add records of the window (the "
                    "counterfactuals frozen on the entry thesis; PAPER -- "
                    "ACTUAL is reported apart, never summed)"),
        reason=None if n else (why or "NO_FINAL_VALUE_ADD_IN_WINDOW"),
        detail={"total_usd": _f(p.get("total_inc")), "actual_book": actual_d})
    k = int(p.get("acted") or 0)
    out["false_refusal"] = metric(
        a, "false_refusal", win=w,
        value=rate(p.get("acted_worse"), k), n=k, unit="share",
        direction=LOWER, source=["xavier_value_add"],
        definition=("PAPER positions Xavier sold out of (actual turnover "
                    "differs from holding) whose result fell short of "
                    "holding to settlement / positions he sold out of; one "
                    "settlement is one draw -- an outcome measure"),
        reason=None if k else (why or (
            "NO_XAVIER_EXIT_WITH_A_FINAL_OUTCOME (%d held to settlement)"
            % n if n else "NO_FINAL_VALUE_ADD_IN_WINDOW")),
        detail={"actual_book": actual_d})

    has_state = await cx.col(xma, "recommendation_state")
    row, why = await cx.read((xma,), lambda: c.fetchrow(
        "SELECT count(*) FILTER (WHERE recommendation IS NOT NULL AND NOT "
        "  (recommendation = ANY($3::text[]))) AS actions, count(*) FILTER "
        "  (WHERE recommendation IS NOT NULL AND NOT (recommendation = "
        "  ANY($3::text[])) AND evidence_state = $4) AS fresh_actions, "
        "  count(*) FILTER (WHERE trigger = 'MARKET_EVENT') AS market, "
        "  count(*) FILTER (WHERE trigger = 'MARKET_EVENT' AND "
        "  evidence_state = $4) AS market_fresh FROM "
        "  xavier_management_assessments WHERE "
        + _IN_WINDOW % {"c": "assessed_at"} + (
            " AND recommendation_state IS NOT NULL" if has_state else ""),
        cx.lo, cx.hi, list(NON_ACTIONS), E_FRESH))
    n = int((row or {}).get("actions") or 0)
    out["freshness_compliance"] = metric(
        a, "freshness_compliance", win=w,
        value=rate((row or {}).get("fresh_actions"), n), n=n, unit="share",
        direction=HIGHER, source=[xma],
        definition=("management recommendations (HOLD included; never the "
                    "%s non-actions) recorded on a FRESH probability (the "
                    "30 s rule) / management recommendations, since "
                    "migration 222" % "/".join(NON_ACTIONS)),
        reason=None if n else (why or "NO_MANAGEMENT_RECOMMENDATION_IN_"
                                      "WINDOW"),
        detail=({"market_event_assessments": row["market"],
                 "market_event_fresh_share": rate(row["market_fresh"],
                                                  row["market"]),
                 "since_migration_222_only": has_state} if row else {}))
    return out


# ═════════════════════════════════════════════════════════════════════
# KAREN, AND EVERY AGENT AS A CHALLENGE TARGET / EVALUATOR
# ═════════════════════════════════════════════════════════════════════

_CITES_TARGET = ("%s @> jsonb_build_array(jsonb_build_object('kind', "
                 "target_kind, 'id', target_id))")


async def karen(cx: _Ctx) -> dict:
    a, w, c = "KAREN", cx.win, cx.conn
    out = {}
    kc = "karen_challenges"
    row, why = await cx.read((kc,), lambda: c.fetchrow(
        "SELECT " + _PCT + ", count(*) FILTER (WHERE cites) AS cites FROM "
        "  (SELECT extract(epoch FROM challenged_at - record_at) AS s, "
        + _CITES_TARGET % "evidence_refs" + " AS cites FROM "
        "  karen_challenges WHERE " + _IN_WINDOW % {"c": "challenged_at"}
        + ") q WHERE s >= 0", cx.lo, cx.hi))
    out["decision_latency"] = _lat(
        row, a, w, source=[kc],
        definition=("seconds from the challenged record (record_at) to "
                    "Karen's challenge"),
        reason_empty=why or "NO_CHALLENGE_IN_WINDOW")
    n = int((row or {}).get("n") or 0)
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("cites"), n), n=n, unit="share",
        direction=HIGHER, source=[kc],
        definition=("challenges whose evidence cites the challenged record "
                    "itself (target_kind, target_id) / challenges"),
        reason=None if n else (why or "NO_CHALLENGE_IN_WINDOW"))
    row, why = await cx.read((kc,), lambda: c.fetchrow(
        "SELECT count(*) FILTER (WHERE state = 'UPHELD') AS upheld, "
        "  count(*) FILTER (WHERE state = 'REJECTED') AS rejected, "
        "  count(*) FILTER (WHERE state = 'WITHDRAWN') AS withdrawn, "
        "  count(*) FILTER (WHERE blocked AND false_block IS NOT NULL) AS "
        "  blocks_assessed, count(*) FILTER (WHERE blocked AND false_block) "
        "  AS false_blocks, (SELECT jsonb_object_agg(detector, n) FROM "
        "  (SELECT detector, count(*) AS n FROM karen_challenges WHERE "
        "   state = 'REJECTED' AND " + _IN_WINDOW % {"c": "resolved_at"} +
        "   GROUP BY detector) z) AS rejected_by_detector, (SELECT count(*) "
        "  FROM karen_challenges WHERE state IN ('OPEN', 'RESPONDED')) AS "
        "  unresolved_now FROM karen_challenges WHERE "
        + _IN_WINDOW % {"c": "resolved_at"}, cx.lo, cx.hi))
    up = int((row or {}).get("upheld") or 0)
    rej = int((row or {}).get("rejected") or 0)
    n = up + rej
    det = {} if not row else {
        "withdrawn": int(row["withdrawn"] or 0),
        "false_block_rate": rate(row["false_blocks"], row["blocks_assessed"]),
        "blocks_assessed": int(row["blocks_assessed"] or 0),
        "unresolved_now": int(row["unresolved_now"] or 0)}
    out["challenge_quality"] = metric(
        a, "challenge_quality", win=w, value=rate(up, n), n=n, unit="share",
        direction=HIGHER, source=[kc],
        definition=("challenges resolved in the window UPHELD / (UPHELD + "
                    "REJECTED); resolved by a third party, never Karen or "
                    "the target (migrations 207 / 212)"),
        reason=None if n else (why or "NO_CHALLENGE_RESOLVED_IN_WINDOW"),
        detail=det)
    out["false_refusal"] = metric(
        a, "false_refusal", win=w, value=rate(rej, n), n=n, unit="share",
        direction=LOWER, source=[kc],
        definition=("challenges the independent evaluator REJECTED "
                    "(overturned) / resolved UPHELD + REJECTED -- a "
                    "challenge is Karen refusing a record"),
        reason=None if n else (why or "NO_CHALLENGE_RESOLVED_IN_WINDOW"),
        detail={"rejected_by_detector": _j((row or {}).get(
            "rejected_by_detector")) or {}})
    out["false_approval"] = metric(
        a, "false_approval", win=w, source=[kc],
        definition="defects Karen let pass (her recall)",
        reason=("NO_INDEPENDENT_DEFECT_CENSUS: a defect no rule names is "
                "never recorded, so what Karen missed cannot be counted"),
        direction=LOWER)
    return out


async def peer_review(cx: _Ctx) -> dict:
    """{agent: {challenge_quality, decision_latency?, evidence_complete?}}
    for challenge targets (dispute accuracy) and evaluators (evaluation
    latency and evidence)."""
    c, w = cx.conn, cx.win
    kc = "karen_challenges"
    rows, why = await cx.read((kc,), lambda: c.fetch(
        "SELECT target_agent, count(*) FILTER (WHERE response_stance = "
        "  'DISPUTE' AND state IN ('UPHELD', 'REJECTED')) AS disputes, "
        "  count(*) FILTER (WHERE response_stance = 'DISPUTE' AND state = "
        "  'REJECTED') AS sustained, count(*) FILTER (WHERE "
        "  response_stance = 'CONCEDE') AS conceded, count(*) FILTER (WHERE "
        "  state = 'UPHELD') AS upheld_against FROM karen_challenges WHERE "
        + _IN_WINDOW % {"c": "coalesce(resolved_at, responded_at)"} +
        " GROUP BY target_agent", cx.lo, cx.hi))
    by = {r["target_agent"]: dict(r) for r in (rows or [])}
    out: dict = {}
    for agent in CHALLENGE_TARGETS:
        r = by.get(agent) or {}
        n = int(r.get("disputes") or 0)
        out.setdefault(agent, {})["challenge_quality"] = metric(
            agent, "challenge_quality", win=w,
            value=rate(r.get("sustained"), n), n=n, unit="share",
            direction=HIGHER, source=[kc],
            definition=("the agent's DISPUTES of Karen's challenges that the "
                        "independent evaluator sustained (REJECTED the "
                        "challenge) / its resolved disputes"),
            reason=None if n else (why or (
                "NO_RESOLVED_DISPUTE (%d conceded)"
                % int(r.get("conceded") or 0))),
            detail={"conceded": int(r.get("conceded") or 0),
                    "upheld_against": int(r.get("upheld_against") or 0)})
    rows, why = await cx.read((kc,), lambda: c.fetch(
        "SELECT resolved_by, " + _PCT + ", count(*) FILTER (WHERE cites) AS "
        "  cites FROM (SELECT resolved_by, extract(epoch FROM resolved_at - "
        "  responded_at) AS s, " + _CITES_TARGET % "resolution_evidence_refs"
        + " AS cites FROM karen_challenges WHERE state IN ('UPHELD', "
        "  'REJECTED') AND responded_at IS NOT NULL AND resolved_by = "
        "  ANY($3::text[]) AND " + _IN_WINDOW % {"c": "resolved_at"} +
        ") q WHERE s >= 0 GROUP BY resolved_by", cx.lo, cx.hi,
        list(EVALUATORS)))
    ev = {r["resolved_by"]: dict(r) for r in (rows or [])}
    for agent in EVALUATORS:
        r = ev.get(agent)
        out.setdefault(agent, {})["evaluation"] = (r, why)
    return out


# ═════════════════════════════════════════════════════════════════════
# AUDREY, THE CHIEF ALLOCATOR, EDDIE, SCOUT
# ═════════════════════════════════════════════════════════════════════

async def audrey(cx: _Ctx, peer: dict) -> dict:
    a, w = "AUDREY", cx.win
    r, why = (peer.get(a) or {}).get("evaluation") or (None, None)
    n = int((r or {}).get("n") or 0)
    out = {"decision_latency": _lat(
        r, a, w, source=["karen_challenges"],
        definition=("seconds from the target's response to Audrey's "
                    "independent evaluation (karen.EVALUATOR_FOR: Derek, "
                    "Xavier, the Chief Allocator)"),
        reason_empty=why or "NO_EVALUATION_BY_AUDREY_IN_WINDOW")}
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((r or {}).get("cites"), n), n=n, unit="share",
        direction=HIGHER, source=["karen_challenges"],
        definition=("Audrey's evaluations whose evidence cites the "
                    "challenged record itself / her evaluations"),
        reason=None if n else (why or "NO_EVALUATION_BY_AUDREY_IN_WINDOW"))
    out["false_approval"] = metric(
        a, "false_approval", win=w, source=["karen_challenges"],
        definition="Audrey evaluations later shown wrong",
        reason=("NO_THIRD_PARTY_REVIEW_OF_AUDREY_EVALUATIONS: her UPHELD / "
                "REJECTED outcomes are not re-reviewed by anyone"),
        direction=LOWER)
    out["false_refusal"] = metric(
        a, "false_refusal", win=w, source=["paper_audrey_findings"],
        definition="Audrey findings later shown to be false alarms",
        reason=("NO_RECORD_OVERTURNS_AN_AUDREY_FINDING: no table marks a "
                "finding a false alarm"), direction=LOWER)
    return out


async def allocator(cx: _Ctx) -> dict:
    a, w, c = "CHIEF_ALLOCATOR", cx.win, cx.conn
    out = {}
    row, why = await cx.read(("intel_allocations", "paper_decisions"),
                             lambda: c.fetchrow(
        "SELECT " + _PCT + " FROM (SELECT extract(epoch FROM "
        "  min(x.computed_at) - d.decided_at) AS s FROM intel_allocations x "
        "  JOIN paper_decisions d ON d.decision_id = x.decision_id WHERE "
        "  x.computed_at >= to_timestamp($1) AND "
        + _IN_WINDOW % {"c": "d.decided_at"} +
        "  GROUP BY x.decision_id, d.decided_at) q WHERE s >= 0",
        cx.lo, cx.hi))
    out["decision_latency"] = _lat(
        row, a, w, source=["intel_allocations", "paper_decisions"],
        definition=("seconds from Derek's decision to its first shadow "
                    "allocation"),
        reason_empty=why or "NO_ALLOCATED_DECISION_IN_WINDOW")
    row, why = await cx.read(("intel_allocations",), lambda: c.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE "
        + _EMPTY_UNMEASURED % {"c": "unmeasured"} + ") AS complete FROM "
        "  intel_allocations WHERE " + _IN_WINDOW % {"c": "computed_at"},
        cx.lo, cx.hi))
    n = int((row or {}).get("n") or 0)
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("complete"), n), n=n, unit="share",
        direction=HIGHER, source=["intel_allocations"],
        definition=("shadow allocations naming no unmeasured input / shadow "
                    "allocations"),
        reason=None if n else (why or "NO_ALLOCATION_IN_WINDOW"))
    why = ("SHADOW_ALLOCATION_HAS_NO_OUTCOME: a shadow weight sizes no "
           "order, so no allocation is approved or refused; the twin's "
           "ALLOCATOR worlds compare it counterfactually (value_added)")
    for m in ("false_approval", "false_refusal"):
        out[m] = metric(a, m, win=w, source=["intel_allocations"],
                        definition="allocations later shown wrong",
                        reason=why, direction=LOWER)
    return out


async def eddie(cx: _Ctx) -> dict:
    a, w, c = "EDDIE", cx.win, cx.conn
    out = {}
    est = "eddie_execution_estimates"
    row, why = await cx.read((est,), lambda: c.fetchrow(
        "SELECT " + _PCT + ", count(*) AS n_all, count(*) FILTER (WHERE "
        "  complete) AS complete, "
        "  count(*) FILTER (WHERE rec = ANY($3::text[])) AS act, "
        "  count(*) FILTER (WHERE rec = ANY($3::text[]) AND age IS NOT NULL "
        "    AND age >= 0 AND age <= $4) AS act_fresh, "
        "  count(*) FILTER (WHERE rec = ANY($3::text[]) AND age IS NULL) AS "
        "    act_unknown_age, (SELECT count(*) FROM eddie_execution_estimates"
        "  WHERE " + _IN_WINDOW % {"c": "estimated_at"} + ") AS total FROM "
        "  (SELECT extract(epoch FROM estimated_at - decided_at) AS s, "
        + _EMPTY_UNMEASURED % {"c": "unmeasured"} + " AS complete, "
        "  recommendation AS rec, book_age_s AS age FROM "
        "  eddie_execution_estimates WHERE "
        + _IN_WINDOW % {"c": "estimated_at"} + ") q",
        cx.lo, cx.hi, list(EXECUTE_RECS), EDDIE_MAX_BOOK_AGE_S))
    out["decision_latency"] = _lat(
        row, a, w, source=[est],
        definition="seconds from Derek's decision to Eddie's estimate",
        reason_empty=why or "NO_ESTIMATE_IN_WINDOW")
    n = int((row or {}).get("n_all") or 0)
    top, _w = await cx.read((est,), lambda: c.fetch(
        "SELECT k, count(*) AS n FROM eddie_execution_estimates, "
        "  jsonb_object_keys(CASE WHEN jsonb_typeof(unmeasured) = 'object' "
        "  THEN unmeasured ELSE '{}'::jsonb END) k WHERE "
        + _IN_WINDOW % {"c": "estimated_at"} +
        " GROUP BY k ORDER BY n DESC, k LIMIT 5", cx.lo, cx.hi))
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("complete"), n), n=n, unit="share",
        direction=HIGHER, source=[est],
        definition=("estimates naming no unmeasured dimension / estimates"),
        reason=None if n else (why or "NO_ESTIMATE_IN_WINDOW"),
        detail={"most_unmeasured": {r["k"]: int(r["n"]) for r in top or []}})
    k = int((row or {}).get("act") or 0)
    out["freshness_compliance"] = metric(
        a, "freshness_compliance", win=w,
        value=rate((row or {}).get("act_fresh"), k), n=k, unit="share",
        direction=HIGHER, source=[est],
        definition=("estimates advising execution (%s) on a book at most "
                    "%.0f s old (Eddie's own bound; an unknown age is not "
                    "compliant) / estimates advising execution"
                    % ("/".join(EXECUTE_RECS), EDDIE_MAX_BOOK_AGE_S)),
        reason=None if k else (why or "NO_EXECUTION_ADVICE_IN_WINDOW"),
        detail={"unknown_book_age": (row or {}).get("act_unknown_age")})

    row, why = await cx.read((est, "eddie_execution_outcomes"),
                             lambda: c.fetchrow(
        "SELECT count(*) FILTER (WHERE rl IS NOT NULL AND pl IS NOT NULL) "
        "  AS n_cal, avg(abs(rl - pl)) AS mae, avg(rl - pl) AS bias, "
        "  count(*) FILTER (WHERE rec = ANY($3::text[]) AND rl IS NOT NULL "
        "    AND te IS NOT NULL) AS n_act, count(*) FILTER (WHERE rec = "
        "    ANY($3::text[]) AND rl > te) AS act_bad, count(*) FILTER (WHERE "
        "    rec = ANY($4::text[]) AND rl IS NOT NULL AND te IS NOT NULL) AS "
        "    n_hold, count(*) FILTER (WHERE rec = ANY($4::text[]) AND "
        "    rl < te) AS hold_bad, count(*) FILTER (WHERE nl IS NOT NULL AND "
        "    rl IS NOT NULL) AS n_alpha, avg(nl - rl) AS alpha FROM (SELECT "
        "  x.realized_execution_loss_pp AS rl, "
        "  x.predicted_execution_loss_pp AS pl, "
        "  x.naive_execution_loss_pp AS nl, e.theoretical_edge_pp AS te, "
        "  e.recommendation AS rec FROM eddie_execution_outcomes x JOIN "
        "  eddie_execution_estimates e USING (estimate_id) WHERE "
        + _IN_WINDOW % {"c": "x.measured_at"} + ") q",
        cx.lo, cx.hi, list(EXECUTE_RECS), list(HOLD_BACK_RECS)))
    r = row or {}
    src = [est, "eddie_execution_outcomes"]
    n = int(r.get("n_cal") or 0)
    out["calibration"] = metric(
        a, "calibration", win=w, value=_f(r.get("mae")) if n else None, n=n,
        unit="pp (mean absolute error)", direction=LOWER, source=src,
        definition=("mean |realized - predicted execution loss| over "
                    "outcomes of the window (eddie.DEFINITIONS "
                    "predicted_vs_realized_execution_loss_pp)"),
        reason=None if n else (why or "NO_OUTCOME_WITH_BOTH_PREDICTED_AND_"
                                      "REALIZED_LOSS"),
        detail={"mean_signed_bias_pp": _f(r.get("bias"))})
    n = int(r.get("n_act") or 0)
    out["false_approval"] = metric(
        a, "false_approval", win=w, value=rate(r.get("act_bad"), n), n=n,
        unit="share", direction=LOWER, source=src,
        definition=("execution advice (%s) whose realized execution loss "
                    "exceeded the theoretical edge (the fill gave the edge "
                    "away) / execution advice with an outcome"
                    % "/".join(EXECUTE_RECS)),
        reason=None if n else (why or "NO_OUTCOME_OF_EXECUTION_ADVICE"))
    n = int(r.get("n_hold") or 0)
    out["false_refusal"] = metric(
        a, "false_refusal", win=w, value=rate(r.get("hold_bad"), n), n=n,
        unit="share", direction=LOWER, source=src,
        definition=("WAIT / SKIP_EXECUTION advice on candidates that "
                    "filled anyway and kept their edge (realized loss < "
                    "theoretical edge) / such advice with an outcome"),
        reason=None if n else (why or "NO_FILLED_CANDIDATE_EDDIE_ADVISED_"
                                      "AGAINST"))
    n = int(r.get("n_alpha") or 0)
    out["value_added"] = metric(
        a, "value_added", win=w, value=_f(r.get("alpha")) if n else None,
        n=n, unit="pp per filled candidate", direction=HIGHER, source=src,
        definition=("mean (naive taker execution loss at the decision - "
                    "realized execution loss): execution alpha "
                    "(eddie.DEFINITIONS execution_alpha_pp)"),
        reason=None if n else (why or "NO_OUTCOME_WITH_A_NAIVE_BASELINE"))
    return out


async def scout(cx: _Ctx, queue_latency: dict) -> dict:
    a, w, c = "SCOUT", cx.win, cx.conn
    out = {}
    q = (queue_latency.get(a) or {}).get(K_RESEARCH)
    out["decision_latency"] = _lat(
        q, a, w, source=["agent_work_requests", "agent_work_request_events"],
        definition=("seconds from a research question's enqueue to its "
                    "completion (migration 234 queue, %s)" % K_RESEARCH),
        reason_empty=(queue_latency.get("_why")
                      or "NO_RESEARCH_QUESTION_RESOLVED_IN_WINDOW"))
    row, why = await cx.read(("scout_features", "scout_feature_tournaments",
                              "scout_feature_observations"),
                             lambda: c.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE has_test AND has_obs) "
        "  AS complete, count(*) FILTER (WHERE NOT has_test) AS no_test, "
        "  count(*) FILTER (WHERE NOT has_obs) AS no_observation FROM "
        "  (SELECT EXISTS (SELECT 1 FROM scout_feature_tournaments t WHERE "
        "   t.feature_id = f.feature_id AND t.frozen_at <= coalesce("
        "   t.evaluated_at, 'infinity'::timestamptz)) AS has_test, EXISTS "
        "   (SELECT 1 FROM scout_feature_observations o WHERE o.feature_id = "
        "   f.feature_id) AS has_obs FROM scout_features f WHERE "
        + _IN_WINDOW % {"c": "f.proposed_at"} + ") q", cx.lo, cx.hi))
    n = int((row or {}).get("n") or 0)
    out["evidence_completeness"] = metric(
        a, "evidence_completeness", win=w,
        value=rate((row or {}).get("complete"), n), n=n, unit="share",
        direction=HIGHER, source=["scout_features",
                                  "scout_feature_tournaments",
                                  "scout_feature_observations"],
        definition=("features Scout proposed that carry a forward test "
                    "frozen before its evaluation and at least one "
                    "observation / features proposed"),
        reason=None if n else (why or "NO_FEATURE_PROPOSED_IN_WINDOW"),
        detail={k: row[k] for k in ("no_test", "no_observation")}
        if n else {})
    row, why = await cx.read(("scout_features", "scout_feature_tournaments"),
                             lambda: c.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE t.verdict = "
        "  'REJECTED') AS rejected FROM scout_feature_tournaments t JOIN "
        "  scout_features f USING (feature_id) WHERE t.verdict IS NOT NULL "
        "  AND " + _IN_WINDOW % {"c": "t.evaluated_at"}, cx.lo, cx.hi))
    n = int((row or {}).get("n") or 0)
    out["false_approval"] = metric(
        a, "false_approval", win=w, value=rate((row or {}).get("rejected"),
                                                n),
        n=n, unit="share", direction=LOWER,
        source=["scout_feature_tournaments", "scout_features"],
        definition=("Scout's proposed features whose frozen forward test "
                    "the evaluator REJECTED / evaluated tests (a proposal "
                    "is Scout approving a hypothesis for testing)"),
        reason=None if n else (why or "NO_FORWARD_TEST_EVALUATED_IN_WINDOW"))
    return out


# ═════════════════════════════════════════════════════════════════════
# CITATIONS, THE QUEUE, THE TWIN, MEMORY
# ═════════════════════════════════════════════════════════════════════

async def _citation_refs(cx: _Ctx) -> tuple:
    """{agent: [(kind, id), ...]} from every citation source, the newest
    CITATION_ROWS rows per agent per source; and {source: rows read}."""
    refs: dict = {a: [] for a in AGENTS}
    read: dict = {}
    for table, who, col, tcol in CITATION_SOURCES:
        if await cx.has(table) or not await cx.col(table, col):
            read["%s.%s" % (table, col)] = "ABSENT"
            continue
        rows, why = await cx.read((table,), lambda table=table, who=who,
                                  col=col, tcol=tcol: cx.conn.fetch(
            "SELECT agent, e->>'kind' AS kind, e->>'id' AS id FROM (SELECT "
            "  agent, refs FROM (SELECT " + who + " AS agent, " + col +
            "  AS refs, row_number() OVER (PARTITION BY " + who + " ORDER BY "
            + tcol + " DESC) AS rn FROM " + table + " WHERE " + who +
            "  = ANY($3::text[]) AND " + _IN_WINDOW % {"c": tcol} +
            "  ) z WHERE rn <= $4) x, jsonb_array_elements(CASE WHEN "
            "  jsonb_typeof(x.refs) = 'array' THEN x.refs ELSE '[]'::jsonb "
            "  END) e WHERE jsonb_typeof(e) = 'object'",
            cx.lo, cx.hi, list(AGENTS), CITATION_ROWS))
        read["%s.%s" % (table, col)] = why or len(rows)
        for r in rows or []:
            if r["kind"] and r["id"]:
                refs[r["agent"]].append((r["kind"].strip(), r["id"].strip()))
    return refs, read


async def _resolve(cx: _Ctx, pairs: set) -> tuple:
    """({(kind, id): True/False} for verifiable kinds, set(unverifiable
    kinds), why). Migration 221's improve_ref_target decides kind ->
    (table, key); a missing table resolves False (the citation is refused,
    never assumed), a non-text / non-integer key is unverifiable."""
    if not await cx.conn.fetchval(
            "SELECT to_regprocedure('improve_ref_target(text)') IS NOT "
            "NULL"):
        return {}, {k for k, _ in pairs}, "MIGRATION_221_NOT_APPLIED"
    kinds = sorted({k for k, _ in pairs})
    rows = await cx.conn.fetch(
        "SELECT k, (improve_ref_target(k)).tbl AS tbl, "
        "       (improve_ref_target(k)).col AS col "
        "  FROM unnest($1::text[]) k", kinds)
    target = {r["k"]: (r["tbl"], r["col"]) for r in rows if r["tbl"]}
    out, unverifiable = {}, set()
    for kind in kinds:
        ids = sorted({i for k, i in pairs if k == kind})
        if kind not in target:
            unverifiable.add(kind)
            continue
        tbl, col = target[kind]
        if not (_IDENT.match(tbl) and _IDENT.match(col)):
            unverifiable.add(kind)
            continue
        if await cx.has(tbl):
            out.update({(kind, i): False for i in ids})
            continue
        typ = await cx.conn.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE "
            " table_schema = current_schema() AND table_name = $1 AND "
            " column_name = $2", tbl, col)
        if typ in ("text", "character varying"):
            cast, vals = "text", ids
        elif typ in ("bigint", "integer"):
            cast = "bigint"
            vals = [int(i) for i in ids if i.isdigit() and len(i) < 19]
            out.update({(kind, i): False for i in ids
                        if not (i.isdigit() and len(i) < 19)})
        else:
            unverifiable.add(kind)
            continue
        hit = set()
        for n in range(0, len(vals), ID_CHUNK):
            got, why = await cx.read((tbl,), lambda chunk=vals[
                    n:n + ID_CHUNK]: cx.conn.fetch(
                "SELECT %s::text AS id FROM %s WHERE %s = ANY($1::%s[])"
                % (col, tbl, col, cast), chunk))
            if why:
                unverifiable.add(kind)
                break
            hit.update(r["id"] for r in got)
        if kind in unverifiable:
            continue
        out.update({(kind, str(v)): str(v) in hit for v in vals})
    return out, unverifiable, None


async def citations(cx: _Ctx) -> dict:
    refs, read = await _citation_refs(cx)
    pairs = {p for v in refs.values() for p in v}
    resolved, unverifiable, why = ((await _resolve(cx, pairs)) if pairs
                                   else ({}, set(), None))
    src = sorted({t for t, *_ in CITATION_SOURCES})
    out = {}
    for agent in AGENTS:
        mine = refs[agent]
        checked = [p for p in mine if p in resolved]
        good = sum(1 for p in checked if resolved[p])
        bad = [p for p in checked if not resolved[p]]
        unv: dict = {}
        for k, _i in mine:
            if k in unverifiable:
                unv[k] = unv.get(k, 0) + 1
        out[agent] = metric(
            agent, "citation_correctness", win=cx.win,
            value=rate(good, len(checked)), n=len(checked), unit="share",
            direction=HIGHER, source=src,
            definition=("cited evidence references (the newest %d citing "
                        "rows per source) that resolve to an existing "
                        "record through migration 221's fixed kind -> "
                        "(table, key) map / references of a mapped kind"
                        % CITATION_ROWS),
            reason=None if checked else (why or (
                "ONLY_UNVERIFIABLE_KINDS_CITED" if mine else
                "NO_CITATION_IN_WINDOW")),
            detail={"references": len(mine),
                    "unresolved_examples": [{"kind": k, "id": i}
                                            for k, i in bad[:3]],
                    "unresolved": len(bad),
                    "unverifiable_kinds": dict(sorted(
                        unv.items(), key=lambda kv: -kv[1])[:5]),
                    "rows_read": read})
    return out


async def queue(cx: _Ctx) -> tuple:
    """({agent: unresolved_blocker_age}, {agent: {kind: latency row},
    "_why": reason}) from the 234 queue."""
    c = cx.conn
    why = await cx.has("agent_work_requests", "agent_work_open",
                       "agent_work_request_events")
    if not why and not await cx.col("agent_work_requests", "due_at"):
        why = "MIGRATION_234_NOT_APPLIED"
    win = {"start": None, "end": cx.now, "seconds": None,
           "basis": "OPEN_QUEUE_ITEMS_AT_READ"}
    if why:
        cards = {a: metric(a, "unresolved_blocker_age", win=win,
                           definition="the agent's durable queue",
                           source=["agent_work_requests"], reason=why,
                           direction=LOWER) for a in AGENTS}
        return cards, {"_why": why}
    rows, rwhy = await cx.read(("agent_work_requests",), lambda: c.fetch(
        WS.OPEN_ITEMS_SQL, None, None, WS.MAX_QUEUE_ITEMS))
    items = [WS.queue_item(dict(r), cx.now) for r in rows or []]
    since: dict = {}
    first, _w = await cx.read(("agent_work_request_events",), lambda: c.fetch(
        "SELECT r.request_id, CASE WHEN r.blocker IS NOT NULL THEN "
        "  extract(epoch FROM r.enqueued_at) ELSE (SELECT extract(epoch "
        "  FROM min(e.at)) FROM agent_work_request_events e WHERE "
        "  e.request_id = r.request_id AND e.state = 'ATTEMPTED' AND "
        "  e.outcome = 'BLOCKED') END::float8 AS since FROM "
        "  agent_work_requests r WHERE r.request_id = ANY($1::text[])",
        [it["request_id"] for it in items if it.get("blocker")]))
    for r in first or []:
        if r["since"] is not None:
            since[r["request_id"]] = float(r["since"])
    limited = len(rows or []) >= WS.MAX_QUEUE_ITEMS
    cards = {}
    for a in AGENTS:
        if rwhy:
            cards[a] = metric(a, "unresolved_blocker_age", win=win,
                              definition="the agent's durable queue",
                              source=["agent_work_requests"], reason=rwhy,
                              direction=LOWER)
        else:
            cards[a] = blocker_card(a, [it for it in items
                                        if it.get("owner") == a],
                                    cx.now, since)
            cards[a]["detail"].update({
                "queue_read_limit": WS.MAX_QUEUE_ITEMS,
                "queue_read_limited": limited})
    lat, lwhy = await cx.read(("agent_work_request_events",), lambda: c.fetch(
        "SELECT r.agent_id, r.kind, " + _PCT + " FROM (SELECT r.agent_id, "
        "  r.kind, extract(epoch FROM e.at - r.enqueued_at) AS s FROM "
        "  agent_work_requests r JOIN agent_work_request_events e ON "
        "  e.request_id = r.request_id AND e.state = 'COMPLETED' WHERE "
        + _IN_WINDOW % {"c": "e.at"} + ") r WHERE s >= 0 GROUP BY "
        "  r.agent_id, r.kind", cx.lo, cx.hi))
    latency: dict = {"_why": lwhy}
    for r in lat or []:
        latency.setdefault(r["agent_id"], {})[r["kind"]] = dict(r)
    return cards, latency


async def twin(cx: _Ctx) -> tuple:
    why = await cx.has("twin_agent_scorecards")
    if why:
        return {}, why

    async def q():
        return await cx.conn.fetch(
            "SELECT agent, metric, book, value, sample_n, status, reason, "
            "  basis, unit, run_id, computed_at FROM twin_agent_scorecards "
            " WHERE run_id = (SELECT run_id FROM twin_agent_scorecards "
            "                 ORDER BY computed_at DESC LIMIT 1)")
    rows, why = await cx.read(("twin_agent_scorecards",), q)
    if why:
        return {}, why
    out = {}
    for r in rows or []:
        k = (r["agent"], r["metric"])
        # one row per (agent, metric): PAPER before COUNTERFACTUAL / ACTUAL
        if k not in out or r["book"] == "PAPER":
            out[k] = dict(r)
    return out, None if out else "TWIN_SCORECARD_NOT_COMPUTED"


async def memory(cx: _Ctx) -> dict:
    """MEMORY USEFULNESS (section 19), per agent: lessons retrieved for the
    window's decisions and what the evaluator concluded. A summary of
    lesson_usage's records -- not a score, and it grants nothing."""
    c = cx.conn
    why = await cx.has("agent_lesson_retrievals",
                       "agent_lesson_supersessions")
    if why:
        return {a: {"status": UNAVAILABLE,
                    "reason": "MIGRATION_234_NOT_APPLIED"} for a in AGENTS}
    ret, rwhy = await cx.read(("agent_lesson_retrievals",), lambda: c.fetch(
        "SELECT agent_id, count(DISTINCT decision_id) AS decisions, "
        "  count(DISTINCT lesson_id) AS lessons FROM agent_lesson_retrievals"
        " WHERE " + _IN_WINDOW % {"c": "decided_at"} +
        " GROUP BY agent_id", cx.lo, cx.hi))
    sup, swhy = await cx.read(("agent_lesson_supersessions",),
                              lambda: c.fetch(
        "SELECT DISTINCT ON (agent_id, lesson_id) agent_id, lesson_id, "
        "  action, weight, evidence, decided_at FROM "
        "  agent_lesson_supersessions WHERE "
        + _IN_WINDOW % {"c": "decided_at"} +
        " ORDER BY agent_id, lesson_id, decided_at DESC", cx.lo, cx.hi))
    r_by = {r["agent_id"]: r for r in ret or []}
    s_by: dict = {}
    for r in sup or []:
        ev = _j(r["evidence"]) or {}
        s_by.setdefault(r["agent_id"], []).append({
            "lesson_id": r["lesson_id"], "action": r["action"],
            "weight": _f(r["weight"]), "decided_at": _ep(r["decided_at"]),
            "n_used": ev.get("n_used"), "n_comparable": ev.get("n_comparable"),
            "mean_difference_usd": ev.get("mean_difference_usd"),
            "design": ev.get("design"), "status": ev.get("status")})
    out = {}
    for a in AGENTS:
        r = r_by.get(a)
        if rwhy or swhy:
            out[a] = {"status": UNAVAILABLE, "reason": rwhy or swhy}
            continue
        if not r and not s_by.get(a):
            out[a] = {"status": UNAVAILABLE,
                      "reason": "NO_LESSON_RETRIEVED_IN_WINDOW"}
            continue
        out[a] = {"status": MEASURED, "reason": None,
                  "decisions_with_lessons": int(r["decisions"]) if r else 0,
                  "lessons_retrieved": int(r["lessons"]) if r else 0,
                  "evaluations": s_by.get(a, []),
                  "authority": "NONE: a lesson informs, it never permits"}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE CARDS
# ═════════════════════════════════════════════════════════════════════

_NO_PROB = "NO_PROBABILISTIC_CLAIM_IN_THIS_ROLE"
_NO_PRICE = ("NO_PRICE_OR_PROBABILITY_EVIDENCE_ACTED_ON_IN_THIS_ROLE")


async def scorecards(conn, *, now: float | None = None,
                     window_days: int = DEFAULT_WINDOW_DAYS,
                     agents=None) -> dict:
    """Every card (or the named agents'), each with its ten metrics in
    METRICS order and the memory-usefulness summary. Reads only."""
    now = float(now if now is not None else time.time())
    want = tuple(agents or AGENTS)
    for a in want:
        if a not in AGENTS:
            raise ValueError("UNKNOWN_AGENT:%s" % a)
    cx = _Ctx(conn, now, window_seconds(window_days))
    w = cx.win
    built: dict = {a: {} for a in AGENTS}
    if "DEREK" in want:
        built["DEREK"].update(await derek(cx))
    if "XAVIER" in want:
        built["XAVIER"].update(await xavier(cx))
    if "KAREN" in want:
        built["KAREN"].update(await karen(cx))
    peer = await peer_review(cx) if set(want) & (
        set(CHALLENGE_TARGETS) | set(EVALUATORS)) else {}
    if "AUDREY" in want:
        built["AUDREY"].update(await audrey(cx, peer))
    if "CHIEF_ALLOCATOR" in want:
        built["CHIEF_ALLOCATOR"].update(await allocator(cx))
    if "EDDIE" in want:
        built["EDDIE"].update(await eddie(cx))
    blockers, qlat = await queue(cx)
    if "SCOUT" in want:
        built["SCOUT"].update(await scout(cx, qlat))
    cites = await citations(cx)
    twin_rows, twin_why = await twin(cx)
    mem = await memory(cx)
    for a in AGENTS:
        b = built[a]
        for m, card in (peer.get(a) or {}).items():
            if m in METRICS:
                b.setdefault(m, card)
        b["citation_correctness"] = cites[a]
        b["unresolved_blocker_age"] = blockers[a]
        if a in TWIN_VALUE:
            b.setdefault("value_added", twin_card(a, twin_rows,
                                                  why_absent=twin_why))
        if a in ("KAREN", "AUDREY", "CHIEF_ALLOCATOR", "SCOUT"):
            b.setdefault("calibration", na(a, "calibration", w, (
                "SCOUT_STATES_HYPOTHESES_NOT_PROBABILITIES: each is tested "
                "by the evaluator's frozen forward test (false_approval)"
                if a == "SCOUT" else
                "EXPECTED_VALUE_RANKS_NOT_PROBABILITIES"
                if a == "CHIEF_ALLOCATOR" else _NO_PROB)))
        if a in ("KAREN", "AUDREY", "CHIEF_ALLOCATOR", "SCOUT"):
            b.setdefault("freshness_compliance", na(
                a, "freshness_compliance", w, _NO_PRICE))
        if a in ("EDDIE", "SCOUT"):
            b.setdefault("challenge_quality", na(
                a, "challenge_quality", w,
                "NOT_A_CHALLENGE_TARGET: karen.TARGETS"))
        if a == "SCOUT":
            b.setdefault("false_refusal", na(
                a, "false_refusal", w, "SCOUT_REFUSES_NOTHING: it proposes "
                "hypotheses; the evaluator decides"))
    cards = []
    for a in want:
        b = built[a]
        missing = [m for m in METRICS if m not in b]
        if missing:
            raise AssertionError("SCORECARD_INCOMPLETE:%s:%s" % (a, missing))
        cards.append({"agent": a, "metrics": [b[m] for m in METRICS],
                      "memory_usefulness": mem.get(a)})
    return {"schema": SCHEMA, "version": VERSION, "computed_at": now,
            "window": w, "min_n": MIN_N, "metric_order": list(METRICS),
            "statuses": list(STATUSES), "agents": cards,
            "single_score": None,
            "single_score_why": ("NO_VANITY_SCORE: each metric stands "
                                 "alone with its own n and window"),
            "never_scored": ("heartbeats, runs, passes, messages, reviews "
                             "written, memories, tokens"),
            "authority": "NONE_RECORDS_ONLY"}
