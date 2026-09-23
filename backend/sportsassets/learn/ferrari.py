"""FERRARI: entry selection and pair completion, with the economics attached.

THE CASE STUDY. `ferrariChampions2026`, whale id 26, address
`0xfe787d2da716d60e8acff57fb87eb13cd4d10319`. The decomposition in
`research/beta48/learning/FERRARI.md` established, from 908,046 fills
over 35,147 conditions, that:

    pairing is genuinely profitable        +4.54% on $137.0M
    but 37.0% of completed pairs clear at or above parity -- they
                                           lock a loss, 7,349 of them
    and 45.4% of the capital is residual   $114.1M, zero sells

So "does it pair?" is the wrong question on its own. This module learns
TWO targets on the SAME decision rows, because the second is the one
the decomposition says actually matters:

    T_COMPLETE  a complementary BUY within H                (behaviour)
    T_CLEARS    a complementary BUY within H whose pair price
                (this entry's price + the completing fill's price)
                is STRICTLY BELOW 1.00                      (economics)

T_CLEARS ⊆ T_COMPLETE by construction, so a model that predicts
completion well and clearing badly is exactly the failure the
decomposition named, and it is now measurable instead of asserted.

────────────────────────────────────────────────────────────────────
THE PAIR PRICE IS A DECISION-LEVEL QUANTITY, NOT THE CONDITION VWAP.

The decomposition's pair price was `vwap(leg0) + vwap(leg1)` over a
whole condition -- the right measure for a book-level post-mortem, the
wrong one for a decision. A decision controls one fill. So here:

    pair_price = entry_price + price of the FIRST complementary BUY
                 inside the horizon

One YES plus one NO pays exactly $1.00 at settlement, so `pair_price <
1.00` is a gross gain per paired share and `>= 1.00` is a gross loss,
on both venues and with no merge call assumed. `bettor_merge.py` records
`RETAIL_NATIVE_MERGE_AVAILABLE = NO`; only the TIMING of the capital
return depends on the venue, not the arithmetic.

GROSS, AND SAYS SO. `trades` has no fees, no rebates and no incentive
payments. Every pair price here is before all of them, and the fee
model is `bettor_fees.py`'s job, not this module's.

────────────────────────────────────────────────────────────────────
SIZE IS OBSERVED AND DELIBERATELY NOT IN THE LABEL.

The completing fill is usually smaller than the entry -- Ferrari's mean
paired fraction is 0.2473 -- so "completed" rarely means "fully
paired". The label stays binary and honest about that: `paired_share`
is carried on the row as an OBSERVATION, never folded into the target.
A target weighted by fill size would be a different, unvalidated claim.

────────────────────────────────────────────────────────────────────
MISSING PRIOR INVENTORY, WHICH IS THE PART THAT IS EASY TO FAKE.

An entry that looks like a NEW position may be an ADD to a leg opened
before our window, and there is no way to tell from the window alone.
The extract therefore carries `prior_inventory`: Ferrari's per-leg
quantity in each sampled condition STRICTLY BEFORE the window. Three
consequences, all enforced below:

    1. a row whose condition shows PRIOR COMPLEMENT quantity is NOT a
       row. The position was already paired before we could see it, so
       "will it pair?" was answered outside our view. It is SKIPPED,
       counted, and named -- not scored as a negative.
    2. a row with prior SAME-LEG quantity is an ADD, not a new
       position, and is labelled as one.
    3. absence from `prior_inventory` means NO RECORD, not NO POSITION.
       `trades` begins 2026-03-31. Everything before that is invisible
       and no amount of care here recovers it; the census reports the
       share of rows exposed to it rather than assuming it away.

────────────────────────────────────────────────────────────────────
WHAT THIS MODULE DOES *NOT* DO, AND WHY.

IT DOES NOT FIT AN "ENTRY SELECTION" CLASSIFIER. That would need a
negative class: markets Ferrari could have entered at that instant and
did not. `trades` holds only what Ferrari DID. Constructing negatives
by sampling other markets would be inventing the opportunity set, and
the resulting number would measure the sampler. So component 1 is
delivered as a CENSUS of decision-time conditions plus the new/add
split, and the supervised part of entry quality is answered where the
data can answer it: conditional on a first leg being taken, does it
complete, and does it clear.

The named missing interface fact is therefore: a point-in-time record
of the markets live at each instant, which `bettor_*` books hold going
forward but not across Ferrari's history.
"""
from __future__ import annotations

import math

from . import dataset as D

VERSION = "BETTOR_LEARN_FERRARI_V1"

ACCOUNT = "ferrariChampions2026"
ADDRESS = "0xfe787d2da716d60e8acff57fb87eb13cd4d10319"

