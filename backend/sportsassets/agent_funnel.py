"""THE PER-AGENT FUNNEL RECEIPT (P0 incident: coverage -> trade starvation).

THE OWNER'S QUESTION (2026-10-04): "I specifically want to know whether we
have hundreds of valid candidates upstream and only a few are reaching
Derek/Eddie/Xavier." Every report answered it differently, and an agent's
refusals were read as "the agent said no" whether the agent had judged the
economics or the software had failed to give it inputs it could judge.

ONE RECEIPT PER AGENT STRATEGY, over one window, from the records the real
writers already keep -- nothing new is written:

  RECEIVED            distinct valuations the strategy was handed (its
                      decisions and its recorded evaluation attempts)
  NOT_DECIDED         received, never decided (timeout, error, deferral),
                      by outcome -- a software failure, counted in
                      REJECTED_SOFTWARE
  REJECTED_SOFTWARE   decided REFUSE with at least one SOFTWARE code (its
                      economics were never judged on inputs the software
                      could vouch for), plus NOT_DECIDED
  ELIGIBLE            decided on validated inputs: REJECTED_ECONOMIC + ENTER
  REJECTED_ECONOMIC   decided REFUSE and every code ECONOMIC (edge, EV,
                      price, depth, a risk rail)
  REJECTED_UNCLASSIFIED  a code the taxonomy does not know -- by name, never
                      counted as economic
  ENTER               decided ENTER
  ORDER               PAPER orders created for those ENTERs
  ORDER_REFUSED_BY_RISK  ENTERs whose order the paper risk check refused
                      (`submit_order` writes no order row, only the
                      PAPER_RISK_REFUSED_THE_ORDER finding naming its own
                      refusal) -- each classified by the code it wraps: a
                      cap is ECONOMIC, a malformed order SOFTWARE
  ORDER_PENDING       ENTERs younger than the backstop's threshold with no
                      order and no refusal yet: the order is in flight
  ENTER_WITHOUT_ORDER ENTERs past that threshold with neither an order nor
                      a refusal -- exactly the ENTERs the backstop
                      (`paper_derek.step_enter_backstop`) names
  FILL                PAPER orders with at least one simulated fill

Every code is classified by the ONE taxonomy (`refusal_taxonomy`): SOFTWARE
or ECONOMIC with its family and funnel stage, unknown codes UNCLASSIFIED by
name. The receipt also carries the stage before any agent -- the collector's
per-event outcomes (`ext_candidate_outcomes`) classified the same way -- and
the valuations written per sport and side, so "hundreds upstream, few
reaching the agents" is a number with its causes, not an impression.

"Orders" and "fills" are the PAPER account's, always. Read-only: every
statement is a SELECT, bounded by the window and LIMITs; the caller runs it
inside a READ ONLY transaction with a statement timeout. Imports only the
taxonomy (pure); no paper, execution, funded or venue module.
"""
from __future__ import annotations

from . import refusal_taxonomy as RT

VERSION = "AGENT_FUNNEL_RECEIPT_V1"
DEFAULT_WINDOW_S = 86400.0
MAX_WINDOW_S = 7 * 86400.0
MAX_GROUPS = 5000
MAX_CODES = 60

STAGES = ("RECEIVED", "NOT_DECIDED", "REJECTED_SOFTWARE", "ELIGIBLE",
          "REJECTED_ECONOMIC", "REJECTED_UNCLASSIFIED", "ENTER", "ORDER",
          "ORDER_REFUSED_BY_RISK", "ORDER_PENDING", "ENTER_WITHOUT_ORDER",
          "FILL")

#: THE BACKSTOP'S OWN DEFINITIONS (paper_derek.ENTER_WITHOUT_ORDER_AFTER_S /
#: R_ORDER_REFUSED), spelled here so this module imports no paper module;
#: pinned equal by tests/test_agent_funnel_receipt.py. REVIEW OF 7bd084b:
#: every ENTER with no order row was counted ENTER_WITHOUT_ORDER (SOFTWARE /
#: INTEGRITY), including risk-refused orders and orders still in flight,
#: which the backstop -- and this receipt's own docstring -- exclude.
ENTER_WITHOUT_ORDER_AFTER_S = 60.0
R_ORDER_REFUSED = "PAPER_RISK_REFUSED_THE_ORDER"

