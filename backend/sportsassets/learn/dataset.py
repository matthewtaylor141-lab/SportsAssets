"""THE FIRST LEARNING DATASET: entry -> complementary fill, within a horizon.

THE TARGET, stated narrowly on purpose:

    Given an OBSERVED ENTRY fill by cohort account A -- a BUY of outcome
    O in condition C at time T -- will A be OBSERVED making a
    COMPLEMENTARY fill (a BUY of the other outcome of C) within H?

WHAT THIS IS NOT, and the three must stay apart:

    it is NOT our fill probability     nobody quoted; no order of ours
                                       was ever in a queue
    it is NOT position closure         a complementary fill is one
                                       action, not the end of a
                                       position
    it is NOT profitability            no price we could have got, no
                                       fee we would have paid, no
                                       capital we would have committed

It is a behavioural prediction about somebody else's next observed
action, and every number it produces is labelled as one.

────────────────────────────────────────────────────────────────────
THE CLOCK RULE, AND WHY IT IS NOT `detected_at - ts`.

Run 82 found that 92.04% of chain-lane `detected_at - ts` differences
are NEGATIVE, and `obs/clock.py` states the consequence plainly: an
elapsed time cannot be negative, so those numbers were never elapsed
times. They are a subtraction across two clock domains -- `ts` is the
block producer's or the venue's, `detected_at` is ours -- with no
recorded offset between them.

So this module NEVER treats `detected_at - ts` as a latency, and never
reports a "forward lag" from it. What it does instead:

    AVAILABLE_AT = max(ts, detected_at)

and calls that a CONSERVATIVE CUTOFF, not an availability guarantee. It
is the earliest instant at which both clocks agree the row could have
been known. It does NOT establish that the row was persisted, indexed,
read by a decision process, or that the process was running. Where
`venue_seen_at` exists -- a venue-originated stamp, a third clock --
it is carried through so a later reader can do better than this rule.

Every feature is built from fills whose AVAILABLE_AT is strictly before
the decision instant. A fill that arrived at the same microsecond is
excluded, because "simultaneous" is not "already known".

────────────────────────────────────────────────────────────────────
FORWARD INGESTION IS IDENTIFIED BY PROVENANCE, NOT BY DATE.

Filtering on a recent `ts` excludes old fills; it does not exclude a
backfill that ran yesterday over last week's fills. The mode is a
property of the LANE:

    chain   the on-chain OrderFilled listener, live as blocks arrive
    s1      the S1 emitter, live
    poll    the venue Data API poller -- which BOTH tails live AND is
            what the reconciler uses to repair history, so a poll row
            is forward-ingested or backfilled and the source alone
            cannot say which

`ingestion/shadow_v2.py` already draws this line at
`LATE_POLL_ROW_S = 900.0` ("detected_at - ts beyond this = reconciler
artifact"). This module reuses that threshold rather than inventing a
second one, and labels every row's mode so the dataset can be split on
it instead of on a date.

────────────────────────────────────────────────────────────────────
WHAT IS NOT RECONSTRUCTED, AND SAYS SO.

`trades` holds FILLS. It does not hold merges, redemptions, transfers,
order submissions, cancellations or rejections. A position can
therefore leave our view by a mechanism this dataset cannot see, and
the honest handling is to carry that as unresolved rather than to close
a lifecycle from fill data alone. Every row records
`lifecycle_observability`, and the only value this module ever emits is
`FILLS_ONLY`.
"""
from __future__ import annotations

VERSION = "BETTOR_LEARN_DATASET_V1"

# `ingestion/shadow_v2.py`'s own threshold, reused rather than re-chosen.
LATE_POLL_ROW_S = 900.0

MODE_LIVE_LANE = "LIVE_LANE"          # chain / s1: a live listener
MODE_POLL_PROMPT = "POLL_PROMPT"      # poll, within the late threshold
MODE_POLL_LATE = "POLL_LATE"          # poll, beyond it: reconciler artifact
MODE_UNKNOWN = "UNKNOWN_LANE"

LIVE_LANES = ("chain", "s1")