LABEL_COMPLETE = D.LABEL_COMPLEMENT
LABEL_CLEARS = "COMPLEMENT_FILL_WITHIN_H_AT_PAIR_PRICE_BELOW_PARITY"

# Entry classes. The third is the honest one.
ENTRY_NEW = "NEW_POSITION_AS_RECORDED"
ENTRY_ADD_IN_WINDOW = "ADD_TO_LEG_OPENED_INSIDE_THE_WINDOW"
ENTRY_ADD_PRE_WINDOW = "ADD_TO_LEG_OPENED_BEFORE_THE_WINDOW"

PARITY = 1.0

# The decision-time features. `dataset.FEATURES` unchanged, plus the
# three that only exist once prior inventory is known. They are all
# history at the decision instant, so none of them looks forward.
FEATURES = D.FEATURES + (
    "is_add_pre_window",
    "pre_window_qty_same_leg_log",
    "pre_window_fills_log",
)


def _prior_map(prior_inventory, conditions_list) -> dict:
    """`[condition_index, qty0_x100, qty1_x100, fills]` -> by condition_id.

    Returns {} for a missing array, which is NOT the same as "no prior
    inventory anywhere" -- it means the extract did not carry the
    question, and `build()` records that distinction rather than
    silently treating unknown as zero.
    """
    out = {}
    for e in (prior_inventory or ()):
        ci, q0, q1, n = e[0], e[1], e[2], e[3]
        out[conditions_list[int(ci)]] = {
            "qty": [float(q0) / 100.0, float(q1) / 100.0],
            "fills": int(n),
        }
    return out