#: The evaluation-attempt outcomes that are NOT a decision (paper_evaluation_
#: attempts.outcome), each a software failure by the taxonomy.
NOT_DECIDED_OUTCOMES = ("TIMEOUT", "ERROR", "DEFERRED_FOR_BOOK_RETRY",
                        "DEFERRED_BOOK_BUDGET", "RETRY_SCHEDULED",
                        "RETRY_NOT_SCHEDULED")
_OUTCOME_CODE = {"TIMEOUT": "TIMEOUT", "ERROR": "ERROR",
                 "DEFERRED_FOR_BOOK_RETRY": "BOOK_RETRY",
                 "DEFERRED_BOOK_BUDGET": "BOOK_READ_BUDGET",
                 "RETRY_SCHEDULED": "BOOK_RETRY",
                 "RETRY_NOT_SCHEDULED": "BOOK_RETRY"}

_ACCT = "($3::text IS NULL OR %s.account_id = $3)"

DECISIONS_SQL = (
    "SELECT d.strategy, coalesce(ev.sport_family, 'UNKNOWN') AS sport, "
    "       coalesce(ev.payout_is_complement, false) AS complement, "
    "       d.verdict, d.refusals, count(*) AS n "
    "  FROM paper_decisions d "
    "  LEFT JOIN external_valuations ev ON ev.id = d.valuation_id "
    " WHERE d.decided_at >= to_timestamp($1) AND d.decided_at < to_timestamp($2) "
    "   AND " + _ACCT % "d" + " "
    " GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4 LIMIT %d" % MAX_GROUPS)

RECEIVED_SQL = (
    "SELECT u.strategy, count(DISTINCT u.valuation_id) AS n FROM ("
    "  SELECT d.strategy, d.valuation_id FROM paper_decisions d "
    "   WHERE d.decided_at >= to_timestamp($1) "
    "     AND d.decided_at < to_timestamp($2) "
    "     AND d.valuation_id IS NOT NULL AND " + _ACCT % "d" + " "
    "  UNION "
    "  SELECT a.strategy, a.valuation_id FROM paper_evaluation_attempts a "
    "   WHERE a.at >= to_timestamp($1) AND a.at < to_timestamp($2) "
    "     AND a.valuation_id IS NOT NULL AND " + _ACCT % "a" + ") u "
    " GROUP BY 1 ORDER BY 1")

NOT_DECIDED_SQL = (
    "SELECT a.strategy, a.outcome, count(DISTINCT a.valuation_id) AS n "
    "  FROM paper_evaluation_attempts a "
    " WHERE a.at >= to_timestamp($1) AND a.at < to_timestamp($2) "
    "   AND a.valuation_id IS NOT NULL AND " + _ACCT % "a" + " "
    "   AND a.outcome = ANY($4::text[]) "
    "   AND NOT EXISTS (SELECT 1 FROM paper_decisions d "
    "                    WHERE d.valuation_id = a.valuation_id "
    "                      AND d.strategy = a.strategy) "
    " GROUP BY 1, 2 ORDER BY 1, 2")

#: $4 the risk-refusal finding kind, $5 the backstop threshold (s). An ENTER
#: is PENDING while younger than the threshold at the receipt's instant (the
#: window's end, never later than now()).
_RISK_REFUSED = (
    "EXISTS (SELECT 1 FROM paper_audrey_findings f "
    "         WHERE f.account_id = d.account_id AND f.kind = $4 "
    "           AND f.subject = d.decision_id)")
_PAST_THRESHOLD = (
    "d.decided_at <= least(to_timestamp($2), now()) "
    "                - make_interval(secs => $5::float8)")

