"""PER INDEPENDENT EVENT, AND THE RUN (pure; no I/O).

The replayed decisions are grouped by their INDEPENDENT event --
profitability.validation.event_key: the fixture, else the market, else the
group -- because positions on one event share one outcome and are one
observation. Per event:

  decision delta     historical verdict vs the R30 canonical action
  sizing delta       the legacy sizing actually ordered vs Allie (SHADOW),
                     EQUAL / ROI-only / capital-hour allocations (USD)
  execution delta    planned vs filled quantity, slippage vs plan, Eddie's
                     expected fill probability
  management delta   reviews whose canonical action differs from the one
                     taken; canonical minus actual result
  P&L                realized, and every counterfactual (summed only when
                     every member is measured; else UNAVAILABLE with the
                     member reasons, plus the measured-only partial sum
                     labelled as such). HYPOTHETICAL members (an admissible
                     opportunity that held no position, priced at its
                     decision-time economics) are summed APART from the
                     realized-result members, never into them
  capital            capital-hours, profit per capital-hour
  marked drawdown    the largest peak-to-trough fall of the event's MARKED
                     equity path (fills, sales, mid marks of the held side
                     at every recorded book, the payout at release);
                     UNAVAILABLE when a member's marks were truncated
  opportunity cost   capital-hours x the pre-allocation tape's hurdle per
                     capital-hour (idle cash is not free)
  lost opportunity   the classification of each decision
  attribution        Alpha Attribution V2 (selection / allocation /
                     execution / management / settlement / probability),
                     with its identity

The run adds the portfolio's marked-equity drawdown over overlapping events,
the INVESTMENT-only totals (the only sleeve fit for any production-confidence
use) beside the all-sleeve research totals, the census of every UNAVAILABLE
reason and the anti-hindsight audit. Every output carries
LABEL = REPLAY_NOT_FORWARD_EVIDENCE.
"""
from __future__ import annotations

from ..intel import attribution_v2 as V2
from ..intel import common as IC
from ..profitability import validation as PV
from . import AUTHORITY, DISCLOSURE, LABEL
from . import counterfactuals as CF

VERSION = "R30_REPLAY_EVENTS_V1"
num = IC.num
INVESTMENT = "INVESTMENT"


def _r(v, k=9):
    return None if v is None else round(float(v), k)


# ═════════════════════════════════════════════════════════════════════
# MARKED EQUITY
# ═════════════════════════════════════════════════════════════════════

def _mid(row, side):
    v = IC.book_view(row.get("bids"), row.get("offers"))
    m = v.get("mid")
    if m is None:
        return None
    return (1.0 - m) if str(side).upper() == "SHORT" else m


def equity_path(pos: dict, marks: list, *, released_at, realized,
                marks_truncated: bool = False) -> dict:
    """[(t, marked equity)] of one position: cash so far (buys at cost plus
    fees out, sales net of fees in) + open quantity x the held side's mid
    at the latest recorded book; the realized result at release."""
    ev = sorted([("B", e) for e in pos["entry"]] +
                [("S", e) for e in pos["sells"]], key=lambda x: x[1]["t"])
    if not ev:
        return {"path": [], "points_without_mark": 0,
                "marks_truncated": False}
    times = sorted({e["t"] for _, e in ev} |
                   {m["observed_at"] for m in marks
                    if m.get("observed_at") is not None})
    if released_at is not None:
        times = [t for t in times if t <= released_at]
    path, skipped = [], 0
    i = 0
    cash = q = 0.0
    last_mark = None
    mk = sorted(marks, key=lambda m: m["observed_at"])
    j = 0
    for t in times:
        while i < len(ev) and ev[i][1]["t"] <= t:
            k, e = ev[i]
            qq, px = num(e["qty"]) or 0.0, num(e["price"]) or 0.0
            fee = num(e.get("fee_usd")) or 0.0
            if k == "B":
                cash -= qq * px + fee
                q += qq
            else:
                cash += qq * px - fee
                q = max(0.0, q - qq)
            i += 1
        while j < len(mk) and mk[j]["observed_at"] <= t:
            m = _mid(mk[j], pos["side"])
            if m is not None:
                last_mark = m
            j += 1
        if q > 1e-9 and last_mark is None:
            skipped += 1
            continue
        path.append((t, cash + q * (last_mark or 0.0)))
    if released_at is not None and realized is not None:
        path.append((released_at, float(realized)))
    return {"path": path, "points_without_mark": skipped,
            "marks_truncated": bool(marks_truncated)}


