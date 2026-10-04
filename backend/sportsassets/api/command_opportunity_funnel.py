"""THE UNIQUE OPPORTUNITY FUNNEL: GET /api/command/opportunity-funnel
(GET only, COMMAND auth via agents_core.require_read). READ ONLY.

    ?sleeve=INVESTMENT|TRAINING|BENCHMARK|UNCLASSIFIED   (default INVESTMENT)
    &strategy=<one strategy of that sleeve>               (default: all)
    &hours=1..168                                         (default 24)

Blockers ranked by UNIQUE OPPORTUNITIES -- key (fixture, us_market_slug,
holding_side, label.line, label.period) -- with every repeated decision row
counted as a re-evaluation, never as another opportunity, and attributed
per (opportunity, strategy): one strategy's ENTER never hides another's
refusal, and the binding blocker is never "whichever strategy evaluated
last"; near misses and missed executable EV at the strategy's own executable
book freshness (profitability/opportunity_funnel.py has the rule).

READS (SELECT only, one READ ONLY transaction under a statement timeout):
  * paper_decisions of the paper account in the window, for the sleeve's
    strategies (newest first, bounded by MAX_ROWS; `truncated` says so);
  * paper_book_observations: each evaluated decision's own readable book,
    else the latest error-free observation of its market at or before the
    decision no older than the strategy's executable bound -- never older;
    at most EV_PER_OPPORTUNITY evaluations per (opportunity, strategy) and
    MAX_EV_EVALUATIONS in all are priced -- first the LATEST fresh-
    probability evaluation of every unit, then the rest up to the per-unit
    bound. Every fresh-probability evaluation left unpriced carries the
    explicit NOT_EVALUATED marker (opportunity_funnel.not_evaluated): it
    still counts toward the probability stages, and an EV computed on a
    partial set is flagged PARTIAL, never reported as the plain best.

The sleeve of a decision is its strategy's (migration 223's classifier map;
a strategy outside it is UNCLASSIFIED, never INVESTMENT). INVESTMENT is the
production-confidence scope; every other sleeve is research.

No route here writes, sends an order or changes a threshold, limit, control
or capital. An unreadable source is UNAVAILABLE with its reason.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/opportunity-funnel"
STATEMENT_TIMEOUT_MS = 10000
CACHE_S = 30.0
MAX_ROWS = 50000
EV_PER_OPPORTUNITY = 5
MAX_EV_EVALUATIONS = 4000
_CACHE: dict = {}

DECISIONS_SQL = (
    "SELECT d.decision_id, d.strategy, d.policy_version, d.verdict, "
    "       d.refusal, d.refusals, d.us_market_slug, d.holding_side, "
    "       d.fixture, d.label->>'line' AS line, "
    "       d.label->>'period' AS scope, "
    "       d.p_blended, d.p_pinnacle, d.p_internal, d.book_obs_id, "
    "       d.pinnacle->>'qualified' AS pin_qualified, "
    "       d.pinnacle->>'age_s' AS pin_age_s, "
    "       d.pinnacle->>'limit_s' AS pin_limit_s, "
    "       d.pinnacle->>'refusal' AS pin_refusal, "
    "       d.pinnacle->>'qualification' AS pin_qualification, "
    "       CASE WHEN jsonb_typeof(d.book->'age_at_decision_s') = 'number' "
    "            THEN (d.book->>'age_at_decision_s')::float8 END "
    "            AS decision_book_age_s, "
    "       extract(epoch FROM d.decided_at)::float8 AS decided_at "
    "  FROM paper_decisions d "
    " WHERE d.account_id = $1 "
    "   AND d.decided_at >= to_timestamp($2) "
    "   AND d.decided_at <= to_timestamp($3) "
    "   AND ((NOT $5::boolean AND d.strategy = ANY($4::text[])) "
    "        OR ($5::boolean AND NOT (d.strategy = ANY($4::text[])))) "
    " ORDER BY d.decided_at DESC LIMIT $6")


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _row(r) -> dict:
    d = dict(r)
    d["pinnacle"] = {"qualified": d.pop("pin_qualified") == "true",
                     "age_s": _f(d.pop("pin_age_s")),
                     "limit_s": _f(d.pop("pin_limit_s")),
                     "refusal": d.pop("pin_refusal"),
                     "qualification": d.pop("pin_qualification")}
    d["refusals"] = list(d.get("refusals") or [])
    return d


def choose_for_ev(rows: list, *, per_key: int = EV_PER_OPPORTUNITY,
                  cap: int = MAX_EV_EVALUATIONS) -> set:
    """The decision ids whose executable EV is priced, per (opportunity,
    strategy) unit: FIRST the latest fresh-probability evaluation of every
    unit (newest units first), THEN the unit's next latest up to `per_key`;
    at most `cap` in all. Pure."""
    from ..profitability import opportunity_funnel as FN
    by: dict = {}
    for r in sorted(rows, key=lambda r: -(r.get("decided_at") or 0.0)):
        k = FN.opportunity_key(r)
        if k is None or not FN.probability_fresh(r)["fresh"]:
            continue
        lst = by.setdefault((k, r.get("strategy")), [])
        if len(lst) < per_key:
            lst.append(r["decision_id"])
    out: set = set()
    for depth in range(per_key):
        for lst in by.values():
            if len(out) >= cap:
                return out
            if depth < len(lst):
                out.add(lst[depth])
    return out


async def _books(conn, rows: list) -> dict:
    """{decision_id: {obs_id, observed_at, bids, offers}}: the decision's
    own recorded readable book, else (none recorded, or its own read was
    unreadable) the latest error-free observation of its market at or before
    the decision within the strategy's executable bound."""
    from ..profitability import capacity as CP
    out: dict = {}
    oids = sorted({int(r["book_obs_id"]) for r in rows
                   if r.get("book_obs_id") is not None})
    own: dict = {}
    if oids:
        for b in await conn.fetch(
                "SELECT obs_id, bids, offers, error, "
                "       extract(epoch FROM observed_at)::float8 AS t "
                "  FROM paper_book_observations "
                " WHERE obs_id = ANY($1::bigint[])", oids):
            if b["error"] is None:
                own[int(b["obs_id"])] = {
                    "obs_id": int(b["obs_id"]), "observed_at": b["t"],
                    "bids": b["bids"], "offers": b["offers"]}
    rest = []
    for r in rows:
        b = (own.get(int(r["book_obs_id"]))
             if r.get("book_obs_id") is not None else None)
        if b:
            out[r["decision_id"]] = b          # its own readable book stands
        elif r.get("us_market_slug") and r.get("decided_at") is not None:
            # no own book, or its own read was unreadable: the latest
            # readable observation inside the strategy's bound, as the
            # capacity model reads it (reads.capacity_books) -- never older
            rest.append(r)
    if rest:
        for b in await conn.fetch(
                "SELECT c.id, b.obs_id, b.bids, b.offers, "
                "       extract(epoch FROM b.observed_at)::float8 AS t "
                "  FROM unnest($1::text[], $2::text[], $3::float8[], "
                "              $4::float8[]) AS c(id, slug, at, bound) "
                "  JOIN LATERAL (SELECT obs_id, bids, offers, observed_at "
                "                  FROM paper_book_observations "
                "                 WHERE us_market_slug = c.slug "
                "                   AND error IS NULL "
                "                   AND observed_at <= to_timestamp(c.at) "
                "                   AND observed_at >= to_timestamp(c.at - "
                "                                              c.bound) "
                "                 ORDER BY observed_at DESC LIMIT 1) b "
                "    ON true",
                [r["decision_id"] for r in rest],
                [r["us_market_slug"] for r in rest],
                [float(r["decided_at"]) for r in rest],
                [CP.executable_bound(r.get("strategy")) for r in rest]):
            out[b["id"]] = {"obs_id": int(b["obs_id"]), "observed_at": b["t"],
                            "bids": b["bids"], "offers": b["offers"]}
    return out


