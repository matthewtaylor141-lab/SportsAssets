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
from collections import Counter, defaultdict
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
    o = row.get("outcome")
    if o is None:
        # A fill that names neither an index nor a label has no leg. Returning
        # str(None) would bucket every such fill onto one shared phantom leg
        # and pair them with each other, which is worse than losing them.
        return None
    return str(o)


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


# ---------------------------------------------------------------------------
# THE SELECTION TEST, AND IT CAME BACK NEGATIVE.
#
# The complete-coverage subset is 9,269 of 17,752 conditions (52.2%), which
# looks like a comfortable half. It is not a random half. A condition is
# "complete" only if EVERY one of its trades happened to carry a probe, and the
# more trades a condition has the less likely that is. Completeness therefore
# selects for FEW TRADES almost mechanically.
#
# Measured, standardised mean differences (complete vs partial):
#
#     u0_trade_count   4.74 vs 28.77   SMD -0.776   LARGE
#     sides_seen       1.36 vs  1.69   SMD -0.690   LARGE
#     u2_fill_count    4.74 vs 18.02   SMD -0.564   LARGE
#     notional          749 vs  4,534  SMD -0.288   SMALL
#     span_s          3,207 vs  5,195  SMD -0.202   SMALL
#     first_price      0.46 vs   0.48  SMD -0.042   NEGLIGIBLE
#
#     sport mix: complete 60.8% Soccer / 10.7% Tennis
#                partial  36.3% Soccer / 36.2% Tennis
#
# `sides_seen` is the one that bites. The complete subset is biased TOWARD
# conditions where RN1 only ever touched ONE side -- which cannot pair at all --
# and AWAY from the busy two-sided conditions where pairing actually happens.
# The valid population is therefore systematically the SMALL, SIMPLE, ONE-SIDED
# end of RN1's book, and that is precisely not the end the pair question is
# about.
# ---------------------------------------------------------------------------

COMPLETE_SUBSET_SELECTION_STATUS = "SELECTED"
SELECTION_MECHANISM = (
    "completeness requires every trade to carry a probe, so conditions with "
    "more trades are mechanically less likely to qualify; the filter selects "
    "for few trades, and few trades means fewer sides and less pairing")
SELECTION_LARGE_EFFECTS = {
    "u0_trade_count": -0.776,
    "sides_seen": -0.690,
    "u2_fill_count": -0.564,
}
WHY_THE_AGGREGATE_MAY_NOT_GENERALISE = (
    "the +$96k aggregate and the 38-39% loss-lock rate are valid ON the "
    "complete subset; that subset under-samples the heavily traded two-sided "
    "conditions where pairing concentrates, so neither figure may be carried "
    "to RN1's whole book")
EFFECT_SIZE_NOT_P_VALUES = (
    "with 17,752 conditions almost any difference is 'significant'; these are "
    "standardised mean differences, and three of them exceed 0.5")

FULL_EXTRACTION_EXECUTED = "NO"
FULL_EXTRACTION_BLOCKER = (
    "the U0 population lives in the production database; no DATABASE_URL, no "
    "PG environment variables and no credentials are present in this "
    "environment, and production access is forbidden by standing instruction. "
    "EV_CORE_WHALE_EXTRACTION_SPEC.md is ready for an authorised operator")


def selection_report(complete_features, partial_features):
    """Standardised mean differences between the two condition populations."""
    import math
    out = {}
    for k in set(complete_features) & set(partial_features):
        a, b = complete_features[k], partial_features[k]
        if len(a) < 2 or len(b) < 2:
            continue
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a) / len(a)
        vb = sum((x - mb) ** 2 for x in b) / len(b)
        pooled = math.sqrt((va + vb) / 2)
        d = (ma - mb) / pooled if pooled > 0 else 0.0
        out[k] = {
            "COMPLETE_MEAN": ma, "PARTIAL_MEAN": mb, "SMD": d,
            "EFFECT": ("NEGLIGIBLE" if abs(d) < 0.1 else
                       "SMALL" if abs(d) < 0.3 else
                       "MEDIUM" if abs(d) < 0.5 else "LARGE"),
        }
    large = [k for k, v in out.items() if v["EFFECT"] == "LARGE"]
    return {
        "FEATURES": out,
        "LARGE_EFFECTS": sorted(large),
        "SELECTION_STATUS": ("SELECTED" if large else "REPRESENTATIVE"),
        "EFFECT_SIZE_NOT_P_VALUES": EFFECT_SIZE_NOT_P_VALUES,
        "SELECTION_MECHANISM": SELECTION_MECHANISM,
    }


