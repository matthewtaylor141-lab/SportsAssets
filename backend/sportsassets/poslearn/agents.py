"""4 · THE AGENT TOURNAMENT (SHADOW): challenger policies for each agent,
evaluated prospectively on the SAME opportunities, with no hindsight
selection. Pure; no I/O.

  DEREK      entry.     DEREK_V1 = the approved rule (the champion's
                        threshold, net edge > 0) on the raw PinnAPI
                        probability. CHALLENGER_A = the threshold raised by
                        THRESHOLD_STEP_PP. CHALLENGER_B = V1 and the
                        opportunity's AVOIDANCE level is not AVOID (an
                        UNMEASURED avoidance applies V1, declared).
  XAVIER     management of the SAME entry set (every opportunity DEREK_V1
                        would enter). XAVIER_V1 = hold to settlement.
                        CHALLENGER_A = take profit when our side's exit price
                        reaches entry cost + TAKE_PROFIT. CHALLENGER_B = exit
                        when a later PinnAPI quote of the contract falls below
                        the entry price (edge lost), at the book as of that
                        quote. The plan is recorded before the outcome; the
                        path is only replayed afterwards.
  ALLOCATOR  sizing of each cycle's DEREK_V1 entries inside a $1,000 SHADOW
                        sleeve. ALLOCATOR_V1 = intel/sizing.shadow_size
                        (reused: quarter Kelly x evidence factors).
                        CHALLENGER_A = equal weight. CHALLENGER_B = the
                        documented future formula EV x EDGE_CONFIDENCE x
                        LIQUIDITY x CALIBRATION x RISK_BUDGET -- in SHADOW
                        only; EDGE_CONFIDENCE unavailable -> $0 (declared).
  EDDIE      execution. Being built in parallel (claude/pos-agents). The
                        interface is a READ of `eddie_execution_estimates`
                        (to_regclass); absent -> the three EDDIE variants are
                        registered AWAITING_INTERFACE and skipped cleanly.
                        No Python import of an execution module, ever.

Metrics per variant: net economics, max drawdown, capital-hours, turnover,
fees, execution loss (half-spread paid against the venue mid as of t),
false refusals (champion-rule entries the variant refused that realized a
positive net edge), and risk (largest position, sport concentration).
Promotion: the same gated path -- a predeclared fixed-sample paired test
against the agent's V1, Karen challenges, Audrey evaluates, a human
promotes. No variant promotes itself.
"""
from __future__ import annotations

import math

from ..intel import common as IC
from ..intel import sizing as SZ
from . import common as C
from . import models as M

VERSION = "POSLEARN_AGENTS_V1"
FAMILY = "AGENT_TOURNAMENT_V1"
AGENTS = ("DEREK", "XAVIER", "ALLOCATOR", "EDDIE")
VARIANTS = ("V1", "CHALLENGER_A", "CHALLENGER_B")
FAMILY_SIZE = len(AGENTS) * (len(VARIANTS) - 1)
THRESHOLD_STEP_PP = 0.5
TAKE_PROFIT = 0.10
SLEEVE_USD = IC.SLEEVE_NOTIONAL_USD
PER_POSITION_CAP = 0.10
MIN_SAMPLE = 200
ALPHA = 0.05
DRAWDOWN_TOLERANCE = 1.25
EDDIE_TABLE = "eddie_execution_estimates"

DESCRIPTIONS = {
    "DEREK_V1": "approved entry rule on the raw PinnAPI probability",
    "DEREK_CHALLENGER_A": "approved rule with the threshold raised by "
                          "%.1f pp" % THRESHOLD_STEP_PP,
    "DEREK_CHALLENGER_B": "approved rule, refused where AVOIDANCE says AVOID",
    "XAVIER_V1": "hold every entry to settlement",
    "XAVIER_CHALLENGER_A": "take profit at entry cost + %.2f" % TAKE_PROFIT,
    "XAVIER_CHALLENGER_B": "exit when a later PinnAPI quote falls below the "
                           "entry price",
    "ALLOCATOR_V1": "intel/sizing.shadow_size (quarter Kelly x evidence)",
    "ALLOCATOR_CHALLENGER_A": "equal weight inside the sleeve",
    "ALLOCATOR_CHALLENGER_B": "EV x EDGE_CONFIDENCE x LIQUIDITY x "
                              "CALIBRATION x RISK_BUDGET (shadow only)",
    "EDDIE_V1": "cross at the displayed ask (taker)",
    "EDDIE_CHALLENGER_A": "rest at the mid when EDDIE's fill probability "
                          ">= 0.5, else cross",
    "EDDIE_CHALLENGER_B": "cross only when the spread is <= 2c",
}


