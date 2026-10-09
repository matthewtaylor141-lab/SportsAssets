"""THE HELD CONTRACT'S PINNAPI FIXTURE, AS THE PINNAPI MATCHER ALREADY PROVED
IT FOR THIS CONTRACT (RC6 xavier-records). Read only: no socket, no write,
no order, no threshold.

THE DEFECT. Xavier's held PinnAPI read (pinnapi_feed_runtime.held_fixture)
finds a held contract's provider fixture by the venue's EXACT structured
team names, and -- where those find nothing -- by the ENTRY valuation's event
key when it names a PinnAPI fixture ("pinnapi:<matchup id>"). A contract
entered on the METERED provider's reading carries that provider's event key
(the venue event's sticky key, migration 261), so a name the venue spells
differently from Pinnacle left the read with nothing at all, for the life of
the position, although the PinnAPI matcher had already priced the SAME
contract on the SAME fixture.

PRODUCTION (read-only research, 2026-10-09 01:30-01:37Z):
  * atc-brb-csc-cri-2026-10-08-csc (Ceara SC vs Criciuma EC, held LONG 60
    from 2026-10-05 00:56Z until its protective sale filled 2026-10-09
    00:23Z): 1,939 held reads NO_FEED_EVENT. Venue names "ceara sc" /
    "criciuma ec"; entry valuation 6594 from the-odds-api.com/v4, event key
    87416e0fc86513494d5993fb8bb340fe. Yet 25 valuations of the SAME contract
    (2026-10-06 08:40Z .. 2026-10-08 12:52Z) were written FROM the PinnAPI
    feed (provider pinnapi.com/raw-websocket), every one recording the
    matched fixture settlement_comparison.reference_input.feed_event_id =
    1637577754: the provider HAD the fixture (coverage), the held read could
    not name it (identity).
  * 14 days of held reviews: 74 held PAPER groups (19,462 reviews) refused
    NO_FEED_EVENT / STRUCTURED_PARTICIPANTS_NOT_TWO with a metered entry key
    while the PinnAPI matcher had recorded a fixture for the same contract.

THE REPAIR. For a held (or about-to-be-held) contract whose entry key is
NOT a PinnAPI key, the newest fixture the PinnAPI matcher recorded for the
SAME venue contract (same us_market_slug) is handed to the held read as its
provider fixture ("pinnapi:<feed_event_id>"). The read then applies exactly
its own checks, unchanged: the fixture must be in this process's cache in
the contract's sport, its start within the read's start tolerance of the
venue's, the held outcome must map to ONE of its designations, and the price
must pass the unchanged 30 s rule. A PinnAPI entry key is used as it always
was. Nothing is made fresh here, and no probability is read from the table:
only the fixture identity the matcher recorded.
"""
from __future__ import annotations

import time

VERSION = "XAVIER_HELD_FIXTURE_V1"
PROVIDER = "pinnapi.com/raw-websocket"
PREFIX = "pinnapi:"
#: how far back a PinnAPI match of the contract is looked for (a held
#: position is read for at most its life; matches older than this are not
#: used)
LOOKBACK_S = 14 * 86400.0
#: at most this many held contracts resolved per batch read
MAX_SLUGS = 400

B_ENTRY_PINNAPI = "ENTRY_VALUATION_NAMES_THE_PINNAPI_FIXTURE"
B_MATCHED = "PINNAPI_MATCHER_RECORDED_THIS_CONTRACTS_FIXTURE"
B_NONE = "NO_PINNAPI_FIXTURE_RECORDED_FOR_THIS_CONTRACT"
B_UNREAD = "PINNAPI_FIXTURE_RECORD_UNREAD"