# The only lifecycle claim this dataset is entitled to make.
LIFECYCLE_OBSERVABILITY = "FILLS_ONLY"

LABEL_COMPLEMENT = "COMPLEMENT_FILL_WITHIN_H"


def ingestion_mode(row) -> str:
    """Which lane a fill arrived on, and whether poll was prompt or late.

    THE SOURCE ALONE IS NOT THE MODE for the poll lane, because the
    poller both tails live and repairs history. A `chain` or `s1` row is
    a live listener by construction; a `poll` row is classified by the
    same threshold `ingestion/shadow_v2.py` uses.

    The comparison below IS a cross-clock subtraction and is used ONLY
    as a coarse classifier, never reported as a latency. A threshold of
    fifteen minutes is far outside any plausible clock offset, so it
    survives the domain mismatch that a per-second reading does not.
    """
    src = (row.get("source") or "").strip().lower()
    if src in LIVE_LANES:
        return MODE_LIVE_LANE
    if src != "poll":
        return MODE_UNKNOWN
    ts, det = row.get("ts"), row.get("detected_at")
    if ts is None or det is None:
        return MODE_UNKNOWN
    return (MODE_POLL_LATE if (float(det) - float(ts)) > LATE_POLL_ROW_S
            else MODE_POLL_PROMPT)


def available_at(row) -> float:
    """The CONSERVATIVE cutoff: the earliest instant both clocks admit.

    NOT AN AVAILABILITY GUARANTEE. It does not establish that the row
    was committed, indexed, read, or that any consumer was running. It
    is the weakest claim that is definitely true, which is the right
    thing to condition a feature on.
    """
    ts = float(row.get("ts") or 0.0)
    det = row.get("detected_at")
    return max(ts, float(det)) if det is not None else ts


def _key(row) -> tuple:
    return (row.get("account"), row.get("condition_id"))


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


LABEL_COMPLEMENT_CONDITIONAL = "COMPLEMENT_FILL_IN_REMAINDER_GIVEN_SURVIVAL"


