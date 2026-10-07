"""XAVIER'S MANAGEMENT LOOP: PROMPT REVIEWS, EXPIRING THESES, A SHADOW
REALLOCATION, AND THE VALUE XAVIER ADDS AGAINST COUNTERFACTUALS FROZEN AT
ENTRY. Records and recommendations only -- this module places, cancels and
changes NO order (paper or real) and grants no authority.

THE LOOP (diagnosis and fix; see also paper_xavier.step and
execmirror.Mirror.xavier_live_reviews).
  paper    every new position is reviewed in the pass that hands it to
           Xavier (FIRST_FILL), due reviews go first (first, fill, market,
           backstop; then most overdue), and Xavier's step has a reserved
           budget -- before, the shared 20 s pass budget was often spent by
           the decision steps that run first, so the step reviewed nothing,
           and groups were visited in group_id order, so the same ones were
           starved pass after pass.
  actual   each OPEN live handoff is reviewed on the tick it appears
           (FIRST), on a change of held quantity (FILL_EVENT) and every
           `execmirror.MANAGEMENT_EVERY_S` (cadence) -- before, one
           process-wide 60 s timer reviewed all positions or none, any
           failing review stopped the rest, and NO review ran while the
           lane was disabled or stopped although positions remained.
  Every review records its trigger, the instant it was due, its latency
  and whether that is within the bound (`PAPER_FIRST_REVIEW_BOUND_S`,
  `ACTUAL_FIRST_REVIEW_BOUND_S`, the cadence for re-reviews).

THE EVIDENCE. Each review attempts a FRESH PinnAPI probability through the
existing measure (paper_benchmark.xavier_measure: the latest
external_valuations row for the exact contract and payout outcome within the
30 s Pinnacle rule, else the in-process PinnAPI feed, else the entry
decision's, labelled stale) and reads executable venue economics (the
observed book / the venue BBO). The state is exactly one of
FRESH_CURRENT_PROBABILITY / STALE_ENTRY_TIME_PROBABILITY /
PROBABILITY_UNAVAILABLE; nothing is invented. No discretionary action
(EXIT / REDUCE / REALLOCATE) is ever recommended on a stale or absent
probability (and migration 206 CHECKs it).

THE THESIS (upgrade B). At entry (the first fill / handoff -- never later,
so nothing is written with hindsight) an immutable thesis records the entry
probability and its source stamp, the EV, assumptions, evidence references
and the expiry: the probability is current evidence until its source stamp
+ the Pinnacle limit (30 s); a pre-event thesis also expires at the event
start. Each review classifies it:
  STILL_VALID       a fresh probability confirms it (edge keeps its sign and
                    moved by at most THESIS_P_TOLERANCE), or the entry
                    evidence is still inside its own 30 s life
  THESIS_CHANGED    a fresh probability moved beyond the tolerance or
                    flipped the edge's sign (direction stated)
  EVIDENCE_EXPIRED  no fresh probability and the entry evidence (or the
                    pre-event horizon) has expired -- no discretionary
                    action is taken on it
  NO_ENTRY_THESIS   the position predates the thesis record

REALLOCATE (upgrade C), SHADOW ONLY. The position's capital efficiency --
expected value from here per dollar it would free per hour to resolution,
(q p - L) / L / h with L the executable liquidation value -- against the
best other currently qualified opportunity: ENTER decisions of an investment
strategy within the 30 s rule that are not held (EV / capital / h).
Recommended only on fresh evidence and a clear margin; never an order.

VALUE-ADD (upgrade H). Frozen at entry on the thesis: HOLD_TO_SETTLEMENT,
IMMEDIATE_EXIT (at the entry-time executable exit) and ACTUAL_XAVIER (what
management did). When the outcome is known (exit / settlement) the P&L,
drawdown (over the marks the reviews recorded), fees and turnover of each are
computed by those predeclared rules and persisted; no policy is selected in
hindsight. `value_add()` and `management_view()` read them.
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from typing import Any

from .. import bettor_paper_ledger as L
from .. import xavier_freshness as XF

VERSION = "XAVIER_MANAGEMENT_V1"
K_PAPER, K_ACTUAL = "PAPER", "ACTUAL"
E_FRESH = "FRESH_CURRENT_PROBABILITY"
E_STALE = "STALE_ENTRY_TIME_PROBABILITY"
E_NONE = "PROBABILITY_UNAVAILABLE"
EVIDENCE_STATES = (E_FRESH, E_STALE, E_NONE)
TH_VALID, TH_CHANGED = "STILL_VALID", "THESIS_CHANGED"
TH_EXPIRED, TH_NONE = "EVIDENCE_EXPIRED", "NO_ENTRY_THESIS"
THESIS_STATES = (TH_VALID, TH_CHANGED, TH_EXPIRED, TH_NONE)
A_HOLD, A_EXIT, A_REDUCE = "HOLD", "EXIT", "REDUCE"
A_HEDGE, A_REALLOCATE = "VERIFIED_HEDGE", "REALLOCATE"
DISCRETIONARY = (A_EXIT, A_REDUCE, A_REALLOCATE)
CF_HOLD, CF_EXIT, CF_ACTUAL = ("HOLD_TO_SETTLEMENT", "IMMEDIATE_EXIT",
                               "ACTUAL_XAVIER")
COUNTERFACTUALS = (CF_HOLD, CF_EXIT, CF_ACTUAL)
T_FIRST, T_FILL = "FIRST_FILL", "FILL_EVENT"
T_MARKET, T_BACKSTOP = "MARKET_EVENT", "SCHEDULED_BACKSTOP"

#: THE BOUNDS. A paper position is handed off and reviewed in the same pass
#: as its first simulated fill; two servicing passes (60 s each) bound it.
#: An actual position is reviewed on the mirror tick that hands it off
#: (TICK_S = 2 s after the venue fill is read); 30 s bounds it.
PAPER_FIRST_REVIEW_BOUND_S = 120.0
ACTUAL_FIRST_REVIEW_BOUND_S = 30.0
#: A re-review is within bound when it lands within one more cadence of due.
REREVIEW_GRACE_FACTOR = 1.0
THESIS_P_TOLERANCE = 0.05
#: A thesis is written only AT ENTRY: within this of the first fill. A
#: position found later is NO_ENTRY_THESIS -- never back-filled.
THESIS_ENTRY_WINDOW_S = 300.0
#: REALLOCATE: an opportunity is "currently qualified" only inside the
#: Pinnacle rule; it must beat the position's efficiency by this fraction.
REALLOCATE_LOOKBACK_S = 30.0
REALLOCATE_MIN_ADVANTAGE = 0.25
INVESTMENT_STRATEGIES = ("PINNACLE_COMPLETED_GAME_PAPER",
                         "DEREK_ENTRY_POLICY_V2")
DEFAULT_HORIZON_H = 3.0
DEFAULT_EVENT_DURATION_H = 3.0
MIN_HORIZON_H = 0.25
B_STALE = "MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE"
B_NO_HEDGE_PROOF = ("NO_HEDGE_WITH_PROVEN_SETTLEMENT_COMPATIBILITY_"
                    "SEARCH_NOT_RUN_ON_THIS_PATH")
B_NO_LIQUIDATION = "NO_EXECUTABLE_EXIT_TO_FREE_CAPITAL"
B_NO_OPPORTUNITY = "NO_OTHER_CURRENTLY_QUALIFIED_OPPORTUNITY"
B_NO_EDGE = "NO_QUALIFIED_OPPORTUNITY_BEATS_THE_POSITION_BY_THE_MARGIN"


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(v, nd=6):
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _dumps(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def _sha(v) -> str:
    return hashlib.sha256(_dumps(v).encode()).hexdigest()


def _ts(v):
    """An epoch as the argument of to_timestamp(), or None."""
    return None if v is None else float(v)


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('xavier_management_assessments') IS NOT NULL"
            " AND to_regclass('xavier_entry_theses') IS NOT NULL"
            " AND to_regclass('xavier_value_add') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def has_valuation_columns(conn) -> bool:
    """Migration 222 applied: the assessment carries its valuation and its
    write-time recommendation state."""
    try:
        return int(await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_name = 'xavier_management_assessments' "
            "   AND column_name IN ('valuation', 'recommendation_state')"
            "   AND table_schema = current_schema()") or 0) == 2
    except Exception:                                           # noqa: BLE001
        return False


def collateral(side: str, long_price) -> float | None:
    """Per-contract cost (and exit proceeds) of the held side from a LONG
    (wire) price: a long pays the price, a short its complement."""
    if long_price is None:
        return None
    p = float(long_price)
    return p if side == "LONG" else 1.0 - p


def side_of_intent(intent) -> str:
    return "LONG" if str(intent or "").endswith("BUY_LONG") else "SHORT"


# ═════════════════════════════════════════════════════════════════════
# THE LOOP'S BOUNDS (pure)
# ═════════════════════════════════════════════════════════════════════

def latency(*, kind: str, trigger: str, at: float, due_at,
            cadence_s: float) -> dict:
    """When the review was due, how late it is and whether that is within
    the bound: the first review against the first-review bound, a re-review
    against one more cadence."""
    due = _epoch(due_at)
    lat = None if due is None else round(float(at) - due, 3)
    if trigger == T_FIRST:
        bound = (PAPER_FIRST_REVIEW_BOUND_S if kind == K_PAPER
                 else ACTUAL_FIRST_REVIEW_BOUND_S)
    else:
        bound = float(cadence_s) * REREVIEW_GRACE_FACTOR
    return {"trigger": trigger, "due_at": due, "review_latency_s": lat,
            "latency_bound_s": bound,
            "within_bound": None if lat is None else lat <= bound,
            "cadence_s": float(cadence_s)}


# ═════════════════════════════════════════════════════════════════════
# THE THESIS (pure)
# ═════════════════════════════════════════════════════════════════════

def expiry(*, entered_at, probability_source_at, limit_s: float,
           event_start_at) -> dict:
    """The probability is current evidence until its OWN source stamp +
    the Pinnacle limit; a pre-event thesis also expires at the event
    start. Pure."""
    ent = _epoch(entered_at)
    src = _epoch(probability_source_at)
    start = _epoch(event_start_at)
    ev_exp = None if src is None else src + float(limit_s)
    if start is not None and ent is not None and start > ent:
        return {"evidence_expires_at": ev_exp, "thesis_expires_at": start,
                "expiry_basis": (
                    "EVENT_START: a pre-event thesis expires when play "
                    "begins; its entry probability is current evidence only "
                    "until its source stamp + %.0f s (Pinnacle rule)"
                    % float(limit_s))}
    return {"evidence_expires_at": ev_exp, "thesis_expires_at": None,
            "expiry_basis": (
                "PINNACLE_30S_RULE: the entry probability is current "
                "evidence until its source stamp + %.0f s; after that the "
                "thesis holds only while a fresh probability re-confirms it "
                "(%s)" % (float(limit_s), "event already started at entry"
                          if start is not None else
                          "no event start on record"))}


def counterfactual_plan(*, qty, entry_cost_usd, entry_fees_usd,
                        entry_price_per_contract, p, exit_walk: dict | None,
                        exit_basis: dict) -> dict:
    """THE THREE PREDECLARED COUNTERFACTUALS, frozen at entry. Pure."""
    q = float(qty)
    cost = float(entry_cost_usd)
    hold = {"rule": ("hold the entry inventory to settlement: pnl = "
                     "entry_qty x settlement payout per contract - entry "
                     "cost (a void refunds the entry price, fees not "
                     "refunded)"),
            "entry_qty": q, "entry_cost_usd": round(cost, 6),
            "entry_fees_usd": _f(entry_fees_usd),
            "entry_price_per_contract": _f(entry_price_per_contract),
            "expected_pnl_at_entry_usd": (None if p is None else
                                          round(q * float(p) - cost, 6)),
            "turnover_usd": round(cost, 6)}
    w = exit_walk or {}
    sold = float(w.get("sold") or 0.0)
    ex = {"rule": ("sell the entry inventory into the ENTRY-TIME executable "
                   "bids (walked depth, fees by the fee schedule); any "
                   "quantity the book could not absorb is held to "
                   "settlement; pnl = net proceeds + unsold x payout - "
                   "entry cost"),
          "available": sold > 0,
          "basis": exit_basis,
          "sold_qty": round(sold, 6),
          "unsold_qty": round(q - sold, 6),
          "exit_proceeds_usd": _f(w.get("proceeds_usd")),
          "exit_fees_usd": _f(w.get("fees_usd")),
          "exit_vwap": (None if sold <= 0 else
                        round(float(w.get("proceeds_usd") or 0) / sold, 6)),
          "pnl_if_fully_sold_usd": (
              None if sold <= 0 or sold + 1e-9 < q else round(
                  float(w["proceeds_usd"]) - float(w["fees_usd"]) - cost,
                  6)),
          "why_unavailable": (None if sold > 0 else
                              "no executable exit depth at entry")}
    act = {"rule": ("what Xavier's management actually did: every fill of "
                    "the position (entry, sales, protection) and the "
                    "settlement of whatever remained"),
           "computed_from": "fills + settlement of this contract and side"}
    return {CF_HOLD: hold, CF_EXIT: ex, CF_ACTUAL: act,
            "selection": "PREDECLARED_AT_ENTRY_NEVER_CHOSEN_IN_HINDSIGHT"}


def build_thesis(*, kind: str, group_id: str, position_ref: str,
                 decision_id, strategy, slug: str, holding_side: str,
                 entered_at, first_fill_at, qty, entry_cost_usd,
                 entry_fees_usd, entry_price_per_contract, p,
                 probability_source, probability_source_at, limit_s,
                 event_start_at, assumptions: dict, evidence_refs: list,
                 exit_walk: dict | None, exit_basis: dict) -> dict:
    """ONE IMMUTABLE ENTRY THESIS. Pure."""
    q = float(qty)
    cost = float(entry_cost_usd)
    per = cost / q if q > 0 else None
    ev = None if p is None else round(q * float(p) - cost, 6)
    exp = expiry(entered_at=entered_at,
                 probability_source_at=probability_source_at,
                 limit_s=limit_s, event_start_at=event_start_at)
    body = {
        "thesis_id": "xth:%s:%s" % (kind.lower(), group_id),
        "position_kind": kind, "group_id": group_id,
        "position_ref": position_ref, "decision_id": decision_id,
        "strategy": strategy, "us_market_slug": slug,
        "holding_side": holding_side, "entered_at": _epoch(entered_at),
        "first_fill_at": _epoch(first_fill_at), "entry_qty": q,
        "entry_cost_per_contract": None if per is None else round(per, 6),
        "entry_cost_usd": round(cost, 6),
        "entry_fees_usd": _f(entry_fees_usd),
        "entry_probability": None if p is None else float(p),
        "probability_source": probability_source if p is not None else None,
        "probability_source_at": _epoch(probability_source_at),
        "probability_limit_s": float(limit_s),
        "entry_ev_per_contract_usd": (None if ev is None or q <= 0
                                      else round(ev / q, 6)),
        "entry_ev_usd": ev,
        "assumptions": assumptions, "evidence_refs": evidence_refs,
        "event_start_at": _epoch(event_start_at), **exp,
        "counterfactuals": counterfactual_plan(
            qty=q, entry_cost_usd=cost, entry_fees_usd=entry_fees_usd,
            entry_price_per_contract=entry_price_per_contract, p=p,
            exit_walk=exit_walk, exit_basis=exit_basis)}
    body["content_sha256"] = _sha(body)
    return body


def classify_thesis(thesis: dict | None, *, evidence: dict,
                    at: float) -> dict:
    """STILL_VALID / THESIS_CHANGED / EVIDENCE_EXPIRED / NO_ENTRY_THESIS.
    Pure. A stale or absent probability never changes a thesis -- it can
    only leave it unconfirmed (expired)."""
    if not thesis:
        return {"state": TH_NONE,
                "why": ("no entry thesis on record for this position (it "
                        "predates the thesis record or was found after "
                        "entry; never back-filled)")}
    state = evidence.get("evidence_state")
    p_now = evidence.get("probability")
    p0 = thesis.get("entry_probability")
    cost = thesis.get("entry_cost_per_contract")
    ev_exp = _epoch(thesis.get("evidence_expires_at"))
    th_exp = _epoch(thesis.get("thesis_expires_at"))
    horizon_passed = th_exp is not None and float(at) >= th_exp
    base = {"thesis_id": thesis.get("thesis_id"), "entry_probability": p0,
            "entry_cost_per_contract": cost,
            "evidence_expires_at": ev_exp, "thesis_expires_at": th_exp,
            "horizon_passed": horizon_passed,
            "tolerance_p": THESIS_P_TOLERANCE}
    if state == E_FRESH and p_now is not None:
        p_now = float(p_now)
        edge_now = None if cost is None else round(p_now - float(cost), 6)
        edge_0 = (None if cost is None or p0 is None
                  else round(float(p0) - float(cost), 6))
        delta = None if p0 is None else round(p_now - float(p0), 6)
        flipped = (edge_now is not None and edge_0 is not None
                   and (edge_now > 0) != (edge_0 > 0))
        changed = p0 is None or flipped or abs(delta) > THESIS_P_TOLERANCE
        return dict(base, state=TH_CHANGED if changed else TH_VALID,
                    basis="FRESH_PROBABILITY_AT_REVIEW",
                    current_probability=p_now, delta_p=delta,
                    edge_now_per_contract=edge_now,
                    edge_at_entry_per_contract=edge_0,
                    ev_sign_flipped=flipped,
                    direction=(None if delta is None or not changed else
                               "IMPROVED" if delta > 0 else "DETERIORATED"))
    if p0 is not None and ev_exp is not None and float(at) < ev_exp \
            and not horizon_passed:
        return dict(base, state=TH_VALID,
                    basis="ENTRY_EVIDENCE_WITHIN_ITS_OWN_FRESHNESS_LIFE",
                    current_probability=None)
    return dict(base, state=TH_EXPIRED, current_probability=None,
                basis=state,
                why=("no fresh probability at review (%s) and the entry "
                     "evidence expired at %s%s; nothing discretionary is "
                     "done on it" % (
                         state, ev_exp, " (pre-event horizon passed)"
                         if horizon_passed else "")))


# ═════════════════════════════════════════════════════════════════════
# REALLOCATE (pure, SHADOW)
# ═════════════════════════════════════════════════════════════════════

def horizon_h(*, at: float, event_start_at=None) -> dict:
    start = _epoch(event_start_at)
    if start is not None:
        end = start + DEFAULT_EVENT_DURATION_H * 3600.0
        h = max((end - float(at)) / 3600.0, MIN_HORIZON_H)
        return {"hours": round(h, 4),
                "basis": "event start + %.0f h assumed duration"
                         % DEFAULT_EVENT_DURATION_H}
    return {"hours": DEFAULT_HORIZON_H,
            "basis": ("ASSUMED %.0f h to resolution (no event start on "
                      "record); the same assumption for every side"
                      % DEFAULT_HORIZON_H)}


def efficiency(ev_usd, capital_usd, hours) -> float | None:
    """Expected value per dollar of committed capital per hour."""
    try:
        if ev_usd is None or capital_usd is None or float(capital_usd) <= 0:
            return None
        return float(ev_usd) / float(capital_usd) / max(float(hours),
                                                        MIN_HORIZON_H)
    except (TypeError, ValueError):
        return None


def reallocation(*, evidence_state: str, qty, p, liquidation_usd,
                 hours: dict, best: dict | None) -> dict:
    """THE REALLOCATE ALTERNATIVE: shadow only (never an order). Pure."""
    out: dict[str, Any] = {"action": A_REALLOCATE, "mode": "SHADOW",
                           "orders_placed": 0, "recommended": False,
                           "rule": ("REALLOCATE when the best other currently "
                                    "qualified opportunity's EV per dollar "
                                    "per hour beats the position's (q p - L)"
                                    " / L / h by at least %.0f%%, on fresh "
                                    "evidence only"
                                    % (REALLOCATE_MIN_ADVANTAGE * 100)),
                           "horizon": hours, "best_opportunity": best}
    if evidence_state != E_FRESH or p is None:
        out.update(blocker=B_STALE, position_efficiency=None)
        return out
    if liquidation_usd is None or float(liquidation_usd) <= 0:
        out.update(blocker=B_NO_LIQUIDATION, position_efficiency=None)
        return out
    lq = float(liquidation_usd)
    ev_hold = float(qty) * float(p) - lq
    eff = efficiency(ev_hold, lq, hours["hours"])
    out.update(position_ev_from_here_usd=round(ev_hold, 6),
               position_capital_freed_usd=round(lq, 6),
               position_efficiency=None if eff is None else round(eff, 9))
    if not best or best.get("efficiency") is None:
        out["blocker"] = B_NO_OPPORTUNITY
        return out
    alt = float(best["efficiency"])
    need = eff + abs(eff) * REALLOCATE_MIN_ADVANTAGE
    out["alternative_efficiency"] = round(alt, 9)
    out["advantage"] = round(alt - eff, 9)
    if alt > 0 and alt > need:
        out.update(recommended=True, blocker=None,
                   why=("the alternative's %.6f per $ per h beats the "
                        "position's %.6f by more than %.0f%%; SHADOW: "
                        "recorded, never executed"
                        % (alt, eff, REALLOCATE_MIN_ADVANTAGE * 100)))
    else:
        out["blocker"] = B_NO_EDGE
    return out


def opportunity_from(row: dict, *, at: float) -> dict | None:
    """One ENTER decision as an alternative use of capital. Pure."""
    pd = _j(row.get("policy_decision")) or {}
    ec = _j(row.get("economics")) or {}
    ev = pd.get("net_expected_profit_usd")
    if ev is None:
        ev = ec.get("expected_net_profit_usd")
    cap = None
    if ec.get("acquisition_cost_usd") is not None:
        cap = float(ec["acquisition_cost_usd"]) + float(
            ec.get("fees_usd") or 0.0)
    elif row.get("proposed_qty") is not None and \
            row.get("limit_price") is not None:
        cap = float(row["proposed_qty"]) * float(row["limit_price"])
    if ev is None or cap is None or cap <= 0:
        return None
    h = horizon_h(at=at)
    eff = efficiency(ev, cap, h["hours"])
    if eff is None:
        return None
    return {"decision_id": row.get("decision_id"),
            "us_market_slug": row.get("us_market_slug"),
            "holding_side": row.get("holding_side"),
            "strategy": row.get("strategy"),
            "decided_at": _epoch(row.get("decided_at")),
            "ev_usd": round(float(ev), 6), "capital_usd": round(cap, 6),
            "horizon": h, "efficiency": round(eff, 9)}


async def best_opportunity(conn, *, at: float, exclude_slugs) -> dict | None:
    """The best currently qualified, not-held ENTER decision. Never
    raises (None)."""
    try:
        rows = await conn.fetch(
            "SELECT d.decision_id, d.us_market_slug, d.holding_side, "
            "       d.strategy, d.decided_at, d.proposed_qty, d.limit_price,"
            "       d.economics, d.policy_decision "
            "  FROM paper_decisions d "
            " WHERE d.verdict = 'ENTER' AND d.strategy = ANY($1::text[]) "
            "   AND d.decided_at > to_timestamp($2) "
            "   AND d.decided_at <= to_timestamp($3) "
            "   AND NOT EXISTS (SELECT 1 FROM paper_orders o "
            "                    WHERE o.decision_id = d.decision_id "
            "                      AND o.filled_qty > 0) "
            "   AND NOT (coalesce(d.us_market_slug, '') = ANY($4::text[])) "
            " ORDER BY d.decided_at DESC LIMIT 50",
            list(INVESTMENT_STRATEGIES), float(at) - REALLOCATE_LOOKBACK_S,
            float(at) + 1.0, sorted(set(exclude_slugs or [])))
    except Exception:                                           # noqa: BLE001
        return None
    best = None
    for r in rows:
        o = opportunity_from(dict(r), at=at)
        if o is not None and (best is None
                              or o["efficiency"] > best["efficiency"]):
            best = o
    return best


# ═════════════════════════════════════════════════════════════════════
# THE ASSESSMENT RECORD
# ═════════════════════════════════════════════════════════════════════

def paper_alternatives(alts: dict, *, reallocate: dict,
                       evidence_state=None) -> list:
    """HOLD / EXIT / REDUCE / NETTING / VERIFIED_HEDGE / REALLOCATE from the
    paper review's own ranking (paper_xavier.alternatives), completed to the
    six management options (xavier_freshness.complete_alternatives: each
    valued or carrying a NAMED missing-evidence reason). Pure."""
    out, seen = [], set()
    for c in alts.get("candidates") or []:
        out.append({"action": c.get("action"), "rankable": True,
                    "value_usd": c.get("value_usd"),
                    "expected_net_usd": c.get("expected_net_usd"),
                    "fees_usd": c.get("fees_usd"), "qty": c.get("qty"),
                    "ev_basis": c.get("ev_basis"), "blocker": None})
        seen.add(c.get("action"))
    for c in alts.get("not_rankable") or []:
        act = c.get("action")
        if act == "ACQUIRE_INDIRECT_HEDGE":
            out.append({"action": A_HEDGE, "rankable": False,
                        "value_usd": None, "blocker": B_NO_HEDGE_PROOF,
                        "source_blocker": c.get("blocker"),
                        "why": ("a hedge is admissible only with proven "
                                "settlement/payoff compatibility (one "
                                "grading variable); none is proven on this "
                                "path, so it is not ranked and never "
                                "assumed")})
            continue
        if act in seen:
            continue
        out.append({"action": act, "rankable": False,
                    "value_usd": c.get("value_usd"),
                    "blocker": c.get("blocker")})
    if not any(x["action"] == A_HEDGE for x in out):
        out.append({"action": A_HEDGE, "rankable": False, "value_usd": None,
                    "blocker": B_NO_HEDGE_PROOF})
    out.append({"action": A_REALLOCATE, "rankable": False,
                "mode": "SHADOW", "recommended": reallocate["recommended"],
                "blocker": reallocate.get("blocker"),
                "value_usd": reallocate.get("position_ev_from_here_usd")})
    return XF.complete_alternatives(out, evidence_state=evidence_state)


def actual_alternatives(*, evidence: dict, held: int, exit_px,
                        fee_fn, at: float, reallocate: dict) -> dict:
    """HOLD / EXIT / REDUCE for an ACTUAL position on the venue's top of
    book (size not shown by the BBO: stated). Pure but for the fee
    function. Returns {"alternatives", "recommendation"}."""
    from .. import bettor_paper_ledger as L
    fresh = evidence.get("evidence_state") == E_FRESH
    p = evidence.get("probability")
    q = int(held or 0)
    alts = []
    vals = {}
    if p is None:
        alts.append({"action": A_HOLD, "rankable": False, "value_usd": None,
                     "blocker": "NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION"})
    else:
        v = round(q * float(p), 6)
        alts.append({"action": A_HOLD, "rankable": True, "value_usd": v,
                     "ev_basis": evidence.get("evidence_state"),
                     "ev_is_current": fresh, "blocker": None})
        vals[A_HOLD] = v
    for act, sell in ((A_EXIT, q), (A_REDUCE, q // 2)):
        if sell < 1:
            alts.append({"action": act, "rankable": False, "value_usd": None,
                         "blocker": "NOTHING_TO_SELL"})
            continue
        if exit_px is None:
            alts.append({"action": act, "rankable": False, "value_usd": None,
                         "blocker": "NO_EXECUTABLE_EXIT_PRICE_IN_THE_VENUE_BBO"})
            continue
        fee = float(L._fee(fee_fn, sell, float(exit_px), at))
        cash = sell * float(exit_px) - fee
        kept = q - sell
        if p is None and kept > 0:
            alts.append({"action": act, "rankable": False, "value_usd": None,
                         "blocker": "NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION"})
            continue
        v = round(cash + kept * float(p or 0.0), 6)
        c = {"action": act, "qty": sell, "value_usd": v,
             "fees_usd": round(fee, 6),
             "evidence_quality": "VENUE_TOP_OF_BOOK_SIZE_NOT_SHOWN"}
        if not fresh:
            alts.append(dict(c, rankable=False, blocker=B_STALE))
        else:
            alts.append(dict(c, rankable=True, blocker=None))
            vals[act] = v
    # SAME-VENUE NETTING: on this venue one signed net position means
    # buying the complement IS the sale -- valued once, as EXIT
    alts.append({"action": "NETTING", "rankable": False, "value_usd": None,
                 "blocker": "IDENTICAL_TO_EXIT_ON_A_ONE_NET_POSITION_VENUE"})
    alts.append({"action": A_HEDGE, "rankable": False, "value_usd": None,
                 "blocker": B_NO_HEDGE_PROOF})
    alts.append({"action": A_REALLOCATE, "rankable": False, "mode": "SHADOW",
                 "recommended": reallocate["recommended"],
                 "blocker": reallocate.get("blocker"),
                 "value_usd": reallocate.get("position_ev_from_here_usd")})
    rec = None
    if vals and fresh:
        rec = max(sorted(vals), key=lambda k: (vals[k], k == A_HOLD))
    # NOT FRESH: no management action is recommended at all -- HOLD never
    # survives by default because EXIT / REDUCE were blocked (owner P0). The
    # position stays held; that is a fact about the book, not a
    # recommendation (WAITING_FOR_FRESH_EVIDENCE, or
    # MANAGEMENT_UNAVAILABLE_STALE_INPUT with no probability at all).
    state = evidence.get("evidence_state") if p is not None else E_NONE
    rec = XF.recorded_recommendation(evidence_state=state, selected=rec)
    return {"alternatives": XF.complete_alternatives(
        alts, evidence_state=state), "recommendation": rec}


def assessment(*, kind: str, group_id: str, review_id: str, thesis: dict |
               None, at: float, lat: dict, evidence: dict,
               venue_economics: dict, thesis_state: dict, alternatives: list,
               recommendation, reallocate: dict, policy: dict) -> dict:
    """THE ROW. Pure. The invariants the table CHECKs are applied here
    first: unavailable is null, nothing discretionary on stale."""
    state = evidence.get("evidence_state")
    if state not in EVIDENCE_STATES:
        state = E_NONE
    fresh = state == E_FRESH
    # NOT FRESH -> NO MANAGEMENT ACTION IS RECOMMENDED (owner P0): never a
    # discretionary sale (206 CHECK) and never a HOLD that only survived
    # because the sales were blocked: WAITING_FOR_FRESH_EVIDENCE, or
    # MANAGEMENT_UNAVAILABLE_STALE_INPUT when there is no probability.
    recommendation = XF.recorded_recommendation(evidence_state=state,
                                                selected=recommendation)
    if not fresh:
        reallocate = dict(reallocate, recommended=False)
    p = None if state == E_NONE else evidence.get("probability")
    limit = evidence.get("probability_limit_s")
    if limit is None and thesis:
        limit = thesis.get("probability_limit_s")
    valuation = XF.valuation_block(dict(evidence, evidence_state=state),
                                   assessed_at=at, limit_s=limit)
    return {"assessment_id": "xma:" + _sha([kind, review_id])[:32],
            "position_kind": kind, "group_id": group_id,
            "review_id": review_id,
            "thesis_id": (thesis or {}).get("thesis_id"),
            "assessed_at": float(at), "trigger": lat["trigger"],
            "due_at": lat.get("due_at"),
            "review_latency_s": lat.get("review_latency_s"),
            "latency_bound_s": lat.get("latency_bound_s"),
            "within_bound": lat.get("within_bound"),
            "evidence_state": state,
            "probability": None if p is None else float(p),
            "probability_source": evidence.get("probability_source"),
            "probability_age_s": _f(evidence.get("probability_age_s"), 3),
            "probability_limitation": evidence.get("probability_limitation"),
            "venue_economics": venue_economics,
            "thesis_state": thesis_state.get("state", TH_NONE),
            "thesis_detail": thesis_state, "alternatives": alternatives,
            "recommendation": recommendation,
            "recommendation_state": XF.write_state(
                evidence_state=state, recommendation=recommendation),
            "valuation": valuation,
            "discretionary_permitted": fresh,
            "reallocate": reallocate, "policy": policy}


async def record_assessment(conn, a: dict) -> dict:
    """INSERT the assessment (idempotent). Never raises."""
    if not await has_schema(conn):
        return {"ok": False, "why": "MIGRATION_206_NOT_APPLIED"}
    # migration 222: the valuation the recommendation stands on and its
    # write-time state, in the same INSERT (the 206 trigger forbids UPDATE);
    # before 222 the row is written as before and the read layer derives
    # the valuation from probability_age_s (stated as derived)
    v222 = await has_valuation_columns(conn)
    cols, vals = "", ""
    extra: tuple = ()
    if v222:
        cols = ", valuation, recommendation_state"
        vals = ", $24::jsonb, $25"
        extra = (_dumps(a.get("valuation") or {}),
                 a.get("recommendation_state"))
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO xavier_management_assessments (assessment_id, "
                " position_kind, group_id, review_id, thesis_id, assessed_at,"
                " trigger, due_at, review_latency_s, latency_bound_s, "
                " within_bound, evidence_state, probability, "
                " probability_source, probability_age_s, venue_economics, "
                " thesis_state, thesis_detail, alternatives, recommendation, "
                " discretionary_permitted, reallocate, policy" + cols + ")"
                " VALUES ($1,$2,"
                " $3,$4,$5,to_timestamp($6),$7,to_timestamp($8),$9,$10,$11,"
                " $12,$13,$14,$15,$16::jsonb,$17,$18::jsonb,$19::jsonb,$20,"
                " $21,$22::jsonb,$23::jsonb" + vals + ") ON CONFLICT DO NOTHING",
                a["assessment_id"], a["position_kind"], a["group_id"],
                a["review_id"], a["thesis_id"], a["assessed_at"],
                a["trigger"], _ts(a.get("due_at")), a.get("review_latency_s"),
                a.get("latency_bound_s"), a.get("within_bound"),
                a["evidence_state"], a.get("probability"),
                a.get("probability_source"), a.get("probability_age_s"),
                _dumps(a["venue_economics"]), a["thesis_state"],
                _dumps(a["thesis_detail"]), _dumps(a["alternatives"]),
                a.get("recommendation"), a["discretionary_permitted"],
                _dumps(a["reallocate"]), _dumps(a["policy"]), *extra)
        return {"ok": True, "assessment_id": a["assessment_id"],
                "valuation_recorded": v222}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "ASSESSMENT_WRITE_FAILED:%s"
                % type(exc).__name__, "detail": str(exc)[:200]}


async def record_thesis(conn, t: dict) -> dict:
    """INSERT the thesis once (the first write wins; never updated)."""
    if not await has_schema(conn):
        return {"ok": False, "why": "MIGRATION_206_NOT_APPLIED"}
    try:
        async with conn.transaction():
            got = await conn.fetchval(
                "INSERT INTO xavier_entry_theses (thesis_id, position_kind, "
                " group_id, position_ref, decision_id, strategy, "
                " us_market_slug, holding_side, entered_at, first_fill_at, "
                " entry_qty, entry_cost_per_contract, entry_cost_usd, "
                " entry_fees_usd, entry_probability, probability_source, "
                " probability_source_at, probability_limit_s, "
                " entry_ev_per_contract_usd, entry_ev_usd, assumptions, "
                " evidence_refs, event_start_at, evidence_expires_at, "
                " thesis_expires_at, expiry_basis, counterfactuals, "
                " content_sha256) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
                " to_timestamp($9),to_timestamp($10),$11,$12,$13,$14,$15,$16,"
                " to_timestamp($17),$18,$19,$20,$21::jsonb,$22::jsonb,"
                " to_timestamp($23),to_timestamp($24),to_timestamp($25),$26,"
                " $27::jsonb,$28) ON CONFLICT DO NOTHING RETURNING thesis_id",
                t["thesis_id"], t["position_kind"], t["group_id"],
                t["position_ref"], t["decision_id"], t["strategy"],
                t["us_market_slug"], t["holding_side"],
                _ts(t["entered_at"]), _ts(t["first_fill_at"]),
                t["entry_qty"], t["entry_cost_per_contract"],
                t["entry_cost_usd"], t["entry_fees_usd"],
                t["entry_probability"], t["probability_source"],
                _ts(t["probability_source_at"]), t["probability_limit_s"],
                t["entry_ev_per_contract_usd"], t["entry_ev_usd"],
                _dumps(t["assumptions"]), _dumps(t["evidence_refs"]),
                _ts(t["event_start_at"]), _ts(t["evidence_expires_at"]),
                _ts(t["thesis_expires_at"]), t["expiry_basis"],
                _dumps(t["counterfactuals"]), t["content_sha256"])
        return {"ok": True, "created": got is not None,
                "thesis_id": t["thesis_id"]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "THESIS_WRITE_FAILED:%s"
                % type(exc).__name__, "detail": str(exc)[:200]}


def _thesis_row(r) -> dict | None:
    if r is None:
        return None
    out = {}
    for k, v in dict(r).items():
        if k in ("assumptions", "evidence_refs", "counterfactuals"):
            v = _j(v)
        elif hasattr(v, "timestamp"):
            v = v.timestamp()
        elif isinstance(v, Decimal):
            v = float(v)
        out[k] = v
    return out


async def thesis_for(conn, kind: str, group_id: str) -> dict | None:
    try:
        if not await has_schema(conn):
            return None
        return _thesis_row(await conn.fetchrow(
            "SELECT * FROM xavier_entry_theses WHERE position_kind=$1 "
            "   AND group_id=$2", kind, group_id))
    except Exception:                                           # noqa: BLE001
        return None


async def _event_start(conn, valuation_id) -> float | None:
    if valuation_id is None:
        return None
    try:
        return _epoch(await conn.fetchval(
            "SELECT m.actual_start_at FROM external_valuations v "
            "  JOIN fixture_metadata m ON m.condition_id = v.condition_id "
            " WHERE v.id = $1", int(valuation_id)))
    except Exception:                                           # noqa: BLE001
        return None


def _entry_probability(d: dict | None) -> tuple:
    """(p, source) of the entry decision on its strategy's own measure."""
    if not d:
        return None, None
    strat = d.get("strategy")
    if strat == "DEREK_ENTRY_POLICY_V2" and d.get("p_blended") is not None:
        return float(d["p_blended"]), "ENTRY_DECISION_BLENDED"
    if d.get("p_pinnacle") is not None:
        return float(d["p_pinnacle"]), "ENTRY_DECISION_PINNACLE"
    if d.get("p_blended") is not None:
        return float(d["p_blended"]), "ENTRY_DECISION_BLENDED"
    return None, None


