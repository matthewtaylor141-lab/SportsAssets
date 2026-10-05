"""EXECUTION-COST / ADVERSE-SELECTION LEARNING AND THE EXECUTION-POLICY
LEAGUE (RESEARCH). Pure; no I/O.

FROM RECORDED FILLS VERSUS LATER RECORDED MARKS ONLY. Per fill, in the held
side's cost space (a LONG buys the offers; a SHORT holds the complement):

  mid at fill      the holding side's mid of the book recorded AT the fill
                   (its own book_obs_id, else the latest observation at or
                   before it within reads.FILL_BOOK_MAX_S)
  spread cost      BUY: (price - mid) x qty; SELL: (mid - price) x qty
  fee              the fill's recorded fee
  adverse at h     BUY: (mid at fill - mid at h) x qty; SELL: the reverse --
                   positive when the price moved against the fill, i.e. the
                   counterparty knew more. The mark at h is the FIRST
                   observation recorded in [t + h, t + h + tolerance]; no
                   observation there -> that horizon is unmeasured for the
                   fill (never interpolated, never zero)

LEARNING. Per (strategy, style) the mean adverse selection per contract at
each horizon is reported raw and SHRUNK toward the pooled mean of every
fill (empirical Bayes, prior strength K_PRIOR fills): with few fills the
estimate leans on the pool, with many on its own record. Archer's recorded
predicted-versus-realized execution loss (eddie_execution_outcomes) is
summarised beside it: whether the existing estimator beats its naive
baseline.

THE LEAGUE. Each execution POLICY -- (direction, order type, time in force)
-- ranked on realized cost per filled dollar = (spread + fee + adverse at
LEAGUE_HORIZON) / gross filled $, over the fills where every term was
measured, with a bootstrap CI; fill rate from the policy's terminal orders.
Ranks only policies with at least MIN_FILLS measured fills. A ranking
changes nothing: no order type, size or route is chosen from it.
"""
from __future__ import annotations

from . import common as C
from .reads import HORIZONS

VERSION = "POS_OS_EXECUTION_V1"
K_PRIOR = 10.0
MIN_FILLS = 5
LEAGUE_HORIZON = 300.0
MAKER, TAKER = "MAKER", "TAKER"
TERMINAL = ("FILLED", "PARTIALLY_FILLED", "EXPIRED", "CANCELED", "CANCELLED",
            "REJECTED")


def style_of(order_type):
    return MAKER if str(order_type or "").upper() == "RESTING" else TAKER


def fill_costs(f):
    """One fill's measured execution terms (USD) and per-contract pp."""
    qty, price = C.num(f.get("qty")), C.num(f.get("price"))
    side = str(f.get("holding_side") or "LONG").upper()
    sell = str(f.get("direction") or "BUY").upper() == "SELL"
    out = {"fill_id": f.get("fill_id"), "strategy": f.get("strategy"),
           "style": style_of(f.get("order_type")),
           "policy": "%s/%s/%s" % ("SELL" if sell else "BUY",
                                   f.get("order_type") or "UNKNOWN",
                                   f.get("time_in_force") or "UNKNOWN"),
           "qty": qty, "fee_usd": C.num(f.get("fee_usd")),
           "gross_usd": (C.num(f.get("gross_usd"))
                         if C.num(f.get("gross_usd")) is not None
                         else (qty * price if qty and price else None)),
           "unmeasured": {}}
    m0 = C.mid_of(f.get("b0_bids"), f.get("b0_offers"), side)
    if not qty or price is None or m0 is None:
        out["spread_usd"] = None
        out["unmeasured"]["spread"] = C.R_NO_MID_AT_FILL
        for h in HORIZONS:
            out["adverse_usd_%d" % h] = None
        return out
    sgn = -1.0 if sell else 1.0
    out["mid_at_fill"] = C.rnd(m0)
    out["spread_usd"] = sgn * (price - m0) * qty
    for i, h in enumerate(HORIZONS):
        mh = C.mid_of(f.get("m%d_bids" % i), f.get("m%d_offers" % i), side)
        if mh is None:
            out["adverse_usd_%d" % h] = None
            out["unmeasured"]["adverse_%d" % h] = C.R_NO_MARK_AFTER_FILL
        else:
            out["adverse_usd_%d" % h] = sgn * (m0 - mh) * qty
    return out