def build(fills, *, horizon_s, observation_end, coverage_exclusions=(),
          min_prior_gap_s=0.0, elapsed_s=0.0) -> dict:
    """Turn a cohort fill stream into entry rows with censored labels.

    `fills`      dicts with account, condition_id, outcome_index, side,
                 price, size, ts, detected_at, source, and optionally
                 sport, venue, venue_seen_at.
    `horizon_s`  the horizon H the label is defined over.
    `observation_end`   the instant the data stops. An entry whose
                 T + H exceeds it is CENSORED, not negative.
    `coverage_exclusions`  (from, to) intervals during which ingestion
                 is not trusted. An entry inside one is dropped, and an
                 entry whose horizon OVERLAPS one is dropped too --
                 because a complementary fill could have occurred in
                 the hole and gone unrecorded, which would be scored as
                 a negative and is not one.

    EVERY FEATURE LOOKS BACKWARD FROM `available_at` OF THE ENTRY, and
    the prior fills it may consult are those whose own `available_at` is
    STRICTLY EARLIER. Equal instants are excluded: simultaneous is not
    already-known.
    """
    if horizon_s <= 0:
        raise ValueError("horizon_s must be positive")
    if elapsed_s < 0 or elapsed_s >= horizon_s:
        raise ValueError("elapsed_s must be in [0, horizon_s)")
    end = float(observation_end)
    excl = [(float(a), float(b)) for a, b in coverage_exclusions]

    rows = []
    for f in fills:
        r = dict(f)
        r["_ts"] = _num(r.get("ts"))
        r["_avail"] = available_at(r)
        r["_mode"] = ingestion_mode(r)
        r["_oi"] = int(_num(r.get("outcome_index"), -1))
        r["_side"] = (r.get("side") or "").strip().upper()
        rows.append(r)
    # Sorted by the instant WE could know it, then by the event instant,
    # because a feature is built from what was available, not from what
    # had happened.
    rows.sort(key=lambda r: (r["_avail"], r["_ts"]))

    # Per (account, condition) running state, folded forward.
    state = {}
    out, skipped = [], {"non_buy": 0, "no_condition": 0, "unknown_outcome": 0,
                        "in_excluded_window": 0, "horizon_overlaps_gap": 0,
                        "completed_before_elapsed": 0}

    # First pass: index each (account, condition) fill list for lookahead.
    by_key = {}
    for r in rows:
        by_key.setdefault(_key(r), []).append(r)

    for i, r in enumerate(rows):
        if r["_side"] != "BUY":
            skipped["non_buy"] += 1
            continue
        if not r.get("condition_id"):
            skipped["no_condition"] += 1
            continue
        if r["_oi"] not in (0, 1):
            # A non-binary or unlabelled outcome has no complement this
            # module can name. It is not guessed.
            skipped["unknown_outcome"] += 1
            continue

        k = _key(r)
        st = state.setdefault(k, {"n": 0, "qty": [0.0, 0.0],
                                  "cost": [0.0, 0.0], "last_avail": None,
                                  "first_avail": None})
        t0 = r["_avail"]

        # ── the decision row, if this is an ENTRY ────────────────────
        # An ENTRY is a buy that opens or adds to the leg while the
        # account does NOT yet hold the complement. Once the complement
        # is held the question has already been answered for this
        # position, and including those rows would leak the label.
        comp = 1 - r["_oi"]
        is_entry = st["qty"][comp] <= 0.0

        if is_entry:
            in_gap = any(a <= t0 <= b for a, b in excl)
            horizon_end = t0 + horizon_s
            overlaps = any(not (b <= t0 or a >= horizon_end) for a, b in excl)
            if in_gap:
                skipped["in_excluded_window"] += 1
            elif overlaps:
                skipped["horizon_overlaps_gap"] += 1
            else:
                row = _row(r, st, by_key[k], horizon_s, end, elapsed_s)
                if row is not None:
                    out.append(row)
                else:
                    skipped["completed_before_elapsed"] += 1

        # ── fold this fill into the running state, AFTER the row ────
        st["n"] += 1
        st["qty"][r["_oi"]] += _num(r.get("size"))
        st["cost"][r["_oi"]] += _num(r.get("size")) * _num(r.get("price"))
        st["last_avail"] = t0
        if st["first_avail"] is None:
            st["first_avail"] = t0

    n_pos = sum(1 for o in out if o["label"] == 1)
    n_cens = sum(1 for o in out if o["censored"])
    return {
        "version": VERSION,
        "target": (LABEL_COMPLEMENT if elapsed_s <= 0.0
                   else LABEL_COMPLEMENT_CONDITIONAL),
        "elapsed_s": float(elapsed_s),
        "conditional_note": (
            "UNCONDITIONAL: the label covers the whole horizon from the "
            "entry, and a prediction using it is only valid if it is made "
            "AT the entry." if elapsed_s <= 0.0 else
            "CONDITIONAL. Every row here survived %.0f s after its entry "
            "with no complementary fill, and the label covers only the "
            "REMAINING %.0f s. This is the target a prediction made "
            "mid-horizon actually answers; scoring such a prediction "
            "against the unconditional target credits it with information "
            "it already had." % (elapsed_s, horizon_s - elapsed_s)),
        "horizon_s": float(horizon_s),
        "observation_end": end,
        "coverage_exclusions": [list(x) for x in excl],
        "lifecycle_observability": LIFECYCLE_OBSERVABILITY,
        "what_is_not_reconstructed":
            "trades holds FILLS. Merges, redemptions, transfers, order "
            "submissions, cancellations and rejections are not in it. A "
            "position can leave our view by a mechanism this dataset "
            "cannot see, so no lifecycle is closed from fill data alone.",
        "clock_note":
            "AVAILABLE_AT = max(ts, detected_at) is a conservative "
            "cutoff, not an availability guarantee: it does not "
            "establish persistence, indexing, or that a consumer was "
            "running. detected_at - ts is NEVER used as a latency -- "
            "run 82 found 92.04% of chain-lane differences negative, "
            "and obs/clock.py records why that cannot be repaired.",
        "rows": out,
        "n_rows": len(out),
        "n_positive": n_pos,
        "n_censored": n_cens,
        "base_rate_uncensored": (
            n_pos / float(len(out) - n_cens) if len(out) > n_cens else None),
        "skipped": skipped,
    }


