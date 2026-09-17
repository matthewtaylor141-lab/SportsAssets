"""Sections 12 and 13. MICROSTRUCTURE_V1 and the continuous relative-value target.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

WHY THIS DOES NOT WAIT FOR SETTLEMENTS
--------------------------------------
Every settlement-based experiment in this programme has been starved of events:
47 common events against an incremental ladder needing thousands. Short-horizon
price movement does not have that problem. A ninety-minute capture at one
observation every four seconds across six markets yields thousands of
observations of MID_MOVE_30S, and each one is a real measurement rather than
one binary outcome per fixture.

That does not make them independent -- consecutive observations on one market
overlap heavily, and the event-clustered machinery still applies -- but it does
mean the microstructure question can be answered on one capture where the
settlement question needs a season.

THE TARGETS ARE PRICE MOVES, NOT OUTCOMES
-----------------------------------------
    MID_MOVE_5S / 30S / 60S / 300S
and, where the book supports it, the move in the EXECUTABLE price, which is the
one a passive order actually earns or loses against. Mid is easier and
executable is truer; both are emitted and neither is called the other.

WHAT THIS MODULE IS NOT
-----------------------
It is not P_FILL. Nothing here identifies whether a passive order would have
been filled, and no feature here may be used as a fill proxy (section 14). A
mid that moves through your level is evidence about the market, not about your
queue position.
"""

import math
from collections import defaultdict, deque

import bettor_dataset

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NO_CAPTURE_YET = "NO_SUBSTANTIVE_CAPTURE_HARVESTED_YET"

# --- Section 12. Targets and features. -------------------------------------

TARGET_HORIZONS_SECONDS = (5, 30, 60, 300)

TARGETS = tuple("MID_MOVE_%dS" % s for s in TARGET_HORIZONS_SECONDS) + \
    tuple("EXECUTABLE_MOVE_%dS" % s for s in TARGET_HORIZONS_SECONDS)

# --- The horizon observability rule, enforced HERE and not only declared. ---
#
# bettor_dataset declares which horizons the V1 capture can actually label.
# Declaring it there and building targets here without consulting it is how a
# forbidden repair survives a correction: this module used to take the FIRST
# tick at-or-after T+h with no tolerance, so on the ~24 s per-market grid every
# MID_MOVE_5S was literally the +24 s move wearing a 5 s name, and every
# MID_MOVE_30S the +48 s move. Both are named in
# bettor_dataset.FORBIDDEN_5S_REPAIRS. The import makes the declaration
# load-bearing instead of decorative.

from bettor_dataset import (                                  # noqa: E402
    HORIZON_STATUS_V1, HORIZON_TOLERANCE_S, FORBIDDEN_5S_REPAIRS,
    horizon_label_coverage_gate, TIE_BREAK_RULE,
    WHY_A_TIE_NEEDS_A_FROZEN_RULE)

# --- Market identity. Enforced, not requested. -----------------------------
#
# The docstring used to say "ticks must be ONE market's series" and nothing
# checked it. Fed an interleaved capture (A at T, B at T+24, A at T+48) the
# 30-second label for A resolved to B and reported a move of +0.30 that
# belonged to a different contract.

MARKET_IDENTITY_KEYS = ("MARKET_ID", "CONDITION_ID", "TOKEN_ID")

EVENT_ID_IS_NOT_A_MARKET_IDENTITY = (
    "one event carries many markets -- moneyline, totals, each side of each "
    "line -- so two rows sharing an EVENT_ID are not two observations of the "
    "same book. Identity must be at MARKET_ID / CONDITION_ID / TOKEN_ID "
    "level or the label is cross-contract")

BUILD_TARGETS_IDENTITY_POLICY = "GROUP_INTERNALLY_BY_MARKET_IDENTITY"

A_CALLER_ASSERTION_IS_NOT_AN_IDENTIFIER = (
    "IDENTITY_STATUS = CALLER_ASSERTED_SINGLE_MARKET let a series with no "
    "market identifier anywhere be labelled on the strength of the caller "
    "saying it was one market's. That assertion cannot be checked by anyone "
    "downstream, does not travel with the rows, and is the same fail-open "
    "that was closed in bettor_dataset. A scientific forward label requires "
    "the market to be named in the data")


def market_identity(tick):
    """The market key for a tick, or None if it carries no market identity."""
    for k in MARKET_IDENTITY_KEYS:
        v = (tick or {}).get(k)
        if v not in (None, "", NOT_IDENTIFIED):
            return (k, v)
    return None

A_DECLARATION_IN_ANOTHER_MODULE_IS_NOT_A_CONTROL = (
    "a rule written as a constant in the module that defines the dataset does "
    "nothing to the module that builds the targets. Until the target builder "
    "and the scorer both refuse, the rule is a comment")


def horizon_status(h):
    """OBSERVABLE, the declared refusal, or NOT_IDENTIFIED. Fails closed.

    A horizon nobody has declared is NOT_IDENTIFIED, and NOT_IDENTIFIED is
    not permission -- it is the absence of a finding about whether this
    capture can measure the horizon at all.
    """
    return HORIZON_STATUS_V1.get(h, NOT_IDENTIFIED)


def horizon_is_measurable(h):
    return horizon_status(h) == "OBSERVABLE"


def horizon_of_target(target):
    """The horizon a target name claims, or None. '_STATUS' is not a target."""
    import re
    m = re.match(r"^[A-Z_]+_(\d+)S$", str(target or ""))
    return int(m.group(1)) if m else None


LABEL_PROVENANCE_IS_REQUIRED = (
    "a target column with no accompanying _STATUS column has no provenance: "
    "nothing says the label came from the canonical builder, at the nearest "
    "observation within tolerance, on the same market. An unchecked label is "
    "not a checked one, and 'not checked therefore yes' is how a gate fails "
    "open")

A_STATUS_STRING_IS_NOT_A_PROVENANCE = (
    "requiring MID_MOVE_60S_STATUS = PRESENT on every row checks that "
    "somebody wrote the word PRESENT. A hand-built column passes it. Every "
    "scored row must carry a LABEL_ARTIFACT_SHA, and the scorer must call "
    "bettor_dataset.verify_label_artifact() on the artifact it names -- "
    "recomputing the seal and re-deriving the observation chain -- before "
    "MAY_SCORE can become True")

LABEL_ARTIFACT_KEY = "LABEL_ARTIFACT_SHA"
LABEL_ARTIFACT_OBJECT_KEY = "LABEL_ARTIFACT"


def _verified_artifact_shas(rows, artifacts=None):
    """Which LABEL_ARTIFACT_SHAs on these rows actually verify?

    `artifacts` maps LABEL_ARTIFACT_SHA -> the sealed artifact object. A row
    naming a SHA with no artifact to check is NOT verified: an unresolvable
    reference is a claim, not a proof.
    """
    by_sha = {}
    for a in (artifacts or {}).values() if isinstance(artifacts, dict) \
            else (artifacts or ()):
        sha = (a or {}).get(LABEL_ARTIFACT_KEY)
        if sha:
            by_sha[sha] = a
    if isinstance(artifacts, dict):
        for sha, a in artifacts.items():
            if isinstance(a, dict):
                by_sha.setdefault(sha, a)
    verified, failed, unresolved = set(), {}, set()
    for r in rows or ():
        sha = (r or {}).get(LABEL_ARTIFACT_KEY)
        if not sha:
            continue
        if sha in verified or sha in failed:
            continue
        art = by_sha.get(sha) or (r or {}).get(LABEL_ARTIFACT_OBJECT_KEY)
        if not isinstance(art, dict):
            unresolved.add(sha)
            continue
        v = bettor_dataset.verify_label_artifact(art)
        if v["LABEL_PROVENANCE_STATUS"] == "VALID" \
                and v.get("LABEL_ARTIFACT_SHA") == sha:
            verified.add(sha)
        else:
            failed[sha] = v["LABEL_PROVENANCE_STATUS"]
    return verified, failed, unresolved