def _shrunk(own_sum, own_n, pool_mean):
    if pool_mean is None:
        return None
    return (own_sum + K_PRIOR * pool_mean) / (own_n + K_PRIOR)


def learn(costs):
    """Per (strategy, style): spread, fee and adverse selection per
    contract at every horizon, raw and shrunk toward the pool."""
    pool = {}
    for h in HORIZONS:
        xs = [(c["adverse_usd_%d" % h], c["qty"]) for c in costs
              if c.get("adverse_usd_%d" % h) is not None]
        q = sum(x[1] for x in xs)
        pool[h] = (sum(x[0] for x in xs) / q) if q else None
    groups: dict = {}
    for c in costs:
        groups.setdefault((str(c["strategy"]), c["style"]), []).append(c)
    rows = []
    for (s, st), cs in sorted(groups.items()):
        meas = [c for c in cs if c.get("spread_usd") is not None]
        q = sum(c["qty"] for c in meas)
        gross = sum(c["gross_usd"] or 0.0 for c in cs)
        fees = [c["fee_usd"] for c in cs if c["fee_usd"] is not None]
        row = {"strategy": s, "style": st, "fills": len(cs),
               "fills_with_mid": len(meas),
               "spread_pp": C.rnd(sum(c["spread_usd"] for c in meas) / q)
               if q else None,
               "fee_per_filled_dollar": C.rnd(sum(fees) / gross)
               if gross and fees else None,
               "adverse": {}}
        for h in HORIZONS:
            xs = [c for c in cs if c.get("adverse_usd_%d" % h) is not None]
            qh = sum(c["qty"] for c in xs)
            sh = sum(c["adverse_usd_%d" % h] for c in xs)
            row["adverse"]["%ds" % h] = {
                "n": len(xs), "raw_pp": C.rnd(sh / qh) if qh else None,
                "shrunk_pp": C.rnd(_shrunk(sh / qh * len(xs) if qh else 0.0,
                                           len(xs), pool[h])),
                "pool_pp": C.rnd(pool[h]),
                "status": C.MEASURED if len(xs) >= MIN_FILLS
                else C.INSUFFICIENT}
        rows.append(row)
    return {"by_strategy_style": rows,
            "pooled_adverse_pp": {"%ds" % h: C.rnd(v)
                                  for h, v in pool.items()},
            "prior_strength_fills": K_PRIOR}


def archer_feedback(outcomes):
    if outcomes is None:
        return {"status": C.UNAVAILABLE, "why": C.R_INPUT_NOT_READ}
    xs = [o for o in outcomes
          if C.num(o.get("realized_execution_loss_pp")) is not None
          and C.num(o.get("predicted_execution_loss_pp")) is not None]
    if not xs:
        return {"status": C.EMPTY, "why": "NO_MEASURED_ARCHER_OUTCOME",
                "n": 0}
    err = [o["realized_execution_loss_pp"] - o["predicted_execution_loss_pp"]
           for o in xs]
    nv = [abs(o["realized_execution_loss_pp"] - o["naive_execution_loss_pp"])
          for o in xs if C.num(o.get("naive_execution_loss_pp")) is not None]
    return {"status": C.OK, "n": len(xs),
            "mean_realized_minus_predicted_pp": C.rnd(C.mean(err)),
            "mean_abs_error_pp": C.rnd(C.mean([abs(e) for e in err])),
            "naive_mean_abs_error_pp": C.rnd(C.mean(nv)) if nv else None,
            "beats_naive": (C.mean([abs(e) for e in err]) < C.mean(nv))
            if nv else None, "source": "eddie_execution_outcomes"}


