"""XAVIER'S DAILY REVIEW: FORECASTS AGAINST WHAT THE BOOK SAYS HAPPENED.

Migration 148 is Xavier's record BEFORE acting. This module is the other
half, run once per UTC day by the scheduled cycle: once a position's outcome
is authoritative in the funded economics ledger, compare what each decision
forecast with what was realised; estimate the alternatives from what was
known at decision time; check the probability inputs against the outcomes;
summarise the model registry; and set aside every decision whose outcome
rests on a settlement the venue now contradicts.

WHAT IT MAY WRITE. Its own review row (migration 149, append-only, one per
UTC day) and the `xavier_daily_review_last` state key. And one thing it is
told to do by the owner's rules: `withdraw_invalidated` for each model key
present -- retiring an approved model whose records no longer reproduce, which
only ever REMOVES authority. It never fits, never promotes, never trades, and
nothing reads its output to authorise, size, price or send anything.

── THE BOUNDARY THAT MATTERS MOST ──────────────────────────────────────

AN ALTERNATIVE THAT WAS NOT TAKEN HAS NO RESULT. It has an ESTIMATE: its
decision-time economics carried to the outcome that is now known. An exit's
estimate is its decision-time proceeds net of fees; HOLD's is the settlement
payout of the held quantity minus its basis; an acquisition's is both legs'
settlement payouts at decision-time quantities and prices minus cost and fees.
Every such row says `kind = HYPOTHETICAL_ESTIMATE`, names its `fill_basis`,
carries `could_have_filled = UNPROVEN` and `eventual_outcome_known = true`.
The contract finishing in the money does not prove that an order resting at
the displayed depth would have filled; a sale moves against itself through
depth and the moment has passed. So no row here says "would have made X",
and no statistic here says which action was right.

── WHAT THE FORECAST IS COMPARED AGAINST ───────────────────────────────

THE SAME SCOPE THE FORECAST WAS MADE IN. A decision's `expected_net_usd` is a
forward, whole-position figure relative to the basis still held at that
instant: HOLD is `p x q - remaining basis`, an exit is proceeds net of fees
minus the basis of what is sold. So the realised figure it is scored against
is the funded economics ledger's cash AFTER the decision instant, over every
leg of the position and every exit child, minus the remaining basis of the
legs held at that instant -- `realised_forward_net_usd`. Cash booked before
the decision (the entry, its fee) is the basis, not a result of the decision.
The ledger's plain sum, `realised_whole_position_net_usd`, is reported beside
it and is what the existing learning path joins; the two differ exactly by
what was already sunk when the decision was taken.

REPEATED REVIEWS OF ONE FIXTURE ARE NOT INDEPENDENT. A position reviewed on
three hundred cycles has one outcome. Every statistic here gives each FIXTURE
a total weight of one, and reports INSUFFICIENT_EVIDENCE below
MIN_FIXTURES_FOR_A_STATISTIC fixtures rather than a figure somebody quotes.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import time
from typing import Any

from . import bettor_xavier as X

VERSION = "XAVIER_REVIEW_V1"

#: THE ONCE-PER-DAY GUARD'S STATE ROW. The review row's unique `review_date` is
#: the authority (a restart finds it and does not re-run); this key carries the
#: last digest for the heartbeat and the last failure, so a failed run is
#: visible and retried next cycle.
STATE_KEY = "xavier_daily_review_last"

#: Decisions decided within this many days before `now` are read. A position
#: lives for days, so a decision older than this has long been reviewed.
WINDOW_DAYS = 45
#: Bounds, each reported when it cuts.
MAX_DECISIONS = 5000
MAX_ALTERNATIVE_ROWS = 400
MAX_ERROR_ROWS = 20
MAX_INVALIDATED_ROWS = 200
MODELS_PER_KEY_AND_STATE = 10
#: Below this many distinct fixtures a statistic is reported with its counts
#: and INSUFFICIENT_EVIDENCE, the same floor `learn.metrics.by_group` uses.
MIN_FIXTURES_FOR_A_STATISTIC = 30
RELIABILITY_BINS = 10
#: The fee attribution's tolerance. A cent, for the same reason the worst-case
#: check uses a cent: the margins here are tens of cents.
FEE_TOLERANCE_USD = 0.01

# ── LABELS ON EVERY ALTERNATIVE ROW ──────────────────────────────────
KIND_HYPOTHETICAL = "HYPOTHETICAL_ESTIMATE"
KIND_BLOCKED = "BLOCKED_NO_ESTIMATE"
KIND_NOT_ESTIMATED = "NOT_ESTIMATED"
COULD_HAVE_FILLED = "UNPROVEN"
FILL_BASIS_DISPLAYED = "DISPLAYED_DEPTH_AT_DECISION_NOT_A_FILL"
FILL_BASIS_NO_ORDER = "NO_ORDER_HOLD_KEEPS_THE_POSITION_AS_HELD"
NOT_A_RESULT = (
    "an estimate from decision-time information and the outcome now known. "
    "It is not a result: nothing shows an order at that price and size "
    "would have filled")

# ── REFUSALS ──────────────────────────────────────────────────────────
R_SCHEMA = "THE_XAVIER_REVIEW_TABLE_IS_NOT_IN_THIS_DATABASE"
R_ALREADY_REVIEWED = "XAVIER_HAS_ALREADY_REVIEWED_THIS_UTC_DAY"
R_DECISIONS_UNREADABLE = "XAVIER_DECISIONS_COULD_NOT_BE_READ"
R_WRITE_FAILED = "XAVIER_REVIEW_WRITE_FAILED"
R_RAISED = "XAVIER_DAILY_REVIEW_RAISED"

# ── OUTCOME STATUS PER DECISION ───────────────────────────────────────
O_AUTHORITATIVE = "AUTHORITATIVE"
O_NO_POSITION = "THE_POSITION_IS_NOT_IN_THE_FUNDED_BOOK"
O_OPEN = "THE_POSITION_IS_NOT_RECONCILED"
O_NO_ECONOMICS = "THE_BOOK_HAS_NO_ECONOMICS_FOR_THIS_POSITION"
O_NOT_FINAL = "THE_OUTCOME_CONTAINS_PROVISIONAL_ECONOMICS"
O_UNREADABLE = "THE_POSITION_COULD_NOT_BE_READ"
O_INVALIDATED = "INVALIDATED"
INV_VENUE_DISAGREES = "THE_VENUE_NOW_CONTRADICTS_THE_BOOKED_SETTLEMENT"

# ── WHY AN ALTERNATIVE HAS NO ESTIMATE ────────────────────────────────
NE_BLOCKED = "BLOCKED_AT_DECISION_TIME"
NE_NO_ECONOMICS = "NO_DECISION_TIME_ECONOMICS_WERE_RECORDED"
NE_NO_RULE = "NO_ESTIMATION_RULE_FOR_THIS_ACTION"
NE_QTY_UNKNOWN = "THE_ALTERNATIVE_DOES_NOT_STATE_ITS_QUANTITY"
NE_BASIS_UNKNOWN = "THE_HELD_BASIS_AT_DECISION_IS_NOT_KNOWN"
NE_HELD_OUTCOME_UNKNOWN = "THE_HELD_CONTRACTS_SETTLEMENT_IS_NOT_KNOWN"
NE_PROCEEDS_UNKNOWN = "THE_ALTERNATIVE_DOES_NOT_STATE_ITS_PROCEEDS_AND_FEES"
NE_COST_UNKNOWN = "THE_ALTERNATIVE_DOES_NOT_STATE_ITS_COST_AND_FEES"
NE_HEDGE_NOT_NAMED = "THE_ALTERNATIVE_DOES_NOT_NAME_THE_HEDGE_CONTRACT"
NE_HEDGE_OUTCOME_UNKNOWN = "THE_HEDGE_CONTRACTS_SETTLEMENT_IS_NOT_KNOWN"

# ── CAUSES A FORECAST ERROR IS FILED UNDER, only where the record says ──
C_SETTLEMENT_CORRECTION = "SETTLEMENT_CORRECTION"
C_EXECUTION = "EXECUTION"
C_LATER_ACTIONS = "LATER_ACTIONS_CHANGED_THE_POSITION"
C_FEES = "FEES"
C_PROBABILITY = "PROBABILITY"
C_NOT_STATED = "NOT_STATED_BY_THE_RECORD"

HOLD_ACTIONS = ("HOLD", "HOLD_TO_SETTLEMENT")
EXIT_ACTIONS = ("DIRECT_EXIT", "EXIT", "REDUCE")
ACQUIRE_ACTIONS = ("ACQUIRE_HEDGE", "ACQUIRE_INDIRECT_HEDGE")

#: WHERE EACH INPUT IS READ FROM ON A RECORDED ALTERNATIVE, first found wins.
#: The record is written by the servicing pass (migration 148) and several
#: producers name the same quantity differently; the lists are the one place
#: that correspondence lives.
VALUE_KEYS = ("expected_net_usd", "value_usd")
QTY_KEYS = ("qty", "quantity", "selected_qty", "plan_quantity")
SLICE_NET_KEYS = ("slice_value_usd",)
CASH_NET_KEYS = ("cash_now_usd", "net_proceeds_usd")
GROSS_PROCEEDS_KEYS = ("proceeds_usd", "expected_proceeds_usd",
                       "gross_proceeds_usd")
FEE_KEYS = ("fees_usd", "fee_usd")
HEDGE_SLUG_KEYS = ("hedge_us_market_slug", "hedge_slug", "candidate_slug",
                   "us_market_slug")
HEDGE_SIDE_KEYS = ("hedge_order_intent", "hedge_side", "order_intent")
HEDGE_QTY_KEYS = ("hedge_qty", "units_valued", "units", "quantity", "qty")

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


# ═════════════════════════════════════════════════════════════════════
# 0 · SMALL PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    """A finite float, or None. A bool is not a number here."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _first(d: dict, keys) -> tuple:
    """(value, key) of the first finite number under `keys`, else (None, None)."""
    for k in keys:
        v = _num((d or {}).get(k))
        if v is not None:
            return v, k
    return None, None