def target_scoring_gate(target, rows=None, min_coverage_pct=None,
                        label_artifacts=None):
    """May any predictor be scored on this target? Fails closed.

    Two conditions, in order. First the DECLARED status of the horizon the
    target name claims -- a horizon this capture cannot label is refused no
    matter what the rows contain. Then, when rows are supplied, measured label
    coverage through the same gate the dataset module uses.
    """
    h = horizon_of_target(target)
    if h is None:
        return {"MAY_SCORE": False, "TARGET": target,
                "REASON": "TARGET_HORIZON_NOT_PARSEABLE",
                "WHY": ("a target whose horizon cannot be read cannot be "
                        "checked against the capture's observability, and an "
                        "unchecked target is not permission")}
    st = horizon_status(h)
    if st != "OBSERVABLE":
        return {"MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
                "HORIZON_STATUS": st,
                "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN",
                "REASON": ("HORIZON_UNOBSERVABLE" if st != NOT_IDENTIFIED
                           else "HORIZON_OBSERVABILITY_NOT_DECLARED"),
                "FORBIDDEN_REPAIRS": FORBIDDEN_5S_REPAIRS,
                "A_DECLARATION_IN_ANOTHER_MODULE_IS_NOT_A_CONTROL":
                    A_DECLARATION_IN_ANOTHER_MODULE_IS_NOT_A_CONTROL}
    # Label PROVENANCE is required. Rows carrying MID_MOVE_30S but no
    # MID_MOVE_30S_STATUS used to return MAY_SCORE = True with
    # COVERAGE_GATE = NOT_CHECKED_NO_LABEL_STATUS_ON_ROWS -- "not checked,
    # therefore yes", which is the shape of every fail-open gate.
    status_key = "%s_STATUS" % target
    rows = list(rows or ())
    if not rows:
        return {"MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
                "HORIZON_STATUS": st,
                "REASON": "NO_ROWS",
                "LABEL_PROVENANCE": "ABSENT"}
    without = [r for r in rows if r.get(status_key) is None]
    if without:
        return {
            "MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
            "HORIZON_STATUS": st,
            "REASON": "LABEL_PROVENANCE_ABSENT",
            "ROWS_WITHOUT_LABEL_STATUS": len(without),
            "ROWS": len(rows),
            "REQUIRED_FIELD": status_key,
            "LABEL_PROVENANCE_IS_REQUIRED": LABEL_PROVENANCE_IS_REQUIRED,
            "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"}

    # The status column exists. That is a string somebody wrote. Now check
    # the canonical artifact it claims to have come from.
    no_sha = [r for r in rows if not r.get(LABEL_ARTIFACT_KEY)]
    if no_sha:
        return {
            "MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
            "HORIZON_STATUS": st,
            "REASON": "LABEL_ARTIFACT_SHA_ABSENT",
            "ROWS_WITHOUT_LABEL_ARTIFACT_SHA": len(no_sha),
            "ROWS": len(rows),
            "REQUIRED_FIELD": LABEL_ARTIFACT_KEY,
            "A_STATUS_STRING_IS_NOT_A_PROVENANCE":
                A_STATUS_STRING_IS_NOT_A_PROVENANCE,
            "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"}
    verified, failed, unresolved = _verified_artifact_shas(
        rows, label_artifacts)
    unverified_rows = [r for r in rows
                       if r.get(LABEL_ARTIFACT_KEY) not in verified]
    if unverified_rows:
        return {
            "MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
            "HORIZON_STATUS": st,
            "REASON": "LABEL_ARTIFACT_NOT_VERIFIED",
            "ROWS_WITH_UNVERIFIED_ARTIFACT": len(unverified_rows),
            "ROWS": len(rows),
            "FAILED_ARTIFACTS": dict(failed),
            "UNRESOLVABLE_ARTIFACT_SHAS": tuple(sorted(unresolved)),
            "A_STATUS_STRING_IS_NOT_A_PROVENANCE":
                A_STATUS_STRING_IS_NOT_A_PROVENANCE,
            "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"}

    shaped = [{"LABEL_STATUS": {"%dS" % h: r.get(status_key)}} for r in rows]
    cov = horizon_label_coverage_gate(shaped, h, min_coverage_pct)
    # MAY_EVALUATE is True, False, or NOT_IDENTIFIED -- and NOT_IDENTIFIED
    # is a non-empty string, so a truthiness test would read "we do not
    # know" as "yes".
    if cov["MAY_EVALUATE"] is not True:
        return {"MAY_SCORE": False, "TARGET": target, "HORIZON_S": h,
                "HORIZON_STATUS": st, "COVERAGE_GATE": cov,
                "REASON": "INSUFFICIENT_LABEL_COVERAGE",
                "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"}
    return {"MAY_SCORE": True, "TARGET": target, "HORIZON_S": h,
            "HORIZON_STATUS": st, "COVERAGE_GATE": cov,
            "LABEL_PROVENANCE": "VERIFIED_CANONICAL_ARTIFACT",
            "VERIFIED_LABEL_ARTIFACT_SHAS": tuple(sorted(verified)),
            "A_STATUS_STRING_IS_NOT_A_PROVENANCE":
                A_STATUS_STRING_IS_NOT_A_PROVENANCE}


CANDIDATE_FEATURES = (
    "ORDER_BOOK_IMBALANCE",
    "RAW_OFI_SHARES",
    "ORDER_FLOW_IMBALANCE_RATIO",
    "MICROPRICE_MINUS_MID",
    "SPREAD",
    "TOUCH_DEPTH",
    "DEPTH_SLOPE",
    "TRADE_FLOW",
    "TRANSITION_FREQUENCY",
    "PRICE_IMPROVEMENT",
    "MOVE_THROUGH",
    "SHORT_HORIZON_VOLATILITY",
    "CROSS_MARKET_RESIDUAL",
)

EVALUATION_RULES = {
    "SPLIT": "CHRONOLOGICAL",
    "CLUSTERING": "EVENT_CLUSTERED",
    "WHY_BOTH": (
        "consecutive observations on one market overlap, so rows are not "
        "independent; and a random split would let the fit learn the session "
        "and call it skill"),
    "OVERLAPPING_TARGETS_ARE_DECLARED": (
        "MID_MOVE_300S windows overlap heavily at a 4-second sampling "
        "interval. The overlap is not removed -- it is declared, and the "
        "interval is event-clustered so it is not mistaken for independent "
        "evidence"),
}

