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
  + tuple("EXECUTABLE_MOVE_%dS" % h for h in HORIZONS_S)

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

V2_DERIVES_CADENCE_FROM_HORIZONS = (
    "for V2, poll cadence is DERIVED from the horizons the experiment intends "
    "to measure -- not chosen first and then discovered to exclude one. V1 is "
    "not modified to fix this")

# A horizon must clear this before any model is scored on it.
MIN_LABEL_COVERAGE_PCT = 50.0


def horizon_label_coverage_gate(rows, horizon_s, min_coverage_pct=None):
    """May a model be evaluated on this horizon at all? Fails closed.

    Checks the DECLARED status first (a horizon unobservable by construction
    is refused regardless of what the rows happen to contain), then measured
    label coverage.
    """
    min_coverage_pct = (MIN_LABEL_COVERAGE_PCT if min_coverage_pct is None
                        else min_coverage_pct)
    declared = HORIZON_STATUS_V1.get(horizon_s)
    if declared and declared != "OBSERVABLE":
        return {
            "HORIZON_S": horizon_s,
            "HORIZON_STATUS": declared,
            "HORIZON_LABEL_COVERAGE_GATE": "FAIL",
            "MAY_EVALUATE": False,
            "RESULT": "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN",
            "WHY": WHY_5S_IS_UNOBSERVABLE if horizon_s == 5 else declared,
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
    ok = cov >= min_coverage_pct
    return {
        "HORIZON_S": horizon_s,
        "HORIZON_STATUS": declared or NOT_IDENTIFIED,
        "LABEL_COVERAGE_PCT": round(cov, 3),
        "MIN_LABEL_COVERAGE_PCT": min_coverage_pct,
        "LABELLED": present, "CANDIDATE_ROWS": total,
        "HORIZON_LABEL_COVERAGE_GATE": "PASS" if ok else "FAIL",
        "MAY_EVALUATE": ok,
        "RESULT": (None if ok
                   else "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"),
    }


WHY_A_TOLERANCE = (
    "the capture samples on a grid with a ~24 s nominal revisit, so an exact "
    "T+5s observation will rarely exist. A bounded tolerance is honest; "
    "widening it after seeing how many labels are missing is not")


def forward_observation(series, t0, horizon_s, tolerance_s=HORIZON_TOLERANCE_S,
                        time_key="DECISION_TIMESTAMP_UTC"):
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
    best, best_gap, best_off = None, None, None
    for r in series or ():
        t = _parse(r.get(time_key))
        if t is None or t <= base:
            continue                       # strictly after the origin
        off = (t - target).total_seconds()
        gap = abs(off)
        if gap > tolerance_s:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap, best_off = r, gap, off
    return best, best_off


def label_row(state_row, series, horizons_s=HORIZONS_S,
              tolerance_s=HORIZON_TOLERANCE_S):
    """Attach forward labels where future observations genuinely support them."""
    out = {}
    t0 = state_row.get("DECISION_TIMESTAMP_UTC")
    mid0 = _num(state_row.get("MID"))
    bid0, ask0 = (_num(state_row.get("BEST_BID")),
                  _num(state_row.get("BEST_ASK")))
    statuses, offsets = {}, {}
    for h in horizons_s:
        fwd, off = forward_observation(series, t0, h, tolerance_s)
        if fwd is None:
            statuses["%dS" % h] = MISSING
            for base in ("MID", "BEST_BID", "BEST_ASK",
                         "EXECUTABLE_BUY_PRICE", "EXECUTABLE_SELL_PRICE"):
                out["%s_T_PLUS_%dS" % (base, h)] = MISSING
            out["MID_MOVE_%dS" % h] = MISSING
            out["EXECUTABLE_MOVE_%dS" % h] = MISSING
            offsets["%dS" % h] = MISSING
            continue
        statuses["%dS" % h] = "PRESENT"
        offsets["%dS" % h] = round(off, 3)
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
        out["MID_MOVE_%dS" % h] = (round(m - mid0, 10)
                                   if (m is not None and mid0 is not None)
                                   else MISSING)
        # Executable move is round-trip-aware: buy at the ask now, sell at the
        # bid later. It is the move a maker could actually have realised.
        out["EXECUTABLE_MOVE_%dS" % h] = (round(b - ask0, 10)
                                          if (b is not None and ask0 is not None)
                                          else MISSING)
    out["LABEL_STATUS"] = statuses
    out["LABEL_REALISED_OFFSET_S"] = offsets
    out["LABEL_STATUS_OVERALL"] = ("PRESENT" if all(
        v == "PRESENT" for v in statuses.values()) else MISSING)
    out["NO_INTERPOLATION_BEYOND_THE_HORIZON"] = \
        NO_INTERPOLATION_BEYOND_THE_HORIZON
    out["HORIZON_TOLERANCE_S"] = tolerance_s
    return out


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
        if mv == MISSING or mv is None or not spread or spread <= 0:
            out[key] = MISSING
            for t in THRESHOLD_FIELDS:
                out["%s_%dS" % (t, h)] = MISSING
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
