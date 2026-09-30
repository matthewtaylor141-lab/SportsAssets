"""AUDREY'S DAILY AUDIT OF DEREK (ENTRIES) AND XAVIER (POSITIONS).

WHAT IT IS. Once a LOCAL day (AUDREY_TIMEZONE, default America/New_York,
boundary 00:00 local; both recorded on every report) has completed, Audrey
reads what Derek and Xavier decided that day and what the authoritative
book says happened, and writes ONE versioned report (migration 155). The
15-minute hook (`run_due`) is idempotent: the same day on the same evidence
returns the same report id and writes nothing; the same day on NEW evidence
(a settlement, a correction, an outcome that became known, a late fee)
writes the next VERSION naming the version it supersedes. Nothing is ever
rewritten. A persisted watermark bounds the work and survives a restart.

WHAT IT READS (never writes): bettor_funded_intents / fills / economics
(THE book), bettor_xavier_decisions and their execution events,
external_valuations (Derek's decisions), ext_candidate_outcomes (Derek's
coverage), bettor_funded_settlement_rechecks / corrections, the model
registry, agent_policy_versions, agent_tasks, improvement_candidates. It
calls ONE registry function that only removes authority,
`bettor_funded_model.withdraw_invalidated` -- the existing path by which a
settlement correction retires an approved model whose training records no
longer reproduce.

── A DAILY BOUNDARY SETTLES NOTHING ───────────────────────────────────
Outcome states are kept apart: PENDING, PROVISIONALLY_SETTLED,
VOID_REFUND_PUSH, CORRECTED, FINALIZED (and CONTESTED, when a re-read
disagrees and no correction answers it). The day draws which decisions are
audited; the book decides what they became, as known when computed.

── FOUR EVIDENCE CATEGORIES, NEVER MERGED ─────────────────────────────
  ACTUAL_EXECUTED_PNL                      the book's own cash rows
  KNOWN_SETTLEMENT_HYPOTHETICAL_EXECUTION  an alternative NOT taken, valued
      from its decision-time economics at the outcome now known; a
      contract that settled profitably does not prove an unplaced order at
      that price and size would have filled (could_have_filled UNPROVEN)
  SIMULATED_WITH_DISCLOSED_ASSUMPTIONS     an outcome NOT yet known, valued
      by the decision-time probability with the assumptions stated
  NOT_OBSERVED_OR_UNEVALUABLE              blocked, missing, invalidated --
      counted by reason, never summed as zero
Each category is summed on its own; no figure adds two of them.

── ATTRIBUTION WITHOUT DOUBLE COUNTING ────────────────────────────────
For a position whose outcome is final: the HANDOFF MARK is the value of the
inventory when Xavier took responsibility -- the executable liquidation
value (the DIRECT_EXIT alternative's net cash) recorded on Xavier's FIRST
decision on it, else that decision's HOLD value plus the basis held
(labelled MODEL_EXPECTED_VALUE). ENTRY COST is every book cash row (cost,
fee) effective at or before that instant.
    derek_credit  = handoff_mark + entry_cash           (entry_cash < 0)
    xavier_credit = total_realised - derek_credit
so derek_credit + xavier_credit == total_realised EXACTLY, by construction.
A position without a first Xavier decision or a mark is UNATTRIBUTED, not
split.

── DECISION QUALITY IS JUDGED ON DECISION-TIME EVIDENCE ───────────────
Xavier: the chosen action was the best ranked by its own recorded
decision-time value (GOOD) or not (POOR). Derek: an entry had positive
decision-time edge (GOOD) or not (POOR). The OUTCOME is recorded beside it,
so a good decision that lost and a poor one that won are named as such.

── FIXTURES, NOT ROWS ─────────────────────────────────────────────────
Repeated decisions on one fixture are one example: every statistic gives a
fixture one weight and reports INSUFFICIENT_EVIDENCE below the floor.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from typing import Any

from . import improvement as IMP

VERSION = "AUDREY_AUDIT_V1"
TZ_ENV = "AUDREY_TIMEZONE"
DEFAULT_TZ = "America/New_York"
DAY_BOUNDARY = "00:00"
SCOPE = "audrey_daily_audit"
#: Bounded work per call: days audited forward from the watermark.
MAX_DAYS_PER_RUN = 3
#: Already-audited days re-checked for new evidence, at most this often.
REAUDIT_DAYS = 7
REAUDIT_INTERVAL_S = 3600.0
MAX_DECISIONS_PER_DAY = 5000
MAX_ROWS_IN_REPORT = 200
MIN_FIXTURES = 30
HEARTBEAT_KEY = "ext_pinnacle_last_cycle"

ACTUAL = IMP.ACTUAL
KNOWN_SETTLEMENT = IMP.KNOWN_SETTLEMENT
SIMULATED = IMP.SIMULATED
UNEVALUABLE = IMP.UNEVALUABLE
CATEGORIES = IMP.EVIDENCE_CATEGORIES

# ── OUTCOME STATES ────────────────────────────────────────────────────
S_PENDING = "PENDING"
S_PROVISIONAL = "PROVISIONALLY_SETTLED"
S_VOID = "VOID_REFUND_PUSH"
S_CORRECTED = "CORRECTED"
S_FINAL = "FINALIZED"
S_CONTESTED = "CONTESTED"
OUTCOME_STATES = (S_PENDING, S_PROVISIONAL, S_VOID, S_CORRECTED, S_FINAL,
                  S_CONTESTED)

# ── DECISION QUALITY ──────────────────────────────────────────────────
Q_GOOD_WON = "GOOD_DECISION_WINNING_OUTCOME"
Q_GOOD_LOST = "GOOD_DECISION_LOSING_OUTCOME"
Q_POOR_WON = "POOR_DECISION_WINNING_OUTCOME"
Q_POOR_LOST = "POOR_DECISION_LOSING_OUTCOME"
Q_GOOD_PENDING = "GOOD_DECISION_OUTCOME_NOT_KNOWN"
Q_POOR_PENDING = "POOR_DECISION_OUTCOME_NOT_KNOWN"
Q_UNJUDGEABLE = "NOT_JUDGEABLE_FROM_DECISION_TIME_EVIDENCE"

R_SCHEMA = "THE_AUDREY_AUDIT_TABLES_ARE_NOT_IN_THIS_DATABASE"
R_RAISED = "AUDREY_AUDIT_RAISED"
NO_CHANGE = "NO_CHANGE"

#: WHAT EVERY FROZEN ALTERNATIVE SHOULD CARRY, and where each is read from
#: (first found wins). A missing field is an evidence gap of Xavier's.
FROZEN_FIELDS = {
    "instrument": ("us_market_slug", "hedge_us_market_slug", "hedge_slug",
                   "candidate_slug", "slug"),
    "side": ("order_intent", "hedge_order_intent", "side", "hedge_side"),
    "qty": ("qty", "quantity", "selected_qty", "plan_quantity", "units",
            "hedge_qty"),
    "executable_price": ("executable_price", "limit_price", "price",
                         "avg_price", "price_cents", "limit_price_cents"),
    "depth": ("depth", "displayed_depth", "depth_at_price", "depth_qty",
              "book_depth"),
    "quote_time": ("quote_at", "quote_observed_at", "quote_expires_at",
                   "book_at", "observed_at"),
    "fees": ("fees_usd", "fee_usd"),
}
ACTION_ALTERNATIVES_NEED = ("instrument", "qty", "executable_price",
                            "depth", "quote_time", "fees")


# ═════════════════════════════════════════════════════════════════════
# 0 · CLOCK AND DAY
# ═════════════════════════════════════════════════════════════════════

def audit_timezone() -> tuple:
    """(name, tzinfo, note). An invalid setting falls back to the default
    and the report says so; it never silently uses UTC."""
    from zoneinfo import ZoneInfo                                # noqa: PLC0415
    name = (os.getenv(TZ_ENV) or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        return name, ZoneInfo(name), None
    except Exception:                                           # noqa: BLE001
        return DEFAULT_TZ, ZoneInfo(DEFAULT_TZ), (
            "%s=%r is not a timezone; %s used" % (TZ_ENV, name, DEFAULT_TZ))


def day_bounds(day: _dt.date, tz) -> tuple:
    """[start, end) of the local day as epochs. A DST day is 23 or 25 h."""
    s = _dt.datetime.combine(day, _dt.time(0, 0), tzinfo=tz)
    e = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(0, 0),
                             tzinfo=tz)
    return s.timestamp(), e.timestamp()


def local_day(now: float, tz) -> _dt.date:
    return _dt.datetime.fromtimestamp(float(now), tz).date()


def last_completed_day(now: float, tz) -> _dt.date:
    return local_day(now, tz) - _dt.timedelta(days=1)


def report_id_for(day: _dt.date, tz_name: str) -> str:
    return "audrey:%s:%s" % (tz_name, day.isoformat())


# ═════════════════════════════════════════════════════════════════════
# 1 · PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

_num = IMP._num
_obj = IMP._obj
_ts = IMP._ts
_epoch = IMP._epoch


def _r6(x):
    return None if x is None else round(float(x), 6)


def _first(d: dict, keys):
    for k in keys:
        v = (d or {}).get(k)
        if v is not None and v != "":
            return v, k
    return None, None


def evidence_sha(report: dict) -> str:
    """THE IDENTITY OF THE DAY'S EVIDENCE: the report without anything that
    changes when nothing about the day changed (when it was computed, the
    live state of the task queue)."""
    body = {k: v for k, v in report.items()
            if k not in ("computed_at", "prior_improvements", "run")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                          .encode()).hexdigest()


def fixture_stats(pairs, *, floor: int = MIN_FIXTURES) -> dict:
    """(fixture, value) pairs -> one weight per fixture."""
    by: dict = {}
    for f, v in pairs:
        if v is None:
            continue
        by.setdefault(str(f), []).append(float(v))
    means = [sum(v) / len(v) for v in by.values()]
    n = len(means)
    return {"fixtures": n, "rows": sum(len(v) for v in by.values()),
            "fixture_mean": _r6(sum(means) / n) if n else None,
            "fixture_sum": _r6(sum(means)) if n else None,
            "weighting": "ONE_WEIGHT_PER_FIXTURE",
            "status": ("OK" if n >= floor else "INSUFFICIENT_EVIDENCE")}


def calibration(rows, *, floor: int = MIN_FIXTURES) -> dict:
    """Brier score of decision-time probability against the settled
    outcome, ONE WEIGHT PER FIXTURE, beside the market-implied benchmark
    (the executable price as a probability) on the same rows."""
    by: dict = {}
    for r in rows:
        p, y = _num(r.get("probability")), r.get("outcome")
        if p is None or y is None:
            continue
        m = _num(r.get("price"))
        by.setdefault(str(r["fixture"]), []).append(
            ((p - float(y)) ** 2, None if m is None else (m - float(y)) ** 2))
    n = len(by)
    if not n:
        return {"fixtures": 0, "status": "NO_SETTLED_ROWS",
                "brier": None, "market_brier": None,
                "weighting": "ONE_WEIGHT_PER_FIXTURE"}
    b = sum(sum(x for x, _ in v) / len(v) for v in by.values()) / n
    mk = [[m for _, m in v if m is not None] for v in by.values()]
    mk = [sum(v) / len(v) for v in mk if v]
    return {"fixtures": n, "rows": sum(len(v) for v in by.values()),
            "brier": _r6(b),
            "market_brier": _r6(sum(mk) / len(mk)) if mk else None,
            "market_fixtures": len(mk),
            "shortfall_vs_market": (_r6(b - sum(mk) / len(mk)) if mk
                                    else None),
            "status": "OK" if n >= floor else "INSUFFICIENT_EVIDENCE",
            "weighting": "ONE_WEIGHT_PER_FIXTURE"}


def attribute(*, total: float | None, entry_cash: float | None,
              handoff_mark: float | None) -> dict:
    """THE SPLIT. Derek is credited the handoff mark net of entry cost;
    Xavier everything after it. The two sum to the total exactly."""
    if total is None or entry_cash is None or handoff_mark is None:
        return {"status": "UNATTRIBUTED", "derek_credit_usd": None,
                "xavier_credit_usd": None,
                "why": ("no final total" if total is None else
                        "no entry cost before handoff" if entry_cash is None
                        else "no handoff mark")}
    derek = round(float(handoff_mark) + float(entry_cash), 6)
    xavier = round(float(total) - derek, 6)
    return {"status": "ATTRIBUTED", "derek_credit_usd": derek,
            "xavier_credit_usd": xavier, "total_usd": round(float(total), 6),
            "sums_exactly": round(derek + xavier, 6) == round(float(total),
                                                              6)}


def quality(good: bool | None, outcome_value: float | None) -> str:
    if good is None:
        return Q_UNJUDGEABLE
    if outcome_value is None:
        return Q_GOOD_PENDING if good else Q_POOR_PENDING
    won = outcome_value > 0
    if good:
        return Q_GOOD_WON if won else Q_GOOD_LOST
    return Q_POOR_WON if won else Q_POOR_LOST


def frozen_evidence(alt: dict, decision: dict) -> dict:
    """What an alternative froze at decision time, and what it did not."""
    ev = _obj(decision.get("evidence")) or {}
    got, missing = {}, []
    for name, keys in FROZEN_FIELDS.items():
        v, _ = _first(alt, keys)
        if v is None and name == "instrument":
            v = decision.get("us_market_slug")
        got[name] = v
        if v is None and name in ACTION_ALTERNATIVES_NEED:
            missing.append(name)
    got.update(
        settlement_terms=alt.get("settlement_rule") or ev.get(
            "settlement_rule"),
        model_version=alt.get("model_version") or ev.get("model_version"),
        policy_version=decision.get("xavier_version"),
        risk_verdict=alt.get("blocker") or decision.get(
            "execution_eligibility"),
        authorization_verdict=decision.get("execution_eligibility"),
        plan_digest=alt.get("plan_digest"))
    if alt.get("action") in IMP_HOLD:
        missing = []          # HOLD sends nothing: no price or depth needed
    elif alt.get("blocker"):
        missing = [m for m in missing if m in ("instrument",)]
    return {"frozen": got, "missing": missing}


IMP_HOLD = ("HOLD", "HOLD_TO_SETTLEMENT")

# ── WHAT XAVIER'S SEARCH ACTUALLY COVERED ─────────────────────────────
#: reasoning.xavier_ladder.search_completeness, when the decision carries
#: it. Older rows do not: that is NOT_RECORDED, never assumed complete.
SC_NOT_RECORDED = "SEARCH_COMPLETENESS_NOT_RECORDED"
SC_COMPLETE = "COMPLETE"
SC_ENDS = ("COMPLETE", "READ_BUDGET_EXHAUSTED", "LIMIT_REACHED", "DEADLINE")
SUPPORTS_BEST_AVAILABLE = "BEST_AVAILABLE"
SUPPORTS_BEST_EXAMINED = "BEST_AMONG_EXAMINED"
SUPPORTS_UNKNOWN = "BEST_AMONG_EXAMINED_COMPLETENESS_UNKNOWN"
NOT_CONSIDERED = "NOT_CONSIDERED_AT_DECISION_TIME"


def _count_of(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, dict):
        return int(sum(_num(x) or 0 for x in v.values()))
    if isinstance(v, (list, tuple)):
        return len(v)
    return None


def search_completeness(decision: dict) -> dict:
    """WHAT THE DECISION'S SEARCH COVERED, as it recorded it. A search
    that did not end COMPLETE supports only 'best among examined'."""
    reasoning = _obj(decision.get("reasoning")) or {}
    ladder = reasoning.get("xavier_ladder") if isinstance(
        reasoning, dict) else None
    view = (ladder or {}).get("search_completeness") if isinstance(
        ladder, dict) else None
    # THE SUPPLIER'S ACCOUNT the decision was gated on is the evidence; the
    # ladder view only adds display fields and never overrides it.
    acct = reasoning.get("search_account") if isinstance(
        reasoning, dict) else None
    sc = None
    if isinstance(view, dict) or isinstance(acct, dict):
        sc = dict(view or {})
        sc.update({k: v for k, v in (acct or {}).items()})
    if not isinstance(sc, dict):
        return {"status": SC_NOT_RECORDED, "supports": SUPPORTS_UNKNOWN,
                "limited": True,
                "why": ("the decision does not record how complete its "
                        "search was; it is never assumed complete")}
    # `stop_reason` is what Xavier's ladder writes
    # (agents.xavier_ladder.search_completeness); without it every recorded
    # search, a COMPLETE one included, read as "ended without saying why".
    ended, _ = _first(sc, ("stop_reason", "ended", "search_ended",
                           "end_reason", "ended_because", "reason"))
    ended = str(ended).upper() if ended is not None else None
    excl = sc.get("excluded") if sc.get("excluded") is not None else \
        sc.get("contracts_excluded")
    out = {
        "ended": ended,
        "discovered": _count_of(_first(sc, ("contracts_discovered",
                                            "discovered"))[0]),
        "examined": _count_of(_first(sc, ("contracts_examined",
                                          "examined"))[0]),
        "excluded": _count_of(excl),
        "excluded_reasons": (excl if isinstance(excl, dict) else
                             sc.get("excluded_reasons")),
        "left_unexamined": _count_of(_first(sc, (
            "left_unexamined", "unexamined", "contracts_left_unexamined"))[
                0])}
    if ended == SC_COMPLETE and sc.get("complete") is not False:
        return dict(out, status=SC_COMPLETE, limited=False,
                    supports=SUPPORTS_BEST_AVAILABLE)
    return dict(out, status="INCOMPLETE:%s" % (ended or "END_NOT_STATED"),
                limited=True, supports=SUPPORTS_BEST_EXAMINED,
                why=("the search ended %s with %s contract(s) left "
                     "unexamined: the chosen action can be judged only "
                     "against what was examined"
                     % (ended or "without saying why",
                        out["left_unexamined"])))


def considered_contracts(decision: dict, alts: list) -> set:
    """Every contract the decision could have considered at its instant:
    the position's own and every alternative's named contract, plus any
    contract lists its search record carries."""
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    seen = set()
    if decision.get("us_market_slug"):
        seen.add(str(decision["us_market_slug"]))
    for a in alts or []:
        if isinstance(a, dict):
            for k in XR.HEDGE_SLUG_KEYS + ("us_market_slug", "slug"):
                if a.get(k):
                    seen.add(str(a[k]))
    reasoning = _obj(decision.get("reasoning")) or {}
    sc = ((reasoning.get("xavier_ladder") or {}).get("search_completeness")
          if isinstance(reasoning, dict) else None) or {}
    for k in ("examined_contracts", "excluded_contracts",
              "discovered_contracts", "unexamined_contracts"):
        v = sc.get(k) if isinstance(sc, dict) else None
        if isinstance(v, dict):
            v = list(v)
        for x in v or []:
            seen.add(str(x.get("slug") if isinstance(x, dict) else x))
    return seen


async def later_discoveries(conn, decision: dict, considered: set) -> list:
    """CONTRACTS FIRST SEEN AFTER THE DECISION INSTANT for the same
    position: named by a LATER Xavier decision's alternatives, or observed
    as a pair on the position's contract after it. Each is recorded as
    NOT_CONSIDERED_AT_DECISION_TIME with when it was discovered: it was not
    executable, eligible or considered at the original instant."""
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    t = _epoch(decision.get("decided_at"))
    found: dict = {}
    rows = await conn.fetch(
        "SELECT xavier_decision_id, alternatives, "
        "       extract(epoch FROM decided_at)::float8 AS at "
        "  FROM bettor_xavier_decisions WHERE intent_id=$1 "
        "   AND decided_at > to_timestamp($2) "
        " ORDER BY decided_at, xavier_decision_id LIMIT 200",
        decision["intent_id"], t)
    for r in rows:
        for a in _obj(r["alternatives"]) or []:
            if not isinstance(a, dict):
                continue
            slug = XR._first_str(a, XR.HEDGE_SLUG_KEYS)
            if slug and slug not in considered and slug not in found:
                found[slug] = {"source": "LATER_XAVIER_DECISION:%s"
                                         % r["xavier_decision_id"],
                               "discovered_at": r["at"]}
    slug0 = decision.get("us_market_slug")
    if slug0 and await _regclass(conn, "bettor_pair_observations"):
        for r in await conn.fetch(
                "SELECT observation_id, hedge_slug, "
                "       extract(epoch FROM observed_at)::float8 AS at "
                "  FROM bettor_pair_observations WHERE primary_slug=$1 "
                "   AND observed_at > to_timestamp($2) "
                " ORDER BY observed_at, observation_id LIMIT 200",
                str(slug0), t):
            s = r["hedge_slug"]
            if s and s not in considered and s not in found:
                found[s] = {"source": "LATER_PAIR_OBSERVATION:%s"
                                      % r["observation_id"],
                            "discovered_at": r["at"]}
    fixture = decision.get("_fixture")
    if fixture and await _regclass(conn,
                                   "bettor_pair_observation_attempts"):
        for r in await conn.fetch(
                "SELECT attempt_id, us_market_slug, "
                "       extract(epoch FROM attempted_at)::float8 AS at "
                "  FROM bettor_pair_observation_attempts WHERE fixture=$1 "
                "   AND attempted_at > to_timestamp($2) "
                " ORDER BY attempted_at, attempt_id LIMIT 200",
                str(fixture), t):
            s = r["us_market_slug"]
            if s and s not in considered and s not in found:
                found[s] = {"source": "LATER_COLLECTION_ATTEMPT:%s"
                                      % r["attempt_id"],
                            "discovered_at": r["at"]}
    return [{"contract": k, "status": NOT_CONSIDERED,
             "discovered_at": v["discovered_at"], "source": v["source"],
             "decided_at": t, "executable_at_decision": False,
             "eligible_at_decision": False, "category": UNEVALUABLE}
            for k, v in sorted(found.items(),
                               key=lambda kv: (kv[1]["discovered_at"],
                                               kv[0]))]


def drawdown(flows) -> dict:
    """Peak-to-trough of the cumulative REALISED cash over the day, in
    booked order. Unrealised marks are not in it."""
    cum = peak = 0.0
    worst = 0.0
    for _, amt in sorted(flows, key=lambda x: (x[0] or 0)):
        cum += float(amt)
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return {"max_drawdown_usd": _r6(worst), "end_cum_usd": _r6(cum),
            "basis": "cumulative realised book cash over the day, in order"}


# ═════════════════════════════════════════════════════════════════════
# 2 · DB HELPERS
# ═════════════════════════════════════════════════════════════════════

async def _regclass(conn, name: str) -> bool:
    return await IMP._regclass(conn, name)


async def has_schema(conn) -> bool:
    return await _regclass(conn, "audrey_audit_reports")


async def _unavailable(fn, *a, **k):
    try:
        return await fn(*a, **k)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "why": "%s: %s" % (
            type(exc).__name__, str(exc)[:160])}


# ═════════════════════════════════════════════════════════════════════
# 3 · DEREK
# ═════════════════════════════════════════════════════════════════════

async def derek_section(conn, *, start: float, end: float) -> dict:
    out: dict[str, Any] = {}
    # ── COVERAGE: what the entry lane saw, and why each was not taken ──
    if await _regclass(conn, "ext_candidate_outcomes"):
        rows = await conn.fetch(
            "SELECT outcome, first_refusal, provider_event_id, "
            "       provider_lag_s, our_processing_s, quote_age_s "
            "  FROM ext_candidate_outcomes "
            " WHERE cycle_at >= $1 AND cycle_at < $2", _ts(start), _ts(end))
        by_outcome, refusals = {}, {}
        for r in rows:
            by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
            if r["first_refusal"]:
                refusals[r["first_refusal"]] = refusals.get(
                    r["first_refusal"], 0) + 1
        lat = {k: IMP.percentile([_num(r[k]) for r in rows], 0.5)
               for k in ("provider_lag_s", "our_processing_s",
                         "quote_age_s")}
        out["coverage"] = {
            "status": "OK" if rows else "EMPTY",
            "why": None if rows else "no candidate was examined this day",
            "candidates_examined": len(rows),
            "distinct_provider_events": len({r["provider_event_id"]
                                             for r in rows
                                             if r["provider_event_id"]}),
            "by_outcome": by_outcome,
            "refusals": dict(sorted(refusals.items(),
                                    key=lambda kv: (-kv[1], kv[0]))),
            "median_delays_s": {k: _r6(v) for k, v in lat.items()}}
    else:
        out["coverage"] = {"status": "UNAVAILABLE",
                           "why": "ext_candidate_outcomes is absent"}
    # ── ENTRIES: the valuations Derek decided ─────────────────────────
    if not await _regclass(conn, "external_valuations"):
        out["entries"] = {"status": "UNAVAILABLE",
                          "why": "external_valuations is absent"}
        return out
    # WHICH DECISIONS SENT A FUNDED ORDER, BY ID. `order_submitted` is the
    # shadow row's own flag and its table CHECKs it FALSE, so on its own every
    # Derek entry read as unplaced -- a funded order included. The funded
    # intent names the Derek decision it executed
    # (decision_ref.derek_decision_id) and that decision names its valuation.
    linked = (await _regclass(conn, "derek_entry_decisions")
              and await _regclass(conn, "bettor_funded_intents"))
    links_sql = (
        "       coalesce((SELECT array_agg(d.decision_id ORDER BY "
        "                 d.decision_id) FROM derek_entry_decisions d "
        "                  WHERE d.valuation_id = v.id), '{}'::text[]) "
        "         AS derek_decision_ids, "
        "       coalesce((SELECT array_agg(i.intent_id ORDER BY i.intent_id) "
        "                   FROM derek_entry_decisions d "
        "                   JOIN bettor_funded_intents i ON i.kind='ENTRY' "
        "                    AND i.decision_ref->>'derek_decision_id' "
        "                        = d.decision_id "
        "                  WHERE d.valuation_id = v.id), '{}'::text[]) "
        "         AS funded_intent_ids, " if linked else
        "       '{}'::text[] AS derek_decision_ids, "
        "       '{}'::text[] AS funded_intent_ids, ")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT id, " + links_sql +
        "       coalesce(event_key, us_market_slug, condition_id) "
        "         AS fixture, decision, admissible, refusals, probability, "
        "       executable_price AS price, cost_per_contract AS cost, "
        "       estimated_edge_per_contract AS edge, proposed_size, "
        "       order_submitted, outcome_known, outcome, age_s, version, "
        "       settlement_rule, execution_estimate IS NOT NULL AS has_exec, "
        "       risk_verdict IS NOT NULL AS has_risk, "
        "       extract(epoch FROM observed_at)::float8 AS observed_at, "
        "       extract(epoch FROM decided_at)::float8 AS decided_at, "
        "       extract(epoch FROM outcome_at)::float8 AS outcome_at "
        "  FROM external_valuations v WHERE record_purpose='ENTRY_DECISION' "
        "   AND decided_at >= $1 AND decided_at < $2 "
        " ORDER BY decided_at, id LIMIT $3", _ts(start), _ts(end),
        MAX_DECISIONS_PER_DAY)]
    buys = [r for r in rows if r["decision"] == "BUY"]
    refused = [r for r in rows if r["decision"] != "BUY"]
    ref_names: dict = {}
    for r in refused:
        for x in r["refusals"] or []:
            ref_names[x] = ref_names.get(x, 0) + 1
    settled = [dict(r, outcome=float(r["outcome"])) for r in rows
               if r["outcome_known"] and r["outcome"] is not None]
    cal = calibration(settled)
    # SELECTED OPPORTUNITIES AND THEIR ECONOMICS. With submission disabled
    # a BUY is an UNPLACED order: known settlement, hypothetical execution.
    selected, missed, sim, unev, refused_known = [], [], [], [], []
    for r in rows:
        placed = bool(r["order_submitted"])
        if r["decision"] == "BUY":
            good = None if _num(r["edge"]) is None else _num(r["edge"]) > 0
        else:
            good = None
        # ACQUISITION = the executable price PLUS the fee: cost_per_contract
        # in external_valuations is the FEE (bettor_external_shadow).
        acq = (None if _num(r["price"]) is None or _num(r["cost"]) is None
               else float(_num(r["price"])) + float(_num(r["cost"])))
        val = None
        if r["outcome_known"] and acq is not None:
            val = float(r["outcome"]) - acq
        item = {"valuation_id": r["id"], "fixture": r["fixture"],
                "decision": r["decision"],
                "decided_at": r["decided_at"],
                "frozen": {"probability": _r6(r["probability"]),
                           "executable_price": _r6(r["price"]),
                           "fee_per_contract": _r6(r["cost"]),
                           "acquisition_per_contract": _r6(acq),
                           "edge_per_contract": _r6(r["edge"]),
                           "proposed_size": _r6(r["proposed_size"]),
                           "quote_observed_at": r["observed_at"],
                           "quote_age_s": _r6(r["age_s"]),
                           "model_version": r["version"],
                           "settlement_terms": r["settlement_rule"],
                           "execution_estimate_recorded": r["has_exec"],
                           "risk_verdict_recorded": r["has_risk"],
                           "refusals": list(r["refusals"] or [])},
                "order_placed": placed,
                "outcome_state": ("SETTLED" if r["outcome_known"]
                                  else S_PENDING),
                # THE EXACT RECORDS: Derek's decision(s) on this valuation
                # and the funded entry intent(s) that name them.
                "derek_decision_ids": list(r["derek_decision_ids"] or []),
                "funded_intent_ids": list(r["funded_intent_ids"] or []),
                "funded_order_sent": bool(r["funded_intent_ids"])}
        if item["funded_order_sent"]:
            # The category below is unchanged: it values this decision-time
            # row, which is not the executed result. What the funded order
            # actually made is the book's (positions / book: ACTUAL).
            item["funded_order_note"] = (
                "a funded order executed this decision; its actual P&L is in "
                "the positions and book sections under the intent id(s). "
                "order_placed is the shadow valuation row's own flag, which "
                "its table pins FALSE")
        if r["decision"] == "BUY":
            item["quality"] = quality(good, val)
            selected.append(item)
        if val is not None:
            cat = ACTUAL if placed else KNOWN_SETTLEMENT
            item["value_per_contract"] = _r6(val)
            item["category"] = cat
            if not placed and r["decision"] != "BUY":
                refused_known.append(item)
                if val > 0:
                    missed.append(item)
        elif not r["outcome_known"] and _num(r["probability"]) is not None \
                and acq is not None:
            item["category"] = SIMULATED
            item["simulated_value_per_contract"] = _r6(
                float(r["probability"]) - acq)
            sim.append(item)
        else:
            item["category"] = UNEVALUABLE
            item["why"] = ("outcome not known and no decision-time price and "
                           "fee" if not r["outcome_known"] else
                           "no decision-time price and fee recorded")
            unev.append(item)
    ks = [(i["fixture"], i["value_per_contract"]) for i in selected
          if i.get("category") == KNOWN_SETTLEMENT]
    out["entries"] = {
        "status": "OK" if rows else "EMPTY",
        "why": None if rows else "Derek recorded no entry decision this day",
        "decisions": len(rows),
        "fixtures": len({r["fixture"] for r in rows}),
        "selected": len(buys), "refused": len(refused),
        "refusals": dict(sorted(ref_names.items(),
                                key=lambda kv: (-kv[1], kv[0]))),
        "orders_placed": sum(1 for r in rows if r["order_submitted"]),
        "funded_orders_sent": sum(1 for r in rows if r["funded_intent_ids"]),
        "sizing": {"proposed_size_median": _r6(IMP.percentile(
            [_num(r["proposed_size"]) for r in buys], 0.5)),
                   "proposed_size_max": _r6(max(
                       [_num(r["proposed_size"]) or 0 for r in buys],
                       default=None) if buys else None)},
        "calibration": cal,
        "selected_economics": {
            "category": KNOWN_SETTLEMENT,
            "could_have_filled": "UNPROVEN",
            "per_contract": fixture_stats(ks),
            "why": ("a BUY was not submitted (submission is disabled): its "
                    "value is the settled payout minus the decision-time "
                    "executable price and fee, and nothing shows the order "
                    "would have filled")},
        "quality": _count([i["quality"] for i in selected]),
        "selected_rows": selected[:MAX_ROWS_IN_REPORT],
        "refused_known_settlement": {
            "category": KNOWN_SETTLEMENT, "could_have_filled": "UNPROVEN",
            "count": len(refused_known),
            "per_contract": fixture_stats(
                [(i["fixture"], i["value_per_contract"])
                 for i in refused_known]),
            "rows": refused_known[:MAX_ROWS_IN_REPORT]},
        "missed_opportunities": {
            "category": KNOWN_SETTLEMENT, "could_have_filled": "UNPROVEN",
            "count": len(missed),
            "fixtures": len({m["fixture"] for m in missed}),
            "rows": missed[:MAX_ROWS_IN_REPORT],
            "basis": ("refused valuations whose contract later settled above "
                      "the decision-time executable price plus fee, from "
                      "captured evidence only")},
        "simulated": {"category": SIMULATED, "count": len(sim),
                      "rows": sim[:MAX_ROWS_IN_REPORT],
                      "per_contract": fixture_stats(
                          [(i["fixture"], i["simulated_value_per_contract"])
                           for i in sim]),
                      "assumptions": ("outcome not yet known: valued at the "
                                      "decision-time probability minus the "
                                      "decision-time executable price and "
                                      "fee; full quantity at the displayed "
                                      "price")},
        "unevaluable": {"category": UNEVALUABLE, "count": len(unev),
                        "rows": unev[:MAX_ROWS_IN_REPORT],
                        "reasons": _count([u["why"] for u in unev])}}
    return out


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


# ═════════════════════════════════════════════════════════════════════
# 4 · XAVIER AND THE POSITIONS
# ═════════════════════════════════════════════════════════════════════

def _alt_value(a: dict):
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    return XR._first(a, XR.VALUE_KEYS)[0]


def _position_state(pos: dict, facts: dict) -> str:
    """The outcome state of one position, as the book knows it now."""
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    st = facts.get("status")
    if st == XR.O_INVALIDATED:
        return S_CONTESTED
    if st in (XR.O_OPEN, XR.O_NO_POSITION, XR.O_NO_ECONOMICS):
        return S_PENDING
    if st == XR.O_NOT_FINAL:
        return S_PROVISIONAL
    legs = pos.get("legs") or []
    void = any(g.get("closed_reason") == "VOIDED_BY_THE_VENUE"
               or (_obj(g.get("settlement")) or {}).get("terminal_reading")
               == "EXPLICIT_VOID" for g in legs)
    push = any(0.0 < (_num((_obj(g.get("settlement")) or {}).get(
        "payout_price")) if _num((_obj(g.get("settlement")) or {}).get(
            "payout_price")) is not None else -1) < 1.0 for g in legs)
    if void or push:
        return S_VOID
    if pos.get("corrections"):
        return S_CORRECTED
    return S_FINAL


async def xavier_section(conn, *, start: float, end: float) -> dict:
    from .. import bettor_xavier as X                            # noqa: PLC0415
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415

    out: dict[str, Any] = {}
    if not await X.has_schema(conn):
        return {"status": "UNAVAILABLE", "why": X.R_SCHEMA}
    decisions = [X._row(r) for r in await conn.fetch(
        "SELECT * FROM bettor_xavier_decisions WHERE decided_at >= $1 "
        "   AND decided_at < $2 ORDER BY decided_at, xavier_decision_id "
        " LIMIT $3", X._dt(start), X._dt(end), MAX_DECISIONS_PER_DAY)]
    execs = {}
    if decisions:
        execs = await X._executions_for(
            conn, [d["xavier_decision_id"] for d in decisions])
    # ── EXECUTION EVENTS AND DELAYS ───────────────────────────────────
    lat_claim, lat_fill, ev_kinds = [], [], {}
    if decisions and await X.has_event_schema(conn):
        rows = await conn.fetch(
            "SELECT e.xavier_decision_id, e.event_kind, "
            "       extract(epoch FROM e.occurred_at)::float8 AS at, "
            "       extract(epoch FROM d.decided_at)::float8 AS decided "
            "  FROM bettor_xavier_execution_events e "
            "  JOIN bettor_xavier_decisions d USING (xavier_decision_id) "
            " WHERE e.xavier_decision_id = ANY($1::text[]) "
            " ORDER BY e.event_id",
            [d["xavier_decision_id"] for d in decisions])
        first: dict = {}
        for r in rows:
            ev_kinds[r["event_kind"]] = ev_kinds.get(r["event_kind"], 0) + 1
            k = (r["xavier_decision_id"], r["event_kind"])
            if k not in first:
                first[k] = r["at"] - r["decided"]
        lat_claim = [v for (_, k), v in first.items()
                     if k == "DISPATCH_CLAIMED"]
        lat_fill = [v for (_, k), v in first.items() if k == "FILL"]
    corrections_present = await _regclass(
        conn, "bettor_funded_settlement_corrections")
    positions: dict = {}
    group_of: dict = {}
    for d in decisions:
        gid = d.get("portfolio_group_id")
        key = gid or ("intent:" + d["intent_id"])
        group_of[d["xavier_decision_id"]] = key
        if key not in positions:
            try:
                positions[key] = await XR.read_position(
                    conn, intent_id=d["intent_id"], group_id=gid,
                    corrections_present=corrections_present)
            except Exception as exc:                            # noqa: BLE001
                positions[key] = {"unreadable": type(exc).__name__}
    slug_cache: dict = {}
    by_action, elig, states = {}, {}, {}
    rows_out, frozen_gaps, alt_total = [], {}, 0
    quality_rows, bench_pairs = [], []
    sc_rows, later_all = [], []
    cats = {c: [] for c in CATEGORIES}
    for d in decisions:
        act = d.get("chosen_action") or "NOTHING_SELECTABLE"
        by_action[act] = by_action.get(act, 0) + 1
        e = d.get("execution_eligibility") or ""
        elig[e.split(":")[0]] = elig.get(e.split(":")[0], 0) + 1
        states[d["responsibility_state"]] = states.get(
            d["responsibility_state"], 0) + 1
        alts = _obj(d.get("alternatives")) or []
        # ── FROZEN DECISION-TIME EVIDENCE ─────────────────────────────
        for a in alts:
            if not isinstance(a, dict):
                continue
            alt_total += 1
            fz = frozen_evidence(a, d)
            for m in fz["missing"]:
                frozen_gaps[m] = frozen_gaps.get(m, 0) + 1
        # ── DECISION QUALITY, FROM ITS OWN RANKING ────────────────────
        ranked = [(a.get("action"), _alt_value(a)) for a in alts
                  if isinstance(a, dict) and not a.get("blocker")
                  and _alt_value(a) is not None]
        chosen = XR.chosen_alternative(d)
        cv = _alt_value(chosen) if chosen else None
        good = None
        if ranked and cv is not None:
            good = cv >= max(v for _, v in ranked) - 1e-9
        pos = positions.get(group_of[d["xavier_decision_id"]]) or {}
        fixture = d.get("us_market_slug") or d["intent_id"]
        realised = None
        state = S_PENDING
        facts = None
        if not pos.get("unreadable") and pos.get("legs"):
            me = next((g for g in pos["legs"]
                       if g["intent_id"] == d["intent_id"]), pos["legs"][0])
            fixture = str(me.get("event_key") or fixture)
            facts = XR.position_facts(
                legs=pos["legs"], children=pos["children"],
                fills=pos["fills"], economics=pos["economics"],
                rechecks=pos["rechecks"], corrections=pos["corrections"],
                decided_at=_epoch(d["decided_at"]))
            state = _position_state(pos, facts)
            if facts["status"] == XR.O_AUTHORITATIVE:
                realised = facts["realised_forward_net_usd"]
        q = quality(good, realised)
        quality_rows.append(q)
        sc = search_completeness(d)
        sc_rows.append(sc)
        later = await later_discoveries(conn, dict(d, _fixture=fixture),
                                        considered_contracts(d, alts))
        for ld in later:
            later_all.append(dict(ld, xavier_decision_id=d[
                "xavier_decision_id"]))
            cats[UNEVALUABLE].append({
                "xavier_decision_id": d["xavier_decision_id"],
                "fixture": fixture, "contract": ld["contract"],
                "why": NOT_CONSIDERED, "discovered_at": ld["discovered_at"],
                "decided_at": ld["decided_at"],
                "executable_at_decision": False})
        row = {"xavier_decision_id": d["xavier_decision_id"],
               "intent_id": d["intent_id"],
               "group": group_of[d["xavier_decision_id"]],
               "fixture": fixture, "decided_at": _epoch(d["decided_at"]),
               "chosen_action": act, "eligibility": e,
               "responsibility_state": d["responsibility_state"],
               "execution": (execs.get(d["xavier_decision_id"]) or {}).get(
                   "status"),
               "outcome_state": state, "quality": q,
               # A LIMITED SEARCH supports 'best among examined' only
               "quality_scope": sc["supports"],
               "search_completeness": sc,
               "not_considered_at_decision_time": later,
               "realised_forward_net_usd": realised,
               "evidence": [{"kind": "bettor_xavier_decisions",
                             "id": d["xavier_decision_id"],
                             "href": "/api/command/xavier/decisions/%s"
                                     % d["xavier_decision_id"]}]}
        rows_out.append(row)
        # ── ACTUAL vs THE ALTERNATIVES NOT TAKEN ─────────────────────
        if realised is not None:
            cats[ACTUAL].append({"xavier_decision_id":
                                 d["xavier_decision_id"], "fixture": fixture,
                                 "value_usd": realised,
                                 "basis": facts["realised_basis"]})
            me = next((g for g in pos["legs"]
                       if g["intent_id"] == d["intent_id"]), pos["legs"][0])
            b = facts["basis_at_decision"].get(me["intent_id"]) or {}
            held = {"qty": b.get("held_qty_at_decision"),
                    "basis_per_contract": b.get("basis_per_contract"),
                    "payout_per_contract": None, "payout_source": None}
            ls = XR.leg_settlement(me, corrections=pos["corrections"],
                                   basis_per_contract=b.get(
                                       "basis_per_contract"))
            if ls.get("ok"):
                held.update(payout_per_contract=ls.get(
                    "payout_per_contract"), payout_source=ls.get("source"))
            ci = XR.chosen_index(d, alts)
            for i, a in enumerate(alts):
                if not isinstance(a, dict) or i == ci:
                    continue
                hedge = None
                slug = XR._first_str(a, XR.HEDGE_SLUG_KEYS)
                if a.get("action") in XR.ACQUIRE_ACTIONS and slug:
                    ms = await XR.market_settlement(conn, slug, slug_cache)
                    side = XR._first_str(a, XR.HEDGE_SIDE_KEYS)
                    if ms.get("ok") and XR.side_payout(
                            ms["long_price"], side) is not None:
                        hedge = {"payout_per_contract": XR.side_payout(
                            ms["long_price"], side),
                            "source": ms["source"]}
                est = XR.hypothetical_estimate(a, held=held, hedge=hedge)
                item = {"xavier_decision_id": d["xavier_decision_id"],
                        "fixture": fixture, "action": a.get("action"),
                        "frozen": frozen_evidence(a, d)["frozen"]}
                if est.get("estimate_usd") is not None:
                    cats[KNOWN_SETTLEMENT].append(dict(
                        item, value_usd=est["estimate_usd"],
                        could_have_filled="UNPROVEN",
                        fill_basis=est.get("fill_basis")))
                    if a.get("action") in IMP_HOLD and act not in IMP_HOLD:
                        bench_pairs.append((fixture, "HOLD_TO_SETTLEMENT",
                                            realised - est["estimate_usd"]))
                    elif act in IMP_HOLD and a.get("action") in (
                            XR.EXIT_ACTIONS):
                        bench_pairs.append((fixture, "DECISION_TIME_EXIT",
                                            realised - est["estimate_usd"]))
                else:
                    cats[UNEVALUABLE].append(dict(
                        item, why=("BLOCKED:%s" % est["blocker"]
                                   if est.get("blocker") else
                                   est.get("no_estimate_because")
                                   or est.get("kind"))))
        elif state in (S_PENDING, S_PROVISIONAL):
            hold_v = next((_alt_value(a) for a in alts
                           if isinstance(a, dict)
                           and a.get("action") == act), None)
            if hold_v is not None:
                cats[SIMULATED].append({
                    "xavier_decision_id": d["xavier_decision_id"],
                    "fixture": fixture, "value_usd": hold_v,
                    "assumptions": ("outcome not known: the chosen action's "
                                    "decision-time expected value, at the "
                                    "decision-time probability")})
            else:
                cats[UNEVALUABLE].append({
                    "xavier_decision_id": d["xavier_decision_id"],
                    "fixture": fixture, "why": "OUTCOME_NOT_KNOWN"})
        else:
            cats[UNEVALUABLE].append({
                "xavier_decision_id": d["xavier_decision_id"],
                "fixture": fixture, "why": state})
    bench: dict = {}
    for f, name, v in bench_pairs:
        bench.setdefault(name, []).append((f, v))
    out.update(
        status="OK" if decisions else "EMPTY",
        why=None if decisions else "Xavier recorded no decision this day",
        decisions=len(decisions),
        fixtures=len({r["fixture"] for r in rows_out}),
        positions=len(positions),
        by_action=dict(sorted(by_action.items())),
        holds=sum(v for k, v in by_action.items() if k in IMP_HOLD),
        exits_and_reductions=sum(v for k, v in by_action.items()
                                 if k in XR.EXIT_ACTIONS),
        pairs={"direct": by_action.get("ACQUIRE_HEDGE", 0),
               "indirect": by_action.get("ACQUIRE_INDIRECT_HEDGE", 0)},
        eligibility=dict(sorted(elig.items())),
        responsibility_states=dict(sorted(states.items())),
        recovery={"order_unresolved": states.get("ORDER_UNRESOLVED", 0),
                  "correction_pending": states.get("CORRECTION_PENDING", 0),
                  "recovered_events": ev_kinds.get("RECOVERED", 0),
                  "unknown_outcome_events": ev_kinds.get("UNKNOWN_OUTCOME",
                                                         0)},
        execution={"events_by_kind": dict(sorted(ev_kinds.items())),
                   "status_by_decision": _count(
                       [r["execution"] for r in rows_out if r["execution"]]),
                   "median_decision_to_claim_s": _r6(IMP.percentile(
                       lat_claim, 0.5)),
                   "median_decision_to_first_fill_s": _r6(IMP.percentile(
                       lat_fill, 0.5))},
        quality=_count(quality_rows),
        search_completeness={
            "by_status": _count([x["status"] for x in sc_rows]),
            "decisions_limited": sum(1 for x in sc_rows if x["limited"]),
            "decisions_not_recorded": sum(1 for x in sc_rows
                                          if x["status"] == SC_NOT_RECORDED),
            "contracts_discovered": sum(x.get("discovered") or 0
                                        for x in sc_rows),
            "contracts_examined": sum(x.get("examined") or 0
                                      for x in sc_rows),
            "contracts_excluded": sum(x.get("excluded") or 0
                                      for x in sc_rows),
            "contracts_left_unexamined": sum(x.get("left_unexamined") or 0
                                             for x in sc_rows),
            "supports": _count([x["supports"] for x in sc_rows]),
            "later_discovered_contracts": len(later_all),
            "later_discovered": later_all[:MAX_ROWS_IN_REPORT],
            "rule": ("a search that did not end COMPLETE supports only 'best "
                     "among examined'; a contract first seen after the "
                     "decision is NOT_CONSIDERED_AT_DECISION_TIME and never "
                     "an executable or missed opportunity of that decision")},
        benchmarks={k: dict(fixture_stats(v, floor=MIN_FIXTURES),
                            compares=(ACTUAL + " minus " + KNOWN_SETTLEMENT),
                            could_have_filled="UNPROVEN")
                    for k, v in sorted(bench.items())},
        frozen_evidence={"alternatives": alt_total,
                         "missing_by_field": dict(sorted(
                             frozen_gaps.items())),
                         "gap_fraction": (_r6(sum(frozen_gaps.values()) /
                                              (alt_total * len(
                                                  ACTION_ALTERNATIVES_NEED)))
                                          if alt_total else None)},
        decision_rows=rows_out[:MAX_ROWS_IN_REPORT])
    out["_categories"] = cats
    out["_positions"] = positions
    return out


async def positions_section(conn, *, start: float, end: float,
                            xavier: dict) -> dict:
    """EVERY POSITION THE DAY TOUCHED: its outcome state and, when final,
    the Derek/Xavier split."""
    from .. import bettor_xavier as X                            # noqa: PLC0415
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    groups = await conn.fetch(
        "SELECT DISTINCT coalesce(portfolio_group_id, 'intent:'||intent_id) "
        "         AS k, portfolio_group_id, intent_id "
        "  FROM bettor_funded_intents WHERE kind='ENTRY' AND ("
        "       (created_at >= $1 AND created_at < $2) "
        "    OR (closed_at >= $1 AND closed_at < $2) "
        "    OR intent_id IN (SELECT intent_id FROM bettor_funded_economics "
        "                      WHERE at >= $1 AND at < $2)) "
        " ORDER BY 1 LIMIT $3", _ts(start), _ts(end), MAX_DECISIONS_PER_DAY)
    positions = dict(xavier.get("_positions") or {})
    corrections_present = await _regclass(
        conn, "bettor_funded_settlement_corrections")
    for g in groups:
        if g["k"] not in positions:
            positions[g["k"]] = await XR.read_position(
                conn, intent_id=g["intent_id"],
                group_id=g["portfolio_group_id"],
                corrections_present=corrections_present)
    rows, states, attributed = [], {}, []
    for key in sorted(positions):
        pos = positions[key]
        if pos.get("unreadable") or not pos.get("legs"):
            continue
        facts = XR.position_facts(
            legs=pos["legs"], children=pos["children"], fills=pos["fills"],
            economics=pos["economics"], rechecks=pos["rechecks"],
            corrections=pos["corrections"], decided_at=end)
        state = _position_state(pos, facts)
        states[state] = states.get(state, 0) + 1
        # ── THE HANDOFF: Xavier's first decision on this position ─────
        first = None
        if await X.has_schema(conn):
            ids = [g["intent_id"] for g in pos["legs"]]
            first = await conn.fetchrow(
                "SELECT * FROM bettor_xavier_decisions "
                " WHERE intent_id = ANY($1::text[]) "
                " ORDER BY decided_at, xavier_decision_id LIMIT 1", ids)
            first = X._row(first) if first is not None else None
        mark, mark_basis, handoff_at = None, None, None
        if first is not None:
            handoff_at = _epoch(first["decided_at"])
            alts = _obj(first.get("alternatives")) or []
            ex = next((a for a in alts if isinstance(a, dict)
                       and a.get("action") in XR.EXIT_ACTIONS
                       and not a.get("blocker")), None)
            if ex is not None:
                cash, _ = XR._first(ex, XR.CASH_NET_KEYS)
                gross, _ = XR._first(ex, XR.GROSS_PROCEEDS_KEYS)
                fee, _ = XR._first(ex, XR.FEE_KEYS)
                if cash is None and gross is not None:
                    cash = gross - (fee or 0.0)
                if cash is None:
                    sv, _ = XR._first(ex, XR.SLICE_NET_KEYS)
                    bf = XR.position_facts(
                        legs=pos["legs"], children=pos["children"],
                        fills=pos["fills"], economics=pos["economics"],
                        decided_at=handoff_at)
                    if sv is not None:
                        cash = sv + bf["remaining_basis_at_decision_usd"]
                if cash is not None:
                    mark, mark_basis = cash, "EXECUTABLE_LIQUIDATION_VALUE"
            if mark is None:
                hold = next((a for a in alts if isinstance(a, dict)
                             and a.get("action") in IMP_HOLD), None)
                hv = _alt_value(hold) if hold else None
                if hv is not None:
                    bf = XR.position_facts(
                        legs=pos["legs"], children=pos["children"],
                        fills=pos["fills"], economics=pos["economics"],
                        decided_at=handoff_at)
                    mark = hv + bf["remaining_basis_at_decision_usd"]
                    mark_basis = "MODEL_EXPECTED_VALUE"
        entry_cash = None
        if handoff_at is not None:
            entry_cash = round(sum(
                float(e["amount_usd"]) for e in pos["economics"]
                if e.get("effective_at") is not None
                and e["effective_at"] <= handoff_at
                and e["kind"] in ("ENTRY_COST", "FEE", "FEE_ADJUSTMENT")), 6)
        total = (facts["realised_whole_position_net_usd"]
                 if state in (S_FINAL, S_CORRECTED, S_VOID) else None)
        att = attribute(total=total, entry_cash=entry_cash,
                        handoff_mark=mark)
        att["handoff_mark_basis"] = mark_basis
        att["handoff_at"] = handoff_at
        if att["status"] == "ATTRIBUTED":
            attributed.append(att)
        legs = pos["legs"]
        rows.append({
            "position": key, "state": state,
            "legs": [g["intent_id"] for g in legs],
            "fixture": str(legs[0].get("event_key")),
            "realised_net_usd": facts["realised_whole_position_net_usd"],
            "provisional_events": facts.get("provisional_events"),
            "corrections": [c.get("correction_id")
                            for c in (pos.get("corrections") or [])],
            "attribution": att,
            "evidence": [{"kind": "bettor_funded_intents",
                          "id": g["intent_id"],
                          "href": "/api/command/funded/intents/%s"
                                  % g["intent_id"]} for g in legs]})
    tot = {"derek_credit_usd": _r6(sum(a["derek_credit_usd"]
                                       for a in attributed)),
           "xavier_credit_usd": _r6(sum(a["xavier_credit_usd"]
                                        for a in attributed)),
           "total_usd": _r6(sum(a["total_usd"] for a in attributed)),
           "positions": len(attributed)}
    tot["sums_exactly"] = (round((tot["derek_credit_usd"] or 0)
                                 + (tot["xavier_credit_usd"] or 0), 6)
                           == round(tot["total_usd"] or 0, 6))
    return {"status": "OK" if rows else "EMPTY",
            "why": None if rows else "no funded position was touched",
            "states": {s: states.get(s, 0) for s in OUTCOME_STATES},
            "rows": rows[:MAX_ROWS_IN_REPORT],
            "attribution": {
                "rule": ("derek_credit = handoff_mark + entry_cash (cash "
                         "booked at or before Xavier's first decision); "
                         "xavier_credit = total_realised - derek_credit. "
                         "They sum to the total exactly; a position without "
                         "a final total, a handoff or a mark is not split"),
                "totals": tot}}


# ═════════════════════════════════════════════════════════════════════
# 5 · THE BOOK
# ═════════════════════════════════════════════════════════════════════

async def book_section(conn, *, start: float, end: float) -> dict:
    rows = [dict(r) for r in await conn.fetch(
        "SELECT event_id, intent_id, kind, amount_usd::float8 AS amt, "
        "       provisional, extract(epoch FROM at)::float8 AS at "
        "  FROM bettor_funded_economics WHERE at >= $1 AND at < $2 "
        " ORDER BY at, event_id", _ts(start), _ts(end))]
    final = [r for r in rows if not r["provisional"]]
    prov = [r for r in rows if r["provisional"]]

    def by_kind(xs):
        out: dict = {}
        for r in xs:
            out[r["kind"]] = round(out.get(r["kind"], 0.0) + r["amt"], 6)
        return dict(sorted(out.items()))
    fees = -round(sum(r["amt"] for r in final
                      if r["kind"] in ("FEE", "FEE_ADJUSTMENT")), 6)
    corr = []
    if await _regclass(conn, "bettor_funded_settlement_corrections"):
        corr = [dict(r) for r in await conn.fetch(
            "SELECT correction_id, intent_id, delta_usd::float8 AS delta, "
            "       from_reading, to_reading "
            "  FROM bettor_funded_settlement_corrections "
            " WHERE created_at >= $1 AND created_at < $2 "
            " ORDER BY correction_id", _ts(start), _ts(end))]
    # ── OPEN INVENTORY AT DAY END, and its latest recorded mark ──────
    open_rows = [dict(r) for r in await conn.fetch(
        "SELECT i.intent_id, i.portfolio_group_id, i.leg_role, "
        "       i.us_market_slug, i.residual_qty::float8 AS residual, "
        "       (SELECT coalesce(sum(e.amount_usd),0)::float8 "
        "          FROM bettor_funded_economics e "
        "         WHERE e.intent_id=i.intent_id AND e.at < $1 "
        "           AND e.kind IN ('ENTRY_COST','FEE','FEE_ADJUSTMENT')) "
        "         AS cash_in "
        "  FROM bettor_funded_intents i WHERE i.kind='ENTRY' "
        "   AND i.created_at < $1 AND (i.closed_at IS NULL "
        "        OR i.closed_at >= $1) "
        "   AND EXISTS (SELECT 1 FROM bettor_funded_fills f "
        "                WHERE f.intent_id=i.intent_id AND f.at < $1) "
        " ORDER BY i.intent_id", _ts(end))]
    marks, unknown = [], 0
    from .. import bettor_xavier as X                            # noqa: PLC0415
    from .. import bettor_xavier_review as XR                    # noqa: PLC0415
    has_x = await X.has_schema(conn)
    for o in open_rows:
        m = None
        if has_x:
            r = await conn.fetchrow(
                "SELECT alternatives, extract(epoch FROM decided_at)::float8 "
                "  AS at, xavier_decision_id FROM bettor_xavier_decisions "
                " WHERE intent_id=$1 AND decided_at < $2 "
                " ORDER BY decided_at DESC LIMIT 1", o["intent_id"],
                _ts(end))
            if r is not None:
                for a in _obj(r["alternatives"]) or []:
                    if isinstance(a, dict) and a.get("action") in \
                            XR.EXIT_ACTIONS and not a.get("blocker"):
                        c, _ = XR._first(a, XR.CASH_NET_KEYS)
                        if c is not None:
                            m = {"intent_id": o["intent_id"],
                                 "liquidation_value_usd": c,
                                 "basis_usd": -o["cash_in"],
                                 "unrealised_usd": _r6(c + o["cash_in"]),
                                 "marked_at": r["at"],
                                 "source": "xavier_decision:%s"
                                           % r["xavier_decision_id"]}
                            break
        if m is None:
            unknown += 1
        else:
            marks.append(m)
    residual = [o for o in open_rows if (o["residual"] or 0) > 0]
    groups: dict = {}
    for o in open_rows:
        if o["portfolio_group_id"]:
            groups.setdefault(o["portfolio_group_id"], set()).add(
                o["leg_role"])
    unpaired = sorted(g for g, roles in groups.items() if len(roles) < 2)
    return {
        "status": "OK" if rows or open_rows else "EMPTY",
        "why": (None if rows or open_rows
                else "no book cash and no open inventory this day"),
        "realized": {"category": ACTUAL, "events": len(final),
                     "net_usd": _r6(sum(r["amt"] for r in final)),
                     "by_kind": by_kind(final)},
        "provisional": {"events": len(prov),
                        "net_usd": _r6(sum(r["amt"] for r in prov)),
                        "by_kind": by_kind(prov),
                        "why": ("provisional economics are NOT realized; "
                                "they are reported apart")},
        "fees_and_costs": {"fees_usd": fees},
        "settlement_corrections": {"count": len(corr), "rows": corr,
                                   "delta_usd": _r6(sum(c["delta"]
                                                        for c in corr))},
        "unrealized": {"marked_positions": len(marks),
                       "unmarked_positions": unknown,
                       "unrealised_usd_marked_only": _r6(
                           sum(m["unrealised_usd"] for m in marks)),
                       "rows": marks[:MAX_ROWS_IN_REPORT],
                       "why": ("unrealized is the latest recorded executable "
                               "liquidation value before day end minus "
                               "basis; a position without one is counted "
                               "UNMARKED, never zero")},
        "exposure": {"open_legs_at_day_end": len(open_rows),
                     "basis_usd": _r6(-sum(o["cash_in"] for o in open_rows))},
        "drawdown": drawdown([(r["at"], r["amt"]) for r in final]),
        "residual_inventory": {"legs_with_residual": len(residual),
                               "residual_qty": _r6(sum(o["residual"] or 0
                                                       for o in residual)),
                               "unpaired_groups": unpaired[:50],
                               "unpaired_group_count": len(unpaired)},
        "evidence": [{"kind": "bettor_funded_economics", "id": r["event_id"],
                      "href": "/api/command/funded/economics/%s"
                              % r["event_id"]}
                     for r in rows[:MAX_ROWS_IN_REPORT]]}


# ═════════════════════════════════════════════════════════════════════
# 6 · COLLECTION THROUGHPUT, VERSIONS, GAPS
# ═════════════════════════════════════════════════════════════════════

async def sample_collection(conn, *, now: float) -> dict:
    """COPY THE COLLECTOR'S LAST PASS FROM THE CYCLE HEARTBEAT (which keeps
    only the last one) into an append-only sample, once per pass."""
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            HEARTBEAT_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return {"sampled": False, "why": type(exc).__name__}
    beat = _obj(raw) if raw else None
    po = (beat or {}).get("pair_observation") if isinstance(beat,
                                                            dict) else None
    if not isinstance(po, dict) or po.get("at") is None:
        return {"sampled": False, "why": "no pair-observation pass on the "
                                         "cycle heartbeat"}
    cand = po.get("candidates") or {}
    na = cand.get("not_attempted") or {}
    at = _num(po.get("at"))
    sid = "po:%s" % (po.get("pass_id") or at)
    try:
        from .. import bettor_pair_observations as PO            # noqa: PLC0415
        per = int(PO.CANDIDATES_PER_PASS)
        recent_key = PO.X_ATTEMPTED_RECENTLY
    except Exception:                                           # noqa: BLE001
        per, recent_key = None, "FIXTURE_ATTEMPTED_RECENTLY_AND_REFUSED"
    wrote = await conn.fetchval(
        "INSERT INTO audrey_collection_samples (sample_id, source, pass_id, "
        " pass_at, sampled_at, configured_per_pass, offered, attempted, "
        " not_attempted, limit_per_pass_left, skipped_recently_refused, "
        " stopped_for_deadline, elapsed_s, budget_s) VALUES "
        " ($1,'CYCLE_HEARTBEAT',$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,"
        "  $13) ON CONFLICT (sample_id) DO NOTHING RETURNING sample_id",
        sid, po.get("pass_id"), _ts(at), _ts(now), per,
        cand.get("offered"), cand.get("attempted"), IMP._j(na),
        na.get("LIMIT_PER_PASS"), na.get(recent_key),
        bool(po.get("stopped_for_deadline")), _num(po.get("elapsed_s")),
        _num(po.get("budget_s")))
    return {"sampled": wrote is not None, "sample_id": sid}


async def collection_section(conn, *, start: float, end: float) -> dict:
    if not await _regclass(conn, "audrey_collection_samples"):
        return {"status": "UNAVAILABLE", "why": R_SCHEMA}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT sample_id, offered, attempted, limit_per_pass_left, "
        "       skipped_recently_refused, stopped_for_deadline, "
        "       configured_per_pass, elapsed_s::float8 AS elapsed_s, "
        "       budget_s::float8 AS budget_s "
        "  FROM audrey_collection_samples WHERE pass_at >= $1 "
        "   AND pass_at < $2 ORDER BY pass_at, sample_id", _ts(start),
        _ts(end))]
    left = [r["limit_per_pass_left"] or 0 for r in rows]
    return {"status": "OK" if rows else "EMPTY",
            "why": None if rows else ("no collector pass was sampled this "
                                      "day"),
            "passes_sampled": len(rows),
            "passes_with_candidates_left_for_limit": sum(1 for x in left
                                                         if x > 0),
            "candidates_left_for_limit": sum(left),
            "skipped_recently_refused": sum(
                r["skipped_recently_refused"] or 0 for r in rows),
            "deadline_stops": sum(1 for r in rows
                                  if r["stopped_for_deadline"]),
            "attempted": sum(r["attempted"] or 0 for r in rows),
            "offered": sum(r["offered"] or 0 for r in rows),
            "configured_per_pass": sorted({r["configured_per_pass"]
                                           for r in rows
                                           if r["configured_per_pass"]
                                           is not None})}


async def versions_section(conn, *, end: float) -> dict:
    from .. import bettor_funded_model as FMD                    # noqa: PLC0415
    out: dict[str, Any] = {
        "audit_version": VERSION,
        "code_version": os.getenv("RENDER_GIT_COMMIT") or "UNKNOWN"}
    try:
        from .. import bettor_xavier as X                        # noqa: PLC0415
        out["xavier_version"] = getattr(X, "VERSION", None)
    except Exception:                                           # noqa: BLE001
        out["xavier_version"] = None
    if await FMD.has_schema(conn):
        out["models"] = [
            {"model_id": r["model_id"], "model_key": r["model_key"],
             "model_version": r["model_version"], "state": r["state"],
             "retired_reason": r["retired_reason"]}
            for r in await conn.fetch(
                "SELECT model_id, model_key, model_version, state, "
                "       retired_reason FROM bettor_funded_models "
                " WHERE created_at < $1 AND state IN ('APPROVED','RETIRED') "
                "   AND (state='APPROVED' OR retired_at >= $2) "
                " ORDER BY model_key, model_id", _ts(end),
                _ts(end - 86400.0 * 30))]
    else:
        out["models"] = {"status": "UNAVAILABLE", "why": FMD.R_SCHEMA_UNAVAILABLE}
    if await _regclass(conn, "agent_policy_versions"):
        out["policies"] = [
            {"agent_id": r["agent_id"], "policy_key": r["policy_key"],
             "version": r["version"], "created_by": r["created_by"]}
            for r in await conn.fetch(
                "SELECT agent_id, policy_key, version, created_by "
                "  FROM agent_policy_versions WHERE created_at < $1 "
                " ORDER BY agent_id, policy_key, created_at", _ts(end))]
    else:
        out["policies"] = {"status": "UNAVAILABLE",
                           "why": "agent_policy_versions is absent"}
    return out


def gaps_section(report: dict) -> dict:
    gaps = []
    d = report.get("derek") or {}
    for k in ("coverage", "entries"):
        s = d.get(k) or {}
        if s.get("status") == "UNAVAILABLE":
            gaps.append({"gap": "DEREK_%s_UNAVAILABLE" % k.upper(),
                         "why": s.get("why")})
    ent = d.get("entries") or {}
    if (ent.get("unevaluable") or {}).get("count"):
        gaps.append({"gap": "DEREK_ROWS_UNEVALUABLE",
                     "count": ent["unevaluable"]["count"],
                     "reasons": ent["unevaluable"]["reasons"]})
    x = report.get("xavier") or {}
    if x.get("status") == "UNAVAILABLE":
        gaps.append({"gap": "XAVIER_UNAVAILABLE", "why": x.get("why")})
    fe = x.get("frozen_evidence") or {}
    if fe.get("missing_by_field"):
        gaps.append({"gap": "XAVIER_ALTERNATIVES_MISSING_FROZEN_EVIDENCE",
                     "fraction": fe.get("gap_fraction"),
                     "by_field": fe["missing_by_field"]})
    sc = x.get("search_completeness") or {}
    if sc.get("decisions_limited"):
        gaps.append({"gap": "XAVIER_SEARCH_INCOMPLETE",
                     "decisions_limited": sc["decisions_limited"],
                     "not_recorded": sc.get("decisions_not_recorded"),
                     "left_unexamined": sc.get("contracts_left_unexamined"),
                     "later_discovered_contracts": sc.get(
                         "later_discovered_contracts")})
    p = report.get("positions") or {}
    st = p.get("states") or {}
    if st.get(S_CONTESTED):
        gaps.append({"gap": "SETTLEMENT_CONTESTED_UNANSWERED",
                     "positions": st[S_CONTESTED]})
    if st.get(S_PROVISIONAL):
        gaps.append({"gap": "PROVISIONAL_ECONOMICS",
                     "positions": st[S_PROVISIONAL]})
    b = report.get("book") or {}
    if (b.get("unrealized") or {}).get("unmarked_positions"):
        gaps.append({"gap": "OPEN_POSITIONS_WITHOUT_A_MARK",
                     "count": b["unrealized"]["unmarked_positions"]})
    ev = report.get("evidence") or {}
    un = (ev.get(UNEVALUABLE) or {}).get("count")
    if un:
        gaps.append({"gap": "ALTERNATIVES_NOT_OBSERVED_OR_UNEVALUABLE",
                     "count": un})
    return {"status": "OK" if gaps else "EMPTY",
            "why": None if gaps else "no data-quality gap found",
            "gaps": gaps}


def evidence_categories(derek: dict, xavier: dict, book: dict) -> dict:
    """THE FOUR CATEGORIES, EACH SUMMED ON ITS OWN, FIXTURES COUNTED."""
    cats = {c: list(v) for c, v in (xavier.get("_categories") or {}).items()}
    for c in CATEGORIES:
        cats.setdefault(c, [])
    ent = derek.get("entries") or {}
    for r in ent.get("selected_rows") or []:
        if r.get("category") in (KNOWN_SETTLEMENT, ACTUAL) and \
                r.get("value_per_contract") is not None:
            cats[r["category"]].append({
                "valuation_id": r["valuation_id"], "fixture": r["fixture"],
                "value_per_contract": r["value_per_contract"],
                "could_have_filled": ("UNPROVEN" if r["category"]
                                      == KNOWN_SETTLEMENT else None)})
    for r in (ent.get("refused_known_settlement") or {}).get("rows") or []:
        cats[KNOWN_SETTLEMENT].append({
            "valuation_id": r["valuation_id"], "fixture": r["fixture"],
            "value_per_contract": r["value_per_contract"],
            "could_have_filled": "UNPROVEN", "refused": True})
    for r in (ent.get("simulated") or {}).get("rows") or []:
        cats[SIMULATED].append({
            "valuation_id": r["valuation_id"], "fixture": r["fixture"],
            "value_per_contract": r["simulated_value_per_contract"],
            "assumptions": (ent.get("simulated") or {}).get("assumptions")})
    for r in (ent.get("unevaluable") or {}).get("rows") or []:
        cats[UNEVALUABLE].append({
            "valuation_id": r["valuation_id"], "fixture": r["fixture"],
            "why": r.get("why")})
    out = {}
    for c in CATEGORIES:
        rows = cats[c]
        usd = [(r["fixture"], r.get("value_usd")) for r in rows
               if r.get("value_usd") is not None]
        pc = [(r["fixture"], r.get("value_per_contract")) for r in rows
              if r.get("value_per_contract") is not None]
        entry = {"count": len(rows),
                 "fixtures": len({str(r.get("fixture")) for r in rows}),
                 "usd": fixture_stats(usd) if usd else None,
                 "per_contract": fixture_stats(pc) if pc else None,
                 "rows": rows[:MAX_ROWS_IN_REPORT]}
        if c == ACTUAL:
            entry["book_realized_net_usd"] = (book.get("realized") or {}).get(
                "net_usd")
            entry["what"] = "cash the authoritative book booked"
        elif c == KNOWN_SETTLEMENT:
            entry["could_have_filled"] = "UNPROVEN"
            entry["what"] = ("alternatives not taken, valued at decision-time "
                             "economics against the outcome now known; not "
                             "a result")
        elif c == SIMULATED:
            entry["what"] = ("outcome not known; decision-time expected "
                             "value under the stated assumptions")
        else:
            entry["what"] = ("blocked, missing or invalidated -- counted by "
                             "reason, never summed as zero")
            entry["reasons"] = _count([str(r.get("why")) for r in rows])
            entry.pop("usd")
            entry.pop("per_contract")
        out[c] = entry
    out["never_merged"] = True
    return out


# ═════════════════════════════════════════════════════════════════════
# 7 · PROPOSALS FROM EVIDENCE
# ═════════════════════════════════════════════════════════════════════

def proposals(report: dict, thresholds: dict) -> dict:
    """WHAT THE DAY'S EVIDENCE JUSTIFIES CHANGING, each with its evidence,
    or NO_CHANGE with every rule that was checked."""
    out, checked = [], []
    col = report.get("collection") or {}
    n_left = col.get("passes_with_candidates_left_for_limit") or 0
    need = int(thresholds.get("collection_alert_passes") or 3)
    checked.append({"rule": "COLLECTION_THROUGHPUT",
                    "passes_with_candidates_left_for_limit": n_left,
                    "threshold": need})
    if n_left >= need:
        out.append({
            "change_class": "COLLECTION_PASS_LIMIT", "assignee": "DEREK",
            "title": "collection pass limit leaves candidates unattempted",
            "hypothesis": ("candidates were left unattempted for "
                           "LIMIT_PER_PASS on %d pass(es); a higher pass "
                           "limit attempts more within the pass budget"
                           % n_left),
            "evidence": {"collection": {k: col.get(k) for k in (
                "passes_sampled", "passes_with_candidates_left_for_limit",
                "candidates_left_for_limit", "deadline_stops",
                "configured_per_pass")}}})
    skipped = col.get("skipped_recently_refused") or 0
    checked.append({"rule": "COLLECTION_REFUSAL_MEMORY",
                    "skipped_recently_refused": skipped,
                    "threshold": need * 2})
    if skipped >= need * 2:
        out.append({
            "change_class": "COLLECTION_REFUSAL_MEMORY", "assignee": "DEREK",
            "title": "fixtures skipped as recently refused",
            "hypothesis": ("%d candidate(s) were skipped as recently "
                           "refused; the memory may withhold fixtures whose "
                           "refusal no longer holds" % skipped),
            "evidence": {"skipped_recently_refused": skipped}})
    cal = ((report.get("derek") or {}).get("entries") or {}).get(
        "calibration") or {}
    floor = int(thresholds.get("min_fixtures_for_statistic") or MIN_FIXTURES)
    short = cal.get("shortfall_vs_market")
    checked.append({"rule": "DEREK_CALIBRATION", "fixtures":
                    cal.get("fixtures"), "floor": floor,
                    "shortfall_vs_market": short})
    if (cal.get("fixtures") or 0) >= floor and short is not None \
            and short > 0:
        out.append({
            "change_class": "DEREK_ENTRY_THRESHOLD", "assignee": "DEREK",
            "title": "Derek's probabilities score worse than the market",
            "hypothesis": ("the decision-time probability's fixture-weighted "
                           "Brier is %.4f worse than the executable price's "
                           "over %d fixtures; the entry threshold is too "
                           "permissive" % (short, cal["fixtures"])),
            "evidence": {"calibration": cal}})
    fe = (report.get("xavier") or {}).get("frozen_evidence") or {}
    frac = fe.get("gap_fraction")
    lim = float(thresholds.get("evidence_gap_alert_fraction") or 0.25)
    checked.append({"rule": "XAVIER_EVIDENCE_GAPS", "gap_fraction": frac,
                    "threshold": lim})
    if frac is not None and frac >= lim and (fe.get("alternatives") or 0) >= 5:
        out.append({
            "change_class": "XAVIER_EVIDENCE_CAPTURE", "assignee": "XAVIER",
            "title": "Xavier's alternatives lack decision-time evidence",
            "hypothesis": ("%.0f%% of required frozen fields are missing on "
                           "Xavier's alternatives (%s); counterfactuals "
                           "cannot be evaluated" % (
                               100 * frac, fe.get("missing_by_field"))),
            "evidence": {"frozen_evidence": fe}})
    sc = (report.get("xavier") or {}).get("search_completeness") or {}
    n_dec = (report.get("xavier") or {}).get("decisions") or 0
    incomplete = sum(v for k, v in (sc.get("by_status") or {}).items()
                     if str(k).startswith("INCOMPLETE:"))
    frac_i = (incomplete / n_dec) if n_dec else None
    checked.append({"rule": "XAVIER_SEARCH_COMPLETENESS",
                    "decisions": n_dec, "incomplete": incomplete,
                    "not_recorded": sc.get("decisions_not_recorded"),
                    "threshold": lim})
    if frac_i is not None and n_dec >= 5 and frac_i >= lim:
        out.append({
            "change_class": "XAVIER_SEARCH_BUDGET", "assignee": "XAVIER",
            "title": "Xavier's searches end before examining every contract",
            "hypothesis": ("%d of %d decision(s) ended their search "
                           "incomplete (%s), leaving %s contract(s) "
                           "unexamined; their choices are only 'best among "
                           "examined'" % (incomplete, n_dec,
                                          sc.get("by_status"),
                                          sc.get("contracts_left_unexamined"))),
            "evidence": {"search_completeness": {
                k: sc.get(k) for k in ("by_status", "contracts_discovered",
                                       "contracts_examined",
                                       "contracts_left_unexamined",
                                       "later_discovered_contracts")}}})
    if not out:
        return {"verdict": NO_CHANGE, "checked": checked,
                "why": "no rule's evidence justified a change this day",
                "proposals": []}
    return {"verdict": "CHANGE_PROPOSED", "checked": checked,
            "proposals": out}


async def report_thresholds(conn) -> dict:
    cls = IMP.CHANGE_CLASSES["REPORT_THRESHOLD"]
    try:
        cur = await IMP.current_policy(conn, cls)
        return dict(IMP.code_default_params(cls), **(cur["params"] or {}),
                    _version=cur.get("version"), _source=cur.get("source"))
    except Exception:                                           # noqa: BLE001
        return dict(IMP.code_default_params(cls), _source="CODE_DEFAULT")


async def prior_improvements(conn) -> dict:
    if not await IMP.tasks_available(conn):
        return {"status": "UNAVAILABLE", "why": IMP.R_TASKS_UNAVAILABLE}
    tasks = await IMP.read_tasks(conn, kind=IMP.TASK_KIND, limit=50)
    cands = await IMP.candidates(conn, limit=50)
    return {"status": "OK" if tasks else "EMPTY",
            "why": None if tasks else "no improvement task yet",
            "tasks": [{"task_id": t["task_id"], "status": t["status"],
                       "assignee": t["assignee"], "title": t["title"]}
                      for t in tasks],
            "candidates": [{"candidate_id": c["candidate_id"],
                            "state": c["state"], "verdict": c["verdict"]}
                           for c in cands]}


# ═════════════════════════════════════════════════════════════════════
# 8 · THE REPORT
# ═════════════════════════════════════════════════════════════════════

async def build_report(conn, *, day: _dt.date, tz_name: str, tz,
                       tz_note=None) -> dict:
    start, end = day_bounds(day, tz)
    # THE EXISTING INVALIDATION PATH FIRST: a correction that changed a
    # training label retires the approved model resting on it, so the
    # report states the registry as it now stands.
    withdrawals = await _withdraw_invalidated(conn)
    derek = await _unavailable(derek_section, conn, start=start, end=end)
    xavier = await _unavailable(xavier_section, conn, start=start, end=end)
    positions = await _unavailable(positions_section, conn, start=start,
                                   end=end, xavier=xavier)
    book = await _unavailable(book_section, conn, start=start, end=end)
    collection = await _unavailable(collection_section, conn, start=start,
                                    end=end)
    versions = await _unavailable(versions_section, conn, end=end)
    evidence = evidence_categories(derek, xavier, book)
    xavier.pop("_categories", None)
    xavier.pop("_positions", None)
    report = {
        "report_id": report_id_for(day, tz_name),
        "audit_day": day.isoformat(), "timezone": tz_name,
        "day_boundary": DAY_BOUNDARY, "timezone_note": tz_note,
        "window": {"start": start, "end": end,
                   "start_utc": _ts(start).isoformat(),
                   "end_utc": _ts(end).isoformat(),
                   "hours": round((end - start) / 3600.0, 3)},
        "boundary_settles_nothing": True,
        "derek": derek, "xavier": xavier, "positions": positions,
        "book": book, "collection": collection, "versions": versions,
        "evidence": evidence,
        "outcomes": {"states": (positions.get("states")
                                if isinstance(positions, dict) else None),
                     "state_names": list(OUTCOME_STATES)}}
    report["data_quality"] = gaps_section(report)
    thresholds = await report_thresholds(conn)
    report["thresholds"] = thresholds
    report["improvements"] = proposals(report, thresholds)
    report["prior_improvements"] = await _unavailable(prior_improvements,
                                                      conn)
    report["run"] = {"model_withdrawals": withdrawals}
    return report


async def _withdraw_invalidated(conn) -> list:
    out = []
    try:
        from .. import bettor_funded_model as FMD                # noqa: PLC0415
        if not await FMD.has_schema(conn):
            return out
        keys = [r["model_key"] for r in await conn.fetch(
            "SELECT DISTINCT model_key FROM bettor_funded_models "
            " WHERE state='APPROVED' ORDER BY 1")]
        for k in keys:
            w = await FMD.withdraw_invalidated(conn, model_key=k)
            if w.get("withdrawn"):
                out.append({"model_key": k,
                            "model_id": w.get("approved_model"),
                            "reason": w.get("reason")})
    except Exception as exc:                                    # noqa: BLE001
        out.append({"error": type(exc).__name__})
    return out


def summary_of(report: dict) -> str:
    d = (report.get("derek") or {}).get("entries") or {}
    x = report.get("xavier") or {}
    p = report.get("positions") or {}
    ev = report.get("evidence") or {}
    imp = report.get("improvements") or {}
    return ("%s (%s, day starts %s local): Derek %s decision(s) over %s "
            "fixture(s), %s selected; Xavier %s decision(s); positions by "
            "state %s; actual %s row(s), known-settlement hypothetical %s, "
            "simulated %s, unevaluable %s (never merged); improvements: %s."
            % (report["audit_day"], report["timezone"], DAY_BOUNDARY,
               d.get("decisions", 0), d.get("fixtures", 0),
               d.get("selected", 0), x.get("decisions", 0),
               {k: v for k, v in (p.get("states") or {}).items() if v},
               (ev.get(ACTUAL) or {}).get("count"),
               (ev.get(KNOWN_SETTLEMENT) or {}).get("count"),
               (ev.get(SIMULATED) or {}).get("count"),
               (ev.get(UNEVALUABLE) or {}).get("count"),
               imp.get("verdict")))


def _changed_sections(old: dict | None, new: dict) -> list:
    if not old:
        return []
    return sorted(k for k in set(old) | set(new)
                  if k not in ("computed_at", "prior_improvements", "run")
                  and json.dumps(old.get(k), sort_keys=True, default=str)
                  != json.dumps(new.get(k), sort_keys=True, default=str))


async def latest_version(conn, report_id: str) -> dict | None:
    r = await conn.fetchrow(
        "SELECT * FROM audrey_audit_reports WHERE report_id=$1 "
        " ORDER BY version DESC LIMIT 1", report_id)
    return _report_row(r)


def _report_row(r) -> dict | None:
    if r is None:
        return None
    out = dict(r)
    for k in ("report", "change_reason"):
        out[k] = _obj(out.get(k))
    for k in ("day_start", "day_end", "computed_at", "recorded_at"):
        if out.get(k) is not None:
            out[k] = _epoch(out[k])
    if out.get("audit_day") is not None:
        out["audit_day"] = str(out["audit_day"])
    return out


async def audit_day(conn, *, day: _dt.date, now: float,
                    tz_name: str | None = None) -> dict:
    """AUDIT ONE LOCAL DAY: same evidence -> the existing version, new
    evidence -> version + 1. Never rewrites."""
    name, tz, note = audit_timezone()
    if tz_name and tz_name != name:
        from zoneinfo import ZoneInfo                            # noqa: PLC0415
        name, tz, note = tz_name, ZoneInfo(tz_name), None
    report = await build_report(conn, day=day, tz_name=name, tz=tz,
                                tz_note=note)
    report["computed_at"] = float(now)
    sha = evidence_sha(report)
    rid = report["report_id"]
    prev = await latest_version(conn, rid)
    if prev is not None and prev["evidence_sha"] == sha:
        return {"ok": True, "report_id": rid, "version": prev["version"],
                "written": False, "evidence_sha": sha,
                "why": "SAME_EVIDENCE_SAME_REPORT", "report": report}
    version = 1 if prev is None else int(prev["version"]) + 1
    change = {"supersedes": None if prev is None else prev["version"],
              "changed_sections": _changed_sections(
                  (prev or {}).get("report"), report),
              "previous_evidence_sha": (prev or {}).get("evidence_sha")}
    start, end = day_bounds(day, tz)
    summary = summary_of(report)
    wrote = await conn.fetchval(
        "INSERT INTO audrey_audit_reports (report_id, version, audit_day, "
        " timezone, day_boundary, day_start, day_end, computed_at, "
        " evidence_sha, supersedes_version, change_reason, report, summary, "
        " audit_version, code_version) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
        " $10,$11::jsonb,$12::jsonb,$13,$14,$15) "
        "ON CONFLICT (report_id, version) DO NOTHING RETURNING version",
        rid, version, day, name, DAY_BOUNDARY, _ts(start), _ts(end),
        _ts(now), sha, None if prev is None else prev["version"],
        IMP._j(change), json.dumps(report, sort_keys=True, default=str),
        summary, VERSION, os.getenv("RENDER_GIT_COMMIT"))
    if wrote is None:
        cur = await latest_version(conn, rid)
        return {"ok": True, "report_id": rid, "version": cur["version"],
                "written": False, "why": "ANOTHER_WRITER_WROTE_THIS_VERSION",
                "report": report}
    return {"ok": True, "report_id": rid, "version": version,
            "written": True, "evidence_sha": sha, "change": change,
            "summary": summary, "report": report}


# ═════════════════════════════════════════════════════════════════════
# 9 · THE HOOK
# ═════════════════════════════════════════════════════════════════════

async def _watermark(conn) -> dict | None:
    r = await conn.fetchrow(
        "SELECT * FROM audrey_audit_watermarks WHERE scope=$1", SCOPE)
    return dict(r) if r is not None else None


async def _put_watermark(conn, *, tz_name, day, report_id, version, now,
                         reaudit_at=None, detail=None) -> None:
    await conn.execute(
        "INSERT INTO audrey_audit_watermarks (scope, timezone, day_boundary, "
        " last_completed_day, last_report_id, last_version, last_run_at, "
        " last_reaudit_at, detail, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,"
        " $8,$9::jsonb,$7) ON CONFLICT (scope) DO UPDATE SET "
        " timezone=EXCLUDED.timezone, day_boundary=EXCLUDED.day_boundary, "
        " last_completed_day=coalesce(EXCLUDED.last_completed_day, "
        "   audrey_audit_watermarks.last_completed_day), "
        " last_report_id=coalesce(EXCLUDED.last_report_id, "
        "   audrey_audit_watermarks.last_report_id), "
        " last_version=coalesce(EXCLUDED.last_version, "
        "   audrey_audit_watermarks.last_version), "
        " last_run_at=EXCLUDED.last_run_at, "
        " last_reaudit_at=coalesce(EXCLUDED.last_reaudit_at, "
        "   audrey_audit_watermarks.last_reaudit_at), "
        " detail=EXCLUDED.detail, updated_at=EXCLUDED.updated_at",
        SCOPE, tz_name, DAY_BOUNDARY, day, report_id, version, _ts(now),
        _ts(reaudit_at), IMP._j(detail or {}))


def task_id_for(change_class: str, day: _dt.date) -> str:
    return "imp:%s:%s" % (change_class.lower(), day.isoformat())


async def create_tasks(conn, report: dict, *, day: _dt.date,
                       now: float) -> list:
    """ONE TASK PER PROPOSAL, deterministic id; while a task of the same
    class is still open, the new evidence is appended to it instead."""
    out = []
    imp = report.get("improvements") or {}
    for p in imp.get("proposals") or []:
        cls = p["change_class"]
        open_same = []
        if await IMP.tasks_available(conn):
            open_same = [t for t in await IMP.read_tasks(
                conn, kind=IMP.TASK_KIND, statuses=(
                    "OPEN", "IN_PROGRESS", "WAITING", "CANDIDATE_READY",
                    "EVALUATING", "APPROVAL_READY"))
                if (t.get("spec") or {}).get("change_class") == cls]
        if open_same:
            t = open_same[0]
            await IMP.task_event(
                conn, t["task_id"], kind="MORE_EVIDENCE", actor="AUDREY",
                detail={"report_id": report["report_id"],
                        "evidence": p["evidence"]}, now=now)
            out.append({"task_id": t["task_id"], "created": False,
                        "appended_evidence": True})
            continue
        tid = task_id_for(cls, day)
        got = await IMP.create_task(
            conn, assignee=p["assignee"], created_by="AUDREY",
            kind=IMP.TASK_KIND, title=p["title"],
            spec={"change_class": cls, "hypothesis": p["hypothesis"],
                  "evidence": p["evidence"],
                  "source_report": report["report_id"]},
            evidence=[{"kind": "audrey_audit_reports",
                       "id": report["report_id"],
                       "href": "/api/command/agents/audrey/reports/%s"
                               % report["report_id"]}],
            task_id=tid, now=now)
        out.append({"task_id": tid, "created": bool(got.get("created",
                                                            got.get("ok"))),
                    "result": {k: v for k, v in got.items()
                               if k in ("ok", "refusal", "created")}})
    return out


async def run_due(conn, *, now: float) -> dict:
    """THE 15-MINUTE HOOK. Samples the collector, audits every completed
    local day past the watermark (bounded), re-checks recent days for new
    evidence at most hourly, and files improvement tasks. Never raises."""
    out: dict[str, Any] = {"version": VERSION, "at": float(now),
                           "audited": [], "reaudited": [], "tasks": []}
    try:
        if not await has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA)
        name, tz, note = audit_timezone()
        out.update(timezone=name, day_boundary=DAY_BOUNDARY,
                   timezone_note=note)
        out["collection_sample"] = await _unavailable(
            sample_collection, conn, now=now)
        wm = await _watermark(conn)
        last_done = last_completed_day(now, tz)
        if wm and wm.get("last_completed_day") and \
                wm.get("timezone") == name:
            nxt = wm["last_completed_day"] + _dt.timedelta(days=1)
        else:
            nxt = last_done
        days = []
        d = nxt
        while d <= last_done and len(days) < MAX_DAYS_PER_RUN:
            days.append(d)
            d += _dt.timedelta(days=1)
        for day in days:
            got = await audit_day(conn, day=day, now=now, tz_name=name)
            rep = got.pop("report")
            out["audited"].append({k: got.get(k) for k in (
                "report_id", "version", "written", "why")})
            out["tasks"] += await create_tasks(conn, rep, day=day, now=now)
            await _put_watermark(conn, tz_name=name, day=day,
                                 report_id=got["report_id"],
                                 version=got["version"], now=now,
                                 detail={"last": got.get("summary"),
                                         "improvements": (rep.get(
                                             "improvements") or {}).get(
                                                 "verdict")})
        # ── RE-AUDIT: new evidence on already-audited days ──────────
        wm = await _watermark(conn)
        last_re = _epoch((wm or {}).get("last_reaudit_at"))
        if wm and wm.get("last_completed_day") and (
                last_re is None or now - last_re >= REAUDIT_INTERVAL_S):
            top = wm["last_completed_day"]
            for k in range(REAUDIT_DAYS):
                day = top - _dt.timedelta(days=k)
                if day in days:
                    continue
                exists = await conn.fetchval(
                    "SELECT 1 FROM audrey_audit_reports WHERE report_id=$1 "
                    " LIMIT 1", report_id_for(day, name))
                if not exists:
                    continue
                got = await audit_day(conn, day=day, now=now, tz_name=name)
                got.pop("report", None)
                if got.get("written"):
                    out["reaudited"].append({k2: got.get(k2) for k2 in (
                        "report_id", "version", "change")})
            await _put_watermark(conn, tz_name=name, day=None,
                                 report_id=None, version=None, now=now,
                                 reaudit_at=now,
                                 detail=dict(_obj(wm.get("detail")) or {},
                                             last_reaudit_versions=len(
                                                 out["reaudited"])))
        if not days and not out["reaudited"]:
            out["why"] = ("nothing due: the last completed local day (%s) "
                          "is audited" % last_done.isoformat())
        return dict(out, ok=True)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_RAISED,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))


# ═════════════════════════════════════════════════════════════════════
# 10 · READERS
# ═════════════════════════════════════════════════════════════════════

async def reports(conn, *, limit: int = 14) -> list:
    if not await has_schema(conn):
        return []
    rows = [_report_row(r) for r in await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (report_id) report_id, version, "
        " audit_day, timezone, day_boundary, day_start, day_end, "
        " computed_at, evidence_sha, supersedes_version, change_reason, "
        " summary FROM audrey_audit_reports "
        " ORDER BY report_id, version DESC) latest "
        " ORDER BY audit_day DESC, report_id LIMIT $1", int(limit))]
    return rows


async def report(conn, report_id: str, *, version: int | None = None
                 ) -> dict | None:
    if not await has_schema(conn):
        return None
    if version is None:
        r = await conn.fetchrow(
            "SELECT * FROM audrey_audit_reports WHERE report_id=$1 "
            " ORDER BY version DESC LIMIT 1", report_id)
    else:
        r = await conn.fetchrow(
            "SELECT * FROM audrey_audit_reports WHERE report_id=$1 "
            " AND version=$2", report_id, int(version))
    out = _report_row(r)
    if out is not None:
        out["versions"] = [dict(v) for v in await conn.fetch(
            "SELECT version, evidence_sha, supersedes_version, "
            " extract(epoch FROM computed_at)::float8 AS computed_at "
            " FROM audrey_audit_reports WHERE report_id=$1 ORDER BY version",
            report_id)]
    return out


def describe() -> dict:
    return {"version": VERSION, "timezone_env": TZ_ENV,
            "default_timezone": DEFAULT_TZ, "day_boundary": DAY_BOUNDARY,
            "tables": ["audrey_audit_reports", "audrey_audit_watermarks",
                       "audrey_collection_samples"],
            "evidence_categories": list(CATEGORIES),
            "outcome_states": list(OUTCOME_STATES),
            "weighting": "ONE_WEIGHT_PER_FIXTURE",
            "writes": ["its own versioned report", "its watermark",
                       "collector samples", "improvement tasks",
                       "withdraw_invalidated (removes authority only)"],
            "never": ["trades", "books", "promotes", "rewrites a report",
                      "counts an unplaced alternative as filled"]}