#: THE NEWEST FIXTURE THE PINNAPI MATCHER RECORDED FOR EACH VENUE CONTRACT:
#: rows written from the PinnAPI feed (the h2h primary and the line lane both
#: record `reference_input` with the provider and the matched fixture id).
#: Identity only -- the row's probability, price and purpose are not read.
CONTRACT_FIXTURES_SQL = """
    SELECT DISTINCT ON (v.us_market_slug) v.us_market_slug AS slug, v.id,
           extract(epoch FROM v.decided_at)::float8 AS decided_at,
           v.settlement_comparison->'reference_input'->>'feed_event_id'
               AS feed_event_id,
           v.settlement_comparison->'reference_input'->'fixture_match'
               AS fixture_match
      FROM external_valuations v
     WHERE v.us_market_slug = ANY($1::text[])
       AND v.provider = 'pinnapi.com/raw-websocket'
       AND v.settlement_comparison->'reference_input'->>'provider'
           = 'pinnapi.com/raw-websocket'
       AND v.settlement_comparison->'reference_input'->>'feed_event_id'
           ~ '^[0-9]{1,15}$'
       AND v.decided_at > to_timestamp($2)
     ORDER BY v.us_market_slug, v.decided_at DESC, v.id DESC
     LIMIT %d""" % MAX_SLUGS


def is_pinnapi_key(event_key) -> bool:
    k = str(event_key or "")
    return k.startswith(PREFIX) and len(k) > len(PREFIX)


def resolve(entry_event_key, matched: dict | None) -> dict:
    """THE KEY HANDED TO THE HELD READ (pure). `matched` is the contract's
    CONTRACT_FIXTURES_SQL row or None. A PinnAPI entry key stands; otherwise
    the matcher's recorded fixture, when there is one; otherwise the entry
    key unchanged (the read refuses exactly as before)."""
    if is_pinnapi_key(entry_event_key):
        return {"event_key": entry_event_key, "basis": B_ENTRY_PINNAPI,
                "entry_event_key": entry_event_key, "version": VERSION}
    if not matched or not matched.get("feed_event_id"):
        return {"event_key": entry_event_key, "basis": B_NONE,
                "entry_event_key": entry_event_key, "version": VERSION}
    return {"event_key": PREFIX + str(matched["feed_event_id"]),
            "basis": B_MATCHED, "entry_event_key": entry_event_key,
            "matched_valuation_id": matched.get("id"),
            "matched_at": matched.get("decided_at"),
            "fixture_match": matched.get("fixture_match"),
            "version": VERSION,
            "checks_still_applied_by_the_read": (
                "fixture held in this process's cache in the contract's "
                "sport; start within the read's tolerance of the venue's; "
                "the outcome maps to one designation; the unchanged 30 s "
                "rule")}


async def matched_fixtures(conn, slugs, *, at: float | None = None,
                           savepoint: bool = True) -> dict:
    """{slug: CONTRACT_FIXTURES_SQL row} for the given venue contracts
    (bounded). Never raises: an unreadable answer is {} plus the reason
    under the key None -- nothing is resolved by it."""
    slugs = sorted({str(s) for s in (slugs or ()) if s})[:MAX_SLUGS]
    if not slugs:
        return {}
    since = float(at if at is not None else time.time()) - LOOKBACK_S
    try:
        if savepoint:
            async with conn.transaction():
                rows = await conn.fetch(CONTRACT_FIXTURES_SQL, slugs, since)
        else:
            rows = await conn.fetch(CONTRACT_FIXTURES_SQL, slugs, since)
    except Exception as exc:                                    # noqa: BLE001
        return {None: "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    out = {}
    for r in rows or []:
        d = dict(r)
        if d.get("slug") and d.get("feed_event_id"):
            out[d["slug"]] = d
    return out


async def held_key(conn, *, us_market_slug, entry_event_key,
                   at: float | None = None, savepoint: bool = True) -> dict:
    """The key (and its provenance) to hand the held read for ONE contract.
    A PinnAPI entry key needs no read."""
    if is_pinnapi_key(entry_event_key):
        return resolve(entry_event_key, None)
    got = await matched_fixtures(conn, [us_market_slug], at=at,
                                 savepoint=savepoint)
    if None in got:
        return {"event_key": entry_event_key, "basis": B_UNREAD,
                "entry_event_key": entry_event_key, "why": got[None],
                "version": VERSION}
    return resolve(entry_event_key, got.get(us_market_slug))
