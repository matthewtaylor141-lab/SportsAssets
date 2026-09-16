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
# WHAT THIS KEY ACTUALLY IS -- CORRECTED.
#
# The derived key was validated against the already-captured rows (zero
# network, top 50 clusters by size plus a board-wide pass) and it FAILED as an
# event identifier. It fails in BOTH directions at once:
#
#   OVER-MERGE.  `nfl-2027-01-10` holds 394 markets spanning 32 distinct
#                participant sets, 32 distinct provider-id sets, 123 distinct
#                player ids and 3 distinct gameStartTimes. The date is a SEASON
#                SETTLEMENT date, not a contest date.
#   UNDER-MERGE. `nfl-2027-01-10`, `nfl-wins-2027-01-10`,
#                `nfl-bestrecord-2027-01-10` and five more are eight separate
#                derived keys carrying the IDENTICAL 32-team roster on the
#                identical settlement date -- one season, split eight ways.
#
# Errors in opposite directions do not cancel to a known quantity. So the
# derived key is a MARKET FAMILY key, and the count it produces is provisional.
#
# IT IS NOT USELESS. On the stratum where the venue's own fields agree -- one
# gameStartTime, one participant pair, exactly two provider-identified teams --
# 4,783 markets are PROVED to be only 72 contests, 66.4 markets per contest.
# Market-level independence is false and the clustering is material. What is
# retracted is the QUANTITY, not the direction.
EVENT_KEY_METHOD = "DERIVED_HEURISTIC_V1"
EVENT_KEY_VALIDATED = False
EVENT_KEY_VALIDATION_RESULT = "FAILS_AS_EVENT_ID_BOTH_DIRECTIONS"
UNIQUE_EVENTS_1456 = "PROVISIONAL_HEURISTIC_COUNT"
INDEPENDENT_SAMPLE_SIZE = NOT_IDENTIFIED
SAMPLE_OVERSTATEMENT_MAGNITUDE = NOT_IDENTIFIED   # was claimed as ">10x". Not.
MARKETS_ARE_NOT_INDEPENDENT = True                # survives
DIFFERENT_SLUGS_DO_NOT_IMPLY_INDEPENDENT_EVENTS = True   # survives

EVENT_ID_SOURCE = "DERIVED_FROM_SLUG_DATE_PREFIX"
VENUE_EVENT_IDENTIFIER_PRESENT = "NO"
EVENT_ID_DERIVATION_FROZEN = True

# THE FOUR-LEVEL EVIDENCE HIERARCHY. Measured board-wide on the observed
# prefix; the counts are recorded here so a later run can be compared to them.
#
#   LEVEL_A  an explicit venue event identifier.  0 clusters / 0 markets.
#            eventSlug, eventId, event, eventTicker, seriesId, groupId,
#            parentId, gameId, conditionId: all absent, 0/20,000.
#   LEVEL_B  a strong composite proved by three agreeing venue fields.
#            72 clusters / 4,783 markets.
#   LEVEL_C  family-only: the cluster is a MARKET FAMILY, not a contest.
#            1,384 clusters / 14,730 markets. The bulk are
#            sportsMarketTypeV2 = FUTURE, where the slug date is a season
#            settlement date.
#   LEVEL_D  unknown. 0 clusters, plus 487 markets with no derivable key at
#            all, which never reach a cluster.
#
# PURITY, board-wide, 1,456 clusters / 19,513 keyed markets:
#   PURE 72, OVERMERGED 114, UNKNOWN 1,270.
#   OVERMERGE_RATE_BY_CLUSTER 7.8%   OVERMERGE_RATE_BY_MARKET 18.2%
# On the top-50-by-size stratum -- the most over-merge-prone slice there is --
#   PURE 24, OVERMERGED 8, UNKNOWN 18; by cluster 16.0%, by market 24.5%.
# UNKNOWN dominates, and UNKNOWN is not PURE: for 1,270 clusters the venue
# publishes nothing that would identify a contest either way.
IDENTITY_LEVEL_A = "A_EXPLICIT_VENUE_EVENT_ID"
IDENTITY_LEVEL_B = "B_STRONG_COMPOSITE_GAME"
IDENTITY_LEVEL_C = "C_FAMILY_ONLY"
IDENTITY_LEVEL_D = "D_UNKNOWN"
IDENTITY_LEVELS = (IDENTITY_LEVEL_A, IDENTITY_LEVEL_B,
                   IDENTITY_LEVEL_C, IDENTITY_LEVEL_D)

