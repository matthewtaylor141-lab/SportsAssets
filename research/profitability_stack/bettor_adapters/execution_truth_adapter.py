"""EXECUTION TRUTH on BETTOR's records -> MAKE / TAKE / WAIT / REFUSE.

Two read-only inputs:
  * the paper order record (research/ps_orders_v1.sql): every paper order with
    the book at submission, fills, fees, cancel/terminal times and the first
    book observed at fill + {0.1, 0.5, 1, 5, 60, 300} s;
  * the decision dataset (research/apl_extract_v1.sql, Alpha Proof Lab run
    37625484193): every settled-label paper decision with its recorded
    probability, venue book, closing book and next-15-minute book path.

Part A measures execution costs per venue x sport x family x price-band x
regime from the paper orders. Part B splits decisions CHRONOLOGICALLY BY EVENT
(first half of events = calibration, second half = evaluation; no event is in
both), estimates every cost on the calibration half only, and on the
evaluation half asks the package's choose_execution for the best path by
conservative lower bound. A path whose cost is UNMEASURED is not admissible;
a positive lower bound with an unmeasured component is still REFUSE.

What the records cannot show is said so: book observations arrive about once a
minute, so adverse selection at 100 ms / 500 ms / 1 s / 5 s is UNMEASURED; the
cancel / submit latencies are the PAPER SIMULATOR's, not a venue's.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import statistics

import numpy as np

from common import (VENUE_PMUS, Z, cluster_mean_ci, event_identity, family_of, fee_per_contract,
                    iso, mid, orient, price_band, regime_of, sport_of, window)
from bettor_profit_stack.execution_truth import (ExecutionCosts, adverse_selection_after_fill,
                                                 choose_execution, edge_decay_slope,
                                                 estimate_fill_probability, maker_value,
                                                 taker_value, wait_value)

LAGS = (0.1, 0.5, 1, 5, 60, 300)
MIN_SEGMENT_EVENTS = 20       # below this a segment falls back to the pooled estimate


def lag_measured(lag: float, actual) -> bool:
    """A mark stands for lag L only if it was observed within max(1.5 L, L + 1 s)."""
    return actual is not None and float(actual) <= max(1.5 * lag, lag + 1.0)


def seg_key(sport, family, band, regime) -> str:
    return "|".join((VENUE_PMUS, sport, family, band, regime))


# ── Part A: measured costs from the paper order record ─────────────────────
def _touch(r) -> float | None:
    """The side-held price the order would pay / receive at submission."""
    b = orient(r.get("sub_bid"), r.get("sub_ask"), r.get("side"))
    if b is None:
        return None
    return b[1] if r.get("direction") == "BUY" else b[0]


def order_segments(orders: list[dict]) -> dict:
    seg = defaultdict(lambda: defaultdict(list))
    for r in orders:
        ev, _ = event_identity(r)
        px = r.get("limit")
        k = seg_key(sport_of(r), family_of(r), price_band(px),
                    regime_of(r.get("decided") or r.get("created"), r.get("start")))
        s = seg[k]
        s["events"].append(ev)
        filled = (r.get("fill_qty") or 0) > 0
        marketable = r.get("type") == "MARKETABLE"
        role = r.get("role")
        if role == "ENTRY" and marketable:
            s["taker_fill"].append(1.0 if filled else 0.0)
            t = _touch(r)
            if filled and t is not None and r.get("vwap") is not None:
                s["taker_slippage"].append((float(r["vwap"]) - t, ev))
        if role == "STANDING_PROTECTION" or (not marketable and role in ("ENTRY", "EXIT", "REDUCE")):
            if r.get("state") != "RESTING":
                s["maker_fill"].append(1.0 if filled else 0.0)
            if r.get("queue_ahead") is not None:
                s["queue_ahead"].append(float(r["queue_ahead"]))
        if filled:
            q = float(r["fill_qty"])
            s["fee_actual"].append(float(r.get("fees") or 0.0) / q)
            fee, _ = fee_per_contract(float(r["vwap"]), "TAKER" if marketable else "MAKER",
                                      iso(r.get("first_fill")))
            if fee is not None:
                s["fee_resid"].append(float(r.get("fees") or 0.0) / q - fee)
            s["fill_basis"].append(r.get("fill_basis") or "UNKNOWN")
            for m in r.get("marks") or []:
                b = orient(m.get("bid"), m.get("ask"), r.get("side"))
                lag = float(m["lag"])
                if b is None:
                    continue
                if lag_measured(lag, m.get("actual_lag")):
                    s["as_%g" % lag].append(adverse_selection_after_fill(
                        float(r["vwap"]), mid(b), r.get("direction") or "BUY"))
                else:
                    s["as_gap_%g" % lag].append(float(m["actual_lag"]))
            if role == "ENTRY" and r.get("p_pin") is not None:
                p = float(r["p_pin"])
                t0 = _touch(r)
                if t0 is not None:
                    ts, ev_ = [0.0], [p - t0]
                    for m in r.get("marks") or []:
                        b = orient(m.get("bid"), m.get("ask"), r.get("side"))
                        if b is not None and float(m["lag"]) >= 60 and lag_measured(float(m["lag"]), m.get("actual_lag")):
                            ts.append(float(m["actual_lag"])); ev_.append(p - b[1])
                    if len(ts) > 1:
                        s["decay"].append(edge_decay_slope(ts, ev_))
        if r.get("cancel_req") is not None and r.get("terminal_ev") is not None:
            s["cancel_latency"].append(float(r["terminal_ev"]) - float(r["cancel_req"]))
        if r.get("decided") is not None and r.get("eligible") is not None:
            s["submit_latency"].append(float(r["eligible"]) - float(r["decided"]))
    return seg


def _summ(xs):
    if not xs:
        return None
    a = np.asarray(xs, dtype=float)
    return {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
            "p90": float(np.percentile(a, 90))}


def segment_table(seg) -> dict:
    out = {}
    for k, s in sorted(seg.items()):
        row = {"orders_events": len({e for e in s["events"] if e}), "order_rows": len(s["events"])}
        if s["taker_fill"]:
            p, se = estimate_fill_probability(s["taker_fill"])
            row["taker_fill_probability"] = {"n": len(s["taker_fill"]), "mean": p, "se": se}
        if s["maker_fill"]:
            p, se = estimate_fill_probability(s["maker_fill"])
            row["maker_fill_probability"] = {"n": len(s["maker_fill"]), "mean": p, "se": se}
        if s["taker_slippage"]:
            row["taker_slippage_per_contract"] = cluster_mean_ci(
                [x for x, _ in s["taker_slippage"]], [e for _, e in s["taker_slippage"]])
        row["fee_actual_per_contract"] = _summ(s["fee_actual"])
        row["fee_minus_schedule_per_contract"] = _summ(s["fee_resid"])
        row["queue_ahead_qty"] = _summ(s["queue_ahead"])
        row["fill_basis"] = dict(Counter(s["fill_basis"]))
        row["adverse_selection_after_fill"] = {}
        for lag in LAGS:
            got = s.get("as_%g" % lag) or []
            gap = s.get("as_gap_%g" % lag) or []
            if got:
                row["adverse_selection_after_fill"]["%gs" % lag] = _summ(got)
            else:
                row["adverse_selection_after_fill"]["%gs" % lag] = {
                    "status": "UNMEASURED",
                    "reason": "NO_BOOK_OBSERVATION_WITHIN_TOLERANCE_OF_LAG",
                    "median_actual_lag_s": statistics.median(gap) if gap else None}
        row["edge_decay_per_second"] = _summ(s["decay"])
        row["cancel_latency_s"] = (dict(_summ(s["cancel_latency"]), basis="PAPER_SIMULATOR_NOT_VENUE")
                                   if s["cancel_latency"] else None)
        row["submit_latency_s"] = (dict(_summ(s["submit_latency"]), basis="PAPER_SIMULATOR_NOT_VENUE")
                                   if s["submit_latency"] else None)
        out[k] = row
    return out


# ── Part B: per-decision MAKE / TAKE / WAIT / REFUSE ───────────────────────
def frame(rows: list[dict]) -> list[dict]:
    """Decision rows oriented to the side held; rows without a usable book,
    probability or label are dropped and counted."""
    out = []
    for r in rows:
        b = orient(r.get("bid"), r.get("ask"), r.get("side"))
        p, yl = r.get("p_pin"), r.get("y_long")
        if b is None or p is None or yl is None:
            continue
        short = r.get("side") == "SHORT"
        cb = orient(r.get("close_bid"), r.get("close_ask"), r.get("side"))
        mn, mx, n = r.get("n15_min_ask"), r.get("n15_max_bid"), r.get("n15_obs") or 0
        if not n:
            mfill = None
        elif short:      # a resting SHORT buy at 1-ask0 fills when YES bid trades THROUGH ask0
            mfill = mx is not None and float(mx) > float(r["ask"])
        else:            # a resting LONG buy at bid0 fills when YES ask trades THROUGH bid0
            mfill = mn is not None and float(mn) < float(r["bid"])
        ev, src = event_identity(r)
        out.append({"id": r["id"], "at": float(r["at"]), "strategy": r.get("strategy"),
                    "event": ev, "event_src": src, "p": float(p),
                    "y": (1.0 - float(yl)) if short else float(yl),
                    "bid": b[0], "ask": b[1], "close_ask": cb[1] if cb else None,
                    "maker_filled": mfill,
                    "key": seg_key(sport_of(r), family_of(r), price_band(b[1]),
                                   regime_of(r.get("at"), r.get("start")))})
    return out


def split_by_event(fr: list[dict], share: float = 0.5):
    first = {}
    for d in fr:
        first[d["event"]] = min(first.get(d["event"], d["at"]), d["at"])
    order = sorted(first, key=lambda e: first[e])
    cut = int(len(order) * share)
    cal_ev = set(order[:cut])
    return [d for d in fr if d["event"] in cal_ev], [d for d in fr if d["event"] not in cal_ev]


def calibrate(cal: list[dict], taker_slip: dict) -> dict:
    """Every cost estimate, per segment with a pooled fallback, from the
    calibration half only."""
    by = defaultdict(list)
    for d in cal:
        by[d["key"]].append(d)
    by["__POOLED__"] = list(cal)

    def est(ds):
        evs = {d["event"] for d in ds}
        bias = cluster_mean_ci([d["y"] - d["p"] for d in ds], [d["event"] for d in ds])
        mk = [d for d in ds if d["maker_filled"] is not None]
        pf, pf_se = estimate_fill_probability([1.0 if d["maker_filled"] else 0.0 for d in mk])
        filled = [d for d in mk if d["maker_filled"]]
        # adverse selection of a filled maker, in probability units: how much
        # worse the outcome runs, relative to p, given that the post was filled
        if len({d["event"] for d in filled}) >= 2 and bias["mean"] is not None:
            c = cluster_mean_ci([d["y"] - d["p"] for d in filled], [d["event"] for d in filled])
            maker_as = {"mean": bias["mean"] - c["mean"], "se": c["se"], "events": c["events"]}
        else:
            maker_as = None
        drift_rows = [d for d in ds if d["close_ask"] is not None]
        drift = (cluster_mean_ci([d["close_ask"] - d["ask"] for d in drift_rows],
                                 [d["event"] for d in drift_rows]) if drift_rows else None)
        return {"events": len(evs), "rows": len(ds), "bias": bias,
                "maker_fill": {"mean": pf, "se": pf_se, "n": len(mk)},
                "maker_adverse_selection": maker_as, "close_drift": drift}

    pooled = est(by["__POOLED__"])
    out = {"__POOLED__": pooled}
    for k, ds in by.items():
        if k == "__POOLED__":
            continue
        e = est(ds)
        out[k] = e if e["events"] >= MIN_SEGMENT_EVENTS else dict(pooled, fallback_from=k,
                                                                  segment_events=e["events"])
    out["__TAKER_SLIPPAGE__"] = taker_slip
    return out


def decide(d: dict, cal: dict) -> dict:
    c = cal.get(d["key"]) or dict(cal["__POOLED__"], fallback_from=d["key"], segment_events=0)
    unmeasured = []
    bias = c["bias"]
    if bias.get("se") is None:
        unmeasured.append("CALIBRATION_BIAS_UNMEASURED")
        p_lb = None
    else:
        # conservative probability: recorded p corrected by the calibration
        # half's bias, less z standard errors
        p_lb = d["p"] + bias["mean"] - Z * bias["se"]
    when = iso(d["at"])
    choices, notes = [], {}

    slip = cal["__TAKER_SLIPPAGE__"]
    fee_t, fee_basis = fee_per_contract(d["ask"], "TAKER", when)
    if fee_t is None:
        unmeasured.append("TAKER_FEE_UNMEASURED:%s" % fee_basis)
    elif p_lb is not None and slip and slip.get("se") is not None:
        slip_ub = max(0.0, slip["mean"] + Z * slip["se"])
        choices.append(taker_value(p_lb, d["ask"], ExecutionCosts(fees=fee_t, slippage=slip_ub)))
    else:
        unmeasured.append("TAKER_SLIPPAGE_UNMEASURED")

    fee_m, fee_mb = fee_per_contract(d["bid"], "MAKER", when)
    mas = c.get("maker_adverse_selection")
    if fee_m is None:
        unmeasured.append("MAKER_FEE_UNMEASURED:%s" % fee_mb)
    elif mas is None or mas.get("se") is None:
        unmeasured.append("MAKER_ADVERSE_SELECTION_UNMEASURED")
    elif p_lb is not None:
        choices.append(maker_value(
            p_lb, d["bid"], c["maker_fill"]["mean"],
            ExecutionCosts(fees=max(0.0, fee_m), rebate=max(0.0, -fee_m),
                           adverse_selection=max(0.0, mas["mean"])),
            fill_probability_se=c["maker_fill"]["se"], edge_se=mas["se"], z=Z))

    drift = c.get("close_drift")
    if drift is None or drift.get("se") is None:
        unmeasured.append("WAIT_PRICE_PATH_UNMEASURED")
    elif p_lb is not None:
        fut = min(0.999, max(0.001, d["ask"] + drift["mean"] + Z * drift["se"]))
        fee_w, _ = fee_per_contract(fut, "TAKER", when)
        if fee_w is None:
            unmeasured.append("WAIT_FEE_UNMEASURED")
        else:
            choices.append(wait_value(p_lb, fut, 0.0, fee_w))
    notes["components_unmeasured"] = unmeasured
    if not choices:
        return {"action": "REFUSE", "reason": "NO_PATH_HAS_ALL_COSTS_MEASURED",
                "lower_bound": None, **notes}
    sel = choose_execution(*choices)
    out = {"action": sel.action, "reason": sel.reason, "lower_bound": sel.lower_bound_profit,
           "candidates": {x.action: x.lower_bound_profit for x in choices}, **notes}
    # a positive bound that leans on an unmeasured component is not proof
    if sel.action != "REFUSE" and any(u.startswith("CALIBRATION") for u in unmeasured):
        out.update(action="REFUSE", reason="POSITIVE_BOUND_BUT_CALIBRATION_UNMEASURED")
    return out


def run(orders: list[dict], decisions: list[dict]) -> dict:
    seg = order_segments(orders)
    table = segment_table(seg)
    entry_slip = [(float(r["vwap"]) - _touch(r), event_identity(r)[0]) for r in orders
                  if r.get("role") == "ENTRY" and r.get("type") == "MARKETABLE"
                  and (r.get("fill_qty") or 0) > 0 and _touch(r) is not None]
    slip = cluster_mean_ci([x for x, _ in entry_slip], [e for _, e in entry_slip])

    fr = frame(decisions)
    cal_rows, ev_rows = split_by_event(fr)
    cal = calibrate(cal_rows, slip)
    actions, reasons = Counter(), Counter()
    by_strategy = defaultdict(Counter)
    realized = defaultdict(lambda: ([], []))
    samples = []
    for d in ev_rows:
        r = decide(d, cal)
        actions[r["action"]] += 1
        reasons["%s:%s" % (r["action"], r["reason"])] += 1
        by_strategy[d["strategy"]][r["action"]] += 1
        if r["action"] != "REFUSE":
            # what the chosen path would actually have earned per posted
            # contract: TAKE at the ask; WAIT at the later (closing) ask; MAKE
            # only when the post was traded through, else nothing
            if r["action"] == "TAKE":
                got = d["y"] - d["ask"]
            elif r["action"] == "WAIT":
                got = None if d["close_ask"] is None else d["y"] - d["close_ask"]
            else:
                got = None if d["maker_filled"] is None else (
                    (d["y"] - d["bid"]) if d["maker_filled"] else 0.0)
            if got is not None:
                for key in (r["action"], "%s|%s" % (d["strategy"], r["action"])):
                    realized[key][0].append(got)
                    realized[key][1].append(d["event"])
        if len(samples) < 25:
            samples.append({"decision_id": d["id"], "event": d["event"], "segment": d["key"], **r})
    unique = lambda ds: len({d["event"] for d in ds})
    return {
        "module": "EXECUTION_TRUTH",
        "decision_rows": {"input": len(decisions), "usable": len(fr),
                          "calibration_half": len(cal_rows), "evaluation_half": len(ev_rows)},
        "unique_independent_events": {"usable": unique(fr), "calibration_half": unique(cal_rows),
                                      "evaluation_half": unique(ev_rows)},
        "event_identity_sources": dict(Counter(d["event_src"] for d in fr)),
        "window": {"decisions": window(d["at"] for d in fr),
                   "orders": window(r.get("created") for r in orders)},
        "paper_orders": {"rows": len(orders),
                         "unique_events": len({event_identity(r)[0] for r in orders} - {None})},
        "segment_costs_from_paper_orders": table,
        "pooled_taker_slippage_per_contract": slip,
        "calibration_estimates": {k: v for k, v in cal.items() if k in ("__POOLED__",)},
        "evaluation_actions": dict(actions),
        "evaluation_action_reasons": dict(reasons),
        "evaluation_actions_by_strategy": {k: dict(v) for k, v in by_strategy.items()},
        "realized_check_of_non_refused_paths": {
            k: dict(cluster_mean_ci(v[0], v[1]),
                    note="per posted contract, realized outcome minus price paid, before fees")
            for k, v in sorted(realized.items())},
        "sample_decisions": samples,
        "unmeasured": {
            "adverse_selection_100ms_500ms_1s_5s": "BOOK_OBSERVATIONS_ARRIVE_ABOUT_ONCE_PER_MINUTE",
            "cancel_replace_latency": "ONLY_PAPER_SIMULATOR_LATENCY_IS_RECORDED_NOT_VENUE",
            "venue_queue_position": "PAPER_QUEUE_AHEAD_IS_A_SIMULATOR_ESTIMATE",
            "management_cost": "NOT_AN_ENTRY_EXECUTION_COMPONENT; SEE PNL_ATTRIBUTION",
        },
    }