def _first_str(d: dict, keys) -> str | None:
    for k in keys:
        v = (d or {}).get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _obj(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _num(v)


def utc_date(now: float) -> _dt.date:
    return _dt.datetime.fromtimestamp(float(now), _dt.timezone.utc).date()


def next_due_at(review_date: _dt.date) -> float:
    """The first instant of the next UTC day: when the next review is due."""
    nxt = _dt.datetime.combine(review_date + _dt.timedelta(days=1),
                               _dt.time(0, 0), tzinfo=_dt.timezone.utc)
    return nxt.timestamp()


def side_payout(long_price, order_intent) -> float | None:
    """What one contract of THIS side pays at a venue long-side price."""
    p = _num(long_price)
    if p is None or order_intent not in (LONG, SHORT):
        return None
    return p if order_intent == LONG else round(1.0 - p, 10)


def side_won(long_price, order_intent):
    """True / False for a binary result; None for a push, a void or unknown.

    A long won at a long-side price of 1, a short at 0. Anything strictly
    between is a push or a refund and is NOT a binary outcome."""
    p = _num(long_price)
    if p is None or order_intent not in (LONG, SHORT):
        return None
    if p == 1.0:
        return order_intent == LONG
    if p == 0.0:
        return order_intent == SHORT
    return None


def fixture_weights(fixtures) -> list:
    """ONE WEIGHT PER FIXTURE, shared by its rows."""
    n: dict = {}
    for f in fixtures:
        n[f] = n.get(f, 0) + 1
    return [1.0 / n[f] for f in fixtures]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE POSITION'S OUTCOME, FROM THE BOOK (pure)
# ═════════════════════════════════════════════════════════════════════

def unanswered_disagreements(rechecks, corrections) -> list:
    """Every DISAGREES re-read (migration 141) that no recorded settlement
    correction answers. `corrections` is None when that table is absent, in
    which case nothing answers a disagreement."""
    answered = {str(c.get("recheck_id")) for c in (corrections or [])
                if c.get("recheck_id") is not None}
    return [r for r in (rechecks or [])
            if r.get("verdict") == "DISAGREES"
            and str(r.get("recheck_id")) not in answered]


def position_facts(*, legs, children, fills, economics, rechecks=(),
                   corrections=None, decided_at: float) -> dict:
    """WHAT THE BOOK SAYS ABOUT ONE POSITION, AS SEEN FROM ONE DECISION.

    `legs` are the position's ENTRY intents (the whole portfolio group),
    `children` their exit intents, `fills` every fill of either (each naming
    `leg_intent_id`, the entry leg it belongs to), `economics` every ledger
    row of either with `effective_at` (a fill's own instant for the rows a
    fill produced), `rechecks` the settlement re-reads of the legs and
    `corrections` the recorded settlement corrections (None: table absent).
    """
    t = float(decided_at)
    out: dict[str, Any] = {"legs": [str(g["intent_id"]) for g in legs or []]}
    if not legs:
        return dict(out, status=O_NO_POSITION)
    open_legs = [str(g["intent_id"]) for g in legs if g.get("open")]
    live_children = [str(c["intent_id"]) for c in children or []
                     if c.get("live")]
    out.update(open_legs=open_legs, live_children=live_children,
               closed=not open_legs and not live_children)

    # ── THE BASIS STILL HELD AT THE DECISION INSTANT, per leg ──────────
    basis: dict = {}
    for g in legs:
        lid = str(g["intent_id"])
        mine = [f for f in fills or [] if str(f.get("leg_intent_id")) == lid]
        eq = sum(float(f["qty"]) for f in mine if f.get("direction") == "ENTRY"
                 and _epoch(f.get("at")) is not None
                 and _epoch(f.get("at")) <= t)
        ec = sum(float(f["cash_usd"]) for f in mine
                 if f.get("direction") == "ENTRY"
                 and _epoch(f.get("at")) is not None
                 and _epoch(f.get("at")) <= t)
        xq = sum(float(f["qty"]) for f in mine if f.get("direction") == "EXIT"
                 and _epoch(f.get("at")) is not None
                 and _epoch(f.get("at")) <= t)
        per = (ec / eq) if eq > 0 else None
        held = max(0.0, eq - xq)
        basis[lid] = {"leg_role": g.get("leg_role"),
                      "order_intent": g.get("order_intent"),
                      "basis_per_contract": (None if per is None
                                             else round(per, 8)),
                      "held_qty_at_decision": round(held, 6),
                      "basis_usd": (0.0 if per is None
                                    else round(per * held, 6))}
    out["basis_at_decision"] = basis
    remaining = round(sum(b["basis_usd"] for b in basis.values()), 6)

    rows = list(economics or [])
    total = round(sum(float(e["amount_usd"]) for e in rows), 6)
    after = [e for e in rows if _epoch(e.get("effective_at")) is not None
             and _epoch(e.get("effective_at")) > t]
    by_kind: dict = {}
    for e in after:
        by_kind[e["kind"]] = round(by_kind.get(e["kind"], 0.0)
                                   + float(e["amount_usd"]), 6)
    flows_after = round(sum(float(e["amount_usd"]) for e in after), 6)
    fees_after = -round(sum(float(e["amount_usd"]) for e in after
                            if e["kind"] in ("FEE", "FEE_ADJUSTMENT")), 6)
    provisional = sum(1 for e in rows if e.get("provisional"))
    out.update(
        economics_events=len(rows), provisional_events=provisional,
        realised_whole_position_net_usd=total,
        remaining_basis_at_decision_usd=remaining,
        flows_after_decision_usd=flows_after,
        flows_after_decision_by_kind=by_kind,
        fees_after_decision_usd=fees_after,
        realised_forward_net_usd=round(flows_after - remaining, 6),
        # A SALE OR A NEW LEG AFTER THE DECISION: the realised path includes
        # what later decisions did, not only what this one forecast.
        later_actions=any(e["kind"] in ("EXIT_PROCEEDS", "ENTRY_COST")
                          for e in after),
        realised_basis=("funded economics after the decision instant minus "
                        "the remaining basis held at it"))

    bad = unanswered_disagreements(rechecks, corrections)
    answered = [c for c in (corrections or [])]
    out["settlement_corrections"] = [
        {"correction_id": c.get("correction_id"),
         "intent_id": c.get("intent_id"), "recheck_id": c.get("recheck_id")}
        for c in answered]
    if bad:
        out["invalidation"] = {
            "reason": INV_VENUE_DISAGREES,
            "recheck_ids": [r.get("recheck_id") for r in bad],
            "legs": sorted({str(r.get("intent_id")) for r in bad}),
            "readings": [{"intent_id": r.get("intent_id"),
                          "booked": r.get("booked_reading"),
                          "booked_payout_price": _num(
                              r.get("booked_payout_price")),
                          "venue_now": r.get("venue_reading"),
                          "venue_payout_price": _num(
                              r.get("venue_payout_price"))} for r in bad],
            "why": ("the outcome rests on a settlement the venue no longer "
                    "gives and no recorded correction answers it. The booked "
                    "figures are not the venue's word, so this decision is "
                    "excluded from every error statistic")}

    if not out["closed"]:
        status = O_OPEN
    elif bad:
        status = O_INVALIDATED
    elif not rows:
        status = O_NO_ECONOMICS
    elif provisional:
        status = O_NOT_FINAL
    else:
        status = O_AUTHORITATIVE
    out["status"] = status
    return out


def leg_settlement(leg: dict, *, corrections=None,
                   basis_per_contract=None) -> dict:
    """WHAT ONE CONTRACT OF THIS LEG PAID, from its own booked settlement.

    A correction that answers the leg overrides the booked reading with its
    `to_price` / `to_reading`. A void pays back the basis per contract (the
    book's void refund rule), which is not a binary outcome."""
    st = _obj(leg.get("settlement")) or {}
    side = leg.get("order_intent")
    reading = st.get("terminal_reading")
    price = _num(st.get("payout_price"))
    source = "BOOKED_SETTLEMENT"
    mine = [c for c in (corrections or [])
            if str(c.get("intent_id")) == str(leg.get("intent_id"))]
    if mine:
        c = mine[-1]
        if _num(c.get("to_price")) is not None:
            price = _num(c.get("to_price"))
        reading = c.get("to_reading") or reading
        source = "RECORDED_SETTLEMENT_CORRECTION"
    if reading == "EXPLICIT_VOID" or leg.get("closed_reason") == \
            "VOIDED_BY_THE_VENUE":
        b = _num(basis_per_contract)
        return {"ok": b is not None, "void": True, "won": None,
                "payout_per_contract": b, "source": source + "_VOID_REFUND",
                "long_price": None}
    if leg.get("closed_reason") != "SETTLED_BY_THE_VENUE" and not mine:
        return {"ok": False, "why": "the leg was not closed by the venue's "
                                    "settlement"}
    pay = side_payout(price, side)
    if pay is None:
        return {"ok": False, "why": "the booked settlement states no price"}
    return {"ok": True, "void": False, "won": side_won(price, side),
            "payout_per_contract": pay, "long_price": price,
            "source": source}


# ═════════════════════════════════════════════════════════════════════
# 2 · ALTERNATIVES: LABELLED ESTIMATES, NEVER RESULTS (pure)
# ═════════════════════════════════════════════════════════════════════

def hypothetical_estimate(alt: dict, *, held: dict, hedge: dict | None = None
                          ) -> dict:
    """ONE NOT-TAKEN ALTERNATIVE, CARRIED TO THE OUTCOME NOW KNOWN.

    `held` = {qty, basis_per_contract, payout_per_contract, payout_source}
    for the position's held leg at the decision instant; `hedge` =
    {payout_per_contract, source} for an acquisition's second contract, or
    None when its settlement is not known. Only decision-time figures from
    the record enter the arithmetic; the outcome enters only as the payout.
    """
    a = alt if isinstance(alt, dict) else {}
    action = str(a.get("action") or "")
    value, value_key = _first(a, VALUE_KEYS)
    row: dict[str, Any] = {"action": action,
                           "decision_time_value_usd": value,
                           "decision_time_value_key": value_key,
                           "blocker": a.get("blocker")}

    def _none(kind, reason, why):
        return dict(row, kind=kind, estimate_usd=None,
                    no_estimate_because=reason, why=why)

    if value is None:
        if a.get("blocker"):
            return _none(KIND_BLOCKED, NE_BLOCKED,
                         "blocked at decision time; a blocked alternative is "
                         "listed with its blocker and carries no estimate")
        return _none(KIND_NOT_ESTIMATED, NE_NO_ECONOMICS,
                     "no decision-time economics were recorded for it")

    b = _num((held or {}).get("basis_per_contract"))
    q = _num((held or {}).get("qty"))
    pay = _num((held or {}).get("payout_per_contract"))

    def _est(estimate, fill_basis, arithmetic, inputs):
        return dict(row, kind=KIND_HYPOTHETICAL,
                    estimate_usd=round(estimate, 6),
                    fill_basis=fill_basis,
                    could_have_filled=COULD_HAVE_FILLED,
                    eventual_outcome_known=True,
                    arithmetic=arithmetic, inputs=inputs,
                    not_a_result=NOT_A_RESULT)

    if action in HOLD_ACTIONS:
        qq, _k = _first(a, QTY_KEYS)
        qq = qq if qq is not None else q
        if qq is None:
            return _none(KIND_NOT_ESTIMATED, NE_QTY_UNKNOWN,
                         "neither the alternative nor the position states "
                         "the held quantity")
        if b is None:
            return _none(KIND_NOT_ESTIMATED, NE_BASIS_UNKNOWN,
                         "the held basis at the decision is not known")
        if pay is None:
            return _none(KIND_NOT_ESTIMATED, NE_HELD_OUTCOME_UNKNOWN,
                         "the held contract's settlement is not known")
        est = qq * pay - qq * b
        return _est(est, FILL_BASIS_NO_ORDER,
                    "%.6g x payout %.6g - %.6g x basis %.6g"
                    % (qq, pay, qq, b),
                    {"qty": qq, "payout_per_contract": pay,
                     "payout_source": (held or {}).get("payout_source"),
                     "basis_per_contract": b})

    if action in EXIT_ACTIONS:
        s, _k = _first(a, QTY_KEYS)
        if s is None:
            return _none(KIND_NOT_ESTIMATED, NE_QTY_UNKNOWN,
                         "the exit's size is not recorded")
        slice_net, sk = _first(a, SLICE_NET_KEYS)
        cash_net, ck = _first(a, CASH_NET_KEYS)
        gross, gk = _first(a, GROSS_PROCEEDS_KEYS)
        fees, fk = _first(a, FEE_KEYS)
        if slice_net is not None:
            sold = slice_net
            how = "%s %.6g (proceeds - fees - basis of the slice)" % (sk,
                                                                      sold)
        elif b is None:
            return _none(KIND_NOT_ESTIMATED, NE_BASIS_UNKNOWN,
                         "the held basis at the decision is not known")
        elif cash_net is not None:
            sold = cash_net - s * b
            how = "%s %.6g - %.6g x basis %.6g" % (ck, cash_net, s, b)
        elif gross is not None and fees is not None:
            sold = gross - fees - s * b
            how = "%s %.6g - %s %.6g - %.6g x basis %.6g" % (
                gk, gross, fk, fees, s, b)
        else:
            return _none(KIND_NOT_ESTIMATED, NE_PROCEEDS_UNKNOWN,
                         "the exit's decision-time proceeds net of fees are "
                         "not recorded")
        kept = max(0.0, (q or s) - s)
        est = sold
        if kept > 1e-9:
            if b is None:
                return _none(KIND_NOT_ESTIMATED, NE_BASIS_UNKNOWN,
                             "the retained quantity's basis is not known")
            if pay is None:
                return _none(KIND_NOT_ESTIMATED, NE_HELD_OUTCOME_UNKNOWN,
                             "part of the position would have been retained "
                             "and the held contract's settlement is not "
                             "known")
            est += kept * pay - kept * b
            how += " + retained %.6g x (payout %.6g - basis %.6g)" % (
                kept, pay, b)
        return _est(est, FILL_BASIS_DISPLAYED, how,
                    {"sold_qty": s, "retained_qty": kept,
                     "payout_per_contract": pay if kept > 1e-9 else None,
                     "basis_per_contract": b})

    if action in ACQUIRE_ACTIONS:
        slug = _first_str(a, HEDGE_SLUG_KEYS)
        if not slug:
            return _none(KIND_NOT_ESTIMATED, NE_HEDGE_NOT_NAMED,
                         "the recorded alternative does not name the second "
                         "contract, so its settlement cannot be looked up")
        hpay = _num((hedge or {}).get("payout_per_contract"))
        if hpay is None:
            return _none(KIND_NOT_ESTIMATED, NE_HEDGE_OUTCOME_UNKNOWN,
                         "the second contract's settlement is not known")
        if pay is None:
            return _none(KIND_NOT_ESTIMATED, NE_HELD_OUTCOME_UNKNOWN,
                         "the held contract's settlement is not known")
        xq, _k = _first(a, HEDGE_QTY_KEYS)
        if xq is None:
            return _none(KIND_NOT_ESTIMATED, NE_QTY_UNKNOWN,
                         "the acquisition's quantity is not recorded")
        hq = _num(a.get("held_qty"))
        if hq is None:
            hq = q if a.get("position_includes_uncovered_inventory") else xq
        fees, _fk = _first(a, FEE_KEYS)
        cost = _num(a.get("cost_usd"))
        hcost = _num(a.get("hedge_cost_usd"))
        if fees is None or (cost is None and (hcost is None or b is None)):
            return _none(KIND_NOT_ESTIMATED, NE_COST_UNKNOWN,
                         "the structure's decision-time cost and fees are "
                         "not both recorded")
        if hq is None:
            return _none(KIND_NOT_ESTIMATED, NE_QTY_UNKNOWN,
                         "the held quantity in the structure is not known")
        # `cost_usd` on an indirect candidate is BOTH legs' cost; a record
        # that states only the hedge's cost adds the held basis to it.
        total_cost = cost if cost is not None else hcost + hq * b
        est = hq * pay + xq * hpay - total_cost - fees
        return _est(est, FILL_BASIS_DISPLAYED,
                    "held %.6g x %.6g + hedge %.6g x %.6g - cost %.6g - "
                    "fees %.6g" % (hq, pay, xq, hpay, total_cost, fees),
                    {"held_qty": hq, "held_payout_per_contract": pay,
                     "hedge_contract": slug,
                     "hedge_side": _first_str(a, HEDGE_SIDE_KEYS),
                     "hedge_qty": xq, "hedge_payout_per_contract": hpay,
                     "hedge_payout_source": (hedge or {}).get("source"),
                     "cost_usd": total_cost, "fees_usd": fees})

    return _none(KIND_NOT_ESTIMATED, NE_NO_RULE,
                 "no estimation rule is defined for %r" % action)


def chosen_index(decision: dict, alts: list) -> int | None:
    """Which recorded alternative the decision chose: the one carrying the
    chosen plan digest when the record names one, else the first with the
    chosen action."""
    ch = decision.get("chosen_action")
    if not ch:
        return None
    idx = [i for i, a in enumerate(alts or [])
           if isinstance(a, dict) and a.get("action") == ch]
    dig = decision.get("chosen_plan_digest")
    if dig:
        for i in idx:
            if alts[i].get("plan_digest") == dig:
                return i
    return idx[0] if idx else None


def chosen_alternative(decision: dict) -> dict | None:
    alts = _obj(decision.get("alternatives")) or []
    i = chosen_index(decision, alts)
    return None if i is None else alts[i]


def predicted_of(decision: dict) -> tuple:
    """(predicted expected_net_usd of the chosen action, where it was read)."""
    alt = chosen_alternative(decision)
    v, k = _first(alt or {}, VALUE_KEYS)
    if v is not None:
        return v, "chosen_alternative.%s" % k
    v, k = _first(_obj(decision.get("expected_economics")) or {}, VALUE_KEYS)
    if v is not None:
        return v, "expected_economics.%s" % k
    return None, None


#: Keys that state the HELD side's probability by construction.
HELD_SIDE_PROBABILITY_KEYS = ("held_probability", "hold_probability")
#: Keys whose side convention the record does not state.
UNSIDED_PROBABILITY_KEYS = ("primary_probability", "probability")


def probability_used(decision: dict, *, held_side: str | None = None
                     ) -> tuple:
    """(the HELD side's probability the decision used, where it was read).

    THE SIDE MATTERS. A reliability check of a short position against a
    long-side probability would score the complement. So sources that are
    the held side's by construction come first -- an explicit held
    probability, HOLD's own cash at settlement per contract (its expected
    payout, in the position's space) -- and a key whose side convention the
    record does not state is used only for a LONG held side, where the two
    coincide."""
    ev = _obj(decision.get("evidence")) or {}
    v, k = _first(ev, HELD_SIDE_PROBABILITY_KEYS)
    if v is not None and 0.0 <= v <= 1.0:
        return v, "evidence.%s" % k
    for a in _obj(decision.get("alternatives")) or []:
        if not isinstance(a, dict) or a.get("action") != "HOLD":
            continue
        cs, qq = _num(a.get("cash_at_settlement_usd")), _num(a.get("qty"))
        if cs is not None and qq:
            p = cs / qq
            if 0.0 <= p <= 1.0:
                return p, "alternatives.HOLD.cash_at_settlement_usd/qty"
        v, k = _first(a, HELD_SIDE_PROBABILITY_KEYS)
        if v is not None and 0.0 <= v <= 1.0:
            return v, "alternatives.HOLD.%s" % k
    if held_side == LONG:
        v, k = _first(ev, UNSIDED_PROBABILITY_KEYS)
        if v is not None and 0.0 <= v <= 1.0:
            return v, "evidence.%s" % k
    return None, None


def attribute_cause(decision: dict, *, facts: dict, error,
                    held_side: str | None = None) -> dict:
    """WHERE THE RECORD SAYS THE ERROR CAME FROM, or that it does not say.

    In order: a recorded settlement correction; an order action that was not
    dispatched or filled short (the realised path is not the one forecast);
    a HOLD whose realised path includes later sales or acquisitions; fees
    that differ from the forecast's; a HOLD resolved at settlement (the
    probability); otherwise the record does not say."""
    ch = decision.get("chosen_action")
    alt = chosen_alternative(decision) or {}
    if facts.get("settlement_corrections"):
        return {"cause": C_SETTLEMENT_CORRECTION,
                "evidence": facts["settlement_corrections"]}
    if ch and ch not in HOLD_ACTIONS:
        elig = str(decision.get("execution_eligibility") or "")
        if elig != X.E_DISPATCHED:
            return {"cause": C_EXECUTION,
                    "evidence": {"execution_eligibility": elig,
                                 "why": "the chosen order was not dispatched, "
                                        "so the realised path is the "
                                        "position without it"}}
        # WHAT EXECUTION DID, from the decision's execution events (migration
        # 148 `bettor_xavier_execution_events`, summarised by
        # `bettor_xavier.summarise_events`); never from the decision row,
        # which is immutable and carries no dispatch outcome.
        ex = decision.get("execution") if isinstance(
            decision.get("execution"), dict) else {}
        if ex.get("status") in X.UNRESOLVED_STATUSES:
            return {"cause": C_EXECUTION,
                    "evidence": {"execution_status": ex.get("status"),
                                 "why": "the send's outcome is not "
                                        "established"}}
        filled = _num(ex.get("filled_qty"))
        planned, _k = _first(alt, QTY_KEYS)
        if filled is not None and planned and filled < planned - 1e-9:
            return {"cause": C_EXECUTION,
                    "evidence": {"planned_qty": planned,
                                 "filled_qty": filled,
                                 "execution_status": ex.get("status")}}
        pf, _k = _first(alt, FEE_KEYS)
        rf = _num(facts.get("fees_after_decision_usd"))
        if pf is not None and rf is not None and \
                abs(rf - pf) > FEE_TOLERANCE_USD:
            return {"cause": C_FEES,
                    "evidence": {"forecast_fees_usd": pf,
                                 "realised_fees_usd": rf}}
        if error is not None and abs(error) > FEE_TOLERANCE_USD:
            return {"cause": C_EXECUTION,
                    "evidence": {"why": "the fill's proceeds or cost differed "
                                        "from the plan's"}}
        return {"cause": C_NOT_STATED, "evidence": None}
    if ch in HOLD_ACTIONS:
        if facts.get("later_actions"):
            return {"cause": C_LATER_ACTIONS,
                    "evidence": {"flows_after_decision_by_kind":
                                 facts.get("flows_after_decision_by_kind")}}
        p, src = probability_used(decision, held_side=held_side)
        return {"cause": C_PROBABILITY,
                "evidence": {"probability_used": p, "read_from": src}}
    return {"cause": C_NOT_STATED, "evidence": None}


# ═════════════════════════════════════════════════════════════════════
# 3 · STATISTICS, ONE WEIGHT PER FIXTURE (pure)
# ═════════════════════════════════════════════════════════════════════

def weighted_stats(rows) -> dict:
    """Count, mean error, mean absolute error -- each fixture weighing one.

    `rows` carry `fixture`, `predicted_usd`, `realised_usd`, `error_usd`.
    Error is realised minus predicted: positive means the forecast was too
    pessimistic. The uncertainty is the spread of the per-fixture mean errors,
    because fixtures, not decisions, are the independent examples."""
    rows = [r for r in rows if _num(r.get("error_usd")) is not None]
    fixtures = [str(r["fixture"]) for r in rows]
    nf = len(set(fixtures))
    out: dict[str, Any] = {"decisions": len(rows), "fixtures": nf,
                           "weighting": "ONE_WEIGHT_PER_FIXTURE"}
    if not rows:
        return dict(out, status="NO_REVIEWED_OUTCOMES", mean_error_usd=None,
                    mean_abs_error_usd=None)
    w = fixture_weights(fixtures)
    tw = sum(w)

    def _wm(key, f=lambda v: v):
        return round(sum(wi * f(float(r[key])) for wi, r in zip(w, rows))
                     / tw, 6)

    per_fix: dict = {}
    for wi, r in zip(w, rows):
        per_fix.setdefault(str(r["fixture"]), 0.0)
        per_fix[str(r["fixture"])] += wi * float(r["error_usd"])
    means = list(per_fix.values())
    se = None
    if len(means) >= 2:
        m = sum(means) / len(means)
        var = sum((x - m) ** 2 for x in means) / (len(means) - 1)
        se = round(math.sqrt(var / len(means)), 6)
    n = len(rows)
    out.update(
        mean_error_usd=_wm("error_usd"),
        mean_abs_error_usd=_wm("error_usd", abs),
        mean_predicted_usd=_wm("predicted_usd"),
        mean_realised_usd=_wm("realised_usd"),
        standard_error_of_mean_error_usd=se,
        status=("INSUFFICIENT_EVIDENCE" if nf < MIN_FIXTURES_FOR_A_STATISTIC
                else "MEASURED"),
        decision_weighted_for_reference_only={
            "mean_error_usd": round(sum(float(r["error_usd"]) for r in rows)
                                    / n, 6),
            "mean_abs_error_usd": round(sum(abs(float(r["error_usd"]))
                                            for r in rows) / n, 6)})
    if nf < MIN_FIXTURES_FOR_A_STATISTIC:
        out["why"] = ("%d fixture(s); fewer than %d is reported with its "
                      "counts and not as a measured error"
                      % (nf, MIN_FIXTURES_FOR_A_STATISTIC))
    return out


def per_action_stats(rows) -> dict:
    """Per chosen action and in total, each fixture weighing one."""
    by: dict = {}
    for r in rows:
        by.setdefault(str(r.get("chosen_action") or "NONE"), []).append(r)
    return {"by_chosen_action": {k: weighted_stats(v)
                                 for k, v in sorted(by.items())},
            "all": weighted_stats(rows),
            "error_is": ("realised forward net minus the chosen action's "
                         "predicted expected_net_usd; positive means the "
                         "forecast was too pessimistic")}


def reliability_check(pairs) -> dict:
    """THE DECISION INPUTS AGAINST THE OUTCOMES. Not a calibration verdict.

    `pairs` = (fixture, probability used, held side won). A push or a void is
    not a binary outcome and never reaches here. Brier and bins are
    fixture-weighted."""
    from .learn import metrics as M

    out: dict[str, Any] = {
        "what_this_is": ("a check of the primary probability the decisions "
                         "used against the held side's outcome, fixture-"
                         "weighted. It is NOT a calibration verdict: that is "
                         "external_source_calibration's measurement"),
        "weighting": "ONE_WEIGHT_PER_FIXTURE"}
    pairs = [(str(f), float(p), 1.0 if w else 0.0) for f, p, w in pairs
             if _num(p) is not None and w is not None]
    nf = len({f for f, _, _ in pairs})
    out.update(decisions=len(pairs), fixtures=nf)
    if not pairs:
        return dict(out, status="NO_BINARY_OUTCOMES", brier=None, bins=None)
    w = fixture_weights([f for f, _, _ in pairs])
    ps = [p for _, p, _ in pairs]
    ys = [y for _, _, y in pairs]
    cal = M.calibration(ps, ys, bins=RELIABILITY_BINS, weights=w)
    out.update(brier=round(M.brier(ps, ys, w), 6), bins=cal["bins"],
               expected_calibration_error=round(cal["ece"], 6),
               status=("INSUFFICIENT_EVIDENCE"
                       if nf < MIN_FIXTURES_FOR_A_STATISTIC else "MEASURED"))
    return out


def alternative_summary(rows) -> dict:
    """Per alternative action: how many were estimated, blocked or not
    estimated (by reason), and -- fixture-weighted -- the estimate and how far
    the alternative's OWN decision-time value was from it. That second figure
    is a check of that alternative's forecast, not a verdict on which action
    was right, and it is labelled so."""
    by: dict = {}
    for r in rows:
        by.setdefault(r["action"], []).append(r)
    out = {}
    for action, rs in sorted(by.items()):
        est = [r for r in rs if r["kind"] == KIND_HYPOTHETICAL]
        reasons: dict = {}
        for r in rs:
            if r["kind"] != KIND_HYPOTHETICAL:
                reasons[r["no_estimate_because"]] = \
                    reasons.get(r["no_estimate_because"], 0) + 1
        d: dict[str, Any] = {"rows": len(rs), "estimated": len(est),
                             "blocked": sum(1 for r in rs
                                            if r["kind"] == KIND_BLOCKED),
                             "not_estimated_because": reasons,
                             "estimates_are": KIND_HYPOTHETICAL,
                             "could_have_filled": COULD_HAVE_FILLED}
        if est:
            fx = [str(r["fixture"]) for r in est]
            w = fixture_weights(fx)
            tw = sum(w)
            d["fixtures"] = len(set(fx))
            d["mean_estimate_usd"] = round(sum(
                wi * r["estimate_usd"] for wi, r in zip(w, est)) / tw, 6)
            d["mean_estimate_minus_own_decision_time_value_usd"] = round(sum(
                wi * (r["estimate_usd"] - r["decision_time_value_usd"])
                for wi, r in zip(w, est)) / tw, 6)
            d["reads_as"] = (
                "how far this alternative's own decision-time forecast was "
                "from its labelled estimate. It does not say whether this "
                "action should have been chosen")
        out[action] = d
    return out


def largest_errors(rows, *, limit: int = MAX_ERROR_ROWS) -> dict:
    """The largest forecast errors, one per fixture, with their evidence
    references, and every error grouped by the cause the record states."""
    valid = [r for r in rows if _num(r.get("error_usd")) is not None]
    best: dict = {}
    for r in valid:
        f = str(r["fixture"])
        if f not in best or abs(r["error_usd"]) > abs(best[f]["error_usd"]):
            best[f] = r
    top = sorted(best.values(), key=lambda r: -abs(r["error_usd"]))[:limit]
    by_cause: dict = {}
    for r in valid:
        c = r.get("cause") or C_NOT_STATED
        by_cause.setdefault(c, []).append(r)
    causes = {}
    for c, rs in sorted(by_cause.items()):
        st = weighted_stats(rs)
        causes[c] = {"decisions": st["decisions"], "fixtures": st["fixtures"],
                     "mean_abs_error_usd": st.get("mean_abs_error_usd"),
                     "status": st.get("status")}
    return {"largest": [{k: r.get(k) for k in (
                "xavier_decision_id", "decision_id", "intent_id", "group_id",
                "fixture", "decided_at", "chosen_action", "predicted_usd",
                "predicted_read_from", "realised_usd",
                "realised_whole_position_net_usd", "realised_basis",
                "error_usd", "cause", "cause_evidence", "inputs")}
                        for r in top],
            "one_per_fixture": True, "by_cause": causes,
            "causes_are_only_what_the_record_states": True}


# ═════════════════════════════════════════════════════════════════════
# 4 · READS
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_xavier_reviews')") is not None
    except Exception:                                           # noqa: BLE001
        return False


async def _regclass(conn, name: str) -> bool:
    try:
        return await conn.fetchval("SELECT to_regclass($1)", name) is not None
    except Exception:                                           # noqa: BLE001
        return False


ECONOMICS_SQL = """
    SELECT e.event_id, e.intent_id, e.kind, e.amount_usd::float8 AS amount_usd,
           e.provisional,
           -- A FILL'S ROWS (its cash, its fee, a late fee adjustment) belong
           -- to the fill's instant, not to when the row was written: a fee
           -- reconciled after the decision is still a cost of a fill taken
           -- before it.
           extract(epoch FROM coalesce(f.at, e.at))::float8 AS effective_at
      FROM bettor_funded_economics e
      LEFT JOIN bettor_funded_fills f
        ON e.event_id ~ '^fev:.+:(CASH|FEE|FEE_ADJ)$'
       AND f.fill_id = regexp_replace(e.event_id,
                                      '^fev:(.+):(CASH|FEE|FEE_ADJ)$', '\\1')
     WHERE e.intent_id = ANY($1::text[])
"""


async def read_position(conn, *, intent_id: str, group_id: str | None,
                        corrections_present: bool) -> dict:
    """Every book row one position's outcome is computed from."""
    if group_id:
        legs = [dict(r) for r in await conn.fetch(
            "SELECT intent_id, leg_role, order_intent, us_market_slug, "
            "       event_key, closed_reason, settlement, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "                                      closed_at) AS open "
            "  FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND kind='ENTRY' "
            " ORDER BY leg_role, intent_id", str(group_id))]
    else:
        legs = []
    if not legs:
        legs = [dict(r) for r in await conn.fetch(
            "SELECT intent_id, leg_role, order_intent, us_market_slug, "
            "       event_key, closed_reason, settlement, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "                                      closed_at) AS open "
            "  FROM bettor_funded_intents "
            " WHERE intent_id=$1 AND kind='ENTRY'", str(intent_id))]
    ids = [g["intent_id"] for g in legs]
    if not ids:
        return {"legs": [], "children": [], "fills": [], "economics": [],
                "rechecks": [], "corrections": None}
    children = [dict(r) for r in await conn.fetch(
        "SELECT intent_id, parent_intent_id, "
        "       bettor_funded_intent_is_live(state) AS live "
        "  FROM bettor_funded_intents WHERE parent_intent_id = ANY($1::text[])",
        ids)]
    fills = [dict(r) for r in await conn.fetch(
        "SELECT f.intent_id, coalesce(i.parent_intent_id, i.intent_id) "
        "         AS leg_intent_id, f.direction, f.qty::float8 AS qty, "
        "       f.cash_usd::float8 AS cash_usd, "
        "       extract(epoch FROM f.at)::float8 AS at "
        "  FROM bettor_funded_fills f "
        "  JOIN bettor_funded_intents i ON i.intent_id = f.intent_id "
        " WHERE i.intent_id = ANY($1::text[]) "
        "    OR i.parent_intent_id = ANY($1::text[])", ids)]
    everyone = ids + [c["intent_id"] for c in children]
    economics = [dict(r) for r in await conn.fetch(ECONOMICS_SQL, everyone)]
    rechecks = []
    if await _regclass(conn, "bettor_funded_settlement_rechecks"):
        rechecks = [dict(r) for r in await conn.fetch(
            "SELECT recheck_id, intent_id, verdict, booked_reading, "
            "       booked_payout_price, venue_reading, venue_payout_price, "
            "       extract(epoch FROM read_at)::float8 AS read_at "
            "  FROM bettor_funded_settlement_rechecks "
            " WHERE intent_id = ANY($1::text[]) "
            " ORDER BY read_at, recheck_id", ids)]
    corrections = None
    if corrections_present:
        corrections = [dict(r) for r in await conn.fetch(
            "SELECT * FROM bettor_funded_settlement_corrections "
            " WHERE intent_id = ANY($1::text[])", ids)]
    return {"legs": legs, "children": children, "fills": fills,
            "economics": economics, "rechecks": rechecks,
            "corrections": corrections}


