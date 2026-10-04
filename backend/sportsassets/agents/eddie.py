"""EDDIE: HEAD OF EXECUTION -- SHADOW EXECUTION ESTIMATES, NO AUTHORITY.

Eddie's one job is to preserve as much of Derek's theoretical edge as
possible between decision and fill. He does NOT predict outcomes: the
probability is Derek's (the decision's recorded p_blended / p_pinnacle /
p_internal). For every Derek candidate (a paper ENTER decision) he records,
in SHADOW (migration 217, `eddie_execution_estimates`):

  THEORETICAL_EDGE            p - mid of the holding side's recorded book
  SPREAD COST                 best acquisition price - mid (half spread)
  EXPECTED_SLIPPAGE           VWAP of walking the book for the proposed qty
                              - best acquisition price
  EXPECTED_FEES               recorded fee (decision economics) per contract,
                              else the ledger's fee schedule, labelled
  EXPECTED_ADVERSE_SELECTION  the mean recorded markout of paper fills of the
                              same style (mid at fill - mid 30..300 s later)
  EXPECTED_FILL_PROBABILITY   the recorded fill rate of terminal paper orders
                              of the same style (MARKETABLE/IOC, RESTING/GTD)
  EXPECTED_TIME_TO_FILL       the recorded median order->first fill time
  EXPECTED_CAPITAL_HOURS      capital reserved x expected time to fill
  MAX EXECUTABLE SIZE         contracts whose marginal net edge stays > 0
  EXPECTED_EXECUTION_LOSS     spread + slippage + fees + adverse selection
  EXPECTED_NET_EXECUTABLE_EDGE  theoretical edge - execution loss

Every field the records cannot support is NULL with a named reason, never a
silent zero. The recommendation (EXECUTE_NOW / REST_LIMIT / SPLIT / WAIT /
SKIP_EXECUTION) is SHADOW ONLY: nothing reads it to act.

THE HARD RULE (code AND database): Eddie can never recommend executing a
candidate whose expected executable EV is <= 0 -- or unmeasured -- whatever
its theoretical EV. `enforce_hard_rule` is applied last to every
recommendation, and migration 217's CHECK refuses the row otherwise.

WHAT EDDIE CANNOT DO, BY CONSTRUCTION. Submit, cancel or request an order;
dispatch; reserve capital; approve, activate or promote anything. This
module imports no order, venue or execution path, writes only
eddie_execution_estimates / eddie_execution_outcomes, declares itself the
acting agent on every write transaction (pos_authority.act_as), and the
database refuses EDDIE on every order / intent / fill / approval / control
table (migration 217).
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from . import pos_authority as PA
from . import pos_evidence as _PE  # noqa: F401  (registers evidence kinds)
from . import registry as R

EDDIE = R.EDDIE
VERSION = "EDDIE_EXECUTION_ESTIMATOR_V1"
AUTHORITY = "SHADOW_ONLY"

EXECUTE_NOW, REST_LIMIT, SPLIT, WAIT, SKIP = (
    "EXECUTE_NOW", "REST_LIMIT", "SPLIT", "WAIT", "SKIP_EXECUTION")
RECOMMENDATIONS = (EXECUTE_NOW, REST_LIMIT, SPLIT, WAIT, SKIP)
#: The recommendations that mean "execute" -- each needs a positive
#: expected net executable edge AND a positive expected executable EV.
EXECUTING = (EXECUTE_NOW, REST_LIMIT, SPLIT)
TAKER, MAKER, SPLIT_TAKER = "TAKER_MARKETABLE", "MAKER_RESTING", "SPLIT_TAKER"

#: a book older than this at decision time is not an executable book
MAX_BOOK_AGE_S = 120.0
#: recorded history needed before a rate / markout is a measurement
MIN_HISTORY = 20
MARKOUT_FROM_S, MARKOUT_TO_S = 30.0, 300.0
HISTORY_LOOKBACK_S = 14 * 86400
HISTORY_LIMIT = 2000

R_NO_SCHEMA = "MIGRATION_217_IS_NOT_APPLIED"
R_DB_REFUSED = "THE_DATABASE_REFUSED_THE_ESTIMATE"
R_NO_SUCH_DECISION = "NO_SUCH_DEREK_CANDIDATE"

#: The evaluation dimensions Eddie covers. Each estimate records, per
#: dimension, MEASURED (with its source) or UNAVAILABLE (with the reason).
DIMENSIONS = (
    "maker_vs_taker", "limit_vs_marketable", "ioc_vs_resting",
    "queue_position", "spread", "depth", "fill_probability", "time_to_fill",
    "cancel_replace_economics", "order_splitting", "price_improvement",
    "adverse_selection", "market_impact", "latency", "venue_microstructure",
    "time_to_event", "live_vs_pregame", "capital_turnover")


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _r(v, n=6):
    return None if v is None else round(float(v), n)


# ═════════════════════════════════════════════════════════════════════
# THE BOOK (pure)
# ═════════════════════════════════════════════════════════════════════

def side_ladders(book: dict | None, holding_side: str) -> dict:
    """The holding side's acquisition ladder (what buying costs), its exit
    ladder (what selling it back pays) and the mid between their best
    levels, from a recorded book {bids, offers}. Pure."""
    md = book if isinstance(book, dict) else None
    if not md or holding_side not in ("LONG", "SHORT"):
        return {"ok": False, "why": "NO_RECORDED_BOOK" if not md
                else "UNKNOWN_HOLDING_SIDE"}
    try:
        acq = SIM.levels_for(md, direction="BUY", holding_side=holding_side)
        ext = SIM.levels_for(md, direction="SELL", holding_side=holding_side)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "BOOK_UNREADABLE_%s" % type(exc).__name__}
    a, x = acq.get("levels") or [], ext.get("levels") or []
    if not a:
        return {"ok": False, "why": "NO_ACQUISITION_LEVELS"}
    best_a = float(a[0]["price"])
    best_x = float(x[0]["price"]) if x else None
    mid = (best_a + best_x) / 2.0 if best_x is not None else None
    return {"ok": True, "acquisition": a, "exit": x, "best_acquisition": best_a,
            "best_exit": best_x, "mid": mid,
            "why": None if mid is not None else "NO_EXIT_LEVELS_NO_MID"}


def walk_book(levels: list, qty: float) -> dict:
    """VWAP of buying `qty` through `levels` (best first). Pure."""
    left, cost, took = float(qty), 0.0, 0.0
    for lv in levels:
        if left <= 1e-9:
            break
        q = min(left, float(lv["qty"]))
        cost += q * float(lv["price"])
        took += q
        left -= q
    return {"filled_qty": took, "vwap": (cost / took) if took else None,
            "shortfall": max(0.0, left)}


def fee_per_contract(qty: float, price: float, recorded_fee_usd=None) -> dict:
    """The recorded fee per contract, else the ledger's schedule. Pure."""
    rq = _num(qty)
    rf = _num(recorded_fee_usd)
    if rf is not None and rq:
        return {"pp": rf / rq, "basis": "DECISION_RECORDED_FEES_USD"}
    if rq and _num(price) is not None:
        try:
            fee = float(L._fee(None, rq, price, None))
            return {"pp": fee / rq, "basis": "LEDGER_DEFAULT_FEE_SCHEDULE"}
        except Exception as exc:                                # noqa: BLE001
            return {"pp": None, "basis": "FEE_SCHEDULE_FAILED_%s"
                    % type(exc).__name__}
    return {"pp": None, "basis": "NO_QTY_OR_PRICE"}


def max_executable_qty(levels: list, *, p: float, fee_pp: float,
                       adverse_pp: float) -> float:
    """Contracts whose MARGINAL net edge (p - level price - fee - adverse
    selection) stays > 0, walking the book best first. Pure."""
    total = 0.0
    for lv in levels:
        if p - float(lv["price"]) - fee_pp - adverse_pp <= 0:
            break
        total += float(lv["qty"])
    return total


# ═════════════════════════════════════════════════════════════════════
# THE ESTIMATE AND THE RECOMMENDATION (pure)
# ═════════════════════════════════════════════════════════════════════

def probability_of(c: dict) -> tuple:
    for k in ("p_blended", "p_pinnacle", "p_internal"):
        v = _num(c.get(k))
        if v is not None and 0 < v < 1:
            return v, k
    return None, None


def recorded_fee_usd(economics) -> Any:
    e = _j(economics) if economics is not None else None
    if not isinstance(e, dict):
        return None
    acq = e.get("acquisition") if isinstance(e.get("acquisition"), dict) \
        else {}
    return acq.get("fees_usd", e.get("fees_usd"))


