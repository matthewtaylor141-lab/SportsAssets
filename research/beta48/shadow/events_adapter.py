#!/usr/bin/env python3
"""EVENTS -> MARKETS ADAPTER for V1 board discovery.

Management decision of 2026-09-18 moved V1 board discovery from /v1/markets to
GET /v1/events on the existing approved public gateway. This module is that
move and nothing else: it reads an event listing and hands the event's OWN
child market objects to the frozen eligibility, identity, ranking and
book-screening rules, unchanged.

WHY AN ADAPTER AND NOT A NEW SELECTION RULE
-------------------------------------------
/v1/events returns rows shaped

    {"events": [ { id, slug, ticker, title, startDate, ..., markets: [ ... ] } ]}

and the child objects inside `markets` are the SAME market rows the selection
code already consumes: they carry `slug`, `id`, `gameStartTime` and
`marketSides`, which is everything `event_identity.event_identity` reads. So
the adapter flattens; it does not translate. Every eligibility decision is
still made by the frozen code on a venue row.

Measured against the retained BLOCK_3 bodies (2,600 events, 77,020 child
markets), the frozen identity rule reaches LEVEL_V1_CONTEST on 15,456 child
markets. The old /v1/markets walk saw ~1,557 contest rows in ~20,000. The
change is a discovery-source revision, not a loosening: the same rule is
simply being shown rows it was never shown before.

THE FOUR THINGS THIS ADAPTER MUST NOT DO
----------------------------------------
1. Use the event slug as the market slug. `mlb-nlchamp-2026-09-27` is the
   parent; `tec-mlb-nlchamp-2026-09-27-lad` is the market. They are different
   objects and conflating them would attach one event's identity to every
   child.
2. Replace the canonical EVENT_ID with the parent event id. The canonical
   EVENT_ID stays whatever `event_identity` derives from the venue's team pair
   and start time. The parent id is recorded as PROVENANCE beside it, never
   instead of it.
3. Infer missing identity. No title matching, no fuzzy matching, no "these two
   markets start within a minute of each other so they are the same game". A
   row the venue does not identify is named and dropped.
4. Compare an event-level count with a market-row count. 2,600 events and
   77,020 market rows are two different quantities and this module keeps them
   in two different fields.
"""
from __future__ import annotations

import hashlib

NOT_IDENTIFIED = "NOT_IDENTIFIED"

ADAPTER_VERSION = "EVENTS_TO_MARKETS_V1"
DISCOVERY_ENDPOINT = "/v1/events"

# The key the venue nests child markets under, and the key the listing itself
# arrives under. Both are read exactly; neither is guessed at.
EVENT_ROWS_KEY = "events"
CHILD_MARKETS_KEY = "markets"

# Provenance fields the adapter attaches to every extracted child market. They
# are namespaced so they cannot collide with a venue field of the same name,
# and so a reader can always tell what the venue said from what we added.
PROV_EVENT_ID = "_PARENT_EVENT_ID"
PROV_EVENT_SLUG = "_PARENT_EVENT_SLUG"
PROV_EVENT_TICKER = "_PARENT_EVENT_TICKER"
PROV_EVENT_TITLE = "_PARENT_EVENT_TITLE"
PROV_EVENT_START = "_PARENT_EVENT_START_DATE"
PROV_SOURCE_PAGE = "_SOURCE_PAGE_REF"
PROV_SOURCE_ENDPOINT = "_SOURCE_ENDPOINT"
PROV_ADAPTER = "_ADAPTER_VERSION"

PROVENANCE_FIELDS = (
    PROV_EVENT_ID, PROV_EVENT_SLUG, PROV_EVENT_TICKER, PROV_EVENT_TITLE,
    PROV_EVENT_START, PROV_SOURCE_PAGE, PROV_SOURCE_ENDPOINT, PROV_ADAPTER)