async def market_settlement(conn, slug: str, cache: dict) -> dict:
    """THE VENUE'S LONG-SIDE SETTLEMENT PRICE OF A CONTRACT WE MAY NOT HAVE
    HELD TO THE END: a funded leg on that contract settled by the venue (and
    not contested), else the non-funded observer's labelled settlement of it.
    Unknown is a real answer and blocks the estimate that needs it."""
    if slug in cache:
        return cache[slug]
    got: dict = {"ok": False, "why": "no settlement of this contract is "
                                     "recorded"}
    try:
        r = await conn.fetchrow(
            "SELECT (settlement->>'payout_price')::float8 AS p, intent_id "
            "  FROM bettor_funded_intents i "
            " WHERE us_market_slug=$1 AND kind='ENTRY' "
            "   AND closed_reason='SETTLED_BY_THE_VENUE' "
            "   AND settlement->>'payout_price' IS NOT NULL "
            "   AND NOT EXISTS (SELECT 1 FROM bettor_funded_settlement_rechecks "
            "                    rc WHERE rc.intent_id=i.intent_id "
            "                      AND rc.verdict='DISAGREES') "
            " ORDER BY closed_at DESC NULLS LAST LIMIT 1", str(slug))
        if r is not None and _num(r["p"]) is not None:
            got = {"ok": True, "long_price": float(r["p"]),
                   "source": "FUNDED_LEG_SETTLED_BY_THE_VENUE:%s"
                             % r["intent_id"]}
    except Exception as exc:                                    # noqa: BLE001
        got = {"ok": False, "why": "the funded settlement read failed (%s)"
                                   % type(exc).__name__}
    if not got.get("ok") and await _regclass(conn, "bettor_pair_observations"):
        try:
            r = await conn.fetchrow(
                "SELECT CASE WHEN primary_slug=$1 "
                "            THEN primary_settlement_price "
                "            ELSE hedge_settlement_price END::float8 AS p, "
                "       observation_id "
                "  FROM bettor_pair_observations "
                " WHERE label_status='LABELLED' "
                "   AND (primary_slug=$1 OR hedge_slug=$1) "
                " ORDER BY outcome_available_at DESC LIMIT 1", str(slug))
            if r is not None and _num(r["p"]) is not None:
                got = {"ok": True, "long_price": float(r["p"]),
                       "source": "PAIR_OBSERVATION_LABEL:%s"
                                 % r["observation_id"]}
        except Exception as exc:                                # noqa: BLE001
            got = {"ok": False, "why": "the observation label read failed "
                                       "(%s)" % type(exc).__name__}
    cache[slug] = got
    return got