def max_drawdown_levels(path) -> float | None:
    """Largest peak-to-trough fall of an equity LEVEL path starting at 0."""
    if not path:
        return None
    peak, dd = 0.0, 0.0
    for _, x in path:
        peak = max(peak, x)
        dd = max(dd, peak - x)
    return dd


def combine(paths: list) -> list:
    """The sum of step-function equity paths (each 0 before its first
    point, its last value after its end)."""
    times = sorted({t for p in paths for t, _ in p})
    out, idx, cur = [], [0] * len(paths), [0.0] * len(paths)
    for t in times:
        for k, p in enumerate(paths):
            while idx[k] < len(p) and p[idx[k]][0] <= t:
                cur[k] = p[idx[k]][1]
                idx[k] += 1
        out.append((t, sum(cur)))
    return out


# ═════════════════════════════════════════════════════════════════════
# CAPITAL EFFICIENCY EXTRAS (cross-decision)
# ═════════════════════════════════════════════════════════════════════

def capital_at(segments, t) -> float:
    return sum(c for t0, t1, c in segments or [] if t0 <= t < t1)


def _segs(item: dict) -> list:
    """The position's capital segments, an OPEN position's open cost basis
    carried to the horizon (counterfactuals.economics)."""
    eco = item["eval"]["economics"]["actual"]
    return eco.get("segments_to_horizon") or eco.get("segments") or []


def capital_extras(item: dict, items: list, tape_h: dict, horizon) -> dict:
    """The marginal qualified opportunities actually available while this
    position held capital, the entries refused because CASH was held by
    prior allocations (INSUFFICIENT_AVAILABLE_PAPER_CASH, or the hedge
    reserve it would have had to spend -- not the per-order / concentration
    / group rails, which are their own class), and the missed executable EV
    attributable to this position's occupied capital (pro rata by capital
    among the run's positions holding capital at each refusal; a position
    still open at the horizon holds its open cost basis until then)."""
    eco = item["eval"]["economics"]["actual"]
    t0 = eco.get("opened_at")
    t1 = eco.get("released_at") or horizon
    if t0 is None:
        na = {"status": "UNAVAILABLE", "why": "NO_CAPITAL_WAS_HELD"}
        return {"marginal_opportunities_available": na,
                "cash_unavailable_by_prior_allocations": na,
                "missed_executable_ev_from_occupied_capital": na}
    slug = item["rec"]["decision"].get("us_market_slug")
    sleeve = item["rec"]["sleeve"]
    if t1 > (tape_h.get("complete_until") or 0.0) + 1e-6:
        marg = {"status": "UNAVAILABLE", "why": (
            "TAPE_TRUNCATED_BEFORE_THE_RELEASE: the bounded tape is complete "
            "only up to %s" % tape_h.get("complete_until"))}
    else:
        avail = [o for o in tape_h["rows"] if t0 <= o["t"] <= t1
                 and o["slug"] != slug and o["sleeve"] == sleeve]
        unrec = [o for o in tape_h["unrecorded"] if t0 <= o["t"] <= t1
                 and o["slug"] != slug and o["sleeve"] == sleeve]
        marg = {
            "status": "MEASURED" if not unrec else "LOWER_BOUND",
            "n": len(avail), "unrecorded_n": len(unrec),
            "expected_net_usd": _r(sum(o["net"] for o in avail), 6),
            "window": [t0, t1],
            "basis": "QUALIFIED opportunities (admissible under the same hard "
                     "rules, positive recorded executable net after fees, "
                     "same sleeve, other markets) on the tape recorded by the "
                     "horizon, decided while this capital was held%s" % (
                         "; LOWER_BOUND: %d admissible ones have unrecorded "
                         "economics" % len(unrec) if unrec else "")}
    refused = [o for o in items if o is not item
               and o["eval"]["lost_opportunity"].get("classification")
               == "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"
               and t0 <= float(o["rec"]["decision"]["decided_at"]) <= t1]
    segs = _segs(item)
    missed_total = attributed = 0.0
    unpriced = 0
    for o in refused:
        t = float(o["rec"]["decision"]["decided_at"])
        net = o["rec"]["ev"]["net"]
        if net is None or net <= 0:
            unpriced += 1 if net is None else 0
            continue
        mine = capital_at(segs, t)
        book = sum(capital_at(_segs(x), t) for x in items)
        missed_total += net
        if book > 0:
            attributed += net * mine / book
    return {
        "marginal_opportunities_available": marg,
        "cash_unavailable_by_prior_allocations": {
            "status": "MEASURED", "n": len(refused),
            "codes": sorted({o["rec"]["outcome"]["refusal"]["code"]
                             for o in refused}),
            "basis": "the run's ENTER decisions whose paper order the ledger "
                     "refused because cash was held (available cash or the "
                     "hedge reserve), decided while this capital was held"},
        "missed_executable_ev_from_occupied_capital": {
            "status": "MEASURED" if not unpriced else "LOWER_BOUND",
            "total_usd": _r(missed_total, 6),
            "attributed_usd": _r(attributed, 6),
            "unpriced_refusals": unpriced,
            "basis": "decision-time executable net of each cash-refused "
                     "entry x this position's capital / the run's capital "
                     "held at that instant (open positions to the horizon; "
                     "positions opened before the run are outside the "
                     "denominator)%s" % (
                         "; LOWER_BOUND: %d refusals without a recorded net"
                         % unpriced if unpriced else "")}}