def enforce_hard_rule(rec: str, *, net_pp, ev_usd, fill_p) -> tuple:
    """THE HARD RULE, applied last: an executing recommendation without a
    positive expected net executable edge, a positive expected executable EV
    and a positive fill probability becomes SKIP_EXECUTION (or WAIT when the
    economics are unmeasured). Returns (recommendation, overridden?)."""
    if rec not in EXECUTING:
        return rec, False
    n, e, f = _num(net_pp), _num(ev_usd), _num(fill_p)
    if n is not None and n > 0 and e is not None and e > 0 \
            and f is not None and f > 0:
        return rec, False
    if n is None or e is None or f is None:
        return WAIT, True
    return SKIP, True


def estimate(candidate: dict, book_row: dict | None, history: dict, *,
             now: float) -> dict:
    """ONE SHADOW EXECUTION ESTIMATE FOR ONE DEREK CANDIDATE. Pure.

    `candidate`: the paper decision (decision_id, decided_at, us_market_slug,
    holding_side, proposed_qty, limit_price, p_*, economics). `book_row`:
    the recorded book it was decided on (obs_id, observed_at, bids, offers,
    tick, market_state) or None. `history`: `history_stats` output."""
    unmeasured: dict[str, str] = {}
    qty = _num(candidate.get("proposed_qty"))
    limit = _num(candidate.get("limit_price"))
    side = candidate.get("holding_side")
    p, p_basis = probability_of(candidate)
    decided = _num(_ep(candidate.get("decided_at")))
    book = None
    age = None
    if book_row:
        book = {"bids": _j(book_row.get("bids")) or [],
                "offers": _j(book_row.get("offers")) or []}
        obs_at = _num(_ep(book_row.get("observed_at")))
        if obs_at is not None and decided is not None:
            age = max(0.0, decided - obs_at)
    lad = side_ladders(book, side) if book else {"ok": False,
                                                 "why": "NO_RECORDED_BOOK"}
    mid = lad.get("mid") if lad.get("ok") else None
    best = lad.get("best_acquisition") if lad.get("ok") else None
    levels = lad.get("acquisition") or []

    theo = (p - mid) if p is not None and mid is not None else None
    if theo is None:
        unmeasured["theoretical_edge"] = (
            "NO_PROBABILITY_RECORDED" if p is None
            else lad.get("why") or "NO_MID")
    spread = (best - mid) if best is not None and mid is not None else None
    if spread is None:
        unmeasured["spread_cost"] = lad.get("why") or "NO_MID"
    # the NAIVE execution: walk the whole proposed size now
    naive_walk = walk_book(levels, qty) if levels and qty else None
    fee = fee_per_contract(qty, (naive_walk or {}).get("vwap") or best
                           or limit,
                           recorded_fee_usd(candidate.get("economics")))
    if fee["pp"] is None:
        unmeasured["fees"] = fee["basis"]
    adv_t = history.get("adverse_selection", {}).get(TAKER)
    adv_m = history.get("adverse_selection", {}).get(MAKER)
    adverse = adv_t.get("value") if adv_t else None
    # the economic size first; the taker walk is of THAT size (a SPLIT
    # takes only the contracts whose marginal edge survives)
    max_q = None
    max_basis = None
    if levels and p is not None and fee["pp"] is not None:
        max_q = max_executable_qty(levels, p=p, fee_pp=fee["pp"],
                                   adverse_pp=adverse or 0.0)
        max_basis = ("MARGINAL_NET_EDGE_AFTER_FEES_AND_ADVERSE_SELECTION"
                     if adverse is not None else
                     "UPPER_BOUND_EXCLUDES_UNMEASURED_ADVERSE_SELECTION")
    else:
        unmeasured["max_executable_size"] = (
            "NO_PROBABILITY_RECORDED" if p is None else
            "NO_FEE" if fee["pp"] is None else lad.get("why") or "NO_LEVELS")
    q_exec = (min(qty, max_q) if max_q else qty) if qty else None
    walk = walk_book(levels, q_exec) if levels and q_exec else None
    slip = None
    if walk and walk["vwap"] is not None and best is not None:
        slip = walk["vwap"] - best
    else:
        unmeasured["slippage"] = ("NO_PROPOSED_QTY" if not qty
                                  else lad.get("why") or "NO_LEVELS")
    naive_loss = None
    if naive_walk and naive_walk["vwap"] is not None and None not in (
            mid, fee["pp"], adverse):
        naive_loss = naive_walk["vwap"] - mid + fee["pp"] + adverse
    if adverse is None:
        unmeasured["adverse_selection"] = (adv_t or {}).get(
            "why") or "NO_MARKOUT_HISTORY"
    fr_t = history.get("fill_rate", {}).get(TAKER) or {}
    fr_m = history.get("fill_rate", {}).get(MAKER) or {}
    ttf_t = history.get("time_to_fill", {}).get(TAKER) or {}
    ttf_m = history.get("time_to_fill", {}).get(MAKER) or {}

    # ── TAKER (cross the spread now) ─────────────────────────────────
    loss_t = None
    if None not in (spread, slip, fee["pp"], adverse):
        loss_t = spread + slip + fee["pp"] + adverse
    net_t = (theo - loss_t) if theo is not None and loss_t is not None \
        else None
    f_t = fr_t.get("value")
    ev_t = (f_t * net_t * q_exec) if None not in (f_t, net_t, q_exec) \
        else None

    # ── MAKER (rest at the holding side's best exit price: no spread
    #    paid, a recorded fill rate and the resting markout instead) ──
    rest_px = lad.get("best_exit") if lad.get("ok") else None
    net_m = None
    adverse_m = adv_m.get("value") if adv_m else None
    fee_m = fee_per_contract(qty, rest_px, None) if rest_px else {"pp": None}
    if None not in (p, rest_px, fee_m["pp"], adverse_m):
        net_m = p - rest_px - fee_m["pp"] - adverse_m
    f_m = fr_m.get("value")
    ev_m = (f_m * net_m * qty) if None not in (f_m, net_m, qty) else None

    # ── THE RECOMMENDATION ───────────────────────────────────────────
    stale = age is not None and age > MAX_BOOK_AGE_S
    style = TAKER
    if theo is not None and theo <= 0:
        rec, why = SKIP, ("NO_THEORETICAL_EDGE_AT_THE_MID: p %.4f vs mid "
                          "%.4f" % (p, mid))
    elif theo is None:
        rec, why = WAIT, "THEORETICAL_EDGE_UNMEASURED: %s" % unmeasured.get(
            "theoretical_edge")
    elif stale:
        rec, why = WAIT, ("BOOK_STALE_AT_DECISION: %.1f s > %.0f s"
                          % (age, MAX_BOOK_AGE_S))
    else:
        options = []
        if ev_t is not None and ev_t > 0 and net_t > 0:
            if qty and q_exec is not None and q_exec + 1e-9 < qty:
                options.append((ev_t, SPLIT, SPLIT_TAKER,
                                "TAKER_NET_POSITIVE_ONLY_FOR_%s_OF_%s"
                                % (_r(q_exec, 2), _r(qty, 2))))
            else:
                options.append((ev_t, EXECUTE_NOW, TAKER,
                                "TAKER_NET_EXECUTABLE_EDGE_POSITIVE"))
        if ev_m is not None and ev_m > 0 and net_m > 0:
            options.append((ev_m, REST_LIMIT, MAKER,
                            "RESTING_EV_EXCEEDS_TAKER" if ev_t
                            else "RESTING_NET_EXECUTABLE_EDGE_POSITIVE"))
        if options:
            options.sort(key=lambda o: -o[0])
            _ev, rec, style, why = options[0]
        elif net_t is not None or net_m is not None:
            rec, why = SKIP, ("EXPECTED_EXECUTABLE_EV_NOT_POSITIVE: taker "
                              "net %s pp / EV %s USD, maker net %s pp / EV "
                              "%s USD" % (_r(net_t, 4), _r(ev_t, 2),
                                          _r(net_m, 4), _r(ev_m, 2)))
        else:
            rec, why = WAIT, ("EXECUTION_ECONOMICS_UNMEASURED: " + ", ".join(
                "%s=%s" % kv for kv in sorted(unmeasured.items())))
    if style == MAKER:
        net, ev, fill_p, ttf = net_m, ev_m, f_m, ttf_m.get("value")
        loss = (theo - net_m) if theo is not None and net_m is not None \
            else None
        adverse_used, fee_used = adverse_m, fee_m["pp"]
        fill_why, ttf_why = fr_m.get("why"), ttf_m.get("why")
    else:
        net, ev, fill_p, ttf = net_t, ev_t, f_t, ttf_t.get("value")
        loss, adverse_used, fee_used = loss_t, adverse, fee["pp"]
        fill_why, ttf_why = fr_t.get("why"), ttf_t.get("why")
    rec, overridden = enforce_hard_rule(rec, net_pp=net, ev_usd=ev,
                                        fill_p=fill_p)
    if overridden:
        why = "HARD_RULE: expected executable EV not positive or unmeasured "\
              "(net %s pp, EV %s USD)" % (_r(net, 4), _r(ev, 2))
    if fill_p is None:
        unmeasured["fill_probability"] = fill_why or "NO_FILL_HISTORY"
    if ttf is None:
        unmeasured["time_to_fill"] = ttf_why or "NO_FILL_TIME_HISTORY"
    if net is None:
        unmeasured["net_executable_edge"] = "COMPONENTS_UNMEASURED: " + \
            ", ".join(sorted(k for k in unmeasured
                             if k != "net_executable_edge")) \
            if unmeasured else "UNMEASURED"
    if adverse_used is None and "adverse_selection" not in unmeasured:
        unmeasured["adverse_selection"] = (adv_m or {}).get(
            "why") or "NO_MARKOUT_HISTORY"
    cap_px = rest_px if style == MAKER else ((walk or {}).get("vwap") or best)
    cap_h = None
    if qty and cap_px is not None and ttf is not None:
        cap_h = qty * cap_px * ttf / 3600.0
    else:
        unmeasured["capital_hours"] = ("NO_TIME_TO_FILL" if ttf is None
                                       else "NO_QTY_OR_PRICE")
    if fee_used is None and "fees" not in unmeasured:
        unmeasured["fees"] = "NO_FEE_FOR_STYLE"
    if loss is None:
        unmeasured.setdefault("execution_loss", "COMPONENTS_UNMEASURED")
    dims = dimensions_of(candidate, book_row, lad, history, walk=walk,
                         levels=levels)
    refs = [{"kind": "paper_decisions", "id": str(candidate["decision_id"])}]
    if book_row and book_row.get("obs_id") is not None:
        refs.append({"kind": "paper_book_observations",
                     "id": str(book_row["obs_id"])})
    return {
        "estimate_id": estimate_id_for(candidate["decision_id"]),
        "decision_id": str(candidate["decision_id"]),
        "estimator_version": VERSION, "estimated_at": float(now),
        "decided_at": decided, "us_market_slug": candidate.get(
            "us_market_slug"),
        "holding_side": side, "proposed_qty": qty, "limit_price": limit,
        "probability": p, "probability_basis": p_basis,
        "book_obs_id": (book_row or {}).get("obs_id"), "book_age_s": _r(age),
        "theoretical_edge_pp": _r(theo), "spread_cost_pp": _r(spread),
        "expected_fees_pp": _r(fee_used),
        "expected_slippage_pp": _r(slip if style != MAKER else 0.0
                                   if rest_px is not None else slip),
        "expected_adverse_selection_pp": _r(adverse_used),
        "expected_execution_loss_pp": _r(loss),
        "expected_net_executable_edge_pp": _r(net),
        "expected_fill_probability": _r(fill_p),
        "expected_time_to_fill_s": _r(ttf, 3),
        "expected_capital_hours": _r(cap_h, 4),
        "max_executable_qty": _r(max_q, 4),
        "expected_executable_ev_usd": _r(ev, 4),
        "execution_style": style, "recommendation": rec,
        "recommendation_reason": why, "unmeasured": unmeasured,
        "dimensions": dims,
        "inputs": {
            "mid": _r(mid), "best_acquisition": _r(best),
            "best_exit": _r(rest_px), "walk": walk,
            "fee_basis": fee["basis"], "max_size_basis": max_basis,
            "styles": {
                TAKER: {"net_pp": _r(net_t), "ev_usd": _r(ev_t, 4),
                        "fill_probability": _r(f_t),
                        "execution_loss_pp": _r(loss_t),
                        "executable_qty": _r(q_exec, 4)},
                MAKER: {"net_pp": _r(net_m), "ev_usd": _r(ev_m, 4),
                        "fill_probability": _r(f_m),
                        "rest_price": _r(rest_px),
                        "adverse_selection_pp": _r(adverse_m)}},
            "naive_execution_loss_pp": _r(naive_loss),
            "naive_walk": naive_walk,
            # the interface views (pos_iface_eddie_execution) read these
            "fee_pp_taker": _r(fee["pp"]), "fee_pp_maker": _r(fee_m["pp"]),
            "planned_vwap": _r(rest_px if style == MAKER else
                               (walk or {}).get("vwap")),
            "planned_qty": _r(qty if style == MAKER else q_exec, 4),
            "history": {k: history.get(k) for k in (
                "fill_rate", "time_to_fill", "adverse_selection",
                "latency")}},
        "evidence_refs": refs, "authority": AUTHORITY,
        "production_effect": "NONE"}


