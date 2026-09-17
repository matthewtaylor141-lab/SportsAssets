#!/usr/bin/env python3
"""WHALE PAIR RECONSTRUCTION. Pair economics from BUY-side flow alone.

A CORRECTION, RECORDED RATHER THAN QUIETLY FIXED. The EV_CORE_V1 report said
the whale corpus is "BUY-only, so pair completion, exits and residual
destruction are not present". That was WRONG, and the programme's own RN1
forensic work already showed why.

On a binary venue the two outcomes are separate tokens. An account closes its
exposure not by SELLING the leg it holds but by BUYING the complement. So a
BUY-only stream contains pair construction in full:

    MATCHED_QTY(t)   = min(CUM_YES_QTY(t), CUM_NO_QTY(t))
    DELTA_MATCHED(t) = MATCHED_QTY(t) - MATCHED_QTY(t-1)
    YES_RESIDUAL     = CUM_YES_QTY - MATCHED_QTY
    NO_RESIDUAL      = CUM_NO_QTY  - MATCHED_QTY

Every one of those is computable from BUYs. The earlier claim confused "no SELL
rows" with "no position reduction", and on a two-token venue those are
different things.

AN OPPOSITE-SIDE BUY IS NOT AUTOMATICALLY A COMPLETED PAIR. It may partially
pair, fully pair, leave residual, or overshoot past the existing leg and open
residual on the OTHER side. This module classifies each increment rather than
assuming, and the four classes are counted separately.

THE ECONOMICS NEED COST BASIS, NOT QUANTITY. A matched pair on a binary pays
exactly 1.00 per matched unit at settlement whichever way it lands, so its
profit is fixed at acquisition:

    MATCHED_PAIR_BASIS = the cost of one matched unit = avg_yes + avg_no
    matched pnl per unit = 1.00 - MATCHED_PAIR_BASIS   (before fees)

A basis above 1.00 is a LOCKED LOSS, bought deliberately or otherwise, and it is
knowable the moment the pair completes. Residual is the opposite: its outcome is
open and its P&L depends on settlement. Reporting them together is precisely the
error the whale cohort work warned about -- a pair-positive account can be
destroying more in residual than the pair locks in.

WHAT STILL GENUINELY NEEDS SELLS. Marking out an EXIT at a price (rather than
via the complement), and any voluntary reduction that is not a complement
purchase. Those specific components stay NOT_IDENTIFIED. The mechanism as a
whole does not.

This module contacts nothing and can place no order.
"""
import json
from collections import defaultdict
from decimal import Decimal as D, InvalidOperation

NOT_IDENTIFIED = "NOT_IDENTIFIED"

CORRECTION_OF = "EV_CORE_V1_REPORT_BUY_ONLY_UNTESTABLE_CLAIM"
CORRECTION = (
    "a BUY-only stream DOES contain pair construction: on a two-token binary "
    "venue the complement is acquired by buying, not by selling")

# How one opposite-side buy interacted with the existing leg.
INCREMENT_CLASSES = ("OPENS_NEW_LEG", "PARTIAL_PAIR", "EXACT_PAIR",
                     "PAIR_AND_OVERSHOOT", "ADDS_TO_EXISTING_LEG")

REQUIRES_SELLS = {
    "VOLUNTARY_EXIT_AT_A_PRICE": ("selling a leg back to the book; not present "
                                  "in a BUY-only corpus"),
    "REDUCTION_NOT_VIA_COMPLEMENT": "any size decrease that is not a pair",
    "REALISED_EXIT_MARKOUT": "needs an exit price, which needs a sell",
}
WHAT_IS_TESTABLE_WITHOUT_SELLS = (
    "cumulative legs, matched quantity, pair completion timing and hazard, "
    "pair basis, residual quantity and basis, and -- with settlements -- "
    "matched and residual P&L separately")