def build(fills, *, horizon_s, observation_end, prior_inventory=None,
          conditions_list=None, prior_known=True, coverage_exclusions=(),
          elapsed_s=0.0) -> dict:
    """Ferrari entry rows carrying BOTH targets and the pair economics.

    `prior_known` states whether the pre-window inventory question was
    asked at all. When it is False the rows still build, but every
    `is_add_pre_window` is 0 and the result is stamped
    PRIOR_INVENTORY_NOT_QUERIED so no reader mistakes an unasked
    question for a negative answer.
    """
    if horizon_s <= 0:
        raise ValueError("horizon_s must be positive")
    if elapsed_s < 0 or elapsed_s >= horizon_s:
        raise ValueError("elapsed_s must be in [0, horizon_s)")

    prior = _prior_map(prior_inventory, conditions_list or []) \
        if prior_known else {}
    end = float(observation_end)
    excl = [(float(a), float(b)) for a, b in coverage_exclusions]

    rows = []
    for f in fills:
        r = dict(f)
        r["_ts"] = D._num(r.get("ts"))
        r["_avail"] = D.available_at(r)
        r["_mode"] = D.ingestion_mode(r)
        r["_oi"] = int(D._num(r.get("outcome_index"), -1))
        r["_side"] = (r.get("side") or "").strip().upper()
        rows.append(r)
    rows.sort(key=lambda r: (r["_avail"], r["_ts"]))

    by_key = {}
    for r in rows:
        by_key.setdefault(D._key(r), []).append(r)

    state = {}
    out = []
    skipped = {
        "non_buy": 0, "no_condition": 0, "unknown_outcome": 0,
        "in_excluded_window": 0, "horizon_overlaps_gap": 0,
        "completed_before_elapsed": 0,
        # THE ONE THAT MATTERS. A position already paired before the
        # window is not an open question, and scoring it as a negative
        # would invent a failure to pair out of a success we could not
        # see.
        "prior_complement_held_before_window": 0,
    }

    for r in rows:
        if r["_side"] != "BUY":
            skipped["non_buy"] += 1
            continue
        if not r.get("condition_id"):
            skipped["no_condition"] += 1
            continue
        if r["_oi"] not in (0, 1):
            skipped["unknown_outcome"] += 1
            continue

        k = D._key(r)
        st = state.setdefault(k, {"n": 0, "qty": [0.0, 0.0],
                                  "cost": [0.0, 0.0], "last_avail": None,
                                  "first_avail": None})
        t0 = r["_avail"]
        comp = 1 - r["_oi"]
        pre = prior.get(r.get("condition_id"))
        pre_same = pre["qty"][r["_oi"]] if pre else 0.0
        pre_comp = pre["qty"][comp] if pre else 0.0

        is_entry = st["qty"][comp] <= 0.0
        if is_entry and pre_comp > 0.0:
            skipped["prior_complement_held_before_window"] += 1
            is_entry = False

        if is_entry:
            in_gap = any(a <= t0 <= b for a, b in excl)
            horizon_end = t0 + horizon_s
            overlaps = any(not (b <= t0 or a >= horizon_end) for a, b in excl)
            if in_gap:
                skipped["in_excluded_window"] += 1
            elif overlaps:
                skipped["horizon_overlaps_gap"] += 1
            else:
                row = _row(r, st, by_key[k], horizon_s, end, elapsed_s,
                           pre_same, pre["fills"] if pre else 0)
                if row is not None:
                    out.append(row)
                else:
                    skipped["completed_before_elapsed"] += 1

        st["n"] += 1
        st["qty"][r["_oi"]] += D._num(r.get("size"))
        st["cost"][r["_oi"]] += D._num(r.get("size")) * D._num(r.get("price"))
        st["last_avail"] = t0
        if st["first_avail"] is None:
            st["first_avail"] = t0

    decided = [o for o in out if not o["censored"]]
    completed = [o for o in decided if o["label"] == 1]
    return {
        "version": VERSION,
        "account": ACCOUNT,
        "address": ADDRESS,
        "targets": {
            "primary": LABEL_COMPLETE,
            "economic": LABEL_CLEARS,
            "note": "T_CLEARS is a SUBSET of T_COMPLETE by construction. "
                    "A row that never completed cannot clear, and is a 0 "
                    "for both. A row that completed at a pair price of "
                    "1.00 or more is a 1 for completion and a 0 for "
                    "clearing -- that is the 37% the decomposition found.",
        },
        "parity": PARITY,
        "pair_price_definition":
            "entry price + the price of the FIRST complementary BUY inside "
            "the horizon. GROSS: trades carries no fees, rebates or "
            "incentive payments, so no net figure is claimed here.",
        "horizon_s": float(horizon_s),
        "elapsed_s": float(elapsed_s),
        "observation_end": end,
        "coverage_exclusions": [list(x) for x in excl],
        "prior_inventory_status": (
            "QUERIED" if prior_known else "PRIOR_INVENTORY_NOT_QUERIED"),
        "prior_inventory_conditions": len(prior),
        "left_censoring_note":
            "prior inventory is counted from the start of `trades` "
            "(2026-03-31) to the window start. Before 2026-03-31 there is "
            "no record at all, so a NEW_POSITION_AS_RECORDED row is new "
            "AS RECORDED and not proven new.",
        "lifecycle_observability": D.LIFECYCLE_OBSERVABILITY,
        "clock_note": (
            "AVAILABLE_AT = max(ts, detected_at), a conservative cutoff "
            "and not an availability guarantee. detected_at - ts is never "
            "used as a latency."),
        "rows": out,
        "n_rows": len(out),
        "n_censored": len(out) - len(decided),
        "n_decided": len(decided),
        "n_completed": len(completed),
        "n_cleared": sum(1 for o in decided if o["label_clears"] == 1),
        "base_rate_complete": (
            len(completed) / float(len(decided)) if decided else None),
        "base_rate_clears": (
            sum(1 for o in decided if o["label_clears"] == 1)
            / float(len(decided)) if decided else None),
        # THE DECOMPOSITION'S QUESTION, ON THESE ROWS: given that it
        # completed, did it clear?
        "clears_given_completed": (
            sum(1 for o in completed if o["label_clears"] == 1)
            / float(len(completed)) if completed else None),
        "entry_class_census": _census(out),
        "skipped": skipped,
    }


def _census(rows) -> dict:
    """Component 1's answer, as a census rather than a classifier."""
    c = {}
    for r in rows:
        c[r["entry_class"]] = c.get(r["entry_class"], 0) + 1
    n = float(len(rows)) or 1.0
    return {
        "counts": c,
        "shares": {k: round(v / n, 4) for k, v in c.items()},
        "why_not_a_classifier":
            "a NEW-vs-ADD classifier would be fitted on features that "
            "contain the answer -- is_first_fill_in_market IS the label -- "
            "and an ENTER-vs-SKIP classifier needs markets Ferrari passed "
            "over, which fills do not hold. The census reports what the "
            "records support.",
    }