OBSERVED_PREFIX_LEVEL_A_CLUSTERS = 0
OBSERVED_PREFIX_LEVEL_B_CLUSTERS = 72
OBSERVED_PREFIX_LEVEL_B_MARKETS = 4783
OBSERVED_PREFIX_LEVEL_C_MARKETS = 14730
OBSERVED_PREFIX_LEVEL_D_MARKETS = 0
OBSERVED_PREFIX_NO_KEY_MARKETS = 487

# BOUNDS, NOT A POINT ESTIMATE.
PROVEN_DISTINCT_EVENT_N = 72
EVENT_COUNT_LOWER_BOUND = 1439
EVENT_COUNT_UPPER_BOUND = 14802
EXACT_INDEPENDENT_EVENT_N = NOT_IDENTIFIED
# The upper bound is the every-non-proven-market-is-its-own-event ceiling. It
# is stated to bound the range and is NOT the programme's working assumption:
# treating every market as independent is the error this whole section exists
# to prevent, and a 10.3x-wide range is the honest state of the evidence.
EVENT_COUNT_UPPER_BOUND_IS_A_CEILING_NOT_AN_ESTIMATE = True

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


def market_family_key(slug):
    """The derived slug-date key, or None when no date is derivable.

    THIS IS A MARKET FAMILY KEY, NOT AN EVENT KEY. It answers "which block of
    the board's naming scheme does this market sit in", and that is all it has
    been shown to answer. `event_key` is kept as a deprecated alias so no
    caller silently changes meaning, and both are named so a reader of a call
    site can tell which claim is being made.
    """
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


# Deprecated alias. Same bytes, honest name kept beside it.
event_key = market_family_key


def _team_ids(market):
    out = set()
    for side in market.get("marketSides") or []:
        team = side.get("team") or {}
        if team.get("id") is not None:
            out.add(team["id"])
    return out


def _provider_ids(market):
    out = set()
    for side in market.get("marketSides") or []:
        for p in ((side.get("team") or {}).get("providerIds") or []):
            if p.get("providerId") is not None:
                out.add((p.get("provider"), p["providerId"]))
    return out


VENUE_EVENT_ID_FIELDS = ("eventSlug", "eventId", "event", "eventTicker",
                         "seriesId", "groupId", "parentId", "gameId",
                         "conditionId")


def underlying_event_key(markets):
    """The UNDERLYING contest for a group of raw market rows, with its level.

    Returns (KEY, LEVEL). The key is NOT_IDENTIFIED at every level except B --
    a family label is not an event id, and this function will not hand one back
    dressed as one.

    LEVEL_A  an explicit venue identifier. Never reached on this venue; the
             probe is kept so the day the venue ships one, this returns it.
    LEVEL_B  three agreeing venue fields: ONE gameStartTime, ONE participant
             pair, EXACTLY TWO provider-identified teams. That is a proof that
             the rows describe one contest, made from the venue's own data.
    LEVEL_C  a market family -- the rows share a naming block and nothing else
             that identifies a contest.
    LEVEL_D  no identity evidence at all.
    """
    ms = list(markets)
    if not ms:
        return NOT_IDENTIFIED, IDENTITY_LEVEL_D

    for f in VENUE_EVENT_ID_FIELDS:
        vals = {m.get(f) for m in ms if m.get(f) not in (None, "")}
        if len(vals) == 1:
            return vals.pop(), IDENTITY_LEVEL_A

    starts = {m.get("gameStartTime") for m in ms}
    teams = set()
    pairs = set()
    for m in ms:
        t = _team_ids(m)
        teams |= t
        if t:
            pairs.add(frozenset(t))
    provs = {frozenset(_provider_ids(m)) for m in ms if _provider_ids(m)}

    # `starts == {None}` is NOT "one start time" -- it is no start time at all,
    # and a level-B claim built on a field nobody filled in is exactly the
    # absence-of-evidence promotion this hierarchy exists to stop.
    if (len(starts) == 1 and None not in starts and len(pairs) == 1
            and len(teams) == 2 and len(provs) == 1):
        start = next(iter(starts))
        return ("%s|%s" % (start, "-".join(str(t) for t in sorted(teams))),
                IDENTITY_LEVEL_B)

    if teams or starts != {None}:
        return NOT_IDENTIFIED, IDENTITY_LEVEL_C
    return NOT_IDENTIFIED, IDENTITY_LEVEL_D