def _d(x):
    if x in (None, "", NOT_IDENTIFIED):
        return None
    try:
        return D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _leg_key(row):
    """Which of the binary's two tokens this fill is on.

    `outcome_index` is the venue's own 0/1 token index; `outcome` is its label.
    Index is preferred because a label can repeat across markets.
    """
    i = row.get("outcome_index")
    if i is not None:
        return int(i)
    return str(row.get("outcome"))


def reconstruct_market(fills):
    """Walk one market's BUY fills in time order and track both legs.

    Returns the full increment history plus the terminal position. A fill with
    an unparseable size or price is counted as skipped, never silently dropped.
    """
    rows = [f for f in (fills or ()) if isinstance(f, dict)]
    rows.sort(key=lambda f: (str(f.get("ts") or ""), str(f.get("trade_id") or "")))

    legs = {}                       # leg -> [cum_qty, cum_cost]
    matched = D(0)
    history = []
    classes = defaultdict(int)
    skipped = 0
    first_leg_ts = None
    first_leg_key = None
    complement_ts = None

    for f in rows:
        if str(f.get("side") or "").upper() not in ("BUY", ""):
            # A SELL would need different handling; the corpus has none, and
            # silently treating one as a buy would corrupt the basis.
            skipped += 1
            continue
        q = _d(f.get("size"))
        p = _d(f.get("price"))
        if q is None or p is None or q <= 0:
            skipped += 1
            continue

        k = _leg_key(f)
        if k not in legs:
            legs[k] = [D(0), D(0)]
        before_matched = matched
        other = [x for x in legs if x != k]

        legs[k][0] += q
        legs[k][1] += q * p

        if first_leg_ts is None:
            first_leg_ts, first_leg_key = f.get("ts"), k
        elif complement_ts is None and k != first_leg_key:
            complement_ts = f.get("ts")

        if other:
            matched = min(legs[k][0], legs[other[0]][0])
        delta = matched - before_matched

        # Classify what this increment did.
        if not other:
            cls = "OPENS_NEW_LEG" if legs[k][0] == q else "ADDS_TO_EXISTING_LEG"
        elif delta == 0:
            cls = "ADDS_TO_EXISTING_LEG"
        elif delta == q:
            cls = ("EXACT_PAIR" if legs[k][0] == legs[other[0]][0]
                   else "PARTIAL_PAIR")
        else:
            cls = "PAIR_AND_OVERSHOOT"
        classes[cls] += 1

        history.append({
            "TS": f.get("ts"), "LEG": k, "QTY": str(q), "PRICE": str(p),
            "CUM_QTY_THIS_LEG": str(legs[k][0]),
            "MATCHED_QTY": str(matched), "DELTA_MATCHED": str(delta),
            "INCREMENT_CLASS": cls,
        })

    if not legs:
        return {"MARKET_STATUS": "NO_USABLE_FILLS", "FILLS_SKIPPED": skipped}

    keys = sorted(legs)
    a = keys[0]
    b = keys[1] if len(keys) > 1 else None
    a_qty, a_cost = legs[a]
    b_qty, b_cost = legs[b] if b is not None else (D(0), D(0))

    avg_a = (a_cost / a_qty) if a_qty else None
    avg_b = (b_cost / b_qty) if b_qty else None
    pair_basis = (avg_a + avg_b) if (avg_a is not None and avg_b is not None) \
        else None

    res_a = a_qty - matched
    res_b = b_qty - matched
    res_basis_a = avg_a if res_a > 0 else None
    res_basis_b = avg_b if res_b > 0 else None

    return {
        "MARKET_STATUS": "RECONSTRUCTED",
        "LEGS_SEEN": len(keys),
        "LEG_A": a, "LEG_B": b if b is not None else NOT_IDENTIFIED,
        "CUM_YES_QTY": str(a_qty),
        "CUM_NO_QTY": str(b_qty),
        "AVG_YES_COST": str(avg_a) if avg_a is not None else NOT_IDENTIFIED,
        "AVG_NO_COST": str(avg_b) if avg_b is not None else NOT_IDENTIFIED,
        "MATCHED_QTY": str(matched),
        "MATCHED_PAIR_BASIS": (str(pair_basis) if pair_basis is not None
                               else NOT_IDENTIFIED),
        "MATCHED_LOCKED_PNL_PER_UNIT": (str(D(1) - pair_basis)
                                        if pair_basis is not None
                                        else NOT_IDENTIFIED),
        "MATCHED_LOCKED_PNL": (str((D(1) - pair_basis) * matched)
                               if pair_basis is not None else NOT_IDENTIFIED),
        "PAIR_BASIS_ABOVE_ONE": (bool(pair_basis > 1) if pair_basis is not None
                                 else NOT_IDENTIFIED),
        "YES_RESIDUAL_QTY": str(res_a),
        "NO_RESIDUAL_QTY": str(res_b),
        "RESIDUAL_BASIS_YES": (str(res_basis_a) if res_basis_a is not None
                               else NOT_IDENTIFIED),
        "RESIDUAL_BASIS_NO": (str(res_basis_b) if res_basis_b is not None
                              else NOT_IDENTIFIED),
        "PAIR_COMPLETION_FRACTION": (str(matched / max(a_qty, b_qty))
                                     if max(a_qty, b_qty) > 0
                                     else NOT_IDENTIFIED),
        "TIME_FIRST_LEG_TO_COMPLEMENT": _gap(first_leg_ts, complement_ts),
        "FIRST_LEG_TS": first_leg_ts or NOT_IDENTIFIED,
        "COMPLEMENT_TS": complement_ts or NOT_IDENTIFIED,
        "EVER_PAIRED": matched > 0,
        "INCREMENT_CLASSES": dict(classes),
        "FILLS_USED": len(history),
        "FILLS_SKIPPED": skipped,
        "HISTORY": history,
        "MATCHED_AND_RESIDUAL_ARE_SEPARATE": (
            "a matched unit settles at 1.00 whichever way the event lands, so "
            "its result is locked at acquisition; residual is still open, and "
            "adding them hides a pair-positive account destroying value in "
            "residual"),
    }