FEATURES = (
    "entry_price",
    "entry_price_dist_from_half",
    "entry_size_log",
    "entry_notional_log",
    "is_outcome_one",
    "prior_fills_this_market",
    "prior_qty_same_leg_log",
    "prior_cost_same_leg_log",
    "seconds_since_prev_fill_this_market_log",
    "seconds_since_first_fill_this_market_log",
    "is_first_fill_in_market",
    "complement_price_if_symmetric",
    "hour_of_day_sin",
    "hour_of_day_cos",
)


def _features(r, st) -> dict:
    """Decision-time features. Shared by both targets, so the
    conditional and unconditional rows cannot drift apart."""
    import math
    t0 = r["_avail"]
    price = _num(r.get("price"))
    size = _num(r.get("size"))
    gap_prev = (t0 - st["last_avail"]) if st["last_avail"] is not None else None
    gap_first = (t0 - st["first_avail"]) if st["first_avail"] is not None \
        else None
    hour = (t0 % 86400.0) / 86400.0
    return {
        "entry_price": price,
        "entry_price_dist_from_half": abs(price - 0.5),
        "entry_size_log": math.log1p(max(0.0, size)),
        "entry_notional_log": math.log1p(max(0.0, size * price)),
        "is_outcome_one": 1.0 if r["_oi"] == 1 else 0.0,
        "prior_fills_this_market": float(st["n"]),
        "prior_qty_same_leg_log": math.log1p(max(0.0, st["qty"][r["_oi"]])),
        "prior_cost_same_leg_log": math.log1p(max(0.0, st["cost"][r["_oi"]])),
        # A MISSING GAP IS NOT A ZERO GAP. The first fill in a market has
        # no previous one, so the gap features carry a sentinel AND an
        # indicator, and the model sees both rather than being told the
        # gap was instantaneous.
        "seconds_since_prev_fill_this_market_log":
            math.log1p(max(0.0, gap_prev)) if gap_prev is not None else 0.0,
        "seconds_since_first_fill_this_market_log":
            math.log1p(max(0.0, gap_first)) if gap_first is not None else 0.0,
        "is_first_fill_in_market": 1.0 if st["n"] == 0 else 0.0,
        # What the pair would cost if the complement traded at its
        # symmetric price. NOT a book reading: we have no book here, and
        # this is a reference level, not a quote.
        "complement_price_if_symmetric": 1.0 - price,
        "hour_of_day_sin": math.sin(2.0 * math.pi * hour),
        "hour_of_day_cos": math.cos(2.0 * math.pi * hour),
    }