def league(costs, orders, *, horizon=LEAGUE_HORIZON, min_fills=MIN_FILLS):
    key = "adverse_usd_%d" % horizon
    pol: dict = {}
    for c in costs:
        pol.setdefault(c["policy"], {"fills": [], "orders": []})[
            "fills"].append(c)
    for o in orders or []:
        k = "%s/%s/%s" % (str(o.get("direction") or "BUY").upper(),
                          o.get("order_type") or "UNKNOWN",
                          o.get("time_in_force") or "UNKNOWN")
        pol.setdefault(k, {"fills": [], "orders": []})["orders"].append(o)
    rows = []
    for k, e in sorted(pol.items()):
        full = [c for c in e["fills"] if c.get("spread_usd") is not None
                and c.get(key) is not None and c["fee_usd"] is not None
                and c["gross_usd"]]
        pairs = [(c["spread_usd"] + c["fee_usd"] + c[key], c["gross_usd"])
                 for c in full]
        g = sum(p[1] for p in pairs)
        term = [o for o in e["orders"] if o.get("state") in TERMINAL]
        oq = sum(C.num(o.get("qty")) or 0.0 for o in term)
        fq = sum(C.num(o.get("filled_qty")) or 0.0 for o in term)
        lo, hi = C.bootstrap_ratio_ci(pairs, seed=C.seed_of(
            ["league", k, len(pairs)])) if len(pairs) >= 2 else (None, None)
        rows.append({
            "policy": k, "fills": len(e["fills"]),
            "fills_fully_measured": len(full),
            "filled_usd": C.rnd(sum(c["gross_usd"] or 0.0
                                    for c in e["fills"])),
            "terminal_orders": len(term),
            "fill_rate_qty": C.share(fq, oq),
            "spread_per_filled_dollar": C.rnd(
                sum(c["spread_usd"] for c in full) / g) if g else None,
            "fee_per_filled_dollar": C.rnd(
                sum(c["fee_usd"] for c in full) / g) if g else None,
            "adverse_per_filled_dollar": C.rnd(
                sum(c[key] for c in full) / g) if g else None,
            "realized_cost_per_filled_dollar": C.rnd(
                sum(p[0] for p in pairs) / g) if g else None,
            "ci90": [C.rnd(lo), C.rnd(hi)],
            "status": C.MEASURED if len(full) >= min_fills
            else C.INSUFFICIENT})
    ranked = sorted((r for r in rows if r["status"] == C.MEASURED),
                    key=lambda r: r["realized_cost_per_filled_dollar"])
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return {"horizon_s": horizon, "min_fills": min_fills, "policies": rows,
            "ranking": [r["policy"] for r in ranked],
            "metric": "(spread + fee + adverse selection at horizon) / "
                      "gross filled $; lower is better",
            "chooses_nothing": True}


def build_learning(inputs, *, now):
    if inputs.get("fills") is None:
        return C.unread(["fills"], inputs, audit=C.BUILT,
                        sources=["paper_fills", "paper_book_observations"])
    costs = [fill_costs(f) for f in inputs["fills"]]
    data = learn(costs) if costs else None
    data = dict(data or {}, archer=archer_feedback(inputs.get("archer_outcomes")),
                horizons_s=list(HORIZONS), version=VERSION)
    return C.section(
        C.OK if costs else C.EMPTY,
        None if costs else "%s: no fill in the window" % C.R_NO_ROWS,
        audit=C.BUILT, data=data,
        sources=["paper_fills", "paper_orders", "paper_book_observations",
                 "eddie_execution_outcomes"],
        existing={"single_horizon_style_markout":
                  "sportsassets/agents/archer.py:summarise_history"})


def build_league(inputs, *, now):
    miss = C.missing(inputs, ["fills", "orders"])
    if miss:
        return C.unread(miss, inputs, audit=C.BUILT,
                        sources=["paper_fills", "paper_orders"])
    costs = [fill_costs(f) for f in inputs["fills"]]
    if not costs and not inputs["orders"]:
        return C.section(C.EMPTY, "%s: no order or fill in the window"
                         % C.R_NO_ROWS, audit=C.BUILT,
                         sources=["paper_fills", "paper_orders"])
    return C.section(C.OK, None, audit=C.BUILT,
                     data=dict(league(costs, inputs["orders"]),
                               version=VERSION),
                     sources=["paper_fills", "paper_orders",
                              "paper_book_observations"])