async def calibration_status(conn) -> dict:
    """THE ODDS SOURCE'S CALIBRATION AS OTHERS MEASURED IT. Read, never
    re-measured: the newest `external_source_calibration` row and the
    scheduled measurement's digest on the cycle heartbeat, each reported
    absent by name when it is."""
    out: dict[str, Any] = {"measured_here": False,
                           "why": ("the review reads the measurement; it "
                                   "does not duplicate it")}
    try:
        from . import bettor_source_calibration as SC
        sv = SC.SOURCE_VERSION
    except Exception:                                           # noqa: BLE001
        sv = None
    out["source_version"] = sv
    if not await _regclass(conn, "external_source_calibration"):
        out["latest_row"] = None
        out["latest_row_absent"] = "EXTERNAL_SOURCE_CALIBRATION_TABLE_ABSENT"
    else:
        try:
            r = await conn.fetchrow(
                "SELECT source_version, sample_size, metric, score, "
                "       tolerance, within_tolerance, measured_by, "
                "       extract(epoch FROM measured_at)::float8 AS measured_at"
                "  FROM external_source_calibration "
                " WHERE ($1::text IS NULL OR source_version=$1) "
                " ORDER BY measured_at DESC LIMIT 1", sv)
            out["latest_row"] = dict(r) if r is not None else None
            if r is None:
                out["latest_row_absent"] = (
                    "EXTERNAL_SOURCE_CALIBRATION_HAS_NO_ROW_FOR_THE_SOURCE")
        except Exception as exc:                                # noqa: BLE001
            out["latest_row"] = None
            out["latest_row_absent"] = ("EXTERNAL_SOURCE_CALIBRATION_READ_"
                                        "FAILED:%s" % type(exc).__name__)
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            "ext_pinnacle_last_cycle")
        beat = _obj(raw) if raw else None
    except Exception as exc:                                    # noqa: BLE001
        beat = None
        out["measurement_digest_absent"] = (
            "THE_CYCLE_HEARTBEAT_COULD_NOT_BE_READ:%s" % type(exc).__name__)
    if isinstance(beat, dict) and beat.get(
            "source_calibration_measurement") is not None:
        out["measurement_digest"] = beat["source_calibration_measurement"]
    else:
        out["measurement_digest"] = None
        out.setdefault("measurement_digest_absent", (
            "SOURCE_CALIBRATION_MEASUREMENT_IS_NOT_ON_THE_CYCLE_HEARTBEAT"))
    return out