def cluster_purity(markets, key=None):
    """The purity test. Groups raw rows by family key and reports the evidence.

    Never returns a verdict of PURE from the absence of evidence: a cluster
    with no team ids and no start-time spread is UNKNOWN, not clean.
    """
    key = key or (lambda m: market_family_key(m.get("slug")))
    groups = {}
    nokey = 0
    for m in markets:
        k = key(m)
        if k is None:
            nokey += 1
            continue
        groups.setdefault(k, []).append(m)

    rows = []
    for k, ms in groups.items():
        starts = {m.get("gameStartTime") for m in ms}
        teams = set()
        pairs = set()
        for m in ms:
            t = _team_ids(m)
            teams |= t
            if t:
                pairs.add(frozenset(t))
        provs = {frozenset(_provider_ids(m)) for m in ms if _provider_ids(m)}
        _, level = underlying_event_key(ms)
        if level == IDENTITY_LEVEL_B:
            verdict = "PURE"
        elif len(pairs) > 1 or len(provs) > 1 or len(teams) > 2 \
                or len(starts) > 1:
            verdict = "OVERMERGED"
        else:
            verdict = "UNKNOWN"
        rows.append({
            "DERIVED_EVENT_KEY": k,
            "MARKET_COUNT": len(ms),
            "DISTINCT_GAME_START_TIMES": len(starts),
            "DISTINCT_PARTICIPANT_PAIRS": len(pairs),
            "DISTINCT_TEAM_IDS": len(teams),
            "DISTINCT_PROVIDER_ID_SETS": len(provs),
            "DISTINCT_MARKET_TYPES": len({m.get("sportsMarketTypeV2")
                                          for m in ms}),
            "SAMPLE_TITLES": [m.get("title") or m.get("question")
                              for m in ms[:4]],
            "IDENTITY_LEVEL": level,
            "VERDICT": verdict,
        })
    rows.sort(key=lambda r: (-r["MARKET_COUNT"], r["DERIVED_EVENT_KEY"]))

    n_cl = len(rows)
    n_mk = sum(r["MARKET_COUNT"] for r in rows)
    over = [r for r in rows if r["VERDICT"] == "OVERMERGED"]
    pure = [r for r in rows if r["VERDICT"] == "PURE"]
    unk = [r for r in rows if r["VERDICT"] == "UNKNOWN"]
    return {
        "CLUSTERS_TESTED": n_cl,
        "PURE_CLUSTERS": len(pure),
        "OVERMERGED_CLUSTERS": len(over),
        "UNKNOWN_CLUSTERS": len(unk),
        "MARKETS_TESTED": n_mk,
        "MARKETS_IN_OVERMERGED_CLUSTERS": sum(r["MARKET_COUNT"] for r in over),
        "MARKETS_IN_PURE_CLUSTERS": sum(r["MARKET_COUNT"] for r in pure),
        "MARKETS_IN_UNKNOWN_CLUSTERS": sum(r["MARKET_COUNT"] for r in unk),
        "MARKETS_WITH_NO_DERIVABLE_KEY": nokey,
        "OVERMERGE_RATE_BY_CLUSTER": (len(over) / n_cl if n_cl
                                      else NOT_IDENTIFIED),
        "OVERMERGE_RATE_BY_MARKET": (
            sum(r["MARKET_COUNT"] for r in over) / n_mk if n_mk
            else NOT_IDENTIFIED),
        "EVENT_KEY_METHOD": EVENT_KEY_METHOD,
        "EVENT_KEY_VALIDATED": EVENT_KEY_VALIDATED,
        "ROWS": rows,
    }