THE_PARENT_IS_NOT_A_MARKET = (
    "an event is a container. It is counted in EVENT_ROWS and never in "
    "MARKET_ROWS, and it never becomes a candidate in its own right")

AN_EVENT_SLUG_IS_NOT_A_MARKET_SLUG = (
    "the venue gives the parent its own slug and every child its own slug. "
    "Using the parent's would collapse an event's markets onto one identity")

# Reasons a child row is refused. Every refused row is named; none is dropped
# silently, for the same reason run 35209604615's EVENTS_QUALIFYING = 0 with
# REJECTED_EVENTS = [] told us nothing.
DROP_NOT_A_DICT = "CHILD_ROW_NOT_AN_OBJECT"
DROP_NO_MARKET_ID = "CHILD_MARKET_HAS_NO_ID"
DROP_NO_MARKET_SLUG = "CHILD_MARKET_HAS_NO_SLUG"
DROP_SLUG_EQUALS_PARENT = "CHILD_SLUG_EQUALS_PARENT_EVENT_SLUG"
DROP_DUPLICATE_MARKET_ID = "DUPLICATE_MARKET_ID"
DROP_DUPLICATE_MARKET_SLUG = "DUPLICATE_MARKET_SLUG"
DROP_CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE_MARKET_IDENTITY"

EVENT_DROP_NOT_A_DICT = "EVENT_ROW_NOT_AN_OBJECT"
EVENT_DROP_NO_ID = "EVENT_HAS_NO_ID"
EVENT_DROP_NO_MARKETS = "EVENT_CARRIES_NO_MARKETS_LIST"
EVENT_DROP_DUPLICATE = "DUPLICATE_EVENT_ID"
EVENT_DROP_CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE_EVENT_IDENTITY"

DROP_REASONS = (
    DROP_NOT_A_DICT, DROP_NO_MARKET_ID, DROP_NO_MARKET_SLUG,
    DROP_SLUG_EQUALS_PARENT, DROP_DUPLICATE_MARKET_ID,
    DROP_DUPLICATE_MARKET_SLUG, DROP_CONFLICTING_DUPLICATE,
    EVENT_DROP_NOT_A_DICT, EVENT_DROP_NO_ID, EVENT_DROP_NO_MARKETS,
    EVENT_DROP_DUPLICATE, EVENT_DROP_CONFLICTING_DUPLICATE)


def event_rows_of(body):
    """The event list on a /v1/events page, or None if the shape is wrong.

    None and [] are different answers. None means "this is not an event
    listing" -- a schema failure the walk must retain and refuse. [] means
    "an event listing with no rows", which is the declared terminal page.
    """
    if not isinstance(body, dict):
        return None
    rows = body.get(EVENT_ROWS_KEY)
    if rows is None:
        return None
    return rows if isinstance(rows, list) else None


def _identity_of_market(m):
    """(id, slug) as the venue supplied them. Neither is ever synthesised."""
    mid = m.get("id")
    if mid is None:
        mid = m.get("marketId")
    slug = m.get("slug")
    return (None if mid is None else str(mid),
            None if not slug else str(slug))