def dimensions_of(c: dict, book_row, lad: dict, history: dict, *, walk,
                  levels) -> dict:
    """Per evaluation dimension: MEASURED with its source, or UNAVAILABLE
    with the reason. Pure."""
    def m(src, **kw):
        return dict({"status": "MEASURED", "source": src}, **kw)

    def u(why):
        return {"status": "UNAVAILABLE", "why": why}
    fr = history.get("fill_rate", {})
    both = (fr.get(TAKER) or {}).get("value") is not None and \
        (fr.get(MAKER) or {}).get("value") is not None
    style_dim = (m("paper_orders terminal fill rates by order type",
                   taker=(fr.get(TAKER) or {}).get("value"),
                   maker=(fr.get(MAKER) or {}).get("value"))
                 if both else u("FILL_HISTORY_BELOW_%d_PER_STYLE"
                                % MIN_HISTORY))
    tick = _j((book_row or {}).get("tick")) if book_row else None
    ms = (book_row or {}).get("market_state")
    lat = history.get("latency") or {}
    q = history.get("queue") or {}
    return {
        "maker_vs_taker": style_dim,
        "limit_vs_marketable": style_dim,
        "ioc_vs_resting": style_dim,
        "queue_position": (m("paper_orders.queue_ahead_qty (simulated queue,"
                             " paper only)", orders=q.get("n"))
                           if q.get("n") else u("NO_QUEUE_RECORD")),
        "spread": (m("paper_book_observations", spread_pp=_r(
            (lad.get("best_acquisition") or 0) - (lad.get("mid") or 0)))
            if lad.get("ok") and lad.get("mid") is not None
            else u(lad.get("why") or "NO_BOOK")),
        "depth": (m("paper_book_observations",
                    levels=len(levels), displayed_qty=_r(sum(
                        float(x["qty"]) for x in levels), 2))
                  if levels else u(lad.get("why") or "NO_BOOK")),
        "fill_probability": ((m("paper_orders terminal fill rate"))
                             if (fr.get(TAKER) or {}).get("value") is not None
                             else u((fr.get(TAKER) or {}).get("why")
                                    or "NO_FILL_HISTORY")),
        "time_to_fill": ((m("paper_fills first fill - paper_orders.created_at"))
                         if (history.get("time_to_fill", {}).get(TAKER) or {})
                         .get("value") is not None
                         else u("NO_FILL_TIME_HISTORY")),
        "cancel_replace_economics": u(
            "NO_CANCEL_REPLACE_SEQUENCE_IS_RECORDED_AS_ONE_ORDER_CHAIN"),
        "order_splitting": (m("book walk of the proposed qty",
                              shortfall=_r((walk or {}).get("shortfall"), 2))
                            if walk else u("NO_BOOK_OR_QTY")),
        "price_improvement": u("MEASURED_ONLY_AFTER_A_FILL_SEE_OUTCOMES"),
        "adverse_selection": (
            m("paper_fills markout vs paper_book_observations %d-%d s later"
              % (MARKOUT_FROM_S, MARKOUT_TO_S))
            if (history.get("adverse_selection", {}).get(TAKER) or {})
            .get("value") is not None else
            u((history.get("adverse_selection", {}).get(TAKER) or {})
              .get("why") or "NO_MARKOUT_HISTORY")),
        "market_impact": u("PAPER_ORDERS_DO_NOT_MOVE_RECORDED_BOOKS_NO_"
                           "IMPACT_RESPONSE_IS_OBSERVABLE"),
        "latency": (m("paper_orders.created_at - decided_at",
                      decision_to_submit_median_s=lat.get("value"))
                    if lat.get("value") is not None
                    else u(lat.get("why") or "NO_ORDER_HISTORY")),
        "venue_microstructure": (m("paper_book_observations.tick",
                                   tick=tick) if tick else
                                 u("NO_TICK_RECORDED_ON_THE_BOOK")),
        "time_to_event": u("NO_EVENT_START_IS_LINKED_TO_THE_DECISION_RECORD"),
        "live_vs_pregame": (m("paper_book_observations.market_state",
                              market_state=ms) if ms
                            else u("NO_MARKET_STATE_ON_THE_BOOK")),
        "capital_turnover": u("SETTLEMENT_TIME_NOT_LINKED_ONLY_EXECUTION_"
                              "CAPITAL_HOURS_ARE_ESTIMATED"),
    }