# ═════════════════════════════════════════════════════════════════════
# PAPER: THESIS AT HANDOFF, ASSESSMENT ON EVERY REVIEW
# ═════════════════════════════════════════════════════════════════════

async def record_paper_thesis(conn, ctx: dict, group_id: str) -> dict:
    """THE PAPER POSITION'S THESIS, at its handoff (first fill). Never
    raises; refuses outside the entry window (no hindsight)."""
    try:
        return await _record_paper_thesis(conn, ctx, group_id)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "THESIS_BUILD_FAILED:%s"
                % type(exc).__name__}


async def _record_paper_thesis(conn, ctx, group_id) -> dict:
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_simulator as SIM
    from . import paper_xavier as PX
    c = ctx.get("clock")
    at = float(c()) if c else float(ctx["now"])
    h = await conn.fetchrow("SELECT * FROM paper_handoffs WHERE group_id=$1",
                            group_id)
    if h is None:
        return {"ok": False, "why": "NO_HANDOFF"}
    ffa = _epoch(h["first_fill_at"])
    if ffa is None or at - ffa > THESIS_ENTRY_WINDOW_S:
        return {"ok": False, "why": "OUTSIDE_THE_ENTRY_WINDOW_NO_HINDSIGHT"}
    o = await conn.fetchrow("SELECT * FROM paper_orders WHERE order_id=$1",
                            h["entry_order_id"])
    if o is None:
        return {"ok": False, "why": "NO_ENTRY_ORDER"}
    fills = await conn.fetch(
        "SELECT fill_id, qty, price, gross_usd, fee_usd, filled_at, "
        "       book_obs_id FROM paper_fills WHERE order_id=$1 "
        " ORDER BY filled_at, fill_id", o["order_id"])
    qty = sum(float(f["qty"]) for f in fills)
    if qty <= 0:
        return {"ok": False, "why": "NO_ENTRY_FILL"}
    gross = sum(float(f["gross_usd"]) for f in fills)
    fees = sum(float(f["fee_usd"]) for f in fills)
    d = None
    if h["decision_id"]:
        d = await conn.fetchrow(
            "SELECT decision_id, strategy, policy_version, decided_at, "
            "       valuation_id, p_pinnacle, p_blended, economics, "
            "       policy_decision FROM paper_decisions "
            " WHERE decision_id=$1", h["decision_id"])
        d = None if d is None else dict(d)
    p, src = _entry_probability(d)
    src_at = None
    if d and d.get("valuation_id") is not None:
        src_at = _epoch(await conn.fetchval(
            "SELECT observed_at FROM external_valuations WHERE id=$1",
            int(d["valuation_id"])))
    start = await _event_start(conn, (d or {}).get("valuation_id"))
    limit = float(ctx["config"]["entry"]["pinnacle_max_age_s"])
    # THE ENTRY-TIME EXECUTABLE EXIT: the latest book observed at or before
    # the first fill (else the latest at all, its instant stated).
    obs = await conn.fetchrow(
        "SELECT obs_id, observed_at, bids, offers, error FROM "
        " paper_book_observations WHERE us_market_slug=$1 "
        "   AND observed_at <= to_timestamp($2) "
        " ORDER BY observed_at DESC LIMIT 1", o["us_market_slug"], at)
    walk, basis = None, {"source": "paper_book_observations", "obs_id": None,
                         "why": "no book observed at entry"}
    if obs is not None and not obs["error"]:
        md = {"bids": L._j(obs["bids"]) or [],
              "offers": L._j(obs["offers"]) or []}
        lv = SIM.levels_for(md, direction="SELL",
                            holding_side=o["holding_side"])["levels"]
        walk = PX._exit_walk(lv, qty, ctx.get("fee_fn"), at)
        basis = {"source": "paper_book_observations",
                 "obs_id": obs["obs_id"],
                 "observed_at": _epoch(obs["observed_at"]),
                 "fee_function": "the paper simulator's fee function"}
    pd = _j((d or {}).get("policy_decision")) or {}
    t = build_thesis(
        kind=K_PAPER, group_id=group_id, position_ref=h["handoff_id"],
        decision_id=h["decision_id"], strategy=h["strategy"],
        slug=o["us_market_slug"], holding_side=o["holding_side"],
        entered_at=(d or {}).get("decided_at") or o["decided_at"],
        first_fill_at=ffa, qty=qty, entry_cost_usd=gross + fees,
        entry_fees_usd=fees, entry_price_per_contract=gross / qty, p=p,
        probability_source=src, probability_source_at=src_at, limit_s=limit,
        event_start_at=start,
        assumptions={
            "void_applied": False,
            "measure": ("the strategy's own entry measure (completed-game / "
                        "benchmark: de-vigged Pinnacle; Derek: the blend)"),
            "decision_net_expected_profit_usd": pd.get(
                "net_expected_profit_usd"),
            "decision_policy_version": (d or {}).get("policy_version"),
            "entry_qty_is": "the ENTRY order's fills at handoff",
            "position": "PAPER (simulated execution)"},
        evidence_refs=[x for x in (
            {"kind": "paper_handoffs", "id": h["handoff_id"]},
            {"kind": "paper_decisions", "id": h["decision_id"]}
            if h["decision_id"] else None,
            {"kind": "external_valuations", "id": d["valuation_id"]}
            if d and d.get("valuation_id") is not None else None,
            {"kind": "paper_book_observations", "id": basis.get("obs_id")}
            if basis.get("obs_id") else None,
            {"kind": "paper_fills", "ids": [f["fill_id"] for f in fills]})
            if x],
        exit_walk=walk, exit_basis=basis)
    return await record_thesis(conn, t)