def _fingerprint(m):
    """A stable digest of the venue fields that decide identity.

    Two rows sharing an id are a duplicate. Two rows sharing an id whose
    identity-bearing fields DIFFER are a conflict, and a conflict is named
    rather than resolved: picking one of them would be a guess.
    """
    parts = [str(m.get("slug")), str(m.get("gameStartTime"))]
    for s in (m.get("marketSides") or []):
        if isinstance(s, dict):
            t = s.get("team") or {}
            parts.append("%s/%s/%s" % (s.get("teamId"), t.get("id"),
                                       t.get("providerId")))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def extract_markets(event_rows, page_ref=None, endpoint=DISCOVERY_ENDPOINT):
    """Flatten one page of events into child market rows plus an audit block.

    Returns (markets, block). `markets` are the venue's own child objects with
    provenance added and NOTHING removed or rewritten -- they go straight into
    the frozen eligibility rules. `block` counts events and market rows
    SEPARATELY and names every dropped row.
    """
    markets = []
    by_id, by_slug, fp_of = {}, {}, {}
    seen_events, event_fp = {}, {}
    dropped, conflicts = [], []
    event_rows_seen = 0
    events_kept = 0
    child_rows_seen = 0

    for ev in (event_rows or ()):
        event_rows_seen += 1
        if not isinstance(ev, dict):
            dropped.append({"LEVEL": "EVENT", "REASON": EVENT_DROP_NOT_A_DICT,
                            "EVENT_ID": None})
            continue
        eid = ev.get("id")
        if eid is None:
            dropped.append({"LEVEL": "EVENT", "REASON": EVENT_DROP_NO_ID,
                            "EVENT_SLUG": ev.get("slug")})
            continue
        eid = str(eid)
        efp = hashlib.sha256(
            ("%s|%s" % (ev.get("slug"), ev.get("ticker"))).encode()).hexdigest()
        if eid in seen_events:
            reason = (EVENT_DROP_DUPLICATE if event_fp.get(eid) == efp
                      else EVENT_DROP_CONFLICTING_DUPLICATE)
            rec = {"LEVEL": "EVENT", "REASON": reason, "EVENT_ID": eid,
                   "EVENT_SLUG": ev.get("slug")}
            dropped.append(rec)
            if reason == EVENT_DROP_CONFLICTING_DUPLICATE:
                conflicts.append(rec)
            continue
        seen_events[eid] = True
        event_fp[eid] = efp

        children = ev.get(CHILD_MARKETS_KEY)
        if not isinstance(children, list):
            dropped.append({"LEVEL": "EVENT", "REASON": EVENT_DROP_NO_MARKETS,
                            "EVENT_ID": eid, "EVENT_SLUG": ev.get("slug")})
            continue
        events_kept += 1

        for child in children:
            child_rows_seen += 1
            if not isinstance(child, dict):
                dropped.append({"LEVEL": "MARKET", "REASON": DROP_NOT_A_DICT,
                                "EVENT_ID": eid})
                continue
            mid, slug = _identity_of_market(child)
            if mid is None:
                dropped.append({"LEVEL": "MARKET", "REASON": DROP_NO_MARKET_ID,
                                "EVENT_ID": eid, "MARKET_SLUG": slug})
                continue
            if slug is None:
                dropped.append({"LEVEL": "MARKET",
                                "REASON": DROP_NO_MARKET_SLUG,
                                "EVENT_ID": eid, "MARKET_ID": mid})
                continue
            if slug == (ev.get("slug") or object()):
                # The parent's slug arriving on a child means the listing is
                # not telling us which market this is. Refused, not reused.
                dropped.append({"LEVEL": "MARKET",
                                "REASON": DROP_SLUG_EQUALS_PARENT,
                                "EVENT_ID": eid, "MARKET_ID": mid,
                                "MARKET_SLUG": slug})
                continue

            fp = _fingerprint(child)
            prior_id = by_id.get(mid)
            prior_slug = by_slug.get(slug)
            if prior_id is not None or prior_slug is not None:
                same = (fp_of.get(mid) == fp) and (fp_of.get(slug) == fp)
                if same:
                    reason = (DROP_DUPLICATE_MARKET_ID if prior_id is not None
                              else DROP_DUPLICATE_MARKET_SLUG)
                else:
                    reason = DROP_CONFLICTING_DUPLICATE
                rec = {"LEVEL": "MARKET", "REASON": reason, "EVENT_ID": eid,
                       "MARKET_ID": mid, "MARKET_SLUG": slug}
                dropped.append(rec)
                if reason == DROP_CONFLICTING_DUPLICATE:
                    conflicts.append(rec)
                continue

            row = dict(child)
            row[PROV_EVENT_ID] = eid
            row[PROV_EVENT_SLUG] = ev.get("slug")
            row[PROV_EVENT_TICKER] = ev.get("ticker")
            row[PROV_EVENT_TITLE] = ev.get("title")
            row[PROV_EVENT_START] = ev.get("startDate") or ev.get("startTime")
            row[PROV_SOURCE_PAGE] = page_ref
            row[PROV_SOURCE_ENDPOINT] = endpoint
            row[PROV_ADAPTER] = ADAPTER_VERSION

            by_id[mid] = row
            by_slug[slug] = row
            fp_of[mid] = fp
            fp_of[slug] = fp
            markets.append(row)

    block = {
        "ADAPTER_VERSION": ADAPTER_VERSION,
        "SOURCE_ENDPOINT": endpoint,
        "SOURCE_PAGE_REF": page_ref,

        # TWO COUNTS, NEVER ONE. An event total is not a market total.
        "EVENT_ROWS": event_rows_seen,
        "EVENTS_WITH_MARKETS": events_kept,
        "CHILD_MARKET_ROWS_SEEN": child_rows_seen,
        "MARKET_ROWS_EXTRACTED": len(markets),
        "UNIQUE_MARKET_IDS": len(by_id),
        "UNIQUE_MARKET_SLUGS": len(by_slug),

        "DROPPED_ROWS": dropped,
        "DROPPED_COUNT": len(dropped),
        "CONFLICTING_DUPLICATES": conflicts,
        "CONFLICTING_DUPLICATE_COUNT": len(conflicts),

        "PARENT_COUNTED_AS_A_MARKET": False,
        "EVENT_SLUG_USED_AS_MARKET_SLUG": False,
        "CANONICAL_EVENT_ID_REPLACED_BY_PARENT_ID": False,
        "IDENTITY_INFERRED_FROM_TITLE_OR_PROXIMITY": False,
    }
    return markets, block