def estimate_id_for(decision_id) -> str:
    raw = json.dumps([VERSION, str(decision_id)])
    return "eex:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24]


# ═════════════════════════════════════════════════════════════════════
# RECORDED HISTORY (bounded reads)
# ═════════════════════════════════════════════════════════════════════

def _style_of(order_type) -> str:
    return MAKER if str(order_type or "").upper() == "RESTING" else TAKER


def _rate(num, den, why_none):
    if den < MIN_HISTORY:
        return {"value": None, "numerator": num, "denominator": den,
                "why": "%s_BELOW_%d (n=%d)" % (why_none, MIN_HISTORY, den)}
    return {"value": round(num / den, 6), "numerator": num,
            "denominator": den, "why": None}


def summarise_history(orders: list, fills: list, markouts: list) -> dict:
    """Fill rates, time to fill, latency and adverse selection per style
    from bounded recorded rows. Pure."""
    out: dict[str, Any] = {"fill_rate": {}, "time_to_fill": {},
                           "adverse_selection": {}}
    terminal = ("FILLED", "PARTIALLY_FILLED", "EXPIRED", "CANCELED")
    for style in (TAKER, MAKER):
        o = [r for r in orders if _style_of(r.get("order_type")) == style
             and r.get("state") in terminal]
        filled = sum(1 for r in o if r.get("state") == "FILLED")
        out["fill_rate"][style] = _rate(filled, len(o),
                                        "TERMINAL_ORDERS")
        t = sorted(float(r["ttf_s"]) for r in fills
                   if _style_of(r.get("order_type")) == style
                   and _num(r.get("ttf_s")) is not None
                   and float(r["ttf_s"]) >= 0)
        out["time_to_fill"][style] = (
            {"value": round(statistics.median(t), 3), "n": len(t),
             "why": None} if len(t) >= MIN_HISTORY else
            {"value": None, "n": len(t),
             "why": "FILL_TIMES_BELOW_%d (n=%d)" % (MIN_HISTORY, len(t))})
        mk = [float(r["markout_pp"]) for r in markouts
              if _style_of(r.get("order_type")) == style
              and _num(r.get("markout_pp")) is not None]
        out["adverse_selection"][style] = (
            {"value": round(max(0.0, statistics.fmean(mk)), 6),
             "raw_mean_pp": round(statistics.fmean(mk), 6), "n": len(mk),
             "why": None,
             "basis": "max(0, mean(mid at fill - mid %d..%d s later))"
                      % (MARKOUT_FROM_S, MARKOUT_TO_S)}
            if len(mk) >= MIN_HISTORY else
            {"value": None, "n": len(mk),
             "why": "MARKOUTS_BELOW_%d (n=%d)" % (MIN_HISTORY, len(mk))})
    lat = sorted(float(r["submit_s"]) for r in orders
                 if _num(r.get("submit_s")) is not None
                 and float(r["submit_s"]) >= 0)
    out["latency"] = ({"value": round(statistics.median(lat), 3),
                       "n": len(lat), "why": None}
                      if len(lat) >= MIN_HISTORY else
                      {"value": None, "n": len(lat),
                       "why": "ORDER_LATENCIES_BELOW_%d (n=%d)"
                              % (MIN_HISTORY, len(lat))})
    out["queue"] = {"n": sum(1 for r in orders
                             if _num(r.get("queue_ahead_qty")) is not None)}
    return out


def _mid_of(bids, offers, side) -> float | None:
    lad = side_ladders({"bids": _j(bids) or [], "offers": _j(offers) or []},
                       side)
    return lad.get("mid") if lad.get("ok") else None


async def history_stats(conn, *, now: float) -> dict:
    """THE RECORDED EXECUTION HISTORY Eddie's estimates rest on. Bounded:
    the last HISTORY_LOOKBACK_S, at most HISTORY_LIMIT rows per read."""
    lo = now - HISTORY_LOOKBACK_S
    orders = [dict(r) for r in await conn.fetch(
        "SELECT order_type, state, queue_ahead_qty, "
        "       extract(epoch FROM created_at - decided_at) AS submit_s "
        "  FROM paper_orders WHERE created_at BETWEEN to_timestamp($1) "
        "   AND to_timestamp($2) ORDER BY created_at DESC LIMIT $3",
        lo, now, HISTORY_LIMIT)]
    fills = [dict(r) for r in await conn.fetch(
        "SELECT o.order_type, extract(epoch FROM min(f.filled_at) - "
        "       o.created_at) AS ttf_s FROM paper_fills f "
        "  JOIN paper_orders o USING (order_id) "
        " WHERE f.filled_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        " GROUP BY o.order_id, o.order_type, o.created_at "
        " ORDER BY o.created_at DESC LIMIT $3", lo, now, HISTORY_LIMIT)]
    rows = await conn.fetch(
        "SELECT f.fill_id, f.holding_side, f.price, o.order_type, "
        "       b0.bids AS b0b, b0.offers AS b0o, b1.bids AS b1b, "
        "       b1.offers AS b1o "
        "  FROM paper_fills f JOIN paper_orders o USING (order_id) "
        "  LEFT JOIN paper_book_observations b0 ON b0.obs_id = f.book_obs_id "
        "  LEFT JOIN LATERAL (SELECT bids, offers FROM paper_book_observations"
        "        b WHERE b.us_market_slug = f.us_market_slug "
        "          AND b.observed_at BETWEEN f.filled_at + make_interval("
        "              secs => $4) AND f.filled_at + make_interval(secs => $5)"
        "        ORDER BY b.observed_at LIMIT 1) b1 ON true "
        " WHERE f.filled_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        " ORDER BY f.filled_at DESC LIMIT $3", lo, now, HISTORY_LIMIT,
        MARKOUT_FROM_S, MARKOUT_TO_S)
    markouts = []
    for r in rows:
        if r["b0b"] is None or r["b1b"] is None:
            continue
        m0 = _mid_of(r["b0b"], r["b0o"], r["holding_side"])
        m1 = _mid_of(r["b1b"], r["b1o"], r["holding_side"])
        if m0 is None or m1 is None:
            continue
        markouts.append({"order_type": r["order_type"],
                         "markout_pp": m0 - m1, "fill_id": r["fill_id"]})
    out = summarise_history(orders, fills, markouts)
    out["read_at"] = now
    return out


# ═════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('eddie_execution_estimates') IS NOT NULL"
        ) is True
    except Exception:                                           # noqa: BLE001
        return False


_COLS = ("estimate_id", "decision_id", "estimator_version", "decided_at",
         "us_market_slug", "holding_side", "proposed_qty", "limit_price",
         "probability", "probability_basis", "book_obs_id", "book_age_s",
         "theoretical_edge_pp", "spread_cost_pp", "expected_fees_pp",
         "expected_slippage_pp", "expected_adverse_selection_pp",
         "expected_execution_loss_pp", "expected_net_executable_edge_pp",
         "expected_fill_probability", "expected_time_to_fill_s",
         "expected_capital_hours", "max_executable_qty",
         "expected_executable_ev_usd", "execution_style", "recommendation",
         "recommendation_reason")
_TS = {"decided_at"}
_JSONB = ("unmeasured", "dimensions", "inputs", "evidence_refs")