# ═════════════════════════════════════════════════════════════════════
# THE V2 ROW OF ONE ENTER DECISION
# ═════════════════════════════════════════════════════════════════════

def v2_row(item: dict, extras: dict) -> dict:
    rec, ev_ = item["rec"], item["eval"]
    pos = ev_["position"]
    hurdle = rec["hurdle"]
    eco = ev_["economics"]["actual"]
    if not pos["entry"]:
        ch, ch_why = 0.0, None
    elif eco.get("state") == "CLOSED":
        ch, ch_why = ev_["capital_hours"], None
    else:
        ch, ch_why = None, ("OPEN_AT_THE_HORIZON: the position still holds "
                            "capital (%s capital-hours so far)" %
                            eco.get("capital_hours_to_horizon"))
    return V2.attribute_v2(
        subject_id=pos["group_id"] or rec["decision"]["decision_id"],
        book="PAPER", sleeve=rec["sleeve"], p=rec["ev"]["p"],
        p_basis=rec["ev"]["p_basis"], d=rec["ev"]["d"],
        d_basis=rec["ev"]["d_basis"], entry_fills=pos["entry"],
        sell_fills=pos["sells"], hedge_legs=pos["hedges"],
        payoff=pos["payoff"], settlement_outcome=pos["outcome"],
        payoff_basis=pos["payoff_basis"], plan_qty=pos["plan_qty"],
        plan_qty_basis=pos["plan_basis"],
        entry_terminal=pos["entry_terminal"],
        alternatives=rec["alternatives_input"],
        allocations=rec["allocations"], rails=rec["rails"],
        allocation_unit=V2.CAPITAL,
        capital_per_contract=rec["ev"].get("capital_per_contract"),
        fill_state_hint=("NO_ORDER_RECORDED" if pos.get("no_order")
                         else None),
        capital=dict(extras, position_capital_hours=ch,
                     capital_hours_why=ch_why,
                     hurdle_ppch=hurdle.get("value"),
                     hurdle_why=hurdle.get("why"),
                     hurdle_basis=hurdle.get("basis")),
        actions=ev_["management"]["reviews"],
        extra={"decision_id": rec["decision"]["decision_id"],
               "event_key": item["event_key"]})


