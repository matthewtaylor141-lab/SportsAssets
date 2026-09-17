"""Sections 1, 2, 5, 6, 21. The permanent BETTOR proprietary dataset.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. This is a schema and the label machinery that fills
it. It contains no fitted parameter.

WHY THIS IS THE ASSET
---------------------
A model can be reproduced by anyone with the same paper. A record of what the
market actually looked like, at instants nobody else was watching, cannot. The
dataset is APPEND-ONLY for that reason: a row rewritten later is a row that no
longer describes the moment it claims to.

THE GRAIN
---------
One object per MARKET x DECISION_TIMESTAMP x CANDIDATE_ACTION. The candidate
action is part of the key because the same market state supports several
actions, and each carries its own economics.

WHAT IS NEVER FABRICATED
------------------------
Depth levels the venue did not publish are MISSING, not zero and not carried
forward. A label whose future observation does not exist is
LABEL_STATUS = MISSING. The temptation is always to fill a gap so the row
"works"; a filled gap is a fact we invented.
"""

import datetime
import hashlib
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MISSING = "MISSING"

NOTHING_IS_TRAINED_HERE = True
APPEND_ONLY = True
WHY_APPEND_ONLY = (
    "a row rewritten later no longer describes the moment it claims to. "
    "Corrections are appended as new versions with their own timestamps, "
    "never edited in place")

SCHEMA_VERSION = "1"
GRAIN = "MARKET x DECISION_TIMESTAMP x CANDIDATE_ACTION"


# --- Section 1. Identifiers. -----------------------------------------------

IDENTIFIERS = (
    "DECISION_ID", "EVENT_ID", "MARKET_ID", "CONDITION_ID",
    "SPORT", "LEAGUE", "MARKET_FAMILY", "OUTCOME", "VENUE",
    "DECISION_TIMESTAMP_UTC", "CANDIDATE_ACTION",
)

# --- Section 2. The complete native market state. --------------------------

NATIVE_STATE_FIELDS = (
    "BEST_BID", "BEST_BID_SIZE", "BEST_ASK", "BEST_ASK_SIZE",
    "SPREAD", "MID", "MICROPRICE",
    "DEPTH_L1", "DEPTH_L2", "DEPTH_L3", "DEPTH_L5",
    "BOOK_IMBALANCE_L1", "BOOK_IMBALANCE_MULTI_LEVEL",
    "LAST_TRADE_PRICE", "LAST_TRADE_SIZE",
    "RECENT_BUY_FLOW", "RECENT_SELL_FLOW",
    "L1_OFI", "MULTI_LEVEL_OFI",
    "BOOK_VERSION", "BOOK_CONTENT_HASH",
    "MESSAGE_RECEIPT_TIMESTAMP", "VENUE_STATE_TIMESTAMP",
    "LAST_MATERIAL_CONTENT_CHANGE_TIMESTAMP", "FEED_CONTENT_STALE",
)

DERIVED_FIELDS = ("SPREAD", "MID", "MICROPRICE", "BOOK_IMBALANCE_L1")
WHY_DERIVED_NOT_SUPPLIED = (
    "spread, mid, microprice and L1 imbalance are FUNCTIONS of the touch. "
    "Accepting them as inputs would let a caller supply a mid that disagrees "
    "with its own bid and ask")