def _gap(a, b):
    if not a or not b:
        return NOT_IDENTIFIED
    from datetime import datetime, timezone

    def p(t):
        s = str(t).replace("Z", "+00:00")
        try:
            v = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    x, y = p(a), p(b)
    return (y - x).total_seconds() if (x and y) else NOT_IDENTIFIED


def settle_market(recon, payout_by_outcome, outcome_of_leg=None):
    """Split settled P&L into its matched and residual parts.

    Matched P&L is locked and does not depend on the outcome. Residual P&L
    does. Keeping them apart is the whole point.
    """
    if recon.get("MARKET_STATUS") != "RECONSTRUCTED":
        return {"SETTLED_MATCHED_PNL": NOT_IDENTIFIED,
                "SETTLED_RESIDUAL_PNL": NOT_IDENTIFIED}
    matched_pnl = _d(recon.get("MATCHED_LOCKED_PNL"))
    res_pnl = D(0)
    known = True
    for leg, qty_key, basis_key in ((recon["LEG_A"], "YES_RESIDUAL_QTY",
                                     "RESIDUAL_BASIS_YES"),
                                    (recon["LEG_B"], "NO_RESIDUAL_QTY",
                                     "RESIDUAL_BASIS_NO")):
        q = _d(recon.get(qty_key))
        basis = _d(recon.get(basis_key))
        if q is None or q <= 0:
            continue
        if basis is None:
            known = False
            continue
        name = (outcome_of_leg(leg) if outcome_of_leg else leg)
        pay = payout_by_outcome.get(str(name))
        if pay is None:
            known = False
            continue
        res_pnl += q * (D(str(pay)) - basis)
    return {
        "SETTLED_MATCHED_PNL": (str(matched_pnl) if matched_pnl is not None
                                else NOT_IDENTIFIED),
        "SETTLED_RESIDUAL_PNL": str(res_pnl) if known else NOT_IDENTIFIED,
        "RESIDUAL_PNL_COMPLETE": known,
        "MATCHED_PNL_IS_OUTCOME_INDEPENDENT": True,
        "WHY": ("a matched unit pays 1.00 regardless of which token wins, so "
                "its profit was fixed when the pair completed"),
    }