ORDERS_SQL = (
    "SELECT d.strategy, "
    "       count(DISTINCT d.decision_id) AS entered, "
    "       count(DISTINCT o.order_id) AS orders, "
    "       count(DISTINCT d.decision_id) FILTER (WHERE o.order_id IS NULL "
    "         AND " + _RISK_REFUSED + ") AS order_refused, "
    "       count(DISTINCT d.decision_id) FILTER (WHERE o.order_id IS NULL "
    "         AND NOT " + _RISK_REFUSED + " AND NOT " + _PAST_THRESHOLD + ")"
    "         AS order_pending, "
    "       count(DISTINCT d.decision_id) FILTER (WHERE o.order_id IS NULL "
    "         AND NOT " + _RISK_REFUSED + " AND " + _PAST_THRESHOLD + ")"
    "         AS enter_without_order, "
    "       count(DISTINCT o.order_id) FILTER (WHERE EXISTS ("
    "         SELECT 1 FROM paper_fills f WHERE f.order_id = o.order_id)) "
    "         AS filled_orders "
    "  FROM paper_decisions d "
    "  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id "
    " WHERE d.verdict = 'ENTER' "
    "   AND d.decided_at >= to_timestamp($1) AND d.decided_at < to_timestamp($2) "
    "   AND " + _ACCT % "d" + " "
    " GROUP BY 1 ORDER BY 1")

#: The code each risk-refusal finding wraps (detail.refusal), per strategy,
#: for ENTERs with no order: classified by the code it wraps.
ORDER_REFUSALS_SQL = (
    "SELECT d.strategy, coalesce(f.detail->>'refusal', '') AS refusal, "
    "       count(DISTINCT d.decision_id) AS n "
    "  FROM paper_decisions d "
    "  JOIN paper_audrey_findings f ON f.account_id = d.account_id "
    "   AND f.kind = $4 AND f.subject = d.decision_id "
    " WHERE d.verdict = 'ENTER' "
    "   AND d.decided_at >= to_timestamp($1) AND d.decided_at < to_timestamp($2) "
    "   AND " + _ACCT % "d" + " "
    "   AND NOT EXISTS (SELECT 1 FROM paper_orders o "
    "                    WHERE o.decision_id = d.decision_id) "
    " GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT %d" % MAX_GROUPS)

ORDER_OUTCOMES_SQL = (
    "SELECT d.strategy, o.state, coalesce(o.terminal_reason, '') AS reason, "
    "       count(*) AS n "
    "  FROM paper_decisions d JOIN paper_orders o "
    "    ON o.decision_id = d.decision_id "
    " WHERE d.verdict = 'ENTER' "
    "   AND d.decided_at >= to_timestamp($1) AND d.decided_at < to_timestamp($2) "
    "   AND " + _ACCT % "d" + " "
    "   AND NOT EXISTS (SELECT 1 FROM paper_fills f "
    "                    WHERE f.order_id = o.order_id) "
    " GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT %d" % MAX_GROUPS)

UPSTREAM_SQL = (
    "SELECT coalesce(family, 'UNKNOWN') AS family, outcome, "
    "       coalesce(first_refusal, '') AS first_refusal, "
    "       count(*) AS n, count(DISTINCT provider_event_id) AS events "
    "  FROM ext_candidate_outcomes "
    " WHERE cycle_at >= to_timestamp($1) AND cycle_at < to_timestamp($2) "
    " GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT %d" % MAX_GROUPS)

VALUATIONS_SQL = (
    "SELECT sport_family, coalesce(payout_is_complement, false) AS complement, "
    "       record_purpose, count(*) AS n, "
    "       count(DISTINCT coalesce(us_market_slug, condition_id)) AS contracts, "
    "       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability "
    "  FROM external_valuations "
    " WHERE decided_at >= to_timestamp($1) AND decided_at < to_timestamp($2) "
    " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")


def window(*, since=None, until=None, now: float) -> tuple:
    """(since, until) epoch seconds: the last DEFAULT_WINDOW_S by default,
    never longer than MAX_WINDOW_S (a bounded read)."""
    u = float(now) if until is None else float(until)
    s = (u - DEFAULT_WINDOW_S) if since is None else float(since)
    s = max(s, u - MAX_WINDOW_S)
    if s >= u:
        s = u - 1.0
    return s, u


def _empty() -> dict:
    return {k: 0 for k in STAGES}


def _bump(d: dict, k, n: int) -> None:
    d[k] = d.get(k, 0) + int(n)