async def paper_reallocation(conn, ctx: dict, *, group_id: str, pos: dict,
                             trigger: str, at: float, measure: dict,
                             exit_levels: list) -> dict:
    """XAVIER'S REALLOCATE COMPARISON FOR ONE PAPER POSITION, computed ONCE
    per review, BEFORE the canonical management intent is built (R30A
    section 8: the intent records every alternative, REALLOCATE included),
    and handed to paper_review_hook so the assessment records the very same
    comparison. The thesis is read (and, on the first fill, written) here
    exactly as the assessment always did. Never raises: a failure is the
    alternative's UNAVAILABLE reason.
    {reallocate, thesis, liquidation_usd, walk, hours}."""
    from . import paper_xavier as PX
    try:
        thesis = await thesis_for(conn, K_PAPER, group_id)
        if thesis is None and trigger == T_FIRST:
            await record_paper_thesis(conn, ctx, group_id)
            thesis = await thesis_for(conn, K_PAPER, group_id)
        q = float(pos["open_qty"])
        w = PX._exit_walk(exit_levels or [], q, ctx.get("fee_fn"), at)
        sold = float(w["sold"])
        liq = round(w["proceeds_usd"] - w["fees_usd"], 6) if sold > 0 else None
        fresh = measure.get("evidence_state") == E_FRESH
        hours = horizon_h(at=at, event_start_at=(thesis or {}).get(
            "event_start_at"))
        best = None
        if fresh:
            # the markets this account HOLDS now (net open, not settled)
            held = {r["us_market_slug"] for r in await conn.fetch(
                "SELECT DISTINCT us_market_slug FROM ("
                + L.CANONICAL_OPEN_POSITIONS_SQL + ") c "
                " WHERE c.account_id = $1", ctx["account_id"])}
            best = await best_opportunity(conn, at=at, exclude_slugs=held)
        re = reallocation(evidence_state=measure.get("evidence_state"), qty=q,
                          p=measure.get("p") if fresh else None,
                          liquidation_usd=liq, hours=hours, best=best)
        return {"reallocate": re, "thesis": thesis, "liquidation_usd": liq,
                "walk": w, "hours": hours}
    except Exception as exc:                                    # noqa: BLE001
        return {"reallocate": None, "thesis": None, "liquidation_usd": None,
                "walk": None, "hours": None,
                "why": "REALLOCATE_COMPARISON_FAILED:%s" % type(exc).__name__}