DEPTH_IS_NEVER_FABRICATED = (
    "a depth level the venue did not publish is MISSING. It is not zero -- "
    "zero means 'no size', which is a claim about the book -- and it is not "
    "carried forward from an earlier observation")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _num(v):
    if v in (None, NOT_IDENTIFIED, MISSING, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def decision_id(market_id, decision_ts, candidate_action):
    """Deterministic. The same state and action always produce the same id."""
    raw = "%s|%s|%s" % (market_id, _iso(decision_ts), candidate_action)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _iso(t):
    p = _parse(t)
    return p.isoformat() if p else NOT_IDENTIFIED


def native_state(book, prev_book=None, freshness=None):
    """Build the native-state block. Derives what is derivable; never invents.

    `book` supplies raw observations. Missing levels arrive as MISSING and
    stay MISSING.
    """
    out = {f: MISSING for f in NATIVE_STATE_FIELDS}
    b = dict(book or {})
    for f in NATIVE_STATE_FIELDS:
        if f in DERIVED_FIELDS:
            continue
        if f in b and b[f] not in (None, ""):
            out[f] = b[f]

    bid, ask = _num(b.get("BEST_BID")), _num(b.get("BEST_ASK"))
    bs, asz = _num(b.get("BEST_BID_SIZE")), _num(b.get("BEST_ASK_SIZE"))

    if bid is not None and ask is not None:
        out["SPREAD"] = round(ask - bid, 10)
        out["MID"] = round((ask + bid) / 2.0, 10)
        if bs is not None and asz is not None and (bs + asz) > 0:
            # Stoikov microprice: the touch weighted by the OPPOSITE size.
            out["MICROPRICE"] = round((bid * asz + ask * bs) / (bs + asz), 10)
    if bs is not None and asz is not None and (bs + asz) > 0:
        out["BOOK_IMBALANCE_L1"] = round((bs - asz) / (bs + asz), 10)

    if freshness:
        for k_src, k_dst in (
                ("MESSAGE_RECEIPT_TIME", "MESSAGE_RECEIPT_TIMESTAMP"),
                ("VENUE_STATE_TIME", "VENUE_STATE_TIMESTAMP"),
                ("LAST_MATERIAL_CONTENT_CHANGE",
                 "LAST_MATERIAL_CONTENT_CHANGE_TIMESTAMP"),
                ("BOOK_CONTENT_HASH", "BOOK_CONTENT_HASH"),
                ("FEED_CONTENT_STALE", "FEED_CONTENT_STALE")):
            if k_src in freshness:
                out[k_dst] = freshness[k_src]
    return out


def decision_row(event_id, market_id, decision_timestamp_utc,
                 candidate_action, book, condition_id=None, sport=None,
                 league=None, market_family=None, outcome=None, venue=None,
                 prev_book=None, freshness=None):
    """One append-only decision object. Absent identifiers are recorded."""
    row = {
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "GRAIN": GRAIN,
        "EVENT_ID": event_id if event_id is not None else NOT_IDENTIFIED,
        "MARKET_ID": market_id if market_id is not None else NOT_IDENTIFIED,
        "CONDITION_ID": condition_id or NOT_IDENTIFIED,
        "SPORT": sport or NOT_IDENTIFIED,
        "LEAGUE": league or NOT_IDENTIFIED,
        "MARKET_FAMILY": market_family or NOT_IDENTIFIED,
        "OUTCOME": outcome or NOT_IDENTIFIED,
        "VENUE": venue or NOT_IDENTIFIED,
        "DECISION_TIMESTAMP_UTC": _iso(decision_timestamp_utc),
        "CANDIDATE_ACTION": candidate_action,
    }
    row["DECISION_ID"] = decision_id(market_id, decision_timestamp_utc,
                                     candidate_action)
    row.update(native_state(book, prev_book, freshness))
    row["MISSING_IDENTIFIERS"] = [k for k in IDENTIFIERS
                                  if row.get(k) == NOT_IDENTIFIED]
    return row


# --- Section 5. Short-horizon outcome labels. ------------------------------

HORIZONS_S = (5, 30, 60, 300)

LABEL_FIELDS = tuple(
    "%s_T_PLUS_%dS" % (base, h)
    for base in ("MID", "BEST_BID", "BEST_ASK",
                 "EXECUTABLE_BUY_PRICE", "EXECUTABLE_SELL_PRICE")
    for h in HORIZONS_S
) + tuple("MID_MOVE_%dS" % h for h in HORIZONS_S) \
  + tuple("EXECUTABLE_MOVE_%dS" % h for h in HORIZONS_S) \
  + tuple("EXECUTABLE_BUY_MOVE_%dS" % h for h in HORIZONS_S) \
  + tuple("EXECUTABLE_SELL_MOVE_%dS" % h for h in HORIZONS_S)

# The label families a target-specific status is published for.
TARGET_BASES = ("MID_MOVE", "EXECUTABLE_MOVE", "EXECUTABLE_BUY_MOVE",
                "EXECUTABLE_SELL_MOVE")

ONE_SIDED_EXECUTABLE_LABEL_IS_NOT_SYMMETRIC = (
    "EXECUTABLE_MOVE_h was BEST_BID(t+h) - BEST_ASK(t): buy at the ask now, "
    "sell at the bid later. That is the BUY side alone. A short's round trip "
    "is BEST_BID(t) - BEST_ASK(t+h), and it is not the negative of the buy "
    "side -- each pays a different half of a spread that itself moves. A "
    "model scored on the buy-side label and then used on both sides is "
    "scored on a target it does not have. EXECUTABLE_MOVE_h is retained as "
    "the BUY side under its original name and marked superseded")

EXECUTABLE_MOVE_SUPERSEDED_BY = "EXECUTABLE_BUY_MOVE_<h>S"

NO_INTERPOLATION_BEYOND_THE_HORIZON = (
    "a label at T+h is ONE observation nearest to T+h within a bounded "
    "tolerance -- never an interpolation, never a value carried across a gap, "
    "and never assembled from observations on both sides. Interpolating "
    "across a hole uses a LATER observation to manufacture the value at T+h, "
    "which leaks information from after the target -- the exact thing the "
    "label is supposed to predict. The realised offset is recorded on every "
    "label so the reader can see how close the observation actually was")

LABEL_STATUS_VALUES = ("PRESENT", "MISSING")

# How far past the nominal horizon an observation may sit and still count.
# Declared here, before any capture, and never widened to rescue a label.
HORIZON_TOLERANCE_S = 12.0

# --- The V1 capture cannot measure the 5-second horizon. -------------------
#
# The frozen capture revisits each market at ~24 s (4.0 s interval x 6
# markets). The nearest observation strictly after T sits ~19 s from a T+5s
# target, past the 12 s tolerance. So MID_MOVE_5S is MISSING BY CONSTRUCTION.
#
# This is a property of the CAPTURE DESIGN, not a gap in the data, and it is
# reported permanently rather than repaired.

V1_NOMINAL_REVISIT_S = 24.0

HORIZON_STATUS_V1 = {
    5: "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY",
    30: "OBSERVABLE",
    60: "OBSERVABLE",
    300: "OBSERVABLE",
}

FIVE_SECOND_HORIZON_STATUS = "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY"

WHY_5S_IS_UNOBSERVABLE = (
    "at a ~24 s revisit the nearest observation strictly after T is ~19 s "
    "from a T+5s target, outside the 12 s tolerance. The horizon is not "
    "sparsely measured, it is UNMEASURED")

FORBIDDEN_5S_REPAIRS = (
    "INTERPOLATE_ACROSS_THE_GAP",
    "WIDEN_THE_TOLERANCE_AFTER_THE_FACT",
    "SUBSTITUTE_PLUS_24S_AND_CALL_IT_PLUS_5S",
    "TRAIN_A_5_SECOND_MODEL",
    "SCORE_A_5_SECOND_CHALLENGER",
)

WHY_NO_REPAIR = (
    "every one of these produces a 5-second result from data that contains no "
    "5-second information. Substituting +24s and calling it +5s is the most "
    "tempting because it yields a full column of numbers, and it is the "
    "worst because nothing downstream can tell the difference")

UNDECLARED_HORIZON_IS_NOT_PERMISSION = (
    "this horizon has no declared observability status for the V1 capture. "
    "NOT_IDENTIFIED is the absence of a finding, not a finding of "
    "OBSERVABLE, and a gate that treated it as permission would let any "
    "horizon nobody had thought about through")

V2_DERIVES_CADENCE_FROM_HORIZONS = (
    "for V2, poll cadence is DERIVED from the horizons the experiment intends "
    "to measure -- not chosen first and then discovered to exclude one. V1 is "
    "not modified to fix this")

# A horizon must clear a coverage bar before any model is scored on it --
# but WHICH bar is not something this module gets to invent.
#
# An earlier build set MIN_LABEL_COVERAGE_PCT = 50.0 and let MAY_EVALUATE
# turn on it. That is exactly the move the interaction-threshold correction
# retracts in edge_dashboard: a round number deciding a permission, with no
# derivation from a bias tolerance, a missingness mechanism or a precision
# target. Applying that standard to someone else's constant and not to my own
# would make the standard a rhetorical device.
#
# So there is no default. A caller that supplies a threshold gets a measured
# verdict against it; a caller that supplies none gets NOT_IDENTIFIED, which
# is not permission.
MIN_LABEL_COVERAGE_PCT = NOT_IDENTIFIED

WHY_NO_DEFAULT_COVERAGE_BAR = (
    "the coverage a horizon needs depends on WHY the labels are missing. "
    "Missing-at-random thins the sample; missing because the market was "
    "moving removes exactly the rows that carry the signal, and no "
    "percentage rescues that. A single number cannot express the "
    "difference, and a number chosen without expressing it is a guess "
    "wearing a threshold's clothes")

COVERAGE_BAR_INPUTS = (
    "MISSINGNESS_MECHANISM",
    "TOLERABLE_BIAS_IN_THE_ESTIMATE",
    "PRECISION_TARGET_AT_THE_SURVIVING_N",
    "INDEPENDENT_EVENTS_SURVIVING",
)


def horizon_label_coverage_gate(rows, horizon_s, min_coverage_pct=None):
    """May a model be evaluated on this horizon at all? Fails closed.

    Checks the DECLARED status first (a horizon unobservable by construction
    is refused regardless of what the rows happen to contain), then measured
    label coverage.
    """
    min_coverage_pct = (MIN_LABEL_COVERAGE_PCT if min_coverage_pct is None
                        else min_coverage_pct)
    # A horizon nobody declared is NOT permission. The earlier form asked
    # `if declared and ...`, so an undeclared horizon (45 s, or True, which
    # equals 1) skipped the refusal entirely and could reach MAY_EVALUATE =
    # True carrying HORIZON_STATUS = NOT_IDENTIFIED -- a gate that published
    # "we have not established this" as permission to evaluate.
    declared = HORIZON_STATUS_V1.get(horizon_s, NOT_IDENTIFIED) \
        if not isinstance(horizon_s, bool) else NOT_IDENTIFIED
    if declared != "OBSERVABLE":
        return {
            "HORIZON_S": horizon_s,
            "HORIZON_STATUS": declared,
            "HORIZON_LABEL_COVERAGE_GATE": "FAIL",
            "MAY_EVALUATE": False,
            "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN",
            "WHY": (WHY_5S_IS_UNOBSERVABLE if horizon_s == 5 else
                    (UNDECLARED_HORIZON_IS_NOT_PERMISSION
                     if declared == NOT_IDENTIFIED else declared)),
            "DECLARED_HORIZONS": tuple(sorted(HORIZON_STATUS_V1)),
            "FORBIDDEN_REPAIRS": FORBIDDEN_5S_REPAIRS,
            "WHY_NO_REPAIR": WHY_NO_REPAIR,
        }
    key = "%dS" % horizon_s
    total = 0
    present = 0
    for r in rows or ():
        st = (r.get("LABEL_STATUS") or {})
        if key not in st:
            continue
        total += 1
        present += 1 if st[key] == "PRESENT" else 0
    if not total:
        return {"HORIZON_S": horizon_s,
                "HORIZON_LABEL_COVERAGE_GATE": "FAIL",
                "MAY_EVALUATE": False,
                "LABEL_COVERAGE_PCT": NOT_IDENTIFIED,
                "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN",
                "WHY": "no row carried a label status for this horizon"}
    cov = 100.0 * present / total
    base = {
        "HORIZON_S": horizon_s,
        "HORIZON_STATUS": declared,
        "LABEL_COVERAGE_PCT": round(cov, 3),
        "MIN_LABEL_COVERAGE_PCT": min_coverage_pct,
        "LABELLED": present, "CANDIDATE_ROWS": total,
    }
    if not isinstance(min_coverage_pct, (int, float)) \
            or isinstance(min_coverage_pct, bool):
        base.update({
            "HORIZON_LABEL_COVERAGE_GATE": "NOT_IDENTIFIED",
            "MAY_EVALUATE": NOT_IDENTIFIED,
            "RESULT": "COVERAGE_BAR_NOT_IDENTIFIED",
            "BLOCKED_ON": ("MIN_LABEL_COVERAGE_PCT",),
            "COVERAGE_BAR_INPUTS": COVERAGE_BAR_INPUTS,
            "WHY_NO_DEFAULT_COVERAGE_BAR": WHY_NO_DEFAULT_COVERAGE_BAR,
        })
        return base
    ok = cov >= min_coverage_pct
    base.update({
        "HORIZON_LABEL_COVERAGE_GATE": "PASS" if ok else "FAIL",
        "MAY_EVALUATE": ok,
        "RESULT": (None if ok
                   else "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"),
        "BAR_IS_THE_CALLERS": (
            "%s%% was supplied by the caller, not derived here" %
            min_coverage_pct),
    })
    return base


WHY_A_TOLERANCE = (
    "the capture samples on a grid with a ~24 s nominal revisit, so an exact "
    "T+5s observation will rarely exist. A bounded tolerance is honest; "
    "widening it after seeing how many labels are missing is not")


# EVENT_ID is NOT a subject key. An event carries several markets -- a
# moneyline and a total on the same game share it -- so matching on EVENT_ID
# would take a DIFFERENT contract's later book as this contract's forward
# observation, and call the price gap between them a "move".
SUBJECT_KEYS = ("MARKET_ID", "CONDITION_ID", "TOKEN_ID")

EVENT_ID_IS_NOT_A_MARKET_IDENTITY = (
    "EVENT_ID was the last entry in SUBJECT_KEYS, so a row that named no "
    "market but named an event matched every other market on that event. The "
    "moneyline's T+60 book would label the total's move. An event is a "
    "collection of markets, and a forward observation is THIS market later")

A_ROW_WITH_NO_IDENTITY_MATCHES_NOTHING = (
    "an origin row carrying no subject key at all used to match every "
    "candidate, so a series spanning six interleaved markets was treated as "
    "one market's. Identity must be established, not assumed from its "
    "absence. The one exception is an explicit origin=None, which is the "
    "caller stating the contract that the series is a single market's")

TIE_BREAK_RULE = "MIN_TUPLE_ABS_TARGET_ERROR_THEN_OBSERVATION_TIMESTAMP"

WHY_A_TIE_NEEDS_A_FROZEN_RULE = (
    "with a 12 s tolerance and a target at T+60, an observation at T+48 and "
    "one at T+72 are equidistant. Taking whichever the iteration reached "
    "first made the scientific label depend on the order rows happened to be "
    "stored in: the same data, re-sorted, produced a different label. The "
    "rule is frozen as the minimum of (ABS_TARGET_ERROR, "
    "OBSERVATION_TIMESTAMP), which prefers the EARLIER observation on an "
    "exact tie -- earlier because it uses strictly less future information")

A_FORWARD_OBSERVATION_IS_THE_SAME_MARKET_LATER = (
    "matching on timestamp alone would take whichever market happened to be "
    "polled next. The capture interleaves 6 markets 4 s apart, so the nearest "
    "row to T+5s is almost always a DIFFERENT market, and the resulting "
    "'move' would be the price gap between two unrelated contracts. Identity "
    "is checked first, and an unidentifiable row is skipped rather than "
    "assumed to match")


def _same_subject(origin, candidate):
    """Do these two rows describe the same market? Fails closed.

    Compares on the first subject key the ORIGIN carries. If the origin names
    no subject at all the series is taken to be a single market's (the
    documented contract, and what every in-repo caller passes); if the origin
    names one and the candidate cannot answer, the candidate is skipped.
    """
    if origin is None:
        # The caller states the contract: this series is one market's.
        return True
    named = [k for k in SUBJECT_KEYS
             if origin.get(k) not in (None, NOT_IDENTIFIED)]
    if not named:
        # An origin row that identifies no market cannot be matched to a
        # later book. Returning True here made a six-market interleaved
        # series look like one market's.
        return False
    cand = candidate or {}
    shared = 0
    for k in named:
        b = cand.get(k)
        if b in (None, NOT_IDENTIFIED):
            continue
        if b != origin[k]:
            return False
        shared += 1
    return shared > 0


def forward_observation(series, t0, horizon_s, tolerance_s=HORIZON_TOLERANCE_S,
                        time_key="DECISION_TIMESTAMP_UTC", origin=None):
    """The observation NEAREST to t0+h, strictly after t0, within tolerance.

    Returns (row, realised_offset_s) or (None, None).

    Three rules, each load-bearing:
      - STRICTLY AFTER t0, so the origin row cannot label itself. Without
        this, a 5-second horizon on a 24-second grid would resolve to the
        current observation and every MID_MOVE_5S would be exactly zero.
      - NEAREST to the target, not the first at-or-after it. On a grid the
        first at-or-after systematically overshoots (a 30 s horizon on a 24 s
        grid would resolve to +48 s), which labels a longer horizon than the
        one being claimed.
      - WITHIN TOLERANCE, or MISSING. A 24 s grid genuinely cannot label a
        5 s horizon, and that is a fact about the capture design rather than
        a gap to paper over.
    """
    base = _parse(t0)
    if base is None:
        return None, None
    target = base + datetime.timedelta(seconds=horizon_s)
    best, best_key, best_off = None, None, None
    for r in series or ():
        if not _same_subject(origin, r):
            continue                       # never another market's book
        t = _parse(r.get(time_key))
        if t is None or t <= base:
            continue                       # strictly after the origin
        off = (t - target).total_seconds()
        gap = abs(off)
        if gap > tolerance_s:
            continue
        # FROZEN TIE RULE: (ABS_TARGET_ERROR, OBSERVATION_TIMESTAMP).
        # Deterministic, and independent of the order `series` arrives in.
        key = (gap, t)
        if best_key is None or key < best_key:
            best, best_key, best_off = r, key, off
    return best, best_off


def label_row(state_row, series, horizons_s=HORIZONS_S,
              tolerance_s=HORIZON_TOLERANCE_S):
    """Attach forward labels where future observations genuinely support them."""
    out = {}
    t0 = state_row.get("DECISION_TIMESTAMP_UTC")
    mid0 = _num(state_row.get("MID"))
    bid0, ask0 = (_num(state_row.get("BEST_BID")),
                  _num(state_row.get("BEST_ASK")))
    statuses, offsets, horizon_status = {}, {}, {}
    target_status, forwards = {}, {}

    def _blank(h, why):
        for base in ("MID", "BEST_BID", "BEST_ASK",
                     "EXECUTABLE_BUY_PRICE", "EXECUTABLE_SELL_PRICE"):
            out["%s_T_PLUS_%dS" % (base, h)] = why
        for base in TARGET_BASES:
            out["%s_%dS" % (base, h)] = why
            target_status["%s_%dS" % (base, h)] = why

    for h in horizons_s:
        declared = HORIZON_STATUS_V1.get(h, NOT_IDENTIFIED)
        horizon_status["%dS" % h] = declared
        # A horizon the capture cannot observe is not labelled at all. With a
        # 12 s tolerance a T+5s target would otherwise resolve to a row at
        # T+17s and publish it as a five-second move -- the forbidden repair
        # SUBSTITUTE_PLUS_24S_AND_CALL_IT_PLUS_5S, arrived at by accident.
        if declared != "OBSERVABLE":
            why = (declared if declared != NOT_IDENTIFIED
                   else "HORIZON_OBSERVABILITY_NOT_IDENTIFIED")
            statuses["%dS" % h] = why
            offsets["%dS" % h] = why
            _blank(h, why)
            continue
        fwd, off = forward_observation(series, t0, h, tolerance_s,
                                       origin=state_row)
        if fwd is None:
            statuses["%dS" % h] = MISSING
            offsets["%dS" % h] = MISSING
            _blank(h, MISSING)
            continue
        statuses["%dS" % h] = "PRESENT"
        offsets["%dS" % h] = round(off, 3)
        forwards["%dS" % h] = {
            "SOURCE_FORWARD_ROW_HASH": row_hash(fwd),
            "FORWARD_OBSERVATION_TIMESTAMP": fwd.get(
                "DECISION_TIMESTAMP_UTC", NOT_IDENTIFIED),
            "REALIZED_OFFSET_S": round(off, 3),
        }
        m = _num(fwd.get("MID"))
        b = _num(fwd.get("BEST_BID"))
        a = _num(fwd.get("BEST_ASK"))
        out["MID_T_PLUS_%dS" % h] = m if m is not None else MISSING
        out["BEST_BID_T_PLUS_%dS" % h] = b if b is not None else MISSING
        out["BEST_ASK_T_PLUS_%dS" % h] = a if a is not None else MISSING
        # Executable, not mid: a buyer lifts the ask, a seller hits the bid.
        out["EXECUTABLE_BUY_PRICE_T_PLUS_%dS" % h] = (a if a is not None
                                                      else MISSING)
        out["EXECUTABLE_SELL_PRICE_T_PLUS_%dS" % h] = (b if b is not None
                                                       else MISSING)

        def _put(name, value):
            key = "%s_%dS" % (name, h)
            out[key] = value
            target_status[key] = (MISSING if value == MISSING else "PRESENT")

        # A resolved horizon does NOT mean every target on it resolved. The
        # forward row may carry a mid and no bid, and LABEL_STATUS said
        # PRESENT for the whole horizon while EXECUTABLE_MOVE was MISSING.
        _put("MID_MOVE", round(m - mid0, 10)
             if (m is not None and mid0 is not None) else MISSING)
        # BUY round trip: lift the ask now, hit the bid later.
        buy = (round(b - ask0, 10)
               if (b is not None and ask0 is not None) else MISSING)
        # SELL round trip: hit the bid now, lift the ask later. NOT the
        # negative of the buy side -- each pays a different half-spread.
        sell = (round(bid0 - a, 10)
                if (a is not None and bid0 is not None) else MISSING)
        _put("EXECUTABLE_BUY_MOVE", buy)
        _put("EXECUTABLE_SELL_MOVE", sell)
        _put("EXECUTABLE_MOVE", buy)          # superseded name, BUY side

    out["LABEL_STATUS"] = statuses
    out["HORIZON_STATUS"] = horizon_status
    out["TARGET_LABEL_STATUS"] = target_status
    out["FORWARD_OBSERVATIONS"] = forwards
    out["SOURCE_ORIGIN_ROW_HASH"] = row_hash(state_row)
    out["ORIGIN_TIMESTAMP"] = t0 if t0 is not None else NOT_IDENTIFIED
    out["MARKET_IDENTITY"] = _label_subject(state_row)
    out["LABEL_REALISED_OFFSET_S"] = offsets
    out["LABEL_STATUS_OVERALL"] = ("PRESENT" if statuses and all(
        v == "PRESENT" for v in statuses.values()) else MISSING)
    out["TARGET_LABEL_STATUS_OVERALL"] = ("PRESENT" if target_status and all(
        v == "PRESENT" for v in target_status.values()) else MISSING)
    out["NO_INTERPOLATION_BEYOND_THE_HORIZON"] = \
        NO_INTERPOLATION_BEYOND_THE_HORIZON
    out["A_RESOLVED_HORIZON_IS_NOT_A_RESOLVED_TARGET"] = (
        "LABEL_STATUS answers 'did a forward observation exist'. "
        "TARGET_LABEL_STATUS answers 'did THIS target compute', which is a "
        "different question whenever the forward row is partially populated")
    out["ONE_SIDED_EXECUTABLE_LABEL_IS_NOT_SYMMETRIC"] = \
        ONE_SIDED_EXECUTABLE_LABEL_IS_NOT_SYMMETRIC
    out["EXECUTABLE_MOVE_SUPERSEDED_BY"] = EXECUTABLE_MOVE_SUPERSEDED_BY
    out["HORIZON_TOLERANCE_S"] = tolerance_s
    return out


# --- Section 5b. The canonical label artifact. -----------------------------
#
# Labels were rebuilt at each call site from whatever series happened to be in
# hand, so two modules could hold different MID_MOVE_60S for the same decision
# and nothing would notice. A scored model and the labels it was scored on
# must be provably the same labels.

LABEL_BUILDER_VERSION = "BETTOR_LABELS_V1"

LABEL_ARTIFACT_FIELDS = ("DECISION_ID", "SUBJECT", "HORIZONS_S",
                         "HORIZON_TOLERANCE_S", "TIE_BREAK_RULE",
                         "LABEL_BUILDER_VERSION", "LABEL_BUILDER_CODE_SHA",
                         "LABEL_SPEC_SHA", "CAPTURE_SPEC_SHA",
                         "HORIZON_STATUS", "TARGET_LABEL_STATUS",
                         "LABELS", "OBSERVATION_CHAIN")

# What every PRESENT target must be able to show about where it came from.
OBSERVATION_CHAIN_FIELDS = ("SOURCE_ORIGIN_ROW_HASH",
                            "SOURCE_FORWARD_ROW_HASH",
                            "ORIGIN_TIMESTAMP",
                            "FORWARD_OBSERVATION_TIMESTAMP",
                            "TARGET_HORIZON_S", "REALIZED_OFFSET_S",
                            "MARKET_IDENTITY", "LABEL_VALUE",
                            "TARGET_LABEL_STATUS")

A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN = (
    "sealing the label object proves the numbers have not been altered "
    "since they were written. It says nothing about WHICH observations "
    "produced them. Two different forward rows, from two different markets "
    "or two different captures, yield two internally consistent artifacts. "
    "The chain names the origin row, the forward row, both timestamps, the "
    "realised offset and the market identity, so a score can be traced to "
    "the observations it rests on")

A_LABEL_REBUILT_IS_NOT_A_LABEL_AGREED = (
    "every consumer rebuilding labels from its own view of the series means "
    "a challenger and a baseline can be scored on different targets and "
    "compared as if they were not. The artifact is built once, sealed with "
    "LABEL_ARTIFACT_SHA, and the SHA travels with every score")

LABEL_PROVENANCE_STATUSES = ("VALID", "SHA_MISMATCH", "NOT_SEALED",
                             "OBSERVATION_CHAIN_INCOMPLETE")

OBSERVATION_CHAIN_STATUSES = ("COMPLETE", "INCOMPLETE")


def row_hash(row):
    """A stable digest of one observation row."""
    return hashlib.sha256(
        json.dumps(row or {}, sort_keys=True, default=str).encode()
    ).hexdigest()


def _label_subject(state_row):
    return {k: (state_row or {}).get(k, NOT_IDENTIFIED) for k in SUBJECT_KEYS}


def label_builder_code_sha():
    """The digest of THIS module's source -- the code that built the labels."""
    import os
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return NOT_IDENTIFIED


def label_spec_sha():
    """The digest of the frozen labelling SPEC, independent of the source."""
    spec = {
        "HORIZONS_S": list(HORIZONS_S),
        "HORIZON_TOLERANCE_S": HORIZON_TOLERANCE_S,
        "TIE_BREAK_RULE": TIE_BREAK_RULE,
        "SUBJECT_KEYS": list(SUBJECT_KEYS),
        "HORIZON_STATUS_V1": dict(HORIZON_STATUS_V1),
        "TARGET_BASES": list(TARGET_BASES),
        "LABEL_BUILDER_VERSION": LABEL_BUILDER_VERSION,
        "NO_INTERPOLATION_BEYOND_THE_HORIZON":
            NO_INTERPOLATION_BEYOND_THE_HORIZON,
    }
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()


def _observation_chain(labels, horizons_s):
    """One chain record per target, naming the observations behind it."""
    chain, incomplete = {}, []
    origin_hash = labels.get("SOURCE_ORIGIN_ROW_HASH", NOT_IDENTIFIED)
    origin_ts = labels.get("ORIGIN_TIMESTAMP", NOT_IDENTIFIED)
    identity = labels.get("MARKET_IDENTITY", {})
    fwds = labels.get("FORWARD_OBSERVATIONS", {})
    for h in horizons_s:
        hk = "%dS" % h
        f = fwds.get(hk) or {}
        for base in TARGET_BASES:
            key = "%s_%dS" % (base, h)
            st = (labels.get("TARGET_LABEL_STATUS") or {}).get(
                key, NOT_IDENTIFIED)
            rec = {
                "SOURCE_ORIGIN_ROW_HASH": origin_hash,
                "SOURCE_FORWARD_ROW_HASH": f.get("SOURCE_FORWARD_ROW_HASH",
                                                 NOT_IDENTIFIED),
                "ORIGIN_TIMESTAMP": origin_ts,
                "FORWARD_OBSERVATION_TIMESTAMP": f.get(
                    "FORWARD_OBSERVATION_TIMESTAMP", NOT_IDENTIFIED),
                "TARGET_HORIZON_S": h,
                "REALIZED_OFFSET_S": f.get("REALIZED_OFFSET_S",
                                           NOT_IDENTIFIED),
                "MARKET_IDENTITY": identity,
                "LABEL_VALUE": labels.get(key, MISSING),
                "TARGET_LABEL_STATUS": st,
            }
            chain[key] = rec
            # Only a PRESENT target has to show a complete chain. A MISSING
            # or UNOBSERVABLE target has nothing to trace, and demanding a
            # forward row for one would be demanding an observation that by
            # construction does not exist.
            if st == "PRESENT":
                for fld in OBSERVATION_CHAIN_FIELDS:
                    v = rec.get(fld)
                    if v in (None, NOT_IDENTIFIED, MISSING):
                        incomplete.append((key, fld))
    return chain, tuple(incomplete)


def label_artifact(state_row, series, horizons_s=HORIZONS_S,
                   tolerance_s=HORIZON_TOLERANCE_S, capture_spec_sha=None):
    """Build the labels ONCE, bind their observations, and seal the lot.

    The SHA is the label identity; the chain is what the identity refers to.
    `capture_spec_sha` comes from the capture manifest and is REQUIRED for a
    complete chain -- without it the labels cannot be tied to the capture
    that produced the observations.
    """
    labels = label_row(state_row, series, horizons_s, tolerance_s)
    chain, incomplete = _observation_chain(labels, horizons_s)
    body = {
        "DECISION_ID": (state_row or {}).get("DECISION_ID", NOT_IDENTIFIED),
        "SUBJECT": _label_subject(state_row),
        "HORIZONS_S": list(horizons_s),
        "HORIZON_TOLERANCE_S": tolerance_s,
        "TIE_BREAK_RULE": TIE_BREAK_RULE,
        "LABEL_BUILDER_VERSION": LABEL_BUILDER_VERSION,
        "LABEL_BUILDER_CODE_SHA": label_builder_code_sha(),
        "LABEL_SPEC_SHA": label_spec_sha(),
        "CAPTURE_SPEC_SHA": capture_spec_sha or NOT_IDENTIFIED,
        "HORIZON_STATUS": labels.get("HORIZON_STATUS", {}),
        "TARGET_LABEL_STATUS": labels.get("TARGET_LABEL_STATUS", {}),
        "LABELS": {k: labels[k] for k in sorted(labels)
                   if k in LABEL_FIELDS},
        "OBSERVATION_CHAIN": chain,
    }
    if not capture_spec_sha:
        incomplete = incomplete + (("ARTIFACT", "CAPTURE_SPEC_SHA"),)
    body["OBSERVATION_CHAIN_STATUS"] = ("COMPLETE" if not incomplete
                                        else "INCOMPLETE")
    body["OBSERVATION_CHAIN_GAPS"] = incomplete
    body["LABEL_ARTIFACT_FIELDS"] = LABEL_ARTIFACT_FIELDS
    body["OBSERVATION_CHAIN_FIELDS"] = OBSERVATION_CHAIN_FIELDS
    body["A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN"] = \
        A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN
    body["A_LABEL_REBUILT_IS_NOT_A_LABEL_AGREED"] = \
        A_LABEL_REBUILT_IS_NOT_A_LABEL_AGREED
    body["LABEL_PROVENANCE_STATUS"] = ("VALID" if not incomplete
                                       else "OBSERVATION_CHAIN_INCOMPLETE")
    # ONE sealing convention: everything but the digest field itself. The
    # seal used to cover a sub-body and four fields were attached AFTER it,
    # so a reader recomputing the digest over the whole object -- which is
    # what the decision gate does -- could never reproduce it. The canonical
    # artifact then failed integrity while a hand-built object sealed the
    # reader's way passed. A convention that only outsiders' forgeries
    # satisfy is worse than no convention.
    body["LABEL_ARTIFACT_SHA"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body


A_STORED_CHAIN_STATUS_IS_A_SELF_ASSESSMENT = (
    "OBSERVATION_CHAIN_STATUS was read out of the artifact, so the seal "
    "protected the artifact's own opinion of itself. It is re-derived from "
    "every PRESENT target's required fields, and -- where the immutable "
    "source rows are available -- the origin and forward row hashes, the "
    "timestamps, the market identity, the label value, the realised offset "
    "and the tie rule are each recomputed against those rows")

SOURCE_RESOLUTION_STATUSES = ("RESOLVED_AND_RECOMPUTED",
                              "SOURCE_ROWS_UNAVAILABLE",
                              "SOURCE_ROW_HASH_MISMATCH",
                              "RECOMPUTATION_MISMATCH")

CANONICAL_SHA_FIELDS = ("LABEL_BUILDER_CODE_SHA", "LABEL_SPEC_SHA",
                        "CAPTURE_SPEC_SHA")


def rederive_observation_chain(artifact, source_rows=None,
                               trusted_shas=None):
    """Re-derive chain completeness; resolve the source rows when given.

    `source_rows` maps a row hash to the immutable captured row. When both
    the origin and forward rows resolve, the chain is not merely complete --
    it is RECOMPUTED: timestamps, market identity, label value, realised
    offset and the frozen tie rule are each checked against the rows.

    `trusted_shas` maps LABEL_BUILDER_CODE_SHA / LABEL_SPEC_SHA /
    CAPTURE_SPEC_SHA to the canonical value; a non-empty string that matches
    nothing canonical is not a verified reference.
    """
    a = artifact or {}
    chain = a.get("OBSERVATION_CHAIN") or {}
    gaps, recomputed, source_status = [], {}, {}
    rows = source_rows or {}
    # The chain is re-derived against what the ARTIFACT CLAIMS TO LABEL, not
    # merely against the chain it happens to carry. Deleting the chain and
    # re-sealing used to produce a COMPLETE re-derivation, because a loop
    # over an empty dict finds nothing wrong: every PRESENT target must have
    # its own chain record, and that record must agree it is present.
    statuses = a.get("TARGET_LABEL_STATUS") or {}
    for key in sorted(statuses):
        if statuses.get(key) != "PRESENT":
            continue
        rec = chain.get(key)
        if not isinstance(rec, dict):
            gaps.append((key, "CHAIN_RECORD_ABSENT_FOR_PRESENT_TARGET"))
        elif rec.get("TARGET_LABEL_STATUS") != "PRESENT":
            gaps.append((key, "CHAIN_RECORD_DISAGREES_WITH_LABEL_STATUS"))
    for key, rec in sorted(chain.items()):
        if rec.get("TARGET_LABEL_STATUS") != "PRESENT":
            continue
        for fld in OBSERVATION_CHAIN_FIELDS:
            v = rec.get(fld)
            if v in (None, NOT_IDENTIFIED, MISSING):
                gaps.append((key, fld))
        if not rows:
            source_status[key] = "SOURCE_ROWS_UNAVAILABLE"
            continue
        o = rows.get(rec.get("SOURCE_ORIGIN_ROW_HASH"))
        f = rows.get(rec.get("SOURCE_FORWARD_ROW_HASH"))
        if not isinstance(o, dict) or not isinstance(f, dict):
            source_status[key] = "SOURCE_ROW_HASH_MISMATCH"
            gaps.append((key, "SOURCE_ROWS_DO_NOT_RESOLVE"))
            continue
        bad = _recompute_chain_record(key, rec, o, f)
        source_status[key] = ("RECOMPUTATION_MISMATCH" if bad
                              else "RESOLVED_AND_RECOMPUTED")
        if bad:
            gaps.extend((key, b) for b in bad)
        recomputed[key] = tuple(bad)
    canonical = {}
    for fld in CANONICAL_SHA_FIELDS:
        got = a.get(fld)
        want = (trusted_shas or {}).get(fld)
        if got in (None, "", NOT_IDENTIFIED):
            canonical[fld] = "ABSENT"
            gaps.append(("ARTIFACT", fld))
        elif want is None:
            canonical[fld] = "PRESENT_NOT_VERIFIED_AGAINST_CANONICAL"
        elif got != want:
            canonical[fld] = "MISMATCH"
            gaps.append(("ARTIFACT", "%s_MISMATCH" % fld))
        else:
            canonical[fld] = "VERIFIED"
    return {
        "OBSERVATION_CHAIN_STATUS": "COMPLETE" if not gaps else "INCOMPLETE",
        "OBSERVATION_CHAIN_GAPS": tuple(gaps),
        "SOURCE_RESOLUTION": source_status,
        "SOURCE_RESOLUTION_STATUSES": SOURCE_RESOLUTION_STATUSES,
        "RECOMPUTATION_PROBLEMS": recomputed,
        "CANONICAL_SHA_VERIFICATION": canonical,
        "A_STORED_CHAIN_STATUS_IS_A_SELF_ASSESSMENT":
            A_STORED_CHAIN_STATUS_IS_A_SELF_ASSESSMENT,
    }


def _recompute_chain_record(key, rec, origin_row, forward_row):
    """Recompute one chain record from the two rows it names."""
    bad = []
    if origin_row.get("DECISION_TIMESTAMP_UTC") != rec.get(
            "ORIGIN_TIMESTAMP"):
        bad.append("ORIGIN_TIMESTAMP_MISMATCH")
    if forward_row.get("DECISION_TIMESTAMP_UTC") != rec.get(
            "FORWARD_OBSERVATION_TIMESTAMP"):
        bad.append("FORWARD_TIMESTAMP_MISMATCH")
    ident = rec.get("MARKET_IDENTITY") or {}
    for r, tag in ((origin_row, "ORIGIN"), (forward_row, "FORWARD")):
        for k in SUBJECT_KEYS:
            want = ident.get(k)
            if want in (None, NOT_IDENTIFIED):
                continue
            if r.get(k) != want:
                bad.append("%s_MARKET_IDENTITY_MISMATCH" % tag)
                break
    t0 = _parse(rec.get("ORIGIN_TIMESTAMP"))
    t1 = _parse(rec.get("FORWARD_OBSERVATION_TIMESTAMP"))
    h = rec.get("TARGET_HORIZON_S")
    if t0 is not None and t1 is not None and isinstance(h, (int, float)):
        want_off = (t1 - t0).total_seconds() - float(h)
        got_off = rec.get("REALIZED_OFFSET_S")
        if not isinstance(got_off, (int, float)) or \
                abs(want_off - float(got_off)) > 1e-6:
            bad.append("REALIZED_OFFSET_MISMATCH")
        if abs(want_off) > HORIZON_TOLERANCE_S:
            bad.append("FORWARD_ROW_OUTSIDE_TOLERANCE")
    base = key.rsplit("_", 1)[0]
    want_val = _recompute_label_value(base, origin_row, forward_row)
    got_val = rec.get("LABEL_VALUE")
    if want_val is None:
        bad.append("LABEL_VALUE_NOT_RECOMPUTABLE")
    elif not isinstance(got_val, (int, float)) or \
            abs(float(got_val) - want_val) > 1e-9:
        bad.append("LABEL_VALUE_MISMATCH")
    return bad


def _recompute_label_value(base, origin_row, forward_row):
    """The label this target SHOULD have, from the two named rows."""
    o_mid, f_mid = _num(origin_row.get("MID")), _num(forward_row.get("MID"))
    o_bid, o_ask = (_num(origin_row.get("BEST_BID")),
                    _num(origin_row.get("BEST_ASK")))
    f_bid, f_ask = (_num(forward_row.get("BEST_BID")),
                    _num(forward_row.get("BEST_ASK")))
    if base == "MID_MOVE":
        return None if (o_mid is None or f_mid is None) \
            else round(f_mid - o_mid, 10)
    if base in ("EXECUTABLE_BUY_MOVE", "EXECUTABLE_MOVE"):
        return None if (o_ask is None or f_bid is None) \
            else round(f_bid - o_ask, 10)
    if base == "EXECUTABLE_SELL_MOVE":
        return None if (o_bid is None or f_ask is None) \
            else round(o_bid - f_ask, 10)
    return None


# Keys attached AFTER sealing, excluded when the seal is recomputed.
# Only the digest field itself, plus what a trusted store attaches on
# retrieval, sits outside the seal. Everything the builder wrote is sealed,
# so a reader recomputing over the whole object gets the same digest.
_UNSEALED_KEYS = ("LABEL_ARTIFACT_SHA", "ARTIFACT_STORE_ID",
                  "ARTIFACT_STORE_KIND", "ARTIFACT_RETRIEVED_FROM")


def verify_label_artifact(artifact):
    """Recompute the seal AND re-derive the chain status. Fails closed.

    An artifact whose numbers verify but whose observation chain is
    incomplete is NOT valid provenance: the seal proves only that nobody
    edited it since it was written.
    """
    a = dict(artifact or {})
    claimed = a.pop("LABEL_ARTIFACT_SHA", None)
    for k in _UNSEALED_KEYS:
        a.pop(k, None)
    if claimed is None:
        return {"LABEL_PROVENANCE_STATUS": "NOT_SEALED",
                "LABEL_ARTIFACT_SHA": NOT_IDENTIFIED,
                "OBSERVATION_CHAIN_STATUS": NOT_IDENTIFIED,
                "A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN":
                    A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN,
                "A_LABEL_REBUILT_IS_NOT_A_LABEL_AGREED":
                    A_LABEL_REBUILT_IS_NOT_A_LABEL_AGREED}
    got = hashlib.sha256(
        json.dumps(a, sort_keys=True, default=str).encode()).hexdigest()
    sealed = got == claimed
    # RE-DERIVED, not read. Trusting the stored OBSERVATION_CHAIN_STATUS made
    # the seal cover a self-assessment: an artifact could declare its own
    # chain COMPLETE and the seal would faithfully protect that declaration.
    rederived = rederive_observation_chain(artifact)
    chain_ok = rederived["OBSERVATION_CHAIN_STATUS"] == "COMPLETE"
    if not sealed:
        status = "SHA_MISMATCH"
    elif not chain_ok:
        status = "OBSERVATION_CHAIN_INCOMPLETE"
    else:
        status = "VALID"
    return {"LABEL_PROVENANCE_STATUS": status,
            "LABEL_ARTIFACT_SHA": claimed,
            "RECOMPUTED_SHA": got,
            "SEAL_INTACT": sealed,
            "OBSERVATION_CHAIN_STATUS":
                rederived["OBSERVATION_CHAIN_STATUS"],
            "OBSERVATION_CHAIN_REDERIVED": True,
            "STORED_OBSERVATION_CHAIN_STATUS": a.get(
                "OBSERVATION_CHAIN_STATUS", NOT_IDENTIFIED),
            "OBSERVATION_CHAIN_GAPS": rederived["OBSERVATION_CHAIN_GAPS"],
            "A_STORED_CHAIN_STATUS_IS_A_SELF_ASSESSMENT":
                A_STORED_CHAIN_STATUS_IS_A_SELF_ASSESSMENT,
            "OBSERVATION_CHAIN_FIELDS": OBSERVATION_CHAIN_FIELDS,
            "A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN":
                A_CHECKSUM_OVER_THE_LABEL_IS_NOT_THE_CHAIN,
            "LABEL_PROVENANCE_STATUSES": LABEL_PROVENANCE_STATUSES}


# --- Section 6. Economic move labels. --------------------------------------

ECONOMIC_FIELDS = tuple("REALIZED_MOVE_TO_SPREAD_%dS" % h for h in HORIZONS_S)
THRESHOLD_FIELDS = ("MOVE_EXCEEDS_HALF_SPREAD", "MOVE_EXCEEDS_FULL_SPREAD",
                    "MOVE_EXCEEDS_2X_SPREAD")

RAW_MOVEMENT_IS_INSUFFICIENT = (
    "a statistically predictable 0.2-cent move through a 2-cent spread is not "
    "an edge. The engine needs the move expressed in units of the cost of "
    "acting, which is the spread")


def economic_labels(state_row, labels, horizons_s=HORIZONS_S):
    """Express each realised move as a multiple of the CURRENT spread."""
    spread = _num(state_row.get("SPREAD"))
    out = {"CURRENT_SPREAD": spread if spread is not None else MISSING,
           "RAW_MOVEMENT_IS_INSUFFICIENT": RAW_MOVEMENT_IS_INSUFFICIENT}
    for h in horizons_s:
        mv = labels.get("MID_MOVE_%dS" % h)
        key = "REALIZED_MOVE_TO_SPREAD_%dS" % h
        # A label may be MISSING, or carry a horizon-status sentinel such as
        # UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY. Anything that is not a number
        # is not a move, and the reason is carried through rather than
        # flattened to MISSING -- an unobservable horizon and a gap in the
        # capture are different facts.
        if not isinstance(mv, (int, float)) or isinstance(mv, bool) \
                or not spread or spread <= 0:
            why = mv if isinstance(mv, str) and mv != MISSING else MISSING
            out[key] = why
            for t in THRESHOLD_FIELDS:
                out["%s_%dS" % (t, h)] = why
            continue
        ratio = abs(float(mv)) / spread
        out[key] = round(ratio, 8)
        out["MOVE_EXCEEDS_HALF_SPREAD_%dS" % h] = ratio > 0.5
        out["MOVE_EXCEEDS_FULL_SPREAD_%dS" % h] = ratio > 1.0
        out["MOVE_EXCEEDS_2X_SPREAD_%dS" % h] = ratio > 2.0
    return out


# --- Section 21. Proprietary-data retention metrics. -----------------------

RETENTION_METRICS = (
    "TOTAL_DECISION_STATES", "TOTAL_EVENT_HOURS", "TOTAL_INDEPENDENT_EVENTS",
    "TOTAL_MARKETS", "TOTAL_MARKET_STATE_TOXICITY_LABELS",
    "TOTAL_EXTERNAL_ALIGNED_STATES",
    "TOTAL_REAL_ORDERS", "TOTAL_REAL_FILLS", "TOTAL_REAL_NONFILLS",
    "TOTAL_FILL_MARKOUT_LABELS",
)

THIS_IS_A_BUSINESS_ASSET = (
    "the rate at which this dataset grows is a business metric, not a "
    "vanity one: it bounds how fast every downstream question can be "
    "answered")


def retention(rows=(), orders=()):
    """Count the asset. Order-derived counts are zero only because no order
    has been placed -- which is a fact, not an absence of measurement."""
    events, markets = set(), set()
    tox = ext = 0
    span = []
    for r in rows or ():
        ev, mk = r.get("EVENT_ID"), r.get("MARKET_ID")
        if ev not in (None, NOT_IDENTIFIED):
            events.add(ev)
        if mk not in (None, NOT_IDENTIFIED):
            markets.add(mk)
        t = _parse(r.get("DECISION_TIMESTAMP_UTC"))
        if t:
            span.append((ev, t))
        if any(k.startswith("MARKET_STATE_TOXICITY") and r[k] != MISSING
               for k in r):
            tox += 1
        if r.get("EXTERNAL_SNAPSHOT_TIMESTAMP") not in (None, NOT_IDENTIFIED,
                                                        MISSING):
            ext += 1
    # Event-hours: per event, last minus first observation.
    by_ev = {}
    for ev, t in span:
        lo, hi = by_ev.get(ev, (t, t))
        by_ev[ev] = (min(lo, t), max(hi, t))
    hours = sum((hi - lo).total_seconds() for lo, hi in by_ev.values()) / 3600.0

    fills = sum(1 for o in orders or ()
                if (o.get("FILLED_SIZE") or 0) and float(o["FILLED_SIZE"]) > 0)
    return {
        "TOTAL_DECISION_STATES": len(rows or ()),
        "TOTAL_EVENT_HOURS": round(hours, 4),
        "TOTAL_INDEPENDENT_EVENTS": len(events),
        "TOTAL_MARKETS": len(markets),
        "TOTAL_MARKET_STATE_TOXICITY_LABELS": tox,
        "TOTAL_EXTERNAL_ALIGNED_STATES": ext,
        "TOTAL_REAL_ORDERS": len(orders or ()),
        "TOTAL_REAL_FILLS": fills,
        "TOTAL_REAL_NONFILLS": len(orders or ()) - fills,
        "TOTAL_FILL_MARKOUT_LABELS": sum(
            1 for o in orders or ()
            if o.get("MARKOUT_30S") not in (None, NOT_IDENTIFIED, MISSING)),
        "THIS_IS_A_BUSINESS_ASSET": THIS_IS_A_BUSINESS_ASSET,
    }


def growth(previous, current):
    """Month-over-month growth on every countable metric."""
    out = {}
    for k in RETENTION_METRICS:
        a = (previous or {}).get(k)
        b = (current or {}).get(k)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            out[k] = NOT_IDENTIFIED
            continue
        out[k] = {"FROM": a, "TO": b, "DELTA": round(b - a, 4),
                  "GROWTH_PCT": (round(100.0 * (b - a) / a, 3) if a
                                 else NOT_IDENTIFIED)}
    return out


def describe():
    return {
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "GRAIN": GRAIN,
        "APPEND_ONLY": APPEND_ONLY,
        "WHY_APPEND_ONLY": WHY_APPEND_ONLY,
        "IDENTIFIERS": IDENTIFIERS,
        "NATIVE_STATE_FIELDS": NATIVE_STATE_FIELDS,
        "DEPTH_IS_NEVER_FABRICATED": DEPTH_IS_NEVER_FABRICATED,
        "WHY_DERIVED_NOT_SUPPLIED": WHY_DERIVED_NOT_SUPPLIED,
        "HORIZONS_S": HORIZONS_S,
        "NO_INTERPOLATION_BEYOND_THE_HORIZON":
            NO_INTERPOLATION_BEYOND_THE_HORIZON,
        "HORIZON_TOLERANCE_S": HORIZON_TOLERANCE_S,
        "WHY_A_TOLERANCE": WHY_A_TOLERANCE,
        "V1_NOMINAL_REVISIT_S": V1_NOMINAL_REVISIT_S,
        "HORIZON_STATUS_V1": dict(HORIZON_STATUS_V1),
        "FIVE_SECOND_HORIZON_STATUS": FIVE_SECOND_HORIZON_STATUS,
        "WHY_5S_IS_UNOBSERVABLE": WHY_5S_IS_UNOBSERVABLE,
        "FORBIDDEN_5S_REPAIRS": FORBIDDEN_5S_REPAIRS,
        "WHY_NO_REPAIR": WHY_NO_REPAIR,
        "V2_DERIVES_CADENCE_FROM_HORIZONS": V2_DERIVES_CADENCE_FROM_HORIZONS,
        "MIN_LABEL_COVERAGE_PCT": MIN_LABEL_COVERAGE_PCT,
        "RAW_MOVEMENT_IS_INSUFFICIENT": RAW_MOVEMENT_IS_INSUFFICIENT,
        "RETENTION_METRICS": RETENTION_METRICS,
        "THIS_IS_A_BUSINESS_ASSET": THIS_IS_A_BUSINESS_ASSET,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
