"""Section 9. MARKET_STATE_TOXICITY labels, with frozen sign conventions.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
    MARKET_STATE_TOXICITY_h  =  UNCONDITIONAL_ON_BETTOR_FILL

"Given this book state, and a HYPOTHETICAL resting quote, how adversely does
the executable price move over the next h seconds?"

It is NOT FILL_CONDITIONAL_TOXICITY. No BETTOR order has rested here, so
nothing conditions on being filled, and the relationship between the two is
NOT_IDENTIFIED in either direction (see toxicity_v1).

THE SIGN CONVENTION, FROZEN
---------------------------
Adverse means the market moved AGAINST the hypothetical quoter:

    BID quote  -> we would be LONG -> adverse is a FALL in value
    ASK quote  -> we would be SHORT -> adverse is a RISE in value

and toxicity is reported POSITIVE-IS-WORSE, so both sides are comparable on
one axis:

    TOXICITY_BID_h = -(VALUE_AT_T_PLUS_H - VALUE_AT_T)
    TOXICITY_ASK_h = +(VALUE_AT_T_PLUS_H - VALUE_AT_T)

Positive means the state was toxic for that side. Fixed here, before any
label is computed, and pinned by test.
"""

import datetime

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MISSING = "MISSING"

NOTHING_IS_TRAINED_HERE = True

LABEL_NAME = "MARKET_STATE_TOXICITY"
CONDITIONING = "UNCONDITIONAL_ON_BETTOR_FILL"
NOT_THIS = "FILL_CONDITIONAL_TOXICITY"
WHY_NOT_THIS = (
    "no BETTOR order has rested on this venue, so nothing conditions on "
    "being filled. Calling it fill-conditional would claim evidence that "
    "does not exist")

HORIZONS_S = (5, 30, 60, 300)
SIDES = ("BID", "ASK")

LABEL_FIELDS = tuple("MARKET_STATE_TOXICITY_%dS" % h for h in HORIZONS_S)

# --- The frozen sign convention. -------------------------------------------

SIGN_CONVENTION = "POSITIVE_IS_MORE_ADVERSE_FOR_THE_QUOTING_SIDE"
SIGN_RULES = {
    "BID": "TOXICITY = -(VALUE_T_PLUS_H - VALUE_T); a FALL is adverse "
           "because a filled bid leaves us long",
    "ASK": "TOXICITY = +(VALUE_T_PLUS_H - VALUE_T); a RISE is adverse "
           "because a filled ask leaves us short",
}
SIGN_FROZEN_BEFORE_ANY_LABEL = True
WHY_FREEZE_THE_SIGN = (
    "a sign fixed after seeing the labels is how 'toxic' and 'benign' get "
    "swapped to match whichever direction the data happened to run")

# --- Which price is "value". -----------------------------------------------

VALUE_BASES = ("EXECUTABLE", "MID")
DEFAULT_VALUE_BASIS = "EXECUTABLE"
WHY_EXECUTABLE = (
    "a maker does not exit at the mid. Measuring adverse movement against the "
    "mid flatters every result by roughly half a spread, which on a 2-cent "
    "market is the entire effect being looked for")

EXECUTABLE_EXIT_PRICE = {
    # Filled on the BID we are long, so we exit by SELLING into the bid.
    "BID": "BEST_BID",
    # Filled on the ASK we are short, so we exit by BUYING at the ask.
    "ASK": "BEST_ASK",
}