def subjects():
    return ["%s_%s" % (a, v) for a in AGENTS for v in VARIANTS]


def agent_of(subject: str) -> str:
    return subject.split("_", 1)[0]


def document(subject, *, version, threshold_pp, threshold_basis,
             awaiting=None) -> dict:
    agent = agent_of(subject)
    base = subject.endswith("_V1")
    z = C.adjusted_z(ALPHA, FAMILY_SIZE)
    params = {"threshold_pp": threshold_pp,
              "threshold_basis": threshold_basis}
    if subject == "DEREK_CHALLENGER_A":
        params["threshold_pp"] = threshold_pp + THRESHOLD_STEP_PP
    if subject == "XAVIER_CHALLENGER_A":
        params["take_profit"] = TAKE_PROFIT
    if agent == "ALLOCATOR":
        params.update(sleeve_usd=SLEEVE_USD,
                      per_position_cap=PER_POSITION_CAP)
    doc = {
        "subject_id": subject, "version": version, "kind": "AGENT_VARIANT",
        "family": FAMILY, "role": "CHAMPION" if base else "CHALLENGER",
        "agent": agent, "code_version": VERSION,
        "policy": DESCRIPTIONS[subject],
        "training_window": "NOT_TRAINED_A_DECLARED_RULE",
        "features": ["p_reference", "price", "fee"] + (
            ["avoidance_level"] if subject == "DEREK_CHALLENGER_B" else []) + (
            ["edge_confidence", "liquidity_usd", "cal_half_width"]
            if subject == "ALLOCATOR_CHALLENGER_B" else []),
        "parameters": params,
        "entry_set": ("every opportunity offered" if agent == "DEREK" else
                      "the opportunities DEREK_V1 would enter (one shared "
                      "set, so no variant picks its own)"),
        "validation_method": (
            "PROSPECTIVE FORWARD on the shared opportunity set; decisions "
            "recorded before outcomes; paired against %s_V1 on the same "
            "opportunities" % agent),
        "minimum_sample": 1 if base else MIN_SAMPLE,
        "hypothesis_family": FAMILY, "family_size": FAMILY_SIZE,
        "metrics": ["net_economics", "max_drawdown", "capital_hours",
                    "turnover", "fees", "execution_loss", "false_refusals",
                    "risk"],
    }
    if base:
        doc["promotion_threshold"] = {"role": "BASELINE: the current policy"}
        doc["failure_threshold"] = {"role": "BASELINE"}
    else:
        doc["promotion_threshold"] = {
            "test": "PAIRED_FIXED_SAMPLE_VS_V1",
            "sample": "the FIRST minimum_sample resolved opportunities "
                      "offered to both, by opportunity_at; decided once",
            "requires_all": [
                "paired mean net P&L difference (challenger - V1) lower "
                "bound at z_adjusted > 0",
                "max drawdown <= %.2f x V1's" % DRAWDOWN_TOLERANCE],
            "alpha": ALPHA, "family_size": FAMILY_SIZE,
            "adjustment": "BONFERRONI", "z_adjusted": round(z, 6)}
        doc["failure_threshold"] = {
            "rule": "paired mean net P&L difference upper bound at "
                    "z_adjusted < 0 -> FAILED_FORWARD",
            "z_adjusted": round(z, 6)}
    if awaiting:
        doc["placeholder"] = {"status": awaiting, "why": (
            "EDDIE (claude/pos-agents) is not present: no %s table. The "
            "variants forecast nothing until EDDIE records execution "
            "estimates; then they are re-registered" % EDDIE_TABLE)}
    return doc


# ═════════════════════════════════════════════════════════════════════
# FORWARD DECISIONS (recorded before the outcome)
# ═════════════════════════════════════════════════════════════════════

def _cstar(opp):
    p, f = C.num(opp.get("price")), C.num(opp.get("fee"))
    return None if p is None or f is None else p + f