async def _read(conn, *, sleeve: str, strategy, hours: float,
                now: float) -> dict:
    from ..profitability import common as C
    from ..profitability import opportunity_funnel as FN
    from ..profitability.runner import fee_fn_for
    known = sorted(C.STRATEGY_SLEEVE)
    if sleeve == C.UNCLASSIFIED:
        names, negate = known, True
    else:
        names = [s for s in known if C.STRATEGY_SLEEVE[s] == sleeve]
        negate = False
    if strategy is not None:
        if C.strategy_sleeve(strategy) != sleeve:
            return {"status": "UNAVAILABLE", "data": None,
                    "why": "STRATEGY_%s_IS_NOT_IN_THE_%s_SLEEVE"
                           % (strategy, sleeve)}
        names, negate = [strategy], False
    start = now - hours * 3600.0
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        if not await conn.fetchval(
                "SELECT to_regclass('paper_decisions') IS NOT NULL"):
            return {"status": "UNAVAILABLE", "data": None,
                    "why": "PAPER_DECISIONS_NOT_PRESENT"}
        rows = [_row(r) for r in await conn.fetch(
            DECISIONS_SQL, C.PAPER_ACCOUNT, start, now, names, negate,
            MAX_ROWS)]
        chosen = choose_for_ev(rows)
        books = await _books(conn, [r for r in rows
                                    if r["decision_id"] in chosen])
    finally:
        await tr.rollback()
    fees: dict = {}

    def fee_for(t):
        day = None if t is None else int(float(t) // 86400)
        if day not in fees:
            fees[day] = fee_fn_for(t)
        return fees[day]
    for r in rows:
        if r["decision_id"] in chosen:
            r["ev"] = FN.evaluate(r, book=books.get(r["decision_id"]),
                                  fee_fn=fee_for(r.get("decided_at")))
        elif not FN.probability_fresh(r)["fresh"]:
            # a stale, unqualified or absent probability is never priced:
            # its reason
            r["ev"] = FN.evaluate(r, book=None, fee_fn=None)
        else:
            # a FRESH probability the bound left unpriced: said so
            r["ev"] = FN.not_evaluated(r)
    overall = FN.compute(rows, sleeve=sleeve, strategy=strategy)
    per = {}
    for s in sorted({r["strategy"] for r in rows if r.get("strategy")}):
        per[s] = FN.compute([r for r in rows if r.get("strategy") == s],
                            sleeve=sleeve, strategy=s)
    return {"status": "OK", "why": None, "data": {
        "version": FN.VERSION,
        "window": {"hours": hours, "start": start, "end": now,
                   "rows_read": len(rows), "max_rows": MAX_ROWS,
                   "truncated": len(rows) >= MAX_ROWS,
                   "truncated_means": ("the window holds more decisions than "
                                       "MAX_ROWS: the OLDEST were not read; "
                                       "narrow `hours`")},
        "strategies_in_scope": names if not negate else
        "every strategy outside the classifier's map",
        "overall": overall, "by_strategy": per,
        "ev_bound": {"per_opportunity_strategy": EV_PER_OPPORTUNITY,
                     "max_evaluations": MAX_EV_EVALUATIONS,
                     "priced": len(chosen),
                     "fresh_probability_not_priced": sum(
                         1 for r in rows if (r.get("ev") or {}).get(
                             "not_evaluated")),
                     "rule": ("the latest fresh-probability evaluation of "
                              "every (opportunity, strategy) unit is priced "
                              "first, then up to the per-unit bound; the "
                              "rest are NOT_EVALUATED (counted, never "
                              "priced, never a zero) and an EV over a "
                              "partial set is flagged PARTIAL")},
        "freshness_rule": ("executable EV only on the decision's own book at "
                           "its recorded age, or the latest observation at "
                           "or before the decision no older than the "
                           "strategy's executable bound (profitability."
                           "capacity.EXECUTABLE_BOOK_MAX_AGE_BY_STRATEGY = "
                           "the entry rule's own bound), with a Pinnacle "
                           "probability qualified (lane and provenance) and "
                           "within its 30 s rule at the decision"),
        "attribution": ("per (opportunity, strategy); `overall` counts an "
                        "opportunity once per blocker that binds any of its "
                        "strategies")}}


@router.get(PATH, dependencies=[Depends(require_read)])
async def opportunity_funnel(
        sleeve: str = Query(default="INVESTMENT",
                            pattern="^(INVESTMENT|TRAINING|BENCHMARK|"
                                    "UNCLASSIFIED)$"),
        strategy: str | None = Query(default=None,
                                     pattern="^[A-Z0-9_]{1,80}$"),
        hours: float = Query(default=24.0, ge=1.0, le=168.0)) -> dict:
    from ..profitability import common as C
    now = time.time()
    key = (sleeve, strategy, round(float(hours), 3))
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, sleeve=sleeve, strategy=strategy,
                              hours=float(hours), now=now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None, sleeve=sleeve, strategy=strategy)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), sleeve=sleeve, strategy=strategy,
                     confidence_scope=C.confidence_scope(sleeve),
                     summed_across_sleeves=False)
    _CACHE[key] = (now, out)
    return out