def receipt(*, decisions, received, not_decided, orders, order_outcomes,
            upstream=(), valuations=(), order_refusals=()) -> dict:
    """THE RECEIPT, from the grouped rows the SELECTs return. Pure."""
    agents: dict = {}

    def agent(name) -> dict:
        return agents.setdefault(str(name), {
            "strategy": str(name), "receipt": _empty(),
            "not_decided_by_outcome": {}, "by_sport": {}, "codes": {},
            "binding": {}, "unfilled_order_reasons": {},
            "order_refused_by_risk": [],
            "complement_side": {"decided": 0, "enter": 0}})

    for r in received:
        agent(r["strategy"])["receipt"]["RECEIVED"] = int(r["n"])
    for r in not_decided:
        a = agent(r["strategy"])
        n = int(r["n"])
        _bump(a["receipt"], "NOT_DECIDED", n)
        _bump(a["receipt"], "REJECTED_SOFTWARE", n)
        _bump(a["not_decided_by_outcome"], r["outcome"], n)
        code = _OUTCOME_CODE.get(r["outcome"], r["outcome"])
        _bump(a["codes"], code, n)
        _bump(a["binding"], code, n)
    for r in decisions:
        a = agent(r["strategy"])
        n = int(r["n"])
        refusals = list(r["refusals"] or [])
        cls = RT.decision_class(r["verdict"], refusals)
        rcpt = a["receipt"]
        sp = a["by_sport"].setdefault(str(r["sport"]), _empty())
        if cls == RT.ENTER:
            for d in (rcpt, sp):
                _bump(d, "ENTER", n)
                _bump(d, "ELIGIBLE", n)
        elif cls == RT.REJECTED_ECONOMIC:
            for d in (rcpt, sp):
                _bump(d, "REJECTED_ECONOMIC", n)
                _bump(d, "ELIGIBLE", n)
        elif cls == RT.REJECTED_SOFTWARE:
            for d in (rcpt, sp):
                _bump(d, "REJECTED_SOFTWARE", n)
        else:
            for d in (rcpt, sp):
                _bump(d, "REJECTED_UNCLASSIFIED", n)
        if r.get("complement"):
            _bump(a["complement_side"], "decided", n)
            if cls == RT.ENTER:
                _bump(a["complement_side"], "enter", n)
        for c in refusals:
            k = RT.normalize(c)
            if k:
                _bump(a["codes"], k, n)
        b = RT.binding(refusals)
        if b is not None:
            _bump(a["binding"], b["code"], n)
    for r in orders:
        a = agent(r["strategy"])
        a["receipt"]["ORDER"] = int(r["orders"])
        a["receipt"]["ORDER_REFUSED_BY_RISK"] = int(r.get("order_refused")
                                                    or 0)
        a["receipt"]["ORDER_PENDING"] = int(r.get("order_pending") or 0)
        a["receipt"]["ENTER_WITHOUT_ORDER"] = int(r["enter_without_order"])
        a["receipt"]["FILL"] = int(r["filled_orders"])
    for r in order_refusals:
        # THE WRAPPER CLASSIFIED BY THE CODE IT CARRIES (refusal_taxonomy.
        # classify_wrapped): a cap is economic, a malformed order is ours
        agent(r["strategy"])["order_refused_by_risk"].append(dict(
            RT.classify_wrapped(R_ORDER_REFUSED, r["refusal"] or None),
            n=int(r["n"])))
    for r in order_outcomes:
        a = agent(r["strategy"])
        key = "%s:%s" % (r["state"], r["reason"] or "NO_TERMINAL_REASON")
        _bump(a["unfilled_order_reasons"], key, int(r["n"]))

    for a in agents.values():
        top = sorted(a["codes"].items(), key=lambda kv: (-kv[1], kv[0]))
        a["codes"] = [dict(RT.classify(c), n=n) for c, n in top[:MAX_CODES]]
        a["codes_truncated"] = len(top) > MAX_CODES
        a["binding"] = [dict(RT.classify(c), n=n) for c, n in sorted(
            a["binding"].items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CODES]]
        a["by_class"] = RT.summarize(
            {c["code"]: c["n"] for c in a["codes"]})["by_class"]
        r = a["receipt"]
        decided = (r["REJECTED_SOFTWARE"] - r["NOT_DECIDED"]
                   + r["REJECTED_ECONOMIC"] + r["REJECTED_UNCLASSIFIED"]
                   + r["ENTER"])
        a["checks"] = {
            "decided": decided,
            "eligible_is_economic_plus_enter":
                r["ELIGIBLE"] == r["REJECTED_ECONOMIC"] + r["ENTER"],
            "orders_refusals_pending_and_missing_cover_enter":
                r["ORDER"] + r["ORDER_REFUSED_BY_RISK"] + r["ORDER_PENDING"]
                + r["ENTER_WITHOUT_ORDER"] >= r["ENTER"],
            "fills_within_orders": r["FILL"] <= r["ORDER"]}

    up_by_outcome: dict = {}
    up_codes: dict = {}
    up_family: dict = {}
    for r in upstream:
        n = int(r["n"])
        _bump(up_by_outcome, r["outcome"], n)
        fam = up_family.setdefault(str(r["family"]), {})
        _bump(fam, r["outcome"], n)
        if r["first_refusal"]:
            _bump(up_codes, r["first_refusal"], n)
    up_top = sorted(up_codes.items(), key=lambda kv: (-kv[1], kv[0]))
    vals = [{"sport_family": r["sport_family"],
             "side": "COMPLEMENT" if r["complement"] else "PRICED_OUTCOME",
             "record_purpose": r["record_purpose"], "n": int(r["n"]),
             "contracts": int(r["contracts"]),
             "with_probability": int(r["with_probability"])}
            for r in valuations]
    return {
        "version": VERSION, "taxonomy_version": RT.VERSION,
        "stages": list(STAGES),
        "agents": sorted(agents.values(), key=lambda a: a["strategy"]),
        "router": {
            "what_this_is": ("the collector's per-provider-event outcome "
                             "before any agent receives a valuation "
                             "(ext_candidate_outcomes), each first refusal "
                             "classified by the same taxonomy"),
            "by_outcome": up_by_outcome, "by_family": up_family,
            "first_refusals": [dict(RT.classify(c), n=n)
                               for c, n in up_top[:MAX_CODES]],
            "by_class": RT.summarize(up_codes)["by_class"]},
        "valuations_written": vals,
        "orders_and_fills_are": "PAPER",
        "unclassified_is_never_economic": True}