# ===========================================================================
# THE SELECTION FINDING, FROZEN (directive section 1)
# ===========================================================================
#
# This block is the finding itself, in a form that TRAVELS. Any statement made
# from the complete-coverage subset must carry these constants beside it. A
# number that appears without them has been stripped of the only context that
# makes it honest.

SELECTION_FINDING_STATUS = "FROZEN"

RN1_CONDITIONS_TOTAL = 17752
RN1_CONDITIONS_COMPLETE_COVERAGE = 9269
RN1_COMPLETE_COVERAGE_PCT = 52.2
RN1_U2_FILLS_RETAINED = 214609
RN1_U0_TRADES_AT_CUTOFF = 962509
RN1_RETAINED_FRACTION_PCT = 22.3
AUDIT_CUTOFF_TS = "2026-09-12T00:00:00Z"

SUBSET_LABEL = "PIPELINE_VALIDATION_SELECTED_SUBSET"
SUBSET_INFERENCE_LABEL = "NOT_VALID_FOR_RN1_POPULATION_INFERENCE"

# What the subset MAY be used for.
SUBSET_PERMITTED_USES = (
    "testing that the code runs and the pipeline executes end to end",
    "verifying accounting identities hold on real rows",
    "building and exercising schemas",
    "descriptive statistics explicitly scoped to the subset itself",
)

# What it may NOT be used for. This list is the operative one.
SUBSET_FORBIDDEN_USES = (
    "inferring RN1's population complement policy",
    "estimating population pair profitability",
    "estimating population loss-lock frequency",
    "fitting production whale-policy coefficients",
    "claiming any population-level causal or predictive relationship",
)

DO_NOT_TRAIN_OR_VALIDATE_BETTOR_POLICY_FROM_THIS_SUBSET = True


def selection_constants():
    """The constants that must travel with any subset-derived number."""
    return {
        "SELECTION_FINDING_STATUS": SELECTION_FINDING_STATUS,
        "COMPLETE_SUBSET_SELECTION_STATUS": COMPLETE_SUBSET_SELECTION_STATUS,
        "SELECTION_MECHANISM": SELECTION_MECHANISM,
        "SELECTION_LARGE_EFFECTS": dict(SELECTION_LARGE_EFFECTS),
        "EFFECT_SIZE_NOT_P_VALUES": EFFECT_SIZE_NOT_P_VALUES,
        "RN1_CONDITIONS_TOTAL": RN1_CONDITIONS_TOTAL,
        "RN1_CONDITIONS_COMPLETE_COVERAGE": RN1_CONDITIONS_COMPLETE_COVERAGE,
        "RN1_COMPLETE_COVERAGE_PCT": RN1_COMPLETE_COVERAGE_PCT,
        "RN1_U2_FILLS_RETAINED": RN1_U2_FILLS_RETAINED,
        "RN1_U0_TRADES_AT_CUTOFF": RN1_U0_TRADES_AT_CUTOFF,
        "RN1_RETAINED_FRACTION_PCT": RN1_RETAINED_FRACTION_PCT,
        "AUDIT_CUTOFF_TS": AUDIT_CUTOFF_TS,
        "SUBSET_LABEL": SUBSET_LABEL,
        "SUBSET_INFERENCE_LABEL": SUBSET_INFERENCE_LABEL,
        "SUBSET_PERMITTED_USES": list(SUBSET_PERMITTED_USES),
        "SUBSET_FORBIDDEN_USES": list(SUBSET_FORBIDDEN_USES),
        "DO_NOT_TRAIN_OR_VALIDATE_BETTOR_POLICY_FROM_THIS_SUBSET":
            DO_NOT_TRAIN_OR_VALIDATE_BETTOR_POLICY_FROM_THIS_SUBSET,
    }


