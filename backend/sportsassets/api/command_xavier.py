"""XAVIER IN THE COMMAND CENTRE: WHAT IT IS DOING WITH EACH POSITION, AND WHY.

The owner's requirement: "Expose Xavier in the Command Centre with each
position's chosen action, alternatives, reasoning, expected and realized
economics, residual exposure, next review and blockers."

WHAT THIS ASSEMBLES. The newest Xavier record per position (migration 148,
written by the scheduled servicing pass BEFORE anything is dispatched), the
funded book's own realised figures for that position, and the latest daily
review (migration 149). It computes no new economics: a value, a worst case
or an increment over HOLD is the record's own figure, and where the record
does not carry an increment it is derived only as value minus HOLD's value
from the same record and marked as derived.

WITH NO POSITIONS IT SAYS WHY. An empty list reads as "all clear", which is
the one reading an operator must not be given when the truth is that no
account is bound, the decision table is missing or the last cycle's
servicing refused. So the empty state names each of those.

READ-ONLY. No mutating statement lives here, no venue client is imported,
and nothing here can place, cancel or price an order. It shows no balance and
no credential. A failed read raises `XavierUnavailable`, which the route turns
into 503 -- never a well-formed page of zeros.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any

from .. import bettor_xavier as X
from .. import bettor_xavier_review as XR

VERSION = "COMMAND_XAVIER_V1"

#: Positions shown per read, newest first; the count beyond is reported.
POSITION_LIMIT = 50
HISTORY_LIMIT = 100
#: Labelled estimates carried from the latest review into this read.
REVIEW_ALTERNATIVE_ROWS = 25
REVIEW_ERROR_ROWS = 5

R_NO_RECORD = "NO_XAVIER_RECORD_FOR_THAT_POSITION"

HYPOTHETICAL_LABEL = (
    "HYPOTHETICAL ESTIMATE -- decision-time economics carried to the outcome "
    "now known. could_have_filled = UNPROVEN: an eventual winning outcome "
    "does not show that an order which was not placed would have filled")


class XavierUnavailable(Exception):
    """The records could not be read. NOT "no positions are managed"."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _iso(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return _dt.datetime.fromtimestamp(float(v), _dt.timezone.utc
                                          ).isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _obj(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return v
    return v


# ═════════════════════════════════════════════════════════════════════
# 1 · PROJECTIONS (pure)
# ═════════════════════════════════════════════════════════════════════

def project_alternatives(alternatives, *, chosen_index=None) -> list:
    """EVERY CONSIDERED ACTION: value, increment over HOLD, worst case,
    capital and blocker, from the record. Nothing is invented: a field the
    record does not carry is None, and an increment derived here says so."""
    alts = [a for a in (_obj(alternatives) or []) if isinstance(a, dict)]
    hold = next((a for a in alts if a.get("action") == "HOLD"
                 and XR._first(a, XR.VALUE_KEYS)[0] is not None), None)
    hold_value = XR._first(hold or {}, XR.VALUE_KEYS)[0]
    out = []
    for i, a in enumerate(alts):
        value, value_key = XR._first(a, XR.VALUE_KEYS)
        inc = XR._num(a.get("increment_vs_hold_usd"))
        inc_basis = "RECORDED" if inc is not None else None
        if inc is None and value is not None and hold_value is not None:
            inc = round(value - hold_value, 6)
            inc_basis = "DERIVED_AS_VALUE_MINUS_HOLD_VALUE_FROM_THIS_RECORD"
        worst, worst_key = XR._first(a, ("worst_case_remaining_loss_usd",
                                         "downside_usd", "worst_case_usd"))
        capital, capital_key = XR._first(a, ("capital_required_usd",
                                             "incremental_capital_usd"))
        out.append({
            "action": a.get("action"),
            "chosen": i == chosen_index,
            "value_usd": value, "value_key": value_key,
            "increment_vs_hold_usd": inc, "increment_basis": inc_basis,
            "worst_case_usd": worst, "worst_case_key": worst_key,
            "capital_usd": capital, "capital_key": capital_key,
            "capital_released_usd": XR._num(a.get("capital_released_usd")),
            "fees_usd": XR._first(a, XR.FEE_KEYS)[0],
            "qty": XR._first(a, XR.QTY_KEYS)[0],
            "execution_uncertainty": a.get("execution_uncertainty"),
            "unpaired_residual_qty": XR._num(a.get("unpaired_residual_qty")),
            "blocker": a.get("blocker"),
            "why": a.get("why"),
            "plan_digest": a.get("plan_digest")})
    return out


def blockers_of(decision: dict, alternatives: list) -> list:
    """WHAT STOPS THIS POSITION, by name: the gate that stopped the winner,
    each alternative's blocker, and each open obligation."""
    out = []
    elig = str(decision.get("execution_eligibility") or "")
    if elig.startswith(X.E_BLOCKED) or elig.startswith(X.E_NOT_DISPATCHED) \
            or elig in (X.E_SUBMISSION_DISABLED, X.E_NOTHING_SELECTABLE):
        out.append({"kind": "EXECUTION", "name": elig})
    ex = decision.get("execution") if isinstance(
        decision.get("execution"), dict) else {}
    if ex.get("status") in X.UNRESOLVED_STATUSES:
        out.append({"kind": "EXECUTION", "name": ex.get("status")})
    for a in alternatives:
        if a.get("blocker"):
            out.append({"kind": "ALTERNATIVE", "action": a.get("action"),
                        "name": a.get("blocker")})
    for o in _obj(decision.get("obligations")) or []:
        if isinstance(o, dict):
            out.append({"kind": "OBLIGATION",
                        "name": o.get("name") or o.get("obligation")
                        or o.get("kind"), "detail": o})
        elif o:
            out.append({"kind": "OBLIGATION", "name": str(o)})
    return out


def project_decision(d: dict) -> dict:
    """One Xavier record, as the Command Centre shows it."""
    alts_raw = _obj(d.get("alternatives")) or []
    ci = XR.chosen_index(d, alts_raw)
    alts = project_alternatives(alts_raw, chosen_index=ci)
    return {
        "xavier_decision_id": d.get("xavier_decision_id"),
        "decision_id": d.get("decision_id"),
        "intent_id": d.get("intent_id"),
        "portfolio_group_id": d.get("portfolio_group_id"),
        "us_market_slug": d.get("us_market_slug"),
        "account_id": d.get("account_id"), "venue": d.get("venue"),
        "decided_at": _iso(d.get("decided_at")),
        "xavier_version": d.get("xavier_version"),
        "responsibility_state": d.get("responsibility_state"),
        "chosen_action": d.get("chosen_action"),
        "chosen_plan_digest": d.get("chosen_plan_digest"),
        "execution_eligibility": d.get("execution_eligibility"),
        # WHAT EXECUTION DID, derived from the append-only execution events
        # on every read (`bettor_xavier.summarise_events`), never stored.
        "execution": d.get("execution"),
        "alternatives": alts,
        "reasoning": _obj(d.get("reasoning")),
        "expected_economics": _obj(d.get("expected_economics")),
        "residual_exposure": _obj(d.get("residual_exposure")),
        "evidence": _obj(d.get("evidence")),
        "obligations": _obj(d.get("obligations")),
        "next_review_at": _iso(d.get("next_review_at")),
        "blockers": blockers_of(d, alts)}


def review_digest(review: dict | None) -> dict:
    """The latest daily review, bounded, with every estimate still labelled."""
    if not review:
        return {"available": False,
                "why": ("no daily review has been recorded yet. It runs once "
                        "per UTC day from the scheduled cycle")}
    alts = review.get("alternatives") or {}
    errs = review.get("errors") or {}
    per = review.get("per_action") or {}
    models = review.get("models") or {}
    return {
        "available": True,
        "review_id": review.get("review_id"),
        "review_date": _iso(review.get("review_date")),
        "computed_at": _iso(review.get("computed_at")),
        "summary": review.get("summary"),
        "decisions_reviewed": review.get("decisions_reviewed"),
        "decisions_with_outcomes": review.get("decisions_with_outcomes"),
        "forecast_error": {"all": per.get("all"),
                           "by_chosen_action": per.get("by_chosen_action"),
                           "error_is": per.get("error_is"),
                           "outcome_status_counts": per.get(
                               "outcome_status_counts")},
        "alternatives": {
            "label": HYPOTHETICAL_LABEL,
            "kind": alts.get("label"),
            "could_have_filled": alts.get("could_have_filled"),
            "by_action": alts.get("by_action"),
            "rows": (alts.get("rows") or [])[:REVIEW_ALTERNATIVE_ROWS],
            "rows_total": alts.get("rows_total")},
        "largest_errors": (errs.get("largest") or [])[:REVIEW_ERROR_ROWS],
        "errors_by_cause": errs.get("by_cause"),
        "reliability": review.get("reliability"),
        "calibration": review.get("calibration"),
        "models": {"promoted_anything": models.get("promoted_anything"),
                   "promotion": models.get("promotion"),
                   "keys": models.get("keys")},
        "invalidated": len(review.get("invalidated") or []),
        "invalidated_rows": (review.get("invalidated") or [])[:10]}


# ═════════════════════════════════════════════════════════════════════
# 2 · READS
# ═════════════════════════════════════════════════════════════════════

async def book_for(conn, *, intent_id: str, group_id: str | None) -> dict:
    """THE FUNDED BOOK'S OWN FIGURES FOR ONE POSITION: realised cash summed
    over the ledger (every leg and exit child), the basis still held, and
    unrealised as the book reports it -- unmeasured, never zero."""
    from .. import bettor_funded_book as FB

    if group_id:
        legs = [r["intent_id"] for r in await conn.fetch(
            "SELECT intent_id FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND kind='ENTRY'", str(group_id))]
    else:
        legs = []
    if not legs:
        legs = [str(intent_id)]
    rows = await conn.fetch(
        "SELECT e.kind, sum(e.amount_usd)::float8 AS usd, count(*) AS n, "
        "       count(*) FILTER (WHERE e.provisional) AS provisional "
        "  FROM bettor_funded_economics e "
        "  JOIN bettor_funded_intents i ON i.intent_id = e.intent_id "
        " WHERE i.intent_id = ANY($1::text[]) "
        "    OR i.parent_intent_id = ANY($1::text[]) "
        " GROUP BY e.kind ORDER BY e.kind", legs)
    by_kind = {r["kind"]: round(float(r["usd"]), 6) for r in rows}
    basis = []
    for lid in legs:
        try:
            b = await FB.remaining_basis(conn, lid)
        except Exception as exc:                                # noqa: BLE001
            b = {"error": type(exc).__name__}
        basis.append(dict(b, intent_id=lid))
    return {
        "legs": legs,
        "realised_usd": round(sum(by_kind.values()), 6) if rows else 0.0,
        "realised_by_kind": by_kind,
        "economics_events": sum(int(r["n"]) for r in rows),
        "provisional_events": sum(int(r["provisional"]) for r in rows),
        "remaining_basis_usd": round(sum(
            float(b.get("remaining_basis_usd") or 0.0) for b in basis), 6),
        "basis": [{k: b.get(k) for k in (
            "intent_id", "basis_per_contract", "residual_qty",
            "remaining_basis_usd", "entry_qty", "exit_qty", "error")}
            for b in basis],
        "unrealised_usd": None,
        "unrealised_status": ("NOT_MEASURED: the funded book has no mark "
                              "source it stands behind "
                              "(bettor_funded_book.pnl); unmarked is shown "
                              "as unmarked, not as zero"),
        "realised_basis": "SUM_OF_THE_FUNDED_ECONOMICS_LEDGER"}


async def why_nothing_is_managed(conn) -> dict:
    """THE EMPTY STATE, NAMED: is an account bound, what did the last cycle's
    servicing say, and is the decision table present."""
    from .. import bettor_funded_activation as FA
    from .. import bettor_funded_book as FB

    out: dict[str, Any] = {}
    try:
        bound = FA._obj(await FA._state(conn, FA.ACCOUNT_KEY)) or {}
    except Exception as exc:                                    # noqa: BLE001
        bound = None
        out["account_binding_unreadable"] = type(exc).__name__
    acct = str((bound or {}).get("account_id") or "").strip()
    out["account_bound"] = bool(acct and (bound or {}).get("venue"))
    out["account_id"] = acct or None
    out["venue"] = (bound or {}).get("venue")
    if not out["account_bound"]:
        out["account_why"] = (
            "no funded account is bound (%s), so the scheduled servicing pass "
            "returns before it manages anything" % FA.ACCOUNT_KEY)
    last = await FB.last_scheduled_decision(conn)
    svc = last.get("servicing") if isinstance(last, dict) else None
    out["last_cycle"] = {
        "available": last.get("available"), "at": last.get("at"),
        "cycle_state": last.get("cycle_state"), "why": last.get("why"),
        "servicing_ok": (svc or {}).get("ok") if isinstance(svc, dict)
        else None,
        "servicing_refusal": (svc or {}).get("refusal")
        if isinstance(svc, dict) else None}
    out["decision_table"] = ("PRESENT" if await X.has_schema(conn)
                             else X.R_SCHEMA)
    reasons = []
    if out["decision_table"] != "PRESENT":
        reasons.append(X.R_SCHEMA)
    if not out["account_bound"]:
        reasons.append("NO_FUNDED_ACCOUNT_IS_BOUND")
    if out["last_cycle"]["servicing_refusal"]:
        reasons.append(str(out["last_cycle"]["servicing_refusal"]))
    if not last.get("available"):
        reasons.append("THE_LAST_CYCLE_RECORDED_NO_SERVICING_DECISION")
    if not reasons:
        reasons.append("NO_POSITION_HAS_RECEIVED_A_FILL_OR_ALL_ARE_RECONCILED")
    out["reasons"] = reasons
    return out


def _switches() -> dict:
    """The submission switches AS THE SERVING CODE HAS THEM. Read, never
    set; shown so a WOULD_DISPATCH eligibility can be read against them."""
    out = {}
    try:
        from .. import bettor_funded_execution as FX
        out["FUNDED_SUBMISSION_ENABLED"] = bool(FX.FUNDED_SUBMISSION_ENABLED)
    except Exception:                                           # noqa: BLE001
        out["FUNDED_SUBMISSION_ENABLED"] = None
    try:
        from .. import bettor_funded_management as FM
        out["FUNDED_EXIT_SUBMISSION_ENABLED"] = bool(
            FM.FUNDED_EXIT_SUBMISSION_ENABLED)
    except Exception:                                           # noqa: BLE001
        out["FUNDED_EXIT_SUBMISSION_ENABLED"] = None
    return out


async def overview(conn, *, limit: int = POSITION_LIMIT) -> dict:
    """GET /api/command/xavier: every position under Xavier's responsibility,
    newest first, with the latest daily review."""
    try:
        got = await X.latest_decisions(conn, limit=int(limit) + 1)
        review = await XR.latest_review(conn)
    except Exception as exc:                                    # noqa: BLE001
        raise XavierUnavailable("XAVIER_RECORDS_UNREADABLE",
                                type(exc).__name__) from exc
    rows = got.get("positions") or []
    truncated = len(rows) > int(limit)
    rows = rows[:int(limit)]
    positions = []
    for d in rows:
        p = project_decision(d)
        try:
            p["realised"] = await book_for(
                conn, intent_id=d["intent_id"],
                group_id=d.get("portfolio_group_id"))
        except Exception as exc:                                # noqa: BLE001
            p["realised"] = {"unreadable": type(exc).__name__,
                             "why": ("the funded book could not be read for "
                                     "this position; that is not a zero")}
        positions.append(p)
    out: dict[str, Any] = {
        "version": VERSION, "as_of": _iso(time.time()),
        "xavier": X.describe(),
        "positions": positions, "position_count": len(positions),
        "positions_truncated_at": int(limit) if truncated else None,
        "empty": not positions,
        "daily_review": review_digest(review),
        "submission_switches": _switches(),
        "hypothetical_label": HYPOTHETICAL_LABEL,
        "read_only": True,
        "history_route": "/api/command/xavier/{intent_id}"}
    if not got.get("ok"):
        out["decision_table_refusal"] = got.get("refusal")
    if not positions:
        try:
            out["why_nothing_is_managed"] = await why_nothing_is_managed(conn)
        except Exception as exc:                                # noqa: BLE001
            raise XavierUnavailable("XAVIER_EMPTY_STATE_UNREADABLE",
                                    type(exc).__name__) from exc
    return out


async def position_history(conn, *, intent_id: str,
                           limit: int = HISTORY_LIMIT) -> dict | None:
    """GET /api/command/xavier/{intent_id}: every review of one position,
    newest first, with the book's figures once. None when there is none."""
    try:
        got = await X.history(conn, intent_id=str(intent_id), limit=int(limit))
    except Exception as exc:                                    # noqa: BLE001
        raise XavierUnavailable("XAVIER_RECORDS_UNREADABLE",
                                type(exc).__name__) from exc
    rows = got.get("decisions") or []
    if not rows:
        return None
    try:
        book = await book_for(conn, intent_id=str(intent_id),
                              group_id=rows[0].get("portfolio_group_id"))
    except Exception as exc:                                    # noqa: BLE001
        book = {"unreadable": type(exc).__name__}
    return {"version": VERSION, "as_of": _iso(time.time()),
            "intent_id": str(intent_id),
            "decisions": [project_decision(d) for d in rows],
            "count": len(rows), "limit": int(limit),
            "realised": book, "hypothetical_label": HYPOTHETICAL_LABEL,
            "read_only": True}