def event_count_bounds(markets):
    """BOUNDS on the independent event count. Never a point estimate.

    LOWER: every family is one event, and families proved to share a roster on
           the same league and settlement date collapse together.
    UPPER: proved clusters count once; every other market is presumed distinct.
           This is a CEILING. It is the every-market-is-independent assumption
           applied only to the unproven remainder, and it is reported to show
           how wide the range is -- not adopted.
    """
    groups = {}
    nokey = 0
    for m in markets:
        k = market_family_key(m.get("slug"))
        if k is None:
            nokey += 1
            continue
        groups.setdefault(k, []).append(m)

    proven = 0
    proven_markets = 0
    rosters = {}
    for k, ms in groups.items():
        _, level = underlying_event_key(ms)
        if level in (IDENTITY_LEVEL_A, IDENTITY_LEVEL_B):
            proven += 1
            proven_markets += len(ms)
            continue
        teams = set()
        for m in ms:
            teams |= _team_ids(m)
        if teams:
            parts = k.split("-")
            rosters.setdefault(
                (parts[0], "-".join(parts[-3:]), frozenset(teams)), []
            ).append(k)

    # Only a PROVED collapse counts: same league token, same settlement date,
    # and an identical non-empty roster of venue team ids. Two derived keys
    # that merely share a league and a date are NOT collapsed -- on this board
    # the middle segment is a market type in one family (nfl-wins-) and a
    # distinct contest in another (ushrmov-al-01-), and nothing in a captured
    # row tells those apart.
    collapsed = sum(len(v) - 1 for v in rosters.values() if len(v) > 1)

    keyed = sum(len(v) for v in groups.values())
    lower = len(groups) - collapsed
    upper = proven + (keyed - proven_markets)
    return {
        "PROVEN_DISTINCT_EVENT_N": proven,
        "EVENT_COUNT_LOWER_BOUND": lower,
        "EVENT_COUNT_UPPER_BOUND": upper,
        "EXACT_INDEPENDENT_EVENT_N": NOT_IDENTIFIED,
        "PROVISIONAL_HEURISTIC_COUNT": len(groups),
        "MARKETS_WITH_NO_DERIVABLE_KEY": nokey,
        "KEYS_REMOVED_BY_PROVED_COLLAPSE": collapsed,
        "UPPER_BOUND_IS_A_CEILING_NOT_AN_ESTIMATE": True,
    }


class EventKeyNotValidated(RuntimeError):
    """Raised when event-weighted inference is attempted on an unvalidated key."""


def by_event(classified, metric, agg=None, key=None, level=None):
    """EVENT-WEIGHTED: aggregate WITHIN event first, then weight events equally.

    GATED. The derived family key FAILED validation as an event identifier, so
    calling this with the default key raises rather than returning a number
    nobody may use. The implementation is kept intact -- the defect is in the
    key, not in the weighting -- and switches back on the moment a caller
    supplies a key whose identity level is A or B.

    Pass `key=` (a callable slug/row -> identity) together with `level=` set to
    IDENTITY_LEVEL_A or IDENTITY_LEVEL_B to run it. Passing a level of C or D,
    or omitting both, raises.
    """
    if level not in (IDENTITY_LEVEL_A, IDENTITY_LEVEL_B):
        raise EventKeyNotValidated(
            "EVENT_WEIGHTED_INFERENCE_DISABLED: EVENT_KEY_VALIDATED=%r, "
            "EVENT_KEY_METHOD=%s, INDEPENDENT_SAMPLE_SIZE=%s. Supply a key at "
            "identity level A or B to enable." % (
                EVENT_KEY_VALIDATED, EVENT_KEY_METHOD, INDEPENDENT_SAMPLE_SIZE))

    key = key or (lambda c: market_family_key(c.get("slug")))
    groups = {}
    dropped = 0
    for c in classified:
        k = key(c)
        if k is None or k == NOT_IDENTIFIED:
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
        "IDENTITY_LEVEL": level,
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
    """Gross market count and FAMILY-level count, never conflated.

    Thirty eligible props on one NFL game are not thirty independent capital
    opportunities. Differing slugs are not evidence of independence.

    The family count is NOT called an independent capacity: the family key
    failed validation as an event id, so the honest capacity figure is a range
    with the family count as its floor, and `INDEPENDENT_CAPACITY` stays
    NOT_IDENTIFIED. Opportunity count and independent capacity are two numbers
    and this returns both names.
    """
    elig = [c for c in classified if c.get(tier) is True]
    ev = {}
    for c in elig:
        k = market_family_key(c.get("slug"))
        ev.setdefault(k if k is not None else ("__NO_EVENT_ID__", c.get("slug")),
                      []).append(c)
    sizes = sorted((len(v) for v in ev.values()), reverse=True)
    return {
        "TIER": tier,
        "MARKET_LEVEL_GROSS_CAPACITY": len(elig),
        "OPPORTUNITY_COUNT": len(elig),
        "FAMILY_LEVEL_COUNT": len(ev),
        "INDEPENDENT_CAPACITY": NOT_IDENTIFIED,
        "INDEPENDENT_CAPACITY_LOWER_BOUND": len(ev),
        "INDEPENDENT_CAPACITY_UPPER_BOUND": len(elig),
        "ELIGIBLE_MARKETS": len(elig),
        "ELIGIBLE_FAMILIES": len(ev),
        "MAX_FAMILY_EXPOSURE": sizes[0] if sizes else 0,
        "CORRELATED_MARKET_COUNT_PER_FAMILY": sizes[:10],
        "EVENT_INDEPENDENCE_INFERRED_FROM_DIFFERING_SLUGS": False,
        "EVENT_KEY_VALIDATED": EVENT_KEY_VALIDATED,
        # Capacity is a COUNT. Turning it into money needs the fill and
        # recycling rates that are still unmeasured.
        "EXPECTED_NET_PNL_PER_CAPITAL_DOLLAR_PER_HOUR": NOT_IDENTIFIED,
    }