def _row(r, st, siblings, horizon_s, end, elapsed_s=0.0):
    """One decision row, or None if it left the risk set before
    `elapsed_s` under the conditional target."""
    t0 = r["_avail"]
    comp = 1 - r["_oi"]

    # THE LABEL, looked up STRICTLY forward. A complementary BUY whose
    # own AVAILABLE_AT falls in (t0, t0 + H]. Using `ts` here instead
    # would count a fill we could not have known about at the time we
    # claim to have observed it.
    hit_at = None
    for s in siblings:
        if s is r or s["_side"] != "BUY" or s["_oi"] != comp:
            continue
        if t0 < s["_avail"] <= t0 + horizon_s:
            if hit_at is None or s["_avail"] < hit_at:
                hit_at = s["_avail"]

    # ── THE CONDITIONAL TARGET ──────────────────────────────────────
    #
    # A prediction written `elapsed_s` after the entry knows one thing
    # an entry-time forecaster does not: that nothing has completed
    # yet. Scoring it against the whole-horizon label credits it with
    # that knowledge. So under `elapsed_s` a row that ALREADY completed
    # inside the elapsed window is not a row at all -- it is a subject
    # that left the risk set -- and the label covers only what remains.
    if elapsed_s > 0.0 and hit_at is not None and hit_at <= t0 + elapsed_s:
        return None

    censored = (t0 + horizon_s) > end and hit_at is None
    label = 1 if hit_at is not None else 0
    window_from = t0 + elapsed_s

    return {
        "account": r.get("account"),
        "condition_id": r.get("condition_id"),
        "market_slug": r.get("market_slug"),
        "sport": r.get("sport"),
        "outcome_index": r["_oi"],
        "entry_at": t0,
        # THE INSTANT THE PREDICTION IS ENTITLED TO BE MADE. Equal to
        # the entry under the unconditional target; `elapsed_s` later
        # under the conditional one.
        "decision_at": window_from,
        "feature_cutoff_at": t0,
        "elapsed_s": float(elapsed_s),
        "label_window": [window_from, t0 + horizon_s],
        "remaining_s": float(horizon_s - elapsed_s),
        "event_ts": r["_ts"],
        "ingestion_mode": r["_mode"],
        "source": r.get("source"),
        "venue_seen_at": r.get("venue_seen_at"),
        "features": _features(r, st),
        "label": label,
        "label_name": (LABEL_COMPLEMENT if elapsed_s <= 0.0
                       else LABEL_COMPLEMENT_CONDITIONAL),
        "time_to_event_s": (hit_at - t0) if hit_at is not None else None,
        "censored": censored,
        "horizon_s": float(horizon_s),
        "lifecycle_observability": LIFECYCLE_OBSERVABILITY,
        # THE GROUPING KEY FOR SPLITS. Entries in the same condition are
        # not independent observations -- one event drives all of them --
        # so a split that cuts through a condition leaks.
        "group_key": r.get("condition_id"),
    }


def split_by_time(ds, *, train_end, calib_end) -> dict:
    """Chronological split with the event-overlap rule enforced.

    THREE PARTS, IN TIME ORDER, because a calibrator fitted on the rows
    the model memorised measures memory, not calibration:

        TRAIN    decision_at <  train_end
        CALIB    train_end   <= decision_at < calib_end
        EVAL     calib_end   <= decision_at

    AND A CONDITION MAY APPEAR IN ONLY ONE PART. Twelve entries in one
    market are twelve views of one event; letting some land in train and
    the rest in eval is the leak that makes a useless model look
    excellent. A condition that straddles a boundary is assigned to the
    EARLIEST part it appears in and its later rows are DROPPED, with the
    count reported -- dropping is the conservative direction, because
    the alternative moves future rows into training.
    """
    first_part = {}
    for r in ds["rows"]:
        t = r["decision_at"]
        part = ("TRAIN" if t < train_end
                else "CALIB" if t < calib_end else "EVAL")
        g = r["group_key"]
        if g not in first_part:
            first_part[g] = part

    parts = {"TRAIN": [], "CALIB": [], "EVAL": []}
    dropped = 0
    for r in ds["rows"]:
        t = r["decision_at"]
        part = ("TRAIN" if t < train_end
                else "CALIB" if t < calib_end else "EVAL")
        if first_part[r["group_key"]] != part:
            dropped += 1
            continue
        parts[part].append(r)

    return {
        "train_end": float(train_end), "calib_end": float(calib_end),
        "parts": parts,
        "counts": {k: len(v) for k, v in parts.items()},
        "positives": {k: sum(1 for r in v if r["label"] == 1)
                      for k, v in parts.items()},
        "censored": {k: sum(1 for r in v if r["censored"])
                     for k, v in parts.items()},
        "conditions": {k: len({r["group_key"] for r in v})
                       for k, v in parts.items()},
        "dropped_for_group_overlap": dropped,
        "rule": "a condition appears in exactly one part; rows of a "
                "straddling condition outside its earliest part are "
                "DROPPED rather than reassigned",
    }


def uncensored(rows) -> list:
    """Rows whose label is decided. Censored rows are NOT negatives.

    A censored row's horizon ran past the end of the data, so nobody
    knows whether the complementary fill happened. Scoring it as 0 would
    manufacture negatives out of the most recent -- and therefore most
    relevant -- part of the sample.
    """
    return [r for r in rows if not r["censored"]]


def xy(rows):
    return ([r["features"] for r in rows], [float(r["label"]) for r in rows])