def merge_blocks(blocks):
    """Sum per-page adapter blocks into the walk-level block."""
    out = {
        "ADAPTER_VERSION": ADAPTER_VERSION,
        "SOURCE_ENDPOINT": DISCOVERY_ENDPOINT,
        "PAGES": len(blocks),
        "EVENT_ROWS": 0,
        "EVENTS_WITH_MARKETS": 0,
        "CHILD_MARKET_ROWS_SEEN": 0,
        "MARKET_ROWS_EXTRACTED": 0,
        "DROPPED_COUNT": 0,
        "CONFLICTING_DUPLICATE_COUNT": 0,
        "DROPPED_ROWS": [],
        "CONFLICTING_DUPLICATES": [],
        "PARENT_COUNTED_AS_A_MARKET": False,
        "EVENT_SLUG_USED_AS_MARKET_SLUG": False,
        "CANONICAL_EVENT_ID_REPLACED_BY_PARENT_ID": False,
        "IDENTITY_INFERRED_FROM_TITLE_OR_PROXIMITY": False,
    }
    for b in blocks or ():
        for k in ("EVENT_ROWS", "EVENTS_WITH_MARKETS", "CHILD_MARKET_ROWS_SEEN",
                  "MARKET_ROWS_EXTRACTED", "DROPPED_COUNT",
                  "CONFLICTING_DUPLICATE_COUNT"):
            out[k] += b.get(k, 0)
        out["DROPPED_ROWS"] += b.get("DROPPED_ROWS") or []
        out["CONFLICTING_DUPLICATES"] += b.get("CONFLICTING_DUPLICATES") or []
    return out


def strip_provenance(market):
    """The venue's row as it arrived, with our added fields removed."""
    return {k: v for k, v in (market or {}).items()
            if k not in PROVENANCE_FIELDS}


def provenance_of(market):
    """Only the fields the adapter added. Parent identity lives HERE."""
    return {k: (market or {}).get(k) for k in PROVENANCE_FIELDS}