def derek(doc, opp, *, avoidance_level=None) -> dict:
    p = C.num(opp.get("p_reference"))
    action, net, why = M.decision(p, C.num(opp.get("price")),
                                  C.num(opp.get("fee")),
                                  doc["parameters"]["threshold_pp"])
    out = {"action": action, "predicted_net_edge": net,
           "abstain_reason": why, "probability": p, "output": {}}
    if (doc["subject_id"] == "DEREK_CHALLENGER_B" and action == "ENTER"
            and avoidance_level == "AVOID"):
        out["action"] = "PASS"
        out["output"]["refused_by"] = "AVOIDANCE_AVOID"
    out["output"]["avoidance_level_seen"] = avoidance_level
    if out["action"] == "ENTER":
        out["shadow_usd"] = C.rnd(_cstar(opp), 8)   # one contract
    return out


def champion_enters(opp, threshold_pp) -> bool:
    a, _, _ = M.decision(C.num(opp.get("p_reference")),
                         C.num(opp.get("price")), C.num(opp.get("fee")),
                         threshold_pp)
    return a == "ENTER"


def xavier(doc, opp, *, threshold_pp) -> dict:
    if not champion_enters(opp, threshold_pp):
        return {"action": "ABSTAIN",
                "abstain_reason": "NOT_IN_THE_SHARED_ENTRY_SET",
                "output": {}}
    plan = {"XAVIER_V1": "HOLD_TO_SETTLEMENT",
            "XAVIER_CHALLENGER_A": "TAKE_PROFIT_PLAN",
            "XAVIER_CHALLENGER_B": "EXIT_ON_EDGE_LOSS_PLAN"}[doc["subject_id"]]
    return {"action": plan, "shadow_usd": C.rnd(_cstar(opp), 8),
            "output": {"plan": plan,
                       "take_profit": doc["parameters"].get("take_profit")}}


def allocate(doc, batch: list, *, ec_by_opp: dict, threshold_pp) -> dict:
    """{opportunity_id: decision} for one cycle's batch of opportunities."""
    subject = doc["subject_id"]
    sleeve = float(doc["parameters"]["sleeve_usd"])
    cap = sleeve * float(doc["parameters"]["per_position_cap"])
    entries = [o for o in batch if champion_enters(o, threshold_pp)]
    raw = {}
    for o in entries:
        f = o.get("features") or {}
        c = _cstar(o)
        p = C.num(o.get("p_reference"))
        why = None
        if subject == "ALLOCATOR_V1":
            hw, mis = f.get("cal_half_width"), f.get("cal_miscal")
            got = SZ.shadow_size(
                p=p, cost=C.num(o.get("price")), fee_pc=C.num(o.get("fee")),
                depth_qty=f.get("depth_top_qty"),
                cal_unc={"half_width": hw, "miscalibration": mis,
                         "n": 300 if hw is not None else 0},
                exec_sd=None, exec_n=0, void_upper=None, same_game_open=0,
                drawdown_pct=None, regime=f.get("regime"), sleeve=sleeve)
            usd = got.get("shadow_usd")
            why = got.get("binding_constraint")
        elif subject == "ALLOCATOR_CHALLENGER_A":
            usd = min(cap, sleeve / max(1, len(entries)))
            why = "EQUAL_WEIGHT"
        else:
            ec = ec_by_opp.get(o["opportunity_id"])
            liq = f.get("liquidity_usd")
            hw = f.get("cal_half_width")
            if ec is None:
                usd, why = 0.0, "EDGE_CONFIDENCE_UNAVAILABLE_DECLARED_ZERO"
            else:
                ev = (p - c) / c
                kelly = max(0.0, (p - c) / (1.0 - c))
                f_liq = 1.0 if liq is None else min(1.0, liq / 1000.0)
                f_cal = 1.0 if hw is None else C.clamp(1.0 - 4.0 * hw)
                usd = min(cap, sleeve * 0.25 * kelly * ec * f_liq * f_cal)
                why = "EV_%.4f_X_EC_%.3f_X_LIQ_%.3f_X_CAL_%.3f_X_BUDGET" % (
                    ev, ec, f_liq, f_cal)
        raw[o["opportunity_id"]] = (max(0.0, usd or 0.0), why)
    total = sum(v[0] for v in raw.values())
    scale = min(1.0, sleeve / total) if total > 0 else 1.0
    out = {}
    for o in batch:
        if o["opportunity_id"] not in raw:
            out[o["opportunity_id"]] = {
                "action": "ABSTAIN",
                "abstain_reason": "NOT_IN_THE_SHARED_ENTRY_SET",
                "output": {}}
            continue
        usd, why = raw[o["opportunity_id"]]
        out[o["opportunity_id"]] = {
            "action": "ALLOCATE", "shadow_usd": C.rnd(usd * scale, 6),
            "output": {"basis": why, "sleeve_scale": C.rnd(scale, 6),
                       "batch_entries": len(entries)}}
    return out