# ═════════════════════════════════════════════════════════════════════
# AGGREGATION
# ═════════════════════════════════════════════════════════════════════

def _sum_block(cfs: list) -> dict:
    vals = [c.get("pnl_usd") for c in cfs]
    missing: dict = {}
    for c in cfs:
        if c.get("pnl_usd") is None:
            w = str(c.get("why") or "NOT_MEASURED").split(":")[0]
            missing[w] = missing.get(w, 0) + 1
    meas = [v for v in vals if v is not None]
    chs = [c.get("capital_hours") for c in cfs]
    ch = None if any(x is None for x in chs) else sum(chs)
    total = None if missing else sum(meas)
    return {"pnl_usd": _r(total), "capital_hours": _r(ch),
            "profit_per_capital_hour": (None if total is None or not ch
                                        else _r(total / ch, 12)),
            "n": len(cfs), "measured_n": len(meas),
            "why": None if not missing else "UNAVAILABLE_MEMBERS",
            "unavailable": missing,
            "measured_only_partial_sum_usd": _r(sum(meas)) if missing
            else None}


def sum_cf(cfs: list) -> dict:
    """The realized-result members summed; the HYPOTHETICAL members (label
    HYPOTHETICAL) summed APART under `hypothetical` -- a decision-time
    hypothetical is never added to realized P&L."""
    real = [c for c in cfs if c.get("label") != CF.HYPO]
    hyp = [c for c in cfs if c.get("label") == CF.HYPO]
    out = _sum_block(real)
    out["members"] = len(cfs)
    out["hypothetical"] = (dict(_sum_block(hyp), label=CF.HYPO,
                                note="decision-time hypotheticals of "
                                     "opportunities that held no position; "
                                     "never added to pnl_usd")
                           if hyp else None)
    return out