async def read(conn, *, since: float, until: float,
               account_id: str | None = None) -> dict:
    """Run the SELECTs (the caller holds a READ ONLY transaction with a
    statement timeout) and build the receipt."""
    args = (float(since), float(until), account_id)
    decisions = await conn.fetch(DECISIONS_SQL, *args)
    received = await conn.fetch(RECEIVED_SQL, *args)
    not_decided = await conn.fetch(NOT_DECIDED_SQL, *args,
                                   list(NOT_DECIDED_OUTCOMES))
    orders = await conn.fetch(ORDERS_SQL, *args, R_ORDER_REFUSED,
                              float(ENTER_WITHOUT_ORDER_AFTER_S))
    order_refusals = await conn.fetch(ORDER_REFUSALS_SQL, *args,
                                      R_ORDER_REFUSED)
    order_outcomes = await conn.fetch(ORDER_OUTCOMES_SQL, *args)
    upstream = await conn.fetch(UPSTREAM_SQL, float(since), float(until))
    valuations = await conn.fetch(VALUATIONS_SQL, float(since), float(until))
    out = receipt(decisions=[dict(r) for r in decisions],
                  received=[dict(r) for r in received],
                  not_decided=[dict(r) for r in not_decided],
                  orders=[dict(r) for r in orders],
                  order_outcomes=[dict(r) for r in order_outcomes],
                  upstream=[dict(r) for r in upstream],
                  valuations=[dict(r) for r in valuations],
                  order_refusals=[dict(r) for r in order_refusals])
    out.update(since=float(since), until=float(until),
               account_id=account_id,
               decision_groups_truncated=len(decisions) >= MAX_GROUPS)
    return out