async def paper_review_hook(conn, ctx: dict, *, group_id: str, pos: dict,
                            review_id: str, trigger: str, at: float,
                            measure: dict, alts: dict, recommendation,
                            exit_levels: list, policy: dict,
                            due_at=None, precomputed: dict | None = None
                            ) -> dict:
    """THE ASSESSMENT OF ONE PAPER REVIEW. Never raises; changes nothing
    the review decided. `precomputed` is paper_reallocation's result when
    the review already compared REALLOCATE (the canonical intent's set): the
    assessment then records that same comparison instead of a second one."""
    try:
        return await _paper_review_hook(
            conn, ctx, group_id=group_id, pos=pos, review_id=review_id,
            trigger=trigger, at=at, measure=measure, alts=alts,
            recommendation=recommendation, exit_levels=exit_levels,
            policy=policy, due_at=due_at, precomputed=precomputed)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "ASSESSMENT_FAILED:%s"
                % type(exc).__name__, "detail": str(exc)[:200]}


async def _paper_review_hook(conn, ctx, *, group_id, pos, review_id, trigger,
                             at, measure, alts, recommendation, exit_levels,
                             policy, due_at, precomputed=None) -> dict:
    from . import paper_xavier as PX
    pre = precomputed if (precomputed or {}).get("reallocate") is not None \
        else None
    if pre is not None:
        thesis = pre.get("thesis")
    else:
        thesis = await thesis_for(conn, K_PAPER, group_id)
        if thesis is None and trigger == T_FIRST:
            await record_paper_thesis(conn, ctx, group_id)
            thesis = await thesis_for(conn, K_PAPER, group_id)
    backstop = float(ctx["config"]["cadence"].get("xavier_backstop_s", 60.0))
    if trigger == T_FIRST and due_at is None:
        due_at = await conn.fetchval(
            "SELECT first_fill_at FROM paper_handoffs WHERE group_id=$1",
            group_id)
    lat = latency(kind=K_PAPER, trigger=trigger, at=at, due_at=due_at,
                  cadence_s=backstop)
    q = float(pos["open_qty"])
    w = PX._exit_walk(exit_levels or [], q, ctx.get("fee_fn"), at)
    sold = float(w["sold"])
    liq = round(w["proceeds_usd"] - w["fees_usd"], 6) if sold > 0 else None
    per_net = (liq / sold) if liq is not None and sold > 0 else None
    cash = (float(pos.get("sale_proceeds_net_usd") or 0)
            - float(pos.get("acquisition_cost_usd") or 0)
            + float(((pos.get("settlement") or {}).get("payout_usd")) or 0))
    marks = {"open_qty": q, "cash_to_date_usd": round(cash, 6),
             "exit_net_per_contract": _f(per_net),
             "actual_mark_pnl_usd": (None if liq is None else round(
                 cash + liq, 6)),
             "hold_mark_pnl_usd": (
                 None if per_net is None or not thesis else round(
                     float(thesis["entry_qty"]) * per_net
                     - float(thesis["entry_cost_usd"]), 6)),
             "unsold_valued_at": "0 (conservative mark)"}
    venue = {"source": "paper_book_observations",
             "book_obs_id": measure.get("book_obs_id"),
             "best_exit": measure.get("best_exit_at_review"),
             "exit_walk_full_qty": {k: w.get(k) for k in (
                 "sold", "unsold", "proceeds_usd", "fees_usd",
                 "worst_price")},
             "liquidation_value_usd": liq, "marks": marks}
    fresh = measure.get("evidence_state") == E_FRESH
    if pre is not None:
        re = pre["reallocate"]
    else:
        hours = horizon_h(at=at, event_start_at=(thesis or {}).get(
            "event_start_at"))
        best = None
        if fresh:
            # the markets this account HOLDS now (net open, not settled)
            held = {r["us_market_slug"] for r in await conn.fetch(
                "SELECT DISTINCT us_market_slug FROM ("
                + L.CANONICAL_OPEN_POSITIONS_SQL + ") c "
                " WHERE c.account_id = $1", ctx["account_id"])}
            best = await best_opportunity(conn, at=at, exclude_slugs=held)
        re = reallocation(evidence_state=measure.get("evidence_state"),
                          qty=q, p=measure.get("p") if fresh else None,
                          liquidation_usd=liq, hours=hours, best=best)
    th = classify_thesis(thesis, evidence=measure, at=at)
    a = assessment(kind=K_PAPER, group_id=group_id, review_id=review_id,
                   thesis=thesis, at=at, lat=lat, evidence=measure,
                   venue_economics=venue, thesis_state=th,
                   alternatives=paper_alternatives(
                       alts, reallocate=re,
                       evidence_state=measure.get("evidence_state")),
                   recommendation=recommendation, reallocate=re,
                   policy=policy)
    got = await record_assessment(conn, a)
    return dict(got, thesis_state=a["thesis_state"],
                recommendation=a["recommendation"],
                recommendation_state=a["recommendation_state"],
                valuation=a["valuation"],
                reallocate_recommended=a["reallocate"]["recommended"],
                within_bound=a["within_bound"],
                review_latency_s=a["review_latency_s"])