def _row(r, st, siblings, horizon_s, end, elapsed_s, pre_same, pre_fills):
    """One Ferrari decision row, or None if it left the risk set early."""
    t0 = r["_avail"]
    comp = 1 - r["_oi"]
    entry_price = D._num(r.get("price"))
    entry_size = D._num(r.get("size"))

    hit_at, hit = None, None
    for s in siblings:
        if s is r or s["_side"] != "BUY" or s["_oi"] != comp:
            continue
        if t0 < s["_avail"] <= t0 + horizon_s:
            if hit_at is None or s["_avail"] < hit_at:
                hit_at, hit = s["_avail"], s

    if elapsed_s > 0.0 and hit_at is not None and hit_at <= t0 + elapsed_s:
        return None

    censored = (t0 + horizon_s) > end and hit_at is None
    label = 1 if hit_at is not None else 0

    pair_price = None
    paired_share = None
    if hit is not None:
        pair_price = entry_price + D._num(hit.get("price"))
        if entry_size > 0.0:
            paired_share = min(entry_size, D._num(hit.get("size"))) / entry_size

    # THE ECONOMIC LABEL. Completing at or above parity is a 0, and it
    # is a DECIDED 0 -- the horizon closed and we saw what it cost.
    label_clears = 1 if (pair_price is not None and pair_price < PARITY) else 0

    if pre_same > 0.0:
        entry_class = ENTRY_ADD_PRE_WINDOW
    elif st["n"] > 0:
        entry_class = ENTRY_ADD_IN_WINDOW
    else:
        entry_class = ENTRY_NEW

    feats = dict(D._features(r, st))
    feats["is_add_pre_window"] = 1.0 if pre_same > 0.0 else 0.0
    feats["pre_window_qty_same_leg_log"] = math.log1p(max(0.0, pre_same))
    feats["pre_window_fills_log"] = math.log1p(max(0.0, float(pre_fills)))

    window_from = t0 + elapsed_s
    return {
        "account": r.get("account") or ACCOUNT,
        "condition_id": r.get("condition_id"),
        "outcome_index": r["_oi"],
        "entry_at": t0,
        "decision_at": window_from,
        "feature_cutoff_at": t0,
        "elapsed_s": float(elapsed_s),
        "label_window": [window_from, t0 + horizon_s],
        "remaining_s": float(horizon_s - elapsed_s),
        "event_ts": r["_ts"],
        "ingestion_mode": r["_mode"],
        "source": r.get("source"),
        "entry_class": entry_class,
        "entry_price": entry_price,
        "entry_size": entry_size,
        "features": feats,
        "label": label,
        "label_name": LABEL_COMPLETE,
        "label_clears": label_clears,
        "label_clears_name": LABEL_CLEARS,
        "pair_price": pair_price,
        "pair_price_is_gross": True,
        "paired_share": paired_share,
        "time_to_event_s": (hit_at - t0) if hit_at is not None else None,
        "censored": censored,
        "horizon_s": float(horizon_s),
        "lifecycle_observability": D.LIFECYCLE_OBSERVABILITY,
        "group_key": r.get("condition_id"),
    }


def xy(rows, *, target="complete"):
    """Feature matrix and the chosen target.

    `target="clears"` scores the ECONOMIC question. Both targets use the
    identical feature vectors, so a difference between the two models is
    a difference in what is being predicted and not in what was seen.
    """
    if target == "complete":
        key = "label"
    elif target == "clears":
        key = "label_clears"
    else:
        raise ValueError("target must be 'complete' or 'clears'")
    return ([r["features"] for r in rows], [float(r[key]) for r in rows])


def pair_price_summary(rows) -> dict:
    """The economics of the completions, described rather than modelled.

    The kernel has no least-squares head -- `Ridge` is IRLS logistic --
    so the pair price magnitude is REPORTED as an order statistic and
    the supervised claim stays binary. Inventing a regression the
    cross-check cannot validate would be the wrong trade.
    """
    px = sorted(r["pair_price"] for r in rows
                if r["pair_price"] is not None and not r["censored"])
    if not px:
        return {"n": 0, "status": "NO_COMPLETIONS"}

    def q(p):
        if len(px) == 1:
            return px[0]
        i = p * (len(px) - 1)
        lo = int(math.floor(i))
        hi = min(lo + 1, len(px) - 1)
        return px[lo] + (px[hi] - px[lo]) * (i - lo)

    below = sum(1 for v in px if v < PARITY)
    shares = [r["paired_share"] for r in rows
              if r.get("paired_share") is not None and not r["censored"]]
    return {
        "n": len(px),
        "below_parity": below,
        "at_or_above_parity": len(px) - below,
        "share_below_parity": round(below / float(len(px)), 4),
        "p05": round(q(0.05), 4),
        "median": round(q(0.50), 4),
        "p95": round(q(0.95), 4),
        "mean_gross_edge_per_paired_share":
            round(sum(PARITY - v for v in px) / float(len(px)), 6),
        "mean_paired_share_of_entry":
            round(sum(shares) / float(len(shares)), 4) if shares else None,
        "gross_note":
            "before fees, rebates and incentives, none of which are in "
            "trades. A positive gross edge is not a positive net one.",
    }