def event_block(key: str, items: list, rows: list) -> dict:
    decs = [it["rec"]["decision"] for it in items]
    sleeves = sorted({it["rec"]["sleeve"] for it in items})
    cfs = {n: sum_cf([it["eval"]["counterfactuals"][n] for it in items])
           for n in CF.COUNTERFACTUALS}
    ent = [it for it in items if it["rec"]["decision"].get("verdict")
           == "ENTER"]
    canon_enter = sum(1 for it in items if it["eval"]["counterfactuals"][
        "R30_CANONICAL_ACTION"].get("action") == "ENTER_WITH_CANONICAL_INTENT")
    differ = [it["rec"]["decision"]["decision_id"] for it in ent
              if it["eval"]["counterfactuals"]["R30_CANONICAL_ACTION"].get(
                  "action") != "ENTER_WITH_CANONICAL_INTENT"]

    def ksum(name):
        xs = [num((r.get("benchmark_allocations") or {}).get(name, {}).get(
            "usd")) for r in rows]
        return None if not xs or any(x is None for x in xs) else _r(sum(xs),
                                                                    6)
    plan = [it["eval"]["position"]["plan_qty"] for it in ent]
    filled = [it["eval"]["position"]["q"] for it in ent]
    slip = [it["eval"]["v1"].get("slippage_usd") for it in ent
            if it["eval"]["position"]["entry"]]
    fps = [num(it["rec"]["components"]["eddie"].get(
        "expected_fill_probability")) for it in ent]
    fps = [x for x in fps if x is not None]
    reviews = sum(it["eval"]["management"]["reviews"] for it in items)
    divergent = sum(it["eval"]["management"]["divergent"] for it in items)
    can = cfs["CANONICAL_XAVIER_MANAGEMENT"]["pnl_usd"]
    act = cfs["ACTUAL_XAVIER_MANAGEMENT"]["pnl_usd"]
    paths = [it["equity"]["path"] for it in items if it["equity"]["path"]]
    marks_cut = [it["rec"]["decision"]["decision_id"] for it in items
                 if it["equity"].get("marks_truncated")]
    dd = (max_drawdown_levels(combine(paths)) if paths and not marks_cut
          else None)
    charges = [num((r.get("capital_efficiency") or {}).get(
        "capital_charge_usd")) for r in rows]
    lo: dict = {}
    for it in items:
        c = it["eval"]["lost_opportunity"].get("classification")
        lo[c] = lo.get(c, 0) + 1
    ev_max = [it["rec"]["evidence"]["decision"]["max_recorded_at"]
              for it in items
              if it["rec"]["evidence"]["decision"]["max_recorded_at"]
              is not None]
    out_max = [it["rec"]["evidence"]["outcome"]["max_recorded_at"]
               for it in items
               if it["rec"]["evidence"]["outcome"]["max_recorded_at"]
               is not None]
    return {
        "event_key": key, "label": LABEL, "sleeves": sleeves,
        "investment_only": sleeves == [INVESTMENT],
        "decisions": len(items),
        "decision_ids": [d["decision_id"] for d in decs][:50],
        "verdicts": {v: sum(1 for d in decs if d.get("verdict") == v)
                     for v in sorted({d.get("verdict") for d in decs})},
        "clock_range": [min(it["rec"]["clock"] for it in items),
                        max(it["rec"]["clock"] for it in items)],
        "deltas": {
            "decision": {"historical_enter": len(ent),
                         "canonical_enter": canon_enter,
                         "enter_without_canonical_intent": differ[:20],
                         "differ_n": len(differ)},
            "sizing": {"legacy_usd": ksum("LEGACY_SIZING"),
                       "allie_usd": ksum("ALLIE"),
                       "equal_usd": ksum("EQUAL_ALLOCATION"),
                       "roi_only_usd": ksum("ROI_ONLY_RANKING"),
                       "capital_hour_usd": ksum("CAPITAL_HOUR_RANKING"),
                       "unit": "plan-cost USD (contracts x d, fees "
                               "excluded -- the V2 identity's unit); "
                               "benchmarks rail-clamped in capital units "
                               "first; legacy as ordered"},
            "execution": {
                "planned_qty": (None if any(p is None for p in plan)
                                else _r(sum(plan), 6)),
                "filled_qty": _r(sum(filled), 6),
                "fill_ratio": (None if any(p is None for p in plan)
                               or not sum(plan) else
                               _r(sum(filled) / sum(plan), 6)),
                "slippage_usd": (None if any(s is None for s in slip)
                                 or not slip else _r(sum(slip), 6)),
                "eddie_expected_fill_probability_mean": (
                    _r(sum(fps) / len(fps), 6) if fps else None)},
            "management": {"reviews": reviews, "divergent": divergent,
                           "canonical_minus_actual_usd": (
                               None if can is None or act is None
                               else _r(can - act))}},
        "pnl": cfs,
        "realized_pnl_usd": cfs["CURRENT_ACTION"]["pnl_usd"],
        "capital_hours": cfs["CURRENT_ACTION"]["capital_hours"],
        "profit_per_capital_hour": cfs["CURRENT_ACTION"][
            "profit_per_capital_hour"],
        "marked_drawdown_usd": _r(dd),
        "marked_drawdown_why": ("MARKS_TRUNCATED: %s" % marks_cut[:5]
                                if marks_cut else None),
        "marked_points_without_mark": sum(
            it["equity"]["points_without_mark"] for it in items),
        "opportunity_cost_usd": (None if not rows or any(
            c is None for c in charges) else _r(sum(charges))),
        "lost_opportunity": dict(sorted(lo.items())),
        "attribution": V2.summarize(rows),
        "evidence": {
            "decision_step_max_recorded_at": max(ev_max) if ev_max else None,
            "outcome_max_recorded_at": max(out_max) if out_max else None,
            "every_decision_input_recorded_by_its_clock": all(
                it["rec"]["evidence"]["decision"]["max_recorded_at"] is None
                or it["rec"]["evidence"]["decision"]["max_recorded_at"]
                <= it["rec"]["clock"] + 1e-6 for it in items)},
    }


