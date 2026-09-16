#!/usr/bin/env python3
"""RUN 85 PHASE 2 -- MULTI-DAY OBSERVATIONAL CAPTURE. READ ONLY. NO CREDENTIAL.

One SEGMENT of the frozen capture. The segment is the unit that can be sealed;
the capture is the union of segments. Nothing here selects, re-selects, scores
or substitutes a market: the cohort is loaded from the committed frozen file
and used verbatim.

--------------------------------------------------------------------------
WHY SEGMENTS
--------------------------------------------------------------------------
A GitHub Actions job is capped at six hours, so a seven-day continuous run
cannot live in one process. The capture is therefore a sequence of
independently sealed segments. The gap between two segments is RECORDED as a
gap -- SEGMENT_START_UTC and SEGMENT_END_UTC are written into every segment's
manifest, so the union has honest holes rather than hidden ones. A completed
segment is never reopened, never overwritten, never edited.

--------------------------------------------------------------------------
THE GRID -- WHY THE HORIZONS ARE EXACT
--------------------------------------------------------------------------
The safe operating point is 0.4 rps nominal, i.e. one request every 2.5 s.
Every horizon the specification asks for is an even multiple of 2.5 s:

        5 s = 2 slots   10 s = 4 slots   30 s = 12 slots   60 s = 24 slots

so the exact-horizon requirement and the rate floor are compatible by
construction; no interpolation is ever needed and none is ever done.

One OBSERVATION SET for one market occupies ten slots of the grid:

  slot   0   1   2   3   4   5  ...  12  13  ...  24  25
  route  bk bbo  bk bbo  bk bbo      bk bbo      bk bbo
  h(s)   0   0   5   5  10  10       30  30      60  60

The paired /bbo read sits one slot (2.5 s) after its /book read. It is the
freshness instrument: /book and /bbo are independently served, so agreement on
the touch is cross-route corroboration. It is NOT proof -- a shared upstream
would agree too -- and the 2.5 s offset means a disagreement can also be real
movement. Both facts are recorded with the counts.

A new set begins every 14 slots (35 s). Checked exhaustively (see the tests):
with the leg pattern above, 14 is the smallest period for which no two sets
ever want the same slot, and it is the maximum-density collision-free packing
that exists for this pattern -- 20 of every 28 slots carry a request. The
effective rate is therefore 0.714 * 0.4 = 0.286 rps, comfortably under the
nominal ceiling, and the ceiling is never raised to buy more samples.

Markets are taken round robin, so with a 12-market cohort each market is
revisited every 12 * 35 s = 7 minutes.

--------------------------------------------------------------------------
MARKOUT SEMANTICS -- CARRIED FORWARD FROM PHASE 2F, UNCHANGED
--------------------------------------------------------------------------
  A HORIZON_OBSERVATION_AVAILABLE  a trustworthy snapshot was obtained at the
                                   target horizon
  B BOOK_CHANGED_BY_HORIZON        descriptive. NEVER a filter
  C MARKOUT                        the price change. An unchanged book with a
                                   trustworthy snapshot is MARKOUT = 0. That
                                   is DATA, not missingness, and it is kept

The primary dataset is conditioned on NOTHING. Not on the book moving, not on
the price being touched, not on the sign of the outcome. Zero markouts are
retained. The touched and crossed registers are kept SEPARATELY and are never
allowed to become the primary set.

--------------------------------------------------------------------------
PASSIVE OPPORTUNITY -- A TOUCH IS NOT A FILL
--------------------------------------------------------------------------
For a set whose t0 book is two sided, a hypothetical passive BUY rests at
best_bid(t0) and a hypothetical passive SELL rests at best_ask(t0).

  PASSIVE_QUOTE_POSTABLE   both touches exist at t0
  PASSIVE_PRICE_TOUCHED    BUY: best_ask(t+h) <= p_bid
                           SELL: best_bid(t+h) >= p_ask
  PASSIVE_PRICE_CROSSED    strict inequality of the same
  PASSIVE_TOUCH_PROXY      the touched label, and nothing more
  PASSIVE_FILL_PROBABILITY NOT_IDENTIFIED, unconditionally

Queue position, our own order's effect on the book, and the venue's matching
rules are all unobserved from a public read, so no fill is claimed. Two touched
prices in the same set are recorded as PAIR_BOTH_TOUCHED and are NOT converted
into a completed pair.

--------------------------------------------------------------------------
NO PROFIT CLAIM
--------------------------------------------------------------------------
Nothing in this file computes NET_EXPECTANCY, NET_ROI, DEPLOYABLE_EDGE or
GUARANTEED_PROFIT, and PMUS_FEES_RESOLVED is still NO -- the 0.06 coefficient
carried on the discovery rows is a FIELD, not an established fee rule. Markouts
are raw price differences in probability units.

--------------------------------------------------------------------------
RETIREMENT -- RECORDED, WITHIN SEGMENT, NEVER REPLACED
--------------------------------------------------------------------------
A subject stops being read when the venue says it is over, not when it looks
unattractive. Frozen rules:

  VENUE_TERMINAL_STATE      a state token in TERMINAL_STATES -> retire at once
  MARKET_NOT_FOUND          3 consecutive 404s -> retire
  SUSTAINED_NON_OPEN_STATE  40 consecutive sets not MARKET_STATE_OPEN
  SUSTAINED_NO_TWO_SIDED_BOOK  40 consecutive sets with no two-sided t0 book

Retirement is scoped WITHIN the segment. The next segment re-reads every
cohort member from scratch, so a transient outage never permanently discards a
subject, while a genuinely finished market retires again immediately. A retired
market's slots go IDLE. They are not handed to another market -- reallocating
mid-capture would be exactly the mid-capture optimisation the specification
forbids. No market is ever substituted.

--------------------------------------------------------------------------
CAPABILITY BOUNDARY
--------------------------------------------------------------------------
GET only. Public gateway only. No credential, no wallet, no signer, no auth
header, no trading SDK, no order or cancel route, no POST/PUT/PATCH/DELETE.
Retry-After is honoured exactly and the rate is never raised. mirror_live stays
false and nothing here can reach it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import platform
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

_c = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_c)
_c.loader.exec_module(C)

_b = importlib.util.spec_from_file_location(
    "run85b", Path(__file__).with_name("run85_phase2b.py"))
B = importlib.util.module_from_spec(_b)
_b.loader.exec_module(B)

PHASE = "run85/phase2capture/1"

# ------------------------------------------------------------ frozen rate
NOMINAL_RPS = 0.4
SPACING_S = 1.0 / NOMINAL_RPS                 # 2.5 s, the grid slot
MAX_429_BEFORE_STOP = 5

# ------------------------------------------------------------ frozen grid
HORIZONS = (5.0, 10.0, 30.0, 60.0)
# (slot offset within the set, route, target horizon in seconds)
LEGS = (
    (0, "book", 0.0), (1, "bbo", 0.0),
    (2, "book", 5.0), (3, "bbo", 5.0),
    (4, "book", 10.0), (5, "bbo", 10.0),
    (12, "book", 30.0), (13, "bbo", 30.0),
    (24, "book", 60.0), (25, "bbo", 60.0),
)
SET_PERIOD_SLOTS = 14          # a new set begins every 14 slots (35 s)
SET_SPAN_SLOTS = 26            # max leg offset + 1
HORIZON_TOLERANCE_S = 0.5      # labelling only; nothing is ever dropped for it

# ------------------------------------------------------- frozen retirement
OPEN_STATES = ("MARKET_STATE_OPEN",)
TERMINAL_STATES = ("MARKET_STATE_CLOSED", "MARKET_STATE_RESOLVED",
                   "MARKET_STATE_SETTLED", "MARKET_STATE_CANCELED",
                   "MARKET_STATE_CANCELLED", "MARKET_STATE_EXPIRED")
NOT_FOUND_STREAK = 3
NON_OPEN_STREAK = 40
NO_BOOK_STREAK = 40

BOOK_PATH = "/v1/markets/%s/book"
BBO_PATH = "/v1/markets/%s/bbo"

COHORT_FILE = "run85_phase2_frozen_cohort.json"
COHORT_SHA256 = "8cdedf26479a309bf7e22c292ebdd2f562673e2b8dccb2ec2793d9ad29187efe"


def say(s=""):
    print(s)
    sys.stdout.flush()


# --------------------------------------------------------------- the plan
def set_slots(set_index):
    """Absolute grid slots for one observation set, as {slot: (route, h)}."""
    base = set_index * SET_PERIOD_SLOTS
    return {base + off: (route, h) for off, route, h in LEGS}


def plan_grid(n_sets):
    """{slot: (set_index, route, horizon)} for the first n_sets sets.

    Raises if two sets ever want the same slot. That assertion is the whole
    argument for SET_PERIOD_SLOTS: it is checked, not asserted in prose.
    """
    grid = {}
    for i in range(n_sets):
        for slot, (route, h) in set_slots(i).items():
            if slot in grid:
                raise RuntimeError(
                    "GRID_COLLISION slot=%d sets=%s,%s" % (slot, grid[slot][0], i))
            grid[slot] = (i, route, h)
    return grid


def sets_in_window(duration_s):
    """How many sets fit in a segment of duration_s, last set fully completed."""
    slots = int(duration_s // SPACING_S)
    if slots < SET_SPAN_SLOTS:
        return 0
    return (slots - SET_SPAN_SLOTS) // SET_PERIOD_SLOTS + 1


# ------------------------------------------------------------- parsing
def touch_of(body):
    """(best_bid, best_ask) from a /book ladder. Either may be None."""
    d = B.market_data(body) or {}
    bids = B.ladder(d.get("bids"))
    offers = B.ladder(d.get("offers"))
    bb = max((p for p, _, _ in bids), default=None)
    ba = min((p for p, _, _ in offers), default=None)
    return bb, ba


def bbo_touch(body):
    """(best_bid, best_ask) from a /bbo response, which carries them directly."""
    d = B.market_data(body) or {}
    bb, _ = B.amount(d.get("bestBid"))
    ba, _ = B.amount(d.get("bestAsk"))
    return bb, ba


def ladder_hash(body):
    """A stable digest of the executable ladder only.

    transactTime and the stats block are excluded on purpose: transactTime is
    the venue's periodic price mark (resolved in Phase 2F), so including it
    would make an unchanged book look changed.
    """
    d = B.market_data(body) or {}
    payload = json.dumps(
        {"bids": [[str(p), str(q), c] for p, q, c in B.ladder(d.get("bids"))],
         "offers": [[str(p), str(q), c] for p, q, c in B.ladder(d.get("offers"))]},
        sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def observation(row, slug, route, target_h, actual_h):
    """The derived record for one leg. The raw row is retained separately."""
    body = row.get("body")
    d = B.market_data(body) or {}
    got_slug = d.get("marketSlug")
    obs = {
        "market_slug": slug,
        "route": route,
        "target_horizon_s": target_h,
        "actual_horizon_s": actual_h,
        "horizon_error_s": (None if actual_h is None else actual_h - target_h),
        "http_status": row.get("http_status"),
        "error": row.get("error"),
        "local_request_wall_utc": row.get("local_request_wall_utc"),
        "local_request_monotonic_ns": row.get("local_request_monotonic_ns"),
        "latency_ms": row.get("latency_ms"),
        "response_sha256": row.get("response_sha256"),
        "response_bytes": row.get("response_bytes"),
        "response_slug": got_slug,
        "identity_ok": (got_slug == slug) if got_slug is not None else None,
        "venue_state": d.get("state"),
        "venue_transact_time": d.get("transactTime"),
    }
    if route == "book":
        bb, ba = touch_of(body)
        obs["best_bid"] = str(bb) if bb is not None else None
        obs["best_ask"] = str(ba) if ba is not None else None
        obs["two_sided"] = (bb is not None and ba is not None)
        obs["ladder_sha256"] = ladder_hash(body) if d else None
        obs["bid_levels"] = len(B.ladder(d.get("bids")))
        obs["ask_levels"] = len(B.ladder(d.get("offers")))
    else:
        bb, ba = bbo_touch(body)
        obs["best_bid"] = str(bb) if bb is not None else None
        obs["best_ask"] = str(ba) if ba is not None else None
        obs["two_sided"] = (bb is not None and ba is not None)
        obs["bid_depth"] = d.get("bidDepth")
        obs["ask_depth"] = d.get("askDepth")
        obs["shares_traded"] = d.get("sharesTraded")
        obs["open_interest"] = d.get("openInterest")
    return obs


def _dec(s):
    return None if s is None else Decimal(s)


def _num(x):
    """One canonical decimal spelling, so a zero markout is always "0".

    Decimal subtraction keeps the operands' exponent, so 0.505 - 0.505 is
    "0.000" and 0.50 - 0.50 is "0.00". Three spellings of zero in the dataset
    would make "was the markout zero?" a string question instead of a numeric
    one, and the zeros are the part of the dataset most easily lost.
    """
    if x is None:
        return None
    return format(x.normalize(), "f")


# ------------------------------------------------------------ derivation
def derive_set(legs):
    """Everything the specification asks a completed set to carry.

    `legs` is {(route, horizon): observation}. Nothing here filters: a set with
    a missing leg is reported with the leg missing, never interpolated and
    never quietly dropped.
    """
    book = {h: legs.get(("book", h)) for h in (0.0,) + HORIZONS}
    bbo = {h: legs.get(("bbo", h)) for h in (0.0,) + HORIZONS}
    t0 = book.get(0.0)

    rec = {
        "legs_observed": sum(1 for o in book.values() if o and o.get("http_status") == 200),
        "legs_expected": len(book),
        "identity_failures": sum(1 for o in book.values()
                                 if o and o.get("identity_ok") is False),
        "horizon_error_max_s": None,
        "horizon_within_tolerance": None,
        "valid_horizon_set": False,
        "passive_quote_postable": False,
        "passive_fill_probability": "NOT_IDENTIFIED",
        "horizons": {},
    }

    errs = [abs(o["horizon_error_s"]) for o in book.values()
            if o and o.get("horizon_error_s") is not None]
    if errs:
        rec["horizon_error_max_s"] = max(errs)
        rec["horizon_within_tolerance"] = max(errs) <= HORIZON_TOLERANCE_S

    all_book_ok = all(o is not None and o.get("http_status") == 200
                      and o.get("ladder_sha256") is not None
                      and o.get("identity_ok") is not False
                      for o in book.values())
    rec["valid_horizon_set"] = bool(all_book_ok and rec["horizon_within_tolerance"])

    if t0 is None:
        return rec

    rec["market_slug"] = t0.get("market_slug")
    rec["t0_wall_utc"] = t0.get("local_request_wall_utc")
    rec["t0_state"] = t0.get("venue_state")

    p_bid, p_ask = _dec(t0.get("best_bid")), _dec(t0.get("best_ask"))
    rec["t0_best_bid"] = t0.get("best_bid")
    rec["t0_best_ask"] = t0.get("best_ask")
    postable = p_bid is not None and p_ask is not None
    rec["passive_quote_postable"] = postable
    if postable:
        rec["t0_mid"] = _num((p_bid + p_ask) / 2)
        rec["t0_spread"] = _num(p_ask - p_bid)

    # cross-route corroboration, one pair per horizon, lag 2.5 s and said so
    agree = dis = absent = 0
    for h in (0.0,) + HORIZONS:
        ob, oq = book.get(h), bbo.get(h)
        if not ob or not oq or ob.get("http_status") != 200 or oq.get("http_status") != 200:
            absent += 1
            continue
        if ob.get("best_bid") == oq.get("best_bid") and ob.get("best_ask") == oq.get("best_ask"):
            agree += 1
        else:
            dis += 1
    rec["cross_route_agree"] = agree
    rec["cross_route_disagree"] = dis
    rec["cross_route_absent"] = absent
    rec["cross_route_lag_s"] = SPACING_S

    for h in HORIZONS:
        oh = book.get(h)
        hr = {"horizon_observation_available": bool(
            oh and oh.get("http_status") == 200 and oh.get("ladder_sha256") is not None)}
        rec["horizons"][str(h)] = hr
        if not hr["horizon_observation_available"]:
            continue
        hr["book_changed_by_horizon"] = (
            oh.get("ladder_sha256") != t0.get("ladder_sha256"))
        hr["horizon_error_s"] = oh.get("horizon_error_s")
        hb, ha = _dec(oh.get("best_bid")), _dec(oh.get("best_ask"))
        hr["best_bid"] = oh.get("best_bid")
        hr["best_ask"] = oh.get("best_ask")

        # markouts. Present whenever both endpoints exist -- including zero.
        if p_bid is not None and hb is not None:
            hr["touch_bid_markout"] = _num(hb - p_bid)
        if p_ask is not None and ha is not None:
            hr["touch_ask_markout"] = _num(ha - p_ask)
        if postable and hb is not None and ha is not None:
            mid0 = (p_bid + p_ask) / 2
            midh = (hb + ha) / 2
            hr["mid_markout"] = _num(midh - mid0)
            hr["buy_markout_vs_mid"] = _num(midh - p_bid)
            hr["sell_markout_vs_mid"] = _num(p_ask - midh)

        if postable:
            hr["buy_touched"] = (ha is not None and ha <= p_bid)
            hr["buy_crossed"] = (ha is not None and ha < p_bid)
            hr["sell_touched"] = (hb is not None and hb >= p_ask)
            hr["sell_crossed"] = (hb is not None and hb > p_ask)
            hr["passive_touch_proxy"] = bool(hr["buy_touched"] or hr["sell_touched"])
            # recorded, and explicitly NOT a completed pair
            hr["pair_both_touched"] = bool(hr["buy_touched"] and hr["sell_touched"])
            hr["pair_completed"] = "NOT_IDENTIFIED"
    return rec


# -------------------------------------------------------------- the run
class Subject:
    def __init__(self, entry):
        self.slug = entry["market_slug"]
        self.entry = entry
        self.retired_reason = None
        self.retired_at = None
        self.not_found = 0
        self.non_open = 0
        self.no_book = 0
        self.sets = 0

    def note_set(self, rec):
        """Frozen retirement rules. Nothing here is about attractiveness."""
        self.sets += 1
        state = rec.get("t0_state")
        if state in TERMINAL_STATES:
            return "VENUE_TERMINAL_STATE:%s" % state
        if state in OPEN_STATES:
            self.non_open = 0
        else:
            self.non_open += 1
            if self.non_open >= NON_OPEN_STREAK:
                return "SUSTAINED_NON_OPEN_STATE:%s" % state
        if rec.get("passive_quote_postable"):
            self.no_book = 0
        else:
            self.no_book += 1
            if self.no_book >= NO_BOOK_STREAK:
                return "SUSTAINED_NO_TWO_SIDED_BOOK"
        return None


def run_segment(http, subjects, duration_s, log_fh, res):
    n_sets = sets_in_window(duration_s)
    grid = plan_grid(n_sets)
    res["planned_sets"] = n_sets
    res["planned_requests"] = len(grid)
    res["idle_slots"] = (max(grid) + 1 - len(grid)) if grid else 0

    pacer = B.AdaptivePacer(base=SPACING_S)
    open_sets = {}          # set_index -> {(route, h): obs}
    set_t0_mono = {}        # set_index -> monotonic of its t0 request start
    done = []
    n429 = 0
    ident_fail = 0
    stop = None
    reanchors = []

    t0 = time.monotonic()
    deadline = t0 + duration_s
    last_start = None

    for slot in range(max(grid) + 1 if grid else 0):
        entry = grid.get(slot)
        if entry is None:
            continue
        set_index, route, target_h = entry
        subj = subjects[set_index % len(subjects)]

        if subj.retired_reason is not None:
            continue                      # slot goes idle; never reassigned

        target_t = t0 + slot * SPACING_S
        if target_t > deadline:
            stop = stop or "SEGMENT_DEADLINE"
            break

        now = time.monotonic()
        # the rate floor is absolute: never two starts closer than SPACING_S
        floor_t = (last_start + SPACING_S) if last_start is not None else now
        fire_at = max(target_t, floor_t)
        if fire_at > now:
            time.sleep(fire_at - now)

        start = time.monotonic()
        last_start = start
        path = (BOOK_PATH if route == "book" else BBO_PATH) % subj.slug
        row = C._get(http, path, None)
        log_fh.write(json.dumps(row, default=str) + "\n")

        if target_h == 0.0 and route == "book":
            set_t0_mono[set_index] = start
        anchor = set_t0_mono.get(set_index)
        actual_h = (start - anchor) if anchor is not None else None
        obs = observation(row, subj.slug, route, target_h, actual_h)
        open_sets.setdefault(set_index, {})[(route, target_h)] = obs

        if obs.get("identity_ok") is False:
            ident_fail += 1
            stop = "IDENTITY_FAILURE"
            break

        if row.get("http_status") == 429:
            n429 += 1
            pacer.on_429((row.get("response_headers") or {}).get("retry-after"))
            last_start = time.monotonic()
            reanchors.append({"slot": slot, "set": set_index})
            if n429 >= MAX_429_BEFORE_STOP:
                stop = "RATE_LIMIT"
                break
        elif row.get("http_status") == 404:
            subj.not_found += 1
            if subj.not_found >= NOT_FOUND_STREAK:
                subj.retired_reason = "MARKET_NOT_FOUND"
                subj.retired_at = row.get("local_request_wall_utc")
        elif row.get("http_status") == 200:
            subj.not_found = 0
            pacer.on_success()

        # a set closes when its last leg has been attempted
        if (target_h, route) == (60.0, "bbo"):
            rec = derive_set(open_sets.pop(set_index, {}))
            rec["set_index"] = set_index
            rec["market_slug"] = subj.slug
            rec["sport_bucket"] = subj.entry.get("sport_bucket")
            rec["native_event_id"] = subj.entry.get("native_event_id")
            done.append(rec)
            set_t0_mono.pop(set_index, None)
            why = subj.note_set(rec)
            if why and subj.retired_reason is None:
                subj.retired_reason = why
                subj.retired_at = rec.get("t0_wall_utc")

        if all(s.retired_reason is not None for s in subjects):
            stop = "ALL_SUBJECTS_RETIRED"
            break

    res["stop_reason"] = stop or "PLAN_EXHAUSTED"
    res["sets_started"] = len(done) + len(open_sets)
    res["sets_completed"] = len(done)
    res["sets_abandoned_at_segment_end"] = len(open_sets)
    res["http_429"] = n429
    res["identity_failures"] = ident_fail
    res["grid_reanchors"] = reanchors
    res["throttle_events"] = pacer.events
    return done


# ----------------------------------------------------------------- report
def report(res, subjects, done, meta):
    L = []

    def w(s=""):
        L.append(s)
        say(s)

    w("=== RUN 85 PHASE 2 OBSERVATIONAL CAPTURE -- SEGMENT %s ===" % meta["segment"])
    w("SEGMENT_START_UTC = %s" % meta["segment_start_utc"])
    w("SEGMENT_END_UTC   = %s" % meta["segment_end_utc"])
    w("COHORT_SHA256 = %s" % meta["cohort_sha256"])
    w("COHORT_SIZE = %d  (frozen; no selection, no substitution)" % len(subjects))
    w("STOP_REASON = %s" % res["stop_reason"])
    w()

    w("--- rate ---")
    w("NOMINAL_RPS = %.2f   SPACING_S = %.2f" % (NOMINAL_RPS, SPACING_S))
    w("VENUE_REQUESTS = %d" % res["venue_requests"])
    w("ACHIEVED_RPS = %s   (N-1)/(last_start - first_start)"
      % (("%.4f" % res["achieved_rps"]) if res.get("achieved_rps") else "N/A"))
    g = res.get("gap_stats") or {}
    w("GAP_S min=%s median=%s max=%s"
      % (g.get("min"), g.get("median"), g.get("max")))
    w("HTTP_429 = %d   GRID_REANCHORS = %d"
      % (res["http_429"], len(res["grid_reanchors"])))
    w()

    w("--- observations ---")
    w("PLANNED_SETS = %d   SETS_COMPLETED = %d   ABANDONED_AT_END = %d"
      % (res["planned_sets"], res["sets_completed"],
         res["sets_abandoned_at_segment_end"]))
    valid = [r for r in done if r.get("valid_horizon_set")]
    w("VALID_HORIZON_SETS = %d of %d" % (len(valid), len(done)))
    w("BOOK_OBSERVATIONS = %d" % res["book_observations"])
    w("BBO_OBSERVATIONS  = %d" % res["bbo_observations"])
    w("IDENTITY_FAILURES = %d" % res["identity_failures"])
    w()

    w("--- markout register (separated, as required) ---")
    for h in HORIZONS:
        k = str(h)
        avail = [r for r in done if (r["horizons"].get(k) or {}).get(
            "horizon_observation_available")]
        post = [r for r in avail if r.get("passive_quote_postable")]
        chg = [r for r in avail if (r["horizons"][k] or {}).get("book_changed_by_horizon")]
        touched = [r for r in post if (r["horizons"][k] or {}).get("passive_touch_proxy")]
        crossed = [r for r in post if ((r["horizons"][k] or {}).get("buy_crossed")
                                       or (r["horizons"][k] or {}).get("sell_crossed"))]
        zero = [r for r in post if (r["horizons"][k] or {}).get("mid_markout") == "0"]
        w("h=%-5s AVAILABLE=%-5d ALL_POSTABLE=%-5d BOOK_CHANGED=%-5d "
          "TOUCHED=%-5d CROSSED=%-5d ZERO_MID_MARKOUT=%d"
          % (k, len(avail), len(post), len(chg), len(touched), len(crossed), len(zero)))
    w("ZERO MARKOUTS ARE RETAINED. The primary set is conditioned on nothing.")
    w("PASSIVE_FILL_PROBABILITY = NOT_IDENTIFIED  (a touch is not a fill)")
    w("PAIR_COMPLETED = NOT_IDENTIFIED  (two touches are not a completed pair)")
    w()

    w("--- cross-route freshness (lag %.1fs, corroboration not proof) ---" % SPACING_S)
    w("AGREE = %d   DISAGREE = %d   ABSENT = %d"
      % (res["cross_agree"], res["cross_disagree"], res["cross_absent"]))
    w()

    w("--- subjects ---")
    for s in subjects:
        w("%-56s sets=%-5d %s"
          % (s.slug, s.sets,
             ("OBSERVABLE" if s.retired_reason is None
              else "RETIRED %s @ %s" % (s.retired_reason, s.retired_at))))
    w("MARKETS_STILL_OBSERVABLE = %d of %d"
      % (sum(1 for s in subjects if s.retired_reason is None), len(subjects)))
    w("RETIREMENT_SCOPE = WITHIN_SEGMENT (no market is ever substituted)")
    w()

    w("--- carried forward, unchanged ---")
    w("PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    w("PASSIVE_FILL_PROBABILITY = NOT_IDENTIFIED")
    w("PMUS_FEES_RESOLVED = NO")
    w("RPS_LIMIT_NOT_ESTABLISHED = YES")
    w("NET_EXPECTANCY / NET_ROI / DEPLOYABLE_EDGE / GUARANTEED_PROFIT: NOT REPORTED")
    w("mirror_live = false")
    return L


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--segment", required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--cohort", default=str(Path(__file__).with_name(COHORT_FILE)))
    a = ap.parse_args(argv)

    cohort_path = Path(a.cohort)
    raw = cohort_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != COHORT_SHA256:
        say("COHORT_SHA256_MISMATCH want=%s got=%s" % (COHORT_SHA256, digest))
        return 2
    cohort = json.loads(raw)
    subjects = [Subject(e) for e in cohort]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    meta = {
        "phase": PHASE, "segment": a.segment,
        "python": sys.version, "platform": platform.platform(),
        "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
        "nominal_rps": NOMINAL_RPS, "spacing_s": SPACING_S,
        "set_period_slots": SET_PERIOD_SLOTS, "horizons": list(HORIZONS),
        "duration_s": a.duration_s,
        "cohort_file": cohort_path.name, "cohort_sha256": digest,
        "cohort_size": len(cohort),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "phase2b_sha256": hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest(),
        "collector_sha256": hashlib.sha256(Path(C.__file__).read_bytes()).hexdigest(),
    }

    res = {}
    t_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["segment_start_utc"] = t_wall
    say(json.dumps(meta, indent=1))
    say()

    raw_path = out / "request_log.jsonl.gz"
    t_start = time.monotonic()
    with gzip.open(raw_path, "wt") as log_fh:
        with httpx.Client(timeout=30.0) as http:
            done = run_segment(http, subjects, a.duration_s, log_fh, res)
    elapsed = time.monotonic() - t_start
    meta["segment_end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # rate arithmetic, in the form locked in Phase 2G-R
    starts = []
    with gzip.open(raw_path, "rt") as fh:
        nb = nq = 0
        for line in fh:
            r = json.loads(line)
            starts.append(r["local_request_monotonic_ns"] / 1e9)
            if r.get("path", "").endswith("/book"):
                nb += 1
            elif r.get("path", "").endswith("/bbo"):
                nq += 1
    res["venue_requests"] = len(starts)
    res["book_observations"] = nb
    res["bbo_observations"] = nq
    res["elapsed_s"] = elapsed
    if len(starts) >= 2:
        span = starts[-1] - starts[0]
        res["achieved_rps"] = (len(starts) - 1) / span if span else None
        gaps = sorted(starts[i + 1] - starts[i] for i in range(len(starts) - 1))
        res["gap_stats"] = {"min": round(gaps[0], 3),
                            "median": round(gaps[len(gaps) // 2], 3),
                            "max": round(gaps[-1], 3)}
    res["cross_agree"] = sum(r.get("cross_route_agree", 0) for r in done)
    res["cross_disagree"] = sum(r.get("cross_route_disagree", 0) for r in done)
    res["cross_absent"] = sum(r.get("cross_route_absent", 0) for r in done)
    res["retired"] = {s.slug: {"reason": s.retired_reason, "at": s.retired_at}
                      for s in subjects if s.retired_reason}

    with gzip.open(out / "sets.jsonl.gz", "wt") as fh:
        for r in done:
            fh.write(json.dumps(r, default=str) + "\n")
    (out / "segment_summary.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))

    say()
    lines = report(res, subjects, done, meta)
    B.seal(out, lines)
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
