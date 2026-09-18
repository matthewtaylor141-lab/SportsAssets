#!/usr/bin/env python3
"""PREDECLARED EVENT SELECTION FOR THE SUBSTANTIVE PUBLIC-BOOK CAPTURE.

WHY THE SELECTION IS FROZEN BEFORE THE FIRST SAMPLED GET. A roster chosen after
seeing how the markets behaved is not a sample of the board; it is a sample of
the outcome. This module reads the board once, applies the eligibility rules
that already exist in `forward/eligibility.py`, ranks what survives by a rule
declared in code rather than by eye, and writes the roster to disk. The capture
then reads that file. There is no path by which a market can join or leave the
roster after sampling starts.

WHAT MAKES AN EVENT ELIGIBLE -- all of them, not a majority:

    VENUE_NATIVE_IDENTITY   gameStartTime plus >= 2 team ids, so the contest is
                            named by the venue rather than parsed out of a
                            question title
    KNOWN_GAME_START        the clock exists
    TWO_ELIGIBLE_MARKETS    at least two markets on that same contest
    TWO_SIDED_BOOK          both markets show a bid AND an ask
    NOT_A_SEASON_FUTURE     the start is within the futures horizon
    NOT_A_LONG_DATED_PROP   same test; a proposition with no contest clock has
                            no identity here anyway and is already excluded
    CURRENT_ACTIVITY        the existing BROAD/ACTIVE/HIGH_ACTIVITY ladder

PREFERENCE, AND IT IS A RULE NOT A TASTE. Rank first the events already LIVE or
starting soon enough that a 90-minute capture spans the pre-game -> live
transition, then by how much of the capture window sits after the start. Ties
break on the frozen salted hash, never on activity magnitude -- ranking by
activity and then reporting an activity distribution measured on the winners is
the circularity this programme has already been taught once.

IF FEWER THAN THREE EVENTS QUALIFY THIS REFUSES. `SELECTION_STATUS` comes back
INSUFFICIENT_QUALIFYING_EVENTS and the capture does not start. A stale future
substituted to fill the roster would answer a different question than the one
asked, while looking like an answer to this one.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import book_schema as BS
import event_identity as EI
import events_adapter as EA

NOT_IDENTIFIED = "NOT_IDENTIFIED"
SELECTION_SALT = "BETA48-SUBSTANTIVE-2026-09-17"
EVENTS_REQUIRED = 3
MARKETS_PER_EVENT = 2
FUTURES_HORIZON_S = 72 * 3600          # beyond this it is a future, not a game
LIVE_PREFERRED = True
CAPTURE_WINDOW_S_DEFAULT = 90 * 60

SELECTION_OK = "FROZEN"
SELECTION_INSUFFICIENT = "INSUFFICIENT_QUALIFYING_EVENTS"
SELECTION_BOARD_INCOMPLETE = "BOARD_RETRIEVAL_INCOMPLETE"

# --- Board retrieval: five outcomes, only one of which is an enumerated board.
#
# RUN 35333848994 IS WHY THIS EXISTS. The walk did
#
#     body = r.json() if r.status_code == 200 else {}
#     items = body.get("markets") or body.get("data") or []
#     if not items or fresh == 0:
#         exhausted = True
#
# so a non-200, a 200 whose payload used a key we do not read, and a page the
# server repeated because it ignored `offset` ALL produced the same bytes as a
# genuinely empty page -- and every one of them was then written down as
# BOARD_LIST_EXHAUSTED = YES. The run reported INSUFFICIENT_QUALIFYING_EVENTS
# over a universe of ONE market. A failed retrieval is not a small board.
#
# Only VERIFIED_END_OF_BOARD may be called an enumerated universe. The others
# say what went wrong and leave the universe unproven.
BOARD_VERIFIED_END = "VERIFIED_END_OF_BOARD"
BOARD_HTTP_FAILURE = "HTTP_FAILURE"
BOARD_SCHEMA_FAILURE = "SCHEMA_FAILURE"
BOARD_PAGINATION_STALLED = "PAGINATION_STALLED"
BOARD_PAGE_CAP_REACHED = "PAGE_CAP_REACHED"
# A SIXTH STATUS, STRICTER THAN THE FIVE ASKED FOR, AND HERE IS WHY.
# The failed run's FIRST request returned one row against limit=100. Treating
# a short FIRST page as a verified end would certify "the venue's whole board
# is one market" from a single request in which pagination was never observed
# to work at all -- the same unsupported claim the repair exists to stop, in
# new clothes. An enumeration needs pagination to have demonstrably advanced:
# at least one full page, or an explicitly empty follow-on page.
BOARD_SINGLE_PAGE_UNCORROBORATED = "SINGLE_PAGE_UNCORROBORATED"
BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED = "COMPLETION_CONTRACT_NOT_ESTABLISHED"
BOARD_PAGINATION_CONTRADICTS_END = "PAGINATION_CONTRADICTS_END"
BOARD_RETRIEVAL_STATUSES = (
    BOARD_VERIFIED_END, BOARD_HTTP_FAILURE, BOARD_SCHEMA_FAILURE,
    BOARD_PAGINATION_STALLED, BOARD_PAGE_CAP_REACHED,
    BOARD_SINGLE_PAGE_UNCORROBORATED,
    BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED,
    BOARD_PAGINATION_CONTRADICTS_END)
ONE_REQUEST_IS_NOT_AN_ENUMERATION = (
    "a short first page says the venue returned fewer rows than we asked for "
    "on one request; it does not say the board ends there, because nothing "
    "in that single exchange shows paging working. Only a full page followed "
    "by a shorter or empty one demonstrates the walk advanced and then ended")
VALID_UNIVERSE_WITH_SELECTION_SHORTFALL = (
    "VALID_UNIVERSE_WITH_SELECTION_SHORTFALL")
A_FAILED_RETRIEVAL_IS_NOT_A_SMALL_BOARD = (
    "a non-200, an unparseable body, a body with no row key we read, and a "
    "page the server repeated are four different failures. Converting any of "
    "them to an empty list and calling the walk exhausted turns 'we could not "
    "read the board' into 'the board is empty', which is the one claim the "
    "evidence cannot support")
ROW_KEYS = ("markets", "data", "items", "results")

WAIT_RATHER_THAN_SUBSTITUTE = (
    "a stale future substituted to fill the roster answers a different "
    "question while looking like an answer to this one")
RANK_IS_NOT_ACTIVITY_MAGNITUDE = (
    "ranking by activity and then reporting an activity distribution measured "
    "on the winners is circular; magnitude only gates, it never ranks")

ELIGIBILITY_CONDITIONS = (
    "VENUE_NATIVE_IDENTITY", "KNOWN_GAME_START", "TWO_ELIGIBLE_MARKETS",
    "TWO_SIDED_BOOK", "NOT_A_SEASON_FUTURE", "NOT_A_LONG_DATED_PROP",
    "CURRENT_ACTIVITY")


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _salt_rank(key):
    return hashlib.sha256((SELECTION_SALT + "|" + str(key)).encode()).hexdigest()


def two_sided(book_body):
    """A book with a bid AND an offer. One side is not a two-sided book.

    NATIVE-SCHEMA CORRECTION (authorised 2026-09-18). This used to read
    book_body["bids"] / ["asks"]; the venue sends
    body["marketData"]["bids"] / ["offers"]. All 15 retained BLOCK_4 books are
    genuinely two-sided and the old reader accepted none of them, so every
    candidate was refused BOOK_NOT_TWO_SIDED and the roster could never fill.

    The REQUIREMENT is unchanged -- both sides, or it is not a two-sided book.
    Only the field names it looks under are corrected, and they are defined
    once in `book_schema` so this reader and throughput_v1's cannot drift
    apart again.
    """
    return BS.two_sided(book_body)


def book_refusal_reason(book_body):
    """Why a book was not two-sided, for the row-accounting reason string."""
    return BS.reason(book_body)


def identity_block(market):
    """The frozen per-market identity that will travel on every captured row."""
    eid, level = EI.event_identity(market)
    lg = EI.leagues(market)
    return {
        "EVENT_ID": eid,
        "EVENT_KEY": eid,
        "EVENT_IDENTITY_LEVEL": level,
        "MARKET_ID": str(market.get("id") or market.get("marketId")
                         or NOT_IDENTIFIED),
        "MARKET_SLUG": market.get("slug") or NOT_IDENTIFIED,
        "GAME_START": market.get("gameStartTime") or NOT_IDENTIFIED,
        "SPORT": (market.get("sport") or market.get("category")
                  or NOT_IDENTIFIED),
        "LEAGUE": (sorted(lg)[0] if lg else NOT_IDENTIFIED),
        "MARKET_SIDES": EI.sides(market) or NOT_IDENTIFIED,
    }


def event_rank(start_dt, now_dt, window_s=CAPTURE_WINDOW_S_DEFAULT):
    """Lower sorts first. Declared in code, applied to every candidate alike.

    TIER 0  already live, or starting inside the window: the capture spans the
            pre-game -> live transition, which is the state change worth having
    TIER 1  starting after the window: eligible, but the capture sees pre-game
            only
    """
    if start_dt is None or now_dt is None:
        return (2, 0)
    delta = (start_dt - now_dt).total_seconds()
    if delta <= 0:
        return (0, -delta)                 # live; longer-live first
    if delta < window_s:
        return (0, window_s - delta)       # transition observed
    return (1, delta)


def eligible_events(markets, books_by_slug, now_iso,
                    window_s=CAPTURE_WINDOW_S_DEFAULT, activity_of=None):
    """Group the board by venue-native event and keep only what qualifies.

    `books_by_slug` is the stage-2 book read per candidate market;
    `activity_of` maps slug -> the existing eligibility verdict dict. Both are
    supplied by the caller so this module reads no network itself.
    """
    now = _parse(now_iso)
    activity_of = activity_of or {}
    books_by_slug = books_by_slug or {}
    by_event = {}
    # EVERY ROW LANDS SOMEWHERE. `row_reason` maps slug -> why it did not reach
    # the roster, and a row that is selected is recorded as SELECTED. There is
    # no `continue` here that leaves a row unaccounted for: run 35209604615
    # reported EVENTS_QUALIFYING = 0 with REJECTED_EVENTS = [], which said
    # nothing at all about where 600 rows went.
    row_reason = {}
    for m in markets or ():
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        if not slug:
            continue
        # THE CLOCK IS TESTED FIRST, AND ONLY SO THE REASON IS HONEST.
        # `event_identity` already refuses a row with no gameStartTime, but it
        # refuses it as "no team binding", which would report a row that has
        # both its teams as though it had neither. The gate is the same; the
        # reason is the true one.
        start = _parse(m.get("gameStartTime"))
        if start is None:
            row_reason[slug] = "NO_GAME_START"
            continue
        eid, level = EI.event_identity(m)
        if eid == NOT_IDENTIFIED or level != EI.LEVEL_V1_CONTEST:
            # NOT a downgrade to SUBJECT and NOT a title fallback: a row the
            # venue does not give two team ids for has no contest identity, and
            # is refused rather than guessed.
            row_reason[slug] = ("NO_VENUE_NATIVE_CONTEST_IDENTITY:%s" % level)
            continue
        if (start - now).total_seconds() > FUTURES_HORIZON_S:
            row_reason[slug] = "SEASON_FUTURE_BEYOND_HORIZON"
            continue
        by_event.setdefault(eid, []).append(m)

    out, rejected = [], []
    for eid, ms in by_event.items():
        keep = []
        for m in ms:
            slug = m.get("slug")
            act = activity_of.get(slug) or {}
            if slug not in books_by_slug:
                row_reason[slug] = "NO_BOOK_READ_WITHIN_READ_BUDGET"
                continue
            if not two_sided(books_by_slug.get(slug)):
                # The SPECIFIC reason, not a single flat verdict: a book that
                # never arrived, a malformed container and a genuinely
                # one-sided book are three different facts about the venue.
                row_reason[slug] = "BOOK_NOT_TWO_SIDED:%s" % (
                    book_refusal_reason(books_by_slug.get(slug)))
                continue
            if not (act.get("HIGH_ACTIVITY_AT_DECISION")
                    or act.get("ACTIVE_AT_DECISION")
                    or act.get("BROAD_AT_DECISION")):
                row_reason[slug] = "NOT_CURRENTLY_ACTIVE"
                continue
            keep.append(m)
        if len(keep) < MARKETS_PER_EVENT:
            rejected.append({"EVENT_ID": eid,
                             "REASON": "FEWER_THAN_%d_ELIGIBLE_MARKETS"
                                       % MARKETS_PER_EVENT,
                             "ELIGIBLE_MARKETS": len(keep)})
            for m in keep:
                row_reason[m.get("slug")] = ("EVENT_HAS_FEWER_THAN_%d_"
                                             "ELIGIBLE_MARKETS"
                                             % MARKETS_PER_EVENT)
            continue
        keep.sort(key=lambda m: _salt_rank(m.get("slug")))
        start = _parse(keep[0].get("gameStartTime"))
        out.append({
            "EVENT_ID": eid,
            "EVENT_NAME": (keep[0].get("eventTitle")
                           or keep[0].get("title") or NOT_IDENTIFIED),
            "GAME_START": keep[0].get("gameStartTime") or NOT_IDENTIFIED,
            "RANK": event_rank(start, now, window_s),
            "MARKETS": keep[:MARKETS_PER_EVENT],
            "ELIGIBLE_MARKET_COUNT": len(keep),
        })
        for m in keep[MARKETS_PER_EVENT:]:
            row_reason[m.get("slug")] = "ELIGIBLE_BUT_BEYOND_MARKETS_PER_EVENT"
    out.sort(key=lambda e: (e["RANK"], _salt_rank(e["EVENT_ID"])))
    return out, rejected, row_reason


def freeze(markets, books_by_slug, now_iso, window_s=CAPTURE_WINDOW_S_DEFAULT,
           activity_of=None, events_required=EVENTS_REQUIRED):
    """Produce the frozen roster, or refuse and say the roster is short."""
    ranked, rejected, row_reason = eligible_events(
        markets, books_by_slug, now_iso, window_s, activity_of)
    chosen = ranked[:events_required]
    ok = len(chosen) >= events_required
    if not ok:
        # A SHORTFALL EMITS NOTHING. Returning the two events that did qualify
        # would leave a usable-looking roster on disk beside a NO verdict, and
        # the next thing that reads the file might take the roster and not the
        # verdict. Waiting is the answer; a partial roster is not a smaller
        # version of the answer.
        chosen = []
    rows, identity_of = [], {}
    for e in chosen:
        for m in e["MARKETS"]:
            blk = identity_block(m)
            identity_of[blk["MARKET_SLUG"]] = blk
            rows.append(dict(blk, EVENT_NAME=e["EVENT_NAME"],
                             SELECTION_REASON=_reason(e)))
    selected_slugs = {r["MARKET_SLUG"] for r in rows}

    # AN EVENT THAT QUALIFIED BUT WAS NOT IN THE TOP N IS STILL ACCOUNTED FOR.
    for e in ranked[len(chosen):]:
        rejected.append({"EVENT_ID": e["EVENT_ID"],
                         "REASON": "QUALIFIED_BUT_NOT_TOP_%d_BY_FROZEN_RANK"
                                   % events_required,
                         "RANK": e["RANK"]})
        for m in e["MARKETS"]:
            row_reason[m.get("slug")] = ("EVENT_QUALIFIED_BUT_NOT_TOP_%d"
                                         % events_required)
    if not ok:
        for e in ranked:
            for m in e["MARKETS"]:
                row_reason.setdefault(
                    m.get("slug"), "ROSTER_SHORT_NOTHING_SELECTED")

    acct = row_accounting(markets, selected_slugs, row_reason)
    return {
        "ROW_ACCOUNTING": acct,
        "UNACCOUNTED_ROWS": acct["UNACCOUNTED_ROWS"],
        "IDENTITY_FAILURES": acct["IDENTITY_FAILURES"],
        "EVENTS_EVALUATED": len(ranked) + len(
            [r for r in rejected if r.get("REASON", "").startswith("FEWER")]),
        "CANONICAL_EVENTS_RESOLVED": acct["CANONICAL_EVENTS_RESOLVED"],
        "EVENTS_REJECTED": len(rejected),
        "SELECTION_STATUS": SELECTION_OK if ok else SELECTION_INSUFFICIENT,
        "EVENT_SELECTION_FROZEN": "YES" if ok else "NO",
        "FROZEN_AT": now_iso,
        "FROZEN_BEFORE_THE_FIRST_SAMPLED_GET": True,
        "SELECTION_SALT": SELECTION_SALT,
        "EVENTS_REQUIRED": events_required,
        "EVENTS_QUALIFYING": len(ranked),
        "EVENTS_SELECTED": len(chosen),
        "MARKETS_PER_EVENT": MARKETS_PER_EVENT,
        "MARKETS_SELECTED": len(rows),
        "ELIGIBILITY_CONDITIONS": list(ELIGIBILITY_CONDITIONS),
        "EVENT_IDS": [e["EVENT_ID"] for e in chosen],
        "EVENT_NAMES": [e["EVENT_NAME"] for e in chosen],
        "GAME_STARTS": [e["GAME_START"] for e in chosen],
        "MARKET_IDS": [r["MARKET_ID"] for r in rows],
        "MARKET_SLUGS": [r["MARKET_SLUG"] for r in rows],
        "SELECTION_REASON": {e["EVENT_ID"]: _reason(e) for e in chosen},
        "IDENTITY_OF": identity_of,
        "REJECTED_EVENTS": rejected[:50],
        "RANK_IS_NOT_ACTIVITY_MAGNITUDE": RANK_IS_NOT_ACTIVITY_MAGNITUDE,
        "WAIT_RATHER_THAN_SUBSTITUTE": WAIT_RATHER_THAN_SUBSTITUTE,
        "NO_SUBSTITUTION_ON_SHORTFALL": True,
        "CAPTURE_MAY_START": "YES" if ok else "NO",
    }


SELECTED = "SELECTED"
NO_INVISIBLE_DROP_PATH = (
    "every board row reaching the selection pipeline lands in SELECTED or "
    "REJECTED_WITH_REASON; run 35209604615 reported zero qualifying events and "
    "an EMPTY rejection list, which said nothing about where 600 rows went")


def row_accounting(markets, selected_slugs, row_reason):
    """Every row in, every row accounted for. UNACCOUNTED_ROWS must be 0.

    This is the audit that the previous run could not produce. A row is either
    SELECTED or REJECTED_WITH_REASON; anything else is a hole in the pipeline
    and is counted so it cannot hide.
    """
    selected_slugs = set(selected_slugs or ())
    rows, counts, unaccounted = [], {}, []
    ident_fail = 0
    seen = set()
    for m in markets or ():
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if slug in selected_slugs:
            disp, reason = SELECTED, SELECTED
        else:
            reason = row_reason.get(slug)
            if reason is None:
                disp = "UNACCOUNTED"
                reason = NOT_IDENTIFIED
                unaccounted.append(slug)
            else:
                disp = "REJECTED_WITH_REASON"
        if str(reason).startswith("NO_VENUE_NATIVE_CONTEST_IDENTITY"):
            ident_fail += 1
        counts[reason] = counts.get(reason, 0) + 1
        rows.append({"MARKET_SLUG": slug, "DISPOSITION": disp,
                     "REASON": reason})
    canonical = len({EI.event_identity(m)[0] for m in (markets or ())
                     if isinstance(m, dict)
                     and EI.event_identity(m)[1] == EI.LEVEL_V1_CONTEST})
    return {
        "ROWS_IN": len(seen),
        "SELECTED": len(selected_slugs & seen),
        "REJECTED_WITH_REASON": sum(1 for r in rows
                                    if r["DISPOSITION"]
                                    == "REJECTED_WITH_REASON"),
        "UNACCOUNTED_ROWS": len(unaccounted),
        "UNACCOUNTED_SLUGS": unaccounted[:50],
        "IDENTITY_FAILURES": ident_fail,
        "CANONICAL_EVENTS_RESOLVED": canonical,
        "REASON_COUNTS": dict(sorted(counts.items())),
        "ROWS": rows,
        "NO_INVISIBLE_DROP_PATH": NO_INVISIBLE_DROP_PATH,
        "EVERY_ROW_ACCOUNTED": len(unaccounted) == 0,
    }


def _reason(e):
    tier = e["RANK"][0]
    return ("LIVE_OR_TRANSITION_WITHIN_CAPTURE_WINDOW" if tier == 0 else
            "ELIGIBLE_PREGAME_ONLY" if tier == 1 else "ELIGIBLE_NO_CLOCK_RANK")


def render(sel):
    L = ["%-44s = %s" % (k, sel[k]) for k in (
        "SELECTION_STATUS", "EVENT_SELECTION_FROZEN", "EVENTS_QUALIFYING",
        "EVENTS_SELECTED", "MARKETS_SELECTED", "CAPTURE_MAY_START")]
    for eid, name, gs in zip(sel["EVENT_IDS"], sel["EVENT_NAMES"],
                             sel["GAME_STARTS"]):
        L.append("    EVENT %s  start=%s  %s" % (eid, gs, name))
        L.append("          reason=%s" % sel["SELECTION_REASON"][eid])
    for s in sel["MARKET_SLUGS"]:
        L.append("    MARKET %s" % s)
    return "\n".join(L)


def write(path, sel):
    Path(path).write_text(json.dumps(sel, indent=1, sort_keys=True,
                                     default=str))
    return sel


# ---------------------------------------------------------------------------
# THE CLI, AND THE ONLY PART OF THIS FILE THAT READS THE NETWORK.
#
# Every decision function above is pure: it is handed the board rows, the book
# bodies and the activity verdicts, and it returns a roster. The reads live
# here so the selection rules can be tested exhaustively without a venue, and
# so no rule can quietly depend on the order the network answered in.
#
# The selection reads are PACED AT THE SAME 0.25 RPS as the capture and come
# out of the same pacer instance, so discovery cannot burst above the one rate
# this programme has validated. They happen strictly before the first sampled
# GET and are counted separately from the capture's own requests.
# ---------------------------------------------------------------------------

MARKETS_PATH = "/v1/markets"

# --- The endpoint's completion contract, per endpoint, from evidence. ------
#
# "A short page means the end" is a CONVENTION, not a fact about a venue. It
# holds only where that endpoint's paging semantics are established. Run
# 35333848994 assumed it for /v1/markets and certified a one-market board.
#
# EVENTS_PATH: established from RETAINED evidence -- Track B-L restored
#   {"active":"true","closed":"false"} with forward `offset` paging and
#   walked to the first empty page, and the 2G-R sealed log shows ~1,900
#   events across 19 offset pages with disjoint id sets per page.
# MARKETS_PATH: NOT ESTABLISHED. Phase 2B recorded that /v1/markets returns
#   eventSlug = None on every row, which is why later phases moved discovery
#   to /v1/events; no retained capture demonstrates that /v1/markets honours
#   `offset` or terminates. Completion on this endpoint is NOT_IDENTIFIED and
#   may not be certified until the contract is established. Switching
#   endpoint is a separate, approved decision -- never an automatic fallback.
EVENTS_PATH = "/v1/events"
TERMINAL_EMPTY_PAGE = "EMPTY_ROW_ARRAY_AT_AN_OFFSET"

# WHAT THE RETAINED EVIDENCE ACTUALLY SHOWS, KEPT APART FROM WHAT IT DOES NOT.
#
# The previous revision of this table said /v1/events was ESTABLISHED on the
# basis of "RETAINED_EVIDENCE_TRACK_BL_BLOCK_3". That was wrong in the one way
# this file exists to prevent. BLOCK_3's own sealed summary records
#
#     PAGINATION_ADVANCES        YES
#     DISCOVERY_LIST_EXHAUSTED   NO
#     FIRST_TERMINAL_OFFSET      null
#     discovery_pages            26
#     EVENTS_DISCOVERED          2600
#
# and its 26 retained page receipts are every one of them a full 100 events at
# offsets 0..2500, with 2,600 distinct event ids and no repeats. That evidence
# establishes that the offset ADVANCES. It says nothing whatever about how the
# walk ENDS, because the walk never ended -- it stopped at its own page cap.
#
# So the two halves are recorded separately. The advance half is OBSERVED. The
# terminal half is DECLARED from the documented limit/offset semantics and has
# never been seen, which means it must be OBSERVED IN THE RUN THAT CLAIMS IT.
# `certify_completion` enforces exactly that: no run inherits another run's
# ending.
BOARD_COMPLETION_CONTRACTS = {
    EVENTS_PATH: {
        "CONTRACT": "OFFSET_FORWARD_UNTIL_EMPTY_PAGE",
        "DECLARED_TERMINAL_CONDITION": TERMINAL_EMPTY_PAGE,
        "TERMINAL_BASIS": "DOCUMENTED_LIMIT_OFFSET_SEMANTICS",
        "ADVANCE_OBSERVED": True,
        "ADVANCE_BASIS": ("RETAINED_BLOCK_3_RECEIPTS_26_PAGES_2600_DISTINCT_"
                          "EVENT_IDS_0_REPEATS"),
        "TERMINAL_OBSERVED_IN_RETAINED_EVIDENCE": False,
        "TERMINAL_MUST_BE_OBSERVED_IN_THIS_RUN": True,
        "SHORT_PAGE_IS_TERMINAL": False,
        "ESTABLISHED": True,
    },
    MARKETS_PATH: {
        "CONTRACT": NOT_IDENTIFIED,
        "DECLARED_TERMINAL_CONDITION": NOT_IDENTIFIED,
        "TERMINAL_BASIS": NOT_IDENTIFIED,
        "ADVANCE_OBSERVED": False,
        "ADVANCE_BASIS": "NO_RETAINED_CAPTURE_DEMONSTRATES_OFFSET_PAGING",
        "TERMINAL_OBSERVED_IN_RETAINED_EVIDENCE": False,
        "TERMINAL_MUST_BE_OBSERVED_IN_THIS_RUN": True,
        "SHORT_PAGE_IS_TERMINAL": False,
        "ESTABLISHED": False,
    },
}
AN_ADVANCE_IS_NOT_AN_ENDING = (
    "26 pages that each advanced the offset prove the offset advances. They "
    "do not prove what the venue returns when the rows run out, because the "
    "rows never ran out -- the walk hit its own cap. ESTABLISHED here means "
    "'this endpoint has a declared terminal condition a run may be required "
    "to observe', never 'a previous run already observed it'")
A_CONVENTION_IS_NOT_A_CONTRACT = (
    "a short page terminates a walk only on an endpoint whose paging "
    "semantics are established. Where the contract is not established the "
    "walk may still run, but completion stays NOT_IDENTIFIED and no "
    "enumeration may be claimed")
PAGINATION_EVIDENCE_MAY_CONTRADICT_A_SHORT_PAGE = (
    "if the venue's own pagination fields say more rows exist -- has_more "
    "true, or a total above the rows retrieved -- then a short page is a "
    "truncated response, not the end of the board, and the contradiction "
    "wins")
BOARD_PAGE_LIMIT = 100
# THE CAP THAT CAUSED RUN 35209604615 TO SELECT NOTHING.
#
# The old cap was 6 pages -- 600 rows -- and the board is ordered futures
# first. The 600 rows the run actually saw were 596 futures and 4 elections:
# not one contest row, so the identity filter never met a game and reported
# zero qualifying events. The board is ~20,000 markets, of which ~1,557 carry
# two venue team ids (every moneyline, every spread).
#
# The walk now runs to EXHAUSTION -- a short page, or a page that contributes
# no new slug -- and BOARD_LIST_EXHAUSTED records which. The cap survives only
# as a runaway bound far above the observed board, and hitting it is reported
# as NOT exhausted so a truncated universe can never look like a complete one.
BOARD_MAX_PAGES = 400
BOARD_CAP_IS_A_RUNAWAY_BOUND_NOT_A_STOP_RULE = True
CANDIDATE_BOOK_READS_MAX = 40


def _row_slug(m):
    """Return (slug, problem). A malformed row is DESCRIBED, never raised on.

    The pinned walk did `by_slug[m["slug"]]` after checking only
    `m.get("slug") not in by_slug`, so a row with no slug key raised KeyError
    and took the whole step down with no receipt.
    """
    if not isinstance(m, dict):
        return None, "ROW_NOT_A_MAPPING:%s" % type(m).__name__
    if "slug" not in m:
        return None, "ROW_MISSING_SLUG_KEY"
    slug = m["slug"]
    if not isinstance(slug, str) or not slug:
        return None, "ROW_SLUG_NOT_A_NON_EMPTY_STRING:%r" % (slug,)
    return slug, None


def _rows_of(body):
    """Return (rows, shape). `rows` is None when no row key could be read.

    The distinction that run 35333848994 lost: a recognised key holding an
    EMPTY list is a real short page; a body with no recognised key at all is a
    schema failure. Both used to become `[]`.
    """
    if isinstance(body, list):
        return list(body), "BARE_LIST"
    if not isinstance(body, dict):
        return None, "NOT_A_MAPPING_OR_LIST:%s" % type(body).__name__
    for k in ROW_KEYS:
        if k in body:
            v = body[k]
            if isinstance(v, list):
                return list(v), "MAPPING_KEY:%s" % k
            return None, "MAPPING_KEY_NOT_A_LIST:%s:%s" % (k, type(v).__name__)
    return None, "NO_ROW_KEY:%s" % ",".join(sorted(body)[:8])


def _pagination_fields(body):
    """Whatever the venue says about paging, recorded and never interpreted."""
    if not isinstance(body, dict):
        return {}
    names = ("next", "next_cursor", "nextCursor", "cursor", "offset", "limit",
             "total", "total_count", "totalCount", "has_more", "hasMore",
             "page", "pages", "count")
    return {n: body[n] for n in names if n in body}


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat(
        ).replace("+00:00", "Z")


NO_RESPONSE_RECEIVED = "NO_RESPONSE_RECEIVED"


def _response_bytes(r):
    """Exactly what arrived, before status or JSON is considered.

    Returns None only when the object genuinely carries no body -- which, for
    a real response, is itself worth recording. httpx exposes `.content`;
    `.text` is the fallback; a test double may have neither.
    """
    raw = getattr(r, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    txt = getattr(r, "text", None)
    if isinstance(txt, str):
        return txt.encode("utf-8", "replace")
    return None


def _body_sha256(body):
    try:
        return hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"),
            default=str).encode()).hexdigest()
    except Exception:                                         # noqa: BLE001
        return NOT_IDENTIFIED


def pagination_contradicts_end(receipts, rows_seen):
    """The venue's own paging fields disputing THIS walk's ending.

    A CONTINUATION FLAG ON AN ORDINARY NONTERMINAL PAGE MEANS CONTINUE.
    The previous revision scanned every receipt for `has_more` and refused the
    walk if any page carried it -- which is every healthy paginated walk ever:
    page 1 of 26 saying "there is more" is the endpoint working correctly, not
    a pagination error. Only the LAST page's flag can contradict the claim that
    the last page was last.

    A declared TOTAL is different in kind. It is a statement about the whole
    collection, so it contradicts wherever it appears, and it is compared
    against rows of the SAME KIND the caller counted -- event rows for an event
    listing, market rows for a market listing. Comparing a venue event total
    against extracted market rows would be the event/market count confusion,
    not a completeness check.
    """
    receipts = list(receipts or ())
    for k in ("total", "total_count", "totalCount", "count"):
        for rec in receipts:
            v = (rec.get("PAGINATION_FIELDS") or {}).get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v > rows_seen:
                return "PAGINATION_FIELD_%s_%d_EXCEEDS_ROWS_RETRIEVED_%d" % (
                    k, v, rows_seen)
    if receipts:
        pf = receipts[-1].get("PAGINATION_FIELDS") or {}
        for k in ("has_more", "hasMore"):
            if pf.get(k) is True:
                return "TERMINAL_PAGE_PAGINATION_FIELD_%s_IS_TRUE" % k
    return None


A_CONTINUATION_FLAG_MIDWALK_IS_NOT_AN_ERROR = (
    "has_more on page 1 of 26 is the endpoint telling the walk to keep going. "
    "Only the terminal page's own flag can dispute the ending")


def board_walk(get, max_pages=BOARD_MAX_PAGES, page_limit=BOARD_PAGE_LIMIT,
               endpoint=MARKETS_PATH, retain_bodies=None):
    """Walk the board, classifying HOW the walk ended and retaining receipts.

    `get(params)` returns a response carrying `.status_code` and `.json()`;
    injecting it is what makes this path testable without a venue, which is
    the second half of the defect -- the old walk lived inside `_cli()` and
    only the pure eligibility functions had tests.

    Returns (by_slug, receipts, status). Only BOARD_VERIFIED_END means the
    universe was enumerated.
    """
    by_slug, receipts, status = {}, [], BOARD_PAGE_CAP_REACHED
    pages = 0
    while pages < max_pages:
        params = {"active": "true", "closed": "false",
                  "limit": page_limit, "offset": pages * page_limit}
        rec = {"PAGE": pages, "ENDPOINT": endpoint, "PARAMS": dict(params),
               "REQUEST_UTC": _utc_now(), "RECEIPT_UTC": NOT_IDENTIFIED,
               "HTTP_STATUS": NOT_IDENTIFIED, "RESPONSE_SHAPE": NOT_IDENTIFIED,
               "ROWS_RETURNED": NOT_IDENTIFIED, "FRESH_SLUGS": NOT_IDENTIFIED,
               "PAGINATION_FIELDS": {}, "REPEATED_EARLIER_PAGE": NOT_IDENTIFIED,
               "BODY_SHA256": NOT_IDENTIFIED, "BODY_ARTIFACT": NOT_IDENTIFIED,
               "MALFORMED_ROWS": (), "ERROR": NOT_IDENTIFIED}
        try:
            r = get(params)
            rec["RECEIPT_UTC"] = _utc_now()
            rec["HTTP_STATUS"] = getattr(r, "status_code", NOT_IDENTIFIED)
        except Exception as e:                                # noqa: BLE001
            rec["ERROR"] = "%s: %s" % (type(e).__name__, e)
            receipts.append(rec)
            status = BOARD_HTTP_FAILURE
            break
        pages += 1
        if rec["HTTP_STATUS"] != 200:
            receipts.append(rec)
            status = BOARD_HTTP_FAILURE
            break
        try:
            body = r.json()
        except Exception as e:                                # noqa: BLE001
            rec["ERROR"] = "%s: %s" % (type(e).__name__, e)
            rec["RESPONSE_SHAPE"] = "UNPARSEABLE_BODY"
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        rec["BODY_SHA256"] = _body_sha256(body)
        if retain_bodies is not None:
            rec["BODY_ARTIFACT"] = retain_bodies(rec["BODY_SHA256"], body)
        rows, shape = _rows_of(body)
        rec["RESPONSE_SHAPE"] = shape
        rec["PAGINATION_FIELDS"] = _pagination_fields(body)
        if rows is None:
            rec["ROWS_RETURNED"] = 0
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        fresh, malformed = 0, []
        for i, m in enumerate(rows):
            slug, problem = _row_slug(m)
            if problem:
                malformed.append({"INDEX": i, "PROBLEM": problem})
                continue
            if slug not in by_slug:
                by_slug[slug] = m
                fresh += 1
        rec["ROWS_RETURNED"] = len(rows)
        rec["FRESH_SLUGS"] = fresh
        rec["MALFORMED_ROWS"] = tuple(malformed)
        rec["REPEATED_EARLIER_PAGE"] = bool(rows) and fresh == 0
        # A MALFORMED ROW IS A SCHEMA FAILURE WITH A RETAINED RECEIPT, not an
        # uncaught KeyError that takes the step down and seals nothing.
        if malformed:
            rec["RESPONSE_SHAPE"] = shape + "+MALFORMED_ROWS:%d" % len(malformed)
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        receipts.append(rec)
        if not rows:
            # An empty follow-on page is a real end; an empty FIRST page is
            # one request that returned nothing and proves no enumeration.
            status = (BOARD_VERIFIED_END if pages > 1
                      else BOARD_SINGLE_PAGE_UNCORROBORATED)
            break
        if fresh == 0:
            status = BOARD_PAGINATION_STALLED    # server ignored `offset`
            break
        if len(rows) < page_limit:
            status = (BOARD_VERIFIED_END if pages > 1
                      else BOARD_SINGLE_PAGE_UNCORROBORATED)
            break
    return by_slug, receipts, status


def discovery_walk(get, max_pages=BOARD_MAX_PAGES, page_limit=BOARD_PAGE_LIMIT,
                   endpoint=EVENTS_PATH, retain_bodies=None):
    """Walk /v1/events and flatten it to child market rows via the adapter.

    The authorised V1 discovery source. It is the same receipt discipline and
    the same failure vocabulary as `board_walk` -- that function is left
    untouched for the markets endpoint -- with three differences the event
    listing forces:

    1. A page's rows are EVENTS. Pagination, staleness and the terminal
       condition are all judged on the EVENT count, because that is the thing
       the offset walks. The market rows are a product of the page, not the
       page's length, and judging the walk on them would be comparing an
       event-level total with a market-row count.
    2. A SHORT PAGE IS NOT THE END. The declared terminal condition for this
       endpoint is an empty rows array, so a page of 40 events at limit 100
       means "read the next offset", not "the board is enumerated". This is
       strictly more conservative than the markets walk and it is deliberate:
       the terminal condition has never been observed, so the only thing that
       may be allowed to prove it is the thing itself.
    3. Extraction happens per page, so a malformed child row is attributed to
       the page and offset it arrived on.

    Returns (by_slug, event_index, receipts, status, adapter_block).
    """
    by_slug, event_index, receipts, blocks = {}, {}, [], []
    index = EA.WalkIndex()          # ONE index for the WHOLE walk
    status = BOARD_PAGE_CAP_REACHED
    pages = 0
    while pages < max_pages:
        offset = pages * page_limit
        params = {"active": "true", "closed": "false",
                  "limit": page_limit, "offset": offset}
        rec = {"PAGE": pages, "ENDPOINT": endpoint, "PARAMS": dict(params),
               "REQUEST_UTC": _utc_now(), "RECEIPT_UTC": NOT_IDENTIFIED,
               "HTTP_STATUS": NOT_IDENTIFIED, "RESPONSE_SHAPE": NOT_IDENTIFIED,
               "ROWS_RETURNED": NOT_IDENTIFIED, "FRESH_EVENTS": NOT_IDENTIFIED,
               "MARKET_ROWS_EXTRACTED": NOT_IDENTIFIED,
               "PAGINATION_FIELDS": {}, "REPEATED_EARLIER_PAGE": NOT_IDENTIFIED,
               "RESPONSE_BYTES": NOT_IDENTIFIED,
               "RESPONSE_BYTES_SHA256": NOT_IDENTIFIED,
               "BODY_CANONICAL_SHA256": NOT_IDENTIFIED,
               "BODY_SHA256": NOT_IDENTIFIED, "BODY_ARTIFACT": NOT_IDENTIFIED,
               "MALFORMED_ROWS": (), "ERROR": NOT_IDENTIFIED}
        try:
            r = get(params)
            rec["RECEIPT_UTC"] = _utc_now()
            rec["HTTP_STATUS"] = getattr(r, "status_code", NOT_IDENTIFIED)
        except Exception as e:                                # noqa: BLE001
            # A TRANSPORT FAILURE HAS NO RESPONSE, AND SAYS SO. It is not an
            # empty body and it is not a zero-byte artifact; there is nothing
            # to retain and the receipt must not imply otherwise.
            rec["ERROR"] = "%s: %s" % (type(e).__name__, e)
            rec["RESPONSE_SHAPE"] = NO_RESPONSE_RECEIVED
            rec["RESPONSE_BYTES"] = NO_RESPONSE_RECEIVED
            rec["BODY_ARTIFACT"] = NO_RESPONSE_RECEIVED
            receipts.append(rec)
            status = BOARD_HTTP_FAILURE
            break
        pages += 1

        # THE BYTES ARE TAKEN BEFORE ANY REJECTION. Run 35333848994 sealed no
        # response evidence at all, so its failure could not be diagnosed after
        # the fact. A 503's body, an HTML error page, a truncated JSON document
        # -- each is the only thing that can explain the next failure, and each
        # used to be discarded before it was ever written down.
        raw = _response_bytes(r)
        if raw is not None:
            rec["RESPONSE_BYTES"] = len(raw)
            rec["RESPONSE_BYTES_SHA256"] = hashlib.sha256(raw).hexdigest()
        if retain_bodies is not None:
            rec["BODY_ARTIFACT"] = retain_bodies(rec, raw)

        if rec["HTTP_STATUS"] != 200:
            receipts.append(rec)
            status = BOARD_HTTP_FAILURE
            break
        try:
            body = r.json()
        except Exception as e:                                # noqa: BLE001
            rec["ERROR"] = "%s: %s" % (type(e).__name__, e)
            rec["RESPONSE_SHAPE"] = "UNPARSEABLE_BODY"
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        # TWO HASHES, NEVER INTERCHANGEABLE. The canonical hash digests the
        # PARSED document with sorted keys, so two byte-different responses
        # that mean the same thing share it; the byte hash digests exactly what
        # arrived. Only the byte hash identifies the retained artifact.
        rec["BODY_CANONICAL_SHA256"] = _body_sha256(body)
        rec["BODY_SHA256"] = rec["BODY_CANONICAL_SHA256"]
        rec["PAGINATION_FIELDS"] = _pagination_fields(body)

        rows = EA.event_rows_of(body)
        if rows is None:
            # No `events` key, or it is not a list. NOT an empty page: an
            # empty page is `{"events": []}` and is the terminal condition.
            rec["RESPONSE_SHAPE"] = "NO_EVENTS_ARRAY:%s" % (
                ",".join(sorted(body)[:8]) if isinstance(body, dict)
                else type(body).__name__)
            rec["ROWS_RETURNED"] = 0
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        rec["RESPONSE_SHAPE"] = "MAPPING_KEY:%s" % EA.EVENT_ROWS_KEY
        rec["ROWS_RETURNED"] = len(rows)

        markets, blk = EA.extract_markets(
            rows, page_ref={"ENDPOINT": endpoint, "OFFSET": offset,
                            "PAGE": rec["PAGE"],
                            "RESPONSE_BYTES_SHA256":
                                rec["RESPONSE_BYTES_SHA256"]},
            endpoint=endpoint, index=index)
        blocks.append(blk)
        rec["MARKET_ROWS_EXTRACTED"] = len(markets)
        rec["MALFORMED_ROWS"] = tuple(blk["DROPPED_ROWS"])

        fresh = 0
        for ev in rows:
            if isinstance(ev, dict) and ev.get("id") is not None:
                eid = str(ev["id"])
                if eid not in event_index:
                    event_index[eid] = {"EVENT_SLUG": ev.get("slug"),
                                        "OFFSET": offset}
                    fresh += 1
        rec["FRESH_EVENTS"] = fresh
        rec["REPEATED_EARLIER_PAGE"] = bool(rows) and fresh == 0

        # The adapter has already applied WALK-WIDE dedup, so anything it
        # returns is new to the whole walk. The old `if slug not in by_slug`
        # first-wins guard is gone: it was the silent arrival-order resolution
        # that let a cross-page conflict pass unnamed.
        for m in markets:
            by_slug[m["slug"]] = m

        # A CONFLICTING DUPLICATE IS A SCHEMA FAILURE, NOT A ROW TO PICK FROM.
        # An ordinary duplicate is deduplicated and counted; two rows claiming
        # one identity with different identity fields cannot both be right and
        # choosing between them would be a guess.
        if blk["CONFLICTING_DUPLICATE_COUNT"]:
            rec["RESPONSE_SHAPE"] += "+CONFLICTING_DUPLICATES:%d" % (
                blk["CONFLICTING_DUPLICATE_COUNT"])
            receipts.append(rec)
            status = BOARD_SCHEMA_FAILURE
            break
        receipts.append(rec)

        if not rows:
            # THE DECLARED TERMINAL CONDITION. An empty FIRST page is one
            # request that returned nothing and enumerates nothing.
            status = (BOARD_VERIFIED_END if pages > 1
                      else BOARD_SINGLE_PAGE_UNCORROBORATED)
            break
        if fresh == 0:
            status = BOARD_PAGINATION_STALLED     # the server ignored `offset`
            break
        # NOTE: no short-page break. See (2) above.
    return (by_slug, event_index, receipts, status,
            EA.merge_blocks(blocks, index=index))


def certify_completion(status, receipts, rows_seen, endpoint=MARKETS_PATH):
    """VERIFIED_END survives only a established contract and no contradiction."""
    if status != BOARD_VERIFIED_END:
        return status, None
    # THE VENUE'S OWN PAGINATION EVIDENCE IS CHECKED FIRST. It is a direct
    # contradiction of "this was the last page" and holds whatever the
    # endpoint's contract status is; the contract question only arises once
    # nothing in the responses disputes the ending.
    why = pagination_contradicts_end(receipts, rows_seen)
    if why:
        return BOARD_PAGINATION_CONTRADICTS_END, why
    contract = BOARD_COMPLETION_CONTRACTS.get(endpoint, {})
    if not contract.get("ESTABLISHED"):
        return (BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED,
                "ENDPOINT_CONTRACT_NOT_ESTABLISHED:%s" % endpoint)
    # THE DECLARED TERMINAL CONDITION MUST BE OBSERVED IN THIS RUN'S RECEIPTS.
    # BLOCK_3 never saw one, so there is nothing to inherit; and even once one
    # HAS been seen, a later run that stops for some other reason has not ended
    # where this run ended. The receipt is the evidence, every time.
    if contract.get("TERMINAL_MUST_BE_OBSERVED_IN_THIS_RUN"):
        want = contract.get("DECLARED_TERMINAL_CONDITION")
        if want == TERMINAL_EMPTY_PAGE:
            last = receipts[-1] if receipts else None
            if not last or last.get("ROWS_RETURNED") != 0:
                got = None if not last else last.get("ROWS_RETURNED")
                return (BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED,
                        "DECLARED_TERMINAL_CONDITION_%s_NOT_OBSERVED:"
                        "LAST_PAGE_ROWS=%s" % (want, got))
        else:
            return (BOARD_COMPLETION_CONTRACT_NOT_ESTABLISHED,
                    "NO_DECLARED_TERMINAL_CONDITION_TO_OBSERVE:%s" % endpoint)
    return BOARD_VERIFIED_END, None


def board_retrieval_block(by_slug, receipts, status, endpoint=MARKETS_PATH):
    """The evidence block the run seals, with the claim it is allowed to make."""
    status, why = certify_completion(status, receipts, len(by_slug), endpoint)
    complete = status == BOARD_VERIFIED_END
    return {
        "BOARD_RETRIEVAL_STATUS": status,
        "BOARD_RETRIEVAL_STATUSES": BOARD_RETRIEVAL_STATUSES,
        "BOARD_LIST_EXHAUSTED": "YES" if complete else "NO",
        "BOARD_UNIVERSE_ENUMERATED": complete,
        "BOARD_PAGES_FETCHED": len(receipts),
        "BOARD_MARKETS_SEEN": len(by_slug),
        "BOARD_REQUEST_RECEIPTS": tuple(receipts),
        "BOARD_ENDPOINT": endpoint,
        "BOARD_COMPLETION_CONTRACT": BOARD_COMPLETION_CONTRACTS.get(
            endpoint, {"CONTRACT": NOT_IDENTIFIED, "ESTABLISHED": False}),
        "COMPLETION_WITHHELD_BECAUSE": why or NOT_IDENTIFIED,
        "A_CONVENTION_IS_NOT_A_CONTRACT": A_CONVENTION_IS_NOT_A_CONTRACT,
        "PAGINATION_EVIDENCE_MAY_CONTRADICT_A_SHORT_PAGE":
            PAGINATION_EVIDENCE_MAY_CONTRADICT_A_SHORT_PAGE,
        "A_FAILED_RETRIEVAL_IS_NOT_A_SMALL_BOARD":
            A_FAILED_RETRIEVAL_IS_NOT_A_SMALL_BOARD,
    }


def discovery_retrieval_block(by_slug, event_index, receipts, status,
                              adapter_block, endpoint=EVENTS_PATH):
    """The evidence block for the EVENTS walk. Two counts, never conflated.

    `rows_seen` for the completion check is the EVENT count, because the offset
    walks events. The extracted market rows are reported beside it and are
    never compared against a venue event total.
    """
    events_seen = len(event_index)
    # ONE COMPLETION DECISION, MADE ON THE EVENT COUNT, USED EVERYWHERE.
    # This block used to certify on the event count and then call
    # `board_retrieval_block`, which certified a SECOND time against
    # len(by_slug) -- the market-row count. Two decisions, one of them
    # comparing a venue event total against extracted market rows, and the
    # second one silently overwriting the first in the shared fields. The
    # board-level fields are now derived from this single verdict.
    status, why = certify_completion(status, receipts, events_seen, endpoint)
    complete = status == BOARD_VERIFIED_END
    blk = {
        "BOARD_RETRIEVAL_STATUS": status,
        "BOARD_RETRIEVAL_STATUSES": BOARD_RETRIEVAL_STATUSES,
        "BOARD_LIST_EXHAUSTED": "YES" if complete else "NO",
        "BOARD_UNIVERSE_ENUMERATED": complete,
        "BOARD_PAGES_FETCHED": len(receipts),
        "BOARD_MARKETS_SEEN": len(by_slug),
        "BOARD_REQUEST_RECEIPTS": tuple(receipts),
        "BOARD_ENDPOINT": endpoint,
        "BOARD_COMPLETION_CONTRACT": BOARD_COMPLETION_CONTRACTS.get(
            endpoint, {"CONTRACT": NOT_IDENTIFIED, "ESTABLISHED": False}),
        "A_CONVENTION_IS_NOT_A_CONTRACT": A_CONVENTION_IS_NOT_A_CONTRACT,
        "PAGINATION_EVIDENCE_MAY_CONTRADICT_A_SHORT_PAGE":
            PAGINATION_EVIDENCE_MAY_CONTRADICT_A_SHORT_PAGE,
        "A_FAILED_RETRIEVAL_IS_NOT_A_SMALL_BOARD":
            A_FAILED_RETRIEVAL_IS_NOT_A_SMALL_BOARD,
        "COMPLETION_CERTIFIED_ON": "EVENT_ROWS",
        "COMPLETION_CERTIFIED_ONCE": True,
    }
    blk.update({
        "DISCOVERY_ENDPOINT": endpoint,
        "DISCOVERY_ADAPTER": adapter_block.get("ADAPTER_VERSION"),
        "DISCOVERY_EVENT_ROWS": events_seen,
        "DISCOVERY_PAGES": len(receipts),
        "DISCOVERY_MARKET_ROWS_EXTRACTED":
            adapter_block.get("MARKET_ROWS_EXTRACTED", 0),
        "DISCOVERY_UNIQUE_MARKET_SLUGS": len(by_slug),
        "DISCOVERY_DROPPED_ROWS": adapter_block.get("DROPPED_COUNT", 0),
        "DISCOVERY_CONFLICTING_DUPLICATES":
            adapter_block.get("CONFLICTING_DUPLICATE_COUNT", 0),
        "DISCOVERY_ADAPTER_BLOCK": adapter_block,
        "DISCOVERY_LIST_EXHAUSTED": "YES" if complete else "NO",
        "FIRST_TERMINAL_OFFSET": (
            receipts[-1]["PARAMS"].get("offset")
            if complete and receipts else None),
        "EVENT_TOTAL_COMPARED_WITH_MARKET_ROW_COUNT": False,
        "AN_ADVANCE_IS_NOT_AN_ENDING": AN_ADVANCE_IS_NOT_AN_ENDING,
        "COMPLETION_WITHHELD_BECAUSE": why or NOT_IDENTIFIED,
    })
    return blk


def selection_status(board_status, roster_ok):
    """A shortfall may only be blamed on the board when the board was read.

    VALID_UNIVERSE_WITH_SELECTION_SHORTFALL is the ONLY reading under which
    'not enough qualifying events' is a statement about the venue.
    """
    if board_status != BOARD_VERIFIED_END:
        return SELECTION_BOARD_INCOMPLETE + ":" + board_status
    if roster_ok:
        return SELECTION_OK
    return VALID_UNIVERSE_WITH_SELECTION_SHORTFALL


def _cli():                                                   # pragma: no cover
    import argparse
    import time
    import httpx
    import collect as C
    import rate_pilot as RP
    sys_path_note = "eligibility lives in ../forward"
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "forward"))
    import eligibility as EL                                   # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rate", default="0.25")
    ap.add_argument("--window-s", type=float, default=CAPTURE_WINDOW_S_DEFAULT)
    # THE INJECTED CLOCK. Offline rehearsal on retained bodies must evaluate
    # them at the time they were retained; the live job passes nothing and
    # reads the real clock. Whichever applies is RECORDED on the selection, so
    # a rehearsal artefact can never be mistaken for a live one.
    ap.add_argument("--as-of", default=None,
                    help="ISO-8601 UTC decision time. Offline rehearsal only.")
    ap.add_argument("--retain-bodies-dir", default=None,
                    help="Directory for retained response bodies.")
    a = ap.parse_args()

    pacer = RP.GlobalPacer(float(a.rate))
    injected = bool(a.as_of)
    now_dt = (_parse(a.as_of) if injected
              else datetime.now(timezone.utc).replace(microsecond=0))
    if now_dt is None:
        raise SystemExit("UNPARSEABLE_AS_OF: %r" % (a.as_of,))
    now_iso = now_dt.isoformat()
    now_epoch = now_dt.timestamp()

    # BODY RETENTION, IN THE REAL COMMAND. Defaults beside the selection file
    # so the evidence archive picks it up with everything else.
    bodies_dir = Path(a.retain_bodies_dir or
                      (Path(a.out).parent / "discovery_bodies"))
    bodies_dir.mkdir(parents=True, exist_ok=True)
    retained = []

    def _retain(rec, raw):
        """Write exactly what arrived, name it by its own byte hash."""
        if raw is None:
            return NO_RESPONSE_RECEIVED
        name = "page_%03d_%s.body" % (rec["PAGE"],
                                      rec["RESPONSE_BYTES_SHA256"][:16])
        (bodies_dir / name).write_bytes(raw)
        retained.append(name)
        return str(Path(bodies_dir.name) / name)

    reads = 0
    with httpx.Client(headers={"accept": "application/json"}) as http:
        # THE BOARD. Deduped by slug, and a page that adds nothing new ends the
        # walk -- this venue family has been observed to ignore paging params,
        # and a repeated page would make one market look like a whole board.
        counted = []

        def _get(params):
            pacer.wait()
            counted.append(1)
            return http.get(C.HOST + EVENTS_PATH, params=params, timeout=20.0)

        # DISCOVERY IS THE EVENT LISTING (management decision 2026-09-18).
        # The event rows are flattened to their OWN child markets by the
        # adapter and handed to the unchanged eligibility rules below.
        (by_slug, event_index, receipts, board_status,
         adapter_block) = discovery_walk(_get, endpoint=EVENTS_PATH,
                                         retain_bodies=_retain)
        reads += len(counted)

        # CANDIDATES FIRST, BOOKS SECOND. Only markets that already carry a
        # venue-native contest identity inside the horizon are worth a book
        # read, so the read budget is spent on markets that can qualify.
        now = _parse(now_iso)
        cands = []
        for m in by_slug.values():
            eid, level = EI.event_identity(m)
            if eid == NOT_IDENTIFIED or level != EI.LEVEL_V1_CONTEST:
                continue
            st = _parse(m.get("gameStartTime"))
            if st is None or (st - now).total_seconds() > FUTURES_HORIZON_S:
                continue
            cands.append(m)
        cands.sort(key=lambda m: (event_rank(_parse(m.get("gameStartTime")),
                                             now, a.window_s),
                                  _salt_rank(m.get("slug"))))
        # THE READ BUDGET DECIDES WHO GETS A BOOK READ, NOT WHO IS ACCOUNTED
        # FOR. Rows beyond it are rejected with
        # NO_BOOK_READ_WITHIN_READ_BUDGET rather than vanishing.
        cands = cands[:CANDIDATE_BOOK_READS_MAX]

        books, activity = {}, {}
        for m in cands:
            slug = m["slug"]
            body, req, recv, status, err = C.read_book_timed(http, pacer, slug)
            reads += 1
            if err:
                continue
            books[slug] = body
            # ONE CLOCK. Trade recency is measured against the SAME decision
            # time the horizon and the ranking use. Mixing an injected as-of
            # with a wall clock here would make every retained book look days
            # stale while the roster thought it was still 2026-09-14.
            activity[slug] = EL.decision_screen(
                m, body, now_epoch if injected else time.time(), _parse_epoch)

    # ACCOUNT FOR THE WHOLE BOARD, not just the rows that got a book read.
    sel = freeze(list(by_slug.values()), books, now_iso, a.window_s, activity)
    sel.update({
        "SELECTION_READS": reads,
        "SELECTION_READS_ARE_PACED_AT_THE_CAPTURE_RATE": True,
        "SELECTION_READS_PRECEDE_THE_FIRST_SAMPLED_GET": True,
        "SELECTION_READS_ARE_NOT_CAPTURE_OBSERVATIONS": True,
        "CANDIDATES_BOOK_READ": len(books),
        "SYS_PATH_NOTE": sys_path_note,
        # THE CLOCK THIS SELECTION WAS DECIDED ON, ON THE RECORD.
        "DECISION_TIME_UTC": now_iso,
        "DECISION_CLOCK_INJECTED": injected,
        "DECISION_CLOCK_SOURCE": ("INJECTED_AS_OF_OFFLINE_REHEARSAL" if injected
                                  else "WALL_CLOCK"),
        "RETAINED_RESPONSE_BODIES": tuple(retained),
        "RETAINED_RESPONSE_BODY_DIR": bodies_dir.name,
    })
    sel.update(discovery_retrieval_block(by_slug, event_index, receipts,
                                         board_status, adapter_block,
                                         endpoint=EVENTS_PATH))
    # THE SHORTFALL MAY ONLY BE BLAMED ON THE VENUE IF THE BOARD WAS READ.
    # `rows_seen` is the EVENT count: the offset walks events, so the event
    # count is what a venue total would have to be compared against.
    certified, _why = certify_completion(board_status, receipts,
                                         len(event_index), EVENTS_PATH)
    sel["SELECTION_STATUS"] = selection_status(
        certified, sel.get("EVENT_SELECTION_FROZEN") == "YES")
    # AN UNENUMERATED BOARD MAY NOT START A CAPTURE, even if three events
    # happened to qualify out of the rows that did arrive. The roster would
    # be drawn from a universe nobody can describe.
    if certified != BOARD_VERIFIED_END:
        sel["CAPTURE_MAY_START"] = "NO"
        sel["CAPTURE_BLOCKED_BY"] = "BOARD_RETRIEVAL_" + certified
    write(a.out, sel)
    print(render(sel))
    if sel["CAPTURE_MAY_START"] != "YES":
        raise SystemExit("SELECTION_STATUS = %s" % sel["SELECTION_STATUS"])


def _parse_epoch(ts):                                         # pragma: no cover
    d = _parse(ts)
    return d.timestamp() if d else None


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