def eddie(doc, opp, *, estimate, threshold_pp) -> dict:
    if not champion_enters(opp, threshold_pp):
        return {"action": "ABSTAIN",
                "abstain_reason": "NOT_IN_THE_SHARED_ENTRY_SET", "output": {}}
    if not estimate:
        return {"action": "ABSTAIN",
                "abstain_reason": "NO_EDDIE_ESTIMATE_FOR_THIS_OPPORTUNITY",
                "output": {}}
    f = opp.get("features") or {}
    s = doc["subject_id"]
    plan = "CROSS"
    if s == "EDDIE_CHALLENGER_A" and (C.num(estimate.get("fill_probability"))
                                      or 0.0) >= 0.5:
        plan = "REST_AT_MID"
    if s == "EDDIE_CHALLENGER_B" and (f.get("spread") is None
                                      or f["spread"] > 0.02):
        plan = "DO_NOT_CROSS"
    return {"action": "ENTER" if plan != "DO_NOT_CROSS" else "PASS",
            "shadow_usd": C.rnd(_cstar(opp), 8),
            "output": {"plan": plan, "eddie_estimate": estimate,
                       "evaluation": "UNPROVEN_UNTIL_EDDIE_RECORDS_FILLS"}}


# ═════════════════════════════════════════════════════════════════════
# EVALUATION (after outcomes)
# ═════════════════════════════════════════════════════════════════════

def xavier_exit(plan, opp, path, *, take_profit):
    """(exit_value_per_contract or None, exit_at or None, observed) by
    replaying the RECORDED plan over the post-entry path."""
    if plan == "HOLD_TO_SETTLEMENT":
        return None, None, True
    side = (opp.get("features") or {}).get("holding_side") or "LONG"
    c = _cstar(opp)
    books = sorted(path.get("books") or [], key=lambda b: b["at"])
    if not books:
        return None, None, False
    if plan == "TAKE_PROFIT_PLAN":
        for b in books:
            v = IC.book_view(b["bids"], b["offers"])
            px = (v["best_bid"] if side == "LONG" else
                  (None if v["best_offer"] is None else 1 - v["best_offer"]))
            if px is not None and px >= c + take_profit:
                return px, b["at"], True
        return None, None, True
    entry_price = C.num(opp.get("price"))
    for q in sorted(path.get("quotes") or [], key=lambda q: q["at"]):
        if q["p"] is None or q["p"] >= entry_price:
            continue
        prior = [b for b in books if q["at"] - 300 <= b["at"] <= q["at"]]
        if not prior:
            continue
        v = IC.book_view(prior[-1]["bids"], prior[-1]["offers"])
        px = (v["best_bid"] if side == "LONG" else
              (None if v["best_offer"] is None else 1 - v["best_offer"]))
        if px is not None:
            return px, q["at"], True
    return None, None, True


def pnl_row(subject, fc, opp, outcome, path=None, *, threshold_pp):
    """Per-opportunity realized result of one variant's recorded decision.
    Returns {pnl, capital, hours, fee, exec_loss, refused_good, sport} or
    None when the decision placed nothing."""
    agent = agent_of(subject)
    c = _cstar(opp)
    o = outcome.get("o")
    at = opp["opportunity_at"]
    end = outcome.get("outcome_at") or at
    f = opp.get("features") or {}
    fee = C.num(opp.get("fee")) or 0.0
    gap = f.get("cross_market_gap")
    p = C.num(opp.get("p_reference"))
    mid = None if (gap is None or p is None) else p - gap
    action = fc["action"]
    good = (c is not None and o is not None
            and champion_enters(opp, threshold_pp) and o - c > 0)
    base = {"sport": opp.get("sport"), "at": end, "refused_good": False,
            "path_observed": None}
    if action in ("PASS", "ABSTAIN"):
        base.update(pnl=0.0, capital=0.0, hours=0.0, fee=0.0, exec_loss=0.0,
                    refused_good=bool(good and action == "PASS"))
        return base
    if c is None:
        return None
    qty = 1.0
    if outcome.get("outcome_class") != "RESOLVED":
        # a VOID refunds the cost: no P&L, capital still tied up
        q = (C.num(fc.get("shadow_usd")) or 0.0) / c if agent == \
            "ALLOCATOR" else 1.0
        base.update(pnl=0.0, capital=q * c,
                    hours=q * c * max(0.0, end - at) / 3600.0, fee=0.0,
                    exec_loss=None)
        return base
    if agent == "ALLOCATOR":
        usd = C.num(fc.get("shadow_usd")) or 0.0
        qty = usd / c
    exit_value, exit_at = None, None
    if agent == "XAVIER":
        exit_value, exit_at, observed = xavier_exit(
            action, opp, path or {}, take_profit=TAKE_PROFIT)
        base["path_observed"] = observed
    value = exit_value if exit_value is not None else float(o)
    held_to = exit_at if exit_at is not None else end
    base.update(pnl=qty * (value - c), capital=qty * c,
                hours=qty * c * max(0.0, held_to - at) / 3600.0,
                fee=qty * fee,
                exec_loss=(None if mid is None else
                           qty * max(0.0, (c - fee) - mid)))
    return base