async def record_estimate(conn, est: dict) -> dict:
    """PERSIST ONE ESTIMATE, idempotently on (decision, estimator version),
    as EDDIE (the transaction declares him the acting agent). Never
    raises."""
    PA.assert_may(EDDIE, "write.execution_estimates")
    rec, _ = enforce_hard_rule(est["recommendation"],
                               net_pp=est["expected_net_executable_edge_pp"],
                               ev_usd=est["expected_executable_ev_usd"],
                               fill_p=est["expected_fill_probability"])
    if rec != est["recommendation"]:
        return {"ok": False, "refusal": "HARD_RULE_VIOLATION_REFUSED",
                "estimate_id": est.get("estimate_id")}
    cols = list(_COLS) + ["estimated_at"] + list(_JSONB)
    vals, ph = [], []
    for i, c in enumerate(cols, start=1):
        v = est.get(c)
        if c in _TS or c == "estimated_at":
            ph.append("CASE WHEN $%d::float8 IS NULL THEN NULL ELSE "
                      "to_timestamp($%d::float8) END" % (i, i))
        elif c in _JSONB:
            ph.append("$%d::jsonb" % i)
            v = json.dumps(v if v is not None else {}, default=str)
        else:
            ph.append("$%d" % i)
        vals.append(v)
    try:
        async with conn.transaction():
            await PA.act_as(conn, EDDIE)
            res = await conn.execute(
                "INSERT INTO eddie_execution_estimates (%s) VALUES (%s) "
                "ON CONFLICT DO NOTHING" % (", ".join(cols), ", ".join(ph)),
                *vals)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": R_DB_REFUSED,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"ok": True, "estimate_id": est["estimate_id"],
            "created": res.endswith("1"),
            "recommendation": est["recommendation"],
            "production_effect": "NONE"}


async def candidates(conn, *, now: float, lookback_s: float,
                     limit: int) -> list:
    """Derek's candidates not yet estimated by this estimator version: the
    paper ENTER decisions in the window, oldest first."""
    rows = await conn.fetch(
        "SELECT d.decision_id, d.decided_at, d.us_market_slug, "
        "       d.holding_side, d.proposed_qty, d.limit_price, d.p_blended, "
        "       d.p_pinnacle, d.p_internal, d.book_obs_id, d.economics "
        "  FROM paper_decisions d WHERE d.verdict = 'ENTER' "
        "   AND d.decided_at BETWEEN to_timestamp($1) AND to_timestamp($2) "
        "   AND NOT EXISTS (SELECT 1 FROM eddie_execution_estimates e "
        "        WHERE e.decision_id = d.decision_id "
        "          AND e.estimator_version = $3) "
        " ORDER BY d.decided_at, d.decision_id LIMIT $4",
        now - lookback_s, now, VERSION, limit)
    return [dict(r) for r in rows]


async def book_for(conn, c: dict) -> dict | None:
    """The recorded book the decision was made on (its book_obs_id), else
    the latest observation of the market at or before the decision."""
    if c.get("book_obs_id") is not None:
        r = await conn.fetchrow(
            "SELECT obs_id, observed_at, bids, offers, tick, market_state "
            "  FROM paper_book_observations WHERE obs_id = $1",
            int(c["book_obs_id"]))
        if r is not None:
            return dict(r)
    if not c.get("us_market_slug") or c.get("decided_at") is None:
        return None
    r = await conn.fetchrow(
        "SELECT obs_id, observed_at, bids, offers, tick, market_state "
        "  FROM paper_book_observations WHERE us_market_slug = $1 "
        "   AND observed_at <= $2 ORDER BY observed_at DESC LIMIT 1",
        c["us_market_slug"], c["decided_at"])
    return dict(r) if r is not None else None


async def estimate_decision(conn, decision_id: str, *, now: float | None = None,
                            history: dict | None = None) -> dict:
    """Estimate (and persist) one named Derek candidate. Never raises."""
    at = float(now if now is not None else time.time())
    if not await schema(conn):
        return {"ok": False, "refusal": R_NO_SCHEMA}
    r = await conn.fetchrow(
        "SELECT decision_id, decided_at, us_market_slug, holding_side, "
        "       proposed_qty, limit_price, p_blended, p_pinnacle, p_internal,"
        "       book_obs_id, economics FROM paper_decisions "
        " WHERE decision_id = $1", str(decision_id))
    if r is None:
        return {"ok": False, "refusal": R_NO_SUCH_DECISION}
    c = dict(r)
    hist = history if history is not None else await history_stats(conn,
                                                                   now=at)
    est = estimate(c, await book_for(conn, c), hist, now=at)
    got = await record_estimate(conn, est)
    return dict(got, estimate=est)


# ── PREDICTED vs REALIZED ────────────────────────────────────────────

def outcome_of(est: dict, orders: list, fills: list, after_book: dict | None,
               *, now: float) -> dict | None:
    """REALIZED execution of an estimated candidate from its paper orders
    and fills. Pure. None when nothing filled."""
    if not fills:
        return None
    unmeasured: dict[str, str] = {}
    inputs = _j(est.get("inputs")) or {}
    qty = sum(float(f["qty"]) for f in fills)
    vwap = sum(float(f["qty"]) * float(f["price"]) for f in fills) / qty
    fee_usd = sum(float(f.get("fee_usd") or 0) for f in fills)
    fee_pp = fee_usd / qty if qty else None
    mid = _num(inputs.get("mid"))
    best = _num(inputs.get("best_acquisition"))
    spread_c = (min(vwap, best) - mid) if None not in (mid, best) else None
    slip = (vwap - best) if best is not None else None
    adv = None
    if after_book and mid is not None:
        m1 = _mid_of(after_book.get("bids"), after_book.get("offers"),
                     est.get("holding_side"))
        if m1 is not None:
            adv = mid - m1
    if adv is None:
        unmeasured["realized_adverse_selection"] = (
            "NO_BOOK_%d_TO_%d_S_AFTER_THE_FILL" % (MARKOUT_FROM_S,
                                                   MARKOUT_TO_S))
    loss = None
    if mid is not None and fee_pp is not None:
        loss = (vwap - mid) + fee_pp + (adv or 0.0)
    else:
        unmeasured["realized_execution_loss"] = "NO_DECISION_MID"
    pred = _num(est.get("expected_execution_loss_pp"))
    if pred is None:
        unmeasured["predicted_execution_loss"] = (
            (_j(est.get("unmeasured")) or {}).get("execution_loss")
            or "THE_ESTIMATE_LEFT_IT_UNMEASURED")
    decided = _num(_ep(est.get("decided_at")))
    first_order = min((o for o in orders), key=lambda o: _ep(o["created_at"]),
                      default=None)
    first_fill = min(_ep(f["filled_at"]) for f in fills)
    d2s = (_ep(first_order["created_at"]) - decided) \
        if first_order is not None and decided is not None else None
    d2f = (first_fill - decided) if decided is not None else None
    cap = None
    if first_order is not None:
        reserved = _num(first_order.get("reserved_usd")) or (qty * vwap)
        cap = reserved * max(0.0, first_fill - _ep(first_order["created_at"])
                             ) / 3600.0
    unmeasured["submit_to_ack"] = "PAPER_SIMULATOR_HAS_NO_VENUE_ACK"
    unmeasured["ack_to_fill"] = "PAPER_SIMULATOR_HAS_NO_VENUE_ACK"
    refs = [{"kind": "eddie_execution_estimates", "id": est["estimate_id"]}]
    refs += [{"kind": "paper_fills", "id": str(f["fill_id"])}
             for f in fills[:20]]
    raw = json.dumps([est["estimate_id"], "PAPER"])
    return {
        "outcome_id": "eeo:%s" % hashlib.sha256(raw.encode()).hexdigest()[:24],
        "estimate_id": est["estimate_id"], "decision_id": est["decision_id"],
        "source": "PAPER", "measured_at": float(now), "filled_qty": qty,
        "fill_vwap": round(vwap, 6), "decision_mid": mid,
        "realized_fees_pp": _r(fee_pp),
        "realized_spread_cost_pp": _r(spread_c),
        "realized_slippage_pp": _r(slip),
        "realized_adverse_selection_pp": _r(adv),
        "realized_execution_loss_pp": _r(loss),
        "predicted_execution_loss_pp": _r(pred),
        "naive_execution_loss_pp": _num(inputs.get("naive_execution_loss_pp")),
        "decision_to_submit_s": _r(d2s, 3), "submit_to_ack_s": None,
        "ack_to_fill_s": None, "decision_to_fill_s": _r(d2f, 3),
        "capital_hours": _r(cap, 4), "unmeasured": unmeasured,
        "evidence_refs": refs}