# ===========================================================================
# THE TWO RN1 RESULTS, DOWNGRADED (directive section 2)
# ===========================================================================
#
# Both results stand as facts about the subset. Neither is a fact about RN1.
# The difference is not a caveat; it is the finding.

POPULATION_GENERALIZABILITY = "NO"
POPULATION_ESTIMATE = NOT_IDENTIFIED

NEVER_DESCRIBE_AS = (
    "RN1 overall",
    "RN1's book",
    "RN1's strategy",
    "the whale's realised P&L",
)

RESULT_BASIS_ABOVE_ONE = {
    "NAME": "RN1_BASIS_ABOVE_ONE_RATE",
    "SCOPE": SUBSET_LABEL,
    "MARKETS_IN_SCOPE": 3310,
    "VALUE_BY_METHOD": {METHOD_WEIGHTED: 38.2,
                        METHOD_FIFO: 39.1,
                        METHOD_INCREMENTAL: 39.1},
    "UNITS": "percent of paired markets",
    "POPULATION_GENERALIZABILITY": POPULATION_GENERALIZABILITY,
    "POPULATION_ESTIMATE": POPULATION_ESTIMATE,
    "INFERENCE_LABEL": SUBSET_INFERENCE_LABEL,
}

RESULT_LOCKED_PNL = {
    "NAME": "RN1_LOCKED_PAIR_PNL",
    "SCOPE": SUBSET_LABEL,
    "MARKETS_IN_SCOPE": 3310,
    "VALUE_BY_METHOD": {METHOD_WEIGHTED: 97388.0,
                        METHOD_FIFO: 95609.0,
                        METHOD_INCREMENTAL: 95606.0},
    "UNITS": "USD",
    "SIGN_AGREEMENT_ACROSS_METHODS_PCT": 90.3,
    "POPULATION_GENERALIZABILITY": POPULATION_GENERALIZABILITY,
    "POPULATION_ESTIMATE": POPULATION_ESTIMATE,
    "INFERENCE_LABEL": SUBSET_INFERENCE_LABEL,
}

RN1_RESULTS = (RESULT_BASIS_ABOVE_ONE, RESULT_LOCKED_PNL)


def stamped(result):
    """A subset result with its selection constants attached. Use this.

    Reporting a subset number without the constants is the failure mode this
    exists to prevent, so the stamping is a function rather than a convention.
    """
    out = dict(result)
    out["SELECTION_CONSTANTS"] = selection_constants()
    out["NEVER_DESCRIBE_AS"] = list(NEVER_DESCRIBE_AS)
    return out


# ===========================================================================
# THE THREE ACCOUNTING OBJECTS, NAMED (directive section 5)
# ===========================================================================
#
# Three conventions were computed and they answer three different questions.
# Leaving them unnamed is what let "the pair cost basis" mean whichever of them
# happened to be in scope.

PORTFOLIO_MATCHED_ECONOMICS = {
    "NAME": "PORTFOLIO_MATCHED_ECONOMICS",
    "METHODS": (METHOD_WEIGHTED, METHOD_FIFO),
    "QUESTION": ("given everything RN1 holds in this market, what does the "
                 "matched block as a whole cost?"),
    "UNIT": "the market's whole matched position",
    "GOOD_FOR": ("mark-to-settlement of an existing book; reporting realised "
                 "and locked P&L"),
    "WRONG_FOR": ("asking what a single complement purchase was worth at the "
                  "moment it was made -- a weighted average recomputes the "
                  "basis of shares bought long ago"),
}