def inference_status(unique_event_n, minimum=30, validated=None):
    """Say UNDERPOWERED rather than manufacture precision.

    An unvalidated key does not produce an event count at all, so the status is
    EVENT_IDENTITY_UNVALIDATED -- which is not the same as UNDERPOWERED, and is
    not repaired by the count happening to be large.
    """
    if validated is None:
        validated = EVENT_KEY_VALIDATED
    if not validated:
        return "EVENT_IDENTITY_UNVALIDATED"
    return ("UNDERPOWERED" if unique_event_n < minimum
            else "EVENT_CLUSTERED_INFERENCE_PERMITTED")


def _pct(vals, p):
    """Nearest-rank percentile on a sorted list. No interpolation, no numpy."""
    s = sorted(vals)
    if not s:
        return NOT_IDENTIFIED
    i = int(round((p / 100.0) * (len(s) - 1)))
    return s[i]


def audit_elapsed_report(observations):
    """Did the audit lane actually get read at the same times as the routed one?

    `audit_schedule` fixes the ORDER before any result exists, which is the
    right design, but an order is a promise about position, not a measurement
    of elapsed time. A stall, a retry, or a rate limit can put the audit lane
    late in wall-clock even from a fair position. So this reports the ACTUAL
    elapsed-time distribution of each lane rather than asserting first/last
    thirds, and refuses to declare the comparison fair from the schedule alone.

    `observations` are dicts with `lane` ("ROUTED"/"AUDIT") and `elapsed_s`
    measured from the scan start at the moment each row was READ.
    """
    lanes = {"ROUTED": [], "AUDIT": []}
    for o in observations:
        lane = o.get("lane")
        e = o.get("elapsed_s")
        if lane in lanes and e is not None:
            lanes[lane].append(e)

    out = {"SCHEDULE_FAIRNESS_ASSERTED_FROM_POSITION": False,
           "ELAPSED_MEASURED": True}
    for lane, key in (("ROUTED", "ROUTED"), ("AUDIT", "AUDIT")):
        v = lanes[lane]
        out["%s_N" % key] = len(v)
        for p in (10, 50, 90):
            out["%s_ELAPSED_P%d" % (key, p)] = _pct(v, p)

    r50 = out["ROUTED_ELAPSED_P50"]
    a50 = out["AUDIT_ELAPSED_P50"]
    if r50 == NOT_IDENTIFIED or a50 == NOT_IDENTIFIED:
        out["MEDIAN_ELAPSED_GAP_S"] = NOT_IDENTIFIED
        out["TIMING_COMPARABLE"] = NOT_IDENTIFIED
        return out

    gap = a50 - r50
    out["MEDIAN_ELAPSED_GAP_S"] = gap
    spread = max(lanes["ROUTED"] + lanes["AUDIT"]) - min(
        lanes["ROUTED"] + lanes["AUDIT"])
    # A gap worth more than a tenth of the scan's own span means the two lanes
    # saw different amounts of market drift, and the false-negative rate that
    # comes out of them is measuring the scan, not the routing rule.
    out["SCAN_SPAN_S"] = spread
    out["TIMING_COMPARABLE"] = (
        True if spread > 0 and abs(gap) <= 0.10 * spread
        else (NOT_IDENTIFIED if spread == 0 else False))
    if out["TIMING_COMPARABLE"] is False:
        out["TIMING_DEFECT"] = (
            "AUDIT_LANE_OBSERVED_AT_MATERIALLY_DIFFERENT_ELAPSED_TIME: the "
            "measured false-negative rate is confounded with scan drift and "
            "is NOT a routing-rule error rate")
    return out


# The three questions, kept apart because only the first two are answerable
# from anything above.
QUESTION_GENERALIZATION = "IS THE UFC MICROSTRUCTURE REPRESENTATIVE?"
QUESTION_ELIGIBILITY = "HOW MANY MARKETS PASS THE PRE-QUOTE SCREENS?"
QUESTION_ECONOMICS = "ARE THOSE MARKETS PROFITABLE TO MAKE?"
QUESTION_ECONOMICS_ANSWERABLE_HERE = False