async def _measure_one(conn, est: dict, *, now: float) -> str | None:
    orders = [dict(o) for o in await conn.fetch(
        "SELECT order_id, created_at, reserved_usd, limit_price, state "
        "  FROM paper_orders WHERE decision_id = $1", est["decision_id"])]
    fills = [dict(f) for f in await conn.fetch(
        "SELECT f.fill_id, f.qty, f.price, f.fee_usd, f.filled_at, "
        "       f.us_market_slug FROM paper_fills f JOIN paper_orders o "
        " USING (order_id) WHERE o.decision_id = $1 ORDER BY f.filled_at",
        est["decision_id"])]
    after = None
    if fills:
        a = await conn.fetchrow(
            "SELECT bids, offers FROM paper_book_observations "
            " WHERE us_market_slug = $1 AND observed_at BETWEEN "
            "       $2::timestamptz + make_interval(secs => $3) AND "
            "       $2::timestamptz + make_interval(secs => $4) "
            " ORDER BY observed_at LIMIT 1",
            fills[-1]["us_market_slug"], fills[-1]["filled_at"],
            MARKOUT_FROM_S, MARKOUT_TO_S)
        after = dict(a) if a is not None else None
    o = outcome_of(est, orders, fills, after, now=now)
    if o is None:
        return None
    cols = [k for k in o if k not in ("unmeasured", "evidence_refs",
                                      "measured_at")]
    n = len(cols)
    await PA.act_as(conn, EDDIE)
    await conn.execute(
        "INSERT INTO eddie_execution_outcomes (%s, measured_at, unmeasured, "
        " evidence_refs) VALUES (%s, to_timestamp($%d), $%d::jsonb, "
        " $%d::jsonb) ON CONFLICT DO NOTHING"
        % (", ".join(cols), ", ".join("$%d" % (i + 1) for i in range(n)),
           n + 1, n + 2, n + 3),
        *[o[k] for k in cols], float(now), json.dumps(o["unmeasured"]),
        json.dumps(o["evidence_refs"]))
    return o["outcome_id"]


async def record_outcomes(conn, *, now: float, limit: int = 20) -> dict:
    """Measure PREDICTED vs REALIZED for estimates whose candidate filled on
    paper and that have no outcome yet. Each in its own transaction declared
    as EDDIE. Bounded; never raises."""
    out = {"recorded": [], "errors": {}}
    rows = await conn.fetch(
        "SELECT e.* FROM eddie_execution_estimates e WHERE NOT EXISTS ("
        "  SELECT 1 FROM eddie_execution_outcomes x WHERE x.estimate_id = "
        "  e.estimate_id AND x.source = 'PAPER') AND EXISTS (SELECT 1 FROM "
        "  paper_orders o JOIN paper_fills f USING (order_id) WHERE "
        "  o.decision_id = e.decision_id) ORDER BY e.estimated_at LIMIT $1",
        limit)
    for r in rows:
        est = dict(r)
        try:
            async with conn.transaction():
                oid = await _measure_one(conn, est, now=now)
            if oid:
                out["recorded"].append(oid)
        except Exception as exc:                                # noqa: BLE001
            out["errors"][est["estimate_id"]] = "%s: %s" % (
                type(exc).__name__, str(exc)[:160])
    return out


# ═════════════════════════════════════════════════════════════════════
# READS
# ═════════════════════════════════════════════════════════════════════

def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in _JSONB or k in ("evidence_refs",):
            v = _j(v)
        elif hasattr(v, "as_tuple"):
            v = float(v)
        out[k] = _ep(v)
    return out


def _with_links(e: dict) -> dict:
    e["evidence"] = [dict(x, href=None) for x in (e.get("evidence_refs")
                                                  or [])]
    e["evidence"].append({"kind": "eddie_execution_estimates",
                          "id": e.get("estimate_id"),
                          "href": "/api/command/eddie/estimates/%s"
                                  % e.get("estimate_id")})
    return e


async def estimates(conn, *, limit: int = 50,
                    recommendation: str | None = None) -> list:
    rows = await conn.fetch(
        "SELECT * FROM eddie_execution_estimates WHERE ($1::text IS NULL OR "
        " recommendation = $1) ORDER BY estimated_at DESC, estimate_id "
        "LIMIT $2", recommendation, max(1, min(int(limit or 50), 500)))
    return [_with_links(_row(r)) for r in rows]


async def estimate_record(conn, estimate_id: str) -> dict | None:
    r = await conn.fetchrow(
        "SELECT * FROM eddie_execution_estimates WHERE estimate_id = $1",
        str(estimate_id))
    if r is None:
        return None
    outs = await conn.fetch(
        "SELECT * FROM eddie_execution_outcomes WHERE estimate_id = $1 "
        " ORDER BY measured_at", str(estimate_id))
    return {"estimate": _with_links(_row(r)),
            "outcomes": [_row(o) for o in outs], "production_effect": "NONE",
            "authority": AUTHORITY}