async def model_summary(conn) -> dict:
    """THE REGISTRY, AS IT STANDS, AND WHETHER ITS EVIDENCE STILL HOLDS.

    For every model key present: `withdraw_invalidated` (which only removes
    authority), then each CANDIDATE and APPROVED model's stored evaluation
    and whether its training records still reproduce. It never promotes."""
    from . import bettor_funded_model as FMD

    out: dict[str, Any] = {
        "promoted_anything": False,
        "promotion": ("NOT_PERFORMED_BY_THE_REVIEW: a model is approved only "
                      "by promote() with a named approver on prospective "
                      "evidence")}
    if not await FMD.has_schema(conn):
        return dict(out, ok=False, refusal=FMD.R_SCHEMA_UNAVAILABLE)
    keys = [r["model_key"] for r in await conn.fetch(
        "SELECT DISTINCT model_key FROM bettor_funded_models ORDER BY 1")]
    out["keys"] = {}
    for key in keys:
        entry: dict[str, Any] = {}
        try:
            w = await FMD.withdraw_invalidated(conn, model_key=key)
            entry["withdraw"] = {k: w.get(k) for k in (
                "ok", "refusal", "approved_model", "withdrawn", "reason",
                "not_withdrawn_because", "provenance_verified")}
        except Exception as exc:                                # noqa: BLE001
            entry["withdraw"] = {"ok": False, "refusal": "WITHDRAWAL_RAISED",
                                 "error": type(exc).__name__}
        models = []
        for state in (FMD.STATE_APPROVED, FMD.STATE_CANDIDATE):
            rows = await conn.fetch(
                "SELECT * FROM bettor_funded_models "
                " WHERE model_key=$1 AND state=$2 "
                " ORDER BY created_at DESC LIMIT $3", key, state,
                MODELS_PER_KEY_AND_STATE)
            total = await conn.fetchval(
                "SELECT count(*) FROM bettor_funded_models "
                " WHERE model_key=$1 AND state=$2", key, state)
            entry["%s_count" % state.lower()] = int(total or 0)
            for r in rows:
                m = FMD._row(r)
                ev = _obj(m.get("evaluation")) or {}
                pros = ev.get(FMD.EVIDENCE_PROSPECTIVE) or {}
                rep = pros.get("report") or {}
                try:
                    chk = await FMD.verify_provenance(conn, m)
                    repro = {"reproduces": bool(chk.get("ok")),
                             "refusal": chk.get("refusal"),
                             "why": chk.get("why")}
                except Exception as exc:                        # noqa: BLE001
                    repro = {"reproduces": None,
                             "refusal": "VERIFICATION_RAISED",
                             "why": type(exc).__name__}
                models.append({
                    "model_id": m["model_id"],
                    "model_version": m["model_version"], "state": state,
                    "evaluated": bool(ev),
                    "evaluated_at": ev.get("evaluated_at"),
                    "prospective_events": pros.get("n_events"),
                    "prospective_log_loss": pros.get("log_loss"),
                    "baseline_log_loss": (rep.get("baseline") or {}).get(
                        "log_loss"),
                    "contamination": (ev.get("contamination") or {}).get(
                        "verdict"),
                    "promotable_evidence": ev.get("promotable_evidence"),
                    "evidence": repro})
        entry["models"] = models
        out["keys"][key] = entry
    return dict(out, ok=True)