def reconstruct_all(fills, key=lambda f: f.get("condition_id")):
    """Reconstruct every market in a fill stream and summarise the mechanism."""
    by = defaultdict(list)
    for f in fills or ():
        k = key(f)
        if k:
            by[k].append(f)
    out = {k: reconstruct_market(v) for k, v in by.items()}
    ok = [r for r in out.values() if r.get("MARKET_STATUS") == "RECONSTRUCTED"]
    paired = [r for r in ok if r["EVER_PAIRED"]]
    two_leg = [r for r in ok if r["LEGS_SEEN"] > 1]
    gaps = [r["TIME_FIRST_LEG_TO_COMPLEMENT"] for r in paired
            if isinstance(r["TIME_FIRST_LEG_TO_COMPLEMENT"], float)]
    locked_loss = [r for r in paired if r["PAIR_BASIS_ABOVE_ONE"] is True]
    classes = defaultdict(int)
    for r in ok:
        for c, n in r["INCREMENT_CLASSES"].items():
            classes[c] += n
    gaps.sort()
    return {
        "MARKETS": out,
        "MARKETS_SEEN": len(out),
        "MARKETS_RECONSTRUCTED": len(ok),
        "MARKETS_WITH_BOTH_LEGS": len(two_leg),
        "MARKETS_EVER_PAIRED": len(paired),
        "PAIR_RATE_OF_TWO_LEG_MARKETS": (len(paired) / float(len(two_leg)))
                                        if two_leg else NOT_IDENTIFIED,
        "PAIR_RATE_OF_ALL_MARKETS": (len(paired) / float(len(ok)))
                                    if ok else NOT_IDENTIFIED,
        "MEDIAN_SECONDS_FIRST_LEG_TO_COMPLEMENT": (gaps[len(gaps) // 2]
                                                   if gaps else NOT_IDENTIFIED),
        "PAIRS_WITH_BASIS_ABOVE_ONE": len(locked_loss),
        "INCREMENT_CLASS_COUNTS": dict(classes),
        "INCREMENT_CLASSES": list(INCREMENT_CLASSES),
        "CORRECTION_OF": CORRECTION_OF,
        "CORRECTION": CORRECTION,
        "WHAT_IS_TESTABLE_WITHOUT_SELLS": WHAT_IS_TESTABLE_WITHOUT_SELLS,
        "REQUIRES_SELLS": dict(REQUIRES_SELLS),
    }


def render(rep):
    keys = ("MARKETS_SEEN", "MARKETS_RECONSTRUCTED", "MARKETS_WITH_BOTH_LEGS",
            "MARKETS_EVER_PAIRED", "PAIR_RATE_OF_TWO_LEG_MARKETS",
            "PAIR_RATE_OF_ALL_MARKETS",
            "MEDIAN_SECONDS_FIRST_LEG_TO_COMPLEMENT",
            "PAIRS_WITH_BASIS_ABOVE_ONE")
    L = ["%-42s = %s" % (k, rep.get(k, NOT_IDENTIFIED)) for k in keys]
    L.append("%-42s = %s" % ("INCREMENT_CLASS_COUNTS",
                             rep.get("INCREMENT_CLASS_COUNTS")))
    return "\n".join(L)


def to_json(rep):
    out = dict(rep)
    out.pop("MARKETS", None)
    return json.dumps(out, indent=1, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# THE COVERAGE GATE, AND IT INVALIDATED THE FIRST SET OF NUMBERS.
#
# The retained corpus (U2_SNAPSHOT_V1) is NOT RN1's full trade history. Its own
# manifest says so: 962,509 RN1 trades existed at the audit cutoff and only
# 214,609 of them carry a copy probe -- 22.3%. A pair reconstruction walks
# CUMULATIVE leg quantities, so a condition whose fills are only partly
# observed produces a matched quantity, a basis and a residual that are all
# wrong. Not noisy: wrong.
#
# Only conditions where EVERY RN1 trade was probed can carry a pair claim.
# `u0_timing_witness_v1` lists every trade id per condition, so completeness is
# CHECKABLE rather than assumed, and this gate does the check.
#
# BIAS DIRECTION, WHERE IT IS KNOWABLE. A missing fill can only ADD to a leg,
# and adding to a leg can only raise or hold min(YES, NO). So an incomplete
# MATCHED_QTY is a LOWER BOUND. The BASIS has no such guarantee -- a missing
# cheap fill lowers the true basis and a missing expensive one raises it -- so
# the basis-above-one finding cannot be signed on partial data at all.
# ---------------------------------------------------------------------------

COVERAGE_COMPLETE = "COMPLETE_ALL_TRADES_PROBED"
COVERAGE_PARTIAL = "PARTIAL_PROBE_COVERAGE"
COVERAGE_UNKNOWN = "COVERAGE_NOT_CHECKABLE"

CORPUS_IS_SINGLE_ACCOUNT = "YES"
CORPUS_ACCOUNT = "RN1"
ACCOUNT_PROVENANCE = (
    "research/SNAPSHOT_CANONICAL_SPEC.md section 1 defines the population as "
    "trades.whale_id = RN1 AND copy_probes.whale_id = RN1; the corpus is one "
    "economic account by construction, so no cross-account leg matching can "
    "occur")
WHY_COVERAGE_GATES_THE_CLAIM = (
    "cumulative legs need every fill; 22.3% of RN1's trades carry a probe, so "
    "a pair statistic over all conditions mixes complete and partial histories")
MATCHED_QTY_BIAS_ON_PARTIAL = "LOWER_BOUND"
BASIS_BIAS_ON_PARTIAL = "UNSIGNED"


def coverage_status(condition_id, observed_trade_ids, full_trade_ids_by_cond):
    """Is this condition's fill history complete? Checked, never assumed."""
    full = (full_trade_ids_by_cond or {}).get(condition_id)
    if full is None:
        return {"COVERAGE": COVERAGE_UNKNOWN, "OBSERVED": len(observed_trade_ids),
                "TOTAL": NOT_IDENTIFIED, "FRACTION": NOT_IDENTIFIED}
    obs = set(observed_trade_ids) & set(full)
    frac = (len(obs) / float(len(full))) if full else 0.0
    return {
        "COVERAGE": (COVERAGE_COMPLETE if len(obs) == len(full)
                     else COVERAGE_PARTIAL),
        "OBSERVED": len(obs), "TOTAL": len(full), "FRACTION": frac,
        "MISSING_FILLS": len(full) - len(obs),
    }


# --- ACCOUNTING CONVENTIONS -------------------------------------------------
# Once the two legs were acquired at different prices, WHICH units are called
# "matched" is a choice, and a choice can manufacture a result. Three
# defensible conventions are implemented and compared, and a finding is only
# reported as robust if it survives all three.

METHOD_WEIGHTED = "WEIGHTED_AVERAGE_LEG_BASIS"
METHOD_FIFO = "FIFO_LOT_MATCHING"
METHOD_INCREMENTAL = "EXACT_INCREMENTAL_COMPLEMENT_MATCHING"
METHODS = (METHOD_WEIGHTED, METHOD_FIFO, METHOD_INCREMENTAL)


def _lots(fills, leg):
    out = []
    for f in fills:
        if _leg_key(f) != leg:
            continue
        q, p = _d(f.get("size")), _d(f.get("price"))
        if q is None or p is None or q <= 0:
            continue
        out.append([q, p, str(f.get("ts") or "")])
    out.sort(key=lambda x: x[2])
    return out


def pair_basis_by_method(fills, method=METHOD_WEIGHTED):
    """Matched quantity and its cost, under one declared convention."""
    rows = [f for f in fills
            if str(f.get("side") or "").upper() in ("BUY", "")]
    legs = sorted({_leg_key(f) for f in rows})
    if len(legs) < 2:
        return None
    a, b = legs[0], legs[1]
    la, lb = _lots(rows, a), _lots(rows, b)
    qa = sum(x[0] for x in la)
    qb = sum(x[0] for x in lb)
    matched = min(qa, qb)
    if matched <= 0:
        return None

    if method == METHOD_WEIGHTED:
        ca = sum(x[0] * x[1] for x in la) / qa
        cb = sum(x[0] * x[1] for x in lb) / qb
        cost = matched * (ca + cb)
    elif method == METHOD_FIFO:
        # The EARLIEST units of each leg are the matched ones.
        cost = _take_fifo(la, matched) + _take_fifo(lb, matched)
    elif method == METHOD_INCREMENTAL:
        # Walk the stream: each increment pairs against the earliest unmatched
        # units of the opposite leg, at the prices actually then outstanding.
        cost = _incremental_cost(rows, a, b, matched)
        if cost is None:
            return None
    else:
        return None

    basis = cost / matched
    return {
        "METHOD": method, "MATCHED_QTY": matched, "MATCHED_COST": cost,
        "MATCHED_PAIR_BASIS": basis,
        "LOCKED_PAIR_PNL": (D(1) - basis) * matched,
        "BASIS_GT_1": basis > 1,
        "RESIDUAL_QTY": (qa - matched) + (qb - matched),
    }


def _take_fifo(lots, want):
    cost = D(0)
    left = want
    for q, p, _ in lots:
        if left <= 0:
            break
        take = min(q, left)
        cost += take * p
        left -= take
    return cost


def _incremental_cost(rows, a, b, matched):
    """Pair each increment against the oldest outstanding opposite units."""
    rows = sorted(rows, key=lambda f: (str(f.get("ts") or ""),
                                       str(f.get("trade_id") or "")))
    open_ = {a: [], b: []}
    cost = D(0)
    done = D(0)
    for f in rows:
        k = _leg_key(f)
        if k not in open_:
            continue
        q, p = _d(f.get("size")), _d(f.get("price"))
        if q is None or p is None or q <= 0:
            continue
        other = b if k == a else a
        while q > 0 and open_[other]:
            oq, op = open_[other][0]
            take = min(q, oq)
            cost += take * (p + op)
            done += take
            q -= take
            if oq - take <= 0:
                open_[other].pop(0)
            else:
                open_[other][0][0] = oq - take
        if q > 0:
            open_[k].append([q, p])
    return cost if done >= matched else (cost if done > 0 else None)


def robustness(fills):
    """Compare the three conventions on one market."""
    out = {}
    for m in METHODS:
        r = pair_basis_by_method(fills, m)
        out[m] = r
    vals = [r for r in out.values() if r]
    if not vals:
        return {"METHODS": out, "PAIR_STATUS": "NO_PAIR"}
    signs = {bool(r["BASIS_GT_1"]) for r in vals}
    return {
        "METHODS": {k: ({kk: str(vv) for kk, vv in v.items()} if v else None)
                    for k, v in out.items()},
        "ALL_METHODS_AGREE_ON_SIGN": len(signs) == 1,
        "BASIS_GT_1_UNDER_ALL": signs == {True},
        "BASIS_GT_1_UNDER_NONE": signs == {False},
        "PAIR_STATUS": "COMPARED",
        "WHY_THREE_METHODS": (
            "which units are called matched is a convention, and a convention "
            "must not be what produces the finding"),
    }
