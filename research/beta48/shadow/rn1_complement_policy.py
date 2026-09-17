"""RN1_COMPLEMENT_POLICY_TRAINING_TABLE and the basis-above-one correlates.

Directive sections 6, 7 and 8.

READ THIS BEFORE USING ANYTHING BELOW
-------------------------------------
Every row this module produces is labelled

    PIPELINE_VALIDATION_SELECTED_SUBSET
    NOT_VALID_FOR_RN1_POPULATION_INFERENCE

and the label is not decoration. The complete-coverage subset those rows come
from is SELECTED, by a mechanism that is understood: completeness requires every
trade in a condition to have carried a probe, so the filter selects against
heavily traded conditions -- which are exactly the conditions where complement
behaviour concentrates. Measured standardised mean differences: u0_trade_count
-0.776, sides_seen -0.690, u2_fill_count -0.564, all LARGE.

So this table MAY be used to test that the code runs, to verify accounting
identities on real rows, to exercise the schema, and to describe the subset
itself. It MAY NOT be used to infer RN1's complement policy, to estimate
population pair profitability or loss-lock frequency, to fit production
whale-policy coefficients, or to claim any population-level relationship.

The table is built now anyway, because the machinery has to exist and be
correct before the full extraction arrives. When it does, the same code runs on
a population and the label comes off.

THE UNIT OF A ROW
-----------------
One row per COMPLEMENT ACQUISITION: a fill that increases the matched quantity
in its condition, i.e. a purchase of the side opposite to one already held.
That, and not the condition, is the decision being modelled. A condition-level
row would average together decisions made minutes and dollars apart.

THE AS-OF RULE
--------------
Every feature is computed from fills STRICTLY EARLIER than the decision fill,
plus the decision fill's own price and size, which the decision-maker plainly
knew. Nothing from later in the condition, nothing from settlement, nothing
from the outcome. `assert_as_of()` re-derives each row from a truncated fill
stream and fails if any feature moves -- the guard is a test, not a comment.

WHY THERE IS NO MOTIVE COLUMN
-----------------------------
Section 8 asks why RN1 accepts a basis above one. The honest answer is that a
fill stream cannot say why. It can say what co-occurred. So this module reports
ASSOCIATIONS between features and the basis-above-one indicator, and it names
no motive: not hedging, not stop-loss, not liquidity provision, not error. Each
of those is a story that fits, and the data does not choose between them.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from decimal import Decimal as D

import whale_pairs as WP

NOT_IDENTIFIED = "NOT_IDENTIFIED"

TABLE_NAME = "RN1_COMPLEMENT_POLICY_TRAINING_TABLE"
TABLE_SCOPE = WP.SUBSET_LABEL
TABLE_INFERENCE_LABEL = WP.SUBSET_INFERENCE_LABEL
ROW_UNIT = "ONE_COMPLEMENT_ACQUISITION_FILL"
ACCOUNTING_CONVENTION = WP.PRIMARY_FOR_COMPLEMENT_DECISIONS

MOTIVE_ASSIGNED = False
WHY_NO_MOTIVE = (
    "A fill stream records what was bought, when, and at what price. It does "
    "not record why. Hedging, a stop-loss rule, market-making inventory "
    "management, a mandate to stay flat before settlement and a plain mistake "
    "all produce the same rows. Naming one of them would be a story, not a "
    "finding."
)


def _d(x):
    try:
        return D(str(x))
    except Exception:                                          # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Section 6-7: the training table
# ---------------------------------------------------------------------------

FEATURES = {
    # identity
    "CONDITION_ID": "the binary market",
    "TRADE_ID": "the decision fill",
    "TS": "venue trade time of the decision -- the as-of clock",
    "LEG_BOUGHT": "which token this fill acquired",
    # the state the decision was made in, from strictly earlier fills
    "OPPOSITE_QTY_BEFORE": "shares of the other leg already held",
    "OPPOSITE_BASIS_BEFORE": "weighted average cost of the other leg, before",
    "SAME_QTY_BEFORE": "shares of this leg already held",
    "SAME_BASIS_BEFORE": "weighted average cost of this leg, before",
    "MATCHED_QTY_BEFORE": "min of the two legs, before",
    "UNMATCHED_OPPOSITE_BEFORE": "the other leg's shares not yet paired",
    "FILLS_BEFORE": "how many fills he had already made here",
    "SECONDS_SINCE_FIRST_FILL": "how long he had been in this market",
    "SECONDS_SINCE_PREVIOUS_FILL": "how recently he had last acted",
    # the decision itself
    "FILL_PRICE": "price paid on this fill",
    "FILL_SIZE": "shares bought on this fill",
    "NEWLY_MATCHED_QTY": "shares this fill paired that were not paired before",
    # the incremental economics of THIS decision
    "INCREMENTAL_PAIR_BASIS": "cost of the pair this fill completed, per share",
    "INCREMENTAL_LOCK_PER_SHARE": "1.00 minus that basis; negative is a loss",
    "INCREMENTAL_LOCKED_PNL": "lock per share times newly matched quantity",
}

TARGETS = {
    "Y_BASIS_ABOVE_ONE": "1 if this fill completed a pair costing more than "
                         "1.00 -- a deliberately locked loss",
    "Y_INCREMENTAL_LOCKED_PNL": "the signed dollars this fill locked",
}

ILLEGAL_AS_FEATURES = (
    "any fill later than the decision fill",
    "the condition's final matched quantity",
    "the condition's total or settled P&L",
    "the settlement outcome",
    "the condition's coverage status",
)

AS_OF_RULE = (
    "features use fills strictly earlier than the decision, plus the decision "
    "fill's own price and size; nothing later, nothing from settlement")


def _sorted_fills(fills):
    """Chronological, with a stable tiebreak so the order is reproducible."""
    return sorted(fills, key=lambda f: (str(f.get("ts") or ""),
                                        str(f.get("trade_id") or "")))


def _ts_seconds(a, b):
    """Seconds between two ISO timestamps, or None if either is unreadable."""
    import datetime
    def p(x):
        if not x:
            return None
        s = str(x).replace("Z", "+00:00")
        try:
            return datetime.datetime.fromisoformat(s)
        except ValueError:
            return None
    da, db = p(a), p(b)
    if da is None or db is None:
        return None
    return (da - db).total_seconds()


def build_rows(fills, condition_id=None):
    """Complement-acquisition rows for ONE condition's fill stream.

    Walks the stream once in time order, carrying only what was known at each
    point. A fill that does not increase matched quantity produces no row: it
    was an addition to a side, not a complement decision.
    """
    fills = _sorted_fills([f for f in (fills or ())
                           if WP._leg_key(f) is not None])
    if not fills:
        return []

    qty = defaultdict(lambda: D(0))
    cost = defaultdict(lambda: D(0))
    rows = []
    first_ts = fills[0].get("ts")
    prev_ts = None

    for i, f in enumerate(fills):
        leg = WP._leg_key(f)
        price, size = _d(f.get("price")), _d(f.get("size"))
        if price is None or size is None or size <= 0:
            prev_ts = f.get("ts")
            continue
        if str(f.get("side") or "BUY").upper() != "BUY":
            # The retained corpus is BUY-only. A SELL would need its own branch
            # and there is none, so it is skipped and counted by the caller
            # rather than silently folded into a buy.
            prev_ts = f.get("ts")
            continue

        legs = sorted(set(list(qty.keys()) + [leg]))
        other = [k for k in legs if k != leg]
        other = other[0] if len(other) == 1 else None

        opp_qty = qty[other] if other is not None else D(0)
        opp_cost = cost[other] if other is not None else D(0)
        same_qty, same_cost = qty[leg], cost[leg]
        matched_before = min(same_qty, opp_qty)

        same_after = same_qty + size
        matched_after = min(same_after, opp_qty)
        newly = matched_after - matched_before

        if newly > 0:
            # The incremental convention: the pair this fill completed costs
            # this fill's price plus the other leg's cost for the shares it
            # paired with. Weighted average of the other leg is the right
            # figure for those shares only because the corpus cannot say which
            # individual shares were used; FIFO is reported separately by
            # whale_pairs.robustness and is not the decision unit.
            opp_unit = (opp_cost / opp_qty) if opp_qty > 0 else None
            if opp_unit is None:
                basis = None
            else:
                basis = price + opp_unit
            row = {
                "CONDITION_ID": condition_id if condition_id is not None
                                else f.get("condition_id"),
                "TRADE_ID": f.get("trade_id") or f.get("id"),
                "TS": f.get("ts"),
                "LEG_BOUGHT": leg,
                "OPPOSITE_QTY_BEFORE": str(opp_qty),
                "OPPOSITE_BASIS_BEFORE": (str(opp_unit) if opp_unit is not None
                                          else NOT_IDENTIFIED),
                "SAME_QTY_BEFORE": str(same_qty),
                "SAME_BASIS_BEFORE": (str(same_cost / same_qty)
                                      if same_qty > 0 else NOT_IDENTIFIED),
                "MATCHED_QTY_BEFORE": str(matched_before),
                "UNMATCHED_OPPOSITE_BEFORE": str(max(D(0),
                                                     opp_qty - same_qty)),
                "FILLS_BEFORE": i,
                "SECONDS_SINCE_FIRST_FILL": _ts_seconds(f.get("ts"), first_ts),
                "SECONDS_SINCE_PREVIOUS_FILL": _ts_seconds(f.get("ts"),
                                                           prev_ts),
                "FILL_PRICE": str(price),
                "FILL_SIZE": str(size),
                "NEWLY_MATCHED_QTY": str(newly),
                "INCREMENTAL_PAIR_BASIS": (str(basis) if basis is not None
                                           else NOT_IDENTIFIED),
                "INCREMENTAL_LOCK_PER_SHARE": (str(D(1) - basis)
                                               if basis is not None
                                               else NOT_IDENTIFIED),
                "INCREMENTAL_LOCKED_PNL": (str((D(1) - basis) * newly)
                                           if basis is not None
                                           else NOT_IDENTIFIED),
                "Y_BASIS_ABOVE_ONE": (1 if (basis is not None and basis > D(1))
                                      else (0 if basis is not None
                                            else NOT_IDENTIFIED)),
                "Y_INCREMENTAL_LOCKED_PNL": (str((D(1) - basis) * newly)
                                             if basis is not None
                                             else NOT_IDENTIFIED),
                "SCOPE": TABLE_SCOPE,
                "INFERENCE_LABEL": TABLE_INFERENCE_LABEL,
                "ACCOUNTING_CONVENTION": ACCOUNTING_CONVENTION,
            }
            rows.append(row)

        qty[leg] = same_after
        cost[leg] = same_cost + price * size
        prev_ts = f.get("ts")

    return rows


def build_table(fills_by_condition, complete_conditions=None):
    """The whole table, plus an accounting of what went into it.

    `complete_conditions`, when given, restricts the build to conditions with
    full probe coverage -- which is the only set on which the cumulative walk
    is even arithmetically valid.
    """
    rows, skipped = [], Counter()
    for cond, fills in sorted(fills_by_condition.items(),
                              key=lambda kv: str(kv[0])):
        if cond is None:
            # A fill with no condition cannot be paired with anything, because
            # pairing is defined within a condition. Counted, not sorted into
            # some arbitrary bucket.
            skipped["NULL_CONDITION_ID"] += 1
            continue
        if complete_conditions is not None and cond not in complete_conditions:
            skipped["INCOMPLETE_COVERAGE"] += 1
            continue
        sells = sum(1 for f in fills
                    if str(f.get("side") or "BUY").upper() != "BUY")
        if sells:
            skipped["CONDITIONS_CONTAINING_SELLS"] += 1
        got = build_rows(fills, condition_id=cond)
        if not got:
            skipped["NO_COMPLEMENT_ACQUISITION"] += 1
        rows.extend(got)

    above = sum(1 for r in rows if r["Y_BASIS_ABOVE_ONE"] == 1)
    priced = sum(1 for r in rows if r["Y_BASIS_ABOVE_ONE"] != NOT_IDENTIFIED)
    return {
        "TABLE_NAME": TABLE_NAME,
        "SCOPE": TABLE_SCOPE,
        "INFERENCE_LABEL": TABLE_INFERENCE_LABEL,
        "ROW_UNIT": ROW_UNIT,
        "ROWS": rows,
        "ROW_COUNT": len(rows),
        "CONDITIONS_WITH_ROWS": len({r["CONDITION_ID"] for r in rows}),
        "BASIS_ABOVE_ONE_ROWS": above,
        "BASIS_ABOVE_ONE_RATE": (above / priced) if priced else NOT_IDENTIFIED,
        "SKIPPED": dict(skipped),
        "FEATURES": dict(FEATURES),
        "TARGETS": dict(TARGETS),
        "ILLEGAL_AS_FEATURES": list(ILLEGAL_AS_FEATURES),
        "AS_OF_RULE": AS_OF_RULE,
        "ACCOUNTING_CONVENTION": ACCOUNTING_CONVENTION,
        "MOTIVE_ASSIGNED": MOTIVE_ASSIGNED,
        "SELECTION_CONSTANTS": WP.selection_constants(),
    }


# ---------------------------------------------------------------------------
# The as-of guard
# ---------------------------------------------------------------------------

FEATURES_THAT_MUST_NOT_MOVE = tuple(
    k for k in FEATURES if k not in ("CONDITION_ID",)) + (
    "INCREMENTAL_PAIR_BASIS", "Y_BASIS_ABOVE_ONE")


def assert_as_of(fills, condition_id=None):
    """Rebuild each row from a stream truncated at its own decision.

    If any feature differs between the full-stream build and the truncated
    build, a later fill reached an earlier row and the table leaks. Returns a
    report; `LEAKS` empty is the pass condition.
    """
    full = build_rows(fills, condition_id)
    ordered = _sorted_fills([f for f in (fills or ())
                             if WP._leg_key(f) is not None])
    leaks = []
    for row in full:
        cut = []
        for f in ordered:
            cut.append(f)
            if (f.get("trade_id") or f.get("id")) == row["TRADE_ID"]:
                break
        again = build_rows(cut, condition_id)
        match = [r for r in again if r["TRADE_ID"] == row["TRADE_ID"]]
        if not match:
            leaks.append({"TRADE_ID": row["TRADE_ID"],
                          "REASON": "ROW_DISAPPEARS_WHEN_TRUNCATED"})
            continue
        for k in FEATURES_THAT_MUST_NOT_MOVE:
            if k in row and row[k] != match[0].get(k):
                leaks.append({"TRADE_ID": row["TRADE_ID"], "FEATURE": k,
                              "FULL": row[k], "TRUNCATED": match[0].get(k)})
    return {
        "ROWS_CHECKED": len(full),
        "LEAKS": leaks,
        "LEAK_COUNT": len(leaks),
        "AS_OF_HOLDS": not leaks,
        "AS_OF_RULE": AS_OF_RULE,
    }


# ---------------------------------------------------------------------------
# Section 8: what co-occurs with a basis above one
# ---------------------------------------------------------------------------

CORRELATE_FEATURES = (
    "OPPOSITE_QTY_BEFORE", "OPPOSITE_BASIS_BEFORE", "SAME_QTY_BEFORE",
    "MATCHED_QTY_BEFORE", "UNMATCHED_OPPOSITE_BEFORE", "FILLS_BEFORE",
    "SECONDS_SINCE_FIRST_FILL", "SECONDS_SINCE_PREVIOUS_FILL",
    "FILL_PRICE", "FILL_SIZE", "NEWLY_MATCHED_QTY",
)

# Some features are ARITHMETIC COMPONENTS of the target, not predictors of it.
# INCREMENTAL_PAIR_BASIS = FILL_PRICE + OPPOSITE_BASIS_BEFORE, and the target is
# that basis exceeding 1.00. So a large effect on either of those two is partly
# the identity restating itself, and reporting it as a finding would be
# circular. They are measured -- the sizes are informative about WHICH side of
# the sum moves -- but they are flagged, and they are excluded from
# LARGE_EFFECTS so that list contains only features that are not the target in
# disguise.
MECHANICAL_COMPONENTS_OF_THE_TARGET = ("FILL_PRICE", "OPPOSITE_BASIS_BEFORE")

ASSOCIATION_IS_NOT_CAUSE = (
    "These are differences in means between the decisions that locked a loss "
    "and those that did not. They describe co-occurrence within a selected "
    "subset. They do not identify a cause, they do not identify a motive, and "
    "because the subset is selected they do not describe RN1."
)


def _num(v):
    if v is None or v == NOT_IDENTIFIED:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def basis_above_one_correlates(rows, features=CORRELATE_FEATURES):
    """Standardised mean differences between locked-loss and other decisions.

    Effect sizes, not p-values: at tens of thousands of rows every difference
    is 'significant' and none of that says which differences matter.
    """
    out = {}
    for k in features:
        a = [x for x in (_num(r.get(k)) for r in rows
                         if r.get("Y_BASIS_ABOVE_ONE") == 1) if x is not None]
        b = [x for x in (_num(r.get(k)) for r in rows
                         if r.get("Y_BASIS_ABOVE_ONE") == 0) if x is not None]
        if len(a) < 2 or len(b) < 2:
            out[k] = {"STATUS": "TOO_FEW_ROWS", "ABOVE_N": len(a),
                      "NOT_ABOVE_N": len(b)}
            continue
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a) / len(a)
        vb = sum((x - mb) ** 2 for x in b) / len(b)
        pooled = math.sqrt((va + vb) / 2)
        d = (ma - mb) / pooled if pooled > 0 else 0.0
        out[k] = {
            "ABOVE_ONE_MEAN": ma, "NOT_ABOVE_ONE_MEAN": mb, "SMD": d,
            "ABOVE_N": len(a), "NOT_ABOVE_N": len(b),
            "EFFECT": ("NEGLIGIBLE" if abs(d) < 0.1 else
                       "SMALL" if abs(d) < 0.3 else
                       "MEDIUM" if abs(d) < 0.5 else "LARGE"),
            "MECHANICAL_COMPONENT_OF_TARGET":
                k in MECHANICAL_COMPONENTS_OF_THE_TARGET,
        }
    large = sorted(k for k, v in out.items()
                   if v.get("EFFECT") == "LARGE"
                   and not v.get("MECHANICAL_COMPONENT_OF_TARGET"))
    mechanical = {k: v for k, v in out.items()
                  if v.get("MECHANICAL_COMPONENT_OF_TARGET")}
    return {
        "FEATURES": out,
        "LARGE_EFFECTS": large,
        "MECHANICAL_COMPONENTS_OF_THE_TARGET": mechanical,
        "WHY_MECHANICAL_COMPONENTS_ARE_EXCLUDED": (
            "INCREMENTAL_PAIR_BASIS is FILL_PRICE plus OPPOSITE_BASIS_BEFORE, "
            "and the target is that sum exceeding 1.00. A large effect on "
            "either addend is the identity restating itself, so they are "
            "reported separately rather than counted as findings"),
        "MOTIVE_ASSIGNED": MOTIVE_ASSIGNED,
        "WHY_NO_MOTIVE": WHY_NO_MOTIVE,
        "ASSOCIATION_IS_NOT_CAUSE": ASSOCIATION_IS_NOT_CAUSE,
        "SCOPE": TABLE_SCOPE,
        "INFERENCE_LABEL": TABLE_INFERENCE_LABEL,
        "SELECTION_CONSTANTS": WP.selection_constants(),
    }