async def outcomes(conn, *, limit: int = 50) -> list:
    rows = await conn.fetch(
        "SELECT x.*, e.recommendation, e.theoretical_edge_pp, "
        "       e.expected_fill_probability "
        "  FROM eddie_execution_outcomes x JOIN eddie_execution_estimates e "
        "  USING (estimate_id) ORDER BY x.measured_at DESC LIMIT $1",
        max(1, min(int(limit or 50), 500)))
    return [_row(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════
# THE SCORECARD: numerator / denominator; null (UNAVAILABLE) until measured
# ═════════════════════════════════════════════════════════════════════

METRIC_SAMPLE = 5000
DEFINITIONS = {
    "edge_preservation_pct": ("mean over filled estimates with a positive "
                              "theoretical edge of (theoretical edge - "
                              "realized execution loss) / theoretical edge"),
    "decision_to_submit_s": "mean seconds from Derek's decision to the order",
    "submit_to_ack_s": "mean seconds from submission to the venue's ack",
    "ack_to_fill_s": "mean seconds from the venue's ack to the first fill",
    "decision_to_fill_s": "mean seconds from the decision to the first fill",
    "fill_rate": ("estimated candidates that filled / estimated candidates "
                  "whose orders reached a terminal state"),
    "fill_probability_calibration": (
        "Brier score of the predicted fill probability against filled (1) "
        "/ not filled (0), over estimates whose orders are terminal"),
    "slippage_pp": "mean realized slippage (fill VWAP - best price at decision)",
    "slippage_saved_lost_pp": ("mean (predicted slippage - realized "
                               "slippage); positive = better than predicted"),
    "spread_captured_pp": "mean (decision mid - fill VWAP): spread captured",
    "price_improvement_pp": "mean (limit price - fill VWAP) for buys",
    "adverse_selection_pp": "mean realized markout after the fill",
    "execution_alpha_pp": ("mean (naive taker execution loss at decision - "
                           "realized execution loss)"),
    "cancel_replace_effectiveness": ("filled after a cancel/replace / "
                                     "cancel/replace sequences"),
    "capital_hours_consumed": "sum of realized execution capital-hours",
    "capital_hours_saved": ("sum of realized capital-hours of candidates "
                            "Eddie recommended NOT to execute (SKIP / WAIT)"),
    "incremental_pnl_vs_naive_usd": (
        "counterfactual, ex-ante executable-edge basis (NOT settled P&L): "
        "sum over filled candidates Eddie recommended SKIP / WAIT of "
        "-(theoretical edge - realized execution loss) x filled qty; "
        "following an EXECUTE recommendation equals the naive action (0)"),
    "predicted_vs_realized_execution_loss_pp": (
        "mean |predicted - realized execution loss| over outcomes with both"),
}


def _metric(name, num, den, *, value=None, why=None, **extra) -> dict:
    measurable = why is None and bool(den)
    if measurable and value is None:
        value = round(num / den, 6)
    return dict({"name": name, "definition": DEFINITIONS[name],
                 "numerator": num if measurable else None,
                 "denominator": den, "value": value if measurable else None,
                 "measurable": measurable, "status": "MEASURED" if measurable
                 else "UNAVAILABLE",
                 "why": None if measurable else (
                     why or "NOTHING_TO_MEASURE_YET")}, **extra)


def _mean_metric(name, xs, why_none):
    xs = [float(x) for x in xs if _num(x) is not None]
    if not xs:
        return _metric(name, None, 0, why=why_none)
    return _metric(name, round(sum(xs), 6), len(xs),
                   value=round(sum(xs) / len(xs), 6))


def summarise_metrics(ests: list, outs: list, terminal: dict) -> dict:
    """THE SCORECARD (pure). `ests`: estimate rows; `outs`: outcome rows
    joined with their estimate's recommendation / theoretical edge / fill
    probability; `terminal`: {estimate_id: filled?} for estimates whose
    orders are terminal."""
    none_out = "NO_ESTIMATED_CANDIDATE_HAS_FILLED_YET"
    pres = [(float(o["theoretical_edge_pp"]) - float(
        o["realized_execution_loss_pp"])) / float(o["theoretical_edge_pp"])
        for o in outs if _num(o.get("theoretical_edge_pp")) and float(
            o["theoretical_edge_pp"]) > 0 and _num(
            o.get("realized_execution_loss_pp")) is not None]
    by_est = {e["estimate_id"]: e for e in ests}
    brier = [(float(by_est[k]["expected_fill_probability"]) - (1.0 if v
                                                               else 0.0)) ** 2
             for k, v in terminal.items() if k in by_est and _num(
                 by_est[k].get("expected_fill_probability")) is not None]
    slip_saved = []
    for o in outs:
        e = by_est.get(o["estimate_id"])
        if e and _num(e.get("expected_slippage_pp")) is not None and \
                _num(o.get("realized_slippage_pp")) is not None:
            slip_saved.append(float(e["expected_slippage_pp"]) - float(
                o["realized_slippage_pp"]))
    improve = []
    for o in outs:
        e = by_est.get(o["estimate_id"])
        if e and _num(e.get("limit_price")) is not None and \
                _num(o.get("fill_vwap")) is not None:
            improve.append(float(e["limit_price"]) - float(o["fill_vwap"]))
    alpha = [float(o["naive_execution_loss_pp"]) - float(
        o["realized_execution_loss_pp"]) for o in outs
        if _num(o.get("naive_execution_loss_pp")) is not None
        and _num(o.get("realized_execution_loss_pp")) is not None]
    skipped = [o for o in outs if o.get("recommendation") in (SKIP, WAIT)]
    incr = [-(float(o["theoretical_edge_pp"]) - float(
        o["realized_execution_loss_pp"])) * float(o["filled_qty"])
        for o in skipped if None not in (
            _num(o.get("theoretical_edge_pp")),
            _num(o.get("realized_execution_loss_pp")),
            _num(o.get("filled_qty")))]
    gap = [abs(float(o["predicted_execution_loss_pp"]) - float(
        o["realized_execution_loss_pp"])) for o in outs
        if _num(o.get("predicted_execution_loss_pp")) is not None
        and _num(o.get("realized_execution_loss_pp")) is not None]
    cap = [o.get("capital_hours") for o in outs]
    cap_saved = [o.get("capital_hours") for o in skipped]
    m = {
        "edge_preservation_pct": _mean_metric(
            "edge_preservation_pct", [x * 100 for x in pres], none_out),
        "decision_to_submit_s": _mean_metric(
            "decision_to_submit_s", [o.get("decision_to_submit_s")
                                     for o in outs], none_out),
        "submit_to_ack_s": _metric("submit_to_ack_s", None, 0,
                                   why="PAPER_SIMULATOR_HAS_NO_VENUE_ACK_"
                                       "AND_NO_ACTUAL_FILL_IS_LINKED"),
        "ack_to_fill_s": _metric("ack_to_fill_s", None, 0,
                                 why="PAPER_SIMULATOR_HAS_NO_VENUE_ACK_"
                                     "AND_NO_ACTUAL_FILL_IS_LINKED"),
        "decision_to_fill_s": _mean_metric(
            "decision_to_fill_s", [o.get("decision_to_fill_s")
                                   for o in outs], none_out),
        "fill_rate": _metric(
            "fill_rate", sum(1 for v in terminal.values() if v),
            len(terminal), why=None if terminal else
            "NO_ESTIMATED_CANDIDATE_HAS_A_TERMINAL_ORDER_YET"),
        "fill_probability_calibration": (
            _metric("fill_probability_calibration", round(sum(brier), 6),
                    len(brier), value=round(sum(brier) / len(brier), 6),
                    unit="brier")
            if len(brier) >= MIN_HISTORY else _metric(
                "fill_probability_calibration", None, len(brier),
                why="TERMINAL_ESTIMATES_BELOW_%d (n=%d)"
                    % (MIN_HISTORY, len(brier)))),
        "slippage_pp": _mean_metric("slippage_pp", [
            o.get("realized_slippage_pp") for o in outs], none_out),
        "slippage_saved_lost_pp": _mean_metric(
            "slippage_saved_lost_pp", slip_saved, none_out),
        "spread_captured_pp": _mean_metric("spread_captured_pp", [
            -float(o["realized_spread_cost_pp"]) for o in outs
            if _num(o.get("realized_spread_cost_pp")) is not None], none_out),
        "price_improvement_pp": _mean_metric("price_improvement_pp",
                                             improve, none_out),
        "adverse_selection_pp": _mean_metric("adverse_selection_pp", [
            o.get("realized_adverse_selection_pp") for o in outs],
            "NO_POST_FILL_BOOK_RECORDED_FOR_ANY_OUTCOME" if outs
            else none_out),
        "execution_alpha_pp": _mean_metric("execution_alpha_pp", alpha,
                                           none_out),
        "cancel_replace_effectiveness": _metric(
            "cancel_replace_effectiveness", None, 0,
            why="NO_CANCEL_REPLACE_SEQUENCE_IS_LINKED_TO_AN_ESTIMATE"),
        "capital_hours_consumed": (
            _metric("capital_hours_consumed", None, 0, why=none_out)
            if not [c for c in cap if _num(c) is not None] else _metric(
                "capital_hours_consumed", round(sum(float(c) for c in cap if
                                                    _num(c) is not None), 4),
                len([c for c in cap if _num(c) is not None]),
                value=round(sum(float(c) for c in cap if _num(c) is not None),
                            4))),
        "capital_hours_saved": (
            _metric("capital_hours_saved", None, 0,
                    why="NO_SKIP_OR_WAIT_CANDIDATE_HAS_FILLED_YET")
            if not [c for c in cap_saved if _num(c) is not None] else _metric(
                "capital_hours_saved", None, 1, value=round(sum(
                    float(c) for c in cap_saved if _num(c) is not None), 4))),
        "incremental_pnl_vs_naive_usd": (
            _metric("incremental_pnl_vs_naive_usd", round(sum(incr), 4),
                    len(incr), value=round(sum(incr), 4),
                    basis="EX_ANTE_EXECUTABLE_EDGE_NOT_SETTLED_PNL")
            if incr else _metric(
                "incremental_pnl_vs_naive_usd", None, 0,
                why="NO_FILLED_CANDIDATE_THAT_EDDIE_RECOMMENDED_NOT_TO_"
                    "EXECUTE")),
        "predicted_vs_realized_execution_loss_pp": _mean_metric(
            "predicted_vs_realized_execution_loss_pp", gap,
            "NO_OUTCOME_HAS_BOTH_PREDICTED_AND_REALIZED_LOSS"),
    }
    by_rec = {r: sum(1 for e in ests if e.get("recommendation") == r)
              for r in RECOMMENDATIONS}
    return {"metrics": m, "estimates_counted": len(ests),
            "outcomes_counted": len(outs), "by_recommendation": by_rec,
            "rule": "null means unmeasured (UNAVAILABLE), never zero",
            "authority": AUTHORITY}


async def metrics(conn) -> dict:
    """The scorecard over the most recent METRIC_SAMPLE estimates. Raises on
    a failed read (the API says UNAVAILABLE)."""
    ests = [_row(r) for r in await conn.fetch(
        "SELECT estimate_id, recommendation, expected_fill_probability, "
        "       expected_slippage_pp, limit_price, theoretical_edge_pp, "
        "       expected_net_executable_edge_pp, decision_id "
        "  FROM eddie_execution_estimates ORDER BY estimated_at DESC "
        " LIMIT $1", METRIC_SAMPLE)]
    outs = await outcomes(conn, limit=500)
    terminal: dict[str, bool] = {}
    if ests:
        for r in await conn.fetch(
                "SELECT e.estimate_id, bool_or(o.state IN ('FILLED', "
                "       'PARTIALLY_FILLED')) AS filled, bool_and(o.state IN "
                "       ('FILLED', 'PARTIALLY_FILLED', 'EXPIRED', "
                "        'CANCELED')) AS terminal "
                "  FROM eddie_execution_estimates e JOIN paper_orders o ON "
                "       o.decision_id = e.decision_id "
                " WHERE e.estimate_id = ANY($1::text[]) "
                " GROUP BY e.estimate_id", [e["estimate_id"] for e in ests]):
            if r["terminal"]:
                terminal[r["estimate_id"]] = bool(r["filled"])
    return summarise_metrics(ests, outs, terminal)


# ═════════════════════════════════════════════════════════════════════
# THE DESK AND SLACK: from his records only
# ═════════════════════════════════════════════════════════════════════

async def desk(conn) -> dict:
    """What Eddie's desk shows, every value from his records (null with
    the reason when there is none)."""
    st = await R.status_of(conn, EDDIE)
    latest = await estimates(conn, limit=1)
    cur = latest[0] if latest else None
    outs = await outcomes(conn, limit=1)
    met = await metrics(conn)
    pnl = met["metrics"]["incremental_pnl_vs_naive_usd"]
    alerts = []
    if st is None:
        alerts.append("NOT_REGISTERED_THE_RUNNER_HAS_NOT_STARTED")
    elif st.get("state") == R.S_FAILED:
        alerts.append("RUNNER_FAILED: %s" % (st.get("last_error") or ""))
    if cur and cur.get("recommendation") in (WAIT, SKIP):
        alerts.append("%s on %s: %s" % (cur["recommendation"],
                                        cur["decision_id"],
                                        cur["recommendation_reason"][:160]))
    return {
        "agent": "EDDIE", "name": "Eddie", "role": "Head of Execution",
        "authority": AUTHORITY,
        "heartbeat": {"state": (st or {}).get("state"),
                      "last_heartbeat_at": (st or {}).get(
                          "last_heartbeat_at"),
                      "activity": (st or {}).get("activity")},
        "current_task": (st or {}).get("activity"),
        "alerts": alerts,
        "current_analysis": None if cur is None else {
            "estimate_id": cur["estimate_id"],
            "candidate": {"decision_id": cur["decision_id"],
                          "us_market_slug": cur["us_market_slug"],
                          "holding_side": cur["holding_side"],
                          "proposed_qty": cur["proposed_qty"],
                          "limit_price": cur["limit_price"]},
            "recommendation": cur["recommendation"],
            "reason": cur["recommendation_reason"],
            "execution_policy": cur["execution_style"],
            "theoretical_edge_pp": cur["theoretical_edge_pp"],
            "expected_net_executable_edge_pp": cur[
                "expected_net_executable_edge_pp"],
            "expected_fill_probability": cur["expected_fill_probability"],
            "expected_slippage_pp": cur["expected_slippage_pp"],
            "expected_capital_hours": cur["expected_capital_hours"],
            "max_executable_qty": cur["max_executable_qty"],
            "book": {"mid": (cur.get("inputs") or {}).get("mid"),
                     "best_acquisition": (cur.get("inputs") or {}).get(
                         "best_acquisition"),
                     "best_exit": (cur.get("inputs") or {}).get("best_exit"),
                     "walk": (cur.get("inputs") or {}).get("walk")},
            "microstructure": {k: v for k, v in (cur.get("dimensions")
                                                 or {}).items()
                               if k in ("spread", "depth", "queue_position",
                                        "venue_microstructure",
                                        "live_vs_pregame", "latency")},
            "unmeasured": cur.get("unmeasured")},
        "predicted_vs_realized": None if not outs else {
            "outcome_id": outs[0]["outcome_id"],
            "predicted_execution_loss_pp": outs[0][
                "predicted_execution_loss_pp"],
            "realized_execution_loss_pp": outs[0][
                "realized_execution_loss_pp"]},
        "hypothesis": None if cur is None else (
            "Executing %s as %s preserves %s pp of a %s pp theoretical edge"
            % (cur["decision_id"], cur["execution_style"],
               cur["expected_net_executable_edge_pp"],
               cur["theoretical_edge_pp"])),
        "recent_finding": None if not outs else (
            "Realized execution loss %s pp vs predicted %s pp on %s"
            % (outs[0]["realized_execution_loss_pp"],
               outs[0]["predicted_execution_loss_pp"],
               outs[0]["decision_id"])),
        "economic_score": {"name": "incremental_pnl_vs_naive_usd",
                           "value": pnl["value"], "why": pnl["why"],
                           "basis": DEFINITIONS[
                               "incremental_pnl_vs_naive_usd"]},
        "affordances": "READ_ONLY_NO_SUBMIT_NO_TRADE",
    }


def _fmt(v, why=None):
    return "unmeasured (%s)" % why if v is None else str(v)


async def slack_answer(conn, *, limit: int = 3) -> str:
    """Eddie's answer to a Slack mention, from his estimate records only (no
    language model, nothing the question could instruct)."""
    rows = await estimates(conn, limit=limit)
    met = (await metrics(conn))["metrics"]
    lines = ["Eddie · head of execution · SHADOW ONLY · answered from "
             "records only (no language model); I hold no order, cancel, "
             "venue or capital authority."]
    if rows:
        lines.append("Latest estimates (%d shown):" % len(rows))
        for e in rows:
            lines.append("- %s · %s · %s · net executable edge %s pp · "
                         "EV %s USD" % (
                             e["estimate_id"], e["decision_id"],
                             e["recommendation"],
                             _fmt(e["expected_net_executable_edge_pp"],
                                  (e.get("unmeasured") or {}).get(
                                      "net_executable_edge")),
                             _fmt(e["expected_executable_ev_usd"])))
    else:
        lines.append("No candidate has been estimated yet.")
    ep = met["edge_preservation_pct"]
    pnl = met["incremental_pnl_vs_naive_usd"]
    lines.append("Edge preservation: %s · Incremental vs naive: %s" % (
        _fmt(ep["value"], ep["why"]), _fmt(pnl["value"], pnl["why"])))
    lines.append("Record: https://command.bettortoken.com/eddie")
    return "\n".join(lines)


async def workroom_posts(conn, *, limit: int = 3) -> list:
    """EVIDENCE-LINKED COLLABORATION POSTS for #agent-workroom, from records
    only: a candidate review whose execution step disagrees with Derek
    (with the review, estimate and decision ids and who is asked next), and
    a predicted-vs-realized outcome (outcome + estimate ids). [(key, text)].
    """
    out = []
    for r in await conn.fetch(
            "SELECT s.review_id, v.decision_id, s.evidence_refs, s.response, "
            "       s.experiment_ref FROM pos_candidate_review_steps s JOIN "
            "       pos_candidate_reviews v USING (review_id) WHERE s.seq = 4 "
            "   AND s.disagreement IS NOT NULL AND s.at > now() - interval "
            "       '1 hour' ORDER BY s.at DESC LIMIT $1", limit):
        refs = ", ".join("%s %s" % (e.get("kind"), e.get("id"))
                         for e in (_j(r["evidence_refs"]) or []))
        exp = _j(r["experiment_ref"]) or {}
        out.append(("review:%s" % r["review_id"], (
            "Eddie · execution review %s · Derek candidate %s\n%s\n"
            "Evidence: %s%s\nNext: %s -- answer in the loop with evidence. "
            "Stage: SHADOW recommendation; nothing is blocked, approved or "
            "sent by this message." % (
                r["review_id"], r["decision_id"], str(r["response"])[:900],
                refs, (" · loop finding %s" % exp.get("id")) if exp.get("id")
                else "", ", ".join(exp.get("routed_to") or
                                   ["Derek", "Xavier", "Audrey"])))))
    for o in await conn.fetch(
            "SELECT outcome_id, estimate_id, decision_id, "
            "       predicted_execution_loss_pp, realized_execution_loss_pp "
            "  FROM eddie_execution_outcomes WHERE created_at > now() - "
            "       interval '1 hour' ORDER BY created_at DESC LIMIT $1", limit):
        out.append(("outcome:%s" % o["outcome_id"], (
            "Eddie · predicted vs realized execution loss · %s\nCandidate %s,"
            " estimate %s: predicted %s, realized %s.\nStage: measured "
            "outcome; nothing is changed by this message." % (
                o["outcome_id"], o["decision_id"], o["estimate_id"],
                _fmt(o["predicted_execution_loss_pp"],
                     "the estimate left it unmeasured"),
                _fmt(o["realized_execution_loss_pp"])))))
    return out[:limit]


async def profile(conn) -> dict:
    st = await R.status_of(conn, EDDIE)
    return dict(PA.profile(EDDIE), registered=st is not None, status=st)