def summarize(rows: list) -> dict:
    out = C.Out(n=len(rows))
    if not rows:
        for k in ("net_economics_usd", "max_drawdown_usd", "capital_hours",
                  "turnover_usd", "fees_usd", "execution_loss_usd",
                  "false_refusals", "largest_position_usd",
                  "sport_concentration"):
            out.put(k, None, "NO_RESOLVED_OPPORTUNITY_YET")
        return out
    rows = sorted(rows, key=lambda r: r["at"])
    cum, peak, dd = 0.0, 0.0, 0.0
    for r in rows:
        cum += r["pnl"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    out.put("net_economics_usd", C.rnd(cum))
    out.put("max_drawdown_usd", C.rnd(dd))
    out.put("capital_hours", C.rnd(sum(r["hours"] for r in rows)))
    out.put("turnover_usd", C.rnd(sum(r["capital"] for r in rows)))
    out.put("fees_usd", C.rnd(sum(r["fee"] for r in rows)))
    el = [r["exec_loss"] for r in rows if r["exec_loss"] is not None]
    acted = [r for r in rows if r["capital"] > 0]
    out.put("execution_loss_usd", C.rnd(sum(el)) if el else None,
            "NO_VENUE_MID_AS_OF_T_FOR_ANY_ACTED_OPPORTUNITY")
    out.put("false_refusals", sum(1 for r in rows if r["refused_good"]))
    out.put("largest_position_usd",
            C.rnd(max(r["capital"] for r in acted)) if acted else None,
            "NOTHING_PLACED")
    by_sport: dict = {}
    for r in acted:
        by_sport[r["sport"]] = by_sport.get(r["sport"], 0.0) + r["capital"]
    tot = sum(by_sport.values())
    out.put("sport_concentration",
            C.rnd(max(by_sport.values()) / tot) if tot > 0 else None,
            "NOTHING_PLACED")
    unobserved = sum(1 for r in rows if r.get("path_observed") is False)
    out["acted"] = len(acted)
    out["path_unobserved"] = unobserved
    return out


def paired_verdict(diffs_in_time_order: list, *, min_sample, family_size,
                   dd_challenger=None, dd_base=None) -> dict:
    """The predeclared fixed-sample test on the FIRST min_sample pairs."""
    z = C.adjusted_z(ALPHA, family_size)
    n_all = len(diffs_in_time_order)
    out = C.Out(pairs_available=n_all, min_sample=min_sample,
                z_adjusted=round(z, 6))
    if n_all < min_sample:
        out["verdict"] = "INSUFFICIENT_SAMPLE"
        out.put("mean_difference", None,
                "FIXED_SAMPLE_%d_NOT_REACHED_HAVE_%d" % (min_sample, n_all))
        return out
    m, lo, hi, n = C.mean_ci(diffs_in_time_order[:min_sample], z)
    out.put("mean_difference", C.rnd(m))
    out["ci_adjusted"] = [C.rnd(lo), C.rnd(hi)]
    dd_ok = (dd_challenger is None or dd_base is None
             or dd_challenger <= DRAWDOWN_TOLERANCE * dd_base + 1e-9)
    if lo is not None and lo > 0 and dd_ok:
        out["verdict"] = "CRITERIA_MET"
    elif hi is not None and hi < 0:
        out["verdict"] = "FAILED"
    else:
        out["verdict"] = "NOT_MET"
    out["drawdown_ok"] = dd_ok
    return out


def finite(x):
    return x is not None and math.isfinite(x)
