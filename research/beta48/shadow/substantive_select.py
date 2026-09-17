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

import event_identity as EI

NOT_IDENTIFIED = "NOT_IDENTIFIED"
SELECTION_SALT = "BETA48-SUBSTANTIVE-2026-09-17"
EVENTS_REQUIRED = 3
MARKETS_PER_EVENT = 2
FUTURES_HORIZON_S = 72 * 3600          # beyond this it is a future, not a game
LIVE_PREFERRED = True
CAPTURE_WINDOW_S_DEFAULT = 90 * 60

SELECTION_OK = "FROZEN"
SELECTION_INSUFFICIENT = "INSUFFICIENT_QUALIFYING_EVENTS"
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
    """A book with a bid AND an ask. One side is not a two-sided book."""
    if not isinstance(book_body, dict):
        return False
    bids = book_body.get("bids") or []
    asks = book_body.get("asks") or []
    return bool(bids) and bool(asks)


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
    by_event = {}
    for m in markets or ():
        if not isinstance(m, dict):
            continue
        eid, level = EI.event_identity(m)
        if eid == NOT_IDENTIFIED or level != EI.LEVEL_V1_CONTEST:
            continue                       # no venue-native contest identity
        start = _parse(m.get("gameStartTime"))
        if start is None:
            continue
        if (start - now).total_seconds() > FUTURES_HORIZON_S:
            continue                       # a season future, not a contest
        by_event.setdefault(eid, []).append(m)

    out, rejected = [], []
    for eid, ms in by_event.items():
        keep = []
        for m in ms:
            slug = m.get("slug")
            act = activity_of.get(slug) or {}
            if not two_sided(books_by_slug.get(slug)):
                continue
            if not (act.get("HIGH_ACTIVITY_AT_DECISION")
                    or act.get("ACTIVE_AT_DECISION")
                    or act.get("BROAD_AT_DECISION")):
                continue
            keep.append(m)
        if len(keep) < MARKETS_PER_EVENT:
            rejected.append({"EVENT_ID": eid,
                             "REASON": "FEWER_THAN_%d_ELIGIBLE_MARKETS"
                                       % MARKETS_PER_EVENT,
                             "ELIGIBLE_MARKETS": len(keep)})
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
    out.sort(key=lambda e: (e["RANK"], _salt_rank(e["EVENT_ID"])))
    return out, rejected


def freeze(markets, books_by_slug, now_iso, window_s=CAPTURE_WINDOW_S_DEFAULT,
           activity_of=None, events_required=EVENTS_REQUIRED):
    """Produce the frozen roster, or refuse and say the roster is short."""
    ranked, rejected = eligible_events(markets, books_by_slug, now_iso,
                                       window_s, activity_of)
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
    return {
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
BOARD_PAGE_LIMIT = 100
BOARD_MAX_PAGES = 6
CANDIDATE_BOOK_READS_MAX = 40


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
    a = ap.parse_args()

    pacer = RP.GlobalPacer(float(a.rate))
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    reads = 0
    with httpx.Client(headers={"accept": "application/json"}) as http:
        # THE BOARD. Deduped by slug, and a page that adds nothing new ends the
        # walk -- this venue family has been observed to ignore paging params,
        # and a repeated page would make one market look like a whole board.
        by_slug, pages, exhausted = {}, 0, False
        while pages < BOARD_MAX_PAGES:
            pacer.wait()
            reads += 1
            r = http.get(C.HOST + MARKETS_PATH,
                         params={"active": "true", "closed": "false",
                                 "limit": BOARD_PAGE_LIMIT,
                                 "offset": pages * BOARD_PAGE_LIMIT},
                         timeout=20.0)
            pages += 1
            body = r.json() if r.status_code == 200 else {}
            items = body.get("markets") or body.get("data") or []
            fresh = 0
            for m in items if isinstance(items, list) else ():
                if isinstance(m, dict) and m.get("slug") not in by_slug:
                    by_slug[m["slug"]] = m
                    fresh += 1
            if not items or fresh == 0:
                exhausted = True
                break

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
        cands = cands[:CANDIDATE_BOOK_READS_MAX]

        books, activity = {}, {}
        for m in cands:
            slug = m["slug"]
            body, req, recv, status, err = C.read_book_timed(http, pacer, slug)
            reads += 1
            if err:
                continue
            books[slug] = body
            activity[slug] = EL.decision_screen(
                m, body, time.time(), _parse_epoch)

    sel = freeze(cands, books, now_iso, a.window_s, activity)
    sel.update({
        "SELECTION_READS": reads,
        "SELECTION_READS_ARE_PACED_AT_THE_CAPTURE_RATE": True,
        "SELECTION_READS_PRECEDE_THE_FIRST_SAMPLED_GET": True,
        "SELECTION_READS_ARE_NOT_CAPTURE_OBSERVATIONS": True,
        "BOARD_PAGES_FETCHED": pages,
        "BOARD_LIST_EXHAUSTED": "YES" if exhausted else "NO",
        "BOARD_MARKETS_SEEN": len(by_slug),
        "CANDIDATES_BOOK_READ": len(books),
        "SYS_PATH_NOTE": sys_path_note,
    })
    write(a.out, sel)
    print(render(sel))
    if sel["CAPTURE_MAY_START"] != "YES":
        raise SystemExit("SELECTION_STATUS = %s" % sel["SELECTION_STATUS"])


def _parse_epoch(ts):                                         # pragma: no cover
    d = _parse(ts)
    return d.timestamp() if d else None


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