def build(items: list, *, horizon: float, tape_h: dict, notes: dict,
          params: dict) -> dict:
    """items: [{rec, eval, equity}] -> {events, decisions, summary}."""
    for it in items:
        d = it["rec"]["decision"]
        g = it["eval"]["position"]["group_id"]
        it["event_key"] = PV.event_key({
            "fixture": d.get("fixture"), "us_market_slug":
            d.get("us_market_slug"), "group_id": g or d["decision_id"]})
    rows_by: dict = {}
    for it in items:
        if it["rec"]["decision"].get("verdict") != "ENTER":
            it["v2"] = None
            continue
        it["v2"] = v2_row(it, capital_extras(it, items, tape_h, horizon))
        rows_by.setdefault(it["event_key"], []).append(it["v2"])
    groups: dict = {}
    for it in items:
        groups.setdefault(it["event_key"], []).append(it)
    events = [event_block(k, g, rows_by.get(k, []))
              for k, g in sorted(groups.items())]

    def totals(sel):
        return {n: sum_cf([it["eval"]["counterfactuals"][n] for it in sel])
                for n in CF.COUNTERFACTUALS}
    inv = [it for it in items if it["rec"]["sleeve"] == INVESTMENT]
    inv_paths = [it["equity"]["path"] for it in inv if it["equity"]["path"]]
    all_paths = [it["equity"]["path"] for it in items if it["equity"]["path"]]
    inv_cut = any(it["equity"].get("marks_truncated") for it in inv)
    all_cut = any(it["equity"].get("marks_truncated") for it in items)
    census: dict = {}
    for it in items:
        for name, comp in it["rec"]["components"].items():
            if not isinstance(comp, dict):
                continue
            st = comp.get("status") or comp.get("state")
            if st == "UNAVAILABLE":
                w = str(comp.get("why") or "UNKNOWN").split(":")[0]
                census.setdefault(name, {})
                census[name][w] = census[name].get(w, 0) + 1
    leads = [it["rec"]["evidence"]["decision"]["max_recorded_at"]
             - it["rec"]["clock"] for it in items
             if it["rec"]["evidence"]["decision"]["max_recorded_at"]
             is not None]
    rows = [it["v2"] for it in items if it["v2"] is not None]
    summary = {
        "version": VERSION, "label": LABEL, "authority": AUTHORITY,
        "disclosure": DISCLOSURE, "decisions": len(items),
        "independent_events": len(events),
        "investment": {
            "decisions": len(inv),
            "events": sum(1 for e in events if e["investment_only"]),
            "counterfactuals": totals(inv),
            "portfolio_marked_drawdown_usd": _r(max_drawdown_levels(
                combine(inv_paths))) if inv_paths and not inv_cut else None,
            "use": "the ONLY block fit for any production-confidence use, "
                   "and even then REPLAY_NOT_FORWARD_EVIDENCE"},
        "research_all_sleeves": {
            "decisions": len(items), "counterfactuals": totals(items),
            "portfolio_marked_drawdown_usd": _r(max_drawdown_levels(
                combine(all_paths))) if all_paths and not all_cut else None,
            "use": "research only; TRAINING / BENCHMARK / UNCLASSIFIED are "
                   "never pooled into the INVESTMENT block"},
        "attribution": V2.summarize(rows),
        "lost_opportunity": _count(it["eval"]["lost_opportunity"].get(
            "classification") for it in items),
        "unavailable_census": census,
        "anti_hindsight": {
            "rule": "every row used at a decision clock was recorded at or "
                    "before it (pit.Reader); outcomes at or before the "
                    "horizon",
            "decisions_checked": len(leads),
            "violations": sum(1 for x in leads if x > 1e-6),
            "max_recorded_minus_clock_s": _r(max(leads), 6) if leads
            else None},
        "notes": dict(notes), "parameters": dict(params),
        "promotion": "NONE: no replay result can promote production policy",
    }
    return {"events": events, "items": items, "summary": summary}


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))