def _num(v):
    if v in (None, NOT_IDENTIFIED, MISSING, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def toxicity(side, value_t, value_t_plus_h):
    """The frozen formula. Positive = more adverse for `side`."""
    if side not in SIDES:
        return {"TOXICITY": MISSING, "REASON": "UNKNOWN_SIDE",
                "DECLARED": SIDES}
    a, b = _num(value_t), _num(value_t_plus_h)
    if a is None or b is None:
        return {"TOXICITY": MISSING, "REASON": "MISSING_VALUE",
                "SIGN_CONVENTION": SIGN_CONVENTION}
    delta = b - a
    tox = -delta if side == "BID" else delta
    return {"TOXICITY": round(tox, 10), "SIDE": side,
            "VALUE_DELTA": round(delta, 10),
            "SIGN_CONVENTION": SIGN_CONVENTION,
            "SIGN_RULE": SIGN_RULES[side],
            "MORE_ADVERSE": tox > 0}


def label_row(state_row, labels, side, basis=DEFAULT_VALUE_BASIS,
              horizons_s=HORIZONS_S):
    """Attach MARKET_STATE_TOXICITY_h for one hypothetical quote side."""
    if basis not in VALUE_BASES:
        return {"STATUS": "UNKNOWN_BASIS", "DECLARED": VALUE_BASES}
    exit_field = EXECUTABLE_EXIT_PRICE[side] if basis == "EXECUTABLE" else "MID"
    v0 = _num(state_row.get(exit_field))
    out = {"TOXICITY_SIDE": side, "VALUE_BASIS": basis,
           "VALUE_FIELD": exit_field,
           "LABEL_NAME": LABEL_NAME, "CONDITIONING": CONDITIONING,
           "NOT_THIS": NOT_THIS,
           "SIGN_CONVENTION": SIGN_CONVENTION,
           "WHY_EXECUTABLE": WHY_EXECUTABLE}
    for h in horizons_s:
        key = "MARKET_STATE_TOXICITY_%dS" % h
        v1 = _num(labels.get("%s_T_PLUS_%dS" % (exit_field, h)))
        if v0 is None or v1 is None:
            out[key] = MISSING
            continue
        out[key] = toxicity(side, v0, v1)["TOXICITY"]
    out["LABEL_STATUS"] = ("PRESENT" if all(
        out.get("MARKET_STATE_TOXICITY_%dS" % h) != MISSING
        for h in horizons_s) else MISSING)
    return out


def label_both_sides(state_row, labels, basis=DEFAULT_VALUE_BASIS,
                     horizons_s=HORIZONS_S):
    """Both hypothetical sides, kept separate. They are different questions."""
    return {s: label_row(state_row, labels, s, basis, horizons_s)
            for s in SIDES}


def summarise(rows, side="BID", horizon_s=30):
    """Mean toxicity across labelled rows, with the missing count kept."""
    key = "MARKET_STATE_TOXICITY_%dS" % horizon_s
    vals, missing = [], 0
    for r in rows or ():
        blk = r.get(side) if isinstance(r.get(side), dict) else r
        v = blk.get(key) if isinstance(blk, dict) else None
        if v in (None, MISSING):
            missing += 1
            continue
        vals.append(float(v))
    if not vals:
        return {"SIDE": side, "HORIZON_S": horizon_s,
                "MEAN_TOXICITY": NOT_IDENTIFIED, "LABELLED": 0,
                "MISSING": missing,
                "WHY": "no labelled row at this horizon"}
    mean = sum(vals) / len(vals)
    return {"SIDE": side, "HORIZON_S": horizon_s,
            "MEAN_TOXICITY": round(mean, 10),
            "LABELLED": len(vals), "MISSING": missing,
            "SIGN_CONVENTION": SIGN_CONVENTION,
            "THIS_IS_NOT_FILL_CONDITIONAL": True,
            "NOT_THIS": NOT_THIS, "WHY_NOT_THIS": WHY_NOT_THIS}


def describe():
    return {
        "LABEL_NAME": LABEL_NAME,
        "CONDITIONING": CONDITIONING,
        "NOT_THIS": NOT_THIS,
        "WHY_NOT_THIS": WHY_NOT_THIS,
        "HORIZONS_S": HORIZONS_S,
        "SIDES": SIDES,
        "SIGN_CONVENTION": SIGN_CONVENTION,
        "SIGN_RULES": dict(SIGN_RULES),
        "SIGN_FROZEN_BEFORE_ANY_LABEL": SIGN_FROZEN_BEFORE_ANY_LABEL,
        "WHY_FREEZE_THE_SIGN": WHY_FREEZE_THE_SIGN,
        "VALUE_BASES": VALUE_BASES,
        "DEFAULT_VALUE_BASIS": DEFAULT_VALUE_BASIS,
        "WHY_EXECUTABLE": WHY_EXECUTABLE,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