# --- Feature units. A ratio and a share count are not the same quantity. ---
#
# ORDER_FLOW_IMBALANCE was sum(bid_size_delta - ask_size_delta) over a window:
# a SIGNED SHARE COUNT, unbounded, in shares. It sat in DIMENSIONLESS_BASELINES
# beside ORDER_BOOK_IMBALANCE -- a ratio in [-1, 1] -- and both were multiplied
# by ONE shared `baseline_scale` to produce a price move. One scale cannot
# convert both: the same number that turns a 0.3 ratio into a sensible move
# turns a 4,000-share flow into a move of 1,200 probability points.

FEATURE_UNITS = {
    "ORDER_BOOK_IMBALANCE": "DIMENSIONLESS_RATIO_MINUS_ONE_TO_ONE",
    "RAW_OFI_SHARES": "SIGNED_SHARES",
    "ORDER_FLOW_IMBALANCE_RATIO": "DIMENSIONLESS_RATIO_MINUS_ONE_TO_ONE",
    "MICROPRICE_MINUS_MID": "PROBABILITY_POINTS",
    "SPREAD": "PROBABILITY_POINTS",
    "TOUCH_DEPTH": "SHARES",
    "DEPTH_SLOPE": "SHARES_PER_PROBABILITY_POINT",
    "TRADE_FLOW": "SHARES",
    "TRANSITION_FREQUENCY": "DIMENSIONLESS_RATE",
    "PRICE_IMPROVEMENT": "PROBABILITY_POINTS",
    "MOVE_THROUGH": "PROBABILITY_POINTS",
    "SHORT_HORIZON_VOLATILITY": "PROBABILITY_POINTS",
    "CROSS_MARKET_RESIDUAL": "PROBABILITY_POINTS",
}

A_SHARE_COUNT_IS_NOT_A_RATIO = (
    "ORDER_FLOW_IMBALANCE was a signed share count carried in the "
    "dimensionless-baseline set and scaled by the same constant as a "
    "[-1, 1] book-imbalance ratio. It is split: RAW_OFI_SHARES keeps the "
    "share count with its own PROBABILITY_POINTS_PER_SHARE scale, and "
    "ORDER_FLOW_IMBALANCE_RATIO is the genuinely dimensionless normalisation "
    "signed_flow / total_absolute_flow. Neither borrows the other's scale")

OFI_RATIO_DEFINITION = (
    "ORDER_FLOW_IMBALANCE_RATIO = sum(delta) / sum(abs(delta)) over the same "
    "window, where delta is (bid_size change - ask_size change) per step. It "
    "is in [-1, 1] by construction and is None when the window had no "
    "movement at all -- a zero denominator is not a zero imbalance")

THIS_IS_NOT_P_FILL = True
NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY = (
    "a mid moving through a level is evidence about the market, not about "
    "whether BETTOR's passive order was ahead of it in the queue")


def _mid(bid, ask):
    if bid is None or ask is None:
        return None
    return 0.5 * (float(bid) + float(ask))


def _microprice(bid, ask, bid_size, ask_size):
    """Size-weighted touch price. Leans toward the side with less size."""
    if None in (bid, ask, bid_size, ask_size):
        return None
    b, a = float(bid_size), float(ask_size)
    if b + a <= 0:
        return None
    return (float(bid) * a + float(ask) * b) / (a + b)


def tick_features(tick, prev=None, flow=None):
    """Features from one book observation. Missing inputs give None.

    `prev` is the preceding tick for the same market; `flow` is a small window
    of recent ticks used for the rate-like features. Nothing is imputed: a
    feature whose inputs are absent is None, and a None is never a zero.
    """
    bid, ask = tick.get("BEST_BID"), tick.get("BEST_ASK")
    bs, as_ = tick.get("BID_SIZE"), tick.get("ASK_SIZE")
    mid = _mid(bid, ask)
    micro = _microprice(bid, ask, bs, as_)
    f = {k: None for k in CANDIDATE_FEATURES}
    if bs is not None and as_ is not None and (bs + as_) > 0:
        f["ORDER_BOOK_IMBALANCE"] = (float(bs) - float(as_)) / (float(bs) + float(as_))
    if micro is not None and mid is not None:
        f["MICROPRICE_MINUS_MID"] = micro - mid
    if bid is not None and ask is not None:
        f["SPREAD"] = float(ask) - float(bid)
    if bs is not None and as_ is not None:
        f["TOUCH_DEPTH"] = float(bs) + float(as_)
    levels = tick.get("LEVELS")
    if levels and len(levels) >= 2:
        try:
            d0, d1 = levels[0], levels[1]
            dp = abs(float(d1["PRICE"]) - float(d0["PRICE"]))
            if dp > 0:
                f["DEPTH_SLOPE"] = (float(d1["SIZE"]) - float(d0["SIZE"])) / dp
        except Exception:
            pass
    if prev:
        pm = _mid(prev.get("BEST_BID"), prev.get("BEST_ASK"))
        if mid is not None and pm is not None:
            f["MOVE_THROUGH"] = mid - pm
        pb, pa = prev.get("BEST_BID"), prev.get("BEST_ASK")
        if bid is not None and pb is not None and ask is not None and pa is not None:
            f["PRICE_IMPROVEMENT"] = (float(bid) - float(pb)) + (float(pa) - float(ask))
    if flow:
        mids = [_mid(t.get("BEST_BID"), t.get("BEST_ASK")) for t in flow]
        mids = [m for m in mids if m is not None]
        if len(mids) > 2:
            mu = sum(mids) / len(mids)
            f["SHORT_HORIZON_VOLATILITY"] = math.sqrt(
                sum((m - mu) ** 2 for m in mids) / (len(mids) - 1))
            changes = sum(1 for a, b in zip(mids, mids[1:]) if a != b)
            f["TRANSITION_FREQUENCY"] = changes / max(len(mids) - 1, 1)
        traded = [t.get("SHARES_TRADED") for t in flow
                  if t.get("SHARES_TRADED") is not None]
        if len(traded) > 1:
            f["TRADE_FLOW"] = float(traded[-1]) - float(traded[0])
        sizes = [(t.get("BID_SIZE"), t.get("ASK_SIZE")) for t in flow]
        deltas = [((b or 0) - (pb or 0)) - ((a or 0) - (pa or 0))
                  for (pb, pa), (b, a) in zip(sizes, sizes[1:])]
        if deltas:
            # The raw signed share count, named for what it is.
            f["RAW_OFI_SHARES"] = sum(deltas)
            denom = sum(abs(d) for d in deltas)
            # A window with no size movement has NO imbalance to report. A
            # zero denominator is not a zero ratio.
            f["ORDER_FLOW_IMBALANCE_RATIO"] = (sum(deltas) / denom
                                               if denom > 0 else None)
    f["_MID"] = mid
    f["_MICROPRICE"] = micro
    return f