# ═════════════════════════════════════════════════════════════════════
# ACTUAL: THESIS AT THE FIRST REVIEW (same tick as the handoff)
# ═════════════════════════════════════════════════════════════════════

async def _live_fills(conn, group_id: str, slug: str) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT f.intent, f.qty, f.price, f.fee_usd, f.observed_at, "
        "       m.role FROM execmirror_fills f LEFT JOIN execmirror_orders m "
        "    USING (mirror_id) WHERE f.group_id=$1 AND f.us_market_slug=$2 "
        " ORDER BY f.observed_at", group_id, slug)]


def live_cash(fills: list) -> dict:
    """Cash (collateral space), fees, turnover, bought / sold of live
    fills. Pure."""
    cash = fees = turnover = 0.0
    bought = sold = 0.0
    for f in fills:
        q, px = float(f["qty"]), float(f["price"])
        fee = float(f.get("fee_usd") or 0)
        intent = str(f["intent"])
        per = 1.0 - px if intent.endswith("_SHORT") else px
        if "_BUY_" in intent:
            cash -= q * per
            bought += q
        else:
            cash += q * per
            sold += q
        cash -= fee
        fees += fee
        turnover += q * per
    return {"cash_usd": round(cash, 6), "fees_usd": round(fees, 6),
            "turnover_usd": round(turnover, 6), "bought": bought,
            "sold": sold, "held": round(bought - sold, 6)}


async def record_actual_thesis(conn, h: dict, *, at: float, quote: dict,
                               fee_fn=None) -> dict:
    """THE ACTUAL POSITION'S THESIS, written by its FIRST review (the tick
    that hands it off). Never raises; refuses outside the entry window."""
    try:
        return await _record_actual_thesis(conn, h, at=at, quote=quote,
                                           fee_fn=fee_fn)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "THESIS_BUILD_FAILED:%s"
                % type(exc).__name__}


async def _record_actual_thesis(conn, h, *, at, quote, fee_fn) -> dict:
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_session as S
    ffa = _epoch(h.get("first_live_fill_at"))
    if ffa is None or at - ffa > THESIS_ENTRY_WINDOW_S:
        return {"ok": False, "why": "OUTSIDE_THE_ENTRY_WINDOW_NO_HINDSIGHT"}
    side = side_of_intent(h.get("opened_intent"))
    fills = [f for f in await _live_fills(conn, h["group_id"],
                                          h["us_market_slug"])
             if "_BUY_" in str(f["intent"])]
    lc = live_cash(fills)
    qty = lc["bought"]
    if qty <= 0:
        return {"ok": False, "why": "NO_ENTRY_FILL"}
    cost = -lc["cash_usd"]
    d = await conn.fetchrow(
        "SELECT d.decision_id, d.strategy, d.policy_version, d.decided_at, "
        "       d.valuation_id, d.p_pinnacle, d.p_blended, "
        "       d.policy_decision, i.intent_id "
        "  FROM execution_intents i JOIN paper_decisions d "
        "    ON d.decision_id = i.decision_id "
        " WHERE i.group_id=$1 ORDER BY i.created_at LIMIT 1", h["group_id"])
    d = None if d is None else dict(d)
    p, src = _entry_probability(d)
    src_at = None
    if d and d.get("valuation_id") is not None:
        src_at = _epoch(await conn.fetchval(
            "SELECT observed_at FROM external_valuations WHERE id=$1",
            int(d["valuation_id"])))
    start = await _event_start(conn, (d or {}).get("valuation_id"))
    limit = float(S.default_config()["entry"]["pinnacle_max_age_s"])
    q = quote or {}
    raw = q.get("bid") if side == "LONG" else q.get("ask")
    exit_px = None if raw is None else collateral(side, raw)
    walk = None
    if exit_px is not None and q.get("read"):
        fee = float(L._fee(fee_fn, qty, exit_px, at))
        walk = {"sold": qty, "unsold": 0.0,
                "proceeds_usd": round(qty * exit_px, 6),
                "fees_usd": round(fee, 6)}
    pd = _j((d or {}).get("policy_decision")) or {}
    t = build_thesis(
        kind=K_ACTUAL, group_id=h["group_id"], position_ref=h["handoff_id"],
        decision_id=(d or {}).get("decision_id"),
        strategy=(d or {}).get("strategy"), slug=h["us_market_slug"],
        holding_side=side,
        entered_at=(d or {}).get("decided_at") or ffa, first_fill_at=ffa,
        qty=qty, entry_cost_usd=cost, entry_fees_usd=lc["fees_usd"],
        entry_price_per_contract=(cost - lc["fees_usd"]) / qty, p=p,
        probability_source=src, probability_source_at=src_at, limit_s=limit,
        event_start_at=start,
        assumptions={
            "void_applied": False,
            "measure": "the entry decision's probability (one decision -> "
                       "paper + actual siblings)",
            "decision_net_expected_profit_usd": pd.get(
                "net_expected_profit_usd"),
            "decision_policy_version": (d or {}).get("policy_version"),
            "immediate_exit_is": ("the venue BBO exit side read by the "
                                  "first review, the tick of the handoff; "
                                  "the BBO shows no size, so the whole "
                                  "entry quantity is assumed to clear at it"),
            "position": "ACTUAL (venue-confirmed fills)"},
        evidence_refs=[x for x in (
            {"kind": "smalllive_handoffs", "id": h["handoff_id"]},
            {"kind": "execution_intents", "id": d["intent_id"]} if d else None,
            {"kind": "paper_decisions", "id": d["decision_id"]} if d else None,
            {"kind": "external_valuations", "id": d["valuation_id"]}
            if d and d.get("valuation_id") is not None else None,
            {"kind": "venue_bbo", "at": q.get("at"), "bid": q.get("bid"),
             "ask": q.get("ask")}) if x],
        exit_walk=walk,
        exit_basis={"source": "venue BBO at the first review",
                    "at": q.get("at"), "read": bool(q.get("read")),
                    "exit_px_held_side": exit_px})
    return await record_thesis(conn, t)


async def actual_review_hook(conn, h: dict, *, review_id: str, at: float,
                             trigger: str, due_at, cadence_s: float,
                             quote: dict, prob: dict, held: int,
                             paper_recommendation=None, policy: dict,
                             fee_fn=None) -> dict:
    """THE ASSESSMENT OF ONE ACTUAL REVIEW (handed to execmirror.Mirror as
    `management_assessor`). Record only: the actual position's action still
    follows the paper decision; nothing here places or cancels anything.
    Never raises."""
    try:
        return await _actual_review_hook(
            conn, h, review_id=review_id, at=at, trigger=trigger,
            due_at=due_at, cadence_s=cadence_s, quote=quote, prob=prob,
            held=held, paper_recommendation=paper_recommendation,
            policy=policy, fee_fn=fee_fn)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "ASSESSMENT_FAILED:%s"
                % type(exc).__name__, "detail": str(exc)[:200]}


async def _actual_review_hook(conn, h, *, review_id, at, trigger, due_at,
                              cadence_s, quote, prob, held,
                              paper_recommendation, policy, fee_fn) -> dict:
    thesis = await thesis_for(conn, K_ACTUAL, h["group_id"])
    if thesis is None and trigger == T_FIRST:
        await record_actual_thesis(conn, h, at=at, quote=quote, fee_fn=fee_fn)
        thesis = await thesis_for(conn, K_ACTUAL, h["group_id"])
    if trigger == T_FIRST and due_at is None:
        due_at = h.get("first_live_fill_at")
    lat = latency(kind=K_ACTUAL, trigger=trigger, at=at, due_at=due_at,
                  cadence_s=cadence_s)
    side = side_of_intent(h.get("opened_intent"))
    q = quote or {}
    raw = q.get("bid") if side == "LONG" else q.get("ask")
    exit_px = None if raw is None or not q.get("read") else collateral(
        side, raw)
    from .. import bettor_paper_ledger as L
    lc = live_cash(await _live_fills(conn, h["group_id"],
                                     h["us_market_slug"]))
    n = int(held or 0)
    liq = None
    if exit_px is not None and n > 0:
        liq = round(n * exit_px - float(L._fee(fee_fn, n, exit_px, at)), 6)
    per_net = None if liq is None or n <= 0 else liq / n
    marks = {"open_qty": n, "cash_to_date_usd": lc["cash_usd"],
             "exit_net_per_contract": _f(per_net),
             "actual_mark_pnl_usd": (None if liq is None else round(
                 lc["cash_usd"] + liq, 6)),
             "hold_mark_pnl_usd": (
                 None if per_net is None or not thesis else round(
                     float(thesis["entry_qty"]) * per_net
                     - float(thesis["entry_cost_usd"]), 6))}
    venue = {"source": "venue BBO (the mirror account's own read)",
             "quote": {k: q.get(k) for k in ("read", "at", "bid", "ask",
                                             "state", "error")},
             "exit_px_held_side": exit_px, "liquidation_value_usd": liq,
             "marks": marks,
             "paper_recommendation_followed": paper_recommendation,
             "action_follows": "THE_PAPER_DECISION (record only)"}
    fresh = prob.get("evidence_state") == E_FRESH
    hours = horizon_h(at=at, event_start_at=(thesis or {}).get(
        "event_start_at"))
    best = None
    if fresh:
        held_slugs = {r["us_market_slug"] for r in await conn.fetch(
            "SELECT us_market_slug FROM smalllive_handoffs "
            " WHERE state = 'OPEN'")}
        best = await best_opportunity(conn, at=at, exclude_slugs=held_slugs)
    re = reallocation(evidence_state=prob.get("evidence_state"), qty=n,
                      p=prob.get("probability") if fresh else None,
                      liquidation_usd=liq, hours=hours, best=best)
    alt = actual_alternatives(evidence=prob, held=n, exit_px=exit_px,
                              fee_fn=fee_fn, at=at, reallocate=re)
    th = classify_thesis(thesis, evidence=prob, at=at)
    a = assessment(kind=K_ACTUAL, group_id=h["group_id"],
                   review_id=review_id, thesis=thesis, at=at, lat=lat,
                   evidence=prob, venue_economics=venue, thesis_state=th,
                   alternatives=alt["alternatives"],
                   recommendation=alt["recommendation"], reallocate=re,
                   policy=policy)
    got = await record_assessment(conn, a)
    return dict(got, thesis_state=a["thesis_state"],
                recommendation=a["recommendation"],
                recommendation_state=a["recommendation_state"],
                valuation=a["valuation"],
                reallocate_recommended=a["reallocate"]["recommended"],
                within_bound=a["within_bound"],
                review_latency_s=a["review_latency_s"],
                thesis_id=a["thesis_id"])