# ═════════════════════════════════════════════════════════════════════
# 5 · THE REVIEW
# ═════════════════════════════════════════════════════════════════════

async def _state(conn) -> dict:
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1", STATE_KEY)
    except Exception:                                           # noqa: BLE001
        return {}
    got = _obj(raw) if raw else None
    return got if isinstance(got, dict) else {}


async def _put_state(conn, value: dict) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        STATE_KEY, json.dumps(value, default=str))


def digest_of(row: dict | None) -> dict | None:
    """The heartbeat's view of one stored review."""
    if not row:
        return None
    inv = _obj(row.get("invalidated"))
    d = row.get("review_date")
    if isinstance(d, str):
        d = _dt.date.fromisoformat(d)
    return {"last_review_date": d.isoformat() if d else None,
            "review_id": row.get("review_id"),
            "computed_at": _epoch(row.get("computed_at")),
            "decisions_reviewed": row.get("decisions_reviewed"),
            "decisions_with_outcomes": row.get("decisions_with_outcomes"),
            "invalidated": (len(inv) if isinstance(inv, list)
                            else row.get("invalidated_count")),
            "next_due_at": next_due_at(d) if d else None}


async def latest_review(conn) -> dict | None:
    if not await has_schema(conn):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM bettor_xavier_reviews ORDER BY review_date DESC LIMIT 1")
    if r is None:
        return None
    out = dict(r)
    for k in ("window", "per_action", "alternatives", "calibration",
              "reliability", "errors", "models", "invalidated"):
        out[k] = _obj(out.get(k))
    return out