INCREMENTAL_COMPLEMENT_DECISION_ECONOMICS = {
    "NAME": "INCREMENTAL_COMPLEMENT_DECISION_ECONOMICS",
    "METHODS": (METHOD_INCREMENTAL,),
    "QUESTION": ("at the moment RN1 bought this complement, what did THAT "
                 "purchase lock in?"),
    "UNIT": "the individual complement fill",
    "GOOD_FOR": ("modelling the decision; every row of a policy training "
                 "table; any statement about why a basis above one was "
                 "accepted"),
    "WRONG_FOR": "reporting the book's total position value",
}

ACCOUNTING_OBJECTS = (PORTFOLIO_MATCHED_ECONOMICS,
                      INCREMENTAL_COMPLEMENT_DECISION_ECONOMICS)

PRIMARY_FOR_COMPLEMENT_DECISIONS = "INCREMENTAL_COMPLEMENT_DECISION_ECONOMICS"
WHY_INCREMENTAL_IS_PRIMARY = (
    "A decision is made at a point in time with the prices available then. "
    "Weighted-average basis answers a question about the portfolio, and FIFO "
    "answers a question about lot ordering; neither is the question the "
    "decision-maker faced. When the subject is 'why did he buy the other side "
    "here', the incremental convention is the only one whose unit is the "
    "decision."
)


# ===========================================================================
# COMPLEMENT SEMANTICS (directive section 4)
# ===========================================================================
#
# Everything in this module rests on one assumption: that leg A and leg B of a
# condition are COMPLEMENTS, so one of each pays exactly 1.00 whatever happens.
# That is what makes MATCHED_LOCKED_PNL outcome-independent, and it is what
# makes "basis above one" mean a locked loss rather than an open position.
#
# The assumption has never been checked. This checks it.
#
# WHAT CAN AND CANNOT BE VERIFIED
# -------------------------------
# The requirement is PAYOFF_A(s) + PAYOFF_B(s) = 1 for EVERY terminal state s.
# A settled market shows us exactly ONE terminal state -- the one that actually
# happened. So the corpus can verify the identity in the state it observed and
# nowhere else.
#
# Two verdicts, kept apart, because collapsing them is exactly the error:
#
#   COMPLEMENT_VERIFIED_IN_OBSERVED_STATE  -- measurable, and measured below.
#   COMPLEMENT_GUARANTEE_STATUS            -- the all-states claim. It stays
#                                             NOT_VERIFIED until the venue's
#                                             settlement rule is read from the
#                                             venue, not inferred from outcomes.
#
# Observing 9,542 markets each pay 1.00 in its own realised state is strong
# evidence and is not a proof. A market that pays 1.00 whenever the favourite
# wins and 0.00 on a void would pass every observation we have and still break
# the pair arithmetic on the day it voids.

COMPLEMENT_GUARANTEE_STATUS = "NOT_VERIFIED"
WHY_NOT_VERIFIED = (
    "A settled market exhibits one terminal state. The all-states identity "
    "cannot be established from realised outcomes at any sample size; it needs "
    "the venue's published settlement rule, including its void, tie, "
    "abandonment and early-settlement branches. Nothing in the retained corpus "
    "contains that rule."
)

COMPLEMENT_OBSERVED_STATUS_VERIFIED = "VERIFIED_IN_OBSERVED_STATE"
COMPLEMENT_OBSERVED_STATUS_VIOLATED = "VIOLATED_IN_OBSERVED_STATE"
COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE = "NOT_CHECKABLE"

BINARY_TOKEN_COUNT = 2
PAYOUT_SUM_TOLERANCE = D("0.000001")

WHAT_A_VIOLATION_MEANS = (
    "If a condition's observed payouts do not sum to 1.00, that condition is "
    "not a binary complement and every pair number computed on it is wrong -- "
    "not approximately wrong, categorically wrong, because the matched block "
    "is no longer outcome-independent. A violation is a reason to exclude the "
    "condition, never a rounding note."
)