# ═════════════════════════════════════════════════════════════════════
# VALUE-ADD (upgrade H)
# ═════════════════════════════════════════════════════════════════════

async def settlement_outcome(conn, slug: str, holding_side: str) -> dict:
    """The held side's payout per contract from authoritative venue
    evidence (paper_xavier.outcome_for / venue_price_settlement), or
    None. Never raises."""
    from . import paper_xavier as PX
    try:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
            "       outcome_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND outcome_basis IS NOT NULL "
            " ORDER BY id", slug)]
        got = PX.outcome_for(rows, holding_side=holding_side)
        if got.get("outcome") == "WON":
            return {"outcome": "WON", "payout_per_contract": 1.0,
                    "evidence": got.get("evidence")}
        if got.get("outcome") == "LOST":
            return {"outcome": "LOST", "payout_per_contract": 0.0,
                    "evidence": got.get("evidence")}
        if got.get("outcome") == "VOID_REFUND":
            return {"outcome": "VOID_REFUND", "payout_per_contract": None,
                    "refund": True, "evidence": got.get("evidence")}
        if got.get("why") == "CONFLICTING_SETTLEMENT_EVIDENCE":
            return {"outcome": None, "why": got["why"]}
        vrows = [dict(r) for r in await conn.fetch(PX.VENUE_PRICE_SQL, slug)]
        vp = PX.venue_price_settlement(vrows, holding_side=holding_side)
        if vp.get("price") is not None:
            return {"outcome": "SETTLED_AT_VENUE_PRICE",
                    "payout_per_contract": float(vp["price"]),
                    "evidence": vp.get("evidence")}
        return {"outcome": None, "why": got.get("why") or vp.get("why")}
    except Exception as exc:                                    # noqa: BLE001
        return {"outcome": None, "why": "SETTLEMENT_READ_FAILED:%s"
                % type(exc).__name__}


def _payout(settle: dict | None, thesis: dict) -> float | None:
    if not settle or settle.get("outcome") is None:
        return None
    if settle.get("refund"):
        hold = (thesis.get("counterfactuals") or {}).get(CF_HOLD) or {}
        return hold.get("entry_price_per_contract")
    return settle.get("payout_per_contract")


def value_add_compute(thesis: dict, *, actual: dict, settle: dict | None,
                      marks: list) -> dict | None:
    """THE COUNTERFACTUAL OUTCOMES BY THE RULES FROZEN ON THE THESIS. Pure.
    None while Xavier's own outcome is not known (position open and not
    settled). HOLD is pending until settlement is known."""
    if actual.get("pnl_usd") is None:
        return None
    cf = thesis.get("counterfactuals") or {}
    hold, ex = cf.get(CF_HOLD) or {}, cf.get(CF_EXIT) or {}
    q = float(thesis["entry_qty"])
    cost = float(thesis["entry_cost_usd"])
    pay = _payout(settle, thesis)
    entry_fees = float(hold.get("entry_fees_usd") or 0.0)
    hold_marks = [m.get("hold_mark_pnl_usd") for m in marks
                  if m.get("hold_mark_pnl_usd") is not None]
    act_marks = [m.get("actual_mark_pnl_usd") for m in marks
                 if m.get("actual_mark_pnl_usd") is not None]
    hold_pnl = None if pay is None else round(q * float(pay) - cost, 6)
    out_hold = {"pnl_usd": hold_pnl, "fees_usd": round(entry_fees, 6),
                "turnover_usd": round(cost - entry_fees, 6),
                "max_drawdown_usd": (None if hold_pnl is None else round(min(
                    [0.0, hold_pnl] + hold_marks), 6)),
                "available": hold_pnl is not None,
                "why_unavailable": (None if hold_pnl is not None else
                                    "settlement not yet known")}
    if ex.get("available"):
        unsold = float(ex.get("unsold_qty") or 0.0)
        tail = 0.0 if unsold <= 1e-9 else (
            None if pay is None else unsold * float(pay))
        ex_pnl = None if tail is None else round(
            float(ex["exit_proceeds_usd"]) - float(ex["exit_fees_usd"])
            + tail - cost, 6)
        out_ex = {"pnl_usd": ex_pnl,
                  "fees_usd": round(entry_fees + float(ex["exit_fees_usd"]),
                                    6),
                  "turnover_usd": round(cost - entry_fees
                                        + float(ex["exit_proceeds_usd"]), 6),
                  "max_drawdown_usd": (None if ex_pnl is None else round(
                      min(0.0, ex_pnl), 6)),
                  "available": ex_pnl is not None,
                  "why_unavailable": (None if ex_pnl is not None else
                                      "unsold remainder awaits settlement")}
    else:
        out_ex = {"pnl_usd": None, "fees_usd": None, "turnover_usd": None,
                  "max_drawdown_usd": None, "available": False,
                  "why_unavailable": ex.get("why_unavailable")
                  or "no executable exit at entry"}
    a_pnl = float(actual["pnl_usd"])
    out_act = {"pnl_usd": round(a_pnl, 6),
               "fees_usd": _f(actual.get("fees_usd")),
               "turnover_usd": _f(actual.get("turnover_usd")),
               "max_drawdown_usd": round(min([0.0, a_pnl] + act_marks), 6),
               "available": True, "basis": actual.get("basis")}

    def _inc(other):
        if not other.get("available"):
            return {"available": False, "why": other.get("why_unavailable")}
        return {"available": True,
                "pnl_usd": round(a_pnl - other["pnl_usd"], 6),
                "fees_usd": (None if out_act["fees_usd"] is None
                             or other["fees_usd"] is None else round(
                                 out_act["fees_usd"] - other["fees_usd"], 6)),
                "turnover_usd": (None if out_act["turnover_usd"] is None
                                 or other["turnover_usd"] is None else round(
                                     out_act["turnover_usd"]
                                     - other["turnover_usd"], 6)),
                "max_drawdown_usd": round(out_act["max_drawdown_usd"]
                                          - other["max_drawdown_usd"], 6)}
    res = {CF_HOLD: out_hold, CF_EXIT: out_ex, CF_ACTUAL: out_act}
    inc = {"ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT": _inc(out_hold),
           "ACTUAL_XAVIER_minus_IMMEDIATE_EXIT": _inc(out_ex)}
    status = ("FINAL" if out_hold["available"] and (
        out_ex["available"] or not ex.get("available"))
        else "PENDING_SETTLEMENT_FOR_HOLD_COUNTERFACTUAL")
    body = {"thesis_id": thesis["thesis_id"],
            "position_kind": thesis["position_kind"],
            "group_id": thesis["group_id"], "status": status,
            "outcome_basis": actual.get("outcome_basis") or "UNKNOWN",
            "outcome_evidence": {"settlement": settle,
                                 "actual": actual.get("basis"),
                                 "marks_used": len(marks)},
            "counterfactuals": res, "incremental": inc,
            "method": ("predeclared at entry (thesis.counterfactuals); "
                       "drawdown = the worst mark-to-exit P&L the reviews "
                       "recorded, floored at 0; no policy chosen in "
                       "hindsight")}
    body["content_sha256"] = _sha(body)
    body["value_add_id"] = "xva:" + body["content_sha256"][:32]
    return body


async def _marks(conn, thesis_id: str) -> list:
    rows = await conn.fetch(
        "SELECT venue_economics->'marks' AS m FROM "
        " xavier_management_assessments WHERE thesis_id=$1 "
        " ORDER BY assessed_at", thesis_id)
    return [x for x in (_j(r["m"]) for r in rows) if isinstance(x, dict)]


async def _actual_outcome(conn, thesis: dict) -> dict:
    """Xavier's own outcome for the position, or pnl None while open."""
    slug, side = thesis["us_market_slug"], thesis["holding_side"]
    if thesis["position_kind"] == K_PAPER:
        # the position's own fills and latest settlement version (the
        # ledger's realized P&L of a fully disposed position: sale proceeds
        # + settlement payout - acquisition cost incl. fees)
        f = await conn.fetchrow(
            "SELECT coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) "
            "         AS bought, "
            "       coalesce(sum(gross_usd + fee_usd) FILTER ("
            "         WHERE direction='BUY'), 0) AS buy_cost, "
            "       coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) "
            "         AS sold, "
            "       coalesce(sum(gross_usd - fee_usd) FILTER ("
            "         WHERE direction='SELL'), 0) AS proceeds, "
            "       coalesce(sum(fee_usd), 0) AS fees, "
            "       coalesce(sum(gross_usd), 0) AS turnover "
            "  FROM paper_fills WHERE group_id=$1 AND us_market_slug=$2 "
            "   AND holding_side=$3", thesis["group_id"], slug, side)
        st = await conn.fetchrow(
            "SELECT qty, payout_usd, outcome FROM paper_settlements "
            " WHERE group_id=$1 AND us_market_slug=$2 AND holding_side=$3 "
            " ORDER BY version DESC LIMIT 1", thesis["group_id"], slug, side)
        settled = 0.0 if st is None else float(st["qty"])
        open_qty = float(f["bought"]) - float(f["sold"]) - settled
        if float(f["bought"]) <= 0 or open_qty > 1e-9:
            return {"pnl_usd": None, "why": "POSITION_OPEN"}
        pnl = (float(f["proceeds"]) + (0.0 if st is None else float(
            st["payout_usd"])) - float(f["buy_cost"]))
        return {"pnl_usd": round(pnl, 6), "fees_usd": float(f["fees"]),
                "turnover_usd": float(f["turnover"]),
                "outcome_basis": "SETTLED" if st is not None else "EXITED",
                "basis": ("paper fills and settlement: realized P&L of the "
                          "closed position")}
    fills = await _live_fills(conn, thesis["group_id"], slug)
    lc = live_cash(fills)
    if lc["held"] <= 1e-9:
        return {"pnl_usd": lc["cash_usd"], "fees_usd": lc["fees_usd"],
                "turnover_usd": lc["turnover_usd"], "outcome_basis": "EXITED",
                "basis": "venue fills: the actual position was closed"}
    st = await settlement_outcome(conn, slug, side)
    pay = _payout(st, thesis)
    if pay is None:
        return {"pnl_usd": None, "why": "ACTUAL_POSITION_OPEN"}
    return {"pnl_usd": round(lc["cash_usd"] + lc["held"] * float(pay), 6),
            "fees_usd": lc["fees_usd"], "turnover_usd": lc["turnover_usd"],
            "outcome_basis": "SETTLED",
            "basis": "venue fills + the venue's settlement of the remainder"}


async def compute_value_add(conn, thesis: dict) -> dict:
    """Compute and persist (idempotently) one thesis's value-add."""
    actual = await _actual_outcome(conn, thesis)
    if actual.get("pnl_usd") is None:
        return {"ok": False, "why": actual.get("why")}
    settle = await settlement_outcome(conn, thesis["us_market_slug"],
                                      thesis["holding_side"])
    body = value_add_compute(thesis, actual=actual, settle=settle,
                             marks=await _marks(conn, thesis["thesis_id"]))
    if body is None:
        return {"ok": False, "why": "OUTCOME_NOT_KNOWN"}
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO xavier_value_add (value_add_id, thesis_id, "
                " position_kind, group_id, status, outcome_basis, "
                " outcome_evidence, counterfactuals, incremental, method, "
                " content_sha256) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,"
                " $8::jsonb,$9::jsonb,$10,$11) ON CONFLICT DO NOTHING",
                body["value_add_id"], body["thesis_id"],
                body["position_kind"], body["group_id"], body["status"],
                body["outcome_basis"], _dumps(body["outcome_evidence"]),
                _dumps(body["counterfactuals"]), _dumps(body["incremental"]),
                body["method"], body["content_sha256"])
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "VALUE_ADD_WRITE_FAILED:%s"
                % type(exc).__name__}
    return {"ok": True, "value_add_id": body["value_add_id"],
            "status": body["status"]}