async def daily_review(conn, *, now: float, account_id: str | None = None,
                       venue: str | None = None) -> dict:
    """RUN THE REVIEW AT MOST ONCE PER UTC DAY. Never raises.

    The review row's unique `review_date` is the guard: a restart finds it
    and does not re-run, and two processes cannot both write one. A run that
    fails records the failure in the state key and writes no row, so the next
    cycle tries again."""
    day = utc_date(now)
    out: dict[str, Any] = {"version": VERSION, "review_date": day.isoformat(),
                           "ran": False, "never_promotes": True}
    try:
        if not await has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA)
        state = await _state(conn)
        done = await conn.fetchrow(
            "SELECT review_id, review_date, computed_at, decisions_reviewed, "
            "       decisions_with_outcomes, "
            "       coalesce((\"window\"->>'invalidated_total')::int, "
            "                jsonb_array_length(invalidated)) "
            "         AS invalidated_count "
            "  FROM bettor_xavier_reviews WHERE review_date=$1", day)
        if done is not None:
            return dict(out, ok=True, refusal=R_ALREADY_REVIEWED,
                        digest=digest_of(dict(done)),
                        state_key_agrees=(state.get("review_date")
                                          == day.isoformat()))
        got = await _review(conn, now=float(now), day=day,
                            account_id=account_id, venue=venue)
    except Exception as exc:                                    # noqa: BLE001
        err = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        try:
            st = await _state(conn)
            st["last_failure"] = {"at": float(now), "error": err,
                                  "review_date": day.isoformat()}
            await _put_state(conn, st)
        except Exception:                                       # noqa: BLE001
            # THE FAILURE IS STILL RETURNED to the cycle, which puts it on the
            # heartbeat; only its copy in the state key is lost.
            pass
        return dict(out, ok=False, refusal=R_RAISED, error=err,
                    retry=("the next cycle retries: no review row was "
                           "written for %s" % day.isoformat()))
    return dict(out, **got)