def check_complement(settlement_row):
    """Verify PAYOFF_A + PAYOFF_B = 1 in the one terminal state we observed.

    Returns (status, detail). Refuses on anything it cannot read rather than
    treating an unreadable market as a passing one.
    """
    if not settlement_row.get("resolved"):
        return COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE, {
            "REASON": "NOT_RESOLVED"}
    payouts = settlement_row.get("payouts")
    tokens = settlement_row.get("tokens") or []
    if not isinstance(payouts, (list, tuple)) or not payouts:
        return COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE, {
            "REASON": "NO_PAYOUT_ARRAY"}
    if len(tokens) != BINARY_TOKEN_COUNT:
        # Three or more outcomes is not a binary complement at all. This is a
        # structural fact about the market, not a data quality problem.
        return COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE, {
            "REASON": "NOT_A_TWO_TOKEN_MARKET", "TOKEN_COUNT": len(tokens)}
    if len(payouts) != BINARY_TOKEN_COUNT:
        return COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE, {
            "REASON": "PAYOUT_LENGTH_DOES_NOT_MATCH_TOKEN_COUNT",
            "PAYOUT_LEN": len(payouts), "TOKEN_COUNT": len(tokens)}
    try:
        vals = [D(str(p)) for p in payouts]
    except Exception:                                          # noqa: BLE001
        return COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE, {
            "REASON": "PAYOUT_NOT_NUMERIC", "PAYOUTS": list(payouts)}
    total = sum(vals, D(0))
    if abs(total - D(1)) <= PAYOUT_SUM_TOLERANCE:
        return COMPLEMENT_OBSERVED_STATUS_VERIFIED, {
            "PAYOUT_SUM": str(total), "PAYOUTS": [str(v) for v in vals]}
    return COMPLEMENT_OBSERVED_STATUS_VIOLATED, {
        "PAYOUT_SUM": str(total), "PAYOUTS": [str(v) for v in vals],
        "WHAT_IT_MEANS": WHAT_A_VIOLATION_MEANS}


def complement_report(settlement_rows):
    """Run the check over a settlement corpus and report it honestly."""
    counts = Counter()
    violations, unreadable = [], Counter()
    for row in settlement_rows or ():
        status, detail = check_complement(row)
        counts[status] += 1
        if status == COMPLEMENT_OBSERVED_STATUS_VIOLATED:
            violations.append({"CONDITION_ID": row.get("condition_id"),
                               "MARKET_SLUG": row.get("market_slug"),
                               **{k: v for k, v in detail.items()
                                  if k != "WHAT_IT_MEANS"}})
        elif status == COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE:
            unreadable[detail.get("REASON", "UNKNOWN")] += 1
    checked = (counts[COMPLEMENT_OBSERVED_STATUS_VERIFIED]
               + counts[COMPLEMENT_OBSERVED_STATUS_VIOLATED])
    return {
        "ROWS": sum(counts.values()),
        "CHECKED": checked,
        "VERIFIED_IN_OBSERVED_STATE": counts[COMPLEMENT_OBSERVED_STATUS_VERIFIED],
        "VIOLATED_IN_OBSERVED_STATE": counts[COMPLEMENT_OBSERVED_STATUS_VIOLATED],
        "NOT_CHECKABLE": counts[COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE],
        "NOT_CHECKABLE_REASONS": dict(unreadable),
        "OBSERVED_STATE_PASS_RATE": (
            counts[COMPLEMENT_OBSERVED_STATUS_VERIFIED] / checked
            if checked else NOT_IDENTIFIED),
        "VIOLATIONS": violations[:50],
        "COMPLEMENT_GUARANTEE_STATUS": COMPLEMENT_GUARANTEE_STATUS,
        "WHY_NOT_VERIFIED": WHY_NOT_VERIFIED,
        "ONE_STATE_PER_MARKET": (
            "each settled market contributes exactly one terminal state, so "
            "this is evidence about realised states and not a proof about all "
            "of them"),
        "WHAT_A_VIOLATION_MEANS": WHAT_A_VIOLATION_MEANS,
    }