CANDIDATES_SQL = """
    SELECT t.* FROM xavier_entry_theses t
     WHERE NOT EXISTS (SELECT 1 FROM xavier_value_add v
                        WHERE v.thesis_id = t.thesis_id AND v.status = 'FINAL')
       AND ((t.position_kind = 'PAPER'
             AND (EXISTS (SELECT 1 FROM paper_settlements s
                           WHERE s.group_id = t.group_id
                             AND s.us_market_slug = t.us_market_slug
                             AND s.holding_side = t.holding_side)
                  OR (SELECT coalesce(sum(CASE WHEN f.direction = 'BUY'
                                               THEN f.qty ELSE -f.qty END), 0)
                        FROM paper_fills f
                       WHERE f.group_id = t.group_id
                         AND f.us_market_slug = t.us_market_slug
                         AND f.holding_side = t.holding_side) <= 0))
         OR (t.position_kind = 'ACTUAL'
             AND (EXISTS (SELECT 1 FROM smalllive_handoffs h
                           WHERE h.handoff_id = t.position_ref
                             AND h.state = 'CLOSED')
                  OR EXISTS (SELECT 1 FROM external_valuations ev
                              WHERE ev.us_market_slug = t.us_market_slug
                                AND ev.outcome_basis IS NOT NULL))))
     ORDER BY t.recorded_at LIMIT 200
"""


#: THE STEP IS NEVER STARVED (production 2026-10-07): it runs after
#: Xavier's reviews, which exhaust the pass budget every pass, so it checked
#: 0 theses per pass and 57 settled positions with a thesis had no value-add
#: row (xavier_value_add stuck at 7). Each pass now computes at least
#: VALUE_ADD_MIN_PER_PASS theses whatever the budget (a handful of small
#: reads each), and a thesis whose outcome is not known yet is not retried
#: for VALUE_ADD_RETRY_S, so pending ones never hold the head of the queue.
VALUE_ADD_MIN_PER_PASS = 8
VALUE_ADD_RETRY_S = 600.0
_VA_ATTEMPTED: dict = {}