async def _review(conn, *, now: float, day, account_id, venue) -> dict:
    window = {"decided_from_epoch": now - WINDOW_DAYS * 86400.0,
              "decided_through_epoch": now, "days": WINDOW_DAYS,
              "account_id": account_id, "venue": venue}
    decisions: list = []
    if await X.has_schema(conn):
        args: list = [X._dt(window["decided_from_epoch"]), X._dt(now)]
        where = ["decided_at > $1", "decided_at <= $2"]
        if account_id:
            args.append(str(account_id))
            where.append("account_id = $%d" % len(args))
        if venue:
            args.append(str(venue))
            where.append("venue = $%d" % len(args))
        args.append(MAX_DECISIONS + 1)
        decisions = [X._row(r) for r in await conn.fetch(
            "SELECT * FROM bettor_xavier_decisions WHERE "
            + " AND ".join(where) + " ORDER BY decided_at DESC LIMIT $%d"
            % len(args), *args)]
        window["decision_table"] = "PRESENT"
    else:
        window["decision_table"] = X.R_SCHEMA
    window["truncated_at"] = (MAX_DECISIONS if len(decisions) > MAX_DECISIONS
                              else None)
    decisions = decisions[:MAX_DECISIONS]
    window["decisions_read"] = len(decisions)
    # WHAT EXECUTION DID WITH EACH, derived from its append-only events on
    # this read (the decision row itself never records an outcome).
    if decisions:
        try:
            execs = await X._executions_for(
                conn, [d["xavier_decision_id"] for d in decisions])
            window["execution_events"] = ("PRESENT" if await
                                          X.has_event_schema(conn)
                                          else "ABSENT")
        except Exception as exc:                                # noqa: BLE001
            execs = {}
            window["execution_events"] = ("UNREADABLE:%s"
                                          % type(exc).__name__)
        for d in decisions:
            d["execution"] = execs.get(d["xavier_decision_id"])

    corrections_present = await _regclass(
        conn, "bettor_funded_settlement_corrections")
    window["settlement_corrections_table"] = (
        "PRESENT" if corrections_present else "ABSENT")
    # ── ONE BOOK READ PER POSITION ─────────────────────────────────────
    group_of: dict = {}
    positions: dict = {}
    for d in decisions:
        gid = d.get("portfolio_group_id")
        if not gid and d["intent_id"] not in group_of:
            gid = await conn.fetchval(
                "SELECT portfolio_group_id FROM bettor_funded_intents "
                " WHERE intent_id=$1", d["intent_id"])
        group_of[d["intent_id"]] = gid or group_of.get(d["intent_id"])
        key = group_of[d["intent_id"]] or ("intent:" + d["intent_id"])
        if key in positions:
            continue
        try:
            positions[key] = await read_position(
                conn, intent_id=d["intent_id"],
                group_id=group_of[d["intent_id"]],
                corrections_present=corrections_present)
        except Exception as exc:                                # noqa: BLE001
            positions[key] = {"unreadable": "%s: %s" % (
                type(exc).__name__, str(exc)[:160])}

    slug_cache: dict = {}
    rows, alt_rows, invalidated, pairs = [], [], [], []
    for d in decisions:
        key = group_of[d["intent_id"]] or ("intent:" + d["intent_id"])
        pos = positions[key]
        t = _epoch(d.get("decided_at"))
        base: dict[str, Any] = {
            "xavier_decision_id": d["xavier_decision_id"],
            "decision_id": d.get("decision_id"),
            "intent_id": d["intent_id"], "group_id": group_of[d["intent_id"]],
            "decided_at": t, "chosen_action": d.get("chosen_action"),
            "execution_eligibility": d.get("execution_eligibility")}
        if pos.get("unreadable"):
            rows.append(dict(base, fixture=d["intent_id"],
                             outcome_status=O_UNREADABLE,
                             error=pos["unreadable"]))
            continue
        me = next((g for g in pos["legs"]
                   if g["intent_id"] == d["intent_id"]),
                  (pos["legs"] or [None])[0])
        fixture = str((me or {}).get("event_key") or d.get("us_market_slug")
                      or d["intent_id"])
        facts = position_facts(
            legs=pos["legs"], children=pos["children"], fills=pos["fills"],
            economics=pos["economics"], rechecks=pos["rechecks"],
            corrections=pos["corrections"], decided_at=t)
        row = dict(base, fixture=fixture, outcome_status=facts["status"])
        if facts["status"] == O_INVALIDATED:
            invalidated.append(dict(base, fixture=fixture,
                                    **facts["invalidation"]))
            rows.append(row)
            continue
        if facts["status"] != O_AUTHORITATIVE:
            rows.append(row)
            continue
        pred, pred_src = predicted_of(d)
        realised = facts["realised_forward_net_usd"]
        err = None if pred is None else round(realised - pred, 6)
        side = (me or {}).get("order_intent")
        cause = attribute_cause(d, facts=facts, error=err, held_side=side)
        p_used, p_src = probability_used(d, held_side=side)
        row.update(
            predicted_usd=pred, predicted_read_from=pred_src,
            realised_usd=realised,
            realised_whole_position_net_usd=facts[
                "realised_whole_position_net_usd"],
            realised_basis=facts["realised_basis"],
            error_usd=err, cause=cause["cause"],
            cause_evidence=cause["evidence"],
            inputs={"probability_used": p_used,
                    "probability_read_from": p_src,
                    "probability_source": (_obj(d.get("evidence")) or {}).get(
                        "probability_source"),
                    "model_version": (_obj(d.get("evidence")) or {}).get(
                        "model_version"),
                    "execution_eligibility": d.get("execution_eligibility"),
                    "execution": d.get("execution")},
            settlement_corrections=facts.get("settlement_corrections"))
        rows.append(row)

        # ── THE HELD LEG'S OUTCOME, for HOLD's estimate and the check ──
        held = {"qty": None, "basis_per_contract": None,
                "payout_per_contract": None, "payout_source": None}
        held_lookup = None
        if me is not None:
            b = facts["basis_at_decision"].get(me["intent_id"]) or {}
            held.update(qty=b.get("held_qty_at_decision"),
                        basis_per_contract=b.get("basis_per_contract"))
            ls = leg_settlement(me, corrections=pos["corrections"],
                                basis_per_contract=b.get(
                                    "basis_per_contract"))
            if not ls.get("ok") and me.get("us_market_slug"):
                ms = await market_settlement(conn, me["us_market_slug"],
                                             slug_cache)
                if ms.get("ok"):
                    ls = {"ok": True, "long_price": ms["long_price"],
                          "payout_per_contract": side_payout(
                              ms["long_price"], me.get("order_intent")),
                          "won": side_won(ms["long_price"],
                                          me.get("order_intent")),
                          "source": ms["source"]}
            if ls.get("ok"):
                held.update(payout_per_contract=ls.get("payout_per_contract"),
                            payout_source=ls.get("source"))
                if p_used is not None and ls.get("won") is not None:
                    pairs.append((fixture, p_used, ls["won"]))
            else:
                # WHY IT IS UNKNOWN travels with every estimate it blocks: a
                # failed read is not the same finding as "never settled".
                held_lookup = {"booked": ls.get("why"),
                               "market": (slug_cache.get(
                                   me.get("us_market_slug")) or {}).get(
                                       "why")}
        # ── EVERY NOT-TAKEN ALTERNATIVE, LABELLED ──────────────────────
        alts = _obj(d.get("alternatives")) or []
        chosen_at = chosen_index(d, alts)
        for i, a in enumerate(alts):
            if not isinstance(a, dict) or i == chosen_at:
                continue
            hedge = None
            ms = None
            slug = _first_str(a, HEDGE_SLUG_KEYS)
            if a.get("action") in ACQUIRE_ACTIONS and slug:
                ms = await market_settlement(conn, slug, slug_cache)
                side = _first_str(a, HEDGE_SIDE_KEYS)
                if ms.get("ok") and side_payout(ms["long_price"], side) \
                        is not None:
                    hedge = {"payout_per_contract": side_payout(
                        ms["long_price"], side), "source": ms["source"]}
            est = hypothetical_estimate(a, held=held, hedge=hedge)
            if est.get("no_estimate_because") == NE_HELD_OUTCOME_UNKNOWN:
                est["settlement_lookup"] = held_lookup
            elif est.get("no_estimate_because") == NE_HEDGE_OUTCOME_UNKNOWN:
                est["settlement_lookup"] = (ms or {}).get("why")
            alt_rows.append(dict(
                est, xavier_decision_id=d["xavier_decision_id"],
                intent_id=d["intent_id"], group_id=group_of[d["intent_id"]],
                fixture=fixture, decided_at=t,
                chosen_action=d.get("chosen_action"),
                chosen_realised_forward_net_usd=realised))

    valid = [r for r in rows if r.get("outcome_status") == O_AUTHORITATIVE]
    per_action = per_action_stats([r for r in valid
                                   if r.get("error_usd") is not None])
    counts: dict = {}
    for r in rows:
        counts[r.get("outcome_status")] = \
            counts.get(r.get("outcome_status"), 0) + 1
    per_action["outcome_status_counts"] = counts
    per_action["decisions_with_outcomes_but_no_forecast"] = sum(
        1 for r in valid if r.get("predicted_usd") is None)

    # STORED ROWS: every not-taken alternative of the NEWEST reviewed decision
    # of each position first, then older ones, up to the bound. The summary
    # counts every row whether stored or not.
    newest: dict = {}
    for r in valid:
        k = r["group_id"] or r["intent_id"]
        if k not in newest or r["decided_at"] > newest[k]:
            newest[k] = r["decided_at"]
    alt_rows.sort(key=lambda r: (
        0 if newest.get(r["group_id"] or r["intent_id"]) == r["decided_at"]
        else 1, -(r["decided_at"] or 0)))
    alternatives = {
        "label": KIND_HYPOTHETICAL,
        "could_have_filled": COULD_HAVE_FILLED,
        "not_a_result": NOT_A_RESULT,
        "no_verdict_on_which_action_was_right": True,
        "by_action": alternative_summary(alt_rows),
        "rows": alt_rows[:MAX_ALTERNATIVE_ROWS],
        "rows_total": len(alt_rows),
        "rows_truncated_at": (MAX_ALTERNATIVE_ROWS
                              if len(alt_rows) > MAX_ALTERNATIVE_ROWS
                              else None)}
    reliability = reliability_check(pairs)
    errors = largest_errors(valid)
    calibration = await calibration_status(conn)
    try:
        models = await model_summary(conn)
    except Exception as exc:                                    # noqa: BLE001
        models = {"ok": False, "refusal": "MODEL_SUMMARY_RAISED",
                  "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                  "promoted_anything": False}
    inv_rows = invalidated[:MAX_INVALIDATED_ROWS]
    window["invalidated_total"] = len(invalidated)
    window["invalidated_truncated_at"] = (
        MAX_INVALIDATED_ROWS if len(invalidated) > MAX_INVALIDATED_ROWS
        else None)
    reviewed = len(rows)
    with_outcomes = len(valid)
    total = per_action["all"]
    summary = (
        "%s: %d Xavier decision(s) read over %d day(s); %d with an "
        "authoritative outcome across %d fixture(s); %d invalidated by a "
        "settlement the venue now contradicts. Forecast error (realised "
        "minus predicted, one weight per fixture): %s. %d not-taken "
        "alternative(s) labelled %s (could_have_filled %s). Nothing was "
        "promoted." % (
            day.isoformat(), reviewed, WINDOW_DAYS, with_outcomes,
            total["fixtures"], len(invalidated),
            ("mean %.4f, mean absolute %.4f USD, %s" % (
                total["mean_error_usd"], total["mean_abs_error_usd"],
                total["status"]) if total.get("mean_error_usd") is not None
             else total["status"]),
            len(alt_rows), KIND_HYPOTHETICAL, COULD_HAVE_FILLED))
    review_id = "xrev:%s" % day.isoformat()
    doc = {"review_id": review_id, "review_date": day,
           "computed_at": X._dt(now), "window": window,
           "decisions_reviewed": reviewed,
           "decisions_with_outcomes": with_outcomes,
           "per_action": per_action, "alternatives": alternatives,
           "calibration": calibration, "reliability": reliability,
           "errors": errors, "models": models, "invalidated": inv_rows,
           "summary": summary}
    try:
        wrote = await conn.fetchval(
            "INSERT INTO bettor_xavier_reviews (review_id, review_date, "
            " computed_at, review_version, \"window\", decisions_reviewed, "
            " decisions_with_outcomes, per_action, alternatives, calibration, "
            " reliability, errors, models, invalidated, summary) VALUES "
            " ($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,"
            "  $11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15) "
            " ON CONFLICT (review_date) DO NOTHING RETURNING review_id",
            review_id, day, X._dt(now), VERSION,
            json.dumps(window, default=str), reviewed, with_outcomes,
            json.dumps(per_action, default=str),
            json.dumps(alternatives, default=str),
            json.dumps(calibration, default=str),
            json.dumps(reliability, default=str),
            json.dumps(errors, default=str), json.dumps(models, default=str),
            json.dumps(inv_rows, default=str), summary)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_WRITE_FAILED,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "retry": "the next cycle retries"}
    if wrote is None:
        # ANOTHER WRITER GOT THERE FIRST. Its row stands; this one is dropped.
        return {"ok": True, "refusal": R_ALREADY_REVIEWED,
                "why": "another process wrote this day's review first"}
    dig = digest_of(dict(doc, invalidated=invalidated))
    # THE ROW IS WRITTEN AND IS THE GUARD. A state key that cannot be written
    # now is reported, not raised: raising here would report "no review row
    # was written" for a day whose row exists.
    try:
        await _put_state(conn, {"review_date": day.isoformat(),
                                "review_id": review_id, "computed_at": now,
                                "decisions_reviewed": reviewed,
                                "decisions_with_outcomes": with_outcomes,
                                "invalidated": len(invalidated),
                                "next_due_at": next_due_at(day)})
        state_written = True
    except Exception as exc:                                    # noqa: BLE001
        state_written = "NOT_WRITTEN:%s" % type(exc).__name__
    return {"ok": True, "refusal": None, "ran": True, "review_id": review_id,
            "digest": dig, "summary": summary, "state_key_written":
            state_written, "models_promoted": False}


def describe() -> dict:
    return {"version": VERSION, "table": "bettor_xavier_reviews",
            "cadence": "at most once per UTC day, from the scheduled cycle",
            "writes": ["its own review row", STATE_KEY,
                       "withdraw_invalidated (removes authority only)"],
            "never": ["fits", "promotes", "trades",
                      "says an alternative would have made money"],
            "alternatives_are": KIND_HYPOTHETICAL,
            "could_have_filled": COULD_HAVE_FILLED,
            "weighting": "ONE_WEIGHT_PER_FIXTURE"}