def build_targets(ticks, horizons=TARGET_HORIZONS_SECONDS,
                  time_key="REQUEST_UTC", tolerance_s=HORIZON_TOLERANCE_S):
    """Attach forward price moves to each tick of ONE MARKET.

    Three rules, each load-bearing and each shared with
    bettor_dataset.forward_observation():

      - A horizon the capture cannot label is REFUSED, not computed at a
        substitute offset. On the V1 ~24 s grid the 5 s horizon is
        UNOBSERVABLE, and a number in MID_MOVE_5S would be the +24 s move.
      - NEAREST to T+h, not the first at-or-after it. First-at-or-after
        systematically overshoots on a grid: a 30 s horizon resolves to +48 s,
        which labels a longer horizon than the one being claimed.
      - WITHIN TOLERANCE or MISSING, with the REALISED OFFSET recorded so a
        reader can see how far from the nominal horizon each label actually
        sits.

    `ticks` must be ONE market's series. Mixing markets would label a row with
    another market's book.

    A tick whose horizon extends past the end of the capture gets None for that
    horizon -- truncating the capture would make the last observations look
    calm, which is a bias toward whatever the market was doing at the close.
    """
    import datetime

    def t(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    supplied = [x for x in ticks or () if t(x.get(time_key))]
    keys = {market_identity(x) for x in supplied}
    named = {k for k in keys if k is not None}
    if None in keys and named:
        # CHECKED BEFORE GROUPING. The mixed-identity refusal used to sit
        # AFTER the grouping branch, so a series with two named markets and
        # one unidentified row took the grouping path -- which quietly DROPPED
        # the unidentified row from every part and returned a clean result.
        # The refusal that was supposed to catch it never ran.
        return [], {"STATUS": "REFUSED_MIXED_IDENTITY",
                    "TICKS": 0,
                    "UNIDENTIFIED_TICKS": sum(
                        1 for x in supplied if market_identity(x) is None),
                    "NAMED_MARKETS": sorted(str(k) for k in named),
                    "WHY": ("some ticks carry a market identity and some do "
                            "not; they cannot be proven to be one market's "
                            "series, and dropping the unidentified rows would "
                            "silently discard data"),
                    "MARKET_IDENTITY_KEYS": MARKET_IDENTITY_KEYS,
                    "EVENT_ID_IS_NOT_A_MARKET_IDENTITY":
                        EVENT_ID_IS_NOT_A_MARKET_IDENTITY}
    if len(named) > 1:
        # Group internally rather than trust the caller. Each market's series
        # is built on its own and the results are concatenated.
        out_all, meta_all = [], {}
        for key in sorted(named, key=lambda kv: (kv[0], str(kv[1]))):
            part = [x for x in supplied if market_identity(x) == key]
            rows_p, meta_p = build_targets(part, horizons, time_key,
                                           tolerance_s)
            out_all.extend(rows_p)
            meta_all[str(key)] = meta_p
        return out_all, {
            "GROUPED_BY_MARKET_IDENTITY": True,
            "MARKETS": sorted(str(k) for k in named),
            "PER_MARKET": meta_all,
            "BUILD_TARGETS_IDENTITY_POLICY": BUILD_TARGETS_IDENTITY_POLICY,
            "EVENT_ID_IS_NOT_A_MARKET_IDENTITY":
                EVENT_ID_IS_NOT_A_MARKET_IDENTITY,
            "TICKS": len(out_all)}
    if supplied and not named:
        # Every row lacks MARKET_ID / CONDITION_ID / TOKEN_ID. The previous
        # build labelled them anyway under IDENTITY_STATUS =
        # CALLER_ASSERTED_SINGLE_MARKET. A caller's assertion is not an
        # identifier: it cannot be checked, it does not travel with the rows,
        # and it is exactly the fail-open that bettor_dataset._same_subject
        # was closed against. Scientific target construction requires the
        # market to be named in the data.
        return [], {"STATUS": "REFUSED_NO_MARKET_IDENTITY",
                    "TICKS": 0,
                    "ROWS_SUPPLIED": len(supplied),
                    "MARKET_IDENTITY_KEYS": MARKET_IDENTITY_KEYS,
                    "WHY": ("no row carries a market-level identifier, so "
                            "nothing establishes that these observations are "
                            "one market's book"),
                    "A_CALLER_ASSERTION_IS_NOT_AN_IDENTIFIER":
                        A_CALLER_ASSERTION_IS_NOT_AN_IDENTIFIER,
                    "EVENT_ID_IS_NOT_A_MARKET_IDENTITY":
                        EVENT_ID_IS_NOT_A_MARKET_IDENTITY}
    identity_status = "MARKET_IDENTITY_ESTABLISHED"

    rows = sorted(supplied, key=lambda x: t(x[time_key]))
    stamps = [t(r[time_key]) for r in rows]
    mids = [_mid(r.get("BEST_BID"), r.get("BEST_ASK")) for r in rows]
    out = []
    truncated = defaultdict(int)
    out_of_tolerance = defaultdict(int)
    refused = {}
    for h in horizons:
        st = horizon_status(h)
        if st != "OBSERVABLE":
            refused["MID_MOVE_%dS" % h] = st
    for i, r in enumerate(rows):
        row = dict(r)
        for h in horizons:
            key = "MID_MOVE_%dS" % h
            row[key] = None
            row[key + "_STATUS"] = None
            row[key + "_REALISED_OFFSET_S"] = None
            if key in refused:
                # Never a number. A number here would be the +24 s move
                # wearing a 5 s name, and nothing downstream could tell.
                row[key + "_STATUS"] = refused[key]
                continue
            target = stamps[i] + datetime.timedelta(seconds=h)
            # NEAREST to the target, STRICTLY after the origin, WITHIN
            # tolerance -- the same three rules bettor_dataset uses. The old
            # first-at-or-after rule systematically overshot on a grid.
            best, best_key, best_off = None, None, None
            for k in range(i + 1, len(rows)):
                off = (stamps[k] - target).total_seconds()
                gap = abs(off)
                if gap > tolerance_s:
                    if stamps[k] > target and best is None:
                        break          # sorted: everything later is worse
                    continue
                # Same FROZEN TIE RULE as bettor_dataset.forward_observation:
                # (ABS_TARGET_ERROR, OBSERVATION_TIMESTAMP). Named
                # tie_key, NOT key -- `key` is the label column name.
                tie_key = (gap, stamps[k])
                if best_key is None or tie_key < best_key:
                    best, best_key, best_off = k, tie_key, off
            if best is None:
                if stamps[-1] < target:
                    truncated[key] += 1
                    row[key + "_STATUS"] = "TRUNCATED_AT_CAPTURE_END"
                else:
                    out_of_tolerance[key] += 1
                    row[key + "_STATUS"] = "NO_OBSERVATION_WITHIN_TOLERANCE"
                continue
            if mids[i] is not None and mids[best] is not None:
                row[key] = mids[best] - mids[i]
                row[key + "_STATUS"] = "PRESENT"
                row[key + "_REALISED_OFFSET_S"] = round(best_off, 3)
            else:
                row[key + "_STATUS"] = "MID_MISSING"
        out.append(row)
    return out, {
        "TICKS": len(out),
        "IDENTITY_STATUS": identity_status,
        "MARKET_IDENTITY_KEYS": MARKET_IDENTITY_KEYS,
        "TRUNCATED_AT_CAPTURE_END": dict(truncated),
        "TRUNCATION_IS_DECLARED_NOT_DROPPED": True,
        "OUT_OF_TOLERANCE": dict(out_of_tolerance),
        "TOLERANCE_S": tolerance_s,
        "REFUSED_HORIZONS": refused,
        "TIE_BREAK_RULE": TIE_BREAK_RULE,
        "WHY_A_TIE_NEEDS_A_FROZEN_RULE": WHY_A_TIE_NEEDS_A_FROZEN_RULE,
        "WHY_REFUSED": (
            "a horizon this capture cannot label is not computed at a "
            "substitute offset. See bettor_dataset.FORBIDDEN_5S_REPAIRS"
            if refused else None),
        "FORBIDDEN_REPAIRS": FORBIDDEN_5S_REPAIRS if refused else (),
        "A_DECLARATION_IN_ANOTHER_MODULE_IS_NOT_A_CONTROL":
            A_DECLARATION_IN_ANOTHER_MODULE_IS_NOT_A_CONTROL,
    }


# --- Section 13. The continuous relative-value target. ---------------------

RELATIVE_VALUE_RULE = (
    "fit the coherent event surface EXCLUDING the target contract, price the "
    "target from that surface, and take the residual. A surface fitted WITH "
    "the target explains the target with itself")

RELATIVE_VALUE_TARGETS = tuple("TARGET_PRICE_%dS" % s
                               for s in TARGET_HORIZONS_SECONDS)

SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET = (
    "the proper test of whether cross-market inconsistency is monetizable is "
    "whether the residual predicts the TARGET'S OWN PRICE over the next "
    "seconds and minutes -- not whether it predicts settlement months of "
    "fixtures later")


def surface_residual(target_price, surface_price_ex_target):
    """RESIDUAL_T = P_TARGET - P_SURFACE_EX_TARGET. Sign is the direction."""
    if target_price is None or surface_price_ex_target is None:
        return None
    return float(target_price) - float(surface_price_ex_target)


def relative_value_rows(observations, horizons=TARGET_HORIZONS_SECONDS):
    """Residual at T against the target's own later price.

    Each observation carries P_TARGET, P_SURFACE_EX_TARGET and TARGET_LATER
    (seconds -> price). A negative residual means the target is cheap against
    its own event surface; if the mechanism is real, the later price rises.
    """
    out, missing = [], defaultdict(int)
    for o in observations or ():
        res = surface_residual(o.get("P_TARGET"),
                               o.get("P_SURFACE_EX_TARGET"))
        if res is None:
            missing["NO_RESIDUAL"] += 1
            continue
        row = {"EVENT_KEY": o.get("EVENT_KEY"),
               "MARKET": o.get("MARKET"), "T": o.get("T"),
               "RESIDUAL_T": res, "P_TARGET": o.get("P_TARGET")}
        # The canonical label artifact travels with the row, or the scorer
        # has nothing to verify and the target cannot be scored.
        if o.get(LABEL_ARTIFACT_KEY):
            row[LABEL_ARTIFACT_KEY] = o[LABEL_ARTIFACT_KEY]
        any_h = False
        for h in horizons:
            p1 = (o.get("TARGET_LATER") or {}).get(h)
            if p1 is None:
                missing["NO_TARGET_AT_%dS" % h] += 1
                continue
            row["TARGET_CHANGE_%dS" % h] = float(p1) - float(o["P_TARGET"])
            # Provenance travels with the label or the label cannot be used.
            row["TARGET_CHANGE_%dS_STATUS" % h] = (
                o.get("TARGET_LATER_STATUS", {}).get(h)
                or "PRESENT_PROVENANCE_NOT_ESTABLISHED")
            any_h = True
        if any_h:
            out.append(row)
    return out, dict(missing)


RELATIVE_VALUE_PROVENANCE_RULE = (
    "no model may report MEASURED from a forward target whose provenance is "
    "not established. The residual is only as good as the price change it is "
    "correlated against, and a hand-built TARGET_CHANGE column establishes "
    "nothing about nearest-within-tolerance, market identity or the realised "
    "offset")


def relative_value_test(rows, horizons=TARGET_HORIZONS_SECONDS,
                        min_coverage_pct=None, label_artifacts=None):
    """Does the residual predict the target's own move? Event-clustered.

    A NEGATIVE correlation is the tradeable one: a target priced above its
    surface should fall back toward it.
    """
    import random
    if not rows:
        return {"STATUS": NO_CAPTURE_YET}
    out = {}
    for h in horizons:
        st = horizon_status(h)
        if st != "OBSERVABLE":
            out["%dS" % h] = {
                "STATUS": "REFUSED", "HORIZON_STATUS": st,
                "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN",
                "WHY": ("publishing a correlation at this horizon would be "
                        "scoring a challenger the capture cannot label")}
            continue
        key = "TARGET_CHANGE_%dS" % h
        # The forward target must come from the canonical label builder.
        # Hand-constructed TARGET_CHANGE columns used to reach STATUS =
        # MEASURED with nothing establishing nearest-within-tolerance,
        # same-market identity or an approved realised offset.
        prov = target_scoring_gate(key, rows, min_coverage_pct,
                                   label_artifacts=label_artifacts)
        if not prov["MAY_SCORE"]:
            out["%dS" % h] = {
                "STATUS": "REFUSED",
                "REASON": prov.get("REASON"),
                "PROVENANCE_GATE": prov,
                "RELATIVE_VALUE_PROVENANCE_RULE":
                    RELATIVE_VALUE_PROVENANCE_RULE}
            continue
        by = defaultdict(list)
        for r in rows:
            if r.get(key) is not None:
                by[r.get("EVENT_KEY")].append((r["RESIDUAL_T"], r[key]))
        pairs = [p for v in by.values() for p in v]
        if len(pairs) < 8 or len(by) < 3:
            out["%dS" % h] = {"STATUS": "TOO_FEW", "N": len(pairs),
                              "EVENTS": len(by)}
            continue

        def corr(ps):
            xs = [a for a, _ in ps]
            ys = [b for _, b in ps]
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
            dy = math.sqrt(sum((b - my) ** 2 for b in ys))
            return (num / (dx * dy)) if dx > 0 and dy > 0 else None

        evs = sorted(by)
        rnd = random.Random(20260917)
        boot = []
        for _ in range(1000):
            samp = []
            for _ in range(len(evs)):
                samp += by[evs[rnd.randrange(len(evs))]]
            c = corr(samp)
            if c is not None:
                boot.append(c)
        boot.sort()
        out["%dS" % h] = {
            "STATUS": "MEASURED",
            "N_OBSERVATIONS": len(pairs),
            "N_EVENTS": len(evs),
            "CORRELATION": corr(pairs),
            "CI95_EVENT_BOOTSTRAP": ((boot[int(0.025 * len(boot))],
                                      boot[int(0.975 * len(boot)) - 1])
                                     if boot else None),
            "NEGATIVE_MEANS_THE_RESIDUAL_REVERTS": True,
        }
    # The PARENT status is DERIVED from the children. It used to be the
    # literal "MEASURED" regardless: a run in which every horizon was REFUSED
    # or TOO_FEW still returned STATUS = MEASURED at the top, and a caller
    # reading only the top level saw a measurement that did not happen.
    child = [v.get("STATUS") for v in out.values()]
    measured = [s for s in child if s == "MEASURED"]
    if not child:
        parent = "NO_HORIZON_EVALUATED"
    elif not measured:
        parent = "NOT_MEASURED"
    elif len(measured) == len(child):
        parent = "MEASURED"
    else:
        parent = "PARTIALLY_MEASURED"
    return {"STATUS": parent,
            "PARENT_STATUS_IS_DERIVED_FROM_CHILDREN": (
                "a parent that says MEASURED while every child says REFUSED "
                "reports a measurement nobody made. MEASURED requires every "
                "evaluated horizon to have been measured"),
            "HORIZON_STATUS_COUNTS": {s: child.count(s)
                                      for s in sorted(set(child))},
            "MEASURED_HORIZONS": tuple(
                k for k, v in sorted(out.items())
                if v.get("STATUS") == "MEASURED"),
            "UNMEASURED_HORIZONS": tuple(
                k for k, v in sorted(out.items())
                if v.get("STATUS") != "MEASURED"),
            "BY_HORIZON": out,
            "RULE": RELATIVE_VALUE_RULE,
            "NOTE": SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET}


def describe():
    return {
        "TARGET_HORIZONS_SECONDS": TARGET_HORIZONS_SECONDS,
        "TARGETS": TARGETS,
        "CANDIDATE_FEATURES": CANDIDATE_FEATURES,
        "EVALUATION_RULES": dict(EVALUATION_RULES),
        "RELATIVE_VALUE_RULE": RELATIVE_VALUE_RULE,
        "RELATIVE_VALUE_TARGETS": RELATIVE_VALUE_TARGETS,
        "SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET":
            SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET,
        "THIS_IS_NOT_P_FILL": THIS_IS_NOT_P_FILL,
        "NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY":
            NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY,
        "STATUS": NO_CAPTURE_YET,
    }


# ===========================================================================
# Section 8. BASELINES. A complex model earns admission only by beating these.
#
# The failure mode this prevents is familiar: a gradient-booster on twelve
# features that looks impressive until someone checks it against "assume the
# price does not move", which on a 5-second horizon in a quiet book is very
# hard to beat. Every baseline here is one line of arithmetic, and any model
# that cannot beat all of them chronologically out of sample has not earned
# its complexity.
# ===========================================================================

BASELINES = ("B0_NO_CHANGE", "B1_CURRENT_MID", "B2_MICROPRICE",
             "B3_LAST_MOVE_DIRECTION", "B4_SIMPLE_BOOK_IMBALANCE",
             "B5_SIMPLE_ORDER_FLOW_IMBALANCE")

BASELINE_SEMANTICS = {
    "B0_NO_CHANGE": "predict zero move. The hardest one to beat at 5s",
    "B1_CURRENT_MID": "predict the mid stays where it is (equivalent to B0 for "
                      "a move target; kept separate for level targets)",
    "B2_MICROPRICE": "predict the move toward microprice minus mid",
    "B3_LAST_MOVE_DIRECTION": "momentum: predict the previous move repeats",
    "B4_SIMPLE_BOOK_IMBALANCE": "predict a move proportional to touch imbalance",
    "B5_SIMPLE_ORDER_FLOW_IMBALANCE": "predict a move proportional to order-flow "
                                      "imbalance over the recent window",
}

A_COMPLEX_MODEL_EARNS_ADMISSION_ONLY_BY_BEATING_ALL_OF_THESE = True
CHRONOLOGICALLY_OUT_OF_SAMPLE = True


DIMENSIONLESS_BASELINES = ("B4_SIMPLE_BOOK_IMBALANCE",
                           "B5_SIMPLE_ORDER_FLOW_IMBALANCE")

# Each scaled baseline names ITS OWN input feature and ITS OWN scale unit.
# B5 reads a SHARE COUNT, so its scale is probability points PER SHARE; B4
# reads a ratio, so its scale is probability points per unit of ratio. They
# are different quantities and cannot share a constant.
SCALED_BASELINE_INPUTS = {
    "B4_SIMPLE_BOOK_IMBALANCE": ("ORDER_BOOK_IMBALANCE",
                                 "PROBABILITY_POINTS_PER_UNIT_RATIO"),
    "B5_SIMPLE_ORDER_FLOW_IMBALANCE": ("RAW_OFI_SHARES",
                                       "PROBABILITY_POINTS_PER_SHARE"),
}

A_SHARED_SCALE_ACROSS_DIFFERENT_UNITS = (
    "one `baseline_scale` used to feed both B4 and B5. B4's input is a "
    "[-1, 1] ratio and B5's is an unbounded signed share count, so the "
    "single constant was a unit conversion for at most one of them and "
    "nonsense for the other. Each scaled baseline now takes its own scale "
    "and its own declared source, and a baseline without one abstains")

BASELINE_UNIT_CONTRACT = (
    "book imbalance is a DIMENSIONLESS ratio in [-1, 1]; raw order-flow "
    "imbalance is a SIGNED SHARE COUNT. A price move is in probability "
    "points. scale=1.0 asserts that one unit of the input equals one full "
    "probability point, which is not a fact about anything -- it is an "
    "arbitrary choice that decides whether the baseline looks strong or "
    "weak. Each transformation must be either CALIBRATED ON TRAINING EVENTS "
    "ONLY or fixed before the experiment and declared, per baseline")

BASELINE_SCALE_SOURCES = ("CALIBRATED_ON_TRAINING_EVENTS",
                          "PREDECLARED_FIXED_TRANSFORMATION")

NEVER_CALIBRATE_ON_THE_EVALUATION_FOLD = (
    "a scale fitted on the fold the baseline is scored against is not a "
    "baseline, it is a fitted model with one parameter, and it will beat an "
    "honest predictor for that reason alone")


def baseline_scale_for(name, scales=None, scale=None, scale_source=None):
    """(value, source) for ONE scaled baseline, or (None, None).

    `scales` maps a baseline name to (value, source). The legacy singular
    `scale`/`scale_source` pair is honoured for B4 ONLY -- it was declared
    against a [-1, 1] ratio, and reusing it on B5's share count would be the
    very dimensional error this split exists to remove.
    """
    entry = (scales or {}).get(name)
    if entry is not None:
        try:
            value, source = entry
        except (TypeError, ValueError):
            return None, None
        if value is None or source not in BASELINE_SCALE_SOURCES:
            return None, None
        return float(value), source
    if name == "B4_SIMPLE_BOOK_IMBALANCE" and scale is not None \
            and scale_source in BASELINE_SCALE_SOURCES:
        return float(scale), scale_source
    return None, None


def baseline_prediction(name, feats, prev_move=None, scale=None,
                        scale_source=None, scales=None):
    """One baseline's predicted move. None when its inputs are absent.

    A SCALED baseline returns None unless the caller supplies BOTH a scale
    and a declared, admissible source for THAT baseline. There is no default
    of 1.0 and no shared scale: either default silently asserted a unit
    conversion nobody measured.
    """
    if name in ("B0_NO_CHANGE", "B1_CURRENT_MID"):
        return 0.0
    if name == "B2_MICROPRICE":
        return feats.get("MICROPRICE_MINUS_MID")
    if name == "B3_LAST_MOVE_DIRECTION":
        return prev_move
    if name in SCALED_BASELINE_INPUTS:
        s, src = baseline_scale_for(name, scales, scale, scale_source)
        if s is None or src is None:
            return None                   # no unit contract, no prediction
        key = SCALED_BASELINE_INPUTS[name][0]
        v = feats.get(key)
        return None if v is None else s * v
    return None


CHALLENGER_ADMISSION_STATUSES = ("ADMITTED", "NOT_ADMITTED",
                                 "NOT_IDENTIFIED")

AN_EMPTY_COMMON_SUPPORT_IS_NOT_A_WIN = (
    "the fair comparison is the rows EVERY predictor could price. When a "
    "baseline abstains everywhere -- a scaled baseline with no declared "
    "scale, a feature absent from the capture -- that intersection is empty, "
    "and the challenger's own-support scores were still published beside a "
    "complete-looking baseline table. An empty or unrepresentative "
    "intersection makes admission NOT_IDENTIFIED, never ADMITTED")

BASELINE_SET_INCOMPLETE_BLOCKS_ADMISSION = (
    "A_COMPLEX_MODEL_EARNS_ADMISSION_ONLY_BY_BEATING_ALL_OF_THESE means ALL "
    "of them. A baseline that could not be scored has not been beaten, so "
    "the admission comparison is not available -- the model is not admitted "
    "by default because its rival was silent")


def score_baselines(rows, target, pred_key=None, baselines=BASELINES,
                    min_coverage_pct=None, baseline_scale=None,
                    baseline_scale_source=None, baseline_scales=None,
                    label_artifacts=None):
    """Score every baseline (and optionally a model) on one target.

    Returns absolute error and direction accuracy per predictor. A predictor
    whose inputs were missing on a row is scored on the rows it COULD price,
    and the count is reported so a thin predictor cannot look good by
    abstaining on the hard ones.
    """
    # SCORE_A_5_SECOND_CHALLENGER is a named forbidden repair. The refusal
    # lives here, at the scorer, because that is where a model would actually
    # acquire a number it could be judged on.
    gate = target_scoring_gate(target, rows, min_coverage_pct,
                               label_artifacts=label_artifacts)
    if not gate["MAY_SCORE"]:
        return {"STATUS": "REFUSED", "TARGET": target, "GATE": gate}

    out = {}
    names = list(baselines) + ([pred_key] if pred_key else [])
    rows = list(rows or ())

    def predict(name, r):
        if name == pred_key:
            return r.get(pred_key)
        return baseline_prediction(name, r, r.get("_PREV_MOVE"),
                                   scale=baseline_scale,
                                   scale_source=baseline_scale_source,
                                   scales=baseline_scales)

    # COMMON EVALUATION SUPPORT: the rows every predictor could price. A
    # predictor that abstains on the hard rows must not win on an easier
    # subset, so each one is scored twice -- on its own support and on the
    # intersection -- and the two are reported side by side.
    scorable = [r for r in rows if r.get(target) is not None]
    common = [r for r in scorable
              if all(predict(nm, r) is not None for nm in names)]

    def score(name, subset):
        errs, dirs, n = [], [], 0
        for r in subset:
            y, p = r.get(target), predict(name, r)
            if y is None or p is None:
                continue
            n += 1
            errs.append(abs(p - y))
            if y != 0:
                dirs.append(1.0 if (p > 0) == (y > 0) else 0.0)
        return {
            "N_SCORED": n,
            "MEAN_ABSOLUTE_ERROR": (sum(errs) / len(errs)) if errs else None,
            "DIRECTION_ACCURACY": (sum(dirs) / len(dirs)) if dirs else None,
            "DIRECTIONAL_ROWS": len(dirs),
        }

    for name in names:
        own = score(name, scorable)
        own["ON_COMMON_SUPPORT"] = score(name, common)
        own["ABSTAINED_ROWS"] = len(scorable) - own["N_SCORED"]
        out[name] = own

    # --- Admission fails closed. -----------------------------------------
    silent = tuple(nm for nm in baselines if out[nm]["N_SCORED"] == 0)
    baseline_set_complete = not silent
    scale_report = {}
    for nm in SCALED_BASELINE_INPUTS:
        s, src = baseline_scale_for(nm, baseline_scales, baseline_scale,
                                    baseline_scale_source)
        scale_report[nm] = {
            "INPUT_FEATURE": SCALED_BASELINE_INPUTS[nm][0],
            "SCALE_UNIT": SCALED_BASELINE_INPUTS[nm][1],
            "SCALE": s if s is not None else NOT_IDENTIFIED,
            "SCALE_SOURCE": src or NOT_IDENTIFIED,
        }
    if pred_key is None:
        admission = NOT_IDENTIFIED
        why = "no challenger supplied; this is a baseline table"
    elif not baseline_set_complete:
        admission = NOT_IDENTIFIED
        why = ("baseline(s) %s scored no rows, so they have not been beaten"
               % ", ".join(silent))
    elif not common:
        admission = NOT_IDENTIFIED
        why = ("the common evaluation support is empty; there is no set of "
               "rows on which every predictor was compared")
    else:
        me = out[pred_key]["ON_COMMON_SUPPORT"]["MEAN_ABSOLUTE_ERROR"]
        rivals = [out[nm]["ON_COMMON_SUPPORT"]["MEAN_ABSOLUTE_ERROR"]
                  for nm in baselines]
        if me is None or any(r is None for r in rivals):
            admission = NOT_IDENTIFIED
            why = "a predictor has no error on the common support"
        else:
            beats_all = all(me < r for r in rivals)
            admission = "ADMITTED" if beats_all else "NOT_ADMITTED"
            why = ("challenger MAE %.10g vs baselines %s on %d common rows"
                   % (me, [round(r, 10) for r in rivals], len(common)))

    return {"TARGET": target, "BY_PREDICTOR": out,
            "COMMON_EVALUATION_SUPPORT_ROWS": len(common),
            "SCORABLE_ROWS": len(scorable),
            "BASELINE_SET_COMPLETE": baseline_set_complete,
            "BASELINES_THAT_SCORED_NOTHING": silent,
            "CHALLENGER_ADMISSION_COMPARISON_STATUS": admission,
            "CHALLENGER_ADMISSION_STATUSES": CHALLENGER_ADMISSION_STATUSES,
            "WHY_ADMISSION": why,
            "AN_EMPTY_COMMON_SUPPORT_IS_NOT_A_WIN":
                AN_EMPTY_COMMON_SUPPORT_IS_NOT_A_WIN,
            "BASELINE_SET_INCOMPLETE_BLOCKS_ADMISSION":
                BASELINE_SET_INCOMPLETE_BLOCKS_ADMISSION,
            "BASELINE_SCALES": scale_report,
            "SCALED_BASELINE_INPUTS": SCALED_BASELINE_INPUTS,
            "A_SHARED_SCALE_ACROSS_DIFFERENT_UNITS":
                A_SHARED_SCALE_ACROSS_DIFFERENT_UNITS,
            "FEATURE_UNITS": FEATURE_UNITS,
            "COMMON_SUPPORT_IS_THE_FAIR_COMPARISON": (
                "MEAN_ABSOLUTE_ERROR is each predictor's own support; "
                "ON_COMMON_SUPPORT is the rows every predictor could price. "
                "Compare on the second, and read ABSTAINED_ROWS before "
                "believing the first"),
            "BASELINE_SCALE": (baseline_scale if baseline_scale is not None
                               else NOT_IDENTIFIED),
            "BASELINE_SCALE_SOURCE": (baseline_scale_source
                                      or NOT_IDENTIFIED),
            "BASELINE_UNIT_CONTRACT": BASELINE_UNIT_CONTRACT,
            "NEVER_CALIBRATE_ON_THE_EVALUATION_FOLD":
                NEVER_CALIBRATE_ON_THE_EVALUATION_FOLD,
            "A_MODEL_MUST_BEAT_ALL_BASELINES":
                A_COMPLEX_MODEL_EARNS_ADMISSION_ONLY_BY_BEATING_ALL_OF_THESE,
            "CHRONOLOGICALLY_OUT_OF_SAMPLE": CHRONOLOGICALLY_OUT_OF_SAMPLE}


# ===========================================================================
# Section 9. Economically meaningful targets.
#
# A correct prediction of a 0.2-cent move is not monetizable through a 2-cent
# spread. Direction accuracy alone will happily report a triumph in exactly
# that situation, so the executable comparison is carried beside it.
# ===========================================================================

ECONOMIC_MEASURES = ("EXPECTED_PRICE_CHANGE", "SIGNED_PRICE_CHANGE",
                     "ABSOLUTE_ERROR", "DIRECTION_ACCURACY",
                     "EXPECTED_EXECUTABLE_MOVE")

A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE = (
    "a 0.2-cent move predicted perfectly through a 2-cent spread earns "
    "nothing. Direction accuracy must always be reported beside the predicted "
    "move relative to the spread")


def economic_row(pred_move, feats):
    """Put a predicted move next to the spread it would have to cross."""
    spread = feats.get("SPREAD")
    out = {
        "PREDICTED_MOVE": pred_move,
        "SPREAD": spread,
        "BEST_BID": feats.get("_BEST_BID"),
        "BEST_ASK": feats.get("_BEST_ASK"),
        "MOVE_AS_FRACTION_OF_SPREAD": None,
        "EXCEEDS_HALF_SPREAD": None,
    }
    if pred_move is not None and spread and spread > 0:
        out["MOVE_AS_FRACTION_OF_SPREAD"] = abs(pred_move) / spread
        out["EXCEEDS_HALF_SPREAD"] = abs(pred_move) > 0.5 * spread
    return out


def economic_summary(rows, pred_key, target):
    """How much of the predicted movement is larger than the spread?"""
    n = big = 0
    fr = []
    for r in rows or ():
        p, s = r.get(pred_key), r.get("SPREAD")
        if p is None or not s or s <= 0:
            continue
        n += 1
        f = abs(p) / s
        fr.append(f)
        if abs(p) > 0.5 * s:
            big += 1
    fr.sort()
    return {
        "TARGET": target,
        "ROWS_WITH_A_SPREAD": n,
        "SHARE_PREDICTING_MORE_THAN_HALF_THE_SPREAD":
            (big / n) if n else None,
        "MOVE_OVER_SPREAD_MEDIAN": fr[len(fr) // 2] if fr else None,
        "MOVE_OVER_SPREAD_P90": fr[int(0.9 * len(fr))] if fr else None,
        "A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE":
            A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE,
    }


# ===========================================================================
# Section 10. Raw informational edge, quantified. NOT maker profit.
# ===========================================================================

MARKOUT_HORIZONS_SECONDS = (5, 30, 60, 300)

EXECUTION_MONETIZABILITY = "NOT_IDENTIFIED"
WHY_NOT_IDENTIFIED = (
    "a markout measures what the mid did after a hypothetical fill. Whether "
    "BETTOR would have BEEN filled is P_FILL, which requires BETTOR's own "
    "passive-order sample and does not exist. Favourable predicted midpoint "
    "movement is an informational edge, not a maker profit")

DO_NOT_CALL_THIS_MAKER_PROFIT = True


def markout(quote_price, side, mid_later, mid_now=None):
    """Signed markout of a hypothetical passive fill. Sign favours the maker.

    A BUY at 0.50 with the mid at 0.52 five seconds later is +0.02 for the
    maker; a SELL at the same level is -0.02.
    """
    if quote_price is None or mid_later is None:
        return None
    if side not in ("BUY", "SELL"):
        return None
    d = float(mid_later) - float(quote_price)
    return d if side == "BUY" else -d


def markout_table(fills, horizons=MARKOUT_HORIZONS_SECONDS):
    """Expected markout by horizon, with the monetizability caveat attached."""
    out = {}
    for h in horizons:
        vals = []
        for f in fills or ():
            m = markout(f.get("QUOTE_PRICE"), f.get("SIDE"),
                        (f.get("MID_LATER") or {}).get(h))
            if m is not None:
                vals.append(m)
        out["EXPECTED_MARKOUT_%dS" % h] = {
            "N": len(vals),
            "MEAN": (sum(vals) / len(vals)) if vals else None,
        }
    out["EXECUTION_MONETIZABILITY"] = EXECUTION_MONETIZABILITY
    out["WHY_NOT_IDENTIFIED"] = WHY_NOT_IDENTIFIED
    out["DO_NOT_CALL_THIS_MAKER_PROFIT"] = DO_NOT_CALL_THIS_MAKER_PROFIT
    return out


# ===========================================================================
# Section 11. Validation protocol. Event AND chronological block.
# ===========================================================================

VALIDATION_SPLIT = "EVENT_AND_CHRONOLOGICAL_BLOCK"
NEVER_RANDOMLY_SCATTER_ADJACENT_TIMESTAMPS = (
    "two ticks four seconds apart in the same game are almost the same "
    "observation. Splitting them across train and test lets the model memorise "
    "the session and report it as skill")

REQUIRED_ALONGSIDE_EVERY_RESULT = ("ROWS", "EVENTS", "EVENT_HOURS",
                                   "OBSERVATIONS_PER_EVENT")


def split_blocks(rows, n_blocks=4, event_key="EVENT_KEY",
                 time_key="REQUEST_UTC"):
    """Contiguous chronological blocks that never split an event."""
    import datetime

    def T(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    first = {}
    for r in rows or ():
        t = T(r.get(time_key))
        e = r.get(event_key)
        if t is None or e is None:
            continue
        if e not in first or t < first[e]:
            first[e] = t
    evs = sorted(first, key=lambda e: (first[e], str(e)))
    if not evs:
        return [], {"STATUS": "NO_EVENTS"}
    size = max(1, len(evs) // n_blocks)
    blocks = [set(evs[i:i + size]) for i in range(0, len(evs), size)]
    out = [[r for r in rows if r.get(event_key) in b] for b in blocks]
    return out, {"BLOCKS": len(out), "EVENTS": len(evs),
                 "SPLIT": VALIDATION_SPLIT,
                 "NO_EVENT_SPANS_TWO_BLOCKS": True}


def result_context(rows, event_key="EVENT_KEY", time_key="REQUEST_UTC"):
    """The four numbers that must accompany every microstructure result."""
    import datetime

    def T(x):
        s = str(x).replace("Z", "+00:00")
        try:
            d = datetime.datetime.fromisoformat(s)
        except Exception:
            return None
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)

    by = defaultdict(list)
    for r in rows or ():
        t = T(r.get(time_key))
        if t is not None and r.get(event_key) is not None:
            by[r[event_key]].append(t)
    hours = 0.0
    for v in by.values():
        if len(v) > 1:
            hours += (max(v) - min(v)).total_seconds() / 3600.0
    n = len(rows or ())
    return {
        "ROWS": n,
        "EVENTS": len(by),
        "EVENT_HOURS": hours,
        "OBSERVATIONS_PER_EVENT": (n / len(by)) if by else None,
        "REQUIRED_ALONGSIDE_EVERY_RESULT": REQUIRED_ALONGSIDE_EVERY_RESULT,
    }