async def step_value_add(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP: every thesis without a FINAL value-add is
    (re)computed when its outcome is known -- at least
    VALUE_ADD_MIN_PER_PASS per pass, more within the pass budget."""
    if not await has_schema(conn):
        return {"refusal": "MIGRATION_206_NOT_APPLIED"}
    # only positions whose outcome can be known: closed / settled paper
    # positions; actual positions closed or with venue outcome evidence
    rows = await conn.fetch(CANDIDATES_SQL)
    now = time.monotonic()
    out = {"candidates": len(rows), "checked": 0, "written": 0,
           "pending": 0, "skipped_recently_pending": 0, "why_pending": {}}
    for r in rows:
        tid = r["thesis_id"]
        if now - _VA_ATTEMPTED.get(tid, -1e18) < VALUE_ADD_RETRY_S:
            out["skipped_recently_pending"] += 1
            continue
        if out["checked"] >= VALUE_ADD_MIN_PER_PASS and \
                time.monotonic() > float(ctx.get("deadline") or 1e18):
            out["budget_exhausted"] = True
            break
        out["checked"] += 1
        got = await compute_value_add(conn, _thesis_row(r))
        if got.get("ok"):
            out["written"] += 1
            _VA_ATTEMPTED.pop(tid, None)
        else:
            out["pending"] += 1
            _VA_ATTEMPTED[tid] = now
            w = str(got.get("why") or "UNKNOWN")[:60]
            out["why_pending"][w] = out["why_pending"].get(w, 0) + 1
    if len(_VA_ATTEMPTED) > 20000:
        _VA_ATTEMPTED.clear()
    return out


def _va_row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("outcome_evidence", "counterfactuals", "incremental"):
            v = _j(v)
        elif hasattr(v, "timestamp"):
            v = v.timestamp()
        out[k] = v
    return out


async def value_add(conn, *, group_id: str | None = None,
                    thesis_id: str | None = None, limit: int = 200) -> list:
    """THE READ FUNCTION: the latest value-add row per thesis."""
    if not await has_schema(conn):
        return []
    rows = await conn.fetch(
        "SELECT DISTINCT ON (thesis_id) * FROM xavier_value_add "
        " WHERE ($1::text IS NULL OR group_id = $1) "
        "   AND ($2::text IS NULL OR thesis_id = $2) "
        " ORDER BY thesis_id, (status = 'FINAL') DESC, computed_at DESC "
        " LIMIT $3", group_id, thesis_id, max(1, min(int(limit), 1000)))
    return [_va_row(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════
# THE READ MODEL (GET /api/command/xavier/management)
# ═════════════════════════════════════════════════════════════════════

def _assessment_view(r) -> dict | None:
    if r is None:
        return None
    a = {}
    for k, v in dict(r).items():
        if k in ("venue_economics", "thesis_detail", "alternatives",
                 "reallocate", "policy", "valuation"):
            v = _j(v)
        elif hasattr(v, "timestamp"):
            v = v.timestamp()
        a[k] = v
    return a


#: the read-time context looks for a newer valuation this far back (a
#: recommendation whose source is older than this is STALE regardless)
CONTEXT_VALUATION_LOOKBACK_S = 3600.0

#: THE LATEST VALUATION OF EACH PAPER GROUP'S OWN CONTRACT (the measure's
#: own contract identity; xavier_freshness.LATEST_VALUATION_SQL)
LATEST_VALUATION_SQL = XF.LATEST_VALUATION_SQL


async def validity_context(conn, items, *, now: float) -> dict:
    """WHAT HAPPENED SINCE EACH RECOMMENDATION, for the read-time validity
    (xavier_freshness.validity): the latest valuation of the paper group's
    own contract, the held market's latest venue mark (best exit of the
    held side in the latest observed book), the in-process PinnAPI change
    of the held slug (pinnapi_held; None in a process without the feed).
    `items`: [(kind, group_id, slug, holding_side)]. Read only; a failed
    read leaves that context out (named in `unchecked`), never invents it.
    """
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_simulator as SIM
    out: dict = {"by_group": {}, "unchecked": []}
    paper = [(g, s, side) for k, g, s, side in items
             if k == K_PAPER and g and s]
    groups = sorted({g for g, _, _ in paper})
    slugs = sorted({s for _, s, _ in paper})
    lv: dict = {}
    if groups:
        try:
            for r in await conn.fetch(LATEST_VALUATION_SQL, groups,
                                      float(now)
                                      - CONTEXT_VALUATION_LOOKBACK_S):
                lv[r["group_id"]] = {"id": r["id"],
                                     "probability": _f(r["probability"]),
                                     "observed_at": _epoch(r["observed_at"])}
        except Exception as exc:                                # noqa: BLE001
            out["unchecked"].append("LATEST_VALUATION_UNREADABLE:%s"
                                    % type(exc).__name__)
    books: dict = {}
    if slugs:
        try:
            for r in await conn.fetch(
                    "SELECT DISTINCT ON (us_market_slug) us_market_slug, "
                    "       bids, offers, error, observed_at "
                    "  FROM paper_book_observations "
                    " WHERE us_market_slug = ANY($1::text[]) "
                    " ORDER BY us_market_slug, observed_at DESC", slugs):
                books[r["us_market_slug"]] = r
        except Exception as exc:                                # noqa: BLE001
            out["unchecked"].append("VENUE_MARK_UNREADABLE:%s"
                                    % type(exc).__name__)
    try:
        from .. import pinnapi_held as PH
    except Exception:                                           # noqa: BLE001
        PH = None                                               # noqa: N806
    for g, s, side in paper:
        ctx: dict = {"latest_valuation": lv.get(g)}
        b = books.get(s)
        if b is not None and not b["error"]:
            lvls = SIM.levels_for({"bids": L._j(b["bids"]) or [],
                                   "offers": L._j(b["offers"]) or []},
                                  direction="SELL",
                                  holding_side=side)["levels"]
            ctx["mark_now"] = lvls[0]["price"] if lvls else None
        ctx["feed_change_at"] = None if PH is None else PH.changed_at(s)
        out["by_group"][g] = ctx
    return out


def _config_limit() -> float | None:
    """THE EXISTING freshness limit as the paper session configures it
    (entry.pinnacle_max_age_s = ext_pinnacle_loop.PINNACLE_MAX_AGE_S), the
    fallback when a stored row recorded none. Never defines one."""
    try:
        from .. import bettor_paper_session as S
        return float(S.default_config()["entry"]["pinnacle_max_age_s"])
    except Exception:                                           # noqa: BLE001
        return None


async def _group_refs(conn, groups: list) -> dict:
    """group -> (slug, holding_side, event_start_at of a pre-event thesis)
    for paper groups (their ENTRY order; the thesis). Never raises."""
    out: dict = {}
    if not groups:
        return out
    try:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (group_id) group_id, us_market_slug, "
                "       holding_side FROM paper_orders "
                " WHERE group_id = ANY($1::text[]) AND role = 'ENTRY' "
                " ORDER BY group_id, created_at", groups):
            out[r["group_id"]] = {"slug": r["us_market_slug"],
                                  "side": r["holding_side"]}
        if await has_schema(conn):
            for r in await conn.fetch(
                    "SELECT group_id, event_start_at, probability_limit_s "
                    "  FROM xavier_entry_theses WHERE position_kind = 'PAPER'"
                    "   AND group_id = ANY($1::text[])", groups):
                d = out.setdefault(r["group_id"], {})
                d["event_start_at"] = _epoch(r["event_start_at"])
                d["limit_s"] = _f(r["probability_limit_s"])
    except Exception:                                           # noqa: BLE001
        pass
    return out


async def gate_paper_reviews(conn, rows: list, *, now: float | None = None,
                             latest_only: bool = True) -> list:
    """THE READ LAYER FOR paper_xavier_reviews ROWS (owner P0): each row's
    recommendation re-judged at `now` (xavier_freshness.of_review: the
    review's own measure, the newer valuation of its contract, the venue
    mark now, the event start; a row that is not its group's newest review
    is INVALID as superseded unless `latest_only` says every row given is
    the newest). The stored word is kept in `recorded_recommendation`; the
    alternatives are completed to the six options. Never raises: on a
    context failure the time rule alone still applies."""
    at = float(now if now is not None else time.time())
    rows = [dict(r) for r in rows]
    groups = sorted({r.get("group_id") for r in rows if r.get("group_id")})
    refs = await _group_refs(conn, groups)
    ctx = await validity_context(
        conn, [(K_PAPER, g, (refs.get(g) or {}).get("slug"),
                (refs.get(g) or {}).get("side")) for g in groups], now=at)
    newest: dict = {}
    if not latest_only:
        for r in rows:
            g, t = r.get("group_id"), _epoch(r.get("reviewed_at"))
            if t is not None and (g not in newest or t > newest[g][0]):
                newest[g] = (t, r.get("review_id"))
    out = []
    for r in rows:
        g = r.get("group_id")
        c = dict(ctx["by_group"].get(g) or {})
        ref = refs.get(g) or {}
        nid = None
        if not latest_only and g in newest and \
                newest[g][1] != r.get("review_id"):
            nid = newest[g][1]
        blk = XF.of_review(
            r, now=at, limit_s=ref.get("limit_s") or _config_limit(),
            latest_valuation=c.get("latest_valuation"),
            feed_change_at=c.get("feed_change_at"),
            mark_now=c.get("mark_now"),
            event_start_at=ref.get("event_start_at"),
            newer_assessment_id=nid)
        gr = XF.gated(r, blk)
        m = _j(r.get("measure")) or {}
        if "alternatives" in r:
            alts = _j(r.get("alternatives")) or {}
            if isinstance(alts, dict):
                flat = list(alts.get("candidates") or []) + list(
                    alts.get("not_rankable") or [])
            else:
                flat = alts if isinstance(alts, list) else []
            gr["alternatives_complete"] = XF.complete_alternatives(
                flat, evidence_state=m.get("evidence_state")
                if isinstance(m, dict) else None)
        out.append(gr)
    return out


def assessment_validity(a: dict | None, *, thesis: dict | None, now: float,
                        ctx: dict | None = None) -> dict | None:
    """THE READ-TIME STATE OF ONE STORED ASSESSMENT (pure): its own
    valuation (migration 222) or the one derived from its recorded age; the
    newer valuation / venue mark / PinnAPI change from `ctx`; the event
    start from the thesis."""
    if a is None:
        return None
    c = dict(ctx or {})
    ve = a.get("venue_economics") or {}
    if not isinstance(ve, dict):
        ve = {}
    return XF.of_assessment(
        a, now=now, limit_s=((thesis or {}).get("probability_limit_s")
                             or _config_limit()),
        latest_valuation=c.get("latest_valuation"),
        feed_change_at=c.get("feed_change_at"),
        mark_at_assessment=ve.get("best_exit"), mark_now=c.get("mark_now"),
        event_start_at=(thesis or {}).get("event_start_at"),
        newer_assessment_id=c.get("newer_assessment_id"))


def position_view(*, kind: str, group_id: str, ref: dict, thesis: dict |
                  None, a: dict | None, va: dict | None, cadence_s: float,
                  now: float, ctx: dict | None = None) -> dict:
    """One managed position as the Command Centre shows it. Pure. The
    recommendation is GATED at read time: `recommendation` is the action
    only while CURRENT and the state (STALE / INVALID /
    WAITING_FOR_FRESH_EVIDENCE / MANAGEMENT_UNAVAILABLE_STALE_INPUT)
    otherwise; the stored word stays in `recorded_recommendation`."""
    last = None if a is None else a.get("assessed_at")
    due = None if last is None else last + float(cadence_s)
    td = (a or {}).get("thesis_detail") or {}
    ve = (a or {}).get("venue_economics") or {}
    fr = assessment_validity(a, thesis=thesis, now=now, ctx=ctx)
    ev_state = (a or {}).get("evidence_state")
    alts = (None if a is None else XF.complete_alternatives(
        a.get("alternatives"), evidence_state=ev_state))
    return {
        "position_kind": kind, "group_id": group_id, **ref,
        "latest_review": None if a is None else {
            "review_id": a.get("review_id"),
            "assessment_id": a.get("assessment_id"),
            "at": last, "trigger": a.get("trigger"),
            "latency_s": a.get("review_latency_s"),
            "latency_bound_s": a.get("latency_bound_s"),
            "within_bound": a.get("within_bound")},
        "next_review_due_at": due,
        "review_overdue": None if due is None else now > due + float(
            cadence_s) * REREVIEW_GRACE_FACTOR,
        "why_no_review": (None if a is not None else
                          "no assessed Xavier review of this position yet"),
        "evidence": None if a is None else {
            "state": a.get("evidence_state"),
            "probability": a.get("probability"),
            "source": a.get("probability_source"),
            "age_s": a.get("probability_age_s"),
            "discretionary_permitted": a.get("discretionary_permitted")},
        "thesis": {"thesis_id": (thesis or {}).get("thesis_id"),
                   "state": (a or {}).get("thesis_state") or (
                       TH_NONE if thesis is None else None),
                   "entry_probability": (thesis or {}).get(
                       "entry_probability"),
                   "probability_source": (thesis or {}).get(
                       "probability_source"),
                   "entry_ev_usd": (thesis or {}).get("entry_ev_usd"),
                   "entered_at": (thesis or {}).get("entered_at"),
                   "evidence_expires_at": (thesis or {}).get(
                       "evidence_expires_at"),
                   "thesis_expires_at": (thesis or {}).get(
                       "thesis_expires_at"),
                   "expiry_basis": (thesis or {}).get("expiry_basis"),
                   "detail": td or None},
        "alternatives": alts,
        # THE GATED RECOMMENDATION (owner P0): the action word only while
        # CURRENT; otherwise the state. Holding a position is not a HOLD
        # recommendation.
        "recommendation": None if fr is None else fr[
            "display_recommendation"],
        "recommendation_state": None if fr is None else fr[
            "recommendation_state"],
        "current_recommendation": None if fr is None else fr[
            "current_recommendation"],
        "recorded_recommendation": (a or {}).get("recommendation"),
        "management_state": None if fr is None else fr["management_state"],
        "freshness": fr,
        # ONE SHAPE FOR EVERY XAVIER DECISION (review id / time, valuation
        # id / version / time, age, limit, superseded_by)
        "decision": None if fr is None else XF.decision(
            fr, review_id=a.get("review_id"),
            reviewed_at=a.get("assessed_at")),
        "position_held": {"open_qty": ref.get("open_qty"),
                          "is": ("a fact about the book; NOT a HOLD "
                                 "recommendation")},
        "reallocate": (a or {}).get("reallocate"),
        "venue_economics": ve or None,
        "policy": (a or {}).get("policy"),
        "value_add": va,
        "counterfactuals_at_entry": (thesis or {}).get("counterfactuals")}


async def management_view(conn, *, limit: int = 100,
                          now: float | None = None) -> dict:
    """EVERY MANAGED POSITION (paper and actual): evidence state, thesis
    state, alternatives, policy state and value-add. Read only."""
    from . import xavier_small_live_policy as XSP
    at = float(now if now is not None else time.time())
    limit = max(1, min(int(limit), 500))
    policy = await XSP.load_review_record(conn)
    out: dict[str, Any] = {
        "version": VERSION, "read_at": at, "read_only": True,
        "places_orders": False, "policy": policy,
        "loop": {
            "paper": {"first_review_bound_s": PAPER_FIRST_REVIEW_BOUND_S,
                      "cadence": "paper_sessions.config.cadence."
                                 "xavier_backstop_s (60 s default)",
                      "triggers": [T_FIRST, T_FILL, T_MARKET, T_BACKSTOP]},
            "actual": {"first_review_bound_s": ACTUAL_FIRST_REVIEW_BOUND_S,
                       "cadence": "execmirror.MANAGEMENT_EVERY_S per handoff",
                       "triggers": [T_FIRST, T_FILL, T_BACKSTOP]}},
        "rules": {
            "evidence_states": list(EVIDENCE_STATES),
            "thesis_states": list(THESIS_STATES),
            "no_discretion_on_stale": (
                "EXIT / REDUCE / REALLOCATE are recommended only on "
                "FRESH_CURRENT_PROBABILITY"),
            "no_hold_by_default": (
                "a review on non-fresh evidence recommends nothing: "
                "WAITING_FOR_FRESH_EVIDENCE (stale probability) or "
                "MANAGEMENT_UNAVAILABLE_STALE_INPUT (none); a held position "
                "is not a HOLD recommendation"),
            "recommendation_states": list(XF.STATES),
            "read_time_validity": (
                "every recommendation is re-judged at read time: STALE once "
                "now > its probability's source_at + its freshness limit, "
                "INVALID on a newer valuation, a PinnAPI change, a venue-"
                "mark move >= %.2f USD, the event starting or a newer "
                "assessment; only CURRENT shows the action"
                % XF.VENUE_MARK_INVALIDATION_USD),
            "reallocate": "SHADOW recommendation only; never an order",
            "verified_hedge": ("only with proven settlement/payoff "
                               "compatibility; otherwise not ranked"),
            "value_add": "counterfactuals frozen at entry; no hindsight"},
        "positions": [], "summary": {}}
    if not await has_schema(conn):
        out["status"] = "UNAVAILABLE"
        out["why"] = "MIGRATION_206_NOT_APPLIED"
        return out
    from .. import execmirror as EM
    backstop = 60.0
    try:
        b = await conn.fetchval(
            "SELECT (config->'cadence'->>'xavier_backstop_s')::float8 "
            "  FROM paper_sessions ORDER BY started_at DESC LIMIT 1")
        backstop = float(b) if b is not None else backstop
    except Exception:                                           # noqa: BLE001
        pass
    refs: list = []
    for r in await conn.fetch(
            "SELECT h.handoff_id, h.group_id, h.first_fill_at, h.strategy, "
            "       h.account_id, o.us_market_slug, o.holding_side, "
            "       coalesce(c.open_qty, 0) AS open_qty, "
            "       EXISTS (SELECT 1 FROM paper_settlements s "
            "                WHERE s.group_id = h.group_id) AS settled "
            "  FROM paper_handoffs h JOIN paper_orders o "
            "    ON o.order_id = h.entry_order_id "
            "  LEFT JOIN (" + L.CANONICAL_OPEN_POSITIONS_SQL + ") c "
            "    ON c.group_id = h.group_id "
            "   AND c.us_market_slug = o.us_market_slug "
            "   AND c.holding_side = o.holding_side "
            " ORDER BY h.first_fill_at DESC LIMIT $1", limit):
        # canonical: the position's own open qty (latest settlement already
        # subtracted), never "any settlement in the group"
        open_ = L.is_open(r["open_qty"])
        refs.append((K_PAPER, r["group_id"], {
            "handoff_id": r["handoff_id"], "market": r["us_market_slug"],
            "holding_side": r["holding_side"], "strategy": r["strategy"],
            "first_fill_at": _epoch(r["first_fill_at"]),
            "open_qty": _f(r["open_qty"]),
            "state": "OPEN" if open_ else "CLOSED",
            "label": "PAPER POSITION (SIMULATED)"}, backstop))
    if await conn.fetchval("SELECT to_regclass('smalllive_handoffs') "
                           "IS NOT NULL"):
        for r in await conn.fetch(
                "SELECT handoff_id, group_id, us_market_slug, opened_intent,"
                "       live_held, first_live_fill_at, state "
                "  FROM smalllive_handoffs "
                " ORDER BY first_live_fill_at DESC LIMIT $1", limit):
            refs.append((K_ACTUAL, r["group_id"], {
                "handoff_id": r["handoff_id"], "market": r["us_market_slug"],
                "holding_side": side_of_intent(r["opened_intent"]),
                "strategy": None,
                "first_fill_at": _epoch(r["first_live_fill_at"]),
                "open_qty": _f(r["live_held"]), "state": r["state"],
                "label": "ACTUAL POSITION"}, EM.MANAGEMENT_EVERY_S))
    groups = sorted({g for _, g, _, _ in refs})
    latest, theses, vas = {}, {}, {}
    if groups:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (group_id, position_kind) * FROM "
                " xavier_management_assessments WHERE group_id = ANY($1) "
                " ORDER BY group_id, position_kind, assessed_at DESC, "
                " assessment_id DESC", groups):
            latest[(r["group_id"], r["position_kind"])] = _assessment_view(r)
        for r in await conn.fetch(
                "SELECT * FROM xavier_entry_theses WHERE group_id = ANY($1)",
                groups):
            theses[(r["group_id"], r["position_kind"])] = _thesis_row(r)
        for v in await value_add(conn, limit=1000):
            if v["group_id"] in groups:
                vas[(v["group_id"], v["position_kind"])] = v
    by_ev, by_th, by_rs = {}, {}, {}
    overdue = missing = shadow = 0
    vctx = await validity_context(
        conn, [(k, g, ref.get("market"), ref.get("holding_side"))
               for k, g, ref, _ in refs if ref["state"] == "OPEN"], now=at)
    for kind, g, ref, cad in refs:
        a = latest.get((g, kind))
        pv = position_view(kind=kind, group_id=g, ref=ref,
                           thesis=theses.get((g, kind)), a=a,
                           va=vas.get((g, kind)), cadence_s=cad, now=at,
                           ctx=(vctx["by_group"].get(g)
                                if kind == K_PAPER else None))
        if ref["state"] == "OPEN":
            st = (pv["evidence"] or {}).get("state") or "NO_REVIEW"
            by_ev[st] = by_ev.get(st, 0) + 1
            rs = pv["recommendation_state"] or "NO_REVIEW"
            by_rs[rs] = by_rs.get(rs, 0) + 1
            ts = pv["thesis"]["state"] or "NO_REVIEW"
            by_th[ts] = by_th.get(ts, 0) + 1
            overdue += 1 if pv["review_overdue"] else 0
            missing += 1 if a is None else 0
            shadow += 1 if (pv["reallocate"] or {}).get("recommended") else 0
        else:
            pv["review_overdue"] = None
        out["positions"].append(pv)
    out["summary"] = {
        "positions": len(refs),
        "open_positions": sum(1 for r in refs if r[2]["state"] == "OPEN"),
        "by_evidence_state": by_ev, "by_thesis_state": by_th,
        "by_recommendation_state": by_rs,
        "validity_context_unchecked": vctx["unchecked"],
        "reviews_overdue": overdue, "open_without_review": missing,
        "reallocate_shadow_recommended": shadow,
        "value_add_rows": len(vas),
        "policy_status": policy["status"],
        "policy_approved": policy["approved"]}
    out["status"] = "OK" if refs else "EMPTY"
    out["why"] = None if refs else "NO_POSITION_HAS_BEEN_HANDED_TO_XAVIER"
    return out
