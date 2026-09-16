#!/usr/bin/env python3
"""MAKER_ELIGIBLE_UNIVERSE_V1 — the pre-quote screen. Pure. Contacts nothing.

Three NESTED tiers, frozen before any economics were evaluated against them:

    BROAD         = status OPEN
                    AND two-sided BBO present
                    AND SPREAD_TICKS <= 5
    ACTIVE        = BROAD AND TRADE_RECENCY <= 24 h
    HIGH_ACTIVITY = BROAD AND TRADE_RECENCY <= 60 min

Nesting is the design, not a coincidence of the thresholds, and it is asserted
as a property over every market rather than promised here: HIGH_ACTIVITY is a
subset of ACTIVE is a subset of BROAD. Note that HIGH_ACTIVITY is defined
against BROAD rather than against ACTIVE; because 60 min < 24 h the two
definitions coincide, and stating it against BROAD means the subset property
survives someone later editing one threshold without the other.

WHAT `lastTradeSetTime` IS, AND IS NOT.

    TRADE_RECENCY_PROXY = CURRENT_TIME - lastTradeSetTime

One timestamp establishes RECENCY. It does not establish a RATE. A market whose
last trade was five minutes ago may have a terrible long-run arrival rate --
one trade in a day, and we happened to read it five minutes after that trade.
So this file computes no interarrival time, no trades-per-hour, no Poisson
rate, and no queue-clearing time, and there is a test asserting those names do
not appear in it at all.

    LEVEL_A_ACTIVITY   recency, from ONE book read      <- what this file does
    LEVEL_B_ACTIVITY   count / elapsed time, from REPEATED sharesTraded deltas
                       or a block-safe tape             <- a different input

The two are kept apart because conflating them is how a cheap screen turns
into an invented fill rate.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Frozen cut points. Round numbers on the observed board distribution: 5 ticks
# is the p75 of the two-sided spread; 24 h and 60 min are operational. No P&L,
# markout, settlement or future price entered any of them.
MAX_SPREAD_TICKS_BROAD = 5
ACTIVE_RECENCY_S = 24 * 3600
HIGH_ACTIVITY_RECENCY_S = 60 * 60

TIERS = ("BROAD", "ACTIVE", "HIGH_ACTIVITY")
TIERS_ARE_NESTED = True

# LEVEL_A is all this file can do. LEVEL_B needs repeated observations.
LEVEL_A_ACTIVITY = "TRADE_RECENCY_FROM_ONE_BOOK_READ"
LEVEL_B_ACTIVITY = "OBSERVED_TRADE_COUNT_PER_ELAPSED_TIME"
LEVEL_B_AVAILABLE = False
TRADE_RECENCY_AVAILABLE = "YES"
TRADE_ARRIVAL_RATE_AVAILABLE = "NO"

# Forbidden here by construction, and by a test.
EXPECTED_WAIT_TO_FILL = NOT_IDENTIFIED
EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR = NOT_IDENTIFIED


# ---------------------------------------------------------------------------
# EVENT IDENTITY
# ---------------------------------------------------------------------------
#
# THE VENUE PUBLISHES NO EVENT IDENTIFIER. Probed against 20,000 captured raw
# market objects: eventSlug, eventId, event, eventTicker, seriesId, groupId,
# parentId, gameId and conditionId are ALL absent, 0/20,000. So the event key
# is derived from the slug, and its derivation is frozen HERE rather than
# invented after seeing a sample's composition.
#
# The rule: find the first YYYY-MM-DD in the slug, keep everything up to and
# including it, and drop the leading grammar prefix (aec-, astatc-, tec-, ...).
# `aec-ufc-alomen-iwobar-2026-09-19` and
# `astatc-ufc-alomen-iwobar-2026-09-19-mof-ko` both key to
# `ufc-alomen-iwobar-2026-09-19`.
#
# WHY THIS MATTERS AT ALL: on the observed prefix, 19,513 markets carry a
# derivable key and they resolve to 1,456 events -- 13.4 markets per event,
# median 8, p90 27, and one event holding 394 markets. Treating 20,000 markets
# as 20,000 independent observations overstates the effective sample by more
# than an order of magnitude.
EVENT_ID_SOURCE = "DERIVED_FROM_SLUG_DATE_PREFIX"
VENUE_EVENT_IDENTIFIER_PRESENT = "NO"
EVENT_ID_DERIVATION_FROZEN = True

MARKET_WEIGHTED_ESTIMAND = "EACH_MARKET_EQUAL_WEIGHT"
EVENT_WEIGHTED_ESTIMAND = "EACH_INDEPENDENT_EVENT_EQUAL_WEIGHT"
ESTIMANDS_ARE_NEVER_COLLAPSED = True

# Reason codes. A market can fail for several reasons; the PRIMARY reason is
# deterministic and ordered, so two runs over the same row always agree.
FAIL_CLOSED = "FAIL_CLOSED"
FAIL_NO_TWO_SIDED_BOOK = "FAIL_NO_TWO_SIDED_BOOK"
FAIL_SPREAD = "FAIL_SPREAD"
FAIL_NO_TRADE_TIMESTAMP = "FAIL_NO_TRADE_TIMESTAMP"
FAIL_INVALID_CLOCK = "FAIL_INVALID_CLOCK"
FAIL_STALE_TRADE = "FAIL_STALE_TRADE"
REASON_PRIORITY = (FAIL_CLOSED, FAIL_NO_TWO_SIDED_BOOK, FAIL_SPREAD,
                   FAIL_NO_TRADE_TIMESTAMP, FAIL_INVALID_CLOCK,
                   FAIL_STALE_TRADE)


def event_key(slug):
    """The frozen event key, or None when no date is derivable."""
    s = str(slug or "")
    i = 0
    while True:
        i = s.find("-", i)
        if i < 0:
            return None
        cand = s[i + 1:i + 11]
        if (len(cand) == 10 and cand[4] == "-" and cand[7] == "-"
                and cand[:4].isdigit() and cand[5:7].isdigit()
                and cand[8:].isdigit()):
            head = s[:i + 11]
            return head.split("-", 1)[1] if "-" in head else head
        i += 1


def by_event(classified, metric, agg=None):
    """EVENT-WEIGHTED: aggregate WITHIN event first, then weight events equally.

    Not obtained by pretending each market is independent -- that is exactly
    the error this function exists to prevent. Markets with no derivable event
    key are excluded and counted, never silently folded in as singletons.
    """
    groups = {}
    dropped = 0
    for c in classified:
        k = event_key(c.get("slug"))
        if k is None:
            dropped += 1
            continue
        groups.setdefault(k, []).append(c)
    agg = agg or (lambda vals: sum(1 for v in vals if v is True) / len(vals))
    per_event = {k: agg([m.get(metric) for m in v]) for k, v in groups.items()}
    return {
        "METRIC": metric,
        "UNIQUE_EVENT_N": len(groups),
        "RAW_MARKET_N": len(classified),
        "MARKETS_WITHOUT_EVENT_ID": dropped,
        "EVENT_WEIGHTED_RESULT": (sum(per_event.values()) / len(per_event)
                                  if per_event else NOT_IDENTIFIED),
        "MARKET_WEIGHTED_RESULT": (
            sum(1 for c in classified if c.get(metric) is True)
            / len(classified) if classified else NOT_IDENTIFIED),
        "INDEPENDENT_SAMPLE_SIZE": len(groups),
        "WEIGHTING": "EVENT_WEIGHTED_AGGREGATES_WITHIN_EVENT_FIRST",
    }


def _q(d):
    """A venue money object -> Decimal, or None. Never a silent zero."""
    if isinstance(d, dict):
        v = d.get("value")
        return D(str(v)) if v not in (None, "") else None
    return None


def spread_ticks(row):
    """Spread in TICKS from the board row, or None if not two-sided.

    Ticks, not cents: the board carries 0.001, 0.005 and 0.01, so a screen
    written in cents would be three different screens.
    """
    bid = _q(row.get("board_bestBidQuote"))
    ask = _q(row.get("board_bestAskQuote"))
    tick = row.get("orderPriceMinTickSize")
    if bid is None or ask is None or not tick:
        return None
    t = D(str(tick))
    return None if t <= 0 else (ask - bid) / t


def stage1(row):
    """Free screen, from a board row already captured. No request."""
    st = spread_ticks(row)
    return {
        "slug": row.get("slug"),
        "STATUS_OPEN": row.get("status") == "MARKET_STATUS_OPEN",
        "TWO_SIDED_BBO": st is not None,
        "SPREAD_TICKS": st,
        "TICK_SIZE": row.get("orderPriceMinTickSize"),
        "SPORT_TOKEN": (str(row.get("slug")).split("-")[1]
                        if len(str(row.get("slug")).split("-")) > 1 else None),
        "BROAD": bool(row.get("status") == "MARKET_STATUS_OPEN"
                      and st is not None and st <= MAX_SPREAD_TICKS_BROAD),
    }


def trade_recency_s(book_body, now_s, parse_time):
    """CURRENT_TIME - lastTradeSetTime, in seconds, or NOT_IDENTIFIED.

    The clock is injected because this file does no date arithmetic of its own
    and must not acquire a timezone opinion. A market that has never traded
    has no lastTradeSetTime, and that is NOT_IDENTIFIED -- not "infinitely
    stale" and certainly not "recent".
    """
    stats = ((book_body or {}).get("marketData") or {}).get("stats") or {}
    ts = stats.get("lastTradeSetTime")
    if not ts:
        return NOT_IDENTIFIED
    try:
        return now_s - parse_time(ts)
    except Exception:
        return NOT_IDENTIFIED


def book_bbo(book_body):
    """Best bid / best offer / spread in ticks FROM THE ARRIVING BOOK.

    The stage-1 spread was observed at T0 and is up to 1.29 hours stale by the
    time the book arrives. It is never carried forward as though it were
    current; the decision screen recomputes from what came back.
    """
    md = (book_body or {}).get("marketData") or {}
    bids = md.get("bids") or []
    offers = md.get("offers") or []
    bid = D(str(bids[0]["px"]["value"])) if bids else None
    ask = D(str(offers[0]["px"]["value"])) if offers else None
    return bid, ask, md.get("state")


def decision_screen(row, book_body, now_s, parse_time, tick=None,
                    book_transact_time=None, book_receipt_time=None):
    """STAGE 2. Every tier recomputed at Ti from the book that just arrived.

    Stage 1 routed this market here; it decides nothing else. Mixing
    SPREAD_AT_T0 with TRADE_RECENCY_AT_Ti describes no market at any single
    decision time, which is the whole reason this function exists.
    """
    t = D(str(tick if tick is not None else (row.get("orderPriceMinTickSize")
                                             or "0.01")))
    bid, ask, state = book_bbo(book_body)
    two_sided = bid is not None and ask is not None and t > 0
    spread = (ask - bid) / t if two_sided else None
    open_at_ti = state in (None, "MARKET_STATE_OPEN")

    out = {
        "slug": row.get("slug"),
        "BOOK_RECEIPT_TIME": book_receipt_time,
        "BOOK_TRANSACT_TIME": book_transact_time,
        "CURRENT_BID_AT_TI": bid,
        "CURRENT_ASK_AT_TI": ask,
        "CURRENT_SPREAD_TICKS_AT_TI": spread,
        "CURRENT_STATE_AT_TI": state,
        "STAGE2_CURRENT_SPREAD_RECOMPUTED": True,
    }
    stats = ((book_body or {}).get("marketData") or {}).get("stats") or {}
    lts = stats.get("lastTradeSetTime")
    out["LAST_TRADE_SET_TIME"] = lts

    # TWO CLOCKS, PER ROW. Never one shared timestamp: that would make the
    # first market of a 1.29-hour scan look stale purely by scan order.
    def _age(end):
        if not lts or end is None:
            return NOT_IDENTIFIED
        try:
            return parse_time(end) - parse_time(lts)
        except Exception:
            return NOT_IDENTIFIED

    at_venue = _age(book_transact_time)
    at_receipt = (_age(book_receipt_time) if book_receipt_time is not None
                  else trade_recency_s(book_body, now_s, parse_time))
    out["TRADE_RECENCY_AT_VENUE_SNAPSHOT"] = at_venue
    out["TRADE_RECENCY_AT_BETTOR_RECEIPT"] = at_receipt

    # A NEGATIVE AGE IS A DEFECT, NOT ACTIVITY. Clamping it to zero would turn
    # a broken clock into the strongest activity signal on the board and put
    # exactly the wrong markets at the top of the screen.
    neg = [v for v in (at_venue, at_receipt)
           if v != NOT_IDENTIFIED and v < 0]
    out["NEGATIVE_RECENCY"] = bool(neg)
    out["LAST_TRADE_BEFORE_TRANSACT_TIME"] = (
        NOT_IDENTIFIED if at_venue == NOT_IDENTIFIED else at_venue >= 0)

    rec = at_receipt if at_receipt != NOT_IDENTIFIED else at_venue
    usable = rec != NOT_IDENTIFIED and rec >= 0

    broad = bool(open_at_ti and two_sided
                 and spread is not None and spread <= MAX_SPREAD_TICKS_BROAD)
    out["BROAD_AT_DECISION"] = broad
    out["ACTIVE_AT_DECISION"] = bool(broad and usable
                                     and rec <= ACTIVE_RECENCY_S)
    out["HIGH_ACTIVITY_AT_DECISION"] = bool(
        broad and usable and rec <= HIGH_ACTIVITY_RECENCY_S)

    # REASON CODES. A market can fail several ways; every applicable code is
    # recorded, and PRIMARY_FAIL_REASON is the first in a fixed priority order
    # so two runs over the same row always agree on the headline.
    #
    # Missingness gets its OWN code rather than collapsing into staleness: a
    # market that never traded and a market that traded two days ago fail the
    # same tier for completely different reasons, and a screen that cannot tell
    # them apart cannot be debugged.
    reasons = []
    if not open_at_ti:
        reasons.append(FAIL_CLOSED)
    if not two_sided:
        reasons.append(FAIL_NO_TWO_SIDED_BOOK)
    elif spread is not None and spread > MAX_SPREAD_TICKS_BROAD:
        reasons.append(FAIL_SPREAD)
    if not lts:
        reasons.append(FAIL_NO_TRADE_TIMESTAMP)
    elif rec == NOT_IDENTIFIED:
        reasons.append(FAIL_INVALID_CLOCK)
    elif rec < 0:
        reasons.append(FAIL_INVALID_CLOCK)
    elif rec > ACTIVE_RECENCY_S:
        reasons.append(FAIL_STALE_TRADE)
    out["FAIL_REASONS"] = reasons
    out["PRIMARY_FAIL_REASON"] = next(
        (r for r in REASON_PRIORITY if r in reasons), None)
    out["LAST_TRADE_SET_TIME_PRESENT"] = bool(lts)
    out["LAST_TRADE_SET_TIME_INVALID_FUTURE"] = bool(
        at_venue != NOT_IDENTIFIED and at_venue < 0)
    return out


def audit_schedule(routed_slugs, audit_slugs, salt):
    """Interleave the routing-censoring audit DETERMINISTICALLY among the
    routed reads.

    THE TRAP THIS AVOIDS. Putting audit markets at the end of a 1.29-hour scan
    gives every one of them the maximum time to tighten, so the measured
    false-negative rate would be mechanically inflated by scan order alone --
    it would measure the scan's latency, not the routing rule's error.

    Both populations are ordered by the same salted hash, so an audit market's
    position in the scan is fixed before any result exists and is uncorrelated
    with anything about the market.
    """
    def h(slug, tag):
        return hashlib.sha256(
            ("%s|%s|%s" % (salt, tag, slug)).encode("utf-8")).hexdigest()

    rows = ([(h(s, "routed"), s, "ROUTED") for s in routed_slugs]
            + [(h(s, "audit"), s, "AUDIT") for s in audit_slugs])
    rows.sort()
    return [{"position": i, "slug": s, "lane": lane}
            for i, (_, s, lane) in enumerate(rows)]


def classify(row, book_body=None, now_s=None, parse_time=None):
    """The three tiers for one market. Nested by construction.

    Without a stage-2 book read, ACTIVE and HIGH_ACTIVITY are NOT_IDENTIFIED
    rather than False: not having looked is not the same as having looked and
    found the market quiet, and only one of those is evidence.
    """
    s1 = stage1(row)
    out = dict(s1)
    out["LEVEL_A_ACTIVITY"] = LEVEL_A_ACTIVITY
    out["LEVEL_B_ACTIVITY"] = NOT_IDENTIFIED

    if book_body is None or now_s is None or parse_time is None:
        out["TRADE_RECENCY_S"] = NOT_IDENTIFIED
        out["ACTIVE"] = NOT_IDENTIFIED
        out["HIGH_ACTIVITY"] = NOT_IDENTIFIED
        out["STAGE2_READ"] = False
        return out

    rec = trade_recency_s(book_body, now_s, parse_time)
    out["TRADE_RECENCY_S"] = rec
    out["STAGE2_READ"] = True
    if rec == NOT_IDENTIFIED:
        # Never traded, or an unreadable timestamp. Both fail an activity
        # screen; neither is NOT_IDENTIFIED, because we DID look.
        out["ACTIVE"] = False
        out["HIGH_ACTIVITY"] = False
        return out
    out["ACTIVE"] = bool(s1["BROAD"] and rec <= ACTIVE_RECENCY_S)
    out["HIGH_ACTIVITY"] = bool(s1["BROAD"] and rec <= HIGH_ACTIVITY_RECENCY_S)
    return out


def census(classified, board_capture_time=None, scan_start=None,
           scan_end=None):
    """Tier counts over a ROLLING CENSUS, with its temporal drift on the face.

    A scan of thousands of books at 2 rps observes different markets at
    different wall-clock times. It is not a simultaneous board snapshot, and
    calling it one would make an hour of drift invisible.
    """
    def n(k, val=True):
        return sum(1 for c in classified if c.get(k) is val)
    out = {
        "OBSERVATION_TYPE": "ROLLING_CENSUS",
        "IS_SIMULTANEOUS_BOARD_SNAPSHOT": False,
        "BOARD_CAPTURE_TIME": board_capture_time or NOT_IDENTIFIED,
        "SCAN_START": scan_start or NOT_IDENTIFIED,
        "SCAN_END": scan_end or NOT_IDENTIFIED,
        # STAGE 1 IS A ROUTING FRAME. Markets it rejects at T0 are never read
        # at Ti, so a market that tightened from 6 ticks to 1 in between is
        # invisible to this pipeline. The result is what the scanner SURFACES,
        # not what the board contains.
        "RESULT_KIND": "PIPELINE_YIELD",
        "TRUE_ELIGIBLE_MARKET_SHARE_OF_BOARD": NOT_IDENTIFIED,
        "TRUE_BOARD_ELIGIBLE_SHARE": NOT_IDENTIFIED,
        "STAGE1_REJECTS_ARE_NEVER_REEXAMINED": True,
        "SAMPLING_FRAME": "OBSERVED_20K_PREFIX",
        "FULL_BOARD_BOUNDARY_KNOWN": "NO",
        "GENERALIZES_TO_OBSERVED_PREFIX": "POTENTIALLY_TESTABLE",
        "GENERALIZES_TO_FULL_BOARD": NOT_IDENTIFIED,
        "STAGE1_INPUT_COUNT": len(classified),
        "STAGE1_TWO_SIDED_COUNT": n("TWO_SIDED_BBO"),
        "STAGE1_BROAD_SURVIVOR_COUNT": n("BROAD"),
        "STAGE2_BOOK_READ_COUNT": n("STAGE2_READ"),
        "ACTIVE_COUNT": n("ACTIVE"),
        "HIGH_ACTIVITY_COUNT": n("HIGH_ACTIVITY"),
        "ACTIVE_NOT_IDENTIFIED": sum(1 for c in classified
                                     if c.get("ACTIVE") == NOT_IDENTIFIED),
        "STAGE1_ROUTED_MARKETS": n("BROAD"),
        "STAGE2_BROAD_AT_DECISION": n("BROAD_AT_DECISION"),
        "STAGE2_ACTIVE_AT_DECISION": n("ACTIVE_AT_DECISION"),
        "STAGE2_HIGH_ACTIVITY_AT_DECISION": n("HIGH_ACTIVITY_AT_DECISION"),
        "NEGATIVE_RECENCY_ROWS": n("NEGATIVE_RECENCY"),
        "TRADE_RECENCY_CLOCK": ("PER_ROW: BOOK_TRANSACT_TIME and "
                                "BOOK_RECEIPT_TIME, never a shared timestamp"),
        "TIERS_ARE_NESTED": TIERS_ARE_NESTED,
        "TRADE_RECENCY_AVAILABLE": TRADE_RECENCY_AVAILABLE,
        "TRADE_ARRIVAL_RATE_AVAILABLE": TRADE_ARRIVAL_RATE_AVAILABLE,
        # The denominators the north star would need, and does not have.
        "EXPECTED_FILL_RATE": NOT_IDENTIFIED,
        "EXPECTED_WAIT_TO_FILL": NOT_IDENTIFIED,
        "EXPECTED_CAPITAL_OCCUPANCY": NOT_IDENTIFIED,
        "EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR": NOT_IDENTIFIED,
    }
    # `is not None`, not truthiness: a scan starting at t=0 is a real scan,
    # and `if scan_start` would silently drop its duration. The same
    # absent-versus-zero confusion this programme keeps meeting.
    routed = out["STAGE1_ROUTED_MARKETS"]
    for tier in ("BROAD", "ACTIVE", "HIGH_ACTIVITY"):
        k = "STAGE2_%s_AT_DECISION" % tier
        out["PIPELINE_YIELD_%s" % tier] = (
            "%d/%d" % (out[k], routed) if routed else NOT_IDENTIFIED)

    if scan_start is not None and scan_end is not None:
        out["ROLLING_CENSUS_DURATION_S"] = scan_end - scan_start
        out["TEMPORAL_DRIFT_LABEL"] = (
            "markets observed at different wall-clock times across the scan; "
            "TRADE_RECENCY_AT_RECEIPT is per-row and not comparable to a "
            "single board capture instant")
    return out


def capacity(classified, tier="HIGH_ACTIVITY_AT_DECISION"):
    """Gross market count and NET independent-event count, never conflated.

    Thirty eligible props on one NFL game are not thirty independent capital
    opportunities. Differing slugs are not evidence of independence.
    """
    elig = [c for c in classified if c.get(tier) is True]
    ev = {}
    for c in elig:
        k = event_key(c.get("slug"))
        ev.setdefault(k if k is not None else ("__NO_EVENT_ID__", c.get("slug")),
                      []).append(c)
    sizes = sorted((len(v) for v in ev.values()), reverse=True)
    return {
        "TIER": tier,
        "MARKET_LEVEL_GROSS_CAPACITY": len(elig),
        "EVENT_LEVEL_NET_CAPACITY": len(ev),
        "ELIGIBLE_MARKETS": len(elig),
        "ELIGIBLE_EVENTS": len(ev),
        "MAX_EVENT_EXPOSURE": sizes[0] if sizes else 0,
        "CORRELATED_MARKET_COUNT_PER_EVENT": sizes[:10],
        "EVENT_INDEPENDENCE_INFERRED_FROM_DIFFERING_SLUGS": False,
        # Capacity is a COUNT. Turning it into money needs the fill and
        # recycling rates that are still unmeasured.
        "EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR": NOT_IDENTIFIED,
    }


def inference_status(unique_event_n, minimum=30):
    """Say UNDERPOWERED rather than manufacture precision."""
    return ("UNDERPOWERED" if unique_event_n < minimum
            else "EVENT_CLUSTERED_INFERENCE_PERMITTED")


# The three questions, kept apart because only the first two are answerable
# from anything above.
QUESTION_GENERALIZATION = "IS THE UFC MICROSTRUCTURE REPRESENTATIVE?"
QUESTION_ELIGIBILITY = "HOW MANY MARKETS PASS THE PRE-QUOTE SCREENS?"
QUESTION_ECONOMICS = "ARE THOSE MARKETS PROFITABLE TO MAKE?"
QUESTION_ECONOMICS_ANSWERABLE_HERE = False
